#!/usr/bin/env python3
"""
landfinder — scrape the cheapest land listings on earth and rank them by USD per acre.

Sources
  properstar  worldwide portal (~100+ countries), land plots sorted cheapest first
  landwatch   all 50 US states, sorted cheapest first
  kijiji      every Canadian province, land-for-sale sorted cheapest first

Usage
  ./run.sh                                   # everything, 10 pages per country/state/province
  ./run.sh --sources properstar --pages 20   # go deeper on one source
  ./run.sh --countries paraguay,bolivia      # only these Properstar countries
  ./run.sh --report-only                     # rebuild results.csv / report.html from saved data

Output (in this folder)
  results.csv   every listing, cheapest USD/acre first, with a "flags" column for suspicious prices
  report.html   open in a browser: sortable, filterable table of the same data
  data/         raw scraped records + progress, so a stopped run resumes where it left off
"""
import argparse, csv, json, os, random, re, sys, time, warnings
from datetime import datetime

warnings.filterwarnings("ignore")
import requests
from bs4 import BeautifulSoup

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ACRE_M2 = 4046.8564224
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

# Properstar URL slugs. Unknown/empty countries are skipped automatically.
PROPERSTAR_COUNTRIES = """
albania algeria andorra angola argentina armenia aruba australia austria azerbaijan bahamas bahrain
bangladesh barbados belarus belgium belize benin bolivia bosnia-and-herzegovina botswana brazil bulgaria
burkina-faso cambodia cameroon canada cape-verde chile china colombia costa-rica croatia cuba curacao
cyprus czech-republic denmark dominica dominican-republic ecuador egypt el-salvador estonia ethiopia fiji
finland france gabon georgia germany ghana greece grenada guatemala guyana haiti honduras hungary iceland
india indonesia ireland israel italy ivory-coast jamaica japan jordan kazakhstan kenya kosovo kyrgyzstan
laos latvia lebanon lithuania luxembourg madagascar malawi malaysia maldives mali malta mauritius mexico
moldova monaco mongolia montenegro morocco mozambique namibia nepal netherlands new-zealand nicaragua
nigeria north-macedonia norway oman pakistan panama paraguay peru philippines poland portugal qatar
romania russia rwanda saint-lucia saint-kitts-and-nevis saint-vincent-and-the-grenadines senegal serbia
seychelles sierra-leone singapore slovakia slovenia south-africa south-korea spain sri-lanka suriname
sweden switzerland taiwan tanzania thailand togo trinidad-and-tobago tunisia turkey uganda ukraine
united-arab-emirates united-kingdom united-states uruguay uzbekistan venezuela vietnam zambia zimbabwe
""".split()

US_STATES = """
alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho
illinois indiana iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota
mississippi missouri montana nebraska nevada new-hampshire new-jersey new-mexico new-york
north-carolina north-dakota ohio oklahoma oregon pennsylvania rhode-island south-carolina south-dakota
tennessee texas utah vermont virginia washington west-virginia wisconsin wyoming
""".split()

KIJIJI_PROVINCES = {
    "alberta": "l9003", "british-columbia": "l9007", "manitoba": "l9006", "new-brunswick": "l9005",
    "newfoundland": "l9008", "nova-scotia": "l9002", "ontario": "l9004", "prince-edward-island": "l9011",
    "quebec": "l9001", "saskatchewan": "l9009", "territories": "l9010",
}

AREA_TO_M2 = {"SquareMeter": 1.0, "SquareFoot": 0.09290304, "Hectare": 10000.0,
              "Acre": ACRE_M2, "SquareKilometer": 1e6, "SquareYard": 0.83612736}


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ----------------------------------------------------------------------------- helpers

def num(s):
    try:
        return float(str(s).replace(",", "").strip())
    except Exception:
        return None


def acres_from_text(text):
    """Pull a land area out of free text ('320 acres', '12.5 ha', '9,000 sq ft', '632 m²')."""
    if not text:
        return None
    t = text.lower()
    pats = [
        (r"([\d][\d,]*\.?\d*)\s*(?:acres?|ac\b|acre lot)", 1.0),
        (r"([\d][\d,]*\.?\d*)\s*(?:hectares?|ha\b|hect)", 2.4710538),
        (r"([\d][\d,]*\.?\d*)\s*(?:sq\.?\s*ft|sqft|square\s*feet|ft²|ft2)", 1 / 43560.0),
        (r"([\d][\d,]*\.?\d*)\s*(?:m²|m2\b|sq\.?\s*m\b|square\s*met)", 1 / ACRE_M2),
    ]
    for pat, factor in pats:
        m = re.search(pat, t)
        if m:
            v = num(m.group(1))
            if v and v > 0:
                return v * factor
    return None


