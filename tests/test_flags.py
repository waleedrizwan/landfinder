"""Sanity flags that keep monthly payments, leases, placeholders and per-m2 prices out of the ranking."""
import pytest

import landfinder as lf
from conftest import fixture_html


def rec(**kw):
    base = {"source": "properstar", "country": "PL", "title": "Land plot", "description": "",
            "price": 20000, "currency": "USD", "acres": 1.0, "url": "https://example.com/1"}
    base.update(kw)
    return base


def flags_for(rates, **kw):
    return lf.finalize(rec(**kw), rates)["flags"]


def test_clean_listing_has_no_flags(rates):
    r = lf.finalize(rec(price=18000, currency="EUR", acres=2.0), rates)
    assert r["flags"] == ""
    assert r["price_usd"] == pytest.approx(20000)
    assert r["usd_per_acre"] == pytest.approx(10000)


def test_no_area(rates):
    r = lf.finalize(rec(acres=None), rates)
    assert r["flags"] == "no land area listed"
    assert r["usd_per_acre"] is None


@pytest.mark.parametrize("kw, expected", [
    ({"price": 250}, "price too low"),
    ({"price": 1800, "description": "Only $99/mo with $199 down"}, "may be payment plan price"),
    ({"title": "Gold mining claims, 20 acres"}, "mining claim"),
    ({"url": "https://www.landwatch.com/x-homes-for-sale/pid/1", "source": "landwatch"}, "house listing"),
    ({"source": "kijiji", "currency": "CAD", "price": 2800}, "price too low for Canada"),
    ({"price": 1200}, "agency listing under $1,500"),
    ({"title": "Farmland for lease"}, "lease/rental"),
    ({"price": 77777}, "placeholder price"),
    ({"title": "Lote 500 m2, precio por m2"}, "per m2/acre/hectare"),
    ({"description": "Sold at auction, starting bid $5,000"}, "auction/tender"),
    ({"title": "WANTED: 5 acres near Calgary"}, "wanted ad"),
])
def test_flags(rates, kw, expected):
    assert expected in flags_for(rates, **kw)


def test_akiya_houses_are_not_flagged(rates):
    r = lf.finalize(rec(kind="house", source="homes", currency="JPY", price=300000, acres=0.05,
                        url="https://www.homes.co.jp/akiyabank/b-1/"), rates)
    assert r["flags"] == ""


def test_no_per_acre_flag(rates):
    # listings are ranked by total price, so a very low price per acre (often a wrong area) isn't a warning
    assert flags_for(rates, price=4000, acres=1000.0) == ""


def test_kijiji_reparses_area_from_text(rates):
    r = lf.finalize(rec(source="kijiji", currency="CAD", price=30000, acres=None, title="Wooded lot, 2.5 acres"), rates)
    assert r["acres"] == 2.5


# --- down payments and monthly payments entered as the price (LandWatch) ---

def test_down_payment_swapped_for_cash_price(rates):
    r = lf.finalize(rec(source="landwatch", price=380, acres=1.0,
                        description="Only $2,700 cash! Or $380 down and $99/mo."), rates)
    assert r["price"] == 2700
    assert r["listed_price"] == 380
    assert "listed price is the down payment" not in r["flags"]


def test_down_payment_without_cash_price_is_flagged(rates):
    r = lf.finalize(rec(source="landwatch", price=500, acres=1.0,
                        description="Owner financing. $500 down, call for details."), rates)
    assert r["price"] == 500
    assert "listed price is the down payment" in r["flags"]


def test_down_payment_fix_only_for_landwatch(rates):
    r = lf.finalize(rec(source="properstar", price=380, description="Only $2,700 cash! Or $380 down."), rates)
    assert r["price"] == 380


def test_real_monthly_price_listing_is_never_shown_at_the_monthly_price(rates):
    """A real LandWatch card priced at $47 that is really '$47/mo'. Re-read with its full description
    (as verify_cheapest does), it must stay flagged so it never ranks as $47 land."""
    card = lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")[0]
    assert card["price"] == 47
    card["description"] = lf.landwatch_full_description(fixture_html("landwatch_detail.html"))
    r = lf.finalize(card, rates)
    assert r["flags"] not in ("", "no land area listed")
    assert lf.site_codes(r["flags"])


# --- flag text -> one-letter codes used by the website ---

@pytest.mark.parametrize("flags, codes", [
    ("", ""),
    (None, ""),
    ("no land area listed", "N"),
    ("lease/rental - not a sale price; no land area listed", "NR"),
    ("price may be per m2/acre/hectare", "U"),
    ("price may be per m2 (would buy under 20 m2 at local prices)", "U"),
    ("placeholder price - same price on many different-size plots", "X"),
    ("may be payment plan price - listed price is the down payment", "P"),
    ("wanted ad - not a listing", "W"),
])
def test_site_codes(flags, codes):
    assert lf.site_codes(flags) == codes


def test_site_codes_are_unique(rates):
    # a $1 Kijiji ad hits both 'price too low' and 'price too low for Canada': one L, shown once
    r = lf.finalize(rec(source="kijiji", currency="CAD", price=1, acres=None), rates)
    codes = lf.site_codes(r["flags"])
    assert codes.count("L") == 1
    assert len(codes) == len(set(codes))


def test_every_flag_text_has_a_site_code(rates):
    """Each warning flag() can produce must map to a code, or the site shows that listing as clean."""
    cases = [{"price": 250}, {"price": 1800, "description": "$99/mo"}, {"title": "mining claim"},
             {"url": "x-homes-for-sale-y"}, {"source": "kijiji", "currency": "CAD", "price": 2800},
             {"price": 1200}, {"title": "for lease"}, {"price": 77777}, {"title": "precio por m2"},
             {"title": "auction"}, {"title": "wanted: land"}, {"acres": None}, {"price": 4000, "acres": 1000.0}]
    extra = ["placeholder price - same price on many different-size plots",
             "price may be per m2 (would buy under 20 m2 at local prices)",
             "may be payment plan price - listed price is the down payment"]
    texts = extra[:]
    for kw in cases:
        texts += [t for t in flags_for(rates, **kw).split("; ") if t]
    for t in texts:
        assert lf.site_codes(t), "no site code for flag %r" % t
