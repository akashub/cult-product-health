"""Runs only on a machine that has config.private.yaml and the real workbook.
The sheet's own Dashboard numbers must be reproduced exactly."""

import pytest

from cultph.config import ROOT, load_config
from cultph.ingest import ingest
from cultph.judge import run_checks, verdict
from cultph.sources import open_source

PRIVATE = ROOT / "config.private.yaml"


def _cfg():
    if not PRIVATE.exists():
        pytest.skip("no config.private.yaml")
    cfg = load_config(PRIVATE)
    if cfg.source.get("type") != "xlsx" or not (ROOT / cfg.source["path"]).exists():
        pytest.skip("real workbook not present")
    return cfg


def test_real_workbook_passes_judge():
    cfg = _cfg()
    src = open_source(cfg.source)
    res = ingest(src, cfg)
    checks = {c["check"]: c for c in run_checks(res, cfg, src)}
    assert checks["dashboard_reconcile"]["status"] == "pass", checks["dashboard_reconcile"]["detail"]
    assert verdict(list(checks.values())) != "fail", [c for c in checks.values() if c["status"] == "fail"]
