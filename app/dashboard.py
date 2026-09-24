"""Cult Product Health dashboard. Run: uv run streamlit run app/dashboard.py

Every chart/table has a drill-down: select a row to see the exact source rows
(sheet tab + row number) that produced the number."""

from __future__ import annotations

import pandas as pd
import streamlit as st

import sqlite3

from cultph.amazon.rating import effective_target, plan as rating_plan, plan_exact
from cultph.amazon.store import AMAZON_DB
from cultph.config import load_config
from cultph.db import LIVE_DB, last_runs, read_table
from cultph.metrics import breakdown, rows_for

st.set_page_config(page_title="Cult Product Health", layout="wide")

_pw = __import__("cultph.config", fromlist=["env"]).env("DASHBOARD_PASSWORD")
if _pw and not st.session_state.get("authed"):
    import hmac

    entered = st.text_input("Password", type="password")
    if entered and hmac.compare_digest(entered, _pw):
        st.session_state["authed"] = True
        st.rerun()
    elif entered:
        st.error("Wrong password")
    st.stop()

if not LIVE_DB.exists():
    st.error("No published data yet. Run `uv run cultph sync` first.")
    st.stop()


@st.cache_data(ttl=60)
def load(name: str) -> pd.DataFrame:
    df = read_table(name)
    for col in ("event_date", "created_at"):
        if col in df:
            df[col] = pd.to_datetime(df[col], format="ISO8601")
    return df


def drill(df: pd.DataFrame, by, within=None, key: str = "", title: str = "", show_cols=None):
    """Breakdown table; selecting a row reveals the underlying source rows."""
    by_l = [by] if isinstance(by, str) else list(by)
    within_l = [within] if isinstance(within, str) else list(within or [])
    b = breakdown(df, by_l, within_l or None)
    if title:
        st.markdown(f"**{title}**  <span style='color:gray'>(n = {len(df):,})</span>", unsafe_allow_html=True)
    ev = st.dataframe(
        b, key=key, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={"share": st.column_config.ProgressColumn("share", format="percent", min_value=0, max_value=1)},
    )
    rows = ev.selection.rows if ev and ev.selection else []
    if rows:
        sel = b.iloc[rows[0]]
        sub = rows_for(df, {c: sel[c] for c in within_l + [c for c in by_l if c not in within_l]})
        st.caption(f"{len(sub):,} source rows behind {sel['count']:,} / {sel['denominator']:,}")
        st.dataframe(sub[show_cols] if show_cols else sub, hide_index=True, width="stretch")


def filters(df: pd.DataFrame, key: str) -> pd.DataFrame:
    c1, c2, c3 = st.columns(3)
    prods = c1.multiselect("Product", sorted(df["product"].dropna().unique()), key=f"{key}_p")
    plats = c2.multiselect("Platform", sorted(df["platform"].dropna().unique()), key=f"{key}_pl")
    months = c3.multiselect("Month", sorted(df["month"].dropna().unique()), key=f"{key}_m")
    if prods:
        df = df[df["product"].isin(prods)]
    if plats:
        df = df[df["platform"].isin(plats)]
    if months:
        df = df[df["month"].isin(months)]
    return df


@st.cache_data(ttl=60)
def load_optional(name: str) -> pd.DataFrame:
    try:
        return load(name)
    except Exception:  # noqa: BLE001 - table not configured
        return pd.DataFrame()


approved, pending, wms, tickets = load("approved"), load("pending"), load("wms"), load("tickets")
sales = load_optional("sales")
checks = load("checks")
CATEGORY = load_config().category_of()

with st.sidebar:
    cat = st.radio("Category", ["All", "massager", "scale"], horizontal=True)
if cat != "All":
    approved, pending, wms, tickets = (d[d["category"] == cat] for d in (approved, pending, wms, tickets))
    if not sales.empty:
        sales = sales[sales["category"] == cat]

# ---------- sidebar: pipeline health (from the latest run, even if it was blocked) ----------
with st.sidebar:
    st.header("Pipeline health")
    runs = last_runs(1)
    run_checks = pd.DataFrame(runs[0]["checks"]) if runs else checks
    if runs:
        r = runs[0]
        st.metric("Last sync", r["status"].upper(), help=r["at"])
        if r["published"]:
            st.caption(f"{r['at']} · published")
        else:
            st.error(f"{r['at']} · BLOCKED — dashboard shows the previous good snapshot")
    st.dataframe(run_checks[["check", "status"]], hide_index=True, width="stretch")
    with st.expander("Check details"):
        for c in run_checks.itertuples():
            st.markdown(f"**{c.check}** — {c.status}  \n{c.detail}")

