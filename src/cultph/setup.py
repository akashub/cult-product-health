"""`cultph setup` writes secrets to data/.env; `cultph doctor` shows what is
ready and the exact next step for anything that isn't."""

from __future__ import annotations

import getpass
import os
import platform
from pathlib import Path

from .config import DATA_DIR, ROOT, env, load_config

ENV_FILE = DATA_DIR / ".env"

SECRETS = [
    ("ANTHROPIC_API_KEY", "Anthropic API key (AI review labels, if ai.provider is anthropic)", True),
    ("OPENAI_API_KEY", "OpenAI API key (AI review labels, if ai.provider is openai)", True),
    ("TELEGRAM_BOT_TOKEN", "Telegram bot token (alerts)", True),
    ("TELEGRAM_CHAT_ID", "Telegram chat id", False),
    ("SLACK_WEBHOOK_URL", "Slack incoming webhook URL (alerts)", True),
    ("SMTP_HOST", "SMTP host for email alerts (e.g. smtp.gmail.com)", False),
    ("SMTP_PORT", "SMTP SSL port (default 465)", False),
    ("SMTP_USER", "SMTP username / from address", False),
    ("SMTP_PASS", "SMTP password (Gmail: an app password)", True),
    ("ALERT_EMAIL_TO", "Send email alerts to", False),
    ("DASHBOARD_PASSWORD", "Dashboard password (optional, for phone/remote access)", True),
]


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or ENV_FILE
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def write_env_file(values: dict[str, str], path: Path | None = None) -> None:
    path = path or ENV_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{k}={v}\n" for k, v in values.items() if v))
    os.chmod(path, 0o600)


def run_setup(ask=None) -> None:
    """Prompts for each secret. Enter keeps the current value; '-' clears it."""
    current = read_env_file()
    print(f"Secrets are stored in {ENV_FILE} (gitignored, readable only by you).")
    print("Press Enter to keep the current value, or type '-' to clear it.\n")
    for key, label, secret in SECRETS:
        shown = "(set)" if current.get(key) else "(not set)"
        prompt = f"{label} {shown}: "
        val = (ask or (getpass.getpass if secret else input))(prompt).strip()
        if val == "-":
            current.pop(key, None)
        elif val:
            current[key] = val
    write_env_file(current)
    print(f"\nSaved. Next: `uv run cultph doctor`")


# ---------------- doctor ----------------

OK, MISSING, OPTIONAL = "✅", "❌", "➖"


def _chromium_ok() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001
        return False


def _amazon_cookie_ok() -> bool:
    from .amazon.fetch import PROFILE_DIR, browser, has_auth_cookie

    if not PROFILE_DIR.exists():
        return False
    try:
        with browser(headless=True, delay=(0, 0)) as f:
            return has_auth_cookie(f.page.context.cookies())
    except Exception:  # noqa: BLE001
        return False


