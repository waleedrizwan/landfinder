"""--more: places searched deeper than --listings (California by default)."""
from types import SimpleNamespace

import pytest

import landfinder as lf


def args(**kw):
    return SimpleNamespace(**dict(dict(listings=250, house_listings=60, pages=30, more={"california": 2000}), **kw))


def test_parse_more():
    assert lf.parse_more("california=2000, Texas=1000") == {"california": 2000, "texas": 1000}
    assert lf.parse_more("") == {}
    assert lf.parse_more(lf.MORE_DEFAULT)["california"] >= 1000
    with pytest.raises(ValueError):
        lf.parse_more("california")


def test_limits_scale_for_a_boosted_place_on_every_site():
    a = args()
    assert lf.search_limits("landwatch california", a) == (2000, 240)
    assert lf.search_limits("zillow california", a) == (2000, 240)
    assert lf.search_limits("landwatch california houses", a, "houses") == (480, 240)


def test_other_places_keep_the_defaults():
    a = args()
    assert lf.search_limits("landwatch texas", a) == (250, 30)
    assert lf.search_limits("landwatch texas houses", a, "houses") == (60, 30)
    assert lf.search_limits("landwatch california", args(more={})) == (250, 30)
    assert lf.search_limits("landwatch california", SimpleNamespace(listings=250, house_listings=60, pages=30)) == (250, 30)


def test_a_boost_below_the_default_changes_nothing():
    assert lf.search_limits("landwatch california", args(more={"california": 100})) == (250, 30)


def test_search_pages_goes_past_the_normal_page_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    store = lf.Store()
    assert len(list(lf.search_pages(store, "landwatch california", args()))) == 240
    assert len(list(lf.search_pages(store, "landwatch nevada", args()))) == 30
