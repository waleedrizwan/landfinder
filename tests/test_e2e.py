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
    recs += lf.parse_landwatch(fixture_html("landwatch_search.html"), "new-mexico")
    recs += lf.parse_kijiji(fixture_html("kijiji_search.html"), "nova-scotia")
    for r in recs:
        r["scraped_at"] = datetime.now().isoformat(timespec="seconds")
    lf.build_outputs(FakeStore(recs))
    return str(workdir / "site")


def wait_for_data(page):
    """Scroll to the listings so the page fetches data.json, then wait until the grid is drawn from it."""
    page.locator("#all").scroll_into_view_if_needed()
    page.wait_for_function("!document.getElementById('count').textContent.startsWith('The cheapest')", timeout=15000)


def first_price(page):
    return page.inner_text("#grid .card:first-child .pr")


def test_desktop(browser, site):
    page, errors = open_page(browser, site, {"width": 1440, "height": 900})
    assert page.title() == "Cheapest Land on Earth"
    assert page.inner_text("#hero .sign") == "$749"
    assert "New Mexico, United States" in page.inner_text("#hero .where")
    assert page.locator("#hero .runnersup a").count() == 5
    assert page.locator(".rank").count() == 3  # US, Paraguay, Canada
    assert page.locator("#grid .card").count() > 0  # the first cards are built into the page
    assert first_price(page) == "$749"
    assert page.locator("#more").is_hidden()  # nothing to page through before the data loads

    wait_for_data(page)
    total = page.inner_text("#count")
    assert total.endswith(" listings") and not total.startswith("0 ")
    assert first_price(page) == "$749"
    page.locator("#map-sec").scroll_into_view_if_needed()  # the map is built when it comes into view
    page.wait_for_function("document.getElementById('map').textContent !== ''", timeout=15000)
    assert page.inner_text("#map") == "The map couldn't load."  # Leaflet is blocked in this test
    page.locator("#all").scroll_into_view_if_needed()

    page.select_option("#fc", "PY")
    assert page.inner_text("#count") == "3 listings"
    page.select_option("#fc", "")
    page.fill("#q", "no such place anywhere")
    assert page.inner_text("#count") == "0 listings"
    assert "No listings match these filters." in page.inner_text("#grid")
    assert page.locator("#more").is_hidden()
    page.click("#grid .empty button")
    assert page.inner_text("#count") == total

    page.select_option("#fs", "price-desc")
    assert first_price(page) != "$749"
    page.select_option("#fs", "price-asc")
    assert first_price(page) == "$749"
    assert errors == []


def test_phone(browser, site):
    page, errors = open_page(browser, site, {"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    wait_for_data(page)
    assert page.locator("#fs").is_visible()
    page.select_option("#fs", "price-desc")
    assert first_price(page) != "$749"
    page.select_option("#fs", "price-asc")
    assert first_price(page) == "$749"
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    assert errors == []


def test_error_state_when_data_fails(browser, site):
    os.remove(os.path.join(site, "data.json"))
    page, errors = open_page(browser, site, {"width": 1440, "height": 900})
    assert page.inner_text("#hero .sign") == "$749"  # built into the page, so it still shows
    page.locator("#all").scroll_into_view_if_needed()
    page.wait_for_function("document.getElementById('count').textContent.includes(\"couldn't\")", timeout=15000)
    assert page.locator("#more").is_hidden()
    assert errors == []


def real_build_is_current():
    """site/ is rebuilt by every run; one from before the current row format can't match the page."""
    try:
        with open(os.path.join(ROOT, "site", "data.json")) as f:
            rows = json.load(f)["rows"]
    except (OSError, ValueError, KeyError):
        return False
    return bool(rows) and len(rows[0]) == lf.KIND + 1


@pytest.mark.skipif(not real_build_is_current(), reason="no site/ build in the current format here")
def test_real_site_build(browser):
    """The site/ folder from the last real run, if there is one."""
    page, errors = open_page(browser, os.path.join(ROOT, "site"), {"width": 1440, "height": 900})
    assert page.inner_text("#hero .sign").startswith("$")
    assert page.locator(".rank").count() > 5
    wait_for_data(page)
    assert page.locator("#grid .card").count() == 48
    assert page.locator("#more").is_visible()
    assert errors == []
