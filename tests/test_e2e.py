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

    # wide screens: the map is a column beside the list, with no tabs (Leaflet is blocked here, so it stays empty)
    assert page.locator("#p-map").is_visible() and page.locator("#p-list").is_visible()
    assert page.locator(".tabs").is_hidden()
    assert errors == []


def test_map_is_a_tab_on_narrower_screens(browser, site):
    page, errors = open_page(browser, site, {"width": 1024, "height": 768})
    wait_for_data(page)
    assert page.locator("#p-map").is_hidden()
    page.click("#t-map")
    assert page.locator("#p-map").is_visible() and page.locator("#p-list").is_hidden()
    page.set_viewport_size({"width": 1440, "height": 900})  # widened: both, side by side
    page.wait_for_function("!document.getElementById('p-list').hidden")
    assert page.locator("#p-map").is_visible()
    page.set_viewport_size({"width": 1024, "height": 768})  # and back: the map tab is still the one picked
    page.wait_for_function("document.getElementById('p-list').hidden")
    page.click("#t-list")
    assert first_price(page) == "$749"
    assert errors == []


def test_phone(browser, site):
    page, errors = open_page(browser, site, {"width": 390, "height": 844})
    wait_for_data(page)
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert first_price(page) == "$749"
    assert page.locator("#fbtn").is_visible()  # the filters open from a button on phones
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
    return bool(rows) and len(rows[0]) == 17


@pytest.mark.skipif(not real_build_is_current(), reason="no site/ build in the current format here")
def test_real_site_build(browser):
    """The site/ folder from the last real run, if there is one."""
    page, errors = open_page(browser, os.path.join(ROOT, "site"), {"width": 1440, "height": 900})
    wait_for_data(page)
    assert first_price(page).strip()
    assert page.locator("#rows tr").count() > 10
    assert page.locator("#more").is_visible()
    assert errors == []
