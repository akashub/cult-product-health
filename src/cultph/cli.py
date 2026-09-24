"""cultph command line.

  cultph sync      read the configured source, judge it, publish if it passes
  cultph sheets    list Google Sheets shared with your Gmail account
  cultph amazon    poll configured ASINs (ratings, histogram, new reviews)
  cultph amazon-login      sign in once in a visible browser (secondary account)
  cultph amazon-discover Q list Cult-brand search results to build the ASIN list
  cultph label     classify new reviews one by one (classifier + judge)
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


def cmd_amazon(args) -> int:
    from .amazon.run import poll

    cfg = load_config(args.config)
    s = poll(cfg.raw.get("amazon", {}), only_asin=args.asin, backfill=args.backfill, headless=not args.headed)
    print(f"snapshots: {s.snapshots}  new reviews: {len(s.new_reviews)}")
    if s.login_required:
        print("! review listing needs login: run `cultph amazon-login` (product-page data was still captured)")
    for p in s.problems:
        print(f" ✘ {p}")
    return 1 if s.problems else 0


def cmd_amazon_login(args) -> int:
    from .amazon.fetch import interactive_login

    interactive_login(load_config(args.config).raw.get("amazon", {}).get("domain", "amazon.in"))
    return 0


def cmd_amazon_discover(args) -> int:
    from .amazon.run import discover

    domain = load_config(args.config).raw.get("amazon", {}).get("domain", "amazon.in")
    for r in discover(domain, args.query):
        print(f"{r['asin']}  {r['rating']:<20} {r['title']}")
    return 0


def cmd_label(args) -> int:
    from .ai import store as label_store
    from .ai.labels import PROMPT_VERSION, Labeler
    from .amazon import store as amazon_store

    cfg = load_config(args.config)
    ai = cfg.raw.get("ai", {})
    taxonomy = ai.get("taxonomy") or []
    if not taxonomy:
        print("no ai.taxonomy in config")
        return 1
    con = amazon_store.connect()
    todo = label_store.pending_reviews(con, PROMPT_VERSION, args.limit)
    print(f"{len(todo)} reviews to label")
    labeler = Labeler(taxonomy, ai.get("classifier_model", "claude-haiku-4-5-20251001"),
                      ai.get("judge_model", "claude-sonnet-5"))
    counts = {"auto": 0, "queue": 0, "error": 0}
    for i, r in enumerate(todo, 1):
        try:
            lab = labeler.label(r)
        except Exception as e:  # noqa: BLE001 - one bad call must not stop the batch
            counts["error"] += 1
            print(f"  ✘ {r['review_id']}: {e}")
            continue
        label_store.save_label(con, lab)
        con.commit()
        counts[lab["status"]] += 1
        print(f"  [{i}/{len(todo)}] {r['review_id']} {lab['status']:<5} {lab['codes'] or '-'}")
    audits = label_store.sample_audits(con, PROMPT_VERSION, ai.get("audit_rate", 0.10), ai.get("audit_min_per_product", 3))
    con.commit()
    print(f"auto {counts['auto']}  queued for review {counts['queue']}  errors {counts['error']}  "
          f"new audit samples {audits}")
    return 1 if counts["error"] else 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cultph")
    p.add_argument("--config", help="path to config yaml (default: config.private.yaml, else example)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync").set_defaults(fn=cmd_sync)
    sub.add_parser("sheets").set_defaults(fn=cmd_sheets)
    a = sub.add_parser("amazon")
    a.add_argument("--asin")
    a.add_argument("--backfill", action="store_true", help="walk every star filter (needs login)")
    a.add_argument("--headed", action="store_true", help="show the browser")
    a.set_defaults(fn=cmd_amazon)
    sub.add_parser("amazon-login").set_defaults(fn=cmd_amazon_login)
    d = sub.add_parser("amazon-discover")
    d.add_argument("query")
    d.set_defaults(fn=cmd_amazon_discover)
    lb = sub.add_parser("label")
    lb.add_argument("--limit", type=int)
    lb.set_defaults(fn=cmd_label)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
