import json, re
from playwright.sync_api import sync_playwright
B = "https://www.properstar.com/paraguay/buy/land-plot"
TRY = ["/price-per-area-low-to-high", "/price-per-sqm-low-to-high", "/price-per-m2-low-to-high",
       "/area-high-to-low", "/land-area-high-to-low", "/size-high-to-low",
       "/price-low-to-high?landArea.min=40000", "/price-low-to-high?area.min=40000", "/price-low-to-high?land.min=40000"]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=False, args=["--disable-blink-features=AutomationControlled"])
    pg = b.new_context(locale="en-US").new_page()
    for t in TRY:
        pg.goto(B + t, wait_until="domcontentloaded", timeout=60000); pg.wait_for_timeout(3500)
        try: pg.wait_for_load_state("load", timeout=15000)
        except Exception: pass
        m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>", pg.content(), re.S)
        if not m: print(t, "no state", pg.url); continue
        st = json.loads(m.group(1))["search"]; d = st["currentDescriptor"]
        f = {k: v for k, v in d.get("filters", {}).items() if v and v != {'min': None, 'max': None}}
        rows = []
        for l in st["results"]["listings"][:4]:
            pv = (l.get("price") or {}).get("values") or [{}]
            a = l.get("area") or {}
            rows.append((pv[0].get("value"), a.get("land"), (a.get("unit") or {}).get("id")))
        print(t, "| sort", d.get("sort"), "| filters", f, "| total", st["results"]["total"], "|", rows)
    b.close()
