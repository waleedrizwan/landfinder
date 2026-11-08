import landfinder as lf


def test_fractional_acre_kijiji_title(rates):
    rec = {"source": "kijiji", "title": "1/4 acre lot", "description": "", "price": 40000,
           "currency": "CAD", "acres": 0.25}
    lf.finalize(rec, rates)
    assert rec["acres"] == 0.25


def test_fractional_acre_text():
    assert lf.acres_from_text("1/4 acre lot") == 0.25
    assert lf.acres_from_text("3/4 acre") == 0.75
    assert lf.acres_from_text("1 1/2 acres") == 1.5
