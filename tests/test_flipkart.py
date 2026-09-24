from datetime import date
from pathlib import Path

from cultph.amazon import store
from cultph.amazon.rating import five_stars_needed
from cultph.flipkart.parse import check_reviews_page, parse_product, parse_reviews_page, relative_date

FX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 9, 24)


def test_product_jsonld_and_reviews_link():
    p = parse_product((FX / "flipkart_product.html").read_text())
    assert (p["avg_rating"], p["total_ratings"], p["total_reviews"]) == (4.2, 20, 3)
    assert p["reviews_path"] == "/sample-gun-a/product-reviews/itm0000000000000?pid=PIDTEST000000001&lid=LSTPIDTEST"
    no_lid = parse_product('<a href="/x/product-reviews/itmabc?pid=P1&amp;aid=z">r</a>')
    assert no_lid["reviews_path"] == "/x/product-reviews/itmabc?pid=P1"


def test_reviews_page_exact_counts_and_reviews():
    r = parse_reviews_page((FX / "flipkart_reviews.html").read_text(), "PIDTEST000000001", TODAY)
    assert r["star_counts"] == {1: 2, 2: 1, 3: 2, 4: 5, 5: 10} and r["total_ratings"] == 20
    a, b, c = r["reviews"]
    assert (a["rating"], a["title"], a["variant"], a["helpful_votes"], a["review_date"]) == \
        (1, "Worst", "Color Grey", 4, "2026-09-21")
    assert a["body"] == "Stopped charging after a week.\nAlso very noisy."
    assert b["variant"] is None and b["date_precision"] == "month" and b["review_date"] == "2026-07-26"
    assert c["body"] == "Nice" and not c["verified"] and c["review_date"] == "2025-08-01"
    assert "Some Buyer" not in str(r)  # reviewer names only feed the id hash
    assert check_reviews_page(r) == []
    assert r["reviews"][0]["review_id"] == parse_reviews_page(
        (FX / "flipkart_reviews.html").read_text(), "PIDTEST000000001", date(2026, 10, 1))["reviews"][0]["review_id"]


def test_reviews_page_checks_catch_layout_change():
    html = (FX / "flipkart_reviews.html").read_text()
    assert any("sum" in e for e in check_reviews_page(parse_reviews_page(html.replace("<div>10</div>", "<div>11</div>"), "P")))
    assert any("none parsed" in e for e in check_reviews_page(parse_reviews_page(html.replace("•", "-"), "P")))
    assert any("header" in e for e in check_reviews_page(parse_reviews_page("<html>new layout</html>", "P")))


def test_relative_dates():
    assert relative_date("· Today", TODAY) == ("2026-09-24", "day")
    assert relative_date("· a month ago", TODAY) == ("2026-08-25", "month")
    assert relative_date("· 1 year ago", TODAY)[1] == "year"
    assert relative_date("· Jan, 2024", TODAY) == ("2024-01-01", "month")
    assert relative_date("· someday", TODAY) == (None, "unknown")


def test_count_snapshot_and_exact_math(tmp_path):
    con = store.connect(tmp_path / "a.db")
    store.add_count_snapshot(con, "P1", "Gun A", "flipkart", 4.2, {1: 2, 2: 1, 3: 2, 4: 5, 5: 10})
    avg_min, avg_max, n, plat = con.execute(
        "SELECT hist_avg_min, hist_avg_max, total_ratings, platform FROM rating_snapshot").fetchone()
    assert avg_min == avg_max == 80 / 20 and n == 20 and plat == "flipkart"   # 2+2+6+20+50 = 80 stars
    # (80 + 5k) / (20 + k) >= 4.05 -> k >= 1.05 -> 2 ; >= 4.1 -> k >= 2.22 -> 3
    assert five_stars_needed(20, 4.0, 4.05) == 2 and five_stars_needed(20, 4.0, 4.1) == 3


def test_repeated_identical_review_is_deduplicated_not_failed():
    html = (FX / "flipkart_reviews.html").read_text()
    block = html[html.index("<div>1.0</div>"):html.index("<div>5.0</div><div>•</div>")]
    r = parse_reviews_page(html.replace(block, block * 3), "P", TODAY)
    assert len(r["reviews"]) == 3 and r["duplicates"] == 2 and check_reviews_page(r) == []
