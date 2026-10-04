"""One sync path for every source (CLI, dashboard upload, Google Sheet import):
read -> parse -> judge -> publish only if nothing failed."""

from __future__ import annotations

import json
import re

from .config import DATA_DIR, Config
from .db import publish
from .ingest import ingest
from .judge import run_checks, verdict

SOURCE_OVERRIDE = DATA_DIR / "source.json"   # set from the dashboard; used by scheduled syncs


def sheet_id_from_url(text: str) -> str | None:
    """Accepts a Google Sheets URL or a bare spreadsheet id."""
    text = (text or "").strip()
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", text)
    if m:
        return m.group(1)
    return text if re.fullmatch(r"[A-Za-z0-9_-]{20,}", text) else None


def sync_from(source, cfg: Config, label: str) -> dict:
    res = ingest(source, cfg)
    checks = run_checks(res, cfg, source)
    status = verdict(checks)
    meta = {"config": cfg.path.name, "source": label, "source_rows": res.source_rows, "tabs": res.tab_map}
    published = publish(res.tables, res.rejects, checks, meta, status)
    return {"status": status, "published": published, "checks": checks, "tabs": res.tab_map,
            "rows": res.source_rows}


def save_default_source(source_cfg: dict) -> None:
    SOURCE_OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_OVERRIDE.write_text(json.dumps(source_cfg, indent=2))


def import_upload(data: bytes, filename: str, cfg: Config) -> dict:
    """Runs an uploaded workbook through the normal sync. The file only exists in a temp
    file for the duration of the import and is always deleted (it contains customer PII)."""
    import os
    import tempfile

    from .sources import XlsxSource

    fd, tmp = tempfile.mkstemp(suffix=".xlsx")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return sync_from(XlsxSource(tmp), cfg, f"upload:{filename}")
    finally:
        os.remove(tmp)


def explain_google_error(e: Exception) -> str:
    msg = str(e)
    low = msg.lower()
    if "admin_policy_enforced" in low or "access_denied" in low or "unverified" in low:
        return ("Google blocked the sign-in. Cult's Workspace admin probably doesn't allow this app to read company "
                "Sheets. Use “Upload a workbook” instead (File → Download → Microsoft Excel in Google Sheets).")
    if "403" in msg or "permission" in low or "does not have permission" in low:
        return ("This Google account can't open that sheet. Sign in with the account the sheet is shared with, "
                "or use “Upload a workbook”.")
    if "404" in msg or "not found" in low:
        return "No sheet found at that link. Check the URL."
    return f"Google Sheets error: {msg[:300]}"
