import re, json, sys
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
urls = sys.argv[1:]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=False, args=["--disable-blink-features=AutomationControlled", "--window-position=-3000,-3000"])
    pg = b.new_context(locale="en-US", viewport={"width": 1366, "height": 900}).new_page()
    for u in urls:
        pg.goto(u, wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(4000)
        soup = BeautifulSoup(pg.content(), "lxml")
        print("=====", u[-30:], "|", pg.title()[:80])
        for s in soup.find_all("script", type="application/ld+json"):
            txt = (s.string or "")
            if '"offers"' in txt or '"price"' in txt: print("LD:", txt[:500])
        body = soup.get_text(" ", strip=True)
        i = body.find("Description")
        print("DESC:", body[i:i+700] if i >= 0 else body[:400])
    b.close()
