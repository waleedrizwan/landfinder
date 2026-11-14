"""build_outputs: the cross-listing checks, de-duplication, results.csv, report.html and the static site."""
import csv, json, re
from datetime import datetime, timedelta

import pytest

import landfinder as lf
from conftest import FakeStore, fixture_html

NOW = datetime(2026, 10, 22, 6, 30)
# site/data.json row fields (see the comment at the top of the script in site_template.html)
PER_ACRE, PRICE, ACRES, CC, REGION, TITLE, SRC, FLAGS, URL, LAT, LON, ORIG, TYPE, TOWN, OWN_PRICE, CUR, TAGS, PROV, SIMILAR, BLURB, DEAL, DEAL_USD, DEAL_N = range(23)


def stamp(days_ago=0):
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def fixture_records():
    recs = lf.parse_properstar(fixture_html("properstar_search.html"), "paraguay")[0]
    recs += lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")[0]
    recs += lf.parse_kijiji(fixture_html("kijiji_search.html"), "nova-scotia")[0]
    return recs


def swiss_records():
    """20 normal Swiss plots at ~$395/m2, plus one 'total' price that is really a per-m2 price."""
    recs = [{"source": "properstar", "country": "CH", "region": "Valais", "title": "Building plot %d" % i,
             "description": "", "price": 400000 + i * 1000, "currency": "USD", "acres": 0.25,
             "url": "https://www.properstar.com/listing/ch%d" % i} for i in range(20)]
    recs.append({"source": "properstar", "country": "CH", "region": "Zermatt", "title": "Plot with a view",
                 "description": "", "price": 650, "currency": "USD", "acres": 0.5,
                 "url": "https://www.properstar.com/listing/ch-per-m2"})
    return recs


def placeholder_records():
    """One exact low price on five different-size plots: a placeholder, not five bargains."""
    return [{"source": "properstar", "country": "BG", "region": "Varna", "title": "Plot %d" % i, "description": "",
             "price": 3000, "currency": "EUR", "acres": 0.1 * (i + 1),
             "url": "https://www.properstar.com/listing/bg%d" % i} for i in range(5)]


@pytest.fixture
def built(workdir):
    recs = fixture_records() + swiss_records() + placeholder_records()
    recs.append({"source": "kijiji", "country": "CA", "region": "Halifax", "title": "Wanted: 10 acres near Halifax",
                 "description": "", "price": 50000, "currency": "CAD", "acres": None,
                 "url": "https://www.kijiji.ca/v-land-for-sale/wanted"})
    recs.append({"source": "landwatch", "country": "US", "region": "Texas", "title": "Old listing",
                 "description": "", "price": 500, "currency": "USD", "acres": 5.0,
                 "url": "https://www.landwatch.com/old/pid/1", "scraped_at": stamp(days_ago=10)})
    for r in recs:
        r.setdefault("scraped_at", stamp())
    lf.build_outputs(FakeStore(recs), english=False)
    with open(workdir / "site" / "data.json") as f:
        data = json.load(f)
    with open(workdir / "site" / "index.html") as f:
        page = f.read()
    with open(workdir / "results.csv") as f:
        rows = list(csv.DictReader(f))
    return {"dir": workdir, "data": data, "page": page, "csv": rows,
            "by_url": {r[URL]: r for r in data["rows"]}, "csv_by_url": {r["url"]: r for r in rows}}


def test_writes_all_outputs(built):
    d = built["dir"]
    for name in ("results.csv", "report.html", "coverage.csv", "site/index.html", "site/data.json", "site/.nojekyll"):
        assert (d / name).exists(), name
    assert not (d / "report.page").exists()


def test_results_csv_is_cheapest_total_first(built):
    prices = [float(r["price_usd"]) for r in built["csv"] if r["price_usd"]]
    assert prices == sorted(prices)
    assert list(built["csv"][0]) == lf.COLS


def test_reposted_parcel_is_kept_once(built):
    pid = [r for r in built["csv"] if "70257423" in (r["title"] or "")]
    assert len(pid) == 1


def test_per_m2_price_is_flagged_and_hidden_on_site(built):
    r = built["csv_by_url"]["https://www.properstar.com/listing/ch-per-m2"]
    assert "price may be per m2 (would buy under 20 m2" in r["flags"]
    assert "U" in built["by_url"]["https://www.properstar.com/listing/ch-per-m2"][FLAGS]


def test_same_price_on_many_plots_is_a_placeholder(built):
    for i in range(5):
        r = built["csv_by_url"]["https://www.properstar.com/listing/bg%d" % i]
        assert "same price on many different-size plots" in r["flags"]
        assert "X" in built["by_url"][r["url"]][FLAGS]


