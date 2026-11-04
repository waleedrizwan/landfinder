"""Per-source parsers, run against trimmed copies of real search pages (tests/fixtures/)."""
import json

import pytest

import landfinder as lf
from conftest import fixture_html


def test_properstar():
    recs, total = lf.parse_properstar(fixture_html("properstar_search.html"), "paraguay")
    assert total == 1249
    assert len(recs) == 3  # the 4th listing hides its price, so it is skipped
    first = recs[0]
    assert first["source"] == "properstar"
    assert first["country"] == "PY"
    assert first["region"] == "Itacurubí del Rosario"
    assert first["title"] == "LAND FOR SALE - ITACURUBI DEL ROSARIO / SAN PEDRO"
    assert (first["price"], first["currency"]) == (3450, "USD")
    assert first["acres"] == pytest.approx(452 / lf.ACRE_M2)
    assert first["lat"] == pytest.approx(-24.533991)
    assert first["url"] == "https://www.properstar.com/listing/120489290"
    assert [(r["price"], r["currency"]) for r in recs[1:]] == [(33000000, "PYG"), (40000000, "PYG")]


def test_properstar_without_state():
    assert lf.parse_properstar("<html><body>Just a moment...</body></html>", "chile") == ([], 0)
    assert lf.parse_properstar("<script>window.__INITIAL_STATE__ = {\"x\": 1};</script>", "chile") == ([], 0)


def test_landwatch():
    recs = lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")
    assert len(recs) == 4
    r = recs[1]
    assert r["source"] == "landwatch"
    assert (r["country"], r["region"]) == ("US", "New Mexico")
    assert (r["price"], r["currency"], r["acres"]) == (749.0, "USD", 0.5)
    assert r["title"] == "La Susida Rd, Deming, NM, 88030, Luna County"
    assert r["url"] == "https://www.landwatch.com/luna-county-new-mexico-undeveloped-land-for-sale/pid/428586497"
    assert recs[0]["price"] == 47.0
    assert "$47/month" in recs[0]["description"]
    assert all(r["url"].startswith("https://www.landwatch.com/") and "/pid/" in r["url"] for r in recs)


def test_landwatch_empty_page():
    assert lf.parse_landwatch("<html><body>No results</body></html>", "ohio") == []


def test_landwatch_full_description():
    d = lf.landwatch_full_description(fixture_html("landwatch_detail.html"))
    assert d.startswith("With owner financing available for just $47/month")
    assert "Cash Price: $1,450" in d
    assert ",," not in d and "\r" not in d.replace("\r,", "")  # the ',,' paragraph separators are removed


def test_landwatch_full_description_missing():
    assert lf.landwatch_full_description("<html></html>") is None


def test_kijiji():
    recs = lf.parse_kijiji(fixture_html("kijiji_search.html"), "nova-scotia")
    assert len(recs) == 6
    r = recs[1]
    assert r["source"] == "kijiji"
    assert r["country"] == "CA"
    assert (r["price"], r["currency"]) == (5000.0, "CAD")  # Kijiji amounts are in cents
    assert r["region"] == "Lot 276 Yorke Street, Sydney Mines, NS, B1V3C8"
    assert r["lat"] == pytest.approx(46.23118)
    assert r["url"].startswith("https://www.kijiji.ca/v-land-for-sale/")
    assert [x["acres"] for x in recs[4:]] == [6.0, 6.0]  # read from the 'areainacres' attribute


def test_kijiji_skips_non_fixed_prices():
    html = fixture_html("kijiji_search.html").replace('"type": "FIXED"', '"type": "CONTACT"', 1)
    assert len(lf.parse_kijiji(html, "nova-scotia")) == 5


def test_kijiji_without_next_data():
    assert lf.parse_kijiji("<html><body></body></html>", "ontario") == []


def castle_page(listings, total):
    state = {"search": {"results": {"total": total, "listings": listings}}}
    return "<script>window.__INITIAL_STATE__ = %s;</script>" % json.dumps(state)


def castle_listing(i, title_en=None, auto="Home for sale in Ravières, France", sub="Castle"):
    return {"id": i, "subType": {"id": sub, "name": sub}, "automaticTitle": auto,
            "title": [{"language": "en", "text": title_en}] if title_en else [],
            "price": {"show": True, "values": [{"type": "Original", "value": 105000, "currencyId": "EUR"}]},
            "area": {"land": 400, "unit": {"id": "SquareMeter"}},
            "location": {"countryISO": "FR", "city": "Ravières"}}


def test_properstar_castles():
    recs, total = lf.parse_properstar(castle_page([
        castle_listing(1),                                         # generic title -> labelled
        castle_listing(2, title_en="Château with moat and park"),  # already says so -> left alone
        castle_listing(3, sub="Villa", auto="Home for sale in Nice, France"),
    ], 705), "france")
    assert total == 705
    assert [r["kind"] for r in recs] == ["castle", "castle", None]
    assert recs[0]["title"] == "Castle: Home for sale in Ravières, France"
    assert recs[1]["title"] == "Château with moat and park"
    assert recs[2]["title"] == "Home for sale in Nice, France"
    assert recs[0]["acres"] == pytest.approx(400 / lf.ACRE_M2)


def test_properstar_urls():
    assert lf.properstar_url("france", 1, castles=True) == \
        "https://www.properstar.com/france/buy/house/castle/price-low-to-high"
    assert lf.properstar_url("france", 3, castles=True).endswith("/buy/house/castle/price-low-to-high?p=3")
    assert lf.properstar_url("paraguay", 2) == \
        "https://www.properstar.com/paraguay/buy/land-plot/price-low-to-high?price.min=%d&p=2" % lf.MIN_PRICE
