import pytest

import landfinder as lf


@pytest.mark.parametrize("text, acres", [
    ("1/4 acre lot", 0.25),
    ("1/2 acre lot", 0.5),
    ("2 1/2 acres", 2.5),
])
def test_fractional_acres(text, acres):
    assert lf.acres_from_text(text) == pytest.approx(acres)


def test_kijiji_finalize_quarter_acre(rates):
    rec = {"source": "kijiji", "title": "1/4 acre lot", "description": "", "acres": 0.25}
    lf.finalize(rec, rates)
    assert rec["acres"] == pytest.approx(0.25)
