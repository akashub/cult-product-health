"""Cult Product Health dashboard. Run: uv run streamlit run app/dashboard.py

Every chart/table has a drill-down: select a row to see the exact source rows
(sheet tab + row number) that produced the number."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from cultph.db import LIVE_DB, last_runs, read_table
from cultph.metrics import breakdown, rows_for

st.set_page_config(page_title="Cult Product Health", layout="wide")

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


approved, pending, wms, tickets = load("approved"), load("pending"), load("wms"), load("tickets")
checks = load("checks")

# ---------- sidebar: pipeline health ----------
with st.sidebar:
    st.header("Pipeline health")
    runs = last_runs(1)
    if runs:
        r = runs[0]
        st.metric("Last sync", r["status"].upper(), help=r["at"])
        st.caption(f"{r['at']} · {'published' if r['published'] else 'blocked'}")
    st.dataframe(checks[["check", "status"]], hide_index=True, width="stretch")
    with st.expander("Check details"):
        for c in checks.itertuples():
            st.markdown(f"**{c.check}** — {c.status}  \n{c.detail}")

st.title("Cult Product Health")
st.caption("Phase 1 · returns, exchanges and support tickets from the shared sheet. "
           "Return % needs units-sold data (not in the sheet yet).")

tab_over, tab_ret, tab_tix, tab_pend, tab_wms, tab_dq = st.tabs(
    ["Overview", "Returns & exchanges", "Support tickets", "Pending verification", "Warehouse returns", "Data quality"])

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
    summary["top_issue"] = top_issue["issue"]
    summary["top_issue_share"] = (top_issue["n"] / summary["approved"]).round(3)
    st.subheader("By product")
    st.dataframe(summary.sort_values("approved", ascending=False), width="stretch",
                 column_config={"top_issue_share": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
    st.subheader("Monthly trend (approved returns + exchanges)")
    st.bar_chart(approved.groupby(["month", "mode"]).size().unstack(fill_value=0))

with tab_ret:
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