def test_site_rows(built):
    data = built["data"]
    urls = set(built["by_url"])
    assert "https://www.kijiji.ca/v-land-for-sale/wanted" not in urls  # wanted ads are dropped
    assert "https://www.landwatch.com/old/pid/1" not in urls  # not seen in the last 3 days
    assert "https://www.landwatch.com/old/pid/1" in built["csv_by_url"]  # ...but still in results.csv
    assert data["countries"] == len({r[CC] for r in data["rows"]}) == 5
    for r in data["rows"]:
        assert len(r) == 23  # ... province, similar-listings count, description excerpt, deal, its typical price and comparables
        assert r[PRICE] is not None
    py = built["by_url"]["https://www.properstar.com/listing/114526942"]
    assert py[PRICE] == 4400 and (py[OWN_PRICE], py[CUR]) == (33000000, "PYG")
    assert data["rates"]["PYG"] == 7500.0  # for the site's Currency list


def test_cheapest_believable_listing(built):
    clean = sorted((r for r in built["data"]["rows"] if r[FLAGS] in ("", "N")), key=lambda r: r[PRICE])
    assert clean[0][PRICE] == 749 and "pid/428586497" in clean[0][URL]
    assert built["by_url"][clean[0][URL]][TOWN] == "Deming, NM"
    flagged = {r[PRICE] for r in built["data"]["rows"] if r[FLAGS] not in ("", "N")}
    assert {47, 650} <= flagged  # the monthly payment and the per-m2 price never rank as clean


def test_index_html_is_the_template(built):
    assert built["page"] == (built["dir"] / "site_template.html").read_text()


def test_report_html_has_the_data(built):
    report = (built["dir"] / "report.html").read_text()
    assert "/*DATA*/[]" not in report and "{{GENERATED}}" not in report
    assert "pid/428586497" in report


def test_same_title():
    cut = "Lot (PID 70257423) Old Port Medway Road Beach Meadows, Nova Scot"
    full = "(PID 70257423) Old Port Medway Road Beach Meadows, Nova Scotia"
    norm = lambda s: re.sub(r"[^a-z0-9]", "", re.sub(r"^\s*lot\b", "", s.lower()))
    assert lf.same_title(norm(cut), cut, norm(full), full, "kijiji")
    assert lf.same_title(norm(full), full, norm(cut), cut, "kijiji")
    # a short generic title is not a cut-off copy of a longer, different one
    short, other = "$45,000 - Residential Lot for sale", "$45,000 - Residential Lot for sale in St-Joachim"
    assert not lf.same_title(norm(short), short, norm(other), other, "kijiji")
    # the 64-character cut-off is a Kijiji thing only
    assert not lf.same_title(norm(cut), cut, norm(full), full, "properstar")
    assert lf.same_title("abc", "ABC", "abc", "abc", "properstar")


def castle_records():
    """Two French castles (from Properstar's castle search) among 20 ordinary French plots."""
    recs = [{"source": "properstar", "country": "FR", "region": "Bourgogne", "title": "Plot %d" % i, "description": "",
             "price": 20000 + i * 500, "currency": "USD", "acres": 0.5, "kind": "land",
             "url": "https://www.properstar.com/listing/fr%d" % i} for i in range(20)]
    for i, price in enumerate((150000, 105000)):
        recs.append({"source": "properstar", "country": "FR", "region": "Ravières", "kind": "houses",
                     "title": "Castle: Home for sale in Ravières, France", "description": "", "price": price,
                     "currency": "USD", "acres": 0.1, "living_m2": 600,
                     "url": "https://www.properstar.com/listing/castle%d" % i})
    for r in recs:
        r["scraped_at"] = stamp()
    return recs


def test_castles_are_tagged_and_dont_skew_the_land_checks(workdir):
    lf.build_outputs(FakeStore(castle_records()), english=False)
    rows = json.loads((workdir / "site" / "data.json").read_text())["rows"]
    castles = sorted((r for r in rows if "castle" in r[URL]), key=lambda r: r[PRICE])
    assert [r[PRICE] for r in castles] == [105000, 150000]
    assert all(r[TAGS] == "K" and r[TYPE] == "H" and r[FLAGS] == "H" for r in castles)  # shown under Castles / House
    assert all(r[FLAGS] == "" for r in rows if "castle" not in r[URL])  # a building's price/m2 doesn't flag the plots