st.title("Cult Product Health")
st.caption("Returns, exchanges and tickets from the shared sheet · Amazon and Flipkart ratings and reviews · "
           "AI issue labels with a judge. Every number links back to its source rows.")

tab_over, tab_alerts, tab_amz, tab_iss, tab_ret, tab_tix, tab_pend, tab_wms, tab_dq = st.tabs(
    ["Overview", "Alerts", "Ratings & reviews", "Review issues (AI)", "Returns & exchanges", "Support tickets",
     "Pending verification", "Warehouse returns", "Data quality"])

with tab_alerts:
    has_alerts = AMAZON_DB.exists() and sqlite3.connect(AMAZON_DB).execute(
        "SELECT count(*) FROM sqlite_master WHERE name='alert_event'").fetchone()[0]
    if not has_alerts:
        st.info("No alerts yet. `uv run cultph alerts` records a baseline on its first run, then raises new alerts.")
    else:
        with sqlite3.connect(AMAZON_DB) as con:
            ev_df = pd.read_sql("SELECT * FROM alert_event ORDER BY created_at DESC", con)
            base = con.execute("SELECT value FROM alert_state WHERE key='baseline_at'").fetchone()
        st.caption(f"Baseline: {base[0].strip(chr(34)) if base else '—'}. Only reviews first seen after this and "
                   "posted recently raise alerts.")
        if ev_df.empty:
            st.success("No alerts since the baseline.")
        for r in ev_df.head(100).itertuples():
            icon = {"urgent": "🚨", "high": "🔴", "normal": "🔵"}.get(r.priority, "•")
            st.markdown(f"{icon} **{r.title}** · <span style='color:gray'>{r.created_at} · {r.rule}"
                        f" · sent: {r.channels or 'feed only'}</span>  \n{r.detail}", unsafe_allow_html=True)


@st.cache_data(ttl=60)
def load_amazon(name: str) -> pd.DataFrame:
    with sqlite3.connect(AMAZON_DB) as con:
        return pd.read_sql(f'SELECT * FROM "{name}"', con)


