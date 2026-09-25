"""Poll Flipkart listings: product page (JSON-LD rating + reviews link), then
the reviews page sorted by latest until caught up. No login needed. Star
counts on Flipkart are exact, so the rating math has no rounding range."""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote_plus

from ..amazon import store
from ..amazon.fetch import RAW_DIR, browser
from ..amazon.run import RunSummary
from .parse import check_reviews_page, detect_block, parse_product, parse_reviews_page

BASE = "https://www.flipkart.com"


def _save_raw(name, html):
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{name}.html").write_text(html)


def poll_listing(f, con, url: str, product: str, summary: RunSummary, max_pages: int, backfill: bool) -> None:
    for _attempt in range(3):  # Flipkart sometimes serves a bare shell page; retry before failing
        final, html = f.get(url, wait_for_text="ratings")
        block = detect_block(html, final)
        if block:
            store.log_run(con, url, url, "fail", block)
            summary.problems.append(f"flipkart {product}: blocked ({block})")
            return
        prod = parse_product(html)
        if prod.get("reviews_path"):
            break
    if not prod.get("reviews_path") and "<title>" in html and "Specifications" in html:
        # a real product page with no customer ratings yet (the '4.1' shown is the seller's rating)
        store.log_run(con, url, url, "warn", "no customer ratings yet")
        summary.no_ratings += 1
        return
    if not prod.get("reviews_path"):
        _save_raw("fk_product_fail", html)
        store.log_run(con, url, url, "fail", "no reviews link / JSON-LD on product page after 3 attempts")
        summary.problems.append(f"flipkart {product}: product page layout changed")
        return
    pid = prod["pid"]
    for page in range(1, (50 if backfill else max_pages) + 1):
        rurl = f"{BASE}{prod['reviews_path']}&sortOrder=MOST_RECENT&page={page}"
        final, html = f.get(rurl, wait_for_text="Helpful", scroll=True)
        if detect_block(html, final):
            store.log_run(con, pid, rurl, "fail", detect_block(html, final))
            summary.problems.append(f"flipkart {product}: reviews blocked")
            return
        rp = parse_reviews_page(html, pid, date.today())
        if page == 1 and (rp["total_reviews"] or 0) > len(rp["reviews"]) and len(rp["reviews"]) < 5:
            # On some listings the 'latest' view renders only a few reviews; add the default view's page 1
            _, html2 = f.get(f"{BASE}{prod['reviews_path']}", wait_for_text="Helpful", scroll=True)
            extra = parse_reviews_page(html2, pid, date.today())
            known = {r["review_id"] for r in rp["reviews"]}
            rp["reviews"] += [r for r in extra["reviews"] if r["review_id"] not in known]
            rp["duplicates"] += extra["duplicates"]
            if rp["total_ratings"] is None:
                rp.update(total_ratings=extra["total_ratings"], total_reviews=extra["total_reviews"],
                          star_counts=extra["star_counts"])
        if page == 1:
            errs = check_reviews_page(rp)
            if errs:
                _save_raw(f"fk_{pid}_reviews_fail", html)
                store.log_run(con, pid, rurl, "fail", "; ".join(errs))
                summary.problems.append(f"flipkart {product}: " + "; ".join(errs))
                return
            store.add_count_snapshot(con, pid, product, "flipkart", prod.get("avg_rating"), rp["star_counts"],
                                     prod.get("price"))
            summary.snapshots += 1
            if (rp["total_reviews"] or 0) >= 10 and len(rp["reviews"]) < 5:
                # star counts are fine, but the review list didn't render fully: say so loudly
                _save_raw(f"fk_{pid}_thin", html)
                store.log_run(con, pid, rurl, "warn", f"only {len(rp['reviews'])} distinct reviews on page 1 "
                              f"of {rp['total_reviews']} ({rp['duplicates']} repeats); list may not have rendered")
                summary.problems.append(f"flipkart {product}: only {len(rp['reviews'])} reviews parsed on page 1")
            gap = ""
            if prod.get("total_ratings") and prod["total_ratings"] != rp["total_ratings"]:
                gap = f"; product page says {prod['total_ratings']} ratings"
        new = store.upsert_reviews(con, pid, product, rp["reviews"], f"flipkart:recent:p{page}", pid, "flipkart")
        summary.new_reviews += [(pid, r) for r in new]
        store.log_run(con, pid, rurl, "pass", f"{len(rp['reviews'])} reviews, {len(new)} new"
                      + (f", {rp['duplicates']} repeated on page" if rp["duplicates"] else "")
                      + (f"; {rp['total_ratings']} ratings{gap}" if page == 1 else ""))
        con.commit()
        if not rp["reviews"] or (not backfill and not new):
            break


def poll(fk_cfg: dict, backfill: bool = False, headless: bool = True, variant_map: dict | None = None) -> RunSummary:
    summary = RunSummary()
    listings = fk_cfg.get("listings", {})
    if not listings:
        return summary
    con = store.connect()
    try:
        with browser(headless=headless, delay=tuple(fk_cfg.get("delay_seconds", [4, 9]))) as f:
            for path, product in listings.items():
                f.fresh_page()  # Flipkart pages are heavy; a new tab per listing keeps memory flat
                url = path if path.startswith("http") else BASE + path
                try:
                    poll_listing(f, con, url, product, summary, fk_cfg.get("max_recent_pages", 5), backfill)
                except Exception as e:  # noqa: BLE001 - one listing must not stop the rest
                    summary.problems.append(f"flipkart {product}: {type(e).__name__}: {e}")
        # if most listings suddenly have 'no ratings', the page layout probably changed
        if summary.no_ratings > len(listings) / 2:
            summary.problems.append(f"flipkart: {summary.no_ratings}/{len(listings)} listings show no ratings; "
                                    "the product page layout may have changed")
    finally:
        store.attribute_reviews(con, variant_map)
        con.commit()
        con.close()
    return summary


def discover(query: str, brand: str = "cult", headless: bool = True) -> list[str]:
    with browser(headless=headless) as f:
        final, html = f.get(f"{BASE}/search?q={quote_plus(query)}")
    return list(dict.fromkeys(ln.split("?")[0] for ln in re.findall(
        rf'href="(/{brand}[^"]*?/p/itm[0-9a-z]+[^"]*)"', html)))