def test_collapse_folds_realtor_and_kijiji_by_mls_number():
    # Kijiji's REALTOR.ca feed writes the lot under the seller's title; REALTOR.ca gives its address. Same MLS number: one parcel.
    kijiji = {"source": "kijiji", "country": "CA", "price_usd": 2143.0, "title": "Affordable building lot in Sydney Mines!",
              "region": "Sydney Mines, NS, B1V3C8", "description": "Great lot, close to amenities. (id:24493) MLS# 202627276",
              "acres": None, "lat": None, "lon": None, "flags": []}
    realtor = {"source": "realtor", "country": "CA", "price_usd": 2143.0, "title": "Lot 276 Yorke Street",
               "region": "Sydney Mines, NS, B1V3C8", "description": "Vacant Land | 0.0534 ac|under 1/2 acre | MLS® 202627276",
               "acres": 0.0534, "lat": 46.23, "lon": -60.21, "flags": []}
    other = {"source": "realtor", "country": "CA", "price_usd": 2143.0, "title": "Lot 277 Yorke Street",
             "region": "Sydney Mines, NS, B1V3C8", "description": "Vacant Land | 0.0534 ac | MLS® 202627277",
             "acres": 0.0534, "lat": 46.24, "lon": -60.22, "flags": []}
    out = lf.collapse_similar([kijiji, realtor, other])
    assert [r["title"] for r in out] == ["Lot 276 Yorke Street", "Lot 277 Yorke Street"]  # the fuller REALTOR.ca record stands for both
    assert out[0]["similar"] == 1 and out[1]["similar"] == 0


def test_site_data_is_split_into_files_cloudflare_accepts(workdir, rates, monkeypatch):
    """Cloudflare Pages refuses a file over 25 MiB: past a size, rows go to data-2.json... listed in data.json.
    data.json holds the cheapest, and index.html names the other files so the page fetches them alongside it."""
    import glob, os
    monkeypatch.setattr(lf, "SITE_PART_BYTES", 2000)
    monkeypatch.setattr(lf, "SITE_FIRST_BYTES", 1000)
    recs = [lf.finalize({"source": "landwatch", "country": "US", "region": "Deming, NM", "title": "Lot %d" % i,
                         "description": "", "price": 5000 + i, "currency": "USD", "acres": 1.0, "lat": 32.2, "lon": -107.7,
                         "url": "https://www.landwatch.com/x/pid/%d" % i, "scraped_at": "2026-11-10T10:00:00"}, rates)
            for i in range(60)]
    site = os.path.join(str(workdir), "site")
    os.makedirs(site, exist_ok=True)
    open(os.path.join(site, "data-9.json"), "w").write("{}")  # left over from a bigger run
    lf.build_site(recs, rates)
    d = json.load(open(os.path.join(site, "data.json")))
    assert d["more"] and not os.path.exists(os.path.join(site, "data-9.json"))
    rows = d["rows"] + [r for name in d["more"] for r in json.load(open(os.path.join(site, name)))["rows"]]
    assert sorted(r[5] for r in rows) == sorted("Lot %d" % i for i in range(60))
    assert all(os.path.getsize(f) < 4000 for f in glob.glob(os.path.join(site, "data*.json")))
    assert [r[1] for r in rows] == sorted(r[1] for r in rows)  # cheapest first, across the files
    assert len(json.dumps(d["rows"])) < 1500 < len(json.dumps(json.load(open(os.path.join(site, d["more"][0])))["rows"]))
    assert "const MORE = %s;" % json.dumps(d["more"]) in open(os.path.join(site, "index.html")).read()
    lf.SITE_PART_BYTES = lf.SITE_FIRST_BYTES = 12_000_000
    lf.build_site(recs, rates)  # small enough for one file: no "more", no extra files
    assert "more" not in json.load(open(os.path.join(site, "data.json")))
    assert not glob.glob(os.path.join(site, "data-*.json"))
    assert "const MORE = [];" in open(os.path.join(site, "index.html")).read()


def test_site_blurb():
    """The list shows the start of English descriptions, cut at a word; nothing from other languages or repeats of the title."""
    long = "Discover the perfect opportunity to create your own haven in the heart of Luna County, New Mexico. " * 3
    b = lf.site_blurb({"source": "landwatch", "title": "La Susida Rd", "description": long})
    assert b.endswith("…") and len(b) <= lf.BLURB_CHARS + 1 and b.startswith("Discover the perfect")
    assert lf.site_blurb({"source": "olx", "title": "Działka", "description": "Sprzedam działkę"}) is None
    assert lf.site_blurb({"source": "kijiji", "title": "Terrain", "description": "Terrain à vendre près du lac"}) is None
    assert lf.site_blurb({"source": "landwatch", "title": "Big lot in Deming", "description": "Big lot in Deming, NM"}) is None
    assert lf.site_blurb({"source": "zillow", "title": "0 Main St", "description": "Lot / Land for sale | 256 days on Zillow | -- sqft"}) == "256 days on Zillow"
    assert lf.site_blurb({"source": "zillow", "title": "0 Main St", "description": "Lot / Land for sale | 12 hours ago | 1 acres lot"}) == "Listed 12 hours ago · 1 acres lot"


