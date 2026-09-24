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

    def get(self, url: str) -> tuple[str, str]:
        """Returns (final_url, html), pausing a random interval between requests."""
        wait = self._last + random.uniform(*self.delay) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self.page.wait_for_timeout(1500 + random.randint(0, 1500))
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


def interactive_login(domain: str) -> None:
    """Opens a visible browser so you can sign in once; the session is saved."""
    with browser(headless=False, delay=(0, 0)) as f:
        f.page.goto(f"https://www.{domain}/ap/signin?openid.return_to=https%3A%2F%2Fwww.{domain}%2F"
                    "&openid.identity=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
                    "&openid.assoc_handle=inflex&openid.mode=checkid_setup"
                    "&openid.claimed_id=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0%2Fidentifier_select"
                    "&openid.ns=http%3A%2F%2Fspecs.openid.net%2Fauth%2F2.0")
        input("Sign in in the browser window, then press Enter here to save the session... ")
