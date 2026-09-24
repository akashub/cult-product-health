"""Readers that turn a workbook (local .xlsx or a Google Sheet) into the same
shape: {tab_name: (headers, rows)} with raw cell values. Everything downstream
is source-agnostic."""

from __future__ import annotations

import warnings
from pathlib import Path

from .config import DATA_DIR

Table = tuple[list[str], list[list]]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]


class XlsxSource:
    def __init__(self, path: str | Path):
        import openpyxl

        self.path = Path(path)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # pivot-cache warnings are noise
            self._wb = openpyxl.load_workbook(self.path, read_only=True, data_only=True)

    def table(self, tab: str) -> Table:
        rows = [list(r) for r in self._wb[tab].iter_rows(values_only=True)]
        if not rows:
            return [], []
        headers = [str(h).strip() if h is not None else "" for h in rows[0]]
        return headers, rows[1:]

    def grid(self, tab: str) -> list[list]:
        return [list(r) for r in self._wb[tab].iter_rows(values_only=True)]


class GoogleAuthError(RuntimeError):
    pass


def google_client(auth: str = "oauth"):
    """auth='oauth': sign in with Gmail (desktop flow). Client JSON at
    data/google_client.json, token cached in data/google_token.json. Apps left in
    Google's 'Testing' mode get tokens that expire after 7 days; set the app to
    'In production' (unverified is fine for personal use) to avoid weekly re-login.
    auth='service_account': key at data/google_service_account.json; share the
    sheets with that account's email. Never expires; best for scheduled runs."""
    import gspread

    if auth == "service_account":
        key = DATA_DIR / "google_service_account.json"
        if not key.exists():
            raise GoogleAuthError(f"missing {key}; see SETUP.md (Google service account)")
        return gspread.service_account(filename=str(key), scopes=SCOPES)
    client_json = DATA_DIR / "google_client.json"
    if not client_json.exists():
        raise GoogleAuthError(f"missing {client_json}; see SETUP.md (Google OAuth client)")
    try:
        return gspread.oauth(scopes=SCOPES, credentials_filename=str(client_json),
                             authorized_user_filename=str(DATA_DIR / "google_token.json"))
    except Exception as e:  # noqa: BLE001 - google.auth RefreshError and friends
        if "invalid_grant" in str(e) or "expired" in str(e).lower() or type(e).__name__ == "RefreshError":
            (DATA_DIR / "google_token.json").unlink(missing_ok=True)
            raise GoogleAuthError("Google login expired or was revoked. Run `uv run cultph sheets` to sign in again. "
                                  "(Apps in Google 'Testing' mode expire every 7 days; see SETUP.md.)") from e
        raise


class GSheetSource:
    """Reads a Google Sheet the user can access (OAuth or service account)."""

    def __init__(self, spreadsheet_id: str, auth: str = "oauth"):
        self._sh = google_client(auth).open_by_key(spreadsheet_id)

    def table(self, tab: str) -> Table:
        values = self._sh.worksheet(tab).get_all_values(value_render_option="UNFORMATTED_VALUE",
                                                        date_time_render_option="SERIAL_NUMBER")
        if not values:
            return [], []
        headers = [str(h).strip() for h in values[0]]
        return headers, [list(r) for r in values[1:]]

    def grid(self, tab: str) -> list[list]:
        return self._sh.worksheet(tab).get_all_values(value_render_option="UNFORMATTED_VALUE")


def list_shared_sheets(auth: str = "oauth") -> list[dict]:
    """Spreadsheets visible to the signed-in account (owned or shared)."""
    return google_client(auth).list_spreadsheet_files()


def open_source(source_cfg: dict):
    kind = source_cfg.get("type", "xlsx")
    if kind == "xlsx":
        from .config import ROOT

        p = Path(source_cfg["path"])
        return XlsxSource(p if p.is_absolute() else ROOT / p)
    if kind == "gsheet":
        return GSheetSource(source_cfg["spreadsheet_id"], source_cfg.get("auth", "oauth"))
    raise ValueError(f"unknown source type {kind!r}")
