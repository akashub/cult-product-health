from pathlib import Path

import pytest

from cultph.amazon import store
from cultph.amazon.checks import check_product, check_reviews, implied_gap
from cultph.amazon.parse import detect_block, parse_product, parse_review_page
from cultph.amazon.rating import avg_range, effective_target, mean_range, five_stars_needed, one_stars_absorbable, plan, required_share_of_five

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


def test_displayed_rating_narrows_range():
    """Real case: shows 4.0 with histogram 55/22/9/3/11 (mean 4.04-4.10). The true
    mean must be < 4.05, so it needs a few 5-star ratings and can absorb no 1-stars."""
    hist = {5: 55, 4: 22, 3: 9, 2: 3, 1: 11}
    lo, hi, ok = mean_range(hist, 4.0)
    assert ok and lo == pytest.approx(4.04) and hi < 4.05
    p = plan(687, hist, effective_target(4.1), shown=4.0)
    assert p["one_star_absorbable"] == (0, 0)
    assert p["five_star_needed"][0] >= 1 and p["five_star_needed"][1] <= 8
    # a listing shown as 4.1 whose histogram mean is 4.045-4.09 is consistent (round half up)
    assert mean_range({5: 64, 4: 0, 3: 15, 2: 21, 1: 0}, 4.1)[2]


def test_rounding_warning_on_mismatch():
    from cultph.amazon.checks import rounding_warning
    p = parse_product((FX / "amazon_product.html").read_text().replace("3.6 out of 5", "4.4 out of 5"))
    assert rounding_warning(p) is not None
    assert rounding_warning(parse_product((FX / "amazon_product.html").read_text())) is None


def test_pool_attribution(tmp_path):
    con = store.connect(tmp_path / "a.db")
    p = parse_product((FX / "amazon_product.html").read_text())
    for asin, prod in [("A1", "Gun A"), ("A2", "Gun B")]:  # two products share pool P1
        store.add_snapshot(con, asin, prod, {**p, "parent_asin": "P1"})
    store.add_snapshot(con, "A3", "Foot C", {**p, "parent_asin": "P3"})
    store.upsert_reviews(con, "A1", "Gun A", p["reviews"], "product_page", "P1")
    store.upsert_reviews(con, "A3", "Foot C", [{**p["reviews"][0], "review_id": "RX"}], "product_page", "P3")
    store.attribute_reviews(con, {"P1": {"Colour: Grey": "Gun B"}})
    got = dict((r[0], r[1:]) for r in con.execute("SELECT review_id, product, attribution FROM review"))
    assert got["RTEST0000001"] == ("Gun B", "variant")        # variant text mapped
    assert got["RTEST0000002"] == (None, "pool")              # shared pool, no variant -> not credited
    assert got["RX"] == ("Foot C", "single-product pool")


def test_ambiguous_parent_asin_is_none():
    html = (FX / "amazon_product.html").read_text()
    one = html + '<script>{"parentAsin":"P1"}{"parentAsin":"P1"}</script>'
    two = html + '<script>{"parentAsin":"P1"}{"parentAsin":"P2"}</script>'
    assert parse_product(one)["parent_asin"] == "P1"
    assert parse_product(two)["parent_asin"] is None


def test_listing_empty_marker():
    assert parse_review_page("<html><body>No customer reviews</body></html>")["empty_marker"]
    assert not parse_review_page("<html><body><div class='new-layout'>x</div></body></html>")["empty_marker"]


def test_auth_cookie_detection():
    from cultph.amazon.fetch import has_auth_cookie
    assert has_auth_cookie([{"name": "at-acbin", "value": "x"}])
    assert not has_auth_cookie([{"name": "session-id", "value": "x"}, {"name": "at-acbin", "value": ""}])


REAL_LISTING = Path(__file__).resolve().parents[1] / "data" / "raw" / "amz_listing_p1.html"


@pytest.mark.skipif(not REAL_LISTING.exists(), reason="no saved real signed-in listing page")
def test_real_saved_listing_page():
    rp = parse_review_page(REAL_LISTING.read_text())
    assert len(rp["reviews"]) == 10 and rp["has_next"] and not rp["empty_marker"]
    assert all(r["review_id"].startswith("R") and r["rating"] in range(1, 6) and r["review_date"] for r in rp["reviews"])
    assert all(r["title"] and r["body"] for r in rp["reviews"])
    assert not any(r["title"].startswith(("1.0 out of", "5.0 out of")) for r in rp["reviews"])
    assert check_reviews(rp["reviews"]) == []



def test_performance_signals_from_fixture_and_real_page(tmp_path):
    from cultph.amazon.parse import bought_estimate
    assert bought_estimate("3K+ bought in past month") == 3000
    assert bought_estimate("50+ bought in past month") == 50
    assert bought_estimate("1.5K+ bought") == 1500 and bought_estimate(None) is None
    html = (FX / "amazon_product.html").read_text().replace("</body>", """
      <div id="social-proofing-faceout-title-tk_bought">2K+ bought in past month</div>
      <span class="a-price"><span class="a-offscreen">₹1,199.00</span></span> <span>M.R.P.: ₹3,479</span>
      <div id="availability"> In stock. </div>
      <table><tr><th>Best Sellers Rank</th><td>#616 in Health &amp; Personal Care (See Top 100) #4 in Electric Handheld Massagers</td></tr></table>
    </body>""")
    perf = parse_product(html)["performance"]
    assert (perf["bought_min"], perf["price"], perf["mrp"], perf["availability"]) == (2000, 1199.0, 3479.0, "In stock")
    assert (perf["bsr_main"], perf["bsr_main_cat"], perf["bsr_sub"], perf["bsr_sub_cat"]) == \
        (616, "Health & Personal Care", 4, "Electric Handheld Massagers")
    con = store.connect(tmp_path / "a.db")
    store.add_snapshot(con, "A1", "Gun A", parse_product(html))
    assert con.execute("SELECT bought_min, bsr_sub, price FROM rating_snapshot").fetchone() == (2000, 4, 1199.0)



def test_boundary_rounding_is_consistent():
    """Live case: shown 3.5 with histogram 52/13/4/3/28 (mean 3.550-3.610)."""
    lo, hi, ok = mean_range({5: 52, 4: 13, 3: 4, 2: 3, 1: 28}, 3.5)
    assert ok and lo == hi == pytest.approx(3.55)
