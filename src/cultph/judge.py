"""The judge: checks run on every ingest before anything is published.
Any 'fail' blocks the publish; the previous good snapshot stays live."""

from __future__ import annotations


import pandas as pd

from .config import Config
from .ingest import IngestResult
from .metrics import breakdown, dashboard_recompute, find_block, return_rates

PASS, WARN, FAIL = "pass", "warn", "fail"


def _check(name, status, detail, **extra):
    return {"check": name, "status": status, "detail": detail, **extra}


def run_checks(res: IngestResult, cfg: Config, source=None) -> list[dict]:
    t = cfg.thresholds
    out: list[dict] = []

    # 1. Headers present
    out.append(_check("headers", FAIL if res.header_errors else PASS,
                      "; ".join(res.header_errors) or "all mapped columns found"))

    # 2. Row conservation: every non-blank source row is stored or rejected with a reason
    for role, n_src in res.source_rows.items():
        n_ok = len(res.tables.get(role, []))
        n_rej = sum(1 for r in res.rejects if r["role"] == role)
        ok = n_src == n_ok + n_rej
        out.append(_check(f"rows:{role}", PASS if ok and n_rej == 0 else (WARN if ok else FAIL),
                          f"source {n_src} = stored {n_ok} + rejected {n_rej}", expected=n_src, actual=n_ok + n_rej))

    # 3. IDs are clean strings (no 123.0, no scientific notation)
    bad_ids = 0
    for df in res.tables.values():
        for col in ("order_id", "ticket_id", "sku", "item_code"):
            if col in df:
                s = df[col].dropna().astype(str)
                bad_ids += int(s.str.contains(r"^\d+\.0+$|e\+", regex=True).sum())
    out.append(_check("ids_clean", FAIL if bad_ids else PASS, f"{bad_ids} malformed ids"))

    # 4. Month label agrees with the row's date
    mism_total = 0
    for role, df in res.tables.items():
        if df.empty:
            continue
        lab = df["month_label_num"]
        date_m = pd.to_datetime(df["month"] + "-01").dt.month
        mism = df[lab.notna() & (lab != date_m)]
        mism_total += len(mism)
        if len(mism):
            rows = ", ".join(f"{r.src_tab}!{r.src_row}" for r in mism.head(10).itertuples())
            out.append(_check(f"month_label:{role}", WARN if len(mism) <= t["max_month_label_mismatch"] else FAIL,
                              f"{len(mism)} rows where month label != date month (e.g. {rows})"))
    if not mism_total:
        out.append(_check("month_label", PASS, "month labels agree with dates"))

    # 5. Product mapping coverage (rows that name a model but can't be mapped)
    for role, df in res.tables.items():
        if df.empty:
            continue
        named = df[df["map_method"] != "not_mentioned"]
        unmapped = named[named["map_method"] == "unmapped"]
        cov = 1 - len(unmapped) / len(named) if len(named) else 1.0
        names = unmapped["model_raw"].value_counts().head(5).to_dict()
        out.append(_check(f"mapping:{role}", PASS if not len(unmapped) else (WARN if cov >= t["min_mapping_coverage"] else FAIL),
                          f"coverage {cov:.2%}; unmapped {len(unmapped)} {names or ''}", actual=round(cov, 4)))
        conflicts = int(df["name_conflict"].sum())
        if conflicts:
            out.append(_check(f"sku_name_conflict:{role}", WARN,
                              f"{conflicts} rows where SKU code and model name point to different products (SKU used)"))

    # 6. Self-check of the metrics layer: shares inside each group sum to 1
    appr = res.tables.get("approved")
    if appr is not None and not appr.empty:
        b = breakdown(appr, "issue", within="product")
        sums = b.groupby("product")["share"].sum()
        off = sums[(sums - 1).abs() > 1e-9]
        out.append(_check("segment_sums", FAIL if len(off) else PASS, f"{len(off)} product groups whose issue shares don't sum to 100%"))

    # 6b. Sales: periods overlap the returns, and no product returns more than it sold
    sales = res.tables.get("sales")
    if sales is not None and not sales.empty and appr is not None and not appr.empty:
        overlap = sorted(set(sales["month"]) & set(appr["month"]))
        out.append(_check("sales_overlap", PASS if overlap else FAIL,
                          f"{len(overlap)} months with both sales and returns" if overlap else
                          f"sales months {sorted(set(sales['month']))[:3]}.. don't overlap returns months"))
        rr = return_rates(appr[appr["month"].isin(overlap)], sales[sales["month"].isin(overlap)], ["product", "month"])
        over = rr[rr["return_pct"] > 1]
        out.append(_check("return_pct_le_100", FAIL if len(over) else PASS,
                          f"{len(over)} product-months with returns > units sold"
                          + (f" (e.g. {over.iloc[0]['product']} {over.iloc[0]['month']})" if len(over) else "")))
        no_sales = sorted(set(appr["product"].dropna()) - set(sales["product"].dropna()))
        if no_sales:
            out.append(_check("sales_coverage", WARN, f"{len(no_sales)} products have returns but no sales rows: {no_sales[:5]}"))

    # 6c. Same order/ticket in two tabs of the same role (e.g. an old and a new layout overlapping)
    for role, col in (("approved", "order_id"), ("tickets", "ticket_id")):
        df = res.tables.get(role)
        if df is None or df.empty or "src_spec" not in df or df["src_spec"].nunique() < 2:
            continue
        ids = df.dropna(subset=[col]).groupby(col)["src_spec"].nunique()
        cross = ids[ids > 1]
        # different dates = a repeat claim (e.g. a replacement that failed again), not a double entry
        dup = df[df[col].isin(cross.index)]
        same_day = dup.assign(d=pd.to_datetime(dup.get("event_date", dup.get("created_at"))).dt.date) \
            .groupby([col, "d"])["src_spec"].nunique()
        doubles = int((same_day > 1).sum())
        out.append(_check(f"cross_tab_duplicates:{role}", FAIL if doubles else PASS,
                          f"{doubles} {col}s entered in two tabs on the same day; "
                          f"{len(cross)} {col}s appear in both tabs on different dates (repeat claims, kept)"))

    # 6d. Every approved row has a known mode (free-text approval values must all be mapped)
    if appr is not None and not appr.empty:
        bad = appr[~appr["mode"].isin(["Exchange", "Return"])]
        out.append(_check("mode_mapped", FAIL if len(bad) else PASS,
                          f"{len(bad)} approved rows with an unmapped return/exchange value"
                          + (f": {bad['mode_raw'].fillna('(blank)').value_counts().head(5).to_dict()}" if len(bad) else "")))

    # 6e. Which months each role covers (a role missing for a month is shown, not treated as zero)
    from .metrics import role_month_coverage
    cov = role_month_coverage(res.tables)
    if not cov.empty:
        gaps = [f"{m}: no {r}" for m, row in cov.iterrows() for r, n in row.items()
                if n == 0 and r in ("pending", "wms")]
        out.append(_check("role_coverage", WARN if gaps else PASS,
                          ("months without some data: " + "; ".join(gaps[:6])) if gaps else "every role covers every month"))

    # 7. Reconcile against the sheet's own Dashboard numbers (month rows + grand total)
    dc = cfg.dashboard_check
    if dc and source is not None:
        try:
            tabs = dc["tab"] if isinstance(dc["tab"], list) else [dc["tab"]]
            names = source.tab_names() if hasattr(source, "tab_names") else tabs
            tab = next((t for t in tabs if t in names), tabs[0])
            block = find_block(source.grid(tab), dc["approved_header"], dc["total_header"])
            block = [(int(float(a)), int(float(b))) for a, b in block]
        except Exception as e:  # noqa: BLE001 - surface any read problem as a failed check
            out.append(_check("dashboard_reconcile", FAIL, f"could not read dashboard block: {e}"))
        else:
            got = dashboard_recompute(res.tables, dc.get("specs"))
            sheet_months, sheet_total = block[:-1], block[-1] if block else (0, 0)
            diffs = []
            if len(sheet_months) != len(got):
                diffs.append(f"sheet has {len(sheet_months)} month rows, raw data has {len(got)} months")
            for (m, a, tot), (ea, et) in zip(got.itertuples(index=False), sheet_months):
                if a != ea or tot != et:
                    diffs.append(f"{m}: approved {a} vs sheet {ea}, total {tot} vs sheet {et}")
            ga, gt = int(got["approved"].sum()), int(got["total"].sum())
            if (ga, gt) != sheet_total:
                diffs.append(f"grand total: approved {ga} vs sheet {sheet_total[0]}, total {gt} vs sheet {sheet_total[1]}")
            out.append(_check("dashboard_reconcile", FAIL if diffs else PASS,
                              "; ".join(diffs) or f"{len(got)} months ({got['month'].iloc[0]}..{got['month'].iloc[-1]}) "
                                                  f"and grand total match (approved {ga}, total {gt})"))
    return out


def verdict(checks: list[dict]) -> str:
    if any(c["status"] == FAIL for c in checks):
        return FAIL
    return WARN if any(c["status"] == WARN for c in checks) else PASS