def deal_market(n=48):
    """n believable plots around one spot in Tennessee at ~$10,000 x acres^0.6, 1 to 10 acres, a few percent apart."""
    recs = []
    for i in range(n):
        acres = (1, 2, 5, 10)[i % 4]
        recs.append({"source": "landwatch", "country": "US", "title": "Lot %d" % i, "flags": "", "type": "vacant land",
                     "price_usd": 10000 * acres ** 0.6 * (0.8, 0.9, 1.0, 1.1, 1.25)[i % 5], "acres": acres,
                     "lat": 35.5 + (i % 6) * 0.03, "lon": -85.2 + (i // 6) * 0.03, "url": "https://example.com/%d" % i})
    return recs


def deal_listing(name, frac, acres=2, **kw):
    """A 2-acre plot in the middle of deal_market() at frac of its typical price."""
    return dict({"source": "landwatch", "country": "US", "title": name, "flags": "", "type": "vacant land",
                 "price_usd": 10000 * acres ** 0.6 * frac, "acres": acres, "lat": 35.58, "lon": -85.13,
                 "url": "https://example.com/" + name}, **kw)


def test_deal_scores_tags_steals_and_good_deals_against_nearby_land():
    picks = [deal_listing("steal", 0.2), deal_listing("good", 0.35), deal_listing("fair", 0.9),
             deal_listing("too-cheap-to-trust", 0.03),  # a per-m2 price or a wrong area, not a bargain
             deal_listing("flagged", 0.2, flags="lease/rental"), deal_listing("house", 0.2, type="house"),
             deal_listing("big-steal", 0.2, acres=10)]  # its comparables are scaled to 10 acres, not compared per acre
    recs = deal_market() + picks
    lf.deal_scores(recs)
    got = {r["title"]: r["deal"] for r in picks}
    assert got == {"steal": "steal", "good": "good deal", "fair": "", "too-cheap-to-trust": "", "flagged": "",
                   "house": "", "big-steal": "steal"}
    steal = picks[0]
    assert steal["deal_n"] == lf.DEAL_COMPS and 0.8 < 10000 * 2 ** 0.6 / steal["deal_usd"] < 1.25
    assert picks[4]["deal_usd"] is None and picks[5]["deal_usd"] is None  # flagged listings and houses aren't scored
    assert not any(r["deal"] for r in recs[:48])  # the ordinary market has no deals


def test_deal_scores_are_deterministic_and_need_enough_comparables():
    import random
    recs = deal_market() + [deal_listing("steal", 0.2)]
    first = {r["url"]: (r["deal"], r["deal_usd"]) for r in lf.deal_scores([dict(r) for r in recs])}
    for seed in range(3):  # the order listings come in doesn't change any verdict or typical price
        shuffled = [dict(r) for r in recs]
        random.Random(seed).shuffle(shuffled)
        assert {r["url"]: (r["deal"], r["deal_usd"]) for r in lf.deal_scores(shuffled)} == first
    few = deal_market(lf.DEAL_MIN_COMPS - 1) + [deal_listing("lonely", 0.2)]
    lf.deal_scores(few)
    assert few[-1]["deal"] == "" and few[-1]["deal_usd"] is None
    far = deal_market() + [deal_listing("far", 0.2, lat=36.9)]  # ~150 km north: nothing within reach
    lf.deal_scores(far)
    assert far[-1]["deal_usd"] is None


def test_site_rows_carry_the_deal_and_its_reason(workdir, rates):
    recs = deal_market() + [deal_listing("steal", 0.2)]
    for r in recs:
        r.update(scraped_at="2026-11-10T10:00:00", price=r["price_usd"], currency="USD", region="Spencer, TN")
    lf.deal_scores(recs)
    lf.build_site(recs, rates)
    rows = json.loads((workdir / "site" / "data.json").read_text())["rows"]
    steal = next(r for r in rows if r[TITLE] == "steal")
    assert steal[DEAL] == "S" and steal[DEAL_N] == lf.DEAL_COMPS and steal[DEAL_USD] % 100 == 0
    assert all(r[DEAL] == "" and r[DEAL_USD] is None and r[DEAL_N] is None for r in rows if r is not steal)
