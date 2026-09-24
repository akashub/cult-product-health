"""SQLite storage with a publish gate: a run is written to a staging file and
only swapped in as the live database when the judge doesn't fail it."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from .config import DATA_DIR

LIVE_DB = DATA_DIR / "cultph.db"
STAGING_DB = DATA_DIR / "cultph.staging.db"
RUN_LOG = DATA_DIR / "runs.jsonl"


def write_snapshot(path: Path, tables: dict[str, pd.DataFrame], rejects: list[dict], checks: list[dict], meta: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as con:
        for name, df in tables.items():
            df.to_sql(name, con, index=False)
        pd.DataFrame(rejects, columns=["role", "src_tab", "src_row", "reason"]).to_sql("rejects", con, index=False)
        pd.DataFrame(checks).to_sql("checks", con, index=False)
        pd.DataFrame([{"key": k, "value": json.dumps(v, default=str)} for k, v in meta.items()]).to_sql("meta", con, index=False)


def publish(tables, rejects, checks, meta, status: str) -> bool:
    write_snapshot(STAGING_DB, tables, rejects, checks, meta)
    published = status != "fail"
    if published:
        os.replace(STAGING_DB, LIVE_DB)
    with RUN_LOG.open("a") as f:
        f.write(json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "status": status,
                            "published": published, "checks": checks, **meta}, default=str) + "\n")
    return published


def read_table(name: str, db: Path = LIVE_DB) -> pd.DataFrame:
    with sqlite3.connect(db) as con:
        return pd.read_sql(f'SELECT * FROM "{name}"', con)


def last_runs(n: int = 20) -> list[dict]:
    if not RUN_LOG.exists():
        return []
    return [json.loads(line) for line in RUN_LOG.read_text().splitlines()[-n:]][::-1]
