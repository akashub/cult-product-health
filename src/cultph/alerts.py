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
        if getattr(r, "platform", "amazon") == "flipkart" and pd.notna(getattr(r, "c5", None)):
            hi = r.hist_avg_min  # exact mean from exact star counts
        else:
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

    if not _state(con, "cleanup_nan_rating_v1"):
        # remove false 'x → nan' rating alerts raised before snapshots always had a displayed rating
        con.execute("DELETE FROM alert_event WHERE rule = 'rating_change' AND title LIKE '%nan%'")
        _set_state(con, "cleanup_nan_rating_v1", True)

    baseline = _state(con, "baseline_at")
    if baseline is None:  # first run: remember where we are, send nothing
        _set_state(con, "baseline_at", now.isoformat(timespec="seconds"))
        _set_state(con, "below_target", below)
        return []

    since = (now.date() - timedelta(days=recent_days)).isoformat()
    alerts: list[Alert] = []
    stamp = now.isoformat(timespec="seconds")

    # Per-source baselines: the first time a listing/source appears (a new platform, the
    # first review walk after an Amazon login, ...) its reviews are history, not news.
    key_base = _state(con, "review_baselines", {})

    def key_of(platform, pool, source) -> str:
        return f"{platform or 'amazon'}:{pool}:{(source or '').split(':')[0]}"

    for plat, pool, src in con.execute(
            "SELECT DISTINCT platform, COALESCE(parent_asin, asin), source FROM review").fetchall():
        key_base.setdefault(key_of(plat, pool, src), stamp)  # unseen source: everything so far is history

    def is_new(platform, pool, source, first_seen, precision) -> bool:
        key = key_of(platform, pool, source)
        # only day-precise dates can prove a review is recent
        return first_seen > key_base[key] and first_seen > baseline and (precision or "day") == "day"

    review_cols = ("SELECT r.review_id, r.asin, COALESCE(r.product, 'pool ' || r.parent_asin), r.rating, r.title, "
                   "r.body, r.review_date, r.platform, COALESCE(r.parent_asin, r.asin), r.source, r.first_seen_at, "
                   "r.date_precision")

    # new 1-2 star reviews
    for rid, asin, prod, rating, title, body, rdate, plat, pool, src, seen, prec in con.execute(
            review_cols + " FROM review r WHERE r.rating <= 2 AND r.review_date >= ?", (since,)).fetchall():
        if is_new(plat, pool, src, seen, prec):
            alerts.append(Alert("low_review", rid, "high", f"{rating}★ review · {prod} ({plat or 'amazon'})",
                                f"{title or ''} — {(body or '')[:280]} ({rdate}, {asin})"))

    # safety issues found by the labeller
    if has_labels:
        for rid, asin, prod, rating, title, body, rdate, plat, pool, src, seen, prec, issues in con.execute(
                review_cols + ", l.issues FROM review_label l JOIN review r USING (review_id) "
                "WHERE l.severity = 'safety' AND r.review_date >= ?", (since,)).fetchall():
            if is_new(plat, pool, src, seen, prec):
                quotes = "; ".join(f"“{i['evidence']}”" for i in json.loads(issues or "[]"))
                alerts.append(Alert("safety", rid, "urgent", f"SAFETY · {prod} ({rating}★, {plat or 'amazon'})",
                                    quotes or (body or "")[:280]))
    _set_state(con, "review_baselines", key_base)

    # displayed rating moved between the last two snapshots
    if not snaps.empty:
        for asin, g in snaps[snaps["captured_at"] > baseline].groupby("asin"):
            full = snaps[snaps["asin"] == asin]
            if len(full) < 2:
                continue
            prev, last = full.iloc[-2], full.iloc[-1]
            if pd.isna(prev["avg_rating"]) or pd.isna(last["avg_rating"]):
                continue  # a snapshot without a displayed rating can't show a change
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


