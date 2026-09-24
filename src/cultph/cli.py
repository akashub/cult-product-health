"""cultph command line.

  cultph sync      read the configured source, judge it, publish if it passes
  cultph sheets    list Google Sheets shared with your Gmail account
  cultph amazon    poll configured ASINs (ratings, histogram, new reviews)
  cultph amazon-login      sign in once in a visible browser (secondary account)
  cultph amazon-discover Q list Cult-brand search results to build the ASIN list
  cultph flipkart  poll configured Flipkart listings (no login needed)
  cultph flipkart-discover Q  list Cult-brand Flipkart product paths
  cultph label     classify new reviews one by one (classifier + judge)
  cultph alerts    evaluate alert rules and notify (first run sets a baseline)
  cultph run       sync → amazon → label (if API key) → alerts; for scheduling
  cultph schedule  write a launchd plist (does not install it; macOS)
  cultph watch     run everything every N minutes in the foreground (any OS)
  cultph setup     enter API keys / alert credentials (saved to data/.env)
  cultph doctor    show what's ready and the next step for what isn't
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
    from .sources import GoogleAuthError

    cfg = load_config(args.config)
    try:
        source = open_source(cfg.source)
    except (GoogleAuthError, FileNotFoundError) as e:
        print(f" ✘ source: {e}")
        return 1
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
    auth = load_config(args.config).source.get("auth", "oauth")
    files = list_shared_sheets(auth)
    for f in files:
        print(f"{f['id']}  {f['name']}")
    print(f"\n{len(files)} spreadsheets. Put the right id under source.spreadsheet_id with source.type: gsheet.")
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


def cmd_flipkart(args) -> int:
    from .flipkart.run import poll

    cfg = load_config(args.config)
    s = poll(cfg.raw.get("flipkart", {}), backfill=args.backfill, headless=not args.headed)
    print(f"flipkart snapshots: {s.snapshots}  new reviews: {len(s.new_reviews)}")
    for p in s.problems:
        print(f" ✘ {p}")
    return 1 if s.problems else 0


def cmd_flipkart_discover(args) -> int:
    from .flipkart.run import discover

    for path in discover(args.query):
        print(f"{path}")
    return 0


def cmd_amazon_login(args) -> int:
    from .amazon.fetch import interactive_login

    ok = interactive_login(load_config(args.config).raw.get("amazon", {}).get("domain", "amazon.in"))
    return 0 if ok else 1


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


def cmd_alerts(args) -> int:
    from .alerts import deliver, evaluate, load_sheet_tables, resolve_channels
    from .amazon import store as amazon_store
    from .db import LIVE_DB

    cfg = load_config(args.config)
    alert_cfg = cfg.raw.get("alerts", {})
    if getattr(args, "test", False):
        from .alerts import CHANNELS, Alert

        a = Alert("test", "test", "normal", "Test alert", "If you can read this, this channel works.")
        from .alerts import resolve_channels

        chans = resolve_channels(alert_cfg.get("channels", "auto"))
        if not chans:
            print("  no channels configured (run `uv run cultph setup`); alerts still appear in the dashboard feed")
        for name in chans:
            try:
                print(f"  {name}: {'sent' if CHANNELS[name](a) else 'not configured'}")
            except Exception as e:  # noqa: BLE001
                print(f"  {name}: error {e}")
        return 0
    con = amazon_store.connect()
    first = con.execute("SELECT count(*) FROM sqlite_master WHERE name='alert_state'").fetchone()[0] == 0 or \
        con.execute("SELECT count(*) FROM alert_state WHERE key='baseline_at'").fetchone()[0] == 0
    alerts = evaluate(con, cfg.raw.get("amazon", {}), alert_cfg, load_sheet_tables(LIVE_DB))
    deliver(con, alerts, resolve_channels(alert_cfg.get("channels", "auto")))
    con.commit()
    if first:
        print("alerts baseline recorded; nothing sent on the first run")
    print(f"{len(alerts)} new alerts")
    for a in alerts:
        print(f"  [{a.priority}] {a.title} — {a.detail[:120]}")
    return 0


def cmd_run(args) -> int:
    from .config import env

    steps = [("sync", cmd_sync), ("amazon", cmd_amazon), ("flipkart", cmd_flipkart)]
    if env("ANTHROPIC_API_KEY"):
        steps.append(("label", cmd_label))
    else:
        print("(skipping label: no ANTHROPIC_API_KEY)")
    steps.append(("alerts", cmd_alerts))
    failed = []
    for name, fn in steps:
        print(f"\n== {name}")
        try:
            if fn(args):
                failed.append(name)
        except Exception as e:  # noqa: BLE001 - later steps (alerts) should still run
            print(f"  ✘ {name} crashed: {e}")
            failed.append(name)
    print(f"\nrun finished; failed steps: {', '.join(failed) or 'none'}")
    return 1 if failed else 0


def cmd_schedule(args) -> int:
    import shutil

    from .config import DATA_DIR, ROOT

    uv = shutil.which("uv") or "uv"
    label = "com.cultph.run"
    plist = DATA_DIR / f"{label}.plist"
    plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>
    <string>{uv}</string><string>run</string><string>--project</string><string>{ROOT}</string>
    <string>cultph</string><string>run</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>StartInterval</key><integer>{args.every * 60}</integer>
  <key>StandardOutPath</key><string>{DATA_DIR / "run.log"}</string>
  <key>StandardErrorPath</key><string>{DATA_DIR / "run.log"}</string>
</dict></plist>
""")
    print(f"wrote {plist} (every {args.every} min). Not installed. To turn it on:")
    print(f"  cp '{plist}' ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/{label}.plist")
    print(f"To turn it off:\n  launchctl bootout gui/$(id -u)/{label}")
    print("It runs only while the Mac is awake.")
    return 0


