"""cultph command line.

  cultph sync      read the configured source, judge it, publish if it passes
  cultph sheets    list Google Sheets shared with your Gmail account
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .db import publish
from .ingest import ingest
from .judge import run_checks, verdict
from .sources import list_shared_sheets, open_source

ICON = {"pass": "✔", "warn": "!", "fail": "✘"}


def cmd_sync(args) -> int:
    cfg = load_config(args.config)
    source = open_source(cfg.source)
    res = ingest(source, cfg)
    checks = run_checks(res, cfg, source)
    status = verdict(checks)
    meta = {"config": cfg.path.name, "source": cfg.source, "source_rows": res.source_rows}
    for c in checks:
        print(f" {ICON[c['status']]} {c['check']:<28} {c['detail']}")
    published = publish(res.tables, res.rejects, checks, meta, status)
    print(f"\nverdict: {status.upper()} — {'published' if published else 'BLOCKED, previous snapshot kept'}")
    return 0 if published else 1


def cmd_sheets(args) -> int:
    for f in list_shared_sheets():
        print(f"{f['id']}  {f['name']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cultph")
    p.add_argument("--config", help="path to config yaml (default: config.private.yaml, else example)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("sheets").set_defaults(fn=cmd_sheets)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