with tab_amz:
    amz_cfg = load_config().raw.get("amazon", {})
    shown_target = float(amz_cfg.get("target_rating", 4.1))
    mode = amz_cfg.get("target_mode", "displayed")
    target = effective_target(shown_target, mode)
    if not AMAZON_DB.exists():
        st.info("No Amazon data yet. Run `uv run cultph amazon`.")
    else:
        snaps, reviews, runs_log = load_amazon("rating_snapshot"), load_amazon("review"), load_amazon("scrape_run")
        for d in (snaps, reviews):
            d["platform"] = d["platform"].fillna("amazon") if "platform" in d else "amazon"
        platform_pick = st.radio("Platform", sorted(snaps["platform"].unique()), horizontal=True, key="plat_pick",
                                 format_func=str.title)
        exact_counts = platform_pick == "flipkart"
        snaps, reviews = snaps[snaps["platform"] == platform_pick], reviews[reviews["platform"] == platform_pick]
        if exact_counts:
            st.caption("Flipkart shows exact per-star counts, so these numbers are exact, not ranges.")
        latest = snaps.sort_values("captured_at").groupby("asin").tail(1)
        pools = latest.groupby("parent_asin")["asin"].apply(list).to_dict()
        def rng(lo_hi, fmt="{}"):
            lo, hi = lo_hi
            return fmt.format(lo) if lo == hi else f"{fmt.format(lo)}–{fmt.format(hi)}"

        rows = []
        for r in latest.itertuples():
            hist = {5: r.p5, 4: r.p4, 3: r.p3, 2: r.p2, 1: r.p1}
            if exact_counts:
                pl = plan_exact({5: r.c5, 4: r.c4, 3: r.c3, 2: r.c2, 1: r.c1}, target)
            else:
                pl = rating_plan(int(r.total_ratings), hist, target, shown=r.avg_rating)
            rows.append({
                "asin": r.asin, "product": r.product, "category": CATEGORY.get(r.product, "?"),
                "displayed": r.avg_rating, "ratings": r.total_ratings,
                ("mean" if exact_counts else "weighted mean"): rng(pl["avg_range"], "{:.3f}") + ("" if pl["consistent"] else " ⚠"),
                f"5★ needed to show {shown_target}": rng(pl["five_star_needed"]),
                "1★ it can absorb": rng(pl["one_star_absorbable"]),
                "5★ share now": r.p5 / 100, "5★ share to hold": pl["five_star_share_needed"],
                **({} if exact_counts else {"shares ratings with": ", ".join(a for a in pools.get(r.parent_asin, []) if a != r.asin)}),
                "stored reviews (pool)": int(reviews["asin"].isin(pools.get(r.parent_asin, [r.asin])).sum()),
                "as of": r.captured_at,
            })
        table = pd.DataFrame(rows).sort_values("displayed")
        if cat != "All":
            table = table[table["category"] == cat]
        st.markdown(f"**Latest per ASIN · target: show {shown_target}★** "
                    f"({'weighted mean ≥ ' + str(target) if mode == 'displayed' else 'exact mean ≥ ' + str(target)})")
        st.caption("ASINs in the same variation family share one rating pool on Amazon (same parent ASIN).")
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            "5★ share now": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
            "5★ share to hold": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
        with st.expander("How the 4.1 numbers are calculated"):
            st.markdown(
                f"""The star histogram on Amazon is its **weighted** distribution, not raw counts (small listings show
shares that can't come from whole ratings). Its weighted mean A matches the displayed rating. The percentages are rounded, so
A is known only within a range, narrowed further by the displayed one-decimal value, and every answer is shown as
*best–worst*. ⚠ means the displayed value and the histogram disagree, so check the page. N = global ratings, T = {target}
(Amazon shows one decimal, so a mean of {target} or more displays as {shown_target}).
- 5★ needed: `ceil((T − A)·N / (5 − T))`
- 1★ it can absorb: `floor((A − T)·N / (T − 1))`
- 5★ share to hold: the share of new ratings that must be 5★ (with the rest in today's 1–4★ mix) for new ratings to average T.

New ratings are weighted by Amazon too (by recency, verified purchase and so on), so treat these as estimates to steer by. Once the
review listing is available (after login), the per-star filters give raw counts, which can tighten this.""")

        pick = st.selectbox("Listing", table["asin"], format_func=lambda a: f"{a} · {table.set_index('asin').loc[a, 'product']}")
        a, b = st.columns(2)
        s_one = snaps[snaps["asin"] == pick].sort_values("captured_at")
        with a:
            st.markdown("**Rating over time**")
            st.line_chart(s_one.set_index("captured_at")[["avg_rating"]])
        with b:
            last = s_one.iloc[-1]
            st.markdown("**Star distribution (latest, %)**")
            st.bar_chart(pd.Series({f"{k}★": last[f"p{k}"] for k in (5, 4, 3, 2, 1)}))
        pool = pools.get(latest.set_index("asin").loc[pick, "parent_asin"], [pick])
        rv = reviews[reviews["asin"].isin(pool)].sort_values("review_date", ascending=False)
        stars = st.multiselect("Stars", [1, 2, 3, 4, 5], default=[1, 2], key="amz_stars")
        if stars:
            rv = rv[rv["rating"].isin(stars)]
        st.markdown(f"**Stored reviews** ({len(rv)} shown)")
        st.dataframe(rv[["review_date", "rating", "title", "body", "verified", "variant", "product", "attribution",
                         "helpful_votes", "first_seen_at", "source", "review_id"]], hide_index=True, width="stretch")
        with st.expander("Scrape log"):
            st.dataframe(runs_log.sort_values("at", ascending=False).head(100), hide_index=True, width="stretch")

