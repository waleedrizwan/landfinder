"""Browser smoke tests: build the site from fixture listings, load it in headless Chromium, check it renders.
Files are served through Playwright request routing on a fake origin, so no port is opened and nothing
outside the page itself (fonts, Leaflet, map tiles) is fetched. Skip with: pytest -m "not e2e"."""
import glob, json, mimetypes, os
from datetime import datetime

import pytest

import landfinder as lf
from conftest import ROOT, FakeStore, fixture_html

pytestmark = pytest.mark.e2e
sync_api = pytest.importorskip("playwright.sync_api")
ORIGIN = "http://landfinder.test/"


def launch(pw):
    """Playwright's own Chromium if it is installed, else the newest headless shell in the browser cache."""
    try:
        return pw.chromium.launch()
    except Exception as e:
        cache = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.expanduser(
            "~/Library/Caches/ms-playwright" if os.uname().sysname == "Darwin" else "~/.cache/ms-playwright")
        shells = sorted(glob.glob(os.path.join(cache, "chromium_headless_shell-*", "*", "chrome-headless-shell")))
        if not shells:
            pytest.skip("no Chromium for Playwright (run: .venv/bin/playwright install chromium): %s" % str(e)[:200])
        return pw.chromium.launch(executable_path=shells[-1])


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        b = launch(pw)
        yield b
        b.close()


def serve(page, site_dir):
    def handle(route):
        url = route.request.url
        if not url.startswith(ORIGIN):
            return route.abort()
        path = os.path.join(site_dir, url[len(ORIGIN):].split("?")[0].split("#")[0] or "index.html")
        if not os.path.isfile(path):
            return route.fulfill(status=404, body="not found")
        with open(path, "rb") as f:
            route.fulfill(status=200, body=f.read(), content_type=mimetypes.guess_type(path)[0] or "text/html")
    page.route("**/*", handle)


def open_page(browser, site_dir, viewport):
    page = browser.new_page(viewport=viewport)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    serve(page, site_dir)
    page.goto(ORIGIN)
    return page, errors


@pytest.fixture
def site(workdir):
    recs = lf.parse_properstar(fixture_html("properstar_search.html"), "paraguay")[0]
    recs += lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")[0]
    recs += lf.parse_kijiji(fixture_html("kijiji_search.html"), "nova-scotia")[0]
    for r in recs:
        r["scraped_at"] = datetime.now().isoformat(timespec="seconds")
    lf.build_outputs(FakeStore(recs), english=False)
    return str(workdir / "site")


def wait_for_data(page):
    """Wait until the list is drawn from data.json."""
    page.wait_for_function("/listings?$/.test(document.getElementById('count').textContent)", timeout=15000)


def first_price(page):
    return page.inner_text("#rows tr:first-child .p")


def test_desktop(browser, site):
    page, errors = open_page(browser, site, {"width": 1440, "height": 900})
    assert page.title() == "Cheapest Land"
    wait_for_data(page)
    total = page.inner_text("#count")
    assert not total.startswith("0 ")
    assert first_price(page) == "$749"
    assert page.inner_text("#rows tr:first-child .where") == "New Mexico, United States, on LandWatch"
    assert page.locator("#more").is_hidden()  # fewer listings than one page

    page.check("#fc input[value='PY']")
    assert page.inner_text("#count") == "3 listings"
    page.uncheck("#fc input[value='PY']")
    assert page.inner_text("#count") == total

    page.fill("#q", "no such place anywhere")
    assert page.inner_text("#count") == "0 listings"
    assert page.locator("#empty").is_visible()
    page.click("#empty button")
    assert page.inner_text("#count") == total

    page.check("#fflag")  # the $47/month LandWatch card is only shown with the flagged listings
    assert first_price(page) == "$47"
    page.uncheck("#fflag")
    assert first_price(page) == "$749"

    # wide screens open on List + map, side by side (Leaflet is blocked here, so the map stays empty)
    assert page.locator("#p-map").is_visible() and page.locator("#p-list").is_visible()
    assert page.get_attribute("#t-both", "aria-selected") == "true"
    assert errors == []


def test_moving_the_map_narrows_the_list(browser, site):
    """Leaflet is blocked in these tests, so this drives the same code a pan or zoom runs: AREA set from the map."""
    page, errors = open_page(browser, site, {"width": 1440, "height": 900})
    wait_for_data(page)
    total = page.inner_text("#count")
    # a box around New Mexico, as if the visitor had zoomed in there
    page.evaluate("""() => { AREA = {getEast: () => -103, getWest: () => -109.1, getNorth: () => 37, getSouth: () => 31.3,
      contains: ([lat, lng]) => lat >= 31.3 && lat <= 37 && lng >= -109.1 && lng <= -103}; shown = PAGE; update(true); }""")
    assert page.inner_text("#count") != total
    assert "In the map area" in page.inner_text("#chips")
    assert all("New Mexico" in t for t in page.locator("#rows .where").all_inner_texts())
    page.click("#chips .chip:has-text('In the map area')")
    assert page.inner_text("#count") == total
    assert errors == []


