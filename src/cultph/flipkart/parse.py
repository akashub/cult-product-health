"""Flipkart parsers (no login needed).

Product page: schema.org JSON-LD gives rating value, rating count, review
count, and the reviews-page link. Reviews page (React Native Web, no stable
class names or review ids): parsed from its visible text lines, which follow
a fixed pattern. The header gives EXACT per-star counts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta

from bs4 import BeautifulSoup

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def detect_block(html: str, final_url: str = "") -> str | None:
    low = html.lower()
    if "/account/login" in final_url:
        return "signin"
    if "are you a human" in low or "recaptcha" in low and "ratings and" not in low:
        return "captcha"
    return None


def parse_product(html: str) -> dict:
    out = {"title": None, "avg_rating": None, "total_ratings": None, "total_reviews": None, "reviews_path": None}
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for item in data if isinstance(data, list) else [data]:
            if item.get("@type") == "Product":
                agg = item.get("aggregateRating") or {}
                out.update(title=item.get("name"), avg_rating=agg.get("ratingValue"),
                           total_ratings=agg.get("ratingCount"), total_reviews=agg.get("reviewCount"))
    m = re.search(r'href="(/[^"?]*?/product-reviews/itm[0-9a-z]+)\?pid=(\w+)((?:&amp;|&)lid=(\w+))?', html)
    if m:
        out["reviews_path"] = f"{m.group(1)}?pid={m.group(2)}" + (f"&lid={m.group(4)}" if m.group(4) else "")
        out["pid"] = m.group(2)
    return out


def _lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    return [ln.strip() for ln in soup.get_text("\n").split("\n") if ln.strip()]


def relative_date(text: str, today: date) -> tuple[str | None, str]:
    """'· 3 days ago' -> (iso date, 'day'); 'Aug, 2025' -> (2025-08-01, 'month')."""
    t = text.lstrip("·").strip().lower()
    if t in ("today", "just now") or "hour" in t or "minute" in t:
        return today.isoformat(), "day"
    if t == "yesterday":
        return (today - timedelta(days=1)).isoformat(), "day"
    m = re.match(r"(\d+|a|an)\s+(day|week|month|year)s?\s+ago", t)
    if m:
        n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(2)] * n
        return (today - timedelta(days=days)).isoformat(), m.group(2)
    m = re.match(r"([a-z]{3})\w*,?\s+(\d{4})", t)
    if m and m.group(1) in MONTHS:
        return date(int(m.group(2)), MONTHS[m.group(1)], 1).isoformat(), "month"
    return None, "unknown"


_RATING = re.compile(r"^[1-5]\.0$")
_HEADER = re.compile(r"^([\d,]+) ratings? and ([\d,]+) reviews?$")


def parse_reviews_page(html: str, pid: str, today: date | None = None) -> dict:
    today = today or date.today()
    lines = _lines(html)
    out = {"total_ratings": None, "total_reviews": None, "star_counts": {}, "reviews": []}
    for i, ln in enumerate(lines):
        m = _HEADER.match(ln)
        if m:
            out["total_ratings"], out["total_reviews"] = (int(g.replace(",", "")) for g in m.groups())
            j = i + 1
            while j + 2 < len(lines) and lines[j + 1] == "★" and lines[j].isdigit():
                out["star_counts"][int(lines[j])] = int(lines[j + 2].replace(",", ""))
                j += 3
            break
    out["duplicates"] = 0
    seen: set[str] = set()
    starts = [i for i in range(len(lines) - 1) if _RATING.match(lines[i]) and lines[i + 1] == "•"]
    for k, s in enumerate(starts):
        block = lines[s: starts[k + 1] if k + 1 < len(starts) else len(lines)]
        date_idx = next((i for i, ln in enumerate(block) if ln.startswith("·")), None)
        helpful_idx = next((i for i, ln in enumerate(block) if ln.startswith("Helpful")), None)
        if date_idx is None or helpful_idx is None:
            continue
        rating, title = int(float(block[0])), block[2]
        pos = 3
        variant = None
        if pos < len(block) and block[pos].startswith("Review for:"):
            variant = block[pos].split(":", 1)[1].strip()
            pos += 1
        city_idx = next((i for i in range(pos, helpful_idx) if block[i].startswith(", ")), None)
        author_idx = (city_idx - 1) if city_idx is not None else helpful_idx - 1
        body = "\n".join(block[pos:author_idx]).strip() or None
        author = block[author_idx]
        city = block[city_idx][2:].strip() if city_idx is not None else None
        hm = re.search(r"(\d+)", block[helpful_idx])
        rdate, precision = relative_date(block[date_idx], today)
        rid = "FK" + hashlib.sha1(f"{pid}|{author}|{city}|{title}|{body}".encode()).hexdigest()[:14]
        if rid in seen:  # Flipkart sometimes lists the identical review several times
            out["duplicates"] += 1
            continue
        seen.add(rid)
        out["reviews"].append({
            "review_id": rid, "rating": rating, "title": title, "body": body, "review_date": rdate,
            "country": "India", "verified": "Verified Purchase" in block or "Certified Buyer" in block,
            "variant": variant, "helpful_votes": int(hm.group(1)) if hm else 0, "date_precision": precision,
        })
    return out


def check_reviews_page(p: dict) -> list[str]:
    errs = []
    n, counts = p["total_ratings"], p["star_counts"]
    if n is None:
        errs.append("ratings header not found (layout changed?)")
    elif sorted(counts) != [1, 2, 3, 4, 5]:
        errs.append(f"star counts incomplete: {counts}")
    elif sum(counts.values()) != n:
        errs.append(f"star counts sum {sum(counts.values())} != {n} ratings")
    if p["total_reviews"] and not p["reviews"]:
        errs.append(f"header says {p['total_reviews']} reviews but none parsed (layout changed?)")
    return errs