with tab_over:
    k = st.columns(4)
    k[0].metric("Approved returns + exchanges", f"{len(approved):,}")
    k[1].metric("Exchanges / returns", f"{(approved['mode'] == 'Exchange').sum():,} / {(approved['mode'] == 'Return').sum():,}")
    k[2].metric("Pending verification", f"{len(pending):,}")
    k[3].metric("Product-issue tickets", f"{int(tickets['is_product_issue'].sum()):,}")

    top_issue = (approved.groupby(["product", "issue"]).size().rename("n").reset_index()
                 .sort_values("n", ascending=False).drop_duplicates("product").set_index("product"))
    summary = pd.DataFrame({
        "approved": approved.groupby("product").size(),
        "exchange": approved[approved["mode"] == "Exchange"].groupby("product").size(),
        "return": approved[approved["mode"] == "Return"].groupby("product").size(),
        "pending": pending.groupby("product").size(),
        "wms_returns": wms.groupby("product").size(),
        "product_tickets": tickets[tickets["is_product_issue"] == 1].groupby("product").size(),
    }).fillna(0).astype(int)
    if not sales.empty:
        from cultph.metrics import return_rates

        rr = return_rates(approved, sales, ["product"]).set_index("product")
        summary["units_sold"] = rr["units"].reindex(summary.index).fillna(0).astype(int)
        summary["return_pct"] = rr["return_pct"].reindex(summary.index)
        summary["selling_pct"] = rr["selling_pct"].reindex(summary.index)
    summary["sku_name_conflicts"] = approved[approved["name_conflict"] == 1].groupby("product").size()
    summary["sku_name_conflicts"] = summary["sku_name_conflicts"].fillna(0).astype(int)
    summary["top_issue"] = top_issue["issue"]
    summary["top_issue_share"] = (top_issue["n"] / summary["approved"]).round(3)
    st.subheader("By product")
    st.caption("sku_name_conflicts = approved rows whose model name disagrees with the SKU code "
               "(counted under the SKU's product). See Data quality.")
    st.dataframe(summary.sort_values("approved", ascending=False), width="stretch",
                 column_config={"top_issue_share": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                                "return_pct": st.column_config.NumberColumn(format="percent"),
                                "selling_pct": st.column_config.NumberColumn(format="percent")})
    if sales.empty:
        st.info("Return % and selling % appear here once a units-sold sheet is added (tabs.sales in the config).")
    st.subheader("Monthly trend (approved returns + exchanges)")
    st.bar_chart(approved.groupby(["month", "mode"]).size().unstack(fill_value=0))

