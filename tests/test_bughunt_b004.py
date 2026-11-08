import landfinder as lf


def test_the_metro_area_not_flagged_per_unit(rates, workdir):
    rec = {"title": "Lot 20 min from the Metro area", "description": "", "price_usd": 5000,
           "acres": 1, "usd_per_acre": 5000, "source": "craigslist"}
    flags = lf.flag(rec)
    assert "per m2" not in flags
    assert lf.believable_flags(flags)