def load_fx():
    """USD exchange rates, cached for the day."""
    path = os.path.join(DATA, "fx.json")
    today = datetime.now().strftime("%Y-%m-%d")
    if os.path.exists(path):
        d = json.load(open(path))
        if d.get("date") == today:
            return d["rates"]
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=20)
        rates = r.json()["rates"]
        json.dump({"date": today, "rates": rates}, open(path, "w"))
        return rates
    except Exception as e:
        if os.path.exists(path):
            log("FX fetch failed (%s); using cached rates" % e)
            return json.load(open(path))["rates"]
        raise


def to_usd(amount, currency, rates):
    if amount is None:
        return None
    cur = (currency or "USD").upper()
    rate = rates.get(cur)
    return amount / rate if rate else None


PAYMENT_RX = re.compile(r"(/\s*mo\b|/\s*month|per month|a month|monthly|\$\s*\d+\s*down|down payment)", re.I)
WANTED_RX = re.compile(r"^\s*(wanted|wtb|looking for|iso\b|in search of)", re.I)
MIN_PRICE = 500  # USD-ish floor applied to Properstar searches
AUCTION_RX = re.compile(r"\b(auction|tender|bid|starting bid)\b", re.I)


def flag(rec):
    f = []
    usd, acres = rec.get("price_usd"), rec.get("acres")
    text = " ".join(str(rec.get(k) or "") for k in ("title", "description"))
    if WANTED_RX.search(rec.get("title") or ""):
        f.append("wanted ad - not a listing")
    if usd is not None and usd < 300:
        f.append("price too low - likely monthly payment or per-m2 price")
    elif usd is not None and usd < 2000 and PAYMENT_RX.search(text):
        f.append("may be payment plan price")
    if AUCTION_RX.search(text):
        f.append("auction/tender - price may be a minimum bid")
    if not acres:
        f.append("no land area listed")
    elif rec.get("usd_per_acre") is not None and rec["usd_per_acre"] < 10 and not f:
        f.append("check: under $10/acre")
    return "; ".join(f)


def finalize(rec, rates):
    rec["price_usd"] = to_usd(rec.get("price"), rec.get("currency"), rates)
    a = rec.get("acres")
    rec["acres"] = round(a, 4) if a else None
    rec["usd_per_acre"] = (rec["price_usd"] / a) if (rec["price_usd"] and a) else None
    rec["flags"] = flag(rec)
    return rec


# ----------------------------------------------------------------------------- storage

class Store:
    """Append-only JSONL of listings + set of finished page URLs, so runs resume."""

    def __init__(self):
        os.makedirs(DATA, exist_ok=True)
        self.path = os.path.join(DATA, "listings.jsonl")
        self.prog_path = os.path.join(DATA, "pages_done.txt")
        self.urls = set()
        if os.path.exists(self.path):
            for line in open(self.path):
                try:
                    self.urls.add(json.loads(line)["url"])
                except Exception:
                    pass
        self.done = set(open(self.prog_path).read().split()) if os.path.exists(self.prog_path) else set()

    def add(self, recs):
        new = 0
        with open(self.path, "a") as f:
            for r in recs:
                if r.get("url") and r["url"] not in self.urls:
                    self.urls.add(r["url"])
                    r["scraped_at"] = datetime.now().isoformat(timespec="seconds")
                    f.write(json.dumps(r) + "\n")
                    new += 1
        return new

    def mark(self, page_url):
        self.done.add(page_url)
        with open(self.prog_path, "a") as f:
            f.write(page_url + "\n")

    def all(self):
        out = []
        if os.path.exists(self.path):
            for line in open(self.path):
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
        return out


# ----------------------------------------------------------------------------- fetchers

