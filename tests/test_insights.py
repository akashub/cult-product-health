from datetime import date, timedelta

import pandas as pd

from cultph.insights import (
    CAPS, Inputs, build_insights, coverage_cutoffs, digest_period, issue_mix, pool_labels, review_trends,
    save_digest, unbiased_reviews, windows,
)

TODAY = date(2026, 9, 25)


def _snap(asin, product, pool, platform="amazon", avg=3.5, n=100):
    return {"asin": asin, "parent_asin": pool, "product": product, "platform": platform, "captured_at": "2026-09-24T10:00:00",
            "avg_rating": avg, "total_ratings": n, "p5": 50, "p4": 10, "p3": 10, "p2": 10, "p1": 20,
            "hist_avg_min": avg - 0.02, "hist_avg_max": avg + 0.02, "c5": None}


def _rev(i, day, rating, asin="A1", pool="P1", product=None, source="listing:recent:all", platform="amazon", prec="day"):
    return {"review_id": f"R{asin}{i}", "asin": asin, "parent_asin": pool, "product": product, "platform": platform,
            "rating": rating, "title": "t", "body": "b", "review_date": day.isoformat(), "date_precision": prec,
            "source": source, "first_seen_at": "2026-09-24T10:00:00"}


def test_pool_labels_join_products_and_disambiguate():
    latest = pd.DataFrame([_snap("A1", "Impact", "P1"), _snap("A2", "Flex", "P1"), _snap("A3", "Halo", "A3"),
                           _snap("F1", "Volt", "F1", "flipkart"), _snap("F2", "Volt", "F2", "flipkart")])
    latest["pool"] = latest["parent_asin"].fillna(latest["asin"])
    names = pool_labels(latest)
    assert names["P1"] == "Flex / Impact" and names["A3"] == "Halo"
    assert names["F1"] != names["F2"] and names["F1"].startswith("Volt ·")


def test_only_unbiased_reviews_count():
    revs = pd.DataFrame([_rev(1, TODAY, 5, source="product_page"), _rev(2, TODAY, 1, source="listing:recent:all"),
                         _rev(3, TODAY, 1, source="listing:recent:one_star"),
                         _rev(4, TODAY, 5, asin="F1", pool="F1", platform="flipkart", source="flipkart:recent:p1"),
                         _rev(5, TODAY, 5, asin="F1", pool="F1", platform="flipkart", source="flipkart:recent:p1", prec="month")])
    unb = unbiased_reviews(revs, {"P1": "Impact", "F1": "Volt"})
    assert sorted(unb["review_id"]) == ["RA12", "RF14"]


def test_capped_listing_marks_older_windows_incomplete():
    # 100 reviews (the Amazon cap) spread over the last 20 days -> data older than that is missing
    revs = pd.DataFrame([_rev(i, TODAY - timedelta(days=i % 20), 4) for i in range(CAPS["amazon"])])
    unb = unbiased_reviews(revs, {"P1": "Impact"})
    assert coverage_cutoffs(unb)[("amazon", "Impact")] == TODAY - timedelta(days=19)
    t = review_trends(unb, TODAY, 3).set_index("window")
    assert t.loc[0, "complete"] and not t.loc[1, "complete"] and not t.loc[2, "complete"]
    # an uncapped listing has no cutoff
    small = unbiased_reviews(pd.DataFrame([_rev(i, TODAY - timedelta(days=30 + i), 4) for i in range(10)]), {"P1": "Impact"})
    assert coverage_cutoffs(small) == {}


def _inputs(revs, labels=None, snaps=None):
    snaps = pd.DataFrame(snaps or [_snap("A1", "Impact", "P1")])
    return Inputs(pd.DataFrame(revs), pd.DataFrame(labels or [], columns=["review_id", "codes", "severity", "status",
                                                                            "human_verdict", "human_codes", "issues"]),
                  snaps)


def test_worsening_needs_minimum_sample_in_both_windows():
    prev = [_rev(i, TODAY - timedelta(days=20), 5) for i in range(10)]
    few_bad = [_rev(100 + i, TODAY - timedelta(days=2), 1) for i in range(5)]      # n=5 < MIN_N
    keys = [i.key for i in build_insights(_inputs(prev + few_bad), TODAY)]
    assert not any(k.startswith("worse:") for k in keys)
    many_bad = [_rev(200 + i, TODAY - timedelta(days=2), 1) for i in range(9)]
    keys = [i.key for i in build_insights(_inputs(prev + many_bad), TODAY)]
    assert any(k.startswith("worse:amazon:") for k in keys)


def test_issue_mix_reports_coverage_and_spikes_are_suppressed_when_low():
    revs = [_rev(i, TODAY - timedelta(days=3), 1) for i in range(10)] + \
           [_rev(50 + i, TODAY - timedelta(days=40), 1) for i in range(10)]
    labels = [{"review_id": f"RA1{i}", "codes": "charging", "severity": "medium", "status": "auto",
               "human_verdict": None, "human_codes": None, "issues": "[]"} for i in range(5)]   # 5/10 = 50% coverage
    unb = unbiased_reviews(pd.DataFrame(revs), {"P1": "Impact"})
    mix = issue_mix(unb, pd.DataFrame(labels), TODAY - timedelta(days=27), TODAY)
    assert mix.iloc[0]["coverage"] == 0.5 and mix.iloc[0]["labelled"] == 5
    keys = [i.key for i in build_insights(_inputs(revs, labels), TODAY)]
    assert not any(k.startswith("issue:") for k in keys)


def test_windows_and_digest_period():
    w = windows(TODAY, 2)
    assert w[0] == (TODAY - timedelta(days=13), TODAY) and w[1][1] == TODAY - timedelta(days=14)
    p = digest_period(TODAY)
    assert (TODAY - p).days < 14 and p.weekday() == 0
    assert digest_period(p) == p and digest_period(p + timedelta(days=13)) == p


def test_save_digest_once_per_period(tmp_path):
    import sqlite3
    con = sqlite3.connect(tmp_path / "d.db")
    ins = build_insights(_inputs([_rev(1, TODAY, 5)]), TODAY)
    assert save_digest(con, TODAY, ins)[0] is True
    assert save_digest(con, TODAY + timedelta(days=1), ins)[0] is False
    assert save_digest(con, digest_period(TODAY) + timedelta(days=14), ins)[0] is True
