"""Deterministic metrics. Every result carries its numerator and denominator
so the dashboard can show exactly which rows produced it."""

from __future__ import annotations

import pandas as pd


def breakdown(df: pd.DataFrame, by: str | list[str], within: str | list[str] | None = None) -> pd.DataFrame:
    """Count rows per `by`, with share of the total inside each `within` group
    (or of the whole frame). Columns: *by, count, denominator, share."""
    by = [by] if isinstance(by, str) else list(by)
    within = [within] if isinstance(within, str) else list(within or [])
    keys = within + [c for c in by if c not in within]
    if df.empty:
        return pd.DataFrame(columns=keys + ["count", "denominator", "share"])
    g = df.fillna({c: "(blank)" for c in keys}).groupby(keys, dropna=False).size().rename("count").reset_index()
    if within:
        g["denominator"] = g.groupby(within)["count"].transform("sum")
    else:
        g["denominator"] = len(df)
    g["share"] = g["count"] / g["denominator"]
    return g.sort_values(within + ["count"], ascending=[True] * len(within) + [False]).reset_index(drop=True)


def monthly_by_label(df: pd.DataFrame) -> pd.Series:
    """Row counts per month label number, the way the hand-made pivots count."""
    if df.empty:
        return pd.Series(dtype=int)
    return df["month_label_num"].dropna().astype(int).value_counts().sort_index()


def dashboard_recompute(tables: dict[str, pd.DataFrame], months: list[int]) -> pd.DataFrame:
    """Recreates the sheet's 'Approved' and 'Total' (approved + pending) columns."""
    appr = monthly_by_label(tables.get("approved", pd.DataFrame()))
    pend = monthly_by_label(tables.get("pending", pd.DataFrame()))
    rows = [{"month": m, "approved": int(appr.get(m, 0)), "total": int(appr.get(m, 0) + pend.get(m, 0))} for m in months]
    return pd.DataFrame(rows)


def rows_for(df: pd.DataFrame, selection: dict) -> pd.DataFrame:
    """Source rows behind one breakdown row: the inverse of breakdown()."""
    mask = pd.Series(True, index=df.index)
    for col, val in selection.items():
        mask &= df[col].fillna("(blank)").astype(str) == str(val)
    return df[mask]
