from datetime import datetime

import pytest

from cultph.normalize import (
    alias_key, clean_id, month_label_number, normalize_platform, parse_dt, platform_from_order_id, split_ids,
)


@pytest.mark.parametrize("raw,expected", [
    (200001.0, "200001"), (9000000000004.0, "9000000000004"), ("200001.0", "200001"),
    ("`30000000000001", "30000000000001"), ("  OD123 ", "OD123"), (None, None), ("", None), (42, "42"),
])
def test_clean_id(raw, expected):
    assert clean_id(raw) == expected


def test_split_ids():
    assert split_ids("9000000000001  9000000000002") == ["9000000000001", "9000000000002"]
    assert split_ids(9000000000003.0) == ["9000000000003"]


def test_alias_key_keeps_plus_distinct():
    assert alias_key("Cult Volt +") == alias_key("cult volt plus") != alias_key("Cult Volt")
    assert alias_key("Cult Hola ( Blue )") == "culthola blue".replace(" ", "")


def test_month_label():
    assert month_label_number("1~January") == 1
    assert month_label_number("12~December") == 12
    assert month_label_number("junk") is None


def test_platform_inference():
    assert platform_from_order_id("402-0000000-0000001") == "Amazon"
    assert platform_from_order_id("OD100000000000000001") == "Flipkart"
    assert platform_from_order_id("100001") == "Cult Store"
    assert platform_from_order_id("abc") is None
    pmap = {alias_key("No Details received"): "Unknown", alias_key("1P"): "Cult Store"}
    assert normalize_platform("No Details received", "OD1", pmap) == ("Flipkart", True)
    assert normalize_platform("No Details received", None, pmap) == ("Unknown", False)
    assert normalize_platform("1P", None, pmap) == ("Cult Store", False)
    assert normalize_platform("Zepto", None, pmap) == ("Zepto", False)


def test_parse_dt():
    assert parse_dt(datetime(2026, 1, 2)) == datetime(2026, 1, 2)
    assert parse_dt("05/01/2026") == datetime(2026, 1, 5)  # day-first
    assert parse_dt(46023.5).date().isoformat() == "2026-01-01"  # Sheets serial
    assert parse_dt("nope") is None


def test_clean_text_whole_number_float():
    from cultph.normalize import clean_text
    assert clean_text(564690.0) == "564690" and clean_text(1.5) == "1.5" and clean_text("  a  b ") == "a b"
