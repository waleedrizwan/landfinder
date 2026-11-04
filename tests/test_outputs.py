"""build_outputs: the cross-listing checks, de-duplication, results.csv, report.html and the static site."""
import csv, json, re
from datetime import datetime, timedelta

import pytest

import landfinder as lf
from conftest import FakeStore, fixture_html

NOW = datetime(2026, 10, 22, 6, 30)


def stamp(days_ago=0):
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def fixture_records():
    recs = lf.parse_properstar(fixture_html("properstar_search.html"), "paraguay")[0]
    recs += lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")
    recs += lf.parse_kijiji(fixture_html("kijiji_search.html"), "nova-scotia")
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
    lf.build_outputs(FakeStore(recs))
    with open(workdir / "site" / "data.json") as f:
        data = json.load(f)
    with open(workdir / "site" / "index.html") as f:
        page = f.read()
    with open(workdir / "results.csv") as f:
        rows = list(csv.DictReader(f))
    return {"dir": workdir, "data": data, "page": page, "csv": rows,
            "by_url": {r[lf.URL]: r for r in data["rows"]}, "csv_by_url": {r["url"]: r for r in rows}}


def test_writes_all_outputs(built):
    d = built["dir"]
    for name in ("results.csv", "report.html", "site/index.html", "site/data.json", "site/.nojekyll"):
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
    assert "U" in built["by_url"]["https://www.properstar.com/listing/ch-per-m2"][lf.FLAGS]


def test_same_price_on_many_plots_is_a_placeholder(built):
    for i in range(5):
        r = built["csv_by_url"]["https://www.properstar.com/listing/bg%d" % i]
        assert "same price on many different-size plots" in r["flags"]
        assert "X" in built["by_url"][r["url"]][lf.FLAGS]


def test_site_rows(built):
    data = built["data"]
    urls = set(built["by_url"])
    assert "https://www.kijiji.ca/v-land-for-sale/wanted" not in urls  # wanted ads are dropped
    assert "https://www.landwatch.com/old/pid/1" not in urls  # not seen in the last 3 days
    assert "https://www.landwatch.com/old/pid/1" in built["csv_by_url"]  # ...but still in results.csv
    assert data["countries"] == len({r[lf.CC] for r in data["rows"]}) == 5
    for r in data["rows"]:
        assert len(r) == lf.KIND + 1
        assert r[lf.PRICE] is not None
    assert built["by_url"]["https://www.properstar.com/listing/114526942"][lf.PRICE] == 4400  # 33,000,000 PYG


def test_hero_is_cheapest_believable_listing(built):
    page = built["page"]
    hero = page.split('<div id="hero">')[1].split("</header>")[0]
    assert '<span class="sign">$749</span>' in hero
    assert "0.50 acres in New Mexico, United States" in hero
    assert "pid/428586497" in hero
    assert "$47<" not in hero and "$650<" not in hero  # monthly payment / per-m2 price never lead
    assert "Today&#x27;s cheapest land on earth: $749 buys 0.50 acres in New Mexico" in page


def test_country_ranking(built):
    ranks = re.findall(r'data-cc="(\w+)".*?<span class="v">(\$[\d,]+)', built["page"], re.S)
    assert ranks[0] == ("US", "$749")
    assert [cc for cc, _ in ranks] == ["US", "PY", "CA", "CH"]  # BG has only placeholder prices
    assert dict(ranks)["CH"] == "$400,000"


def test_template_placeholders_are_all_filled(built):
    assert not re.search(r"<!--(HERO|RANKS|COUNTRIES|UPDATED)-->|\{\{\w+\}\}", built["page"])
    assert '<option value="PY">' in built["page"]


def test_report_html_has_the_data(built):
    report = (built["dir"] / "report.html").read_text()
    assert "/*DATA*/[]" not in report and "{{GENERATED}}" not in report
    assert "pid/428586497" in report


@pytest.mark.parametrize("v, s", [(None, "—"), (0.5, "$0.50"), (749, "$749"), (1234567.4, "$1,234,567")])
def test_site_money(v, s):
    assert lf.site_money(v) == s


@pytest.mark.parametrize("v, s", [(None, "—"), (0.004, "<0.01"), (0.5, "0.50"), (10.2, "10.2"), (48136, "48,136")])
def test_site_acres(v, s):
    assert lf.site_acres(v) == s


def test_site_country_and_flag():
    assert lf.site_country("us") == "United States"
    assert lf.site_country("new-zealand") == "New Zealand"
    assert lf.site_flag("PL") == "\U0001f1f5\U0001f1f1"
    assert lf.site_flag("new-zealand") == "\U0001f310"


def test_site_with_no_believable_listings(workdir):
    rows = [[None, 1.0, None, "CA", "Somewhere", "Lot", "kijiji", "NXL", "https://www.kijiji.ca/x", None, None]]
    (workdir / "site").mkdir()
    lf.write_site(rows, {"CA"}, NOW.astimezone())
    page = (workdir / "site" / "index.html").read_text()
    assert "No listings yet." in page
    assert "The cheapest pieces of land you can actually buy anywhere in the world" in page


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
    """Two French castles among 20 ordinary French plots."""
    recs = [{"source": "properstar", "country": "FR", "region": "Bourgogne", "title": "Plot %d" % i, "description": "",
             "price": 20000 + i * 500, "currency": "USD", "acres": 0.5,
             "url": "https://www.properstar.com/listing/fr%d" % i} for i in range(20)]
    for i, price in enumerate((150000, 105000)):
        recs.append({"source": "properstar", "country": "FR", "region": "Ravières", "kind": "castle",
                     "title": "Castle: Home for sale in Ravières, France", "description": "", "price": price,
                     "currency": "USD", "acres": 0.1, "url": "https://www.properstar.com/listing/castle%d" % i})
    return recs


def test_castles_are_ranked_and_get_their_own_section(workdir):
    recs = castle_records()
    for r in recs:
        r["scraped_at"] = stamp()
    lf.build_outputs(FakeStore(recs))
    data = json.loads((workdir / "site" / "data.json").read_text())
    page = (workdir / "site" / "index.html").read_text()
    castles = [r for r in data["rows"] if r[lf.KIND] == "castle"]
    assert [r[lf.PRICE] for r in castles] == [105000, 150000]   # in the main data, cheapest first
    assert all(r[lf.FLAGS] == "" for r in castles)              # a building's price/m2 doesn't flag them...
    assert all(r[lf.FLAGS] == "" for r in data["rows"])         # ...nor skews the land check for the plots
    assert '<section id="castles">' in page and '<a href="#castles">Castles</a>' in page
    section = page.split('<section id="castles">')[1].split("</section>")[0]
    assert section.count('class="card"') == 2
    assert section.index("castle1") < section.index("castle0")  # $105,000 before $150,000
    csv_rows = list(csv.DictReader(open(workdir / "results.csv")))
    assert {r["url"].rsplit("/", 1)[1]: r["kind"] for r in csv_rows}["castle1"] == "castle"


def test_no_castle_section_without_castles(workdir):
    recs = castle_records()[:20]
    for r in recs:
        r["scraped_at"] = stamp()
    lf.build_outputs(FakeStore(recs))
    page = (workdir / "site" / "index.html").read_text()
    assert 'id="castles"' not in page and 'href="#castles"' not in page
