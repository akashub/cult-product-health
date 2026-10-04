from datetime import date, timedelta

import pandas as pd

from cultph.insights import Inputs
from cultph.products import build_board

TODAY = date(2026, 10, 4)


def _snap(asin, product, platform, avg, n, pool=None):
    return {"asin": asin, "parent_asin": pool or asin, "product": product, "platform": platform,
            "captured_at": "2026-10-03T10:00:00", "avg_rating": avg, "total_ratings": n, "p5": 50, "p4": 20,
            "p3": 10, "p2": 5, "p1": 15, "hist_avg_min": avg, "hist_avg_max": avg, "c5": None, "image_url": None}


def _inputs(snaps, approved=None):
    return Inputs(pd.DataFrame(columns=["review_id", "asin", "parent_asin", "product", "platform", "rating", "title",
                                        "body", "review_date", "date_precision", "source", "first_seen_at"]),
                  pd.DataFrame(columns=["review_id", "codes", "severity", "status", "human_verdict", "human_codes",
                                        "issues"]),
                  pd.DataFrame(snaps), approved if approved is not None else pd.DataFrame())


def test_rating_signal_is_ratings_weighted_and_headline_is_amazon():
    cards = {c.product: c for c in build_board(_inputs([_snap("A1", "Edge", "amazon", 3.0, 100),
                                                         _snap("F1", "Edge", "flipkart", 4.5, 100)]), TODAY, {})}
    c = cards["Edge"]
    assert c.rating == 3.0 and c.platform == "amazon"             # Amazon is the headline
    assert c.signals["rating"][0] == 50.0                         # (3.75 - 3.0) / 1.5
    assert set(c.platform_ratings) == {"amazon", "flipkart"}


def test_no_marketplace_rating_means_no_score():
    appr = pd.DataFrame([{"product": "Orphan", "event_date": (date(2026, 9, 1) - timedelta(days=30 * k)).isoformat(),
                          "month": f"2026-{9 - k:02d}", "issue": "x", "mode": "Return", "order_id": str(k)}
                         for k in range(4)])
    cards = {c.product: c for c in build_board(_inputs([_snap("A1", "Rated", "amazon", 4.4, 50)], appr), TODAY, {})}
    assert cards["Orphan"].score is None and cards["Rated"].score is not None