class Browser:
    """Real Google Chrome via Playwright. Several big portals block plain scripts and headless
    browsers, so by default a visible Chrome window opens and is reused for every page."""

    BLOCK_TITLES = ("access denied", "service unavailable", "just a moment", "attention required",
                    "are you a robot", "pardon our interruption")

    def __init__(self, headless=False, delay=2.5):
        from playwright.sync_api import sync_playwright
        self.delay = delay
        self.headless = headless
        self.pw = sync_playwright().start()
        self._launch()

    def _launch(self):
        headless = self.headless
        args = ["--disable-blink-features=AutomationControlled",
                # keep the window out of the way: open off-screen, don't throttle it in the background
                "--window-position=-3000,-3000", "--window-size=1366,900",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows"]
        try:
            self.browser = self.pw.chromium.launch(channel="chrome", headless=headless, args=args)
        except Exception:
            log("Google Chrome not found; using Playwright's Chromium (run: .venv/bin/playwright install chromium)")
            self.browser = self.pw.chromium.launch(headless=headless, args=args)
        self.ctx = self.browser.new_context(locale="en-US", viewport={"width": 1366, "height": 900})
        self.page = self.ctx.new_page()
        if not headless:
            self._minimize()

    def _minimize(self):
        """Minimize the Chrome window to the Dock so it never covers the screen."""
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
        except Exception as e:
            log("  could not minimize Chrome window (%s)" % str(e)[:80])

    def get(self, url, settle_ms=3500):
        for attempt in range(3):
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self.page.wait_for_timeout(settle_ms)
                try:  # bot checks sometimes reload the page once
                    self.page.wait_for_load_state("load", timeout=20000)
                except Exception:
                    pass
                title = (self.page.title() or "").lower()
                if any(b in title for b in self.BLOCK_TITLES):
                    wait = 20 * (attempt + 1)
                    log("  blocked (%r), waiting %ds then retrying" % (title[:40], wait))
                    time.sleep(wait)
                    continue
                html = self.page.content()
                time.sleep(self.delay + random.random() * self.delay)
                return html
            except Exception as e:
                msg = str(e)
                log("  fetch error %s (attempt %d)" % (msg[:120], attempt + 1))
                if "has been closed" in msg or "Target closed" in msg:
                    log("  Chrome was closed - reopening it (minimized)")
                    try:
                        self.browser.close()
                    except Exception:
                        pass
                    try:
                        self._launch()
                    except Exception as e2:
                        log("  relaunch failed: %s" % str(e2)[:120])
                time.sleep(5)
        return None

    def close(self):
        try:
            self.browser.close()
            self.pw.stop()
        except Exception:
            pass


class Plain:
    def __init__(self, delay=1.5):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

    def get(self, url):
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=30)
                if r.status_code == 200:
                    time.sleep(self.delay + random.random() * self.delay)
                    return r.text
                log("  HTTP %d (attempt %d)" % (r.status_code, attempt + 1))
                if r.status_code == 404:
                    return None
            except Exception as e:
                log("  fetch error %s" % str(e)[:120])
            time.sleep(10 * (attempt + 1))
        return None


# ----------------------------------------------------------------------------- sources

def parse_properstar(html, slug):
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>", html, re.S)
    if not m:
        return [], 0
    try:
        res = json.loads(m.group(1))["search"]["results"]
    except Exception:
        return [], 0
    out = []
    for L in res.get("listings") or []:
        price = L.get("price") or {}
        vals = price.get("values") or []
        if not price.get("show") or not vals:
            continue
        pv = next((v for v in vals if v.get("type") == "Original"), vals[0])
        area = L.get("area") or {}
        m2 = None
        for v in [area] + (area.get("values") or []):
            land = v.get("land")
            unit = ((v.get("unit") or {}).get("id"))
            if land and unit in AREA_TO_M2:
                m2 = land * AREA_TO_M2[unit]
                break
        loc = L.get("location") or {}
        title = L.get("automaticTitle") or ""
        en = [t["text"] for t in (L.get("title") or []) if t.get("language") == "en"]
        out.append({
            "source": "properstar",
            "country": loc.get("countryISO") or slug,
            "region": loc.get("city") or "",
            "title": (en[0] if en else "") or title,
            "description": title,
            "price": pv.get("value"),
            "currency": pv.get("currencyId"),
            "acres": (m2 / ACRE_M2) if m2 else None,
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
            "url": "https://www.properstar.com/listing/%s" % L.get("id"),
        })
    return out, int(res.get("total") or 0)