def checks(live: bool = False) -> list[tuple[str, str, str, str]]:
    """Returns (status, item, detail, next_step) rows."""
    rows = []
    cfg = load_config()
    private = cfg.path.name == "config.private.yaml"
    rows.append((OK if private else MISSING, "Private config", cfg.path.name,
                 "" if private else "copy config.private.yaml from the handover bundle into the project folder"))

    src = cfg.source
    if src.get("type") == "gsheet":
        auth = src.get("auth", "oauth")
        key_file = DATA_DIR / ("google_service_account.json" if auth == "service_account" else "google_client.json")
        rows.append((OK if key_file.exists() else MISSING, f"Google {auth} credentials", key_file.name,
                     "" if key_file.exists() else "see SETUP.md → Google Sheets"))
        if auth == "oauth":
            tok = (DATA_DIR / "google_token.json").exists()
            rows.append((OK if tok else MISSING, "Google sign-in", "token cached" if tok else "not signed in",
                         "" if tok else "uv run cultph sheets"))
        sid = bool(src.get("spreadsheet_id"))
        rows.append((OK if sid else MISSING, "Spreadsheet chosen", src.get("spreadsheet_id", "-"),
                     "" if sid else "uv run cultph sheets, then set source.spreadsheet_id"))
    else:
        p = Path(src.get("path", ""))
        p = p if p.is_absolute() else ROOT / p
        rows.append((OK if p.is_file() else MISSING, "Workbook (xlsx source)", p.name,
                     "" if p.is_file() else "put the workbook at that path, or switch source.type to gsheet"))

    sales = cfg.tabs.get("sales")
    rows.append((OK if sales else OPTIONAL, "Sales data (return % / selling %)", sales["name"] if sales else "not configured",
                 "" if sales else "add a tabs.sales section when a units-sold sheet exists (see config.example.yaml)"))

    amz = cfg.raw.get("amazon", {})
    rows.append((OK if amz.get("asins") else MISSING, "Amazon ASIN list", f"{len(amz.get('asins', {}))} listings",
                 "" if amz.get("asins") else 'uv run cultph amazon-discover "cult massager"'))
    chromium = _chromium_ok()
    rows.append((OK if chromium else MISSING, "Browser for scraping", "chromium" if chromium else "not installed",
                 "" if chromium else "uv run playwright install chromium"))
    signed = _amazon_cookie_ok() if chromium else False
    rows.append((OK if signed else MISSING, "Amazon sign-in (full review list)", "session saved" if signed else "not signed in",
                 "" if signed else "uv run cultph amazon-login"))

    ai = cfg.raw.get("ai", {})
    provider = ai.get("provider", "anthropic")
    key_name = "OPENAI_API_KEY" if provider == "openai" else "ANTHROPIC_API_KEY"
    key = env(key_name)
    rows.append((OK if key else MISSING, f"AI key ({provider})", f"{key_name} {'set' if key else 'not set'}",
                 "" if key else "uv run cultph setup"))

    from .alerts import configured_channels

    chans = configured_channels()
    rows.append((OK if chans else MISSING, "Alert channels", ", ".join(chans) or "none",
                 "" if chans else "uv run cultph setup (Telegram, Slack or email)"))
    pw = env("DASHBOARD_PASSWORD")
    rows.append((OK if pw else OPTIONAL, "Dashboard password", "set" if pw else "not set (fine on this computer only)",
                 "" if pw else "uv run cultph setup, if the dashboard will be reachable from other devices"))

    plist = Path.home() / "Library/LaunchAgents/com.cultph.run.plist"
    if platform.system() == "Darwin":
        rows.append((OK if plist.exists() else OPTIONAL, "Scheduled runs", "installed" if plist.exists() else "not installed",
                     "" if plist.exists() else "uv run cultph schedule --every 60   (or keep `uv run cultph watch` running)"))
    else:
        rows.append((OPTIONAL, "Scheduled runs", platform.system(), "keep `uv run cultph watch --every 60` running"))

    if live:
        rows += live_checks(cfg, key, signed, chromium)
    return rows


def live_checks(cfg, key, signed, chromium) -> list[tuple[str, str, str, str]]:
    rows = []
    amz = cfg.raw.get("amazon", {})
    if chromium and amz.get("asins"):
        from .amazon.fetch import check_session

        state = check_session(amz.get("domain", "amazon.in"), next(iter(amz["asins"])))
        rows.append((OK if state == "ok" else MISSING, "LIVE Amazon review listing", state,
                     "" if state == "ok" else ("uv run cultph amazon-login" if state == "signin" else "wait an hour and retry")))
    if key:
        try:
            from .ai.labels import Labeler

            ai = cfg.raw.get("ai", {})
            lab = Labeler(ai.get("taxonomy", []), ai.get("classifier_model", "claude-haiku-4-5-20251001"),
                          ai.get("judge_model", "claude-sonnet-5"), provider=ai.get("provider", "anthropic")).label(
                {"review_id": "doctor", "rating": 1, "title": "Stopped working",
                 "body": "The massager stopped charging after one week and makes a loud rattling noise."})
            rows.append((OK, "LIVE AI labelling", f"{lab['status']}: {lab['codes'] or '-'} (judge {lab['judge_verdict']})", ""))
        except Exception as e:  # noqa: BLE001
            rows.append((MISSING, "LIVE AI labelling", f"{type(e).__name__}: {str(e)[:120]}", "check the key / model names"))
    if cfg.source.get("type") == "gsheet":
        try:
            from .sources import open_source

            open_source(cfg.source)
            rows.append((OK, "LIVE Google Sheet", "opened", ""))
        except Exception as e:  # noqa: BLE001
            rows.append((MISSING, "LIVE Google Sheet", str(e)[:160], "see message"))
    return rows


def print_doctor(live: bool = False) -> int:
    rows = checks(live)
    for status, item, detail, nxt in rows:
        print(f" {status} {item:<36} {detail}")
        if nxt:
            print(f"      → {nxt}")
    missing = sum(1 for r in rows if r[0] == MISSING)
    print(f"\n{'All set.' if not missing else f'{missing} item(s) need attention.'}"
          + ("" if live else "  Run `uv run cultph doctor --live` to test the connections for real."))
    return 1 if missing else 0
