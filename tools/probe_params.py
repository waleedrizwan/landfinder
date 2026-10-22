import json, re
from playwright.sync_api import sync_playwright
BASE = "https://www.properstar.com/bulgaria/buy/land-plot/price-low-to-high"
GUESSES = ["?price.min=500", "?price-min=500", "?minprice=500", "?priceMin=500", "?pmin=500",
           "?price=500-", "?min-price=500"]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=False, args=["--disable-blink-features=AutomationControlled"])
    pg = b.new_context(locale="en-US").new_page()
    for g in GUESSES:
        pg.goto(BASE + g, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(3500)
        try: pg.wait_for_load_state("load", timeout=20000)
        except Exception: pass
        pg.wait_for_timeout(1500)
        html = pg.content()
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>", html, re.S)
        if not m:
            print(g, "no state", pg.url); continue
        st = json.loads(m.group(1))["search"]
        pr = st["currentDescriptor"]["filters"].get("price")
        res = st["results"]
        first = [l["price"]["values"][0]["value"] for l in res["listings"][:3] if l.get("price", {}).get("values")]
        print(g, "->", pg.url.replace(BASE, ""), "filter", pr, "total", res["total"], "first", first)
    b.close()
