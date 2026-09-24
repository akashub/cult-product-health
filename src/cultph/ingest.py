"""Parse raw tabs into clean, typed tables. Every output row keeps src_tab and
src_row (the 1-based sheet row) so any number in the dashboard can be traced
back to the exact cell it came from."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import Config
from .normalize import (
    UNKNOWN,
    alias_key,
    clean_id,
    clean_text,
    is_blank,
    month_label_number,
    normalize_platform,
    parse_dt,
    split_ids,
)

ROLES = ("tickets", "approved", "pending", "wms", "sales")


@dataclass
class IngestResult:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    source_rows: dict[str, int] = field(default_factory=dict)  # non-blank rows per role
    rejects: list[dict] = field(default_factory=list)
    header_errors: list[str] = field(default_factory=list)


class ProductResolver:
    """SKU code is authoritative; model-name aliases are used when there is no
    SKU. Names nobody has mapped yet are reported, never guessed."""

    def __init__(self, cfg: Config):
        self.skus = cfg.sku_index()
        self.aliases = cfg.alias_index()
        self.not_mentioned = cfg.not_mentioned

    def resolve(self, sku: str | None, model: str | None) -> dict:
        by_sku = self.skus.get(alias_key(sku)) if sku else None
        key = alias_key(model) if model else ""
        by_name = self.aliases.get(key) if key else None
        if by_sku:
            product, method = by_sku, "sku"
        elif by_name:
            product, method = by_name, "alias"
        elif not key or key in self.not_mentioned:
            product, method = None, "not_mentioned"
        else:
            product, method = None, "unmapped"
        conflict = bool(by_sku and by_name and by_sku != by_name)
        return {"product": product, "map_method": method, "name_conflict": conflict}


def _column_getter(headers: list[str], columns: dict[str, str], role: str, errors: list[str]):
    idx = {h: i for i, h in enumerate(headers)}
    pos = {}
    for field_name, header in columns.items():
        header = str(header).strip()
        if header in idx:
            pos[field_name] = idx[header]
        else:
            errors.append(f"{role}: column {header!r} (for {field_name}) not found")
    return lambda row, f: row[pos[f]] if f in pos and pos[f] < len(row) else None


def _channel_platform(channel: str | None, prefixes: list[tuple[str, str]]) -> str:
    if not channel:
        return UNKNOWN
    low = channel.lower()
    for prefix, platform in prefixes:
        if low.startswith(prefix):
            return platform
    return "Other"


def _parse_row(role: str, get, row, cfg: Config, resolver: ProductResolver) -> tuple[dict | None, str | None]:
    """Returns (record, reject_reason)."""
    df = cfg.dayfirst
    if role == "tickets":
        order_id = clean_id(get(row, "order_id"))
        created = parse_dt(get(row, "created_at"), df)
        if created is None:
            return None, "unparseable created_at"
        platform, inferred = normalize_platform(get(row, "platform"), order_id, cfg.platform_map)
        l2 = clean_text(get(row, "l2"))
        rec = {
            "ticket_id": clean_id(get(row, "ticket_id")),
            "created_at": created,
            "platform": platform,
            "platform_inferred": inferred,
            "order_id": order_id,
            "model_raw": clean_text(get(row, "model")),
            "l1": clean_text(get(row, "l1")),
            "l2": l2,
            "l3": clean_text(get(row, "l3")),
            "is_product_issue": alias_key(l2 or "") in cfg.product_issue_l2,
            "subject": clean_text(get(row, "subject")),
        }
        rec.update(resolver.resolve(None, rec["model_raw"]))
    elif role == "approved":
        order_id = clean_id(get(row, "order_id"))
        event = parse_dt(get(row, "event_date"), df)
        if event is None:
            return None, "unparseable event_date"
        mode = clean_text(get(row, "mode"))
        marketplace, inferred = normalize_platform(None, order_id, cfg.platform_map)
        amount = get(row, "amount")
        try:
            amount = float(amount) if not is_blank(amount) else None
        except (TypeError, ValueError):
            amount = None
        rec = {
            "event_date": event,
            "order_id": order_id,
            "batch": clean_text(get(row, "batch")),
            "model_raw": clean_text(get(row, "model")),
            "sku": clean_id(get(row, "sku")),
            "amount": amount,
            "issue": clean_text(get(row, "issue")),
            "mode": mode.title() if mode else None,
            "channel": clean_text(get(row, "channel")),
            "platform": marketplace,
            "platform_inferred": inferred,
            "awbs": ",".join(split_ids(get(row, "awb"))) or None,
            "warehouse": clean_text(get(row, "warehouse")),
        }
        rec.update(resolver.resolve(rec["sku"], rec["model_raw"]))
    elif role == "pending":
        order_id = clean_id(get(row, "order_id"))
        created = parse_dt(get(row, "created_at"), df)
        if created is None:
            return None, "unparseable created_at"
        platform, inferred = normalize_platform(None, order_id, cfg.platform_map)
        rec = {
            "created_at": created,
            "order_id": order_id,
            "sku": clean_id(get(row, "sku")),
            "model_raw": clean_text(get(row, "model")),
            "reason": clean_text(get(row, "reason")),
            "created_by": clean_text(get(row, "created_by")),
            "comment": clean_text(get(row, "comment")),
            "platform": platform,
            "platform_inferred": inferred,
        }
        rec.update(resolver.resolve(rec["sku"], rec["model_raw"]))
    elif role == "wms":
        created = parse_dt(get(row, "created_at"), df)
        if created is None:
            return None, "unparseable created_at"
        channel = clean_text(get(row, "channel_name"))
        rec = {
            "created_at": created,
            "channel_name": channel,
            "platform": _channel_platform(channel, cfg.channel_prefixes),
            "channel_type": clean_text(get(row, "channel_type")),
            "return_type": clean_text(get(row, "return_type")),
            "return_sub_type": clean_text(get(row, "return_sub_type")),
            "reason": clean_text(get(row, "reason")),
            "order_id": clean_id(get(row, "order_id")),
            "item_code": clean_id(get(row, "item_code")),
            "sku": clean_id(get(row, "sku")),
            "model_raw": clean_text(get(row, "model")),
            "facility": clean_text(get(row, "facility")),
            "status": clean_text(get(row, "status")),
        }
        rec.update(resolver.resolve(rec["sku"], rec["model_raw"]))
    elif role == "sales":
        # one row = units sold of a product (or SKU) in a period, optionally per platform
        period = parse_dt(get(row, "date"), df)
        if period is None:
            n = month_label_number(get(row, "month_label"))
            year = get(row, "year")
            if n and not is_blank(year):
                period = parse_dt(f"{int(float(year))}-{n:02d}-01", False)
        if period is None:
            return None, "unparseable sales period"
        try:
            units = float(get(row, "units"))
        except (TypeError, ValueError):
            return None, "units not a number"
        platform_raw = get(row, "platform")
        platform = normalize_platform(platform_raw, None, cfg.platform_map)[0] if not is_blank(platform_raw) else "All"
        rev = get(row, "revenue")
        try:
            rev = float(rev) if not is_blank(rev) else None
        except (TypeError, ValueError):
            rev = None
        rec = {"created_at": period, "sku": clean_id(get(row, "sku")), "model_raw": clean_text(get(row, "model")),
               "platform": platform, "units": units, "revenue": rev}
        rec.update(resolver.resolve(rec["sku"], rec["model_raw"]))
    else:
        raise ValueError(role)

    date_field = "event_date" if role == "approved" else "created_at"
    rec["month"] = rec[date_field].strftime("%Y-%m")
    rec["month_label_num"] = month_label_number(get(row, "month_label"))
    return rec, None


def ingest(source, cfg: Config) -> IngestResult:
    res = IngestResult()
    resolver = ProductResolver(cfg)
    for role in ROLES:
        tab_cfg = cfg.tabs.get(role)
        if not tab_cfg:
            continue
        headers, rows = source.table(tab_cfg["name"])
        get = _column_getter(headers, tab_cfg["columns"], role, res.header_errors)
        records, nonblank = [], 0
        for i, row in enumerate(rows):
            if all(is_blank(v) for v in row):
                continue
            nonblank += 1
            src_row = i + 2  # header is sheet row 1
            rec, reason = _parse_row(role, get, row, cfg, resolver)
            if rec is None:
                res.rejects.append({"role": role, "src_tab": tab_cfg["name"], "src_row": src_row, "reason": reason})
                continue
            rec["src_tab"] = tab_cfg["name"]
            rec["src_row"] = src_row
            records.append(rec)
        res.source_rows[role] = nonblank
        df = pd.DataFrame.from_records(records)
        if not df.empty:
            df["category"] = df["product"].map(cfg.category_of())
        res.tables[role] = df
    return res
