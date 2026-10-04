"""Cult Product Health dashboard. Run: uv run streamlit run app/dashboard.py

Every chart/table has a drill-down: select a row to see the exact source rows
(sheet tab + row number) that produced the number."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

import sqlite3

from cultph.amazon.rating import effective_target, plan as rating_plan, plan_exact
from cultph.amazon.store import AMAZON_DB
from cultph.config import load_config
from cultph.db import LIVE_DB, last_runs, read_table
from cultph.metrics import breakdown, rows_for

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from style import CSS, SEVERITY, card as card_html, glance, header, quote, section, status_badge, tiles  # noqa: E402

st.set_page_config(page_title="Cult Product Health", page_icon="📊", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

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
    st.markdown("**Returns data sync**")
    runs = last_runs(1)
    run_checks = pd.DataFrame(runs[0]["checks"]) if runs else checks
    if runs:
        r = runs[0]
        st.markdown(status_badge(r["status"], r["at"][:16].replace("T", " "), r["published"]), unsafe_allow_html=True)
    n_fail = int((run_checks["status"] == "fail").sum())
    n_warn = int((run_checks["status"] == "warn").sum())
    st.caption(f"{len(run_checks)} data checks · {n_fail} failed · {n_warn} warnings")
    with st.expander("Check details"):
        for c in run_checks.itertuples():
            mark = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(c.status, "•")
            st.markdown(f"{mark} **{c.check}**  \n<span style='color:#6B7280;font-size:.8rem'>{c.detail}</span>",
                        unsafe_allow_html=True)

st.markdown(header("Cult Product Health",
                   "Returns, ratings and reviews for Cult massagers and scales · Amazon · Flipkart · shared sheet",
                   "Updated " + (lambda v: v[:16].replace("T", " ") if v else "—")(
                       sqlite3.connect(AMAZON_DB).execute("SELECT MAX(captured_at) FROM rating_snapshot").fetchone()[0]
                       if AMAZON_DB.exists() else None)),
            unsafe_allow_html=True)

tab_over, tab_prod, tab_alerts, tab_amz, tab_iss, tab_ret, tab_tix, tab_pend, tab_wms, tab_dq, tab_src = st.tabs(
    ["Overview", "Products", "Alerts", "Ratings & reviews", "Review issues (AI)", "Returns & exchanges",
     "Support tickets", "Pending verification", "Warehouse returns", "Data quality", "Data sources"])

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
    import datetime as _dt

    from cultph.config import env as _env
    from cultph.insights import build_insights, load_inputs, review_trends, scorecard, unbiased_reviews, \
        pool_labels, latest_snapshots, issue_mix, windows

    @st.cache_data(ttl=300, show_spinner="Working out insights…")
    def overview_data(today: _dt.date):
        cfg = load_config()
        ai = cfg.raw.get("ai", {})
        key = "OPENAI_API_KEY" if ai.get("provider") == "openai" else "ANTHROPIC_API_KEY"
        inp = load_inputs(AMAZON_DB, LIVE_DB, cfg, last_runs(50), has_ai_key=bool(_env(key)))
        latest = latest_snapshots(inp.snaps)
        names = pool_labels(latest) if not latest.empty else {}
        unb = unbiased_reviews(inp.reviews, names)
        return (build_insights(inp, today), scorecard(inp, today), review_trends(unb, today, 8), unb, inp.labels,
                latest.assign(unit=latest["pool"].map(names)) if not latest.empty else latest)

    today = _dt.date.today()
    insights, card, trends, unb, labels_df, latest_units = overview_data(today)
    unit_category = {u: CATEGORY.get(u.split(" / ")[0].split(" ·")[0], "massager") for u in
                     (set(card["product"]) if not card.empty else set())}

    # ---------------- insights
    import altair as alt
    from style import board_head, board_item, status_line

    n = {s: sum(1 for i in insights if i.severity == s) for s in SEVERITY}
    h1, h2 = st.columns([3, 2])
    h1.markdown(section("What needs attention", f"as of {today:%d %b %Y}"), unsafe_allow_html=True)
    with h2:
        st.markdown('<div style="height:30px"></div>' + status_line(n), unsafe_allow_html=True)

    # portfolio at a glance (one row of key numbers)
    if not card.empty:
        amz = card[card["platform"] == "amazon"]
        w_avg = (amz["rating"] * amz["ratings"]).sum() / max(amz["ratings"].sum(), 1) if len(amz) else 0
        at_target = int(card["status"].str.startswith("✅").sum())
        rev14 = int(card["reviews 14d"].sum())
        safety30 = int(card["safety 30d"].sum())
        glance_items = [("Amazon avg rating", f"{w_avg:.2f}★", "weighted by ratings"),
                        ("At or above 4.1★", f"{at_target}/{len(card)}", "product listings"),
                        ("New reviews", f"{rev14}", "last 14 days"),
                        ("Safety mentions", f"{safety30}", "last 30 days")]
        if not approved.empty:
            last_m = sorted(approved["month"].dropna().unique())[-1]
            glance_items.append(("Returns + exchanges", f"{int((approved['month'] == last_m).sum()):,}", f"in {last_m} (sheet)"))
        st.markdown(glance(glance_items), unsafe_allow_html=True)
    st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)

    cols = st.columns(3, gap="medium")
    for col, sev in zip(cols, ("act", "watch", "good")):
        items = [i for i in insights if i.severity == sev]
        with col:
            st.markdown(board_head(sev, len(items)), unsafe_allow_html=True)
            if not items:
                st.markdown('<div class="ib-empty">Nothing here right now.</div>', unsafe_allow_html=True)
            for idx, ins in enumerate(items):
                with st.container(key=f"ib_{sev}_{idx}"):
                    st.markdown(board_item(ins), unsafe_allow_html=True)
                    with st.expander("Details"):
                        st.markdown(f"<div class='ib-detail'>{ins.detail}<div class='w'>↗ {ins.where}</div></div>",
                                    unsafe_allow_html=True)
                        if ins.evidence is not None and len(ins.evidence):
                            st.dataframe(ins.evidence, hide_index=True, width="stretch", height=min(36 * (len(ins.evidence) + 1), 300))
    notes = [i for i in insights if i.severity == "info"]
    if notes:
        with st.expander(f"Data & pipeline notes · {len(notes)}"):
            for ins in notes:
                st.markdown(f"**{ins.title}** — {ins.detail}  \n<span style='color:#6B7280;font-size:.8rem'>↗ {ins.where}</span>",
                            unsafe_allow_html=True)

    # ---------------- scorecard
    st.markdown(section("Product scorecard", "one row per product · ratings, sales signals and recent reviews"),
                unsafe_allow_html=True)
    if card.empty:
        st.info("No marketplace data yet. Run `uv run cultph amazon` / `cultph flipkart`.")
    else:
        sc_plat = st.segmented_control("Platform", ["amazon", "flipkart"], default="amazon", format_func=str.title,
                                       key="sc_plat", label_visibility="collapsed") or "amazon"
        view = card[card["platform"] == sc_plat].drop(columns=["platform", "listings"])
        if cat != "All":
            view = view[view["product"].map(unit_category) == cat]
        if sc_plat == "flipkart":
            view = view.drop(columns=["bought/month", "sub-rank", "rank category", "in stock"], errors="ignore")
        for col in ("sub-rank", "price", "★ new (14d)", "★ prev 14d", "★ 14d", "% 1–2★ 14d"):
            if col in view:
                view[col] = pd.to_numeric(view[col], errors="coerce")
        empty_cols = [c for c in view.columns if view[c].replace("", pd.NA).isna().all()]
        view = view.drop(columns=empty_cols)  # e.g. sales/rank columns before the first poll that captures them
        view = view.rename(columns={"status": "vs 4.1★ target", "reviews 14d": "new reviews (14d)",
                                    "★ 14d": "★ new (14d)", "★ prev 14d": "★ prev 14d", "% 1–2★ 14d": "1–2★ share (14d)",
                                    "top complaint 30d": "top complaint (30d)", "safety 30d": "safety (30d)"})
        with st.container(key="panel_scorecard"):
            st.dataframe(view, hide_index=True, width="stretch", height=min(38 * (len(view) + 1) + 4, 520),
                         column_config={
                             "product": st.column_config.TextColumn("product", width="medium"),
                             "rating": st.column_config.NumberColumn("rating", format="%.1f ★"),
                             "ratings": st.column_config.NumberColumn("ratings", format="%d"),
                             "1–2★ share (14d)": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                             "★ new (14d)": st.column_config.NumberColumn(format="%.2f"),
                             "★ prev 14d": st.column_config.NumberColumn(format="%.2f"),
                             "price": st.column_config.NumberColumn(format="₹%d"),
                             "sub-rank": st.column_config.NumberColumn(format="#%d")})
            st.caption("Rating = what the marketplace shows. “14d” = reviews posted in the last 14 days from the marketplace's "
                       "most-recent list, vs the 14 days before. Bought/month, rank, price and stock come from Amazon's "
                       "product page. Top complaint uses AI labels on ≤3★ reviews."
                       + (f" Not captured yet (appear after the next poll): {', '.join(empty_cols)}." if empty_cols else ""))

    # ---------------- bi-weekly trends
    st.markdown(section("Bi-weekly review trends", "new reviews per 14-day window · complete windows only"),
                unsafe_allow_html=True)
    if trends.empty:
        st.info("No review history yet.")
    else:
        c1, c2 = st.columns([1, 3])
        with c1:
            tp = st.segmented_control("Platform", ["amazon", "flipkart"], default="amazon", format_func=str.title,
                                      key="tr_plat", label_visibility="collapsed") or "amazon"
        tr = trends[trends["platform"] == tp]
        units = (tr.groupby("unit")["n"].sum().sort_values(ascending=False).index.tolist())
        if cat != "All":
            units = [u for u in units if unit_category.get(u, "massager") == cat]
        with c2:
            unit = st.selectbox("Product", units, key="tr_unit", label_visibility="collapsed") if units else None
        if unit:
            t = tr[tr["unit"] == unit].sort_values("window", ascending=False)
            shown = t[t["complete"] & (t["n"] > 0)].sort_values("start").copy()
            shown["1–2★ share"] = shown["neg_share"]
            order = shown["label"].tolist()
            X = alt.X("label:O", sort=order, title=None, axis=alt.Axis(labelAngle=0, labelFontSize=11))

            def tidy(chart):
                return chart.configure_view(strokeWidth=0).configure(background="#FFFFFF").configure_axis(
                    gridColor="#EEF0F4", domainColor="#D1D5DB", labelColor="#4B5563", titleColor="#6B7280")
            a, b = st.columns(2, gap="medium")
            with a, st.container(key="panel_trend_stars"):
                st.markdown("**Average stars of new reviews**")
                base = alt.Chart(shown).encode(x=X)
                line = base.mark_line(color="#4F46E5", strokeWidth=3, point=alt.OverlayMarkDef(size=70, filled=True)).encode(
                    y=alt.Y("avg_stars:Q", title="avg ★", scale=alt.Scale(domain=[1, 5])),
                    tooltip=[alt.Tooltip("label:N", title="window"), alt.Tooltip("avg_stars:Q", title="avg ★", format=".2f"),
                             alt.Tooltip("n:Q", title="reviews")])
                rule = alt.Chart(pd.DataFrame({"y": [4.1]})).mark_rule(color="#059669", strokeDash=[5, 4]).encode(y="y:Q")
                st.altair_chart(tidy((line + rule).properties(height=250)), use_container_width=True)
                st.caption("Dashed line = 4.1★ target.")
            with b, st.container(key="panel_trend_volume"):
                st.markdown("**Volume and share of 1–2★ reviews**")
                vol = alt.Chart(shown).mark_bar(color="#C7D2FE", cornerRadiusTopLeft=5, cornerRadiusTopRight=5, size=38).encode(
                    x=X,
                    y=alt.Y("n:Q", title="new reviews"),
                    tooltip=[alt.Tooltip("label:N", title="window"), alt.Tooltip("n:Q", title="reviews"),
                             alt.Tooltip("1–2★ share:Q", format=".0%")])
                neg = alt.Chart(shown).mark_line(color="#DC2626", strokeWidth=2.5, point=alt.OverlayMarkDef(size=60, filled=True)).encode(
                    x=X, y=alt.Y("1–2★ share:Q", title="1–2★ share", axis=alt.Axis(format="%"),
                                                scale=alt.Scale(domain=[0, 1])))
                st.altair_chart(tidy(alt.layer(vol, neg).resolve_scale(y="independent").properties(height=250)),
                                use_container_width=True)
                st.caption("Bars = new reviews · red line = share that are 1–2★.")
            if (~t["complete"]).any():
                st.caption("Older windows are hidden where the scraper couldn't reach every review (Amazon lists at most 100 "
                           "recent reviews per product), so they never show up as a false decline. The newest window may still fill in.")
            with st.expander("Window-by-window numbers"):
                table = t[["label", "n", "avg_stars", "neg_share", "complete", "latest"]].rename(columns={
                    "label": "14-day window", "avg_stars": "avg ★", "neg_share": "1–2★ share",
                    "complete": "complete data", "latest": "newest (may fill in)"})
                st.dataframe(table, hide_index=True, width="stretch",
                             column_config={"1–2★ share": st.column_config.NumberColumn(format="percent"),
                                            "avg ★": st.column_config.NumberColumn(format="%.2f")})

            wins = windows(today, 4)
            mix = pd.concat([issue_mix(unb, labels_df, s, e).assign(window=f"{s:%d %b}–{e:%d %b}", window_start=pd.Timestamp(s))
                             for s, e in wins])
            mix = mix[(mix["platform"] == tp) & (mix["unit"] == unit)] if not mix.empty else mix
            m1, m2 = st.columns([3, 2], gap="medium")
            with m1, st.container(key="panel_issues"):
                st.markdown("**What unhappy reviewers (≤3★) complain about**")
                if mix.empty:
                    st.caption("No labelled ≤3★ reviews in the last 8 weeks for this product.")
                else:
                    mix_order = mix.sort_values("window_start")["window"].unique().tolist()
                    bars = alt.Chart(mix).mark_bar(size=42).encode(
                        x=alt.X("window:O", sort=mix_order, title=None, axis=alt.Axis(labelAngle=0)),
                        y=alt.Y("sum(reviews):Q", title="reviews mentioning"),
                        color=alt.Color("code:N", title="issue", scale=alt.Scale(scheme="tableau10")),
                        tooltip=["window:N", "code:N", "reviews:Q", "labelled:Q", "low_reviews:Q"])
                    st.altair_chart(bars.properties(height=260).configure_view(strokeWidth=0).configure(background="#FFFFFF")
                                    .configure_axis(gridColor="#EEF0F4", labelColor="#4B5563", titleColor="#6B7280"),
                                    use_container_width=True)
                    cov = mix.groupby("window").agg(labelled=("labelled", "first"), low=("low_reviews", "first"))
                    st.caption("Label coverage (labelled / all ≤3★): " +
                               " · ".join(f"{w}: {r.labelled}/{r.low}" for w, r in cov.iterrows()))
            with m2, st.container(key="panel_amazon_says"):
                st.markdown("**Amazon's own summary**")
                row = latest_units[latest_units["unit"] == unit] if (tp == "amazon" and not latest_units.empty) else pd.DataFrame()
                if len(row) and "customers_say" in row and row["customers_say"].notna().any():
                    r = row[row["customers_say"].notna()].iloc[-1]
                    chips = [f"{a} · {n}" for a, n in json.loads(r["aspects"])] if r.get("aspects") else []
                    st.markdown(quote(r["customers_say"], chips), unsafe_allow_html=True)
                    st.caption("“Customers say”, generated by Amazon from its reviews; mention counts per aspect.")
                else:
                    st.caption("Shown for Amazon listings once the product page has been polled with the new fields.")

    # ---------------- returns summary (sheet data)
    with st.expander("Returns & exchanges by product (from the shared sheet)", expanded=False):
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
        st.caption("sku_name_conflicts = approved rows whose model name disagrees with the SKU code "
                   "(counted under the SKU's product). See Data quality.")
        st.dataframe(summary.sort_values("approved", ascending=False), width="stretch",
                     column_config={"top_issue_share": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                                    "return_pct": st.column_config.NumberColumn(format="percent"),
                                    "selling_pct": st.column_config.NumberColumn(format="percent")})
        if sales.empty:
            st.info("Return % and selling % appear here once a units-sold sheet is added (tabs.sales in the config).")
        st.markdown("**Monthly approved returns + exchanges**")
        st.bar_chart(approved.groupby(["month", "mode"]).size().unstack(fill_value=0))

    # ---------------- digest history
    with st.expander("Bi-weekly digests"):
        try:
            with sqlite3.connect(AMAZON_DB) as _con:
                dg = pd.read_sql("SELECT period_start, created_at, body FROM digest ORDER BY period_start DESC", _con)
        except Exception:  # noqa: BLE001
            dg = pd.DataFrame()
        if dg.empty:
            st.caption("The first digest is written on the next scheduled run, then every 14 days.")
        for r in dg.itertuples():
            st.markdown(f"**Period starting {r.period_start}** · written {r.created_at[:16]}")
            st.text(r.body)

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

with tab_src:
    from cultph.config import DATA_DIR as _DATA_DIR
    from cultph.pipeline import (explain_google_error, import_upload, save_default_source, sheet_id_from_url,
                                 sync_from)

    def show_result(r: dict):
        if r["published"]:
            st.success(f"Imported · verdict {r['status'].upper()} · the dashboard now shows this data.")
        else:
            st.error("Not imported: a data check failed, so the previous data stays live. See the failing checks below.")
        st.markdown("**Tabs recognised** (matched by their column headers)")
        st.dataframe(pd.DataFrame([{"layout": k, "tab": v} for k, v in r["tabs"].items()]), hide_index=True)
        st.markdown("**Rows read:** " + ", ".join(f"{k} {v:,}" for k, v in r["rows"].items()))
        ck = pd.DataFrame(r["checks"])[["check", "status", "detail"]]
        st.dataframe(ck.sort_values("status", key=lambda s: s.map({"fail": 0, "warn": 1, "pass": 2})),
                     hide_index=True, width="stretch")
        st.cache_data.clear()

    cfg_now = load_config()
    src = cfg_now.source
    st.markdown(section("Data sources", "where the returns, exchanges and tickets come from"), unsafe_allow_html=True)
    st.caption(f"Current source for scheduled syncs: **{src.get('type')}** · "
               f"{src.get('spreadsheet_id') or src.get('path')}")

    up, gs = st.columns(2, gap="medium")
    with up, st.container(key="panel_upload"):
        st.markdown("#### Upload a workbook")
        st.caption("Works with any version of the returns sheet (.xlsx). In Google Sheets: File → Download → "
                   "Microsoft Excel. Tabs are recognised by their columns, so renamed tabs are fine. The file is read "
                   "and then deleted; only the parsed rows are kept (no customer emails).")
        f = st.file_uploader("Workbook", type=["xlsx"], label_visibility="collapsed")
        if f is not None and st.button("Check & import", type="primary", key="do_upload"):
            with st.spinner("Reading and checking every row…"):
                try:
                    show_result(import_upload(f.getvalue(), f.name, cfg_now))
                except Exception as e:  # noqa: BLE001
                    st.error(f"Couldn't read that file: {e}")

    with gs, st.container(key="panel_gsheet"):
        st.markdown("#### Import from Google Sheets")
        st.caption("Sign in with the Gmail the sheet is shared with (your Cult account). The first time, a Google "
                   "window opens; the sign-in is then remembered on this computer.")
        has_client = (_DATA_DIR / "google_client.json").exists() or (_DATA_DIR / "google_service_account.json").exists()
        if not has_client:
            st.warning("Google sign-in isn't set up on this computer yet: save the OAuth client file as "
                       "`data/google_client.json` (SETUP.md → Google Sheets). Until then, use the upload.")
        url = st.text_input("Sheet link", placeholder="https://docs.google.com/spreadsheets/d/…", disabled=not has_client)
        keep = st.checkbox("Use this sheet for scheduled syncs", value=True, disabled=not has_client)
        if st.button("Sign in & import", type="primary", disabled=not (has_client and url), key="do_gsheet"):
            sid = sheet_id_from_url(url)
            if not sid:
                st.error("That doesn't look like a Google Sheets link.")
            else:
                from cultph.sources import GSheetSource

                with st.spinner("Opening the sheet with your Google account…"):
                    try:
                        source = GSheetSource(sid, src.get("auth", "oauth"))
                        r = sync_from(source, cfg_now, f"gsheet:{sid}")
                    except Exception as e:  # noqa: BLE001
                        st.error(explain_google_error(e))
                    else:
                        show_result(r)
                        if keep and r["published"]:
                            save_default_source({"type": "gsheet", "spreadsheet_id": sid,
                                                 "auth": src.get("auth", "oauth")})
                            st.info("Scheduled syncs now read this sheet.")

with tab_prod:
    import datetime as _dt2

    import altair as alt
    from cultph.config import env as _env2
    from cultph.insights import load_inputs as _load_inputs, latest_snapshots as _latest, pool_labels as _pool_labels, \
        unbiased_reviews as _unbiased, final_labels as _final_labels
    from cultph.products import LABELS as SIG_LABELS, WEIGHTS as SIG_WEIGHTS, build_board, movers
    from style import gauge, product_hero, product_tile, signal_bars, ticker

    @st.cache_data(ttl=300, show_spinner="Scoring products…")
    def products_data(today: _dt2.date):
        cfg = load_config()
        inp = _load_inputs(AMAZON_DB, LIVE_DB, cfg, last_runs(50))
        lat = _latest(inp.snaps)
        names = _pool_labels(lat) if not lat.empty else {}
        return build_board(inp, today, cfg.category_of()), inp, _unbiased(inp.reviews, names)

    today2 = _dt2.date.today()
    cards, pinp, punb = products_data(today2)
    sel = st.session_state.get("sel_product")

    if sel:
        c = next((x for x in cards if x.product == sel), None)
        if st.button("← All products", key="back_products"):
            st.session_state.pop("sel_product", None)
            st.rerun()
        if c:
            links = []
            for l in c.listings:
                if l["platform"] == "amazon":
                    links.append(f'<a href="https://www.amazon.in/dp/{l["id"]}" target="_blank">Amazon · {l["id"]}</a>')
                else:
                    links.append(f'<a href="https://www.flipkart.com/search?q={c.product.replace(" ", "+")}" '
                                 f'target="_blank">Flipkart · {l["id"]}</a>')
            with st.container(key="panel_hero"):
                st.markdown(product_hero(c, " · ".join(links)), unsafe_allow_html=True)
            k = st.columns(5)
            k[0].metric("Rating", f"{c.rating}★" if c.rating else "—", help=f"{c.platform.title()}, {c.ratings:,} ratings")
            k[1].metric("New reviews (14d)", c.new_reviews_14d)
            k[2].metric("Stars of new reviews", f"{c.series[-1][1]:.2f}★" if c.series else "—",
                        f"{c.delta_stars:+.2f} vs prev 14d" if c.delta_stars is not None else None)
            k[3].metric("Returns per day", f"{c.returns_per_day:.1f}" if c.returns_per_day is not None else "—",
                        f"{c.returns_ratio:.1f}× usual" if c.returns_ratio is not None else None, delta_color="inverse")
            k[4].metric("Safety mentions (90d)", c.safety_90d)

            left, right = st.columns([2, 3], gap="medium")
            with left, st.container(key="panel_signals"):
                st.markdown("**How the health score is made**")
                st.markdown(signal_bars(c.signals, SIG_LABELS, SIG_WEIGHTS), unsafe_allow_html=True)
                st.caption("Score = weighted average of the signals available for this product (missing ones are left out).")
            with right, st.container(key="panel_ratinghist"):
                st.markdown("**Marketplace rating over time**")
                sn = pinp.snaps[pinp.snaps["product"] == c.product].copy()
                if len(sn):
                    sn["platform"] = sn["platform"].fillna("amazon")
                    sn["day"] = pd.to_datetime(sn["captured_at"]).dt.normalize()
                    daily = sn.sort_values("captured_at").groupby(["platform", "asin", "day"]).tail(1)
                    ch = alt.Chart(daily).mark_line(point=True, strokeWidth=2.5).encode(
                        x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b")),
                        y=alt.Y("avg_rating:Q", title="rating", scale=alt.Scale(zero=False)),
                        color=alt.Color("platform:N", scale=alt.Scale(domain=["amazon", "flipkart"], range=["#F59E0B", "#2563EB"])),
                        detail="asin:N", tooltip=["platform", "asin", "day:T", "avg_rating", "total_ratings"])
                    st.altair_chart(ch.properties(height=230).configure_view(strokeWidth=0).configure(background="#FFFFFF"),
                                    use_container_width=True)
                    st.caption("One point per day per listing (history starts when tracking began).")

            a, b = st.columns(2, gap="medium")
            with a, st.container(key="panel_p_reviews"):
                st.markdown("**Stars of new reviews, per 14 days**")
                if c.series:
                    sdf = pd.DataFrame(c.series, columns=["window", "avg", "n"])
                    base = alt.Chart(sdf).encode(x=alt.X("window:O", sort=sdf["window"].tolist(), title=None,
                                                         axis=alt.Axis(labelAngle=0)))
                    bars = base.mark_bar(color="#E0E7FF", size=30).encode(y=alt.Y("n:Q", title="reviews"))
                    line = base.mark_line(color="#4F46E5", strokeWidth=3, point=True).encode(
                        y=alt.Y("avg:Q", title="avg ★", scale=alt.Scale(domain=[1, 5])), tooltip=["window", "avg", "n"])
                    st.altair_chart(alt.layer(bars, line).resolve_scale(y="independent").properties(height=230)
                                    .configure_view(strokeWidth=0).configure(background="#FFFFFF"), use_container_width=True)
                else:
                    st.caption("Not enough recent reviews yet.")
            with b, st.container(key="panel_p_returns"):
                st.markdown("**Approved returns + exchanges per month** (shared sheet)")
                ap = pinp.approved[pinp.approved["product"] == c.product] if not pinp.approved.empty else pd.DataFrame()
                if len(ap):
                    rm = ap.groupby(["month", "mode"]).size().rename("n").reset_index()
                    ch = alt.Chart(rm).mark_bar(size=22).encode(
                        x=alt.X("month:O", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("n:Q", title="claims"),
                        color=alt.Color("mode:N", scale=alt.Scale(domain=["Exchange", "Return"], range=["#818CF8", "#F87171"])),
                        tooltip=["month", "mode", "n"])
                    st.altair_chart(ch.properties(height=230).configure_view(strokeWidth=0).configure(background="#FFFFFF"),
                                    use_container_width=True)
                    top_iss = ap["issue"].value_counts().head(5)
                    st.caption("Top return reasons: " + " · ".join(f"{i} ({n})" for i, n in top_iss.items()))
                else:
                    st.caption("No returns recorded for this product in the sheet.")

            c1, c2 = st.columns([3, 2], gap="medium")
            with c1, st.container(key="panel_p_lowreviews"):
                st.markdown("**Latest unhappy reviews (≤2★)**")
                rv = pinp.reviews[(pinp.reviews["product"] == c.product) & (pinp.reviews["rating"] <= 2)]
                rv = rv.sort_values("review_date", ascending=False).head(8)
                if len(rv):
                    fl = _final_labels(pinp.labels)[["review_id", "final_codes"]] if not pinp.labels.empty else None
                    if fl is not None:
                        rv = rv.merge(fl, on="review_id", how="left")
                    for r in rv.itertuples():
                        codes = getattr(r, "final_codes", None)
                        tag = f" · `{codes}`" if isinstance(codes, str) and codes else ""
                        st.markdown(f"**{'★' * int(r.rating)}** {r.title or ''} · <span style='color:#6B7280'>"
                                    f"{r.review_date} · {r.platform}</span>{tag}  \n{(r.body or '')[:280]}",
                                    unsafe_allow_html=True)
                else:
                    st.caption("No 1–2★ reviews stored for this product.")
            with c2, st.container(key="panel_p_summary"):
                st.markdown("**Amazon's own summary**")
                sn = pinp.snaps[(pinp.snaps["product"] == c.product)]
                if "customers_say" in sn and sn["customers_say"].notna().any():
                    r = sn[sn["customers_say"].notna()].sort_values("captured_at").iloc[-1]
                    chips = [f"{a} · {n}" for a, n in json.loads(r["aspects"])] if r.get("aspects") else []
                    st.markdown(quote(r["customers_say"], chips), unsafe_allow_html=True)
                else:
                    st.caption("Appears after the next Amazon poll for this product.")
        else:
            st.info("That product isn't in the data anymore.")
    else:
        up, down = movers(cards)
        st.markdown(section("Products", f"{len(cards)} products · photos for "
                                        f"{sum(1 for c in cards if c.image_url)} · health score 0–100"),
                    unsafe_allow_html=True)
        if up or down:
            st.markdown(ticker([(c.product, c.delta_stars) for c in up + down]), unsafe_allow_html=True)
            st.caption("Movers: change in the average stars of new reviews, last 14 days vs the 14 before (min 8 reviews each).")
        f1, f2, f3 = st.columns([2, 2, 3])
        sort_by = f1.selectbox("Sort", ["Health score (worst first)", "Health score (best first)", "Rating",
                                        "Most ratings", "Name"], key="p_sort")
        grade = f2.multiselect("Health", ["At risk", "Watch", "Healthy", "n/a"], key="p_grade")
        q = f3.text_input("Search", placeholder="Search products", key="p_q", label_visibility="visible")
        view = [c for c in cards if (cat == "All" or c.category == cat) and (not grade or c.grade in grade)
                and (not q or q.lower() in c.product.lower())]
        key_fn = {"Health score (worst first)": lambda c: (c.score is None, c.score or 0),
                  "Health score (best first)": lambda c: (c.score is None, -(c.score or 0)),
                  "Rating": lambda c: (c.rating is None, -(c.rating or 0)),
                  "Most ratings": lambda c: -c.ratings, "Name": lambda c: c.product}[sort_by]
        view.sort(key=key_fn)
        per_row = 4
        for start in range(0, len(view), per_row):
            cols = st.columns(per_row, gap="small")
            for col, c in zip(cols, view[start:start + per_row]):
                with col, st.container(key=f"tile_{start}_{c.product.replace(' ', '_').replace('/', '_')}"):
                    st.markdown(product_tile(c), unsafe_allow_html=True)
                    if st.button("View details →", key=f"open_{c.product}", use_container_width=True):
                        st.session_state["sel_product"] = c.product
                        st.rerun()
        st.caption("Health score: rating 35% · stars of new reviews 25% · 1–2★ share 15% · safety 10% · returns trend 15%. "
                   "Products with no marketplace listing show “–”. Open a product for the full breakdown.")

