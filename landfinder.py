#!/usr/bin/env python3
"""
landfinder — scrape the cheapest land listings on earth and rank them by total price in USD.

Sources
  properstar  worldwide portal (~100+ countries), land plots sorted cheapest first
  homes       HOME'S akiya bank: land and empty houses for sale in all 47 Japanese prefectures (houses are flagged)
  castles     Properstar's castle listings in the same countries (in the main ranking, plus their own site section)
  landwatch   all 50 US states, sorted cheapest first
  kijiji      every Canadian province, land-for-sale sorted cheapest first

Usage
  ./run.sh                                   # everything, 10 pages per country/state/province
  ./run.sh --sources properstar --pages 20   # go deeper on one source
  ./run.sh --countries paraguay,bolivia      # only these Properstar countries
  ./run.sh --report-only                     # rebuild results.csv / report.html from saved data

Output (in this folder)
  results.csv   every listing, cheapest total price first, with a "flags" column for suspicious prices
  report.html   open in a browser: sortable, filterable table of the same data
  site/         the static website (index.html + data.json) deployed by publish.sh
  data/         raw scraped records + progress, so a stopped run resumes where it left off
"""
import argparse, csv, html, json, math, os, queue, random, re, subprocess, sys, threading, time, warnings
from datetime import datetime, timedelta

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

US_STATE_CODES = dict(zip(US_STATES, """
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR
PA RI SC SD TN TX UT VT VA WA WV WI WY""".split()))

# OLX runs the same classifieds platform in several countries: (land-for-sale search, minimum price in the
# site's currency, roughly US$500). Below that, OLX is mostly per-m² prices typed into the total price box.
OLX_SITES = {
    "PL": ("https://www.olx.pl/nieruchomosci/dzialki/sprzedaz/", 2000),
    "PT": ("https://www.olx.pt/imoveis/terrenos-quintas/", 500),
    "RO": ("https://www.olx.ro/imobiliare/terenuri/", 500),
    "UA": ("https://www.olx.ua/uk/nedvizhimost/zemlya/prodazha-zemli/", 20000),
    "KZ": ("https://www.olx.kz/nedvizhimost/zemlya/prodazha/", 250000),
}
# OLX area fields, in m² per unit. Ukraine and Kazakhstan list land in sotky (1 sotka = 100 m²).
OLX_AREA_M2 = {"m": 1.0, "area_de_terreno_m2": 1.0, "land_area": 100.0}

# Encuentra24 country sites (Central America), in English
ENCUENTRA24_COUNTRIES = {"CR": "costa-rica-en", "PA": "panama-en", "NI": "nicaragua-en", "HN": "honduras-en",
                         "GT": "guatemala-en", "SV": "el-salvador-en"}

# LIFULL HOME'S akiya bank (homes.co.jp/akiyabank): municipal empty-house banks, one search per prefecture
HOMES_PREFECTURES = """
tohoku/hokkaido tohoku/aomori tohoku/iwate tohoku/miyagi tohoku/akita tohoku/yamagata tohoku/fukushima
kanto/tokyo kanto/kanagawa kanto/saitama kanto/chiba kanto/ibaraki kanto/tochigi kanto/gunma
hokuriku/niigata hokuriku/toyama hokuriku/ishikawa hokuriku/fukui hokuriku/yamanashi hokuriku/nagano
tokai/aichi tokai/gifu tokai/shizuoka tokai/mie kinki/osaka kinki/hyogo kinki/kyoto kinki/shiga kinki/nara
kinki/wakayama chugoku/tottori chugoku/shimane chugoku/okayama chugoku/hiroshima chugoku/yamaguchi
chugoku/tokushima chugoku/kagawa chugoku/ehime chugoku/kochi kyushu/fukuoka kyushu/saga kyushu/nagasaki
kyushu/kumamoto kyushu/oita kyushu/miyazaki kyushu/kagoshima kyushu/okinawa""".split()
HOMES_CATEGORIES = {"land": "102", "house": "101"}  # for sale only; the 30x categories are rentals

ALL_SOURCES = "properstar,castles,landwatch,kijiji,olx,zillow,immobiliare,encuentra24,homes"

AREA_TO_M2 = {"SquareMeter": 1.0, "SquareFoot": 0.09290304, "Hectare": 10000.0,
              "Acre": ACRE_M2, "SquareKilometer": 1e6, "SquareYard": 0.83612736}


STOP = threading.Event()
_LOG_LOCK = threading.Lock()


def log(msg):
    name = threading.current_thread().name
    tag = "" if name == "MainThread" else "%s " % name
    with _LOG_LOCK:
        print("[%s] %s%s" % (datetime.now().strftime("%H:%M:%S"), tag, msg), flush=True)


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
    romance = bool(re.search(r"\b(de|des|la|le|du|à|superficie|terrain|terre|hectares? de|à vendre|venta|terreno)\b", t))
    pats = [
        (r"((?:\d[\d,]*)?\.?\d+)\s*(?:acres?|ac\b|acre lot)", 1.0),
        (r"([\d][\d,]*\.?\d*)\s*(?:hectares?|ha\b|hect)", 2.4710538),
        (r"([\d][\d,]*\.?\d*)\s*(?:sq\.?\s*ft|sqft|square\s*feet|ft²|ft2)", 1 / 43560.0),
        (r"([\d][\d,]*\.?\d*)\s*(?:m²|m2\b|sq\.?\s*m\b|square\s*met)", 1 / ACRE_M2),
    ]
    for pat, factor in pats:
        m = re.search(pat, t)
        if m:
            raw = m.group(1).rstrip(".,")
            # "5,62 ha" and French/Spanish "1,140 hectare" use a decimal comma
            if re.search(r",\d{1,2}$", raw) or (romance and re.fullmatch(r"\d{1,3},\d{3}", raw)):
                raw = raw.replace(".", "").replace(",", ".")
            v = num(raw)
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


PAYMENT_RX = re.compile(r"(\d\s*(?:/\s*)?month\b|/\s*mo\b|/\s*month|per month|a month|monthly|\$\s*\d+\s*down|down payment)", re.I)
WANTED_RX = re.compile(r"^\s*(wanted|wtb|looking for|iso\b|in search of)", re.I)
MIN_PRICE = 1500  # USD-ish floor for Properstar searches; cheaper "prices" there are per-m2 or placeholders
TILE_SCALE = 0.5  # tiled windows draw pages at half size
CLAIM_RX = re.compile(r"\b(access deeds?|membership|timeshare|mining claims?|lode claims?|placer claims?|gold claims?|mineral claims?|mineral rights?|unpatented|lode gold|mining|prospecting)\b", re.I)
LEASE_RX = re.compile(r"(rent[- ]to[- ]own|\bfor lease\b|\blease[ds]?\b|\bleasing\b|\bfor rent\b|\brental\b|à louer|\btenant\b|/\s*month|\bmonthly\b|en alquiler|se alquila|\barriendo\b"
                      r"|\barrenda|\bdzierżaw|\bwynajm|\bînchiri|\binchiri|\bоренд|\bаренд|\bin affitto\b|\baffittasi\b)", re.I)
PER_UNIT_RX = re.compile(r"(\bx|\bper|\bpor|/|\bel|\ble|\bpar|\bthe)\s*(m2\b|m²|mt2|mts2|metro|sq\.?\s*m\b|vara|acre\b|hect|ha\b)", re.I)


def placeholder_price(p):
    if p is None:
        return False
    d = str(int(p)) if float(p).is_integer() else ""
    return p <= 100 or (len(d) >= 3 and len(set(d)) == 1) or d in ("1234", "12345", "123456")


