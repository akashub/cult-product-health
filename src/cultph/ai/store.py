"""review_label table in amazon.db. One row per (review, prompt version);
a changed review body is relabelled. Human decisions are never overwritten."""

from __future__ import annotations

from datetime import datetime

LABEL_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_label (
  review_id TEXT, prompt_version TEXT, body_hash TEXT, issues TEXT, codes TEXT, sentiment TEXT, severity TEXT,
  classifier_model TEXT, judge_model TEXT, judge_verdict TEXT, judge_reason TEXT, judge_missing TEXT,
  judge_wrong TEXT, check_errors TEXT, status TEXT, labeled_at TEXT,
  human_verdict TEXT, human_codes TEXT, human_at TEXT,
  PRIMARY KEY (review_id, prompt_version));
"""

COLS = ["review_id", "prompt_version", "body_hash", "issues", "codes", "sentiment", "severity", "classifier_model",
        "judge_model", "judge_verdict", "judge_reason", "judge_missing", "judge_wrong", "check_errors", "status"]


def ensure(con) -> None:
    con.executescript(LABEL_SCHEMA)


def pending_reviews(con, prompt_version: str, limit: int | None = None) -> list[dict]:
    """Reviews with no label for this prompt version, or whose text changed since."""
    from .labels import body_hash

    ensure(con)
    rows = con.execute(
        "SELECT r.review_id, r.rating, r.title, r.body, l.body_hash FROM review r "
        "LEFT JOIN review_label l ON l.review_id = r.review_id AND l.prompt_version = ? "
        "ORDER BY r.first_seen_at DESC", (prompt_version,)).fetchall()
    out = []
    for rid, rating, title, body, old_hash in rows:
        r = {"review_id": rid, "rating": rating, "title": title, "body": body}
        if old_hash is None or old_hash != body_hash(r):
            out.append(r)
    return out[:limit] if limit else out


def save_label(con, lab: dict) -> None:
    ensure(con)
    con.execute(
        f"INSERT INTO review_label ({', '.join(COLS)}, labeled_at) VALUES ({', '.join('?' * len(COLS))}, ?) "
        "ON CONFLICT(review_id, prompt_version) DO UPDATE SET "
        # a changed review text invalidates any earlier human decision (old values are read here)
        "human_verdict = CASE WHEN body_hash = excluded.body_hash THEN human_verdict END, "
        "human_codes = CASE WHEN body_hash = excluded.body_hash THEN human_codes END, "
        + ", ".join(f"{c}=excluded.{c}" for c in COLS[2:]) + ", labeled_at=excluded.labeled_at",
        [lab[c] for c in COLS] + [datetime.now().isoformat(timespec="seconds")])


def set_human(con, review_id: str, prompt_version: str, verdict: str, codes: str | None = None) -> None:
    """verdict: 'correct' (AI label right) or 'fixed' (person supplied codes)."""
    con.execute("UPDATE review_label SET human_verdict=?, human_codes=?, human_at=? "
                "WHERE review_id=? AND prompt_version=?",
                (verdict, codes, datetime.now().isoformat(timespec="seconds"), review_id, prompt_version))
