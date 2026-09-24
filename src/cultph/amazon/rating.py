"""How many ratings are needed to reach / hold a target average (default 4.1).

Findings from real pages (Phase 2 spike):
- The product-page histogram is Amazon's *weighted* distribution, not raw
  counts (6 ratings can show 64% / 15% / 21%). The displayed average matches
  the histogram's weighted mean to about ±0.02.
- Percentages are rounded to whole numbers, so the weighted mean is known only
  within a small range.

So the calculator treats the displayed rating as a weighted mean over N ratings
and answers with a (best, worst) range. New ratings on Amazon are also
weighted, so these are estimates to steer by, not guarantees."""

from __future__ import annotations

import math


def avg_range(hist_pct: dict[int, float]) -> tuple[float, float]:
    """Bounds of sum(s * q_s) / 100 where each true share q_s lies within
    ±0.5 of its displayed whole-number percentage and the shares sum to 100."""
    lo = {s: max(0.0, hist_pct.get(s, 0) - 0.5) for s in range(1, 6)}
    hi = {s: min(100.0, hist_pct.get(s, 0) + 0.5) for s in range(1, 6)}
    if sum(lo.values()) > 100 or sum(hi.values()) < 100:
        raise ValueError(f"histogram {hist_pct} cannot sum to 100%")

    def fill(order):
        q = dict(lo)
        left = 100 - sum(q.values())
        for s in order:
            add = min(left, hi[s] - q[s])
            q[s] += add
            left -= add
        return sum(s * v for s, v in q.items()) / 100

    return fill([1, 2, 3, 4, 5]), fill([5, 4, 3, 2, 1])


def five_stars_needed(n: int, avg: float, target: float = 4.1) -> int:
    """Smallest k with (avg*n + 5k) / (n + k) >= target."""
    if n == 0 or avg >= target:
        return 0
    return max(0, math.ceil((target - avg) * n / (5 - target) - 1e-9))


def one_stars_absorbable(n: int, avg: float, target: float = 4.1) -> int:
    """Largest m with (avg*n + m) / (n + m) >= target."""
    if n == 0 or avg < target:
        return 0
    return math.floor((avg - target) * n / (target - 1) + 1e-9)


def required_share_of_five(hist_pct: dict[int, float], target: float = 4.1) -> float:
    """Share of new ratings that must be 5-star (the rest in today's 1-4 star
    mix) for new ratings to average the target."""
    others = {s: hist_pct.get(s, 0) for s in (1, 2, 3, 4)}
    tot = sum(others.values())
    if tot == 0:
        return 0.0
    avg_other = sum(s * p for s, p in others.items()) / tot
    if avg_other >= target:
        return 0.0
    return (target - avg_other) / (5 - avg_other)


def displayed_band(shown: float) -> tuple[float, float]:
    """A rating displayed as 4.0 (round half up to one decimal) lies in [3.95, 4.05)."""
    return shown - 0.05, shown + 0.05 - 1e-9


def mean_range(hist_pct: dict[int, float], shown: float | None) -> tuple[float, float, bool]:
    """Histogram range narrowed by the displayed rating. Returns (lo, hi, consistent);
    if the two don't overlap, falls back to the histogram range with consistent=False."""
    h_lo, h_hi = avg_range(hist_pct)
    if shown is None:
        return h_lo, h_hi, True
    d_lo, d_hi = displayed_band(shown)
    lo, hi = max(h_lo, d_lo), min(h_hi, d_hi)
    if lo > hi:
        return h_lo, h_hi, False
    return lo, hi, True


def plan(n: int, hist_pct: dict[int, float], target: float = 4.1, shown: float | None = None) -> dict:
    a_min, a_max, consistent = mean_range(hist_pct, shown)
    return {
        "n": n,
        "consistent": consistent,
        "avg_range": (round(a_min, 3), round(a_max, 3)),
        "five_star_needed": (five_stars_needed(n, a_max, target), five_stars_needed(n, a_min, target)),
        "one_star_absorbable": (one_stars_absorbable(n, a_min, target), one_stars_absorbable(n, a_max, target)),
        "five_star_share_needed": required_share_of_five(hist_pct, target),
        "target": target,
    }


def effective_target(target: float, mode: str = "displayed") -> float:
    """Amazon shows one decimal, so 'display 4.1' means a weighted mean >= 4.05.
    mode='exact' requires the mean itself to reach the target."""
    return round(target - 0.05, 4) if mode == "displayed" else target
