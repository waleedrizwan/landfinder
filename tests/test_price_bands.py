"""US states (LandWatch, Zillow) are searched in price slices up to --reach, an equal share of listings from each,
so a state's results aren't only its cheapest desert parcels."""
import re
from types import SimpleNamespace

import pytest

import landfinder as lf
from conftest import RATES


class FakeLandWatch:
    """A state with one lot at each price in `prices`. Like the real site: a search holding over 500 results answers
    with a sample of 500 spread over its whole price range, pages past the end are empty, and an empty search is
    padded with listings from outside it."""
    label = ""

    def __init__(self, prices):
        self.prices, self.urls = sorted(prices), []

    def get(self, url, ready=None, **kw):
        self.urls.append(url)
        m = re.search(r"price-(\d+)-(\d+)/", url)
        lo, hi = (int(m.group(1)), int(m.group(2))) if m else (0, 10 ** 12)
        p = int((re.search(r"/page-(\d+)", url) or [0, 1])[1])
        hits = [x for x in self.prices if lo <= x <= hi]
        shown = hits if len(hits) <= 500 else hits[::len(hits) // 500 + 1][:500]
        page = shown[(p - 1) * 25:p * 25] if hits else self.prices[:20]
        cards = "".join('<div data-qa-placard="1"><a href="/kern-county-california-land-for-sale/pid/%d">x</a>'
                        '<span>$%s</span><span>%d acres</span><span>Mojave, CA, 93501</span></div>' % (x, format(x, ","), 5)
                        for x in page)
        return "<html><body><h2>%s Listings</h2>%s</body></html>" % (format(len(hits), ","), cards)


def args(**kw):
    return SimpleNamespace(**dict(dict(listings=250, house_listings=60, pages=30, more={}, reach=200000), **kw))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(lf, "DATA", str(tmp_path))
    s = lf.Store()
    s.rates = dict(RATES)
    return s


def prices_of(store):
    return sorted(r["price"] for r in store.all())


def per_slice(prices, width=10000, n=20):
    return [sum(1 for x in prices if i * width <= x < (i + 1) * width) for i in range(n)]


def test_price_slices():
    assert lf.slice_count(250) == 20 and lf.slice_count(2000) == 20  # never more than 20
    assert lf.slice_count(60) == 5 and lf.slice_count(5) == 1
    assert lf.price_slices(20, 200000)[:2] == [(0, 9999), (10000, 19999)]
    assert lf.price_slices(20, 200000)[-1] == (190000, 199999)
    assert lf.price_slices(5, 200000) == [(0, 39999), (40000, 79999), (80000, 119999), (120000, 159999), (160000, 199999)]


def test_landwatch_url_shapes():
    assert lf.landwatch_url("california") == "https://www.landwatch.com/california-land-for-sale/sort-price-low-high"
    assert lf.landwatch_url("california", "land", (10000, 19999), 3).endswith(
        "/california-land-for-sale/price-10000-19999/sort-price-low-high/page-3")
    assert lf.landwatch_url("texas", "houses", (0, 39999)).endswith(
        "/texas-land-for-sale/homes/price-0-39999/sort-price-low-high")


def test_landwatch_state_is_covered_up_to_the_reach_not_just_its_cheapest(store):
    site = FakeLandWatch(range(1000, 1_000_000, 20))  # 500 lots in every $10,000, far past the reach
    lf.crawl_landwatch_one(site, store, "california", args())
    got = prices_of(store)
    assert all(n >= 12 for n in per_slice(got)), per_slice(got)  # every $10,000 slice up to $200,000 has its share
    assert max(got) < 200000 and len(got) >= 250
    s = store.searches["landwatch california"]
    assert s["stop"] == "enough listings"
    assert s["total"] == sum(1 for x in site.prices if x < 200000)  # what the site has up to the reach


def test_landwatch_more_listings_for_a_boosted_state(store):
    site = FakeLandWatch(range(1000, 1_000_000, 20))
    lf.crawl_landwatch_one(site, store, "california", args(more={"california": 2000}))
    got = prices_of(store)
    assert len(got) >= 2000 and all(n >= 100 for n in per_slice(got)), per_slice(got)


def test_landwatch_thin_slices_pass_their_share_up(store):
    site = FakeLandWatch(list(range(150000, 200000, 50)))  # nothing under $150,000
    lf.crawl_landwatch_one(site, store, "ohio", args())
    got = prices_of(store)
    assert len(got) >= 250 and min(got) >= 150000
    assert len({r["url"] for r in store.all()}) == len(got)  # nothing kept from an empty slice's padding


def test_landwatch_state_with_few_lots_stops_at_the_reach(store):
    site = FakeLandWatch([3000, 9000, 150000, 4000000])
    lf.crawl_landwatch_one(site, store, "rhode-island", args())
    assert prices_of(store) == [3000, 9000, 150000]
    assert store.searches["landwatch rhode-island"]["stop"] == "price reached"
    assert len(site.urls) == 1  # so few that the whole range is one page: no slices


def test_landwatch_houses_use_fewer_wider_slices(store):
    site = FakeLandWatch(range(1000, 1_000_000, 20))
    lf.crawl_landwatch_one(site, store, "ohio", args(), kind="houses")
    got = prices_of(store)
    assert all("/homes/" in u for u in site.urls) and len(site.urls) <= 13
    assert all(n >= 12 for n in per_slice(got, 40000, 5))


def test_landwatch_reach_option(store):
    site = FakeLandWatch(range(1000, 1_000_000, 20))
    lf.crawl_landwatch_one(site, store, "ohio", args(reach=400000))
    got = prices_of(store)
    assert all(n >= 12 for n in per_slice(got, 20000, 20)) and max(got) > 380000


def test_landwatch_resumes_where_it_stopped(store):
    site = FakeLandWatch(range(1000, 1_000_000, 20))
    lf.STOP.clear()
    real_get, calls = site.get, []

    def get(url, **kw):
        calls.append(url)
        if len(calls) == 8:
            lf.STOP.set()  # the user stops the run
        return real_get(url, **kw)
    site.get = get
    try:
        lf.crawl_landwatch_one(site, store, "california", args())
    finally:
        lf.STOP.clear()
    assert store.searches["landwatch california"]["stop"] is None
    site.get, first = real_get, list(site.urls)
    store2 = lf.Store()  # a new run, reading what the stopped one saved
    store2.rates = dict(RATES)
    lf.crawl_landwatch_one(site, store2, "california", args())
    assert not set(site.urls[len(first):]) & set(first)  # no page is fetched twice, not even the count
    assert all(n >= 12 for n in per_slice(sorted(r["price"] for r in store2.all())))


class FakeZillow:
    """One lot at each price in `prices`; pages of 41, cheapest first, the last page repeated past the end."""
    label = ""

    def __init__(self, prices):
        self.prices, self.calls = sorted(prices), []

    def get(self, url):
        q = lf.requests.utils.unquote(url)
        lo = int(re.search(r'"min":(\d+)', q).group(1))
        hi = int((re.search(r'"max":(\d+)', q) or [0, 10 ** 12])[1])
        p = int((re.search(r'"currentPage":(\d+)', q) or [0, 1])[1])
        self.calls.append((lo, hi, p))
        hits = [x for x in self.prices if lo <= x <= hi]
        page = hits[(p - 1) * 41:p * 41] or hits[-41:]
        return '{"totalResultCount":%d} %s' % (len(hits), " ".join(map(str, page)))


@pytest.fixture
def zillow_parse(monkeypatch):
    def parse(html, state, kind="land"):
        return [{"source": "zillow", "country": "US", "region": "CA", "title": "lot", "description": "", "currency": "USD",
                 "price": float(x), "acres": 1.0, "lat": None, "lon": None,
                 "url": "https://www.zillow.com/homedetails/%s" % x} for x in html.split("} ")[1].split()]
    monkeypatch.setattr(lf, "parse_zillow", parse)


def test_zillow_url_price_range():
    q = lf.requests.utils.unquote(lf.zillow_url("california", 2, floor=10000, ceil=19999))
    assert '"price":{"min":10000,"max":19999}' in q and "/ca/land/2_p/" in q
    assert '"price":{"min":1000}' in lf.requests.utils.unquote(lf.zillow_url("california", 1))


def test_zillow_state_is_covered_up_to_the_reach(store, zillow_parse):
    site = FakeZillow(range(500, 1_000_000, 20))
    lf.crawl_zillow_one(site, store, "california", args())
    got = prices_of(store)
    assert all(n >= 12 for n in per_slice(got)), per_slice(got)
    assert min(got) >= 1000 and max(got) < 200000  # nothing under Zillow's $1,000 placeholder floor
    assert store.searches["zillow california"]["stop"] == "enough listings"
    assert len(site.calls) == 21  # a look at the whole range, then one page of 41 gives each slice its share


def test_zillow_boosted_state_takes_several_pages_a_slice(store, zillow_parse):
    site = FakeZillow(range(500, 1_000_000, 20))
    lf.crawl_zillow_one(site, store, "california", args(more={"california": 2000}))
    assert all(n >= 100 for n in per_slice(prices_of(store)))


def test_zillow_state_with_few_lots_stops_at_the_reach(store, zillow_parse):
    site = FakeZillow([3000, 9000, 150000, 4000000])
    lf.crawl_zillow_one(site, store, "rhode-island", args())
    assert prices_of(store) == [3000, 9000, 150000]
    assert store.searches["zillow rhode-island"]["stop"] == "price reached"
    assert len(site.calls) == 1  # so few that the whole range is one page: no slices
    lf.crawl_zillow_one(site, store, "rhode-island", args())
    assert len(site.calls) == 1  # and a rerun has nothing left to do


def test_small_market_is_read_straight_through_cheapest_first(store):
    site = FakeLandWatch(range(1000, 181000, 1000))  # 180 lots under the reach: fewer than the 250 wanted
    lf.crawl_landwatch_one(site, store, "vermont", args())
    assert prices_of(store) == list(range(1000, 181000, 1000))
    assert len(site.urls) == 8 and all("price-0-199999" in u for u in site.urls)  # 180 lots, 25 a page
    assert store.searches["landwatch vermont"]["stop"] == "price reached"


def test_slices_are_in_the_sites_currency(store):
    """A site whose price filter is in Canadian dollars gets its slices converted (1.4 CAD per USD in the test rates)."""
    bands = []

    def load(url, band, p):
        bands.append(band)
        return [{"source": "kijiji", "country": "CA", "region": "NS", "title": "lot", "description": "5 acres",
                 "price": float(band[0] + k), "currency": "CAD", "acres": 5.0, "lat": None, "lon": None,
                 "url": "https://www.kijiji.ca/v/%d-%d" % (band[0], k)} for k in range(40)], 5000, 40
    lf.crawl_sliced(store, "kijiji nova-scotia", args(), "land", lambda band, p: "https://x/%d-%d/%d" % (band + (p,)),
                    load, 40, currency="CAD", floor=1400)
    assert bands[0] == (1400, 279999)  # the whole range: the floor up to $200,000
    bands = list(dict.fromkeys(bands[1:]))
    assert bands[0] == (1400, 13999) and bands[1] == (14000, 27999) and bands[-1] == (266000, 279999)
    assert len(bands) == 20


def test_page_cap_is_shared_out_so_the_dear_slices_are_reached(store):
    """A site where hardly any listing on a page is believable: the cheap slices can't use up every page."""
    bands = []

    def load(url, band, p):
        bands.append(band)
        # no land area and a price repeated on every listing of the page: one believable listing a page at most
        return [{"source": "olx", "country": "PL", "region": "PL", "title": "lot %d" % k, "description": "",
                 "price": None if k else float(band[0] + p), "currency": "USD", "acres": 5.0, "lat": None, "lon": None,
                 "url": "https://x/%d-%d-%d" % (band[0], p, k)} for k in range(40)], 100000, 40
    lf.crawl_sliced(store, "olx poland", args(), "land", lambda band, p: "https://x/%d-%d/%d" % (band + (p,)), load, 40)
    per_band = __import__("collections").Counter(bands[1:])
    assert len(per_band) == 20 and set(per_band.values()) == {2}  # 30 pages -> two for each of the 20 slices
    assert store.searches["olx poland"]["stop"] == "page cap"