with tab_iss:
    import json

    from cultph.ai import store as label_store
    from cultph.ai.labels import PROMPT_VERSION

    has_labels = AMAZON_DB.exists() and sqlite3.connect(AMAZON_DB).execute(
        "SELECT count(*) FROM sqlite_master WHERE name='review_label'").fetchone()[0]
    if not has_labels:
        st.info("No labelled reviews yet. Run `uv run cultph label` (needs ANTHROPIC_API_KEY).")
    else:
        with sqlite3.connect(AMAZON_DB) as con:
            label_store.ensure(con)  # applies column migrations
            lab = pd.read_sql(
                "SELECT l.*, r.asin, r.product, r.attribution, r.rating, r.title, r.body, r.review_date "
                "FROM review_label l JOIN review r USING (review_id) WHERE l.prompt_version = ?",
                con, params=(PROMPT_VERSION,))
        # final label: human fix > human-confirmed or auto; unreviewed queue items are excluded
        lab["final"] = lab["status"].eq("auto") | lab["human_verdict"].isin(["correct", "fixed"])
        lab["final_codes"] = lab["codes"].where(lab["human_verdict"] != "fixed", lab["human_codes"]).fillna("")
        human = lab[lab["human_verdict"].notna()]
        audited = human[human["status"] == "auto"]      # random sample of auto-accepted labels
        disputed = human[human["status"] == "queue"]
        waiting = ((lab["status"] == "queue") | (lab["audit"] == 1)) & lab["human_verdict"].isna()
        k = st.columns(5)
        k[0].metric("Labelled reviews", len(lab))
        k[1].metric("Auto-accepted", f"{(lab['status'] == 'auto').mean():.0%}")
        k[2].metric("Waiting for a person", int(waiting.sum()))
        k[3].metric("Auto-label accuracy (audit)",
                    f"{(audited['human_verdict'] == 'correct').mean():.0%} of {len(audited)}" if len(audited) else "n/a",
                    help="A person checked a random sample of auto-accepted labels. This is how far to trust the issue shares.")
        k[4].metric("Disputed labels AI got right",
                    f"{(disputed['human_verdict'] == 'correct').mean():.0%} of {len(disputed)}" if len(disputed) else "n/a")
        st.caption(f"Issue shares use final labels only ({int(lab['final'].sum())} reviews). A review can have several "
                   "issues, so shares don't add up to 100%. Reviews from shared rating pools are left out of per-product numbers.")

        fin = lab[lab["final"] & lab["product"].notna()]
        exploded = fin.assign(code=fin["final_codes"].str.split(",")).explode("code")
        exploded = exploded[exploded["code"].fillna("") != ""]
        denom = fin.groupby("product").size().rename("labelled_reviews")
        share = (exploded.groupby(["product", "code"])["review_id"].nunique().rename("reviews").reset_index()
                 .merge(denom, on="product"))
        share["share"] = share["reviews"] / share["labelled_reviews"]
        share = share.sort_values(["product", "reviews"], ascending=[True, False])
        ev = st.dataframe(share, hide_index=True, width="stretch", key="iss_share", on_select="rerun",
                          selection_mode="single-row",
                          column_config={"share": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
        if ev and ev.selection and ev.selection.rows:
            sel = share.iloc[ev.selection.rows[0]]
            sub = exploded[(exploded["product"] == sel["product"]) & (exploded["code"] == sel["code"])]
            ev_rows = []
            for r in sub.itertuples():
                quote = next((i["evidence"] for i in json.loads(r.issues) if i["code"] == sel["code"]), "")
                ev_rows.append({"review_date": r.review_date, "rating": r.rating, "evidence": quote,
                                "title": r.title, "body": r.body, "status": r.status, "human": r.human_verdict,
                                "review_id": r.review_id})
            st.caption(f"{len(ev_rows)} reviews behind {sel['reviews']} / {sel['labelled_reviews']}")
            st.dataframe(pd.DataFrame(ev_rows), hide_index=True, width="stretch")

        st.subheader("Review queue")
        st.caption("Disputed labels (the judge didn't agree, or the evidence check failed) plus a random audit sample of "
                   "auto-accepted ones. Your decisions form the gold set.")
        codes_all = [t["code"] for t in load_config().raw.get("ai", {}).get("taxonomy", [])]
        queue = lab[waiting].sort_values("status", ascending=False).head(20)
        if queue.empty:
            st.success("Queue is empty.")
        for r in queue.itertuples():
            with st.container(border=True):
                st.markdown(f"**{r.rating}★ · {r.title or ''}**  \n{r.body or ''}")
                st.markdown("AI label: " + (", ".join(f"`{i['code']}` — “{i['evidence']}”" for i in json.loads(r.issues)) or "_no issues_"))
                st.caption(("🎲 audit sample · " if r.status == "auto" else "⚠ disputed · ")
                           + f"judge: {r.judge_verdict} — {r.judge_reason}"
                           + (f" · checks: {r.check_errors}" if r.check_errors else ""))
                c1, c2, c3 = st.columns([1, 3, 1])
                if c1.button("AI is correct", key=f"ok_{r.review_id}"):
                    with sqlite3.connect(AMAZON_DB) as con:
                        label_store.set_human(con, r.review_id, PROMPT_VERSION, "correct")
                    st.cache_data.clear()
                    st.rerun()
                fix = c2.multiselect("Correct codes", codes_all, default=[c for c in (r.codes or "").split(",") if c in codes_all],
                                     key=f"codes_{r.review_id}")
                if c3.button("Save fix", key=f"fix_{r.review_id}"):
                    with sqlite3.connect(AMAZON_DB) as con:
                        label_store.set_human(con, r.review_id, PROMPT_VERSION, "fixed", ",".join(sorted(fix)))
                    st.cache_data.clear()
                    st.rerun()

with tab_ret:
    if not sales.empty:
        from cultph.metrics import return_rates

        st.markdown("**Return rate = approved returns + exchanges ÷ units sold** (months with sales data only)")
        months_both = sorted(set(sales["month"]) & set(approved["month"]))
        a_s, s_s = approved[approved["month"].isin(months_both)], sales[sales["month"].isin(months_both)]
        dim = st.radio("Split by", ["product", "product + month", "product + platform"], horizontal=True, key="rr_dim")
        by = {"product": ["product"], "product + month": ["product", "month"], "product + platform": ["product", "platform"]}[dim]
        st.dataframe(return_rates(a_s, s_s, by), hide_index=True, width="stretch",
                     column_config={"return_pct": st.column_config.NumberColumn(format="percent"),
                                    "selling_pct": st.column_config.NumberColumn(format="percent")})
        st.divider()
    df = filters(approved, "ret")
    cols = ["event_date", "product", "sku", "model_raw", "issue", "mode", "platform", "channel", "batch", "amount",
            "warehouse", "order_id", "src_tab", "src_row"]
    a, b = st.columns(2)
    with a:
        drill(df, "issue", key="ret_issue", title="Issue share", show_cols=cols)
        drill(df, "platform", key="ret_plat", title="Marketplace (from order-id pattern)", show_cols=cols)
    with b:
        drill(df, "mode", key="ret_mode", title="Return vs exchange", show_cols=cols)
        drill(df, "batch", key="ret_batch", title="Batch", show_cols=cols)
    drill(df, "issue", within="product", key="ret_pi", title="Issue share within each product", show_cols=cols)
    drill(df, "warehouse", key="ret_wh", title="Warehouse", show_cols=cols)

with tab_tix:
    df = filters(tickets, "tix")
    cols = ["created_at", "ticket_id", "product", "model_raw", "l2", "l3", "platform", "platform_inferred",
            "order_id", "subject", "src_tab", "src_row"]
    st.caption("Product issues = tickets whose L2 is the product category; everything else is order/service related.")
    a, b = st.columns(2)
    with a:
        drill(df, "is_product_issue", key="tix_split", title="Product vs non-product tickets", show_cols=cols)
        drill(df[df["is_product_issue"] == 1], "l3", key="tix_l3", title="Product issue share (L3)", show_cols=cols)
    with b:
        drill(df[df["is_product_issue"] == 0], "l2", key="tix_np", title="Non-product ticket share (L2)", show_cols=cols)
        drill(df, "platform", key="tix_plat", title="Platform", show_cols=cols)
    drill(df[df["is_product_issue"] == 1], "l3", within="product", key="tix_pl3",
          title="Product issue share within each product", show_cols=cols)

with tab_pend:
    df = filters(pending, "pend")
    cols = ["created_at", "product", "sku", "model_raw", "reason", "comment", "order_id", "src_tab", "src_row"]
    drill(df, "reason", key="pend_r", title="Return reason", show_cols=cols)
    drill(df, "reason", within="product", key="pend_pr", title="Reason within each product", show_cols=cols)

with tab_wms:
    df = filters(wms, "wms")
    cols = ["created_at", "product", "sku", "channel_name", "return_type", "return_sub_type", "reason", "status",
            "facility", "order_id", "src_tab", "src_row"]
    a, b = st.columns(2)
    with a:
        drill(df, "return_sub_type", key="wms_sub", title="Customer vs courier (RTO) return", show_cols=cols)
        drill(df, "platform", key="wms_plat", title="Channel", show_cols=cols)
    with b:
        drill(df, "return_type", key="wms_type", title="Return type", show_cols=cols)
        drill(df[df["return_sub_type"] == "Customer Return"], "reason", key="wms_reason",
              title="Customer return reason", show_cols=cols)

with tab_dq:
    st.subheader("SKU code vs model name conflicts")
    st.caption("The SKU code wins. Rows here are likely data-entry errors worth fixing at the source.")
    conf = pd.concat([d[d["name_conflict"] == 1].assign(table=n) for n, d in
                      [("approved", approved), ("pending", pending), ("wms", wms)]])
    st.dataframe(conf.groupby(["table", "sku", "product", "model_raw"]).size().rename("rows").reset_index()
                 .sort_values("rows", ascending=False), hide_index=True, width="stretch")
    st.subheader("Rows with no model named")
    st.dataframe(pd.DataFrame({n: [int((d["map_method"] == "not_mentioned").sum()), len(d)] for n, d in
                               [("tickets", tickets), ("approved", approved), ("pending", pending), ("wms", wms)]},
                              index=["not named", "total"]), width="stretch")
    st.subheader("Rejected rows")
    st.dataframe(load("rejects"), hide_index=True, width="stretch")
