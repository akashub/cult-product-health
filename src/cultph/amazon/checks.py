"""Judge for scraped pages. A page that fails is not stored."""

from __future__ import annotations

from .rating import avg_range


def check_product(parsed: dict) -> list[str]:
    """Returns a list of problems; empty means the snapshot is safe to store."""
    errs = []
    n, avg, hist = parsed.get("total_ratings"), parsed.get("avg_rating"), parsed.get("hist_pct") or {}
    if not isinstance(n, int) or n <= 0:
        errs.append(f"total_ratings not a positive integer: {n!r}")
    if avg is None or not 1 <= avg <= 5:
        errs.append(f"avg_rating out of range: {avg!r}")
    if sorted(hist) != [1, 2, 3, 4, 5]:
        errs.append(f"histogram incomplete: {hist}")
    elif not 97 <= sum(hist.values()) <= 103:  # five values each rounded by at most 0.5
        errs.append(f"histogram sums to {sum(hist.values())}%")
    errs += check_reviews(parsed.get("reviews", []))
    return errs


def check_reviews(reviews: list[dict]) -> list[str]:
    errs = []
    ids = [r["review_id"] for r in reviews]
    if len(ids) != len(set(ids)):
        errs.append("duplicate review ids on page")
    for r in reviews:
        if r["rating"] not in (1, 2, 3, 4, 5):
            errs.append(f"{r['review_id']}: rating {r['rating']!r}")
        if not (r["title"] or r["body"]):
            errs.append(f"{r['review_id']}: empty title and body")
    return errs


def implied_gap(parsed: dict) -> float:
    """Displayed average minus the histogram's weighted mean (midpoint of its
    rounding range). Large gaps mean the page and histogram disagree."""
    a_min, a_max = avg_range(parsed["hist_pct"])
    return round(parsed["avg_rating"] - (a_min + a_max) / 2, 3)