AUCTION_RX = re.compile(r"\b(auction|tender|bid|starting bid|all'asta|asta giudiziaria|subasta|leilão|leilao|licytacj\w*|przetarg\w*|licitați\w*|licitatie|аукціон\w*|аукцион\w*)\b", re.I)


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
    if CLAIM_RX.search(text):
        f.append("mining claim/mineral rights/access deed - not land ownership")
    if "homes-for-sale" in (rec.get("url") or "") or rec.get("kind") == "house":
        f.append("house listing - not vacant land")
    if rec.get("source") == "kijiji" and usd is not None and usd < 3000:
        f.append("price too low for Canada - likely a deposit, lease or placeholder")
    if rec.get("source") == "properstar" and usd is not None and usd < 1500:
        f.append("price may be per m2/acre/hectare (agency listing under $1,500)")
    if LEASE_RX.search(text):
        f.append("lease/rental - not a sale price")
    if placeholder_price(rec.get("price")):
        f.append("placeholder price")
    if PER_UNIT_RX.search(text):
        f.append("price may be per m2/acre/hectare")
    if AUCTION_RX.search(text):
        f.append("auction/tender - price may be a minimum bid")
    if not acres:
        f.append("no land area listed")
    # No per-acre checks: listings are ranked by what you pay. A wrong area doesn't change the price.
    return "; ".join(f)


DOWN_RX = re.compile(r"\$\s?([\d,]+(?:\.\d\d)?)\s*(?:down\b|dp\b|deposit|to start|and take over)", re.I)
CASH_RX = re.compile(r"(?:(?:only|just|cash price(?: of| is)?|full price(?: of| is)?|total price(?: of| is)?|price(?: is)?:?|asking|for)\s*\$\s?([\d,]{3,}))"
                     r"|(?:\$\s?([\d,]{3,})\s*(?:cash|total|full price|obo|firm|or best))", re.I)
FINANCE_RX = re.compile(r"\b(seller financ|owner financ|financing available|down payment|\bdown\b|per month|/mo\b|monthly)", re.I)


def fix_down_payment(rec):
    """Some sellers enter the down payment as the price ('Only $2,700 Cash! ... $380 down').
    Swap in the real cash/total price from the text; return True if the price is a down payment."""
    p = rec.get("price")
    if not p:
        return False
    text = " ".join(str(rec.get(k) or "") for k in ("title", "description"))
    downs = {num(m) for m in DOWN_RX.findall(text)}
    amt = re.escape("{:,.0f}".format(p)).replace(",", ",?")
    if re.search(r"\$\s?%s\s*(?:/|per|a|each)?\s*(?:mo\b|month)" % amt, text, re.I):
        return True  # the listed price is the monthly payment
    if p not in downs and not (p < 3000 and FINANCE_RX.search(text)):
        return False
    totals = [num(a or b) for a, b in CASH_RX.findall(text)]
    totals = [t for t in totals if t and t >= p * 1.5]
    if totals:
        rec["listed_price"] = p
        rec["price"] = min(totals)
        return False
    return p in downs or bool(re.search(r"\$\s?%s\s*down" % re.escape("{:,.0f}".format(p)), text, re.I))


def finalize(rec, rates):
    down_only = rec.get("source") == "landwatch" and fix_down_payment(rec)
    if rec.get("source") == "kijiji":  # re-read area with the current parser (fixes older scrapes)
        a = acres_from_text(rec.get("title")) or acres_from_text(rec.get("description"))
        if a:
            rec["acres"] = a
    rec["price_usd"] = to_usd(rec.get("price"), rec.get("currency"), rates)
    a = rec.get("acres")
    rec["acres"] = round(a, 4) if a else None
    rec["usd_per_acre"] = (rec["price_usd"] / a) if (rec["price_usd"] and a) else None
    rec["flags"] = flag(rec)
    if down_only:
        rec["flags"] = "; ".join(x for x in (rec["flags"], "may be payment plan price - listed price is the down payment") if x)
    return rec


# ----------------------------------------------------------------------------- storage

class Store:
    """Append-only JSONL of listings + set of finished page URLs, so runs resume.
    A listing seen again on a later run is appended again; the newest line per URL wins, which keeps
    prices current and lets the site drop listings that haven't been seen lately."""

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
        self.lock = threading.Lock()

    def add(self, recs):
        new = 0
        with self.lock, open(self.path, "a") as f:
            for r in recs:
                if not r.get("url"):
                    continue
                if r["url"] not in self.urls:
                    self.urls.add(r["url"])
                    new += 1
                r["scraped_at"] = datetime.now().isoformat(timespec="seconds")
                f.write(json.dumps(r) + "\n")
        return new

    def mark(self, page_url):
        with self.lock, open(self.prog_path, "a") as f:
            self.done.add(page_url)
            f.write(page_url + "\n")

    def all(self):
        """Latest record per URL, with first_seen = when it was first scraped."""
        latest, first = {}, {}
        if os.path.exists(self.path):
            for line in open(self.path):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                u = r.get("url")
                if not u:
                    continue
                first.setdefault(u, r.get("scraped_at"))
                latest[u] = r
        for u, r in latest.items():
            r["first_seen"] = first[u]
        return list(latest.values())


# ----------------------------------------------------------------------------- fetchers

class Browser:
    """Real Google Chrome via Playwright. Several big portals block plain scripts and headless
    browsers, so by default a visible Chrome window opens and is reused for every page."""

    BLOCK_TITLES = ("access denied", "service unavailable", "just a moment", "attention required",
                    "are you a robot", "pardon our interruption", "azure waf", "verification required")

    def __init__(self, headless=False, delay=2.5, slot=None):
        """slot = (x, y, w, h) to show a small tiled window; None = off-screen and minimized."""
        from playwright.sync_api import sync_playwright
        self.delay = delay
        self.headless = headless
        self.slot = slot
        self.label = ""
        self.pw = sync_playwright().start()
        self._launch()

    def _launch(self):
        headless = self.headless
        args = ["--disable-blink-features=AutomationControlled",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows"]
        if self.slot:
            x, y, w, h = self.slot
            # small window, content drawn at half scale so sites still get their desktop layout
            args += ["--window-position=%d,%d" % (x, y), "--window-size=%d,%d" % (w, h),
                     "--force-device-scale-factor=%s" % TILE_SCALE]
        else:  # keep the window out of the way: off-screen, then minimized
            args += ["--window-position=-3000,-3000", "--window-size=1366,900"]
        try:
            self.browser = self.pw.chromium.launch(channel="chrome", headless=headless, args=args)
        except Exception:
            log("Google Chrome not found; using Playwright's Chromium (run: .venv/bin/playwright install chromium)")
            self.browser = self.pw.chromium.launch(headless=headless, args=args)
        if self.slot:
            self.ctx = self.browser.new_context(locale="en-US", no_viewport=True)
        else:
            self.ctx = self.browser.new_context(locale="en-US", viewport={"width": 1366, "height": 900})
        self.page = self.ctx.new_page()
        if not headless and not self.slot:
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
                # JavaScript bot checks (Azure WAF on Properstar, Cloudflare) usually pass on their own
                # within a few seconds and then load the real page
                for _ in range(8):
                    if not any(b in title for b in self.BLOCK_TITLES):
                        break
                    self.page.wait_for_timeout(2500)
                    title = (self.page.title() or "").lower()
                if any(b in title for b in self.BLOCK_TITLES):
                    wait = 20 * (attempt + 1)
                    log("  blocked (%r), waiting %ds then retrying" % (title[:40], wait))
                    time.sleep(wait)
                    continue
                html = self.page.content()
                self._show_label()
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

    def _show_label(self):
        """Banner + window title showing what this window is working on."""
        if not (self.slot and self.label):
            return
        try:
            self.page.evaluate("""(t) => {
                let d = document.getElementById('__lf');
                if (!d) { d = document.createElement('div'); d.id = '__lf';
                  d.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;' +
                    'background:#14161b;color:#7fffd4;font:bold 30px Menlo,monospace;padding:8px 14px;opacity:.92';
                  document.documentElement.appendChild(d); }
                d.textContent = t; document.title = t; }""", "%s | %s" % (threading.current_thread().name, self.label))
        except Exception:
            pass

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
        pics = (((L.get("resources") or {}).get("pictures") or {}).get("items") or [{}])
        castle = ((L.get("subType") or {}).get("id")) == "Castle"
        name = (en[0] if en else "") or title
        if castle and not CASTLE_RX.search(name):
            name = "Castle: " + name  # Properstar titles castles "Home for sale in ..."
        out.append({
            "source": "properstar",
            "country": loc.get("countryISO") or slug,
            "region": loc.get("city") or "",
            "title": name,
            "description": title,
            "price": pv.get("value"),
            "currency": pv.get("currencyId"),
            "acres": (m2 / ACRE_M2) if m2 else None,
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
            "url": "https://www.properstar.com/listing/%s" % L.get("id"),
            "img": pics[0].get("url"),
            "kind": "castle" if castle else None,
        })
    return out, int(res.get("total") or 0)


CASTLE_RX = re.compile(r"\b(castles?|ch[aâ]teaux?|castell[oi]|castillos?|burg|schloss|kasteel|zamek|castelo)\b", re.I)


def properstar_url(slug, p, castles=False):
    if castles:  # Properstar's castle subtype: /buy/castle on its own isn't a real search and lists everything
        url = "https://www.properstar.com/%s/buy/house/castle/price-low-to-high" % slug
        return url + ("?p=%d" % p if p > 1 else "")
    # price.min skips the "EUR 5" listings that are really per-m2 prices or placeholders
    url = "https://www.properstar.com/%s/buy/land-plot/price-low-to-high?price.min=%d" % (slug, MIN_PRICE)
    return url + ("&p=%d" % p if p > 1 else "")


def crawl_properstar_one(br, store, slug, pages, castles=False):
    total_new = 0
    for p in range(1, pages + 1):
        if STOP.is_set():
            break
        url = properstar_url(slug, p, castles)
        if url in store.done:
            continue
        br.label = "%s  page %d  (%s)" % (slug, p, "castles" if castles else "properstar")
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
    return total_new


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
            addr = next((x for x in rest if x.strip() and not re.match(r"^[\d.,]+\s*(beds?|baths?|sq\.? ?ft|sqft)\b", x.strip(), re.I)), "")
        if len(text) > len(desc):
            desc = (desc + " | " + text) if desc else text
        img = card.find("img", src=re.compile(r"^https://"))
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
            "img": img["src"] if img else None,
        })
    return out


