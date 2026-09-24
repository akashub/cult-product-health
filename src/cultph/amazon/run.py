"""Poll configured ASINs: product page every time (rating + histogram + top
reviews), then the 'most recent' review listing until we reach reviews we
already have. --backfill walks every star filter too (needs login)."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from . import store
from .checks import check_product, check_reviews, implied_gap, rounding_warning
from .fetch import RAW_DIR, browser
from .parse import BLOCK_CAPTCHA, BLOCK_SIGNIN, detect_block, parse_product, parse_review_page

STAR_FILTERS = ["one_star", "two_star", "three_star", "four_star", "five_star"]


class CaptchaHit(Exception):
    pass


@dataclass
class RunSummary:
    snapshots: int = 0
    new_reviews: list[tuple[str, str]] = field(default_factory=list)  # (asin, review_id)
    problems: list[str] = field(default_factory=list)
    login_required: bool = False


def _save_raw(name: str, html: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    (RAW_DIR / f"{name}.html").write_text(html)


def _fetch_checked(f, con, asin, url, raw_name):
    final, html = f.get(url)
    block = detect_block(html, final)
    if block == BLOCK_CAPTCHA:
        _save_raw(raw_name, html)
        store.log_run(con, asin, url, "fail", "captcha")
        raise CaptchaHit(url)
    return block, html


def poll_asin(f, con, domain, asin, product, summary: RunSummary, max_pages: int, backfill: bool) -> None:
    url = f"https://www.{domain}/dp/{asin}"
    # Amazon sometimes serves an alternate layout without the ratings block; retry before failing.
    for attempt in range(3):
        block, html = _fetch_checked(f, con, asin, url, f"{asin}_product_fail")
        if block:
            store.log_run(con, asin, url, "fail", block)
            summary.problems.append(f"{asin}: product page blocked ({block})")
            return
        parsed = parse_product(html)
        errs = check_product(parsed)
        if not errs:
            break
    if errs:
        _save_raw(f"{asin}_product_fail", html)
        store.log_run(con, asin, url, "fail", f"after 3 attempts: {'; '.join(errs)}")
        summary.problems.append(f"{asin}: " + "; ".join(errs))
        return
    store.add_snapshot(con, asin, product, parsed)
    pool = parsed.get("parent_asin")
    new = store.upsert_reviews(con, asin, product, parsed["reviews"], "product_page", pool)
    summary.snapshots += 1
    summary.new_reviews += [(asin, r) for r in new]
    warn = rounding_warning(parsed)
    store.log_run(con, asin, url, "warn" if warn else "pass",
                  f"avg {parsed['avg_rating']} n {parsed['total_ratings']} gap {implied_gap(parsed)} new {len(new)}"
                  + (f"; {warn}" if warn else ""))
    if warn:
        summary.problems.append(f"{asin}: {warn}")
    con.commit()

    walks = [("recent", None)] + ([(s, star) for star in STAR_FILTERS for s in ("recent", "helpful")] if backfill else [])
    for sort, star in walks:
        for page in range(1, max_pages + 1):
            url = (f"https://www.{domain}/product-reviews/{asin}?reviewerType=all_reviews"
                   f"&sortBy={sort}&pageNumber={page}" + (f"&filterByStar={star}" if star else ""))
            block, html = _fetch_checked(f, con, asin, url, f"{asin}_reviews_fail")
            if block == BLOCK_SIGNIN:
                store.log_run(con, asin, url, "warn", "login required for review listing")
                summary.login_required = True
                return
            rp = parse_review_page(html)
            errs = check_reviews(rp["reviews"])
            if not rp["reviews"] and not rp["empty_marker"] and page == 1:
                # signed in, not blocked, yet nothing parsed: the markup probably changed
                errs.append("listing page parsed to 0 reviews without a 'no reviews' message; selectors may be stale")
            if errs:
                _save_raw(f"{asin}_reviews_fail", html)
                store.log_run(con, asin, url, "fail", "; ".join(errs))
                summary.problems.append(f"{asin} p{page}: " + "; ".join(errs))
                break
            new = store.upsert_reviews(con, asin, product, rp["reviews"], f"listing:{sort}:{star or 'all'}", pool)
            summary.new_reviews += [(asin, r) for r in new]
            store.log_run(con, asin, url, "pass", f"{len(rp['reviews'])} reviews, {len(new)} new; {rp['filter_info'] or ''}")
            con.commit()
            caught_up = not backfill and sort == "recent" and rp["reviews"] and not new
            if caught_up or not rp["has_next"] or not rp["reviews"]:
                break


def poll(amazon_cfg: dict, only_asin: str | None = None, backfill: bool = False, headless: bool = True) -> RunSummary:
    domain = amazon_cfg.get("domain", "amazon.in")
    asins = amazon_cfg.get("asins", {})
    if only_asin:
        asins = {only_asin: asins.get(only_asin, only_asin)}
    summary = RunSummary()
    con = store.connect()
    try:
        with browser(headless=headless, delay=tuple(amazon_cfg.get("delay_seconds", [4, 9]))) as f:
            for asin, product in asins.items():
                try:
                    poll_asin(f, con, domain, asin, product, summary, amazon_cfg.get("max_recent_pages", 10), backfill)
                except CaptchaHit as e:
                    summary.problems.append(f"captcha at {e}; stopping this run to back off")
                    break
    finally:
        store.attribute_reviews(con, amazon_cfg.get("variant_map"))
        con.commit()
        con.close()
    return summary


def discover(domain: str, query: str, brand: str = "cult", headless: bool = True) -> list[dict]:
    """Search results whose title starts with the brand, to help build the ASIN list."""
    with browser(headless=headless) as f:
        final, html = f.get(f"https://www.{domain}/s?k={quote_plus(query)}")
    if detect_block(html, final):
        raise RuntimeError(f"search blocked: {detect_block(html, final)}")
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for d in soup.select('div[data-asin][data-component-type="s-search-result"]'):
        title = (d.select_one("h2") or d).get_text(" ", strip=True)
        asin = d["data-asin"]
        if asin and asin not in seen and title.lower().startswith(brand.lower()):
            seen.add(asin)
            r = d.select_one("span.a-icon-alt")
            out.append({"asin": asin, "title": title[:90], "rating": r.get_text(strip=True) if r else ""})
    return out
