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
    # (None, 0): a bot check or a new layout, which stops the search without marking the page done
    assert lf.parse_properstar("<html><body>Just a moment...</body></html>", "chile") == (None, 0)
    assert lf.parse_properstar("<script>window.__INITIAL_STATE__ = {\"x\": 1};</script>", "chile") == (None, 0)


def test_landwatch():
    recs, _ = lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")
    assert len(recs) == 4
    assert {r["kind"] for r in recs} == {"land"}
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
    # no listing data at all: a bot check or an error page, not the end of the results (recs None, so the crawl
    # marks the page "fetch failed" and a rerun retries it)
    assert lf.parse_landwatch("<html><body>No results</body></html>", "ohio") == (None, None)


def test_landwatch_full_description():
    d = lf.landwatch_full_description(fixture_html("landwatch_detail.html"))
    assert d.startswith("With owner financing available for just $47/month")
    assert "Cash Price: $1,450" in d
    assert ",," not in d and "\r" not in d.replace("\r,", "")  # the ',,' paragraph separators are removed


def test_landwatch_full_description_missing():
    assert lf.landwatch_full_description("<html></html>") is None


def test_kijiji():
    recs, _ = lf.parse_kijiji(fixture_html("kijiji_search.html"), "nova-scotia")
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
    assert len(lf.parse_kijiji(html, "nova-scotia")[0]) == 5


def test_kijiji_without_next_data():
    assert lf.parse_kijiji("<html><body></body></html>", "ontario") == (None, None)  # blocked page, not an empty one


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
    ], 705), "france", "houses")
    assert total == 705
    assert [r["kind"] for r in recs] == ["houses"] * 3
    assert recs[0]["title"] == "Castle: Home for sale in Ravières, France"
    assert recs[1]["title"] == "Château with moat and park"
    assert recs[2]["title"] == "Home for sale in Nice, France"
    assert recs[0]["acres"] == pytest.approx(400 / lf.ACRE_M2)


def test_properstar_urls():
    assert lf.properstar_url("france", 1, "castles") == \
        "https://www.properstar.com/france/buy/house/castle/price-low-to-high"
    assert lf.properstar_url("france", 3, "castles").endswith("/buy/house/castle/price-low-to-high?p=3")
    assert lf.properstar_url("paraguay", 2) == \
        "https://www.properstar.com/paraguay/buy/land-plot/price-low-to-high?price.min=%d&p=2" % lf.MIN_PRICE
    assert lf.properstar_url("paraguay", 1, "houses") == \
        "https://www.properstar.com/paraguay/buy/house/price-low-to-high?price.min=%d" % lf.MIN_PRICE


def test_homes_land():
    recs = lf.parse_homes(fixture_html("homes_search.html"), "hokuriku/nagano", "land")
    assert len(recs) == 2  # 応相談 (price on request) is skipped
    r = recs[0]
    assert (r["source"], r["country"], r["currency"], r["kind"]) == ("homes", "JP", "JPY", "land")
    assert r["price"] == 2_000_000
    assert r["acres"] == pytest.approx(138.13 / lf.ACRE_M2)
    assert r["title"] == "Land: 本町通り"
    assert r["region"] == "長野県 木曽郡上松町"
    assert "JR上松駅" in r["description"] and "Point" not in r["description"]
    assert r["url"] == "https://www.homes.co.jp/akiyabank/b-38871/"
    assert recs[1]["price"] == 12_005_000
    assert recs[1]["acres"] == pytest.approx(300 * 3.305785 / lf.ACRE_M2)  # tsubo
    assert recs[1]["url"] == "https://www.homes.co.jp/akiyabank/b-30666/"


def test_homes_house_kind_and_empty_or_blocked_pages():
    assert {r["kind"] for r in lf.parse_homes(fixture_html("homes_search.html"), "kyushu/oita", "houses")} == {"houses"}
    assert lf.parse_homes("<title>【ホームズ】空き家バンク</title><p>0件</p>", "tohoku/akita") == []
    assert lf.parse_homes("<title>Access Denied</title>", "tohoku/akita") is None


@pytest.mark.parametrize("text, value", [
    ("200万円", 2_000_000), (" 200.52万円 ", 2_005_200), ("1億2,000万円", 120_000_000), ("35,000円", 35_000),
    ("0円", 0), ("応相談", None), ("", None), (None, None),
])
def test_yen(text, value):
    assert lf.yen(text) == value


def test_homes_url():
    assert lf.homes_url("hokuriku/nagano", 1) == \
        "https://www.homes.co.jp/akiyabank/hokuriku/nagano/?sort=price_asc&categories%5B%5D=102"
    assert lf.homes_url("kyushu/oita", 3, "houses").endswith("/kyushu/oita/?sort=price_asc&categories%5B%5D=101&page=3")
    assert len(lf.HOMES_PREFECTURES) == 47


def realtor_fixture():
    return json.loads(fixture_html("realtor_search.json"))


def test_realtor():
    recs, total = lf.parse_realtor(realtor_fixture(), "quebec")
    assert total == 12828  # how many vacant-land listings the API counts in the Ontario+Quebec box
    assert len(recs) == 4  # the two Ontario lots are outside Quebec, even though the box reaches them
    r = recs[0]
    assert r["source"] == "realtor"
    assert r["country"] == "CA"
    assert (r["price"], r["currency"]) == (3000.0, "CAD")
    assert r["title"] == "Ch. Rodgers"
    assert r["region"] == "Gore, QC, J0V1K0"  # town, province code, postal code: the shape Kijiji's REALTOR.ca ads have
    assert lf.town(r) == ("Gore", "QC")
    assert r["lat"] == pytest.approx(45.75017206)
    assert r["url"] == "https://www.realtor.ca/real-estate/29070840/ch-rodgers-gore-lac-mackay-sector"[:len(r["url"])]
    assert r["url"].startswith("https://www.realtor.ca/real-estate/29070840/")
    assert "MLS® 26872738" in r["description"]
    assert r["acres"] == pytest.approx(56101298 / 43560.0)  # "56101298 sqft|Unknown"
    assert recs[1]["acres"] == pytest.approx(970.6 / lf.ACRE_M2)  # "970.6 m2"


def test_realtor_ontario_side_of_the_box():
    recs, _ = lf.parse_realtor(realtor_fixture(), "ontario")
    assert [r["price"] for r in recs] == [1500.0, 5000.0]
    assert recs[0]["acres"] == 10.0  # "10 - 49.99 acres": the lower bound
    assert recs[1]["acres"] == pytest.approx(60 * 100 / 43560.0)  # "60 x 100 FT|under 1/2 acre": the lot's dimensions
    assert recs[1]["region"] == "Manitouwadge, ON, P0T2C0"


def test_realtor_not_a_results_page():
    assert lf.parse_realtor({"ErrorCode": {"Id": 500}}, "ontario") == (None, None)
    assert lf.parse_realtor(None, "ontario") == (None, None)


def test_realtor_acres():
    assert lf.realtor_acres({"SizeTotal": "30 x 40 M"}) == pytest.approx(1200 / lf.ACRE_M2)
    assert lf.realtor_acres({"SizeTotal": "under 1/2 acre"}) == 0.5
    assert lf.realtor_acres({"SizeTotal": "Unknown"}) is None
    assert lf.realtor_acres({}) is None
