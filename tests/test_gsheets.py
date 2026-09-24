"""The Google Sheets path must produce the same result as the xlsx path."""

import pytest

from cultph.config import ROOT, load_config
from cultph.ingest import ingest
from cultph.judge import run_checks, verdict
from cultph.sources import XlsxSource

from make_fixture import build
from sheets_replay import replay


def _compare(cfg, xlsx):
    a = ingest(XlsxSource(xlsx), cfg)
    src = replay(xlsx)
    b = ingest(src, cfg)
    assert a.source_rows == b.source_rows and not b.rejects and not b.header_errors
    for role in a.tables:
        cols = [c for c in a.tables[role].columns if c not in ("created_at", "event_date")]
        left, right = (t[cols].astype(object).where(t[cols].notna(), None) for t in (a.tables[role], b.tables[role]))
        assert left.equals(right), role
        assert (a.tables[role]["month"] == b.tables[role]["month"]).all()
    checks = {c["check"]: c for c in run_checks(b, cfg, src)}
    assert checks["dashboard_reconcile"]["status"] == "pass", checks["dashboard_reconcile"]["detail"]
    return verdict(list(checks.values()))


def test_sheets_shape_matches_xlsx_on_fixture(tmp_path):
    wb = tmp_path / "s.xlsx"
    build(wb)
    assert _compare(load_config(ROOT / "config.example.yaml"), wb) != "fail"


def test_sheets_shape_matches_xlsx_on_real_workbook():
    private = ROOT / "config.private.yaml"
    if not private.exists():
        pytest.skip("no private config")
    cfg = load_config(private)
    xlsx = ROOT / cfg.source.get("path", "")
    if not xlsx.is_file():
        pytest.skip("no real workbook")
    assert _compare(cfg, xlsx) != "fail"