def crawl_landwatch_one(br, store, st, pages):
    total_new = 0
    for p in range(1, pages + 1):
        if STOP.is_set():
            break
        url = "https://www.landwatch.com/%s-land-for-sale/sort-price-low-high" % st
        if p > 1:
            url += "/page-%d" % p
        if url in store.done:
            continue
        br.label = "%s  page %d  (landwatch)" % (st, p)
        html = br.get(url)
        if not html:
            break
        recs = parse_landwatch(html, st)
        if not recs:
            store.mark(url)
            break
        total_new += store.add(recs)
        store.mark(url)
    return total_new


def screen_area():
    """Usable desktop size on macOS (falls back to a 1440x900 screen)."""
    try:
        out = subprocess.run(["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
                             capture_output=True, text=True, timeout=10).stdout
        x0, y0, x1, y1 = [int(v) for v in out.replace(" ", "").split(",")]
        return x1 - x0, y1 - y0
    except Exception:
        return 1440, 900


def tile_slots(n, area="right"):
    """Grid of n small windows on the right half (or the full) screen, below the menu bar."""
    sw, sh = screen_area()
    top = 30
    x0 = sw // 2 if area == "right" else 0
    width, height = sw - x0, sh - top
    cols = max(1, math.ceil(math.sqrt(n)))
    rows = math.ceil(n / cols)
    w, h = width // cols, height // rows
    return [(x0 + (i % cols) * w, top + (i // cols) * h, w, h) for i in range(n)]


def run_pool(jobs, store, a):
    """Several Chrome windows, each taking the next country/state off a shared queue."""
    q = queue.Queue()
    for j in jobs:
        q.put(j)
    n = max(1, min(a.workers, len(jobs)))
    slots = [None] * n if a.hidden else tile_slots(n, a.tile)
    total = len(jobs)
    done = [0]

    def worker(i):
        br = None
        try:
            br = Browser(headless=a.headless, delay=a.delay, slot=slots[i])
            while not STOP.is_set():
                try:
                    src, key = q.get_nowait()
                except queue.Empty:
                    break
                fn = BROWSER_CRAWLERS[src]
                try:
                    new = fn(br, store, key, a.pages)
                except Exception as e:
                    log("%s %s failed: %s" % (src, key, str(e)[:120]))
                    new = 0
                done[0] += 1
                log("%-10s [%d/%d] %-28s +%d listings" % (src, done[0], total, key, new))
        finally:
            if br:
                br.close()

    threads = [threading.Thread(target=worker, args=(i,), name="W%d" % (i + 1), daemon=True) for i in range(n)]
    for i, t in enumerate(threads):
        t.start()
        time.sleep(3)  # stagger window launches
    while any(t.is_alive() for t in threads):
        for t in threads:
            t.join(0.5)


BROWSER_CRAWLERS = {  # sources that need a real Chrome window: fn(browser, store, key, pages) -> new listings
    "properstar": lambda br, store, key, pages: crawl_properstar_one(br, store, key, pages),
    "castles": lambda br, store, key, pages: crawl_properstar_one(br, store, key, pages, castles=True),
    "landwatch": lambda br, store, key, pages: crawl_landwatch_one(br, store, key, pages),
    "immobiliare": lambda br, store, key, pages: crawl_search(br, store, "immobiliare", key, pages,
                                                              immobiliare_url, parse_immobiliare),
    "encuentra24": lambda br, store, key, pages: crawl_search(br, store, "encuentra24", key, pages,
                                                              encuentra24_url, parse_encuentra24),
    "homes": lambda br, store, key, pages: crawl_search(br, store, "homes", key, pages, homes_url, parse_homes),
}


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
            "img": (v.get("imageUrls") or [None])[0],
        })
    return out


def crawl_kijiji(fetch, store, provinces, pages):
    for pi, (prov, loc) in enumerate(provinces.items(), 1):
        total_new = 0
        for p in range(1, pages + 1):
            if STOP.is_set():
                return
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


def crawl_search(fetch, store, src, key, pages, url_for, parse):
    """Page through one cheapest-first search. parse(html, key) returns a list of records, or None when the page
    isn't a results page at all (a bot check or an error page): that page is left unmarked so a rerun retries it."""
    total_new, seen = 0, set()
    for p in range(1, pages + 1):
        if STOP.is_set():
            break
        url = url_for(key, p)
        if url in store.done:
            continue
        fetch.label = "%s  page %d  (%s)" % (key, p, src)
        html = fetch.get(url)
        if not html:
            break
        recs = parse(html, key)
        if recs is None:
            log("%s %s page %d: no results data on the page (blocked?), skipping the rest" % (src, key, p))
            break
        urls = {r["url"] for r in recs if r.get("url")}
        if not recs or urls <= seen:  # empty, or the site repeats its last page past the end
            store.mark(url)
            break
        seen |= urls
        total_new += store.add(recs)
        store.mark(url)
    return total_new


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def eu_number(s):
    """'10.000' or '1.335,5' (European thousands dots / decimal comma) -> float."""
    s = (s or "").strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s) or "," in s:
        s = s.replace(".", "").replace(",", ".")
    return num(s)


# ---- OLX: Poland, Portugal, Romania, Ukraine, Kazakhstan (plain HTTP; listings are JSON in the page)

def olx_url(cc, p):
    base, floor = OLX_SITES[cc]
    q = "search%5Border%5D=filter_float_price:asc&search%5Bfilter_float_price:from%5D=" + str(floor)
    return base + "?" + ("page=%d&" % p if p > 1 else "") + q


def parse_olx(html, cc):
    m = re.search(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")', html)
    if not m:
        return None
    try:
        ads = json.loads(json.loads(m.group(1)))["listing"]["listing"]["ads"]
    except Exception:
        return None
    out = []
    for a in ads:
        rp = (a.get("price") or {}).get("regularPrice") or {}
        if not rp.get("value") or not a.get("url"):
            continue
        desc = strip_tags(a.get("description"))[:1500]
        m2 = None
        for prm in a.get("params") or []:
            if prm.get("key") in OLX_AREA_M2 and num(prm.get("normalizedValue")):
                m2 = num(prm["normalizedValue"]) * OLX_AREA_M2[prm["key"]]
                break
        loc, mp = a.get("location") or {}, a.get("map") or {}
        out.append({
            "source": "olx",
            "country": cc,
            "region": ", ".join(x for x in (loc.get("cityName"), loc.get("regionName")) if x),
            "title": a.get("title"),
            "description": desc,
            "price": float(rp["value"]),
            "currency": rp.get("currencyCode"),
            "acres": (m2 / ACRE_M2) if m2 else (acres_from_text(a.get("title")) or acres_from_text(desc)),
            "lat": mp.get("lat"), "lon": mp.get("lon"),
            "url": a["url"].split("?")[0],
            "img": (a.get("photos") or [None])[0],
        })
    return out


def crawl_olx(fetch, store, countries, pages):
    for i, cc in enumerate(countries, 1):
        if STOP.is_set():
            break
        n = crawl_search(fetch, store, "olx", cc, pages, olx_url, parse_olx)
        log("olx        [%d/%d] %-28s +%d listings" % (i, len(countries), site_country(cc), n))


# ---- Zillow: lots and land in every US state (plain HTTP; results are JSON in __NEXT_DATA__)

def zillow_url(state, p):
    fs = {k: {"value": False} for k in ("sf", "tow", "mf", "con", "apa", "manu", "apco")}  # land only
    fs.update({"sort": {"value": "pricea"}, "price": {"min": 1000}})
    sq = {"pagination": {"currentPage": p} if p > 1 else {}, "isMapVisible": False, "isListVisible": True,
          "filterState": fs}
    return "https://www.zillow.com/%s/land/%s?searchQueryState=%s" % (
        US_STATE_CODES[state].lower(), "%d_p/" % p if p > 1 else "",
        requests.utils.quote(json.dumps(sq, separators=(",", ":")), safe=""))


def parse_zillow(html, state):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        res = json.loads(m.group(1))["props"]["pageProps"]["searchPageState"]["cat1"]["searchResults"]["listResults"]
    except Exception:
        return None
    out = []
    for L in res:
        info = (L.get("hdpData") or {}).get("homeInfo") or {}
        if info.get("homeType", "LOT") != "LOT" or not L.get("unformattedPrice") or not L.get("detailUrl"):
            continue
        ll = L.get("latLong") or {}
        url = L["detailUrl"]
        out.append({
            "source": "zillow",
            "country": "US",
            "region": ", ".join(x for x in (L.get("addressCity"), L.get("addressState")) if x) or state.replace("-", " ").title(),
            "title": L.get("address") or "",
            "description": " | ".join(x for x in (L.get("statusText"), L.get("flexFieldText"), L.get("lotAreaString")) if x),
            "price": float(L["unformattedPrice"]),
            "currency": "USD",
            "acres": acres_from_text(L.get("lotAreaString")),
            "lat": ll.get("latitude"), "lon": ll.get("longitude"),
            "url": url if url.startswith("http") else "https://www.zillow.com" + url,
            "img": L.get("imgSrc"),
        })
    return out


def crawl_zillow(fetch, store, states, pages):
    for i, st in enumerate(states, 1):
        if STOP.is_set():
            break
        n = crawl_search(fetch, store, "zillow", st, pages, zillow_url, parse_zillow)
        log("zillow     [%d/%d] %-28s +%d listings" % (i, len(states), st, n))


# ---- Immobiliare.it: all of Italy (real Chrome; results are JSON in __NEXT_DATA__)

def immobiliare_url(key, p):
    # under €1,000 is almost all "price on request" placeholders and €1 auction lots
    return "https://www.immobiliare.it/vendita-terreni/%s/?criterio=prezzo&ordine=asc&prezzoMinimo=1000%s" % (
        key, "&pag=%d" % p if p > 1 else "")


def parse_immobiliare(html, key):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]
        res = data["results"]
    except Exception:
        return None
    out = []
    for r in res:
        e = r.get("realEstate") or {}
        price = (e.get("price") or {}).get("value")
        url = (r.get("seo") or {}).get("url")
        if not price or not url:
            continue
        p = (e.get("properties") or [{}])[0]
        loc = p.get("location") or {}
        sm = re.match(r"\s*([\d.,]+)\s*(m²|m2|ha)", p.get("surface") or "")
        m2 = eu_number(sm.group(1)) * (10000 if sm.group(2) == "ha" else 1) if sm else None
        photos = (p.get("multimedia") or {}).get("photos") or []
        img = ((photos[0].get("urls") or {}).get("small") or "").replace("/xxs-c.jpg", "/m-c.jpg") if photos else None
        desc = strip_tags(p.get("description") or p.get("caption") or "")[:1500]
        if e.get("contract") == "auction":
            desc = "Auction. " + desc
        out.append({
            "source": "immobiliare",
            "country": "IT",
            "region": ", ".join(x for x in (loc.get("city"), loc.get("region")) if x),
            "title": e.get("title") or "",
            "description": desc,
            "price": float(price),
            "currency": "EUR",
            "acres": (m2 / ACRE_M2) if m2 else None,
            "lat": loc.get("latitude"), "lon": loc.get("longitude"),
            "url": url,
            "img": img or None,
        })
    return out


# ---- HOME'S akiya bank: Japan (real Chrome, at the normal slow pace)

def homes_url(key, p):
    """key = "region/prefecture:land" or "...:house". Cheapest first; GET parameters work as well as the form."""
    pref, kind = key.split(":")
    return "https://www.homes.co.jp/akiyabank/%s/?sort=price_asc&categories%%5B%%5D=%s%s" % (
        pref, HOMES_CATEGORIES[kind], "&page=%d" % p if p > 1 else "")


def yen(text):
    """'200万円' -> 2000000, '1億2,000万円' -> 120000000, '35,000円' -> 35000; None for 応相談 etc."""
    t = re.sub(r"[\s,]", "", text or "")
    m = re.fullmatch(r"(?:([\d.]+)億)?(?:([\d.]+)万)?(\d+)?円", t)
    if not m or not any(m.groups()):
        return None
    oku, man, en = (float(x) if x else 0 for x in m.groups())
    return oku * 1e8 + man * 1e4 + en


def homes_m2(text):
    m = re.match(r"\s*([\d,.]+)\s*(m²|m2|㎡|坪)", text or "")
    if not m:
        return None
    v = num(m.group(1))
    return v * 3.305785 if (v and m.group(2) == "坪") else v


def parse_homes(html, key):
    soup = BeautifulSoup(html, "html.parser")
    boxes = soup.select(".sec-resultList .mod-result-bukkenBox")
    if not boxes:
        title = soup.title.get_text() if soup.title else ""
        return [] if "空き家バンク" in title else None  # a real page with no results, or a block page
    kind = key.split(":")[1]
    out = []
    for b in boxes:
        a = b.select_one('a[href*="/akiyabank/b-"]')
        if not a:
            continue
        spec = {dt.get_text(strip=True).lstrip("・"): dd.get_text(" ", strip=True)
                for dt, dd in zip(b.select(".specText dt"), b.select(".specText dd"))}
        price = yen(spec.get("価格"))
        if price is None:
            continue
        cat = b.select_one(".categoryTag")
        addr = b.select_one(".bukkenTitle .address")
        point = b.select_one(".pointText p")
        m2 = homes_m2(spec.get("土地面積"))
        img = b.select_one(".bukkenPhoto img")
        name = addr.get_text(" ", strip=True) if addr else ""
        out.append({
            "source": "homes",
            "country": "JP",
            "region": spec.get("所在地", ""),
            "title": ("House: " if kind == "house" else "Land: ") + (name or spec.get("所在地", "")),
            "description": " | ".join(x for x in ((cat.get_text(strip=True) if cat else ""),
                                                  (point.get_text(" ", strip=True).replace("Point", "", 1).strip()
                                                   if point else "")) if x),
            "price": price,
            "currency": "JPY",
            "acres": (m2 / ACRE_M2) if m2 else None,
            "lat": None, "lon": None,
            "url": a["href"].split("?")[0],
            "img": (img.get("src") or img.get("data-src")) if img else None,
            "kind": kind,
        })
    return out


# ---- Encuentra24: Central America (real Chrome; Cloudflare blocks plain requests)

E24_CURRENCY = [("C$", "NIO"), ("₡", "CRC"), ("US$", "USD"), ("$", "USD"), ("Q", "GTQ"), ("L", "HNL")]


def encuentra24_url(cc, p):
    return "https://www.encuentra24.com/%s/real-estate-for-sale-lots-land%s?sort=f_price&dir=asc" % (
        ENCUENTRA24_COUNTRIES[cc], ".%d" % p if p > 1 else "")


def parse_encuentra24(html, cc):
    soup = BeautifulSoup(html, "lxml")
    tiles = soup.select("div.d3-ad-tile")
    if not tiles and not soup.select_one(".d3-category-list__wrapper"):
        return None
    out = []
    for t in tiles:
        a = t.find("a", href=re.compile(r"/real-estate-for-sale-lots-land/[^/?]+/\d+$"))
        pr = t.select_one(".d3-ad-tile__price")
        txt = pr.get_text(" ", strip=True) if pr else ""
        pm = re.search(r"([\d][\d,]*(?:\.\d+)?)", txt)
        if not a or not pm:
            continue
        cur = next((c for sym, c in E24_CURRENCY if txt.lstrip().startswith(sym)), "USD")
        area = None
        for d in t.select(".d3-ad-tile__details-item"):
            dm = re.search(r"([\d][\d,]*(?:\.\d+)?)\s*m\s*2", d.get_text(" ", strip=True))
            if dm:
                area = num(dm.group(1)) / ACRE_M2
                break
        get = lambda sel: (t.select_one(sel).get_text(" ", strip=True) if t.select_one(sel) else "")
        img = t.select_one("img.d3-ad-tile__photo")
        src = (img.get("data-original") or img.get("src")) if img else None
        out.append({
            "source": "encuentra24",
            "country": cc,
            "region": get(".d3-ad-tile__location"),
            "title": get(".d3-ad-tile__title"),
            "description": get(".d3-ad-tile__short-description")[:1500],
            "price": num(pm.group(1)),
            "currency": cur,
            "acres": area,
            "lat": None, "lon": None,
            "url": "https://www.encuentra24.com" + a["href"],
            "img": src if src and "no-image" not in src else None,
        })
    return out


# ----------------------------------------------------------------------------- verification

def landwatch_full_description(html):
    soup = BeautifulSoup(html, "lxml")
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(sc.string or "")
        except Exception:
            continue
        t = d.get("@type")
        if (t == "RealEstateListing" or (isinstance(t, list) and "RealEstateListing" in t)) and d.get("description"):
            return re.sub(r"\s*,,\s*|\s*,\s*,\s*", " ", d["description"]).strip()
    return None


def verify_cheapest(store, a, n=60, rounds=4):
    """Open the cheapest believable LandWatch listings and re-check them against their full description
    (search cards cut descriptions short, which hides 'Only $2,700 cash ... $380 down' style pricing)."""
    today = datetime.now().strftime("%Y-%m-%d")
    br = None
    try:
        for rnd in range(rounds):
            rates = load_fx()
            raw = {r["url"]: r for r in store.all()}
            recs = sorted((finalize(dict(r), rates) for r in raw.values()),
                          key=lambda r: (r["price_usd"] is None, r["price_usd"] or 0))
            top = [r for r in recs if r["price_usd"] and r["flags"] in ("", "no land area listed")][:n]
            todo = [r for r in top if r["source"] == "landwatch" and not (r.get("verified_at") or "").startswith(today)]
            if not todo:
                break
            log("verifying %d of the cheapest %d listings (round %d)" % (len(todo), n, rnd + 1))
            if br is None:
                br = Browser(headless=a.headless, delay=a.delay, slot=None)
            for r in todo:
                if STOP.is_set():
                    return
                br.label = "verify " + r["url"][-12:]
                html = br.get(r["url"])
                rec = dict(raw[r["url"]])
                rec["verified_at"] = datetime.now().isoformat(timespec="seconds")
                if html:
                    desc = landwatch_full_description(html)
                    if desc:
                        rec["description"] = desc
                store.add([rec])
    finally:
        if br:
            br.close()


# ----------------------------------------------------------------------------- output

COLS = ["price_usd", "usd_per_acre", "acres", "country", "region", "title", "price", "currency",
        "source", "flags", "url", "lat", "lon", "scraped_at", "kind"]


KIJIJI_TITLE_MAX = 64  # Kijiji cuts titles off at this many characters


def same_title(t1, raw1, t2, raw2, source):
    """Normalized titles t1/t2 name the same listing: equal, or (Kijiji) one is the other cut off at 64 chars,
    as in '(PID 123) Old Port Road, Nova Scotia' vs 'Lot (PID 123) Old Port Road, Nova Scot'."""
    if t1 == t2:
        return True
    if source != "kijiji":
        return False
    (short, raw_short), long_ = ((t1, raw1), t2) if len(t1) < len(t2) else ((t2, raw2), t1)
    return len(raw_short) >= KIJIJI_TITLE_MAX and long_.startswith(short)


def build_outputs(store):
    rates = load_fx()
    recs = [finalize(r, rates) for r in store.all()]
    # one exact price on many different-size plots -> a placeholder or a per-m2 price, not a real total
    same = {}
    for r in recs:
        if r.get("price") and (r.get("price_usd") or 0) < 5000:
            same.setdefault((r.get("source"), r.get("country"), round(r["price"], 2)), set()).add(r.get("acres"))
    for r in recs:
        k = (r.get("source"), r.get("country"), round(r.get("price") or 0, 2))
        if k in same and len(same[k]) >= 5:
            r["flags"] = "; ".join(x for x in (r["flags"], "placeholder price - same price on many different-size plots") if x)
    # a "total" price that would only buy ~20 m2 at that country's typical price per m2 is a per-m2 price
    m2p = {}
    for r in recs:
        if r["price_usd"] and r.get("acres") and not r["flags"] and r.get("kind") != "castle":  # land prices only
            m2p.setdefault(r.get("country"), []).append(r["price_usd"] / (r["acres"] * ACRE_M2))
    med_m2 = {c: sorted(v)[len(v) // 2] for c, v in m2p.items() if len(v) >= 20}
    for r in recs:
        m = med_m2.get(r.get("country"))
        if m and r["price_usd"] and r["price_usd"] < 20 * m:
            r["flags"] = "; ".join(x for x in (r["flags"], "price may be per m2 (would buy under 20 m2 at local prices)") if x)
    recs.sort(key=lambda r: (r["price_usd"] is None, r["price_usd"] or 0))  # cheapest total price first
    # same parcel reposted under a new URL ("Lot (PID 123) X" vs "(PID 123) X") -> keep one
    seen, uniq = {}, []
    for r in recs:
        title = r.get("title") or ""
        t = re.sub(r"[^a-z0-9]", "", re.sub(r"^\s*lot\b", "", title.lower()))
        key = (r.get("source"), r.get("country"), round(r.get("price") or 0), r.get("acres"))
        if any(same_title(t, title, *prev, r.get("source")) for prev in seen.get(key, ())):
            continue
        seen.setdefault(key, []).append((t, title))
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
    page = tpl.replace("/*DATA*/[]", json.dumps(slim)).replace(
        "{{GENERATED}}", datetime.now().strftime("%Y-%m-%d %H:%M"))
    open(os.path.join(HERE, "report.html"), "w").write(page)
    build_site(recs)
    clean = [r for r in recs if r["price_usd"] and r["flags"] in ("", "no land area listed")]
    log("wrote %d listings (%d with a believable price) -> results.csv, report.html" % (len(recs), len(clean)))
    for r in clean[:15]:
        log("  $%9.0f  %9s ac  %-3s %-22s %s" % (
            r["price_usd"], "%.2f" % r["acres"] if r["acres"] else "?", r["country"], (r["region"] or "")[:22], r["url"]))


SITE = os.path.join(HERE, "site")
FLAG_CODES = [("price too low", "L"), ("payment plan", "P"), ("auction", "A"), ("wanted ad", "W"),
              ("no land area", "N"), ("lease/rental", "R"), ("placeholder", "X"),
              ("per m2/acre", "U"), ("price may be per m2", "U"),
              ("mining claim", "M"), ("house listing", "H"), ("too low for Canada", "L")]


def site_codes(flags):
    """One letter per kind of warning in a listing's flags text, each letter once."""
    return "".join(dict.fromkeys(c for txt, c in FLAG_CODES if txt in (flags or "")))


SOURCE_NAMES = {"properstar": "Properstar", "landwatch": "LandWatch", "kijiji": "Kijiji", "olx": "OLX",
                "zillow": "Zillow", "immobiliare": "Immobiliare.it", "encuentra24": "Encuentra24",
                "homes": "HOME'S Akiya Bank"}
COUNTRY_NAMES = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan", "AG": "Antigua & Barbuda",
    "AI": "Anguilla", "AL": "Albania", "AM": "Armenia", "AO": "Angola", "AQ": "Antarctica",
    "AR": "Argentina", "AS": "American Samoa", "AT": "Austria", "AU": "Australia", "AW": "Aruba",
    "AX": "Åland Islands", "AZ": "Azerbaijan", "BA": "Bosnia & Herzegovina", "BB": "Barbados",
    "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso", "BG": "Bulgaria", "BH": "Bahrain",
    "BI": "Burundi", "BJ": "Benin", "BL": "St. Barthélemy", "BM": "Bermuda", "BN": "Brunei", "BO": "Bolivia",
    "BQ": "Caribbean Netherlands", "BR": "Brazil", "BS": "Bahamas", "BT": "Bhutan", "BV": "Bouvet Island",
    "BW": "Botswana", "BY": "Belarus", "BZ": "Belize", "CA": "Canada", "CC": "Cocos (Keeling) Islands",
    "CD": "Congo - Kinshasa", "CF": "Central African Republic", "CG": "Congo - Brazzaville",
    "CH": "Switzerland", "CI": "Côte d’Ivoire", "CK": "Cook Islands", "CL": "Chile", "CM": "Cameroon",
    "CN": "China", "CO": "Colombia", "CR": "Costa Rica", "CU": "Cuba", "CV": "Cape Verde", "CW": "Curaçao",
    "CX": "Christmas Island", "CY": "Cyprus", "CZ": "Czechia", "DE": "Germany", "DJ": "Djibouti",
    "DK": "Denmark", "DM": "Dominica", "DO": "Dominican Republic", "DZ": "Algeria", "EC": "Ecuador",
    "EE": "Estonia", "EG": "Egypt", "EH": "Western Sahara", "ER": "Eritrea", "ES": "Spain", "ET": "Ethiopia",
    "FI": "Finland", "FJ": "Fiji", "FK": "Falkland Islands", "FM": "Micronesia", "FO": "Faroe Islands",
    "FR": "France", "GA": "Gabon", "GB": "United Kingdom", "GD": "Grenada", "GE": "Georgia",
    "GF": "French Guiana", "GG": "Guernsey", "GH": "Ghana", "GI": "Gibraltar", "GL": "Greenland",
    "GM": "Gambia", "GN": "Guinea", "GP": "Guadeloupe", "GQ": "Equatorial Guinea", "GR": "Greece",
    "GS": "South Georgia & South Sandwich Islands", "GT": "Guatemala", "GU": "Guam", "GW": "Guinea-Bissau",
    "GY": "Guyana", "HK": "Hong Kong", "HM": "Heard & McDonald Islands", "HN": "Honduras", "HR": "Croatia",
    "HT": "Haiti", "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IM": "Isle of Man",
    "IN": "India", "IO": "British Indian Ocean Territory", "IQ": "Iraq", "IR": "Iran", "IS": "Iceland",
    "IT": "Italy", "JE": "Jersey", "JM": "Jamaica", "JO": "Jordan", "JP": "Japan", "KE": "Kenya",
    "KG": "Kyrgyzstan", "KH": "Cambodia", "KI": "Kiribati", "KM": "Comoros", "KN": "St. Kitts & Nevis",
    "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait", "KY": "Cayman Islands", "KZ": "Kazakhstan",
    "LA": "Laos", "LB": "Lebanon", "LC": "St. Lucia", "LI": "Liechtenstein", "LK": "Sri Lanka",
    "LR": "Liberia", "LS": "Lesotho", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya",
    "MA": "Morocco", "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro", "MF": "St. Martin",
    "MG": "Madagascar", "MH": "Marshall Islands", "MK": "North Macedonia", "ML": "Mali",
    "MM": "Myanmar (Burma)", "MN": "Mongolia", "MO": "Macao", "MP": "Northern Mariana Islands",
    "MQ": "Martinique", "MR": "Mauritania", "MS": "Montserrat", "MT": "Malta", "MU": "Mauritius",
    "MV": "Maldives", "MW": "Malawi", "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique", "NA": "Namibia",
    "NC": "New Caledonia", "NE": "Niger", "NF": "Norfolk Island", "NG": "Nigeria", "NI": "Nicaragua",
    "NL": "Netherlands", "NO": "Norway", "NP": "Nepal", "NR": "Nauru", "NU": "Niue", "NZ": "New Zealand",
    "OM": "Oman", "PA": "Panama", "PE": "Peru", "PF": "French Polynesia", "PG": "Papua New Guinea",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PM": "St. Pierre & Miquelon",
    "PN": "Pitcairn Islands", "PR": "Puerto Rico", "PS": "Palestine", "PT": "Portugal", "PW": "Palau",
    "PY": "Paraguay", "QA": "Qatar", "RE": "Réunion", "RO": "Romania", "RS": "Serbia", "RU": "Russia",
    "RW": "Rwanda", "SA": "Saudi Arabia", "SB": "Solomon Islands", "SC": "Seychelles", "SD": "Sudan",
    "SE": "Sweden", "SG": "Singapore", "SH": "St. Helena", "SI": "Slovenia", "SJ": "Svalbard & Jan Mayen",
    "SK": "Slovakia", "SL": "Sierra Leone", "SM": "San Marino", "SN": "Senegal", "SO": "Somalia",
    "SR": "Suriname", "SS": "South Sudan", "ST": "São Tomé & Príncipe", "SV": "El Salvador",
    "SX": "Sint Maarten", "SY": "Syria", "SZ": "Eswatini", "TC": "Turks & Caicos Islands", "TD": "Chad",
    "TF": "French Southern Territories", "TG": "Togo", "TH": "Thailand", "TJ": "Tajikistan", "TK": "Tokelau",
    "TL": "Timor-Leste", "TM": "Turkmenistan", "TN": "Tunisia", "TO": "Tonga", "TR": "Türkiye",
    "TT": "Trinidad & Tobago", "TV": "Tuvalu", "TW": "Taiwan", "TZ": "Tanzania", "UA": "Ukraine",
    "UG": "Uganda", "UM": "U.S. Outlying Islands", "US": "United States", "UY": "Uruguay",
    "UZ": "Uzbekistan", "VA": "Vatican City", "VC": "St. Vincent & Grenadines", "VE": "Venezuela",
    "VG": "British Virgin Islands", "VI": "U.S. Virgin Islands", "VN": "Vietnam", "VU": "Vanuatu",
    "WF": "Wallis & Futuna", "WS": "Samoa", "XK": "Kosovo", "YE": "Yemen", "YT": "Mayotte",
    "ZA": "South Africa", "ZM": "Zambia", "ZW": "Zimbabwe",
}


def site_country(cc):
    """Country name shown on the site; same rule as the page's JavaScript."""
    if cc and len(cc) == 2 and cc.upper() in COUNTRY_NAMES:
        return COUNTRY_NAMES[cc.upper()]
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), (cc or "").replace("-", " "))


