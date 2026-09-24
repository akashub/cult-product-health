"""Append-only Amazon history in data/amazon.db (separate from the sheet DB,
which is rebuilt on every sync). Reviews are keyed on Amazon's review id."""

from __future__ import annotations

import sqlite3
from datetime import datetime

from ..config import DATA_DIR
from .rating import avg_range

AMAZON_DB = DATA_DIR / "amazon.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rating_snapshot (
  asin TEXT, parent_asin TEXT, product TEXT, captured_at TEXT, avg_rating REAL, total_ratings INTEGER,
  p5 INTEGER, p4 INTEGER, p3 INTEGER, p2 INTEGER, p1 INTEGER, hist_avg_min REAL, hist_avg_max REAL);
CREATE TABLE IF NOT EXISTS review (
  review_id TEXT PRIMARY KEY, asin TEXT, product TEXT, rating INTEGER, title TEXT, body TEXT,
  review_date TEXT, country TEXT, verified INTEGER, variant TEXT, helpful_votes INTEGER,
  first_seen_at TEXT, last_seen_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS scrape_run (
  at TEXT, asin TEXT, url TEXT, status TEXT, detail TEXT);
"""


def connect(path=AMAZON_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_snapshot(con, asin: str, product: str, parsed: dict) -> None:
    h = parsed["hist_pct"]
    a_min, a_max = avg_range(h)
    con.execute("INSERT INTO rating_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (asin, parsed.get("parent_asin"), product, now(), parsed["avg_rating"], parsed["total_ratings"],
                 h.get(5), h.get(4), h.get(3), h.get(2), h.get(1), round(a_min, 4), round(a_max, 4)))


def upsert_reviews(con, asin: str, product: str, reviews: list[dict], source: str) -> list[str]:
    """Inserts unseen reviews, refreshes last_seen/helpful on known ones. Returns new ids."""
    ts, new = now(), []
    known = {r[0] for r in con.execute(
        f"SELECT review_id FROM review WHERE review_id IN ({','.join('?' * len(reviews))})",
        [r["review_id"] for r in reviews])} if reviews else set()
    for r in reviews:
        if r["review_id"] in known:
            con.execute("UPDATE review SET last_seen_at=?, helpful_votes=MAX(helpful_votes, ?) WHERE review_id=?",
                        (ts, r["helpful_votes"], r["review_id"]))
            # a truncated body from the product page never overwrites a full one
            if r.get("body"):
                con.execute("UPDATE review SET body=? WHERE review_id=? AND (body IS NULL OR length(body) < ?)",
                            (r["body"], r["review_id"], len(r["body"])))
        else:
            con.execute("INSERT INTO review VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (r["review_id"], asin, product, r["rating"], r["title"], r["body"], r["review_date"],
                         r["country"], int(r["verified"]), r["variant"], r["helpful_votes"], ts, ts, source))
            new.append(r["review_id"])
    return new


def log_run(con, asin: str, url: str, status: str, detail: str) -> None:
    con.execute("INSERT INTO scrape_run VALUES (?,?,?,?,?)", (now(), asin, url, status, detail))
