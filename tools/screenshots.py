#!/usr/bin/env python3
"""Retake the README screenshots (docs/screenshots/) from the current site/ build, in headless Chromium.
Run it after a scrape (or tools/rebuild_site.py) whenever the site's look changes:
  .venv/bin/python tools/screenshots.py
"""
import functools, http.server, os, threading

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE, OUT = os.path.join(ROOT, "site"), os.path.join(ROOT, "docs", "screenshots")
# the storage notice only shows on a first visit; the shots show the page as a returning visitor sees it
QUIET = "try { localStorage.setItem('notice-ok', '1'); localStorage.setItem('counted', '1'); } catch (e) {}"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def serve():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=SITE))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d/" % server.server_port


def open_page(browser, url, **context):
    page = browser.new_context(**context).new_page()
    page.add_init_script(QUIET)
    page.goto(url)
    page.wait_for_function("!document.getElementById('count').classList.contains('loading')", timeout=60000)
    page.wait_for_timeout(800)  # the rows' fade-in
    return page


def shoot(page, name):
    page.screenshot(path=os.path.join(OUT, name))
    print("wrote docs/screenshots/" + name)


def main():
    assert os.path.exists(os.path.join(SITE, "data.json")), "no site/ build: run a scrape or tools/rebuild_site.py first"
    os.makedirs(OUT, exist_ok=True)
    server, url = serve()
    desktop = dict(viewport={"width": 1440, "height": 900}, color_scheme="light")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        page = open_page(browser, url, **desktop)  # the list, cheapest first
        shoot(page, "list.png")

        page.click("#t-map")  # every listing on the map
        page.wait_for_timeout(3000)  # map tiles
        shoot(page, "map.png")

        page = open_page(browser, url, **desktop)  # filtered to one continent
        page.click("#b-where")
        page.locator("#tfcont label", has_text="Europe").first.click()
        page.keyboard.press("Escape")
        page.mouse.move(0, 0)  # no row hover highlight
        page.evaluate("scrollTo(0, 0)")
        page.wait_for_timeout(800)
        shoot(page, "europe.png")

        page = open_page(browser, url, viewport={"width": 390, "height": 844}, device_scale_factor=2,
                         color_scheme="dark", is_mobile=True, has_touch=True)
        shoot(page, "phone.png")
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()