def site_flag(cc):
    if cc and len(cc) == 2:
        return "".join(chr(127397 + ord(c)) for c in cc.upper())
    return "\U0001f310"


def site_money(v):
    if v is None:
        return "\u2014"
    return "$%.2f" % v if v < 10 else "$" + format(int(round(v)), ",")


def site_acres(v):
    if v is None:
        return "\u2014"
    return "<0.01" if v < .01 else "%.2f" % v if v < 1 else "%.1f" % v if v < 100 else format(int(round(v)), ",")


# site/data.json row: [price_usd, acres, country, region, title, source, flag codes, url, lat, lon, image, kind]
PRICE, ACRES, CC, REGION, TITLE, SRC, FLAGS, URL, LAT, LON, IMG, KIND = range(12)
CASTLE_CARDS = 12
FIRST_CARDS = 24  # listing cards written into index.html, so the top of the grid needs no JavaScript


def site_clean(row):
    """Buyable at the listed price: no warning flags ('N' = no area listed is fine for a total-price ranking)."""
    return row[PRICE] is not None and row[FLAGS] in ("", "N")


def site_size(v):
    return "Size not listed" if v is None else "%s %s" % (site_acres(v), "acre" if v == 1 else "acres")


def site_card(r, rank):
    """One listing card. The page's JavaScript card() builds the same markup."""
    e = html.escape
    url = e(r[URL])
    img = ('<img src="%s" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="this.remove()">'
           % e(r[IMG])) if r[IMG] else ""
    place = ", ".join(x for x in (r[REGION], site_country(r[CC])) if x)
    title = r[TITLE] or "Land in " + place
    return (
        '<article class="card"><a class="ph" href="%s" target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">'
        '%s<span class="rk">#%d</span></a>'
        '<div class="bd"><p class="pr">%s</p><p class="sz">%s</p>'
        '<h3><a href="%s" target="_blank" rel="noopener">%s</a></h3>'
        '<p class="pl"><span aria-hidden="true">%s</span> %s</p>'
        '<p class="src">%s</p></div></article>'
    ) % (url, img, rank, site_money(r[PRICE]), site_size(r[ACRES]), url, e(title), site_flag(r[CC]), e(place),
         e(SOURCE_NAMES.get(r[SRC], r[SRC] or "")))


