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


@pytest.mark.parametrize("source", ["homes", "ieichiba"])
def test_akiya_houses_are_not_flagged(rates, source):
    # on the akiya sites the empty house is what you buy, so it ranks with the land, even at ¥0
    r = lf.finalize(rec(kind="houses", source=source, currency="JPY", price=0, acres=0.05,
                        url="https://www.homes.co.jp/akiyabank/b-1/"), rates)
    assert r["flags"] == ""
    assert r["type"] == "house"


def test_other_house_sections_are_flagged(rates):
    r = lf.finalize(rec(kind="houses", source="olx", price=20000), rates)
    assert "house listing" in r["flags"]
    assert lf.believable_flags(r["flags"])  # shown when you pick House on the site


def test_very_low_price_per_acre_is_checked(rates):
    # $4/acre is usually a wrong area; listings that already have a warning don't get this one too
    assert flags_for(rates, price=4000, acres=1000.0) == "check: under $10/acre"
    assert "under $10/acre" not in flags_for(rates, price=4000, acres=1000.0, title="for lease")


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
    card = lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")[0][0]
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
             {"title": "auction"}, {"title": "wanted: land"}, {"acres": None}, {"price": 4000, "acres": 1000.0},
             {"title": "1/2 share of a farm"}, {"title": "Cemetery plots for sale"},
             {"source": "olx", "country": "PL", "title": "Działka ROD", "currency": "PLN", "price": 30000},
             {"title": "Lote Usd 880.000", "currency": "ARS", "price": 880000},
             {"kind": "houses", "source": "olx"}]
    extra = ["placeholder price - same price on many different-size plots",
             "price may be per m2 (would buy under 20 m2 at local prices)",
             "far below other listings in this country",
             "may be payment plan price - listed price is the down payment"]
    texts = extra[:]
    for kw in cases:
        texts += [t for t in flags_for(rates, **kw).split("; ") if t]
    for t in texts:
        assert lf.site_codes(t), "no site code for flag %r" % t


# ---- found by auditing the listings the site showed on 2026-11-10

@pytest.mark.parametrize("kw, expected", [
    ({"title": "будинок в Гостомелі", "description": "Здається будинок дуплекс"}, "lease/rental"),
    ({"description": "За Нова година се отдава къща за гости"}, "lease/rental"),
    ({"title": "HOUSE IN ANTICRETICO AREA 3 STEPS IN FRONT"}, "lease/rental"),
    ({"description": "Perfect for office users. $17.50/sf/yr, NNN"}, "lease/rental"),
    ({"description": "1975 mini home. Lot rent $148.86."}, "lease/rental"),
    ({"description": "Chalet rustique situé sur un terrain loué au bord du lac"}, "lease/rental"),
    ({"description": "1/4 share of a deluxe ground floor suite at the resort"}, "share of a property"),
    ({"description": "Fractional Ownership cottage on Shadow Lake"}, "share of a property"),
    ({"description": "This is a one week time share, week 50 each year"}, "share of a property"),
    ({"title": "Investment Opportunity – 50% Undivided Ownership in Land Plot"}, "share of a property"),
    ({"title": "2 bedroom house with a life annuity"}, "life annuity"),
    ({"description": "14 x 54 mini home to be moved"}, "building only"),
    ({"title": "Domek holenderski 8 × 3 m"}, "building only"),
    ({"title": "Lote en Camposanto La Piedad", "acres": 0.001, "price": 4800}, "burial plot"),
    ({"title": "Terreno", "description": "Espacio para 3 tumbas en Jardines del Recuerdo", "acres": None, "price": 2600}, "burial plot"),
    ({"description": "750 mz aptas para ganado, precio por manzana $3000"}, "per m2/acre/hectare"),
    ({"source": "encuentra24", "price": 1000}, "agency listing under $1,500"),
    ({"source": "olx", "kind": "houses", "price": 1300, "currency": "EUR", "title": "Moradia T3 com jardim e garagem"}, "price too low for a house"),
])
def test_audit_catches(rates, kw, expected):
    assert expected in flags_for(rates, **kw)


@pytest.mark.parametrize("kw", [
    {"title": "Terreno agricolo, Camposanto", "price": 150000, "currency": "EUR"},  # a town near Modena
    {"title": "Nnn Compromise Gulch, Neihart, MT 59465"},  # a street-number placeholder, not a triple-net lease
    {"description": "You own your land — no pad or lot rent. Prepare to be moved by the views."},
    {"description": "Cena obejmuje działkę i domek holenderski z trzema sypialniami"},  # a plot sold with a caravan on it
    {"description": "Готова за бизнес с нощувки; може да се отдава на туристи"},  # could be let: still a sale
    {"description": "Just under 1/2 acre, 1/4 mile from the lake"},
])
def test_audit_rules_leave_sales_alone(rates, kw):
    assert flags_for(rates, **kw) == ""


def test_cheap_ruin_is_still_a_house_price(rates):
    f = flags_for(rates, source="olx", kind="houses", price=1500, currency="EUR", title="Casa em ruínas para recuperar")
    assert "price too low for a house" not in f


def test_landwatch_home_card_title_and_floor_area(rates):
    card = " | 3 beds | • | 2 baths | • | 3,217 sqft | 191 Dapplegray, Bell Canyon, CA, 91307, Ventura County | Agent"
    assert lf.landwatch_card(card) == ("191 Dapplegray, Bell Canyon, CA, 91307, Ventura County", pytest.approx(298.9, abs=0.1))
    assert lf.landwatch_card(" | 414,255 sqft | 9.51 Ac, Beryl, UT, 84714, Iron County") == ("9.51 Ac, Beryl, UT, 84714, Iron County", None)
    # a home card saved with the old "•" title is repaired, and a rent listed as a sale price is caught by its floor area
    r = lf.finalize({"source": "landwatch", "country": "US", "title": "•", "kind": "houses", "price": 10750, "currency": "USD",
                     "acres": 1.0, "url": "https://www.landwatch.com/ventura-county-california-homes-for-sale/pid/1",
                     "description": "Resort-style estate | $10,750 | • | 1 acres" + card}, rates)
    assert r["title"].startswith("191 Dapplegray")
    assert "under $40 per m2" in r["flags"]
