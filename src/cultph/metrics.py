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


def monthly_counts(df: pd.DataFrame) -> pd.Series:
    """Row counts per YYYY-MM (month labels are checked against dates separately)."""
    if df.empty:
        return pd.Series(dtype=int)
    return df["month"].value_counts().sort_index()


def dashboard_recompute(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Recreates the sheet's 'Approved' and 'Total' (approved + pending) columns,
    one row per month present in either tab, oldest first."""
    appr = monthly_counts(tables.get("approved", pd.DataFrame()))
    pend = monthly_counts(tables.get("pending", pd.DataFrame()))
    months = sorted(set(appr.index) | set(pend.index))
    return pd.DataFrame([{"month": m, "approved": int(appr.get(m, 0)), "total": int(appr.get(m, 0) + pend.get(m, 0))}
                         for m in months])


def find_block(grid: list[list], left_header: str, right_header: str) -> list[tuple]:
    """Locates two adjacent header cells (e.g. 'Approved' | 'Total') and returns
    the numeric pairs below them until the first blank row. The last pair is the
    grand total row."""
    for r, row in enumerate(grid):
        for c, v in enumerate(row):
            if str(v).strip() == left_header and c + 1 < len(row) and str(row[c + 1]).strip() == right_header:
                out = []
                for below in grid[r + 1:]:
                    a = below[c] if c < len(below) else None
                    b = below[c + 1] if c + 1 < len(below) else None
                    if a in (None, "") and b in (None, ""):
                        break
                    out.append((a, b))
                return out
    raise LookupError(f"header cells {left_header!r} | {right_header!r} not found")


def rows_for(df: pd.DataFrame, selection: dict) -> pd.DataFrame:
    """Source rows behind one breakdown row: the inverse of breakdown()."""
    mask = pd.Series(True, index=df.index)
    for col, val in selection.items():
        mask &= df[col].fillna("(blank)").astype(str) == str(val)
    return df[mask]