def site_cards(rows, n=FIRST_CARDS):
    c = sorted((r for r in rows if site_clean(r)), key=lambda r: r[PRICE])[:n]
    return "\n      ".join(site_card(r, i) for i, r in enumerate(c, 1))


def site_castles(rows, n=CASTLE_CARDS):
    """The "Cheapest castles" section, or nothing when there are no castles."""
    c = sorted((r for r in rows if len(r) > KIND and r[KIND] == "castle" and site_clean(r)), key=lambda r: r[PRICE])
    if not c:
        return ""
    cards = "\n      ".join(site_card(r, i) for i, r in enumerate(c[:n], 1))
    return (
        '<section id="castles"><div class="wrap">\n'
        '    <h2>Cheapest castles</h2>\n'
        '    <p class="lede">The cheapest of %s castles for sale, by total asking price. They\'re in the main ranking too, '
        'far down it.</p>\n'
        '    <div class="grid">\n      %s\n    </div>\n'
        '  </div></section>' % (format(len(c), ","), cards))


def site_hero(rows):
    """HTML for the hero: cheapest believable listing, plus the next five."""
    c = sorted((r for r in rows if site_clean(r)), key=lambda r: r[PRICE])
    if not c:
        return '<p class="kicker">No listings yet.</p>', "", ""
    top = c[0]
    e = html.escape
    place = ", ".join(x for x in (top[REGION], site_country(top[CC])) if x)
    size = ("%s %s in " % (site_acres(top[ACRES]), "acre" if top[ACRES] == 1 else "acres")) if top[ACRES] is not None else "A plot in "
    runners = []
    for r in c[1:6]:
        parts = (r[REGION] or "").split(",")
        if r[CC] in ("US", "CA"):
            label = parts[-2].strip() if len(parts) >= 2 and parts[-2].strip() else (r[REGION] or site_country(r[CC]))
        else:
            label = site_country(r[CC])
        ac = (", %s ac" % site_acres(r[ACRES])) if r[ACRES] is not None else ""
        runners.append('<a href="%s" target="_blank" rel="noopener"><b>%s</b> %s %s%s</a>' % (
            e(r[URL]), site_money(r[PRICE]), site_flag(r[CC]), e(label), e(ac)))
    body = (
        '<p class="kicker">The cheapest land you can buy on earth right now</p>\n'
        '    <h1 class="price"><span class="sign">%s</span><span class="per">total</span></h1>\n'
        '    <p class="where">%s%s %s</p>\n'
        '    <p class="facts">Listed on %s</p>\n'
        '    <a class="btn" href="%s" target="_blank" rel="noopener">Open the listing</a>\n'
        '    <div class="runnersup">%s</div>'
    ) % (site_money(top[PRICE]), e(size), e(place), site_flag(top[CC]), SOURCE_NAMES.get(top[SRC], top[SRC]),
         e(top[URL]), "".join(runners))
    headline = "%s buys %s%s" % (site_money(top[PRICE]), size if top[ACRES] is not None else "a plot in ", place)
    return body, headline, place


