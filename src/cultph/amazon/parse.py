"""Pure parsers over saved Amazon HTML. No network here, so it is testable.

Product page (no login needed): average, global rating count, histogram
(rounded percentages), ~8 top reviews. Review listing pages need a signed-in
session; they use the same review block markup."""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

BLOCK_SIGNIN = "signin"
BLOCK_CAPTCHA = "captcha"


def detect_block(html: str, final_url: str = "") -> str | None:
    """A captcha or sign-in page must never be stored as '0 reviews'."""
    if "/ap/signin" in final_url or 'name="signIn"' in html or "<title>Amazon Sign In" in html:
        return BLOCK_SIGNIN
    if "validateCaptcha" in html or "Type the characters you see" in html or "/errors/validateCaptcha" in final_url:
        return BLOCK_CAPTCHA
    return None


def _text(el) -> str | None:
    return el.get_text(" ", strip=True) if el else None


def _first(root, *selectors):
    for sel in selectors:
        el = root.select_one(sel)
        if el and el.get_text(strip=True):
            return el
    return None


def _num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    return float(m.group(0).replace(",", "")) if m else None


_DATE = re.compile(r"Reviewed in (.+?) on (\d{1,2} \w+ \d{4})")


def parse_review(el) -> dict:
    stars = _text(_first(el, '[data-hook="review-star-rating"]', '[data-hook="cmps-review-star-rating"]'))
    title_el = _first(el, '[data-hook="review-title"] span:not(.a-icon-alt):not(.a-letter-space)',
                      '[data-hook="review-title"]', '[data-hook="reviewTitle"]')
    title = _text(title_el)
    if title and stars and title.startswith(stars):  # listing pages prefix the title with the star text
        title = title[len(stars):].strip()
    body = _text(_first(el, '[data-hook="review-body"] span', '[data-hook="review-body"]',
                        '[data-hook="reviewRichContentContainer"]'))
    date_s = _text(el.select_one('[data-hook="review-date"]')) or ""
    m = _DATE.search(date_s)
    review_date, country = None, None
    if m:
        country = m.group(1)
        try:
            review_date = datetime.strptime(m.group(2), "%d %B %Y").date().isoformat()
        except ValueError:
            review_date = None
    helpful = _text(el.select_one('[data-hook="helpful-vote-statement"]')) or ""
    helpful_n = 1 if helpful.lower().startswith("one person") else int(_num(helpful) or 0)
    variant = _text(_first(el, '[data-hook="format-strip"]', '[data-hook="format-strip-linkless"]'))
    return {
        "review_id": el.get("id"),
        "rating": int(_num(stars)) if stars else None,
        "title": title,
        "body": body,
        "review_date": review_date,
        "country": country,
        "verified": bool(el.select_one('[data-hook="avp-badge"], [data-hook="avp-badge-linkless"]')),
        "variant": variant,
        "helpful_votes": helpful_n,
    }


def parse_reviews(soup) -> list[dict]:
    seen, out = set(), []
    for el in soup.select('[data-hook="review"]'):
        r = parse_review(el)
        if r["review_id"] and r["review_id"] not in seen:
            seen.add(r["review_id"])
            out.append(r)
    return out


def parse_histogram(soup) -> dict[int, int]:
    """{5: 52, 4: 13, ...} percentages as displayed (rounded)."""
    hist: dict[int, int] = {}
    for el in soup.select("#histogramTable [aria-label]"):
        m = re.match(r"(\d+) percent of reviews have (\d) stars?", el.get("aria-label", ""))
        if m:
            hist[int(m.group(2))] = int(m.group(1))
    return hist


def parse_product(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    avg = _num(_text(_first(soup, '[data-hook="rating-out-of-text"]', "#acrPopover span.a-icon-alt")))
    total = _num(_text(_first(soup, '[data-hook="total-review-count"]', "#acrCustomerReviewText")))
    parents = set(re.findall(r'"parentAsin"\s*:\s*"(\w+)"', html))
    return {
        "title": _text(soup.select_one("#productTitle")),
        "parent_asin": parents.pop() if len(parents) == 1 else None,  # ambiguous -> unknown
        "avg_rating": avg,
        "total_ratings": int(total) if total is not None else None,
        "hist_pct": parse_histogram(soup),
        "reviews": parse_reviews(soup),
    }


_NO_REVIEWS = re.compile(r"no customer reviews|there are 0 customer reviews|0 global ratings|no reviews yet", re.I)


def parse_review_page(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    empty_marker = bool(_NO_REVIEWS.search(soup.get_text(" ", strip=True)))
    nxt = soup.select_one("li.a-last")
    has_next = bool(nxt and "a-disabled" not in (nxt.get("class") or []) and nxt.select_one("a")) or \
        bool(soup.select_one('[data-hook="show-more-button"]'))
    filter_info = _text(soup.select_one('[data-hook="cr-filter-info-review-rating-count"]'))
    return {"reviews": parse_reviews(soup), "has_next": has_next, "filter_info": filter_info,
            "empty_marker": empty_marker}
