from datetime import datetime

import pandas as pd

from cultph.ai import store as label_store
from cultph.alerts import evaluate
from cultph.amazon import store

AMZ = {"target_rating": 4.1, "target_mode": "displayed"}
ALR = {"recent_days": 14, "return_spike_x": 2.0, "return_spike_min": 5}
BASE = {"country": "India", "verified": True, "variant": None, "helpful_votes": 0}


def snap(con, asin, avg, hist, at):
    con.execute("INSERT INTO rating_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (asin, asin, "Gun A", at, avg, 100, hist[5], hist[4], hist[3], hist[2], hist[1], 0, 0))


def review(con, rid, rating, rdate, seen):
    store.upsert_reviews(con, "A1", "Gun A", [{"review_id": rid, "rating": rating, "title": "t", "body": "b",
                                              "review_date": rdate, **BASE}], "x")
    con.execute("UPDATE review SET first_seen_at = ? WHERE review_id = ?", (seen, rid))


def test_first_run_is_baseline_only(tmp_path):
    con = store.connect(tmp_path / "a.db")
    review(con, "OLD", 1, "2026-09-20", "2026-09-24T09:00:00")
    assert evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10)) == []


def test_new_recent_low_review_alerts_once(tmp_path):
    con = store.connect(tmp_path / "a.db")
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10))                      # baseline 10:00
    review(con, "NEW", 1, "2026-09-24", "2026-09-24T11:00:00")                  # posted today, seen after baseline
    review(con, "BACKFILL", 1, "2025-01-10", "2026-09-24T11:00:00")            # seen after baseline but old
    review(con, "GOOD", 5, "2026-09-24", "2026-09-24T11:00:00")
    got = evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 12))
    assert [(a.rule, a.object_key) for a in got] == [("low_review", "NEW")]
    assert evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 13)) == []        # deduplicated


def test_safety_label_is_urgent_and_first(tmp_path):
    con = store.connect(tmp_path / "a.db")
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10))
    review(con, "HOT", 2, "2026-09-24", "2026-09-24T11:00:00")
    label_store.ensure(con)
    con.execute("INSERT INTO review_label (review_id, prompt_version, severity, issues, status) VALUES "
                "('HOT', 'v1', 'safety', '[{\"code\": \"heat_safety\", \"evidence\": \"burning smell\"}]', 'auto')")
    got = evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 12))
    assert [a.rule for a in got] == ["safety", "low_review"]
    assert got[0].priority == "urgent" and "burning smell" in got[0].detail


def test_rating_change_and_target_crossing(tmp_path):
    con = store.connect(tmp_path / "a.db")
    ok = {5: 64, 4: 0, 3: 15, 2: 21, 1: 0}       # shows 4.1, mean 4.045-4.09
    low = {5: 55, 4: 22, 3: 9, 2: 3, 1: 11}      # shows 4.0, mean 4.04-4.05 (< 4.05)
    snap(con, "A1", 4.1, ok, "2026-09-24T09:00:00")
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10))                      # baseline: above target
    snap(con, "A1", 4.0, low, "2026-09-24T11:00:00")
    got = {a.rule: a for a in evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 12))}
    assert set(got) == {"rating_change", "target"}
    assert got["rating_change"].priority == "high" and "4.1 → 4.0" in got["rating_change"].title
    assert "fell below" in got["target"].title
    assert evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 13)) == []        # no repeat while still below


def test_return_spike_only_for_weeks_after_baseline(tmp_path):
    con = store.connect(tmp_path / "a.db")
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 14, 10))                      # baseline Mon 14 Sep
    rows = [("2026-08-18", 1), ("2026-08-25", 1), ("2026-09-01", 2), ("2026-09-08", 1), ("2026-09-15", 8)]
    appr = pd.DataFrame([{"product": "Gun A", "event_date": d} for d, n in rows for _ in range(n)])
    got = evaluate(con, AMZ, ALR, {"approved": appr}, now=datetime(2026, 9, 22, 9))  # last full week = 14-20 Sep
    assert [a.rule for a in got] == ["return_spike"]
    assert "8 approved" in got[0].detail