def site_ranks(rows, limit=30):
    """HTML for the by-country grid: cheapest believable listing per country, cheapest country first."""
    by = {}
    for r in rows:
        if not site_clean(r):
            continue
        b = by.setdefault(r[CC], {"min": r, "n": 0})
        b["n"] += 1
        if r[PRICE] < b["min"][PRICE]:
            b["min"] = r
    items = sorted(by.items(), key=lambda kv: kv[1]["min"][PRICE])[:limit]
    out = []
    for i, (cc, b) in enumerate(items, 1):
        m = b["min"]
        out.append(
            '<button class="rank" type="button" data-cc="%s"><span class="ri">%d</span>'
            '<span class="fl" aria-hidden="true">%s</span><span class="cn">%s</span>'
            '<span class="v">%s</span><span class="meta">%s · %s listings</span></button>'
            % (html.escape(cc), i, site_flag(cc), html.escape(site_country(cc)), site_money(m[PRICE]),
               html.escape(site_size(m[ACRES])), format(b["n"], ",")))
    return "\n    ".join(out)


def build_site(recs, fresh_days=3):
    """Write site/index.html + site/data.json: every listing seen in the last few days of runs."""
    os.makedirs(SITE, exist_ok=True)
    stamps = [r.get("scraped_at") for r in recs if r.get("scraped_at")]
    if not stamps:
        return
    newest = max(stamps)
    cutoff = (datetime.fromisoformat(newest) - timedelta(days=fresh_days)).isoformat()
    rows, countries = [], set()
    for r in recs:
        if (r.get("scraped_at") or "") < cutoff or r.get("price_usd") is None:
            continue
        codes = site_codes(r.get("flags"))
        if "W" in codes:
            continue
        rnd = lambda v, d: None if v is None else round(v, d)
        cc = (r.get("country") or "").upper() if len(r.get("country") or "") == 2 else (r.get("country") or "")
        countries.add(cc)
        rows.append([rnd(r["price_usd"], 0), rnd(r.get("acres"), 3), cc, (r.get("region") or "")[:60],
                     (r.get("title") or "")[:110], r.get("source"), codes, r["url"],
                     rnd(r.get("lat"), 4), rnd(r.get("lon"), 4), r.get("img") or None, r.get("kind")])
    stamp = datetime.now().astimezone()
    data = {"generated": stamp.isoformat(timespec="seconds"), "countries": len(countries), "rows": rows}
    with open(os.path.join(SITE, "data.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    write_site(rows, countries, stamp)
    log("site: %d listings from %d countries -> site/" % (len(rows), len(countries)))


def write_site(rows, countries, stamp):
    """Fill site_template.html with the hero, first listing cards, country grid and filters -> site/index.html."""
    hero, headline, place = site_hero(rows)
    options = "".join('<option value="%s">%s %s</option>' % (html.escape(cc), site_flag(cc), html.escape(site_country(cc)))
                      for cc in sorted(countries, key=site_country))
    srcs = sorted({r[SRC] for r in rows if r[SRC]}, key=lambda s: SOURCE_NAMES.get(s, s).lower())
    src_options = "".join('<option value="%s">%s</option>' % (html.escape(s), html.escape(SOURCE_NAMES.get(s, s)))
                          for s in srcs)
    names = [SOURCE_NAMES.get(s, s) for s in srcs] or list(SOURCE_NAMES.values())
    src_list = ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]
    updated = "Updated %s from %s live listings in %d countries." % (
        stamp.strftime("%B %-d, %Y at %-I:%M %p"), format(len(rows), ","), len(countries))
    description = ("Today's cheapest land on earth: %s. Every cheap plot for sale worldwide, "
                   "ranked by total price and updated daily from live listings." % headline) if headline else \
        "The cheapest pieces of land you can actually buy anywhere in the world, ranked by total price and updated daily from live listings."
    with open(os.path.join(HERE, "site_template.html")) as f:
        page = f.read()
    castles = site_castles(rows)
    for key, val in (("<!--HERO-->", hero), ("<!--CARDS-->", site_cards(rows)), ("<!--RANKS-->", site_ranks(rows)),
                     ("<!--CASTLES-->", castles),
                     ("<!--CASTLES_NAV-->", '<a href="#castles">Castles</a>' if castles else ""),
                     ("<!--COUNTRIES-->", options), ("<!--SOURCES-->", src_options),
                     ("{{SOURCE_LIST}}", html.escape(src_list)), ("{{SOURCE_COUNT}}", str(len(names))),
                     ("<!--UPDATED-->", html.escape(updated)), ("{{DESCRIPTION}}", html.escape(description, quote=True)),
                     ("{{ROWS}}", format(len(rows), ","))):
        assert key in page, "site_template.html is missing " + key
        page = page.replace(key, val)
    with open(os.path.join(SITE, "index.html"), "w") as f:
        f.write(page)
    open(os.path.join(SITE, ".nojekyll"), "w").close()


# ----------------------------------------------------------------------------- main

def csv_list(s, default):
    return [x.strip() for x in s.split(",") if x.strip()] if s else default


def main():
    ap = argparse.ArgumentParser(description="Find the cheapest land listings worldwide.")
    ap.add_argument("--sources", default=ALL_SOURCES, help="comma-separated, default: " + ALL_SOURCES)
    ap.add_argument("--pages", type=int, default=10, help="pages per country/state/province (20-25 listings each)")
    ap.add_argument("--countries", help="Properstar country slugs, comma-separated (default: all)")
    ap.add_argument("--states", help="US state slugs for LandWatch and Zillow, e.g. new-mexico,arizona (default: all)")
    ap.add_argument("--olx", help="OLX countries, e.g. PL,RO (default: %s)" % ",".join(OLX_SITES))
    ap.add_argument("--central-america", help="Encuentra24 countries, e.g. CR,PA (default: %s)" % ",".join(ENCUENTRA24_COUNTRIES))
    ap.add_argument("--prefectures", help="Japanese prefectures for HOME'S akiya bank, e.g. nagano,shimane (default: all)")
    ap.add_argument("--provinces", help="Kijiji provinces, e.g. nova-scotia,saskatchewan (default: all)")
    ap.add_argument("--workers", type=int, default=4, help="Chrome windows scraping in parallel")
    ap.add_argument("--tile", choices=["right", "full"], default="right",
                    help="tile the windows over the right half (default) or the whole screen")
    ap.add_argument("--hidden", action="store_true", help="no visible windows: one per worker, minimized to the Dock")
    ap.add_argument("--headless", action="store_true", help="fully headless Chrome (more likely to get blocked)")
    ap.add_argument("--delay", type=float, default=2.5, help="base seconds between page loads")
    ap.add_argument("--fresh", action="store_true", help="re-scrape pages already done")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--verify", type=int, default=60,
                    help="after scraping, open this many of the cheapest listings to re-check their price (0 = off)")
    ap.add_argument("--verify-only", action="store_true", help="only run the verification step, then rebuild outputs")
    a = ap.parse_args()

    store = Store()
    if a.fresh:
        store.done = set()
        open(store.prog_path, "w").close()
    if a.verify_only:
        verify_cheapest(store, a, a.verify)
    elif not a.report_only:
        sources = csv_list(a.sources, [])
        countries = csv_list(a.countries, PROPERSTAR_COUNTRIES)
        states = csv_list(a.states, US_STATES)
        provs = {p: KIJIJI_PROVINCES[p] for p in csv_list(a.provinces, list(KIJIJI_PROVINCES))}
        log("starting: sources=%s pages=%d (%d already-scraped listings on disk)" % (
            ",".join(sources), a.pages, len(store.urls)))
        olx = [c.upper() for c in csv_list(a.olx, list(OLX_SITES))]
        e24 = [c.upper() for c in csv_list(a.central_america, list(ENCUENTRA24_COUNTRIES))]
        unknown = set(sources) - set(ALL_SOURCES.split(","))
        if unknown:
            ap.error("unknown source(s): %s (choose from %s)" % (",".join(sorted(unknown)), ALL_SOURCES))
        plain = []  # sites that answer plain HTTP requests: one background thread each, beside the Chrome windows
        if "kijiji" in sources:
            plain.append(("kijiji", lambda: crawl_kijiji(Plain(delay=max(1.0, a.delay / 2)), store, provs, a.pages)))
        if "olx" in sources:
            plain.append(("olx", lambda: crawl_olx(Plain(delay=max(1.0, a.delay / 2)), store, olx, a.pages)))
        if "zillow" in sources:
            plain.append(("zillow", lambda: crawl_zillow(Plain(delay=a.delay), store, states, a.pages)))
        threads = []
        for name, fn in plain:
            def guarded(fn=fn, name=name):
                try:
                    fn()
                except Exception as e:
                    log("%s failed: %s" % (name, str(e)[:160]))
            threads.append(threading.Thread(target=guarded, name=name, daemon=True))
            threads[-1].start()
        try:
            prefs = csv_list(a.prefectures, HOMES_PREFECTURES)
            prefs = [next((h for h in HOMES_PREFECTURES if h.endswith("/" + x)), x) for x in prefs]  # "nagano" ok
            per_source = {"landwatch": states, "properstar": countries, "castles": countries,
                          "immobiliare": ["italia"], "encuentra24": e24,
                          "homes": ["%s:%s" % (pf, k) for pf in prefs for k in HOMES_CATEGORIES]}
            lists = [[(src, key) for key in keys] for src, keys in per_source.items() if src in sources]
            jobs = []
            for i in range(max([len(l) for l in lists] or [0])):  # interleave so no single site gets every window
                for l in lists:
                    jobs += l[i:i + 1]
            if jobs:
                log("%d browser jobs across %d Chrome windows" % (len(jobs), min(a.workers, len(jobs))))
                run_pool(jobs, store, a)
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(0.5)
            if a.verify and not STOP.is_set():
                verify_cheapest(store, a, a.verify)
        except KeyboardInterrupt:
            STOP.set()
            log("stopped by user - progress is saved; rerun the same command to resume")
    build_outputs(store)


if __name__ == "__main__":
    main()
