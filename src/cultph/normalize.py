"""Value cleaning shared by every tab. Pure functions, no I/O."""

from __future__ import annotations

import math
import re
from datetime import date, datetime

import pandas as pd

UNKNOWN = "Unknown"

_AMAZON_ORDER = re.compile(r"^\d{3}-\d{7}-\d{7}$")
_FLIPKART_ORDER = re.compile(r"^OD\d+$")
_STORE_ORDER = re.compile(r"^\d{5,8}$")
_MONTH_LABEL = re.compile(r"^\s*(\d{1,2})\s*~")


def is_blank(v) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    if v is pd.NaT:
        return True
    return isinstance(v, str) and v.strip() == ""


def clean_id(v) -> str | None:
    """IDs must be strings: Excel hands back 200001.0 for 200001, and a stray
    backtick prefix appears on some exported item codes."""
    if is_blank(v):
        return None
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    s = str(v).strip().lstrip("`'").strip()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    return s or None


def split_ids(v) -> list[str]:
    """A single cell sometimes holds several AWBs separated by whitespace/commas."""
    s = clean_id(v)
    if s is None:
        return []
    return [p for p in re.split(r"[\s,;/]+", s) if p]


def clean_text(v) -> str | None:
    if is_blank(v):
        return None
    return re.sub(r"\s+", " ", str(v)).strip() or None


def alias_key(v) -> str:
    """Key used to look up model-name aliases: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", str(v).lower().replace("+", "plus"))


def parse_dt(v, dayfirst: bool = True) -> datetime | None:
    if is_blank(v):
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float)):
        # Google Sheets serial date (days since 1899-12-30)
        return datetime(1899, 12, 30) + pd.to_timedelta(float(v), unit="D")
    ts = pd.to_datetime(str(v).strip(), dayfirst=dayfirst, errors="coerce")
    return None if pd.isna(ts) else ts.to_pydatetime()


def month_label_number(v) -> int | None:
    """'1~January' -> 1. Returns None when the label is missing or unparseable."""
    if is_blank(v):
        return None
    if isinstance(v, (datetime, date)):
        return v.month
    m = _MONTH_LABEL.match(str(v))
    return int(m.group(1)) if m else None


def platform_from_order_id(order_id: str | None) -> str | None:
    if not order_id:
        return None
    if _AMAZON_ORDER.match(order_id):
        return "Amazon"
    if _FLIPKART_ORDER.match(order_id):
        return "Flipkart"
    if _STORE_ORDER.match(order_id):
        return "Cult Store"
    return None


def normalize_platform(raw, order_id: str | None, platform_map: dict[str, str]) -> tuple[str, bool]:
    """Returns (platform, inferred). The configured map folds spelling variants;
    values mapping to Unknown are filled from the order-id pattern when possible."""
    text = clean_text(raw)
    mapped = platform_map.get(alias_key(text), text) if text else UNKNOWN
    if mapped in (None, UNKNOWN):
        guessed = platform_from_order_id(order_id)
        if guessed:
            return guessed, True
        return UNKNOWN, False
    return mapped, False
