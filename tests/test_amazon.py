from pathlib import Path

import pytest

from cultph.amazon import store
from cultph.amazon.checks import check_product, implied_gap
from cultph.amazon.parse import detect_block, parse_product, parse_review_page
from cultph.amazon.rating import avg_range, effective_target, five_stars_needed, one_stars_absorbable, plan, required_share_of_five

FX = Path(__file__).parent / "fixtures"


def test_parse_product():
    p = parse_product((FX / "amazon_product.html").read_text())
    assert p["avg_rating"] == 3.6 and p["total_ratings"] == 1204
    assert p["hist_pct"] == {5: 52, 4: 13, 3: 4, 2: 3, 1: 28}
    assert p["parent_asin"] is None
    r1, r2 = p["reviews"]
    assert r1 == {"review_id": "RTEST0000001", "rating": 1, "title": "Stopped working",
                  "body": "Not charging after a week.", "review_date": "2026-09-01", "country": "India",
                  "verified": True, "variant": "Colour: Grey", "helpful_votes": 9}
    assert r2["helpful_votes"] == 1 and not r2["verified"]
    assert "Some Reviewer" not in str(p)  # reviewer names are never captured
    assert check_product(p) == []
    assert abs(implied_gap(p)) < 0.05


def test_parse_review_page():
    rp = parse_review_page((FX / "amazon_reviews_page.html").read_text())
    assert rp["has_next"] and rp["filter_info"].startswith("372 total ratings")
    assert rp["reviews"][0]["title"] == "Loud noise" and rp["reviews"][0]["rating"] == 2


def test_block_detection():
    assert detect_block("<html>ok</html>", "https://www.amazon.in/dp/X") is None
    assert detect_block("", "https://www.amazon.in/ap/signin?x") == "signin"
    assert detect_block("<form action='/errors/validateCaptcha'>", "") == "captcha"


def test_check_rejects_blocked_or_broken_page():
    empty = parse_product("<html><body>nothing</body></html>")
    assert check_product(empty)  # a login/captcha-like page must never pass as '0 ratings'
    bad = parse_product((FX / "amazon_product.html").read_text().replace("52 percent", "72 percent"))
    assert any("sums to" in e for e in check_product(bad))


def test_avg_range_brackets_weighted_mean():
    true_shares = {5: 51.6, 4: 13.4, 3: 3.8, 2: 2.9, 1: 28.3}  # sums to 100
    shown = {k: round(v) for k, v in true_shares.items()}
    a_min, a_max = avg_range(shown)
    assert a_min <= sum(k * v for k, v in true_shares.items()) / 100 <= a_max
    assert a_max - a_min < 0.07  # rounding of 5 shares moves the mean by at most ~0.03 each way


def test_small_n_weighted_histogram_is_accepted():
    """Real pages show e.g. N=6 with 64/0/15/21/0; that's weighted, not a bug."""
    p = parse_product((FX / "amazon_product.html").read_text()
                      .replace("1,204 global ratings", "6 global ratings"))
    assert check_product(p) == []


def test_five_stars_needed_and_absorbable():
    assert five_stars_needed(0, 0) == 0
    assert five_stars_needed(10, 4.1) == 0           # exactly at target
    assert five_stars_needed(10, 4.0) == 2           # (40+5k)/(10+k) >= 4.1 -> k >= 1.11
    assert (40 + 5 * 2) / 12 >= 4.1 and (40 + 5) / 11 < 4.1
    assert one_stars_absorbable(10, 4.5) == 1        # (45+1)/11 = 4.18 ok; (45+2)/12 = 3.92 not
    assert one_stars_absorbable(10, 4.0) == 0
    # all other ratings 4-star (avg 4.0): need 10% five-star for new ratings to average 4.1
    assert required_share_of_five({1: 0, 2: 0, 3: 0, 4: 100}) == pytest.approx(0.1)
    assert required_share_of_five({5: 100}) == 0.0


def test_plan_range_order():
    p = plan(1204, {5: 52, 4: 13, 3: 4, 2: 3, 1: 28})
    lo, hi = p["five_star_needed"]
    assert 0 < lo <= hi
    assert p["one_star_absorbable"] == (0, 0)


def test_store_dedupes_and_keeps_longest_body(tmp_path):
    con = store.connect(tmp_path / "a.db")
    p = parse_product((FX / "amazon_product.html").read_text())
    assert len(store.upsert_reviews(con, "A1", "Sample Gun A", p["reviews"], "product_page")) == 2
    rp = parse_review_page((FX / "amazon_reviews_page.html").read_text())
    new = store.upsert_reviews(con, "A1", "Sample Gun A", rp["reviews"], "listing:recent:all")
    assert new == ["RTEST0000003"]
    body = con.execute("SELECT body FROM review WHERE review_id='RTEST0000001'").fetchone()[0]
    assert body.startswith("Not charging after a week. Full text")
    assert con.execute("SELECT count(*) FROM review").fetchone()[0] == 3


REAL = Path(__file__).resolve().parents[1] / "data" / "raw" / "product.html"


@pytest.mark.skipif(not REAL.exists(), reason="no saved real product page")
def test_real_saved_product_page():
    assert check_product(parse_product(REAL.read_text())) == []


def test_effective_target():
    assert effective_target(4.1) == 4.05
    assert effective_target(4.1, "exact") == 4.1
