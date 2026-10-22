import re, sys
from playwright.sync_api import sync_playwright
url = "https://www.landwatch.com/michigan-land-for-sale/sort-price-low-high"
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=False, args=["--disable-blink-features=AutomationControlled", "--window-position=-3000,-3000"])
    pg = b.new_context(locale="en-US", viewport={"width": 1366, "height": 900}).new_page()
    pg.goto(url, wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(5000)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(pg.content(), "lxml")
    cards = soup.select("div[data-qa-placard]")
    print("cards", len(cards))
    for c in cards[:8]:
        a = c.find("a", href=re.compile(r"/pid/\d+"))
        print("----", a["href"][-30:] if a else None)
        print(c.get_text(" | ", strip=True)[:300])
        for s in c.find_all("script", type="application/ld+json"):
            print("LD:", (s.string or "")[:600])
    b.close()
