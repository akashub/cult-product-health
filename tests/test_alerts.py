from datetime import datetime

import pandas as pd

from cultph.ai import store as label_store
from cultph.alerts import evaluate
from cultph.amazon import store

AMZ = {"target_rating": 4.1, "target_mode": "displayed"}
ALR = {"recent_days": 14, "return_spike_x": 2.0, "return_spike_min": 5}
BASE = {"country": "India", "verified": True, "variant": None, "helpful_votes": 0}


def snap(con, asin, avg, hist, at):
    con.execute("INSERT INTO rating_snapshot (asin, parent_asin, product, captured_at, avg_rating, total_ratings, "
                "p5, p4, p3, p2, p1, hist_avg_min, hist_avg_max) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (asin, asin, "Gun A", at, avg, 100, hist[5], hist[4], hist[3], hist[2], hist[1], 0, 0))


def review(con, rid, rating, rdate, seen, asin="A1", source="product_page", platform="amazon", precision="day"):
    store.upsert_reviews(con, asin, "Gun A", [{"review_id": rid, "rating": rating, "title": "t", "body": "b",
                                               "review_date": rdate, "date_precision": precision, **BASE}],
                         source, asin, platform)
    con.execute("UPDATE review SET first_seen_at = ? WHERE review_id = ?", (seen, rid))


def test_first_run_is_baseline_only(tmp_path):
    con = store.connect(tmp_path / "a.db")
    review(con, "OLD", 1, "2026-09-20", "2026-09-24T09:00:00")
    assert evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10)) == []


def test_new_recent_low_review_alerts_once(tmp_path):
    con = store.connect(tmp_path / "a.db")
    review(con, "SEED", 5, "2026-09-01", "2026-09-24T09:00:00")                 # listing known before baseline
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10))                      # baseline 10:00
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10, 30))                  # registers source key
    review(con, "NEW", 1, "2026-09-24", "2026-09-24T11:00:00")                  # posted today, seen after baseline
    review(con, "BACKFILL", 1, "2025-01-10", "2026-09-24T11:00:00")            # seen after baseline but old
    review(con, "GOOD", 5, "2026-09-24", "2026-09-24T11:00:00")
    got = evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 12))
    assert [(a.rule, a.object_key) for a in got] == [("low_review", "NEW")]
    assert evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 13)) == []        # deduplicated


def test_safety_label_is_urgent_and_first(tmp_path):
    con = store.connect(tmp_path / "a.db")
    review(con, "SEED", 5, "2026-09-01", "2026-09-24T09:00:00")
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10))
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10, 30))
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


# ---- delivery channels (network mocked) ----
import json as _json

import cultph.alerts as alerts_mod
from cultph.alerts import CHANNELS, Alert, deliver

A = Alert("low_review", "R1", "high", "1★ review · Gun A", "Not charging")


class _Resp:
    status = 200


def _env(values):
    return lambda name: values.get(name)


def test_channels_skip_when_not_configured(monkeypatch):
    monkeypatch.setattr(alerts_mod, "env", _env({}))
    assert not CHANNELS["telegram"](A) and not CHANNELS["slack"](A) and not CHANNELS["email"](A)


def test_telegram_and_slack_payloads(monkeypatch):
    sent = []
    monkeypatch.setattr(alerts_mod, "env", _env({"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "42",
                                                 "SLACK_WEBHOOK_URL": "https://hooks.example/x"}))
    monkeypatch.setattr(alerts_mod.urllib.request, "urlopen",
                        lambda req, timeout: sent.append((req.full_url, _json.loads(req.data))) or _Resp())
    assert CHANNELS["telegram"](A) and CHANNELS["slack"](A)
    assert sent[0] == ("https://api.telegram.org/botT/sendMessage", {"chat_id": "42", "text": "1★ review · Gun A\nNot charging"})
    assert sent[1][0] == "https://hooks.example/x" and "Not charging" in sent[1][1]["text"]


