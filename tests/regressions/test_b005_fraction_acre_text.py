import landfinder as lf


def test_fraction_acre_text():
    assert lf.acres_from_text("1/2 acre lot") == 0.5
    assert lf.acres_from_text("3/4 acre") == 0.75


def test_kijiji_finalize_fractional_acre(rates):
    rec = {"source": "kijiji", "title": "1/2 acre lot near lake", "description": "",
           "price": 50000, "currency": "USD", "acres": 0.5}
    lf.finalize(rec, rates)
    assert rec["acres"] == 0.5
