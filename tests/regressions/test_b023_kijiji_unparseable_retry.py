import types

import landfinder as lf


class LayoutChangedFetch:
    """HTTP 200 with a __NEXT_DATA__ script but no __APOLLO_STATE__ (Kijiji layout change)."""
    label = ""

    def get(self, url):
        return ('<html><script id="__NEXT_DATA__" type="application/json">'
                '{"props": {"pageProps": {}}}</script></html>')


def test_kijiji_unparseable_page_is_retried_on_rerun(workdir):
    a = types.SimpleNamespace(pages=5, listings=100, house_listings=100)
    store = lf.Store()
    store.rates = {"USD": 1.0, "CAD": 1.4}
    lf.crawl_kijiji(LayoutChangedFetch(), store, {"ontario": "k0l9004"}, a)
    sid = lf.search_id("kijiji", "ontario", "land")
    assert store.searches[sid]["stop"] == "fetch failed"
    assert store.done == set()
    # a rerun must not treat the search as finished
    store2 = lf.Store()
    assert (store2.searches.get(sid) or {}).get("stop") != "end of results"
    assert store2.done == set()
