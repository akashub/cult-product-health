"""Playwright fetcher. Only fetches; parsing lives in parse.py.

A persistent browser profile under data/amazon_profile keeps the Amazon login
(needed for the full review listing). Use a secondary Amazon account."""

from __future__ import annotations

import random
import time
from contextlib import contextmanager

from ..config import DATA_DIR

PROFILE_DIR = DATA_DIR / "amazon_profile"
RAW_DIR = DATA_DIR / "raw"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


class Fetcher:
    def __init__(self, page, delay: tuple[float, float]):
        self.page = page
        self.delay = delay
        self._last = 0.0

    def get(self, url: str, wait_for_text: str | None = None, scroll: bool = False) -> tuple[str, str]:
        """Returns (final_url, html), pausing a random interval between requests.
        wait_for_text: wait up to 12 s for client-rendered content containing it."""
        wait = self._last + random.uniform(*self.delay) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self.page.wait_for_timeout(1500 + random.randint(0, 1500))
        if scroll:
            self.page.mouse.wheel(0, 3000)
            self.page.wait_for_timeout(1000)
        if wait_for_text:
            try:
                self.page.wait_for_function("t => document.body && document.body.innerText.includes(t)",
                                            arg=wait_for_text, timeout=12_000)
            except Exception:  # noqa: BLE001 - page may legitimately lack it (no reviews)
                pass
        self._last = time.monotonic()
        return self.page.url, self.page.content()


@contextmanager
def browser(headless: bool = True, delay: tuple[float, float] = (4.0, 9.0)):
    from playwright.sync_api import sync_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=headless, locale="en-IN", user_agent=UA,
            viewport={"width": 1366, "height": 900})
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            yield Fetcher(page, delay)
        finally:
            ctx.close()


AUTH_COOKIES = {"at-acbin", "sess-at-acbin", "at-main", "sess-at-main"}


def has_auth_cookie(cookies: list[dict]) -> bool:
    return any(c.get("name") in AUTH_COOKIES and c.get("value") for c in cookies)


def interactive_login(domain: str, timeout_s: int = 600) -> bool:
    """Opens a visible browser on Amazon's sign-in page and waits until Amazon
    sets its sign-in cookie (or the window is closed). No terminal input needed,
    so it also works from scripts. Returns True when signed in."""
    signin = (f"https://www.{domain}/ap/signin?openid.return_to=https%3A%2F%2Fwww.{domain}%2F"
              "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
              "&openid.assoc_handle=inflex&openid.mode=checkid_setup"
              "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
              "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0")
    with browser(headless=False, delay=(0, 0)) as f:
        ctx = f.page.context
        if has_auth_cookie(ctx.cookies()):
            print("Already signed in; session is saved.")
            return True
        f.page.goto(signin)
        print("Sign in in the browser window (use a secondary account). It closes by itself once you're in.")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                if has_auth_cookie(ctx.cookies()):
                    f.page.wait_for_timeout(2000)  # let Amazon finish setting cookies
                    print("Signed in. Session saved under data/amazon_profile.")
                    return True
                f.page.wait_for_timeout(1000)
            except Exception:  # noqa: BLE001 - window closed by the user
                break
        print("Not signed in (window closed or timed out).")
        return False


def check_session(domain: str, asin: str) -> str:
    """'ok' if the review listing opens, 'signin' if the session is missing or
    expired, 'captcha' if Amazon is challenging us."""
    from .parse import detect_block

    with browser(headless=True, delay=(0, 0)) as f:
        final, html = f.get(f"https://www.{domain}/product-reviews/{asin}?sortBy=recent&pageNumber=1")
    return detect_block(html, final) or "ok"