def crawl_properstar(br, store, countries, pages):
    for ci, slug in enumerate(countries, 1):
        total_new = 0
        for p in range(1, pages + 1):
            # price.min skips the "EUR 5" listings that are really per-m2 prices or placeholders
            url = "https://www.properstar.com/%s/buy/land-plot/price-low-to-high?price.min=%d" % (slug, MIN_PRICE)
            if p > 1:
                url += "&p=%d" % p
            if url in store.done:
                continue
            html = br.get(url)
            if not html:
                break
            recs, total = parse_properstar(html, slug)
            if not recs:
                store.mark(url)
                break
            total_new += store.add(recs)
            store.mark(url)
            if p * 20 >= total:
                break
        log("properstar [%d/%d] %-28s +%d listings" % (ci, len(countries), slug, total_new))


def parse_landwatch(html, state):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("div[data-qa-placard]"):
        a = card.find("a", href=re.compile(r"/pid/\d+"))
        if not a:
            continue
        text = card.get_text(" | ", strip=True)
        pm = re.search(r"\$([\d,]+)", text)
        am = re.search(r"([\d.,]+)\s*acres?", text)
        desc_el = card.find("script", type="application/ld+json")
        desc = ""
        if desc_el:
            try:
                desc = json.loads(desc_el.string or "").get("description", "")
            except Exception:
                pass
        addr = ""
        if am:
            rest = text[am.end():].split(" | ")
            addr = next((x for x in rest if x.strip()), "")
        out.append({
            "source": "landwatch",
            "country": "US",
            "region": state.replace("-", " ").title(),
            "title": addr,
            "description": desc,
            "price": num(pm.group(1)) if pm else None,
            "currency": "USD",
            "acres": num(am.group(1)) if am else None,
            "lat": None, "lon": None,
            "url": "https://www.landwatch.com" + a["href"].split("#")[0],
        })
    return out


def crawl_landwatch(br, store, states, pages):
    for si, st in enumerate(states, 1):
        total_new = 0
        for p in range(1, pages + 1):
            url = "https://www.landwatch.com/%s-land-for-sale/sort-price-low-high" % st
            if p > 1:
                url += "/page-%d" % p
            if url in store.done:
                continue
            html = br.get(url)
            if not html:
                break
            recs = parse_landwatch(html, st)
            if not recs:
                store.mark(url)
                break
            total_new += store.add(recs)
            store.mark(url)
        log("landwatch  [%d/%d] %-28s +%d listings" % (si, len(states), st, total_new))