def test_list_map_and_both_views(browser, site):
    """The switch above the results picks List, Map (its own page: no sidebar) or, on wide screens, both side by side.
    The pick goes in the address, so the back button returns to the last view."""
    page, errors = open_page(browser, site, {"width": 1024, "height": 768})
    wait_for_data(page)
    assert page.locator("#p-map").is_hidden() and page.locator("#t-both").is_hidden()  # too narrow for side by side
    page.click("#t-map")
    assert page.locator("#p-map").is_visible() and page.locator("#p-list").is_hidden()
    assert page.locator("aside").is_hidden() and page.url.endswith("#map")
    page.set_viewport_size({"width": 1440, "height": 900})  # widened: still the map page, the view that was picked
    assert page.locator("#p-list").is_hidden()
    page.click("#t-both")
    assert page.locator("#p-map").is_visible() and page.locator("#p-list").is_visible() and page.locator("aside").is_visible()
    page.go_back()  # back to the map page
    page.wait_for_function("document.getElementById('p-list').hidden")
    page.click("#t-list")
    assert page.locator("#p-map").is_hidden() and first_price(page) == "$749"
    assert errors == []


def test_phone_map_button(browser, site):
    page, errors = open_page(browser, site, {"width": 390, "height": 844})
    wait_for_data(page)
    assert page.locator(".views").is_hidden() and page.inner_text("#viewpill") == "Show map"
    page.click("#viewpill")
    assert page.locator("#p-map").is_visible() and page.locator("#p-list").is_hidden()
    assert page.inner_text("#viewpill") == "Show list"
    page.click("#viewpill")
    assert first_price(page) == "$749"
    assert errors == []


def test_phone(browser, site):
    page, errors = open_page(browser, site, {"width": 390, "height": 844})
    wait_for_data(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert first_price(page) == "$749"
    assert page.locator("#fbtoggle").is_visible() and page.locator("#b-price").is_hidden()  # the bar starts folded up
    page.click("#fbtoggle")
    assert page.locator("#b-price").is_visible()
    assert page.locator("#fbtn").is_visible()  # the full filter panel opens from a button in it
    assert errors == []


def test_error_state_when_data_fails(browser, site):
    os.remove(os.path.join(site, "data.json"))
    page, errors = open_page(browser, site, {"width": 1440, "height": 900})
    page.wait_for_function("document.getElementById('count').textContent.includes(\"Couldn't\")", timeout=15000)
    assert errors == []


def real_build_is_current():
    """site/ is rebuilt by every run; one from before the current row format can't match the page."""
    try:
        with open(os.path.join(ROOT, "site", "data.json")) as f:
            rows = json.load(f)["rows"]
    except (OSError, ValueError, KeyError):
        return False
    return bool(rows) and len(rows[0]) == 20


@pytest.mark.skipif(not real_build_is_current(), reason="no site/ build in the current format here")
def test_real_site_build(browser):
    """The site/ folder from the last real run, if there is one."""
    page, errors = open_page(browser, os.path.join(ROOT, "site"), {"width": 1440, "height": 900})
    wait_for_data(page)
    assert first_price(page).strip()
    assert page.locator("#rows tr").count() > 10
    assert page.locator("#more").is_visible()
    assert errors == []


def test_shortlist(browser, site):
    """The heart saves a listing in this browser; Shortlist shows only those, and keeps one that later drops off the site."""
    page, errors = open_page(browser, site, {"width": 1440, "height": 900})
    wait_for_data(page)
    assert page.inner_text("#b-sl") == "Shortlist"
    page.click("#rows tr:first-child .save")
    assert page.get_attribute("#rows tr:first-child .save", "aria-pressed") == "true"
    assert page.inner_text("#b-sl") == "Shortlist (1)"
    page.click("#b-sl")
    assert page.inner_text("#count") == "1 listing" and first_price(page) == "$749"
    assert "Shortlist" in page.inner_text("#chips")
    page.reload()  # kept in the browser
    wait_for_data(page)
    assert page.inner_text("#b-sl") == "Shortlist (1)"
    # as if the listing had dropped off the site: the saved copy still shows, marked
    page.evaluate("""() => { const u = [...SAVED.keys()][0]; ALL = ALL.filter(r => r[8] !== u); BYURL.delete(u); SL_ON = true; refilter(); }""")
    assert page.inner_text("#count") == "1 listing" and first_price(page) == "$749"
    assert "No longer on the site" in page.inner_text("#rows tr:first-child .gone")
    page.click("#rows tr:first-child .save")  # removed: the shortlist is empty
    assert page.inner_text("#count") == "0 listings" and "Your shortlist is empty" in page.inner_text("#emptymsg")
    assert errors == []