def cmd_setup(args) -> int:
    from .setup import run_setup

    run_setup()
    return 0


def cmd_doctor(args) -> int:
    from .setup import print_doctor

    return print_doctor(live=args.live)


def cmd_watch(args) -> int:
    """Cross-platform scheduler: run everything every N minutes until Ctrl-C."""
    import random
    import time
    from datetime import datetime

    print(f"Running every {args.every} min (±10%). Ctrl-C to stop. Works only while this computer is awake.")
    try:
        while True:
            print(f"\n######## {datetime.now():%Y-%m-%d %H:%M}")
            cmd_run(args)
            time.sleep(args.every * 60 * random.uniform(0.9, 1.1))
    except KeyboardInterrupt:
        print("stopped")
    return 0


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
    fk = sub.add_parser("flipkart")
    fk.add_argument("--backfill", action="store_true", help="walk all review pages")
    fk.add_argument("--headed", action="store_true")
    fk.set_defaults(fn=cmd_flipkart)
    fd = sub.add_parser("flipkart-discover")
    fd.add_argument("query")
    fd.set_defaults(fn=cmd_flipkart_discover)
    d = sub.add_parser("amazon-discover")
    d.add_argument("query")
    d.set_defaults(fn=cmd_amazon_discover)
    lb = sub.add_parser("label")
    lb.add_argument("--limit", type=int)
    lb.set_defaults(fn=cmd_label)
    al = sub.add_parser("alerts")
    al.add_argument("--test", action="store_true", help="send a test message on each configured channel")
    al.set_defaults(fn=cmd_alerts)
    r = sub.add_parser("run")
    r.set_defaults(fn=cmd_run, asin=None, backfill=False, headed=False, limit=None)
    sub.add_parser("setup").set_defaults(fn=cmd_setup)
    dr = sub.add_parser("doctor")
    dr.add_argument("--live", action="store_true", help="also test Amazon, AI and Google connections for real")
    dr.set_defaults(fn=cmd_doctor)
    w = sub.add_parser("watch")
    w.add_argument("--every", type=int, default=60, help="minutes between runs")
    w.set_defaults(fn=cmd_watch, asin=None, backfill=False, headed=False, limit=None)
    sc = sub.add_parser("schedule")
    sc.add_argument("--every", type=int, default=60, help="minutes between runs")
    sc.set_defaults(fn=cmd_schedule)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