def parse_kijiji(html, prov):
    soup = BeautifulSoup(html, "lxml")
    nd = soup.find("script", id="__NEXT_DATA__")
    if not nd:
        return []
    try:
        apollo = json.loads(nd.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except Exception:
        return []
    out = []
    for k, v in apollo.items():
        if not (isinstance(v, dict) and "price" in v and "title" in v and v.get("url")):
            continue
        pr = v.get("price") or {}
        amt = pr.get("amount")
        if pr.get("type") != "FIXED" or not amt:
            continue
        acres = None
        for at in ((v.get("attributes") or {}).get("all") or []):
            if at.get("canonicalName") == "areainacres" and at.get("canonicalValues"):
                acres = num(at["canonicalValues"][0])
        if not acres:
            acres = acres_from_text(v.get("title")) or acres_from_text(v.get("description"))
        loc = v.get("location") or {}
        coords = loc.get("coordinates") or {}
        out.append({
            "source": "kijiji",
            "country": "CA",
            "region": loc.get("address") or prov.replace("-", " ").title(),
            "title": v.get("title"),
            "description": v.get("description"),
            "price": amt / 100.0,
            "currency": "CAD",
            "acres": acres,
            "lat": coords.get("latitude"), "lon": coords.get("longitude"),
            "url": v["url"],
        })
    return out


def crawl_kijiji(fetch, store, provinces, pages):
    for pi, (prov, loc) in enumerate(provinces.items(), 1):
        total_new = 0
        for p in range(1, pages + 1):
            seg = "" if p == 1 else "page-%d/" % p
            url = "https://www.kijiji.ca/b-land-for-sale/%s/%sc641%s?sort=priceAsc" % (prov, seg, loc)
            if url in store.done:
                continue
            html = fetch.get(url)
            if not html:
                break
            recs = parse_kijiji(html, prov)
            if not recs:
                store.mark(url)
                break
            n = store.add(recs)
            total_new += n
            store.mark(url)
            if n == 0 and p > 1:   # Kijiji repeats the last page past the end
                break
        log("kijiji     [%d/%d] %-28s +%d listings" % (pi, len(provinces), prov, total_new))


# ----------------------------------------------------------------------------- output

COLS = ["usd_per_acre", "price_usd", "acres", "country", "region", "title", "price", "currency",
        "source", "flags", "url", "lat", "lon", "scraped_at"]


def build_outputs(store):
    rates = load_fx()
    recs = [finalize(r, rates) for r in store.all()]
    recs.sort(key=lambda r: (r["usd_per_acre"] is None, r["usd_per_acre"] or 0))
    # same parcel reposted under a new URL ("Lot (PID 123) X" vs "(PID 123) X") -> keep one
    seen, uniq = set(), []
    for r in recs:
        t = re.sub(r"[^a-z0-9]", "", re.sub(r"^\s*lot\b", "", (r.get("title") or "").lower()))
        key = (r.get("source"), r.get("country"), round(r.get("price") or 0), r.get("acres"), t)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    recs = uniq
    csv_path = os.path.join(HERE, "results.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for r in recs:
            row = dict(r)
            for k in ("usd_per_acre", "price_usd"):
                if row.get(k) is not None:
                    row[k] = round(row[k], 2)
            w.writerow(row)
    slim = [{k: r.get(k) for k in COLS if k not in ("lat", "lon", "scraped_at")} for r in recs]
    tpl = open(os.path.join(HERE, "report_template.html")).read()
    html = tpl.replace("/*DATA*/[]", json.dumps(slim)).replace(
        "{{GENERATED}}", datetime.now().strftime("%Y-%m-%d %H:%M"))
    open(os.path.join(HERE, "report.html"), "w").write(html)
    clean = [r for r in recs if r["usd_per_acre"] and not r["flags"]]
    log("wrote %d listings (%d with clean price+area) -> results.csv, report.html" % (len(recs), len(clean)))
    for r in clean[:15]:
        log("  $%10.2f/acre  $%11.0f  %9.2f ac  %-3s %-22s %s" % (
            r["usd_per_acre"], r["price_usd"], r["acres"], r["country"], (r["region"] or "")[:22], r["url"]))


# ----------------------------------------------------------------------------- main

def csv_list(s, default):
    return [x.strip() for x in s.split(",") if x.strip()] if s else default


def main():
    ap = argparse.ArgumentParser(description="Find the cheapest land listings worldwide.")
    ap.add_argument("--sources", default="properstar,landwatch,kijiji")
    ap.add_argument("--pages", type=int, default=10, help="pages per country/state/province (20-25 listings each)")
    ap.add_argument("--countries", help="Properstar country slugs, comma-separated (default: all)")
    ap.add_argument("--states", help="US state slugs for LandWatch, e.g. new-mexico,arizona (default: all)")
    ap.add_argument("--provinces", help="Kijiji provinces, e.g. nova-scotia,saskatchewan (default: all)")
    ap.add_argument("--headless", action="store_true", help="hide the Chrome window (more likely to get blocked)")
    ap.add_argument("--delay", type=float, default=2.5, help="base seconds between page loads")
    ap.add_argument("--fresh", action="store_true", help="re-scrape pages already done")
    ap.add_argument("--report-only", action="store_true")
    a = ap.parse_args()

    store = Store()
    if a.fresh:
        store.done = set()
        open(store.prog_path, "w").close()
    if not a.report_only:
        sources = csv_list(a.sources, [])
        countries = csv_list(a.countries, PROPERSTAR_COUNTRIES)
        states = csv_list(a.states, US_STATES)
        provs = {p: KIJIJI_PROVINCES[p] for p in csv_list(a.provinces, list(KIJIJI_PROVINCES))}
        log("starting: sources=%s pages=%d (%d already-scraped listings on disk)" % (
            ",".join(sources), a.pages, len(store.urls)))
        br = None
        try:
            if "kijiji" in sources:
                crawl_kijiji(Plain(delay=max(1.0, a.delay / 2)), store, provs, a.pages)
            if "landwatch" in sources or "properstar" in sources:
                br = Browser(headless=a.headless, delay=a.delay)
            if "landwatch" in sources:
                crawl_landwatch(br, store, states, a.pages)
            if "properstar" in sources:
                crawl_properstar(br, store, countries, a.pages)
        except KeyboardInterrupt:
            log("stopped by user - progress is saved; rerun the same command to resume")
        finally:
            if br:
                br.close()
    build_outputs(store)


if __name__ == "__main__":
    main()
