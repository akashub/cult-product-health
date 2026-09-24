"""The judge: checks run on every ingest before anything is published.
Any 'fail' blocks the publish; the previous good snapshot stays live."""

from __future__ import annotations


import pandas as pd

from .config import Config
from .ingest import IngestResult
from .metrics import breakdown, dashboard_recompute, find_block

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

    # 7. Reconcile against the sheet's own Dashboard numbers (month rows + grand total)
    dc = cfg.dashboard_check
    if dc and source is not None:
        try:
            block = find_block(source.grid(dc["tab"]), dc["approved_header"], dc["total_header"])
            block = [(int(float(a)), int(float(b))) for a, b in block]
        except Exception as e:  # noqa: BLE001 - surface any read problem as a failed check
            out.append(_check("dashboard_reconcile", FAIL, f"could not read dashboard block: {e}"))
        else:
            got = dashboard_recompute(res.tables)
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


