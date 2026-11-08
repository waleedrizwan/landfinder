import types

import landfinder as lf

BOT_CHECK = "<html><head><title>Just a moment</title></head><body><p>Verifying you are human</p></body></html>"


class Browser:
    label = ""

    def get(self, url, ready=None, **kw):
        return BOT_CHECK


class RecStore:
    """Same page/stop/done/searches contract as lf.Store, in memory."""

    def __init__(self):
        self.done, self.searches, self.stops, self.pages = set(), {}, [], []

    def believable(self, sid):
        return 0

    def page(self, sid, url, recs, total=None):
        self.pages.append(url)
        self.done.add(url)
        return 0

    def stop(self, sid, why):
        self.stops.append(why)
        self.searches.setdefault(sid, {})["stop"] = why


def test_landwatch_non_results_page_is_not_end_of_results(workdir):
    a = types.SimpleNamespace(pages=5, listings=100, house_listings=100)
    store = RecStore()
    lf.crawl_landwatch_one(Browser(), store, "texas", a)
    assert "end of results" not in store.stops
    assert store.stops == ["fetch failed"]
    assert not store.done  # page stays unmarked so a rerun retries it
