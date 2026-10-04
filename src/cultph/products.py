"""Per-product board: one card per product with its photo, marketplace ratings, a
transparent 0-100 health score and the series behind its sparklines.

Health score = weighted mean of the signals that exist for the product (missing
ones are left out and the weights renormalised; the card says "N of 5 signals").
A product with no marketplace rating gets no score ("not enough data"):

  rating      35%  marketplace rating (ratings-weighted across Amazon and Flipkart): 3.0 -> 0, 4.5 -> 100
  recent      25%  avg stars of new reviews in the last 28 days (needs MIN_N): 1 -> 0, 5 -> 100
  negative    15%  share of 1-2 star among those reviews: 0% -> 100, 50%+ -> 0
  safety      10%  safety-labelled reviews in 90 days: 0 -> 100, 1 -> 60, 2 -> 30, 3+ -> 0
  returns     15%  latest returns per day vs the 3 months before: <=1x -> 100, 2x+ -> 0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd

from .insights import MIN_N, Inputs, _safety, latest_snapshots, pool_labels, review_trends, unbiased_reviews

WEIGHTS = {"rating": 0.35, "recent": 0.25, "negative": 0.15, "safety": 0.10, "returns": 0.15}
LABELS = {"rating": "Marketplace rating", "recent": "Stars of new reviews (28d)", "negative": "1–2★ share (28d)",
          "safety": "Safety mentions (90d)", "returns": "Returns trend"}


def _clamp(x: float) -> float:
    return max(0.0, min(100.0, x))


@dataclass
class ProductCard:
    product: str
    category: str
    image_url: str | None
    score: float | None
    signals: dict = field(default_factory=dict)        # name -> (score 0-100, explanation)
    rating: float | None = None                        # headline rating (platform with most ratings)
    ratings: int = 0
    platform: str = ""
    delta_stars: float | None = None                   # avg stars of new reviews: latest complete window vs previous
    series: list = field(default_factory=list)         # [(window label, avg stars, n)] oldest first
    listings: list = field(default_factory=list)       # [{platform, id, rating, ratings, shared_with}]
    platform_ratings: dict = field(default_factory=dict)  # platform -> (rating, ratings)
    new_reviews_14d: int = 0
    returns_per_day: float | None = None
    returns_ratio: float | None = None
    safety_90d: int = 0

    @property
    def grade(self) -> str:
        if self.score is None:
            return "n/a"
        return "Healthy" if self.score >= 75 else "Watch" if self.score >= 55 else "At risk"


def _returns_trend(approved: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """product -> (returns per day in the latest month, ratio vs the 3 months before)."""
    if approved.empty:
        return {}
    a = approved.dropna(subset=["product"]).copy()
    a["d"] = pd.to_datetime(a["event_date"], format="ISO8601")
    months = sorted(a["month"].unique())
    if len(months) < 4:
        return {}
    last, base = months[-1], months[-4:-1]
    covered = int(a.loc[a["month"] == last, "d"].max().day)
    days = {m: pd.Period(m).days_in_month for m in months}
    per = a.groupby(["product", "month"]).size().unstack(fill_value=0)
    out = {}
    for prod, row in per.iterrows():
        rate = row[last] / covered
        base_rate = sum(row[m] / days[m] for m in base) / len(base)
        out[prod] = (rate, rate / base_rate if base_rate > 0 else (2.0 if rate > 0 else 1.0))
    return out


def build_board(inp: Inputs, today: date, category_of: dict[str, str]) -> list[ProductCard]:
    latest = latest_snapshots(inp.snaps)
    names = pool_labels(latest) if not latest.empty else {}
    unb = unbiased_reviews(inp.reviews, names)
    trends = review_trends(unb, today, 8)
    safety = _safety(inp, today, 90)
    returns = _returns_trend(inp.approved)

    products = set(latest["product"].dropna()) if not latest.empty else set()
    products |= set(returns)
    cards = []
    for prod in sorted(products):
        lst = latest[latest["product"] == prod] if not latest.empty else latest
        listings, units = [], set()
        for r in lst.itertuples():
            pool = r.pool
            shared = [p for p in latest.loc[latest["pool"] == pool, "product"].unique() if p != prod] \
                if r.platform == "amazon" else []
            rating = r.avg_rating if pd.notna(r.avg_rating) else round(r.hist_avg_min, 1)
            img = getattr(r, "image_url", None)
            listings.append({"platform": r.platform, "id": r.asin, "rating": rating, "ratings": int(r.total_ratings),
                             "shared_with": shared, "image": img if isinstance(img, str) and img.startswith("http") else None})
            units.add((r.platform, names.get(pool, prod) if shared else prod))
        top = max(listings, key=lambda x: x["ratings"]) if listings else None
        by_plat = {}
        for x in listings:                                   # the listing with most ratings speaks per platform
            if x["platform"] not in by_plat or x["ratings"] > by_plat[x["platform"]]["ratings"]:
                by_plat[x["platform"]] = x
        headline = by_plat.get("amazon") or by_plat.get("flipkart")
        image = next((x["image"] for x in sorted(listings, key=lambda x: -x["ratings"]) if x.get("image")), None)
        if image is None and "image_url" in inp.snaps:
            any_img = inp.snaps[(inp.snaps["product"] == prod) & inp.snaps["image_url"].fillna("").str.startswith("http")]
            image = any_img.sort_values("captured_at")["image_url"].iloc[-1] if len(any_img) else None

        # review series from the platform with the most reviews in the window set
        t = trends[trends.apply(lambda r: (r["platform"], r["unit"]) in units, axis=1)] if len(trends) and units else trends.iloc[0:0]
        series, delta, recent_n, recent_avg, recent_neg, new14 = [], None, 0, None, None, 0
        if len(t):
            best = t.groupby(["platform", "unit"])["n"].sum().idxmax()
            tt = t[(t["platform"] == best[0]) & (t["unit"] == best[1])].sort_values("start")
            comp = tt[tt["complete"] & (tt["n"] > 0)]
            series = [(r.label, round(r.avg_stars, 2), int(r.n)) for r in comp.itertuples()]
            new14 = int(tt[tt["window"] == 0]["n"].sum())
            last2 = tt[tt["window"] <= 1]
            recent_n = int(last2["n"].sum())
            if recent_n:
                w = unb[(unb["platform"] == best[0]) & (unb["unit"] == best[1]) &
                        (unb["day"] >= today - timedelta(days=27))]
                recent_avg, recent_neg = w["rating"].mean(), (w["rating"] <= 2).mean()
            c0 = tt[(tt["window"] == 0) & tt["complete"]]
            c1 = tt[(tt["window"] == 1) & tt["complete"]]
            if len(c0) and len(c1) and c0.iloc[0]["n"] >= MIN_N and c1.iloc[0]["n"] >= MIN_N:
                delta = round(c0.iloc[0]["avg_stars"] - c1.iloc[0]["avg_stars"], 2)

        n_safety = sum(len(g) for (plat, unit), g in safety.items() if unit == prod or (plat, unit) in units)
        sig = {}
        if by_plat:
            tot = sum(x["ratings"] for x in by_plat.values()) or 1
            w_rating = sum(x["rating"] * x["ratings"] for x in by_plat.values()) / tot
            sig["rating"] = (_clamp((w_rating - 3.0) / 1.5 * 100),
                             " · ".join(f"{pl.title()} {x['rating']}★ ({x['ratings']:,})" for pl, x in sorted(by_plat.items())))
        if recent_avg is not None and recent_n >= MIN_N:
            sig["recent"] = (_clamp((recent_avg - 1) / 4 * 100), f"{recent_avg:.2f}★ from {recent_n} new reviews")
            sig["negative"] = (_clamp((1 - recent_neg / 0.5) * 100), f"{recent_neg:.0%} are 1–2★")
        if listings or prod in returns:
            sig["safety"] = ({0: 100, 1: 60, 2: 30}.get(n_safety, 0), f"{n_safety} in 90 days")
        rpd, ratio = returns.get(prod, (None, None))
        if ratio is not None:
            sig["returns"] = (_clamp((2 - ratio) * 100), f"{rpd:.1f}/day, {ratio:.1f}× usual")
        wsum = sum(WEIGHTS[k] for k in sig)
        score = round(sum(WEIGHTS[k] * v[0] for k, v in sig.items()) / wsum, 0) if wsum and "rating" in sig else None

        cards.append(ProductCard(
            product=prod, category=category_of.get(prod, "massager"), image_url=image, score=score, signals=sig,
            rating=headline["rating"] if headline else None, ratings=headline["ratings"] if headline else 0,
            platform=headline["platform"] if headline else "", delta_stars=delta, series=series, listings=listings,
            platform_ratings={pl: (x["rating"], x["ratings"]) for pl, x in by_plat.items()},
            new_reviews_14d=new14, returns_per_day=rpd, returns_ratio=ratio, safety_90d=n_safety))
    return cards


def movers(cards: list[ProductCard], k: int = 5) -> tuple[list[ProductCard], list[ProductCard]]:
    moved = [c for c in cards if c.delta_stars is not None]
    up = sorted([c for c in moved if c.delta_stars > 0], key=lambda c: -c.delta_stars)[:k]
    down = sorted([c for c in moved if c.delta_stars < 0], key=lambda c: c.delta_stars)[:k]
    return up, down