def test_email(monkeypatch):
    got = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            got["host"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            got["login"] = u

        def send_message(self, m):
            got["subject"], got["to"] = m["Subject"], m["To"]

    monkeypatch.setattr(alerts_mod, "env", _env({"SMTP_HOST": "smtp.example", "SMTP_USER": "me@x", "SMTP_PASS": "p",
                                                 "ALERT_EMAIL_TO": "you@x"}))
    monkeypatch.setattr(alerts_mod.smtplib, "SMTP_SSL", FakeSMTP)
    assert CHANNELS["email"](A)
    assert got == {"host": ("smtp.example", 465), "login": "me@x", "subject": "[Cult alert] 1★ review · Gun A", "to": "you@x"}


def test_failing_channel_does_not_block_others(tmp_path, monkeypatch):
    con = store.connect(tmp_path / "a.db")
    con.executescript(alerts_mod.SCHEMA)
    con.execute("INSERT INTO alert_event VALUES ('low_review','R1','t','high','x','y','')")

    def boom(a):
        raise OSError("down")

    monkeypatch.setitem(CHANNELS, "slack", boom)
    monkeypatch.setitem(CHANNELS, "telegram", lambda a: True)
    deliver(con, [A], ["slack", "telegram"])
    assert con.execute("SELECT channels FROM alert_event").fetchone()[0] == "slack:error:OSError,telegram"


def test_resolve_channels(monkeypatch):
    monkeypatch.setattr(alerts_mod, "env", _env({"SLACK_WEBHOOK_URL": "u"}))
    monkeypatch.setattr(alerts_mod, "desktop_supported", lambda: False)
    assert alerts_mod.resolve_channels("auto") == ["slack"]
    assert alerts_mod.resolve_channels(["email"]) == ["email"]



def test_new_platform_or_first_login_walk_does_not_flood(tmp_path):
    con = store.connect(tmp_path / "a.db")
    review(con, "SEED", 5, "2026-09-01", "2026-09-24T09:00:00")
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10))                       # global baseline
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10, 30))
    # Flipkart added later: 30 recent low reviews appear at once
    for i in range(30):
        review(con, f"FK{i}", 1, "2026-09-23", "2026-09-24T11:00:00", asin="P1", source="flipkart:recent:p1",
               platform="flipkart")
    # first Amazon listing walk after login: old + recent reviews under a new source kind
    for i in range(30):
        review(con, f"L{i}", 2, "2026-09-22", "2026-09-24T11:00:00", source="listing:recent:all")
    assert evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 12)) == []          # all treated as history
    # afterwards, genuinely new reviews on those sources do alert
    review(con, "FKNEW", 1, "2026-09-24", "2026-09-24T13:00:00", asin="P1", source="flipkart:recent:p1",
           platform="flipkart")
    review(con, "FKOLDISH", 1, "2026-09-24", "2026-09-24T13:00:00", asin="P1", source="flipkart:recent:p1",
           platform="flipkart", precision="month")                               # imprecise date: no alert
    got = evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 14))
    assert [a.object_key for a in got] == ["FKNEW"]


def test_first_labelling_run_does_not_flood_safety(tmp_path):
    con = store.connect(tmp_path / "a.db")
    for i in range(5):
        review(con, f"OLD{i}", 2, "2026-09-20", "2026-09-24T09:00:00")
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10))
    evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 10, 30))
    label_store.ensure(con)
    for i in range(5):  # key added later; old reviews labelled 'safety' in one go
        con.execute("INSERT INTO review_label (review_id, prompt_version, severity, issues, status) VALUES "
                    f"('OLD{i}', 'v1', 'safety', '[]', 'auto')")
    assert evaluate(con, AMZ, ALR, now=datetime(2026, 9, 24, 12)) == []


def test_flipkart_target_uses_exact_mean(tmp_path):
    from cultph.alerts import _below_target
    snaps = pd.DataFrame([{"asin": "P1", "platform": "flipkart", "p5": 55, "p4": 22, "p3": 9, "p2": 3, "p1": 11,
                           "avg_rating": 4.1, "hist_avg_min": 4.049, "hist_avg_max": 4.049, "c5": 1, "captured_at": "t"}])
    assert _below_target(snaps, 4.05) == {"P1": True}
