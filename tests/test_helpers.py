"""Number and area parsing, currency conversion."""
import pytest

import landfinder as lf


@pytest.mark.parametrize("raw, expected", [
    ("1,250", 1250.0), (" 3.5 ", 3.5), (42, 42.0), ("abc", None), (None, None), ("", None),
])
def test_num(raw, expected):
    assert lf.num(raw) == expected


@pytest.mark.parametrize("text, acres", [
    ("320 acres of ranch land", 320.0),
    ("Beautiful 1.5 acre lot", 1.5),
    (".75 acres", 0.75),
    ("12.5 ha near the river", 12.5 * 2.4710538),
    ("2 hectares", 2 * 2.4710538),
    ("9,000 sq ft corner lot", 9000 / 43560.0),
    ("632 m² plot", 632 / lf.ACRE_M2),
    ("Terrain de 800 m2", 800 / lf.ACRE_M2),
    # decimal comma: "5,62 ha" is 5.62 hectares, and a French/Spanish "1,140" is 1.14
    ("5,62 ha", 5.62 * 2.4710538),
    ("Terrain à vendre de 1,140 hectare", 1.14 * 2.4710538),
    # but an English thousands separator stays a thousands separator
    ("1,140 hectares of farmland", 1140 * 2.4710538),
])
def test_acres_from_text(text, acres):
    assert lf.acres_from_text(text) == pytest.approx(acres)


@pytest.mark.parametrize("text", [None, "", "Lot for sale, great views", "0 acres"])
def test_acres_from_text_none(text):
    assert lf.acres_from_text(text) is None


def test_to_usd(rates):
    assert lf.to_usd(100, "USD", rates) == 100
    assert lf.to_usd(140, "CAD", rates) == pytest.approx(100)
    assert lf.to_usd(7_500_000, "pyg", rates) == pytest.approx(1000)  # currency code is case-insensitive
    assert lf.to_usd(50, None, rates) == 50  # no currency means USD


def test_to_usd_unknown_or_missing(rates):
    assert lf.to_usd(100, "XYZ", rates) is None
    assert lf.to_usd(None, "USD", rates) is None


@pytest.mark.parametrize("price, expected", [
    (None, False), (50, True), (100, True), (101, False), (2500, False),
    (1111, True), (99999, True), (1234, True), (123456, True), (1500.5, False),
])
def test_placeholder_price(price, expected):
    assert lf.placeholder_price(price) is expected
