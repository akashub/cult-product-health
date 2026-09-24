"""Alert rules and delivery.

The first run only records a baseline and sends nothing, because the first
Amazon poll (or a backfill, or the first run after logging in) inserts old
reviews that are not news. After that, a review alerts only if it was first
seen after the baseline AND was posted recently. Every alert has a unique
(rule, object) key, so nothing is sent twice."""

from __future__ import annotations

import json
import platform
import shutil
import smtplib
import sqlite3
import subprocess
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import EmailMessage

import pandas as pd

from .amazon.rating import effective_target, mean_range
from .config import env

SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_state (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS alert_event (
  rule TEXT, object_key TEXT, created_at TEXT, priority TEXT, title TEXT, detail TEXT, channels TEXT,
  UNIQUE (rule, object_key));
"""

PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2}


@dataclass
class Alert:
    rule: str
    object_key: str
    priority: str
    title: str
    detail: str


def _state(con, key, default=None):
    row = con.execute("SELECT value FROM alert_state WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def _set_state(con, key, value):
    con.execute("INSERT INTO alert_state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)))


def _latest_snapshots(con) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM rating_snapshot ORDER BY captured_at", con)


def _below_target(snaps: pd.DataFrame, target: float) -> dict[str, bool]:
    out = {}
    for r in snaps.groupby("asin").tail(1).itertuples():
        _, hi, _ = mean_range({5: r.p5, 4: r.p4, 3: r.p3, 2: r.p2, 1: r.p1}, r.avg_rating)
        out[r.asin] = hi < target
    return out


def evaluate(con, amazon_cfg: dict, alert_cfg: dict, sheet_tables: dict[str, pd.DataFrame] | None = None,
             now: datetime | None = None) -> list[Alert]:
    con.executescript(SCHEMA)
    now = now or datetime.now()
    recent_days = int(alert_cfg.get("recent_days", 14))
    target = effective_target(float(amazon_cfg.get("target_rating", 4.1)), amazon_cfg.get("target_mode", "displayed"))
    has_labels = con.execute("SELECT count(*) FROM sqlite_master WHERE name='review_label'").fetchone()[0]
    snaps = _latest_snapshots(con) if con.execute(
        "SELECT count(*) FROM sqlite_master WHERE name='rating_snapshot'").fetchone()[0] else pd.DataFrame()
    below = _below_target(snaps, target) if not snaps.empty else {}

    baseline = _state(con, "baseline_at")
    if baseline is None:  # first run: remember where we are, send nothing
        _set_state(con, "baseline_at", now.isoformat(timespec="seconds"))
        _set_state(con, "below_target", below)
        return []

    since = (now.date() - timedelta(days=recent_days)).isoformat()
    alerts: list[Alert] = []

    # new 1-2 star reviews
    for rid, asin, prod, rating, title, body, rdate in con.execute(
            "SELECT review_id, asin, COALESCE(product, 'pool ' || parent_asin), rating, title, body, review_date "
            "FROM review WHERE rating <= 2 AND first_seen_at > ? AND review_date >= ?", (baseline, since)):
        alerts.append(Alert("low_review", rid, "high", f"{rating}★ review · {prod}",
                            f"{title or ''} — {(body or '')[:280]} ({rdate}, {asin})"))

    # safety issues found by the labeller
    if has_labels:
        for rid, prod, rating, body, issues in con.execute(
                "SELECT r.review_id, COALESCE(r.product, 'pool ' || r.parent_asin), r.rating, r.body, l.issues "
                "FROM review_label l JOIN review r USING (review_id) "
                "WHERE l.severity = 'safety' AND r.first_seen_at > ? AND r.review_date >= ?", (baseline, since)):
            quotes = "; ".join(f"“{i['evidence']}”" for i in json.loads(issues or "[]"))
            alerts.append(Alert("safety", rid, "urgent", f"SAFETY · {prod} ({rating}★)", quotes or (body or "")[:280]))

    # displayed rating moved between the last two snapshots
    if not snaps.empty:
        for asin, g in snaps[snaps["captured_at"] > baseline].groupby("asin"):
            full = snaps[snaps["asin"] == asin]
            if len(full) < 2:
                continue
            prev, last = full.iloc[-2], full.iloc[-1]
            if last["captured_at"] in set(g["captured_at"]) and prev["avg_rating"] != last["avg_rating"]:
                drop = last["avg_rating"] < prev["avg_rating"]
                alerts.append(Alert("rating_change", f"{asin}:{last['captured_at']}", "high" if drop else "normal",
                                    f"{last['product']}: {prev['avg_rating']} → {last['avg_rating']}★",
                                    f"{asin} · {last['total_ratings']} ratings"))

    # crossed below / back above the target (state transitions only)
    prev_below = _state(con, "below_target", {})
    for asin, is_below in below.items():
        was = prev_below.get(asin)
        if was is not None and was != is_below:
            prod = snaps[snaps["asin"] == asin].iloc[-1]["product"]
            alerts.append(Alert("target", f"{asin}:{now.date()}:{'below' if is_below else 'above'}",
                                "high" if is_below else "normal",
                                f"{prod} {'fell below' if is_below else 'is back above'} {target} (weighted mean)", asin))
    _set_state(con, "below_target", {**prev_below, **below})

    # weekly return spike per product vs trailing 4 weeks (only weeks ending after the baseline)
    appr = (sheet_tables or {}).get("approved")
    if appr is not None and not appr.empty:
        df = appr.dropna(subset=["product"]).copy()
        df["week"] = pd.to_datetime(df["event_date"], format="ISO8601").dt.to_period("W-SUN")
        last_full = pd.Period(now.date(), "W-SUN") - 1
        if last_full.end_time.date() >= datetime.fromisoformat(baseline).date():
            weekly = df.groupby(["product", "week"]).size()
            for prod in df["product"].unique():
                w = weekly.get(prod, pd.Series(dtype=int))
                cur = int(w.get(last_full, 0))
                trail = [int(w.get(last_full - i, 0)) for i in range(1, 5)]
                mean = sum(trail) / 4
                if cur >= max(int(alert_cfg.get("return_spike_min", 5)), float(alert_cfg.get("return_spike_x", 2.0)) * mean):
                    alerts.append(Alert("return_spike", f"{prod}:{last_full}", "high",
                                        f"Return spike · {prod}", f"{cur} approved returns/exchanges in week {last_full} "
                                                                  f"vs {mean:.1f}/week over the previous 4 weeks"))

    # keep only alerts never raised before
    fresh = []
    for a in alerts:
        cur = con.execute("INSERT OR IGNORE INTO alert_event (rule, object_key, created_at, priority, title, detail, "
                          "channels) VALUES (?,?,?,?,?,?,?)",
                          (a.rule, a.object_key, now.isoformat(timespec="seconds"), a.priority, a.title, a.detail, ""))
        if cur.rowcount:
            fresh.append(a)
    return sorted(fresh, key=lambda a: PRIORITY_ORDER.get(a.priority, 9))


# ---------------- delivery ----------------

def _desktop(a: Alert) -> bool:
    """Native notification on macOS (osascript) or Linux (notify-send)."""
    system = platform.system()
    if system == "Darwin":
        esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
        script = f'display notification "{esc(a.detail[:200])}" with title "Cult · {esc(a.title[:80])}"'
        return subprocess.run(["osascript", "-e", script], capture_output=True).returncode == 0
    if system == "Linux" and shutil.which("notify-send"):
        return subprocess.run(["notify-send", f"Cult · {a.title[:80]}", a.detail[:200]], capture_output=True).returncode == 0
    return False


def desktop_supported() -> bool:
    return platform.system() == "Darwin" or (platform.system() == "Linux" and bool(shutil.which("notify-send")))


def _telegram(a: Alert) -> bool:
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    data = json.dumps({"chat_id": chat, "text": f"{a.title}\n{a.detail}"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data,
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=15).status == 200


def _slack(a: Alert) -> bool:
    hook = env("SLACK_WEBHOOK_URL")
    if not hook:
        return False
    req = urllib.request.Request(hook, data=json.dumps({"text": f"*{a.title}*\n{a.detail}"}).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=15).status == 200


def _email(a: Alert) -> bool:
    host, user, pw, to = env("SMTP_HOST"), env("SMTP_USER"), env("SMTP_PASS"), env("ALERT_EMAIL_TO")
    if not (host and user and pw and to):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = f"[Cult alert] {a.title}", user, to
    msg.set_content(a.detail)
    with smtplib.SMTP_SSL(host, int(env("SMTP_PORT") or 465), timeout=20) as s:
        s.login(user, pw)
        s.send_message(msg)
    return True


CHANNELS = {"desktop": _desktop, "macos": _desktop, "telegram": _telegram, "slack": _slack, "email": _email}


def configured_channels() -> list[str]:
    """Channels that can actually send right now, given data/.env."""
    out = ["desktop"] if desktop_supported() else []
    if env("TELEGRAM_BOT_TOKEN") and env("TELEGRAM_CHAT_ID"):
        out.append("telegram")
    if env("SLACK_WEBHOOK_URL"):
        out.append("slack")
    if all(env(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL_TO")):
        out.append("email")
    return out


def resolve_channels(setting) -> list[str]:
    """'auto' (default) = every configured channel; or an explicit list."""
    if setting in (None, "auto", ["auto"]):
        return configured_channels()
    return list(setting)


def deliver(con, alerts: list[Alert], channels: list[str]) -> None:
    """Sends each alert on every configured channel; records where it went."""
    for a in alerts:
        sent = []
        for name in channels:
            try:
                if CHANNELS[name](a):
                    sent.append(name)
            except Exception as e:  # noqa: BLE001 - a failing channel must not block the others
                sent.append(f"{name}:error:{type(e).__name__}")
        con.execute("UPDATE alert_event SET channels = ? WHERE rule = ? AND object_key = ?",
                    (",".join(sent), a.rule, a.object_key))


def load_sheet_tables(db_path) -> dict[str, pd.DataFrame]:
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as c:
        return {"approved": pd.read_sql("SELECT * FROM approved", c)}


