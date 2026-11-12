import types

import landfinder as lf


class BlockFetch:
    """Every page comes back HTTP 200 but is a bot-check page with no __NEXT_DATA__."""
    label = ""

    def get(self, url):
        return "<html><body>Please verify you are human</body></html>"


def test_kijiji_block_page_is_not_end_of_results(workdir):
    store = lf.Store()
    store.rates = {"USD": 1.0, "CAD": 1.4}
    a = types.SimpleNamespace(pages=5, listings=100, house_listings=100)
    lf.crawl_kijiji(BlockFetch(), store, {"ontario": "k0l9004"}, a)
    sid = lf.search_id("kijiji", "ontario", "land")
    assert store.done == set(), "block page must stay unmarked so a rerun retries it"
    assert store.searches[sid]["stop"] == "fetch failed"
