#!/usr/bin/env python3
"""
landfinder — scrape the cheapest land listings on earth and rank them by total price in USD.
Each site's houses-for-sale section is searched too, for ruins, fixer-uppers, old farmhouses and the odd castle.

Sources
  properstar    worldwide portal (~100+ countries), land plots sorted cheapest first
  landwatch     all 50 US states, sorted cheapest first
  kijiji        every Canadian province, land-for-sale sorted cheapest first
  olx           classifieds in Poland, Ukraine, Romania, Portugal, Bulgaria, Kazakhstan, Uzbekistan
  mercadolibre  classifieds in Argentina and Mexico
  ieichiba      Japanese owners selling empty houses (akiya) and land themselves
  homes         HOME'S akiya bank: land and empty houses in all 47 Japanese prefectures (houses ranked with the land)
  castles       Properstar's castle listings in the same countries as properstar
  zillow        lots and land in all 50 US states
  immobiliare   land for sale across Italy
  encuentra24   lots and land in Costa Rica, Panama, Nicaragua, Honduras, Guatemala and El Salvador

Usage
  ./run.sh                                       # everything: 250 believable listings per country/state/province
  ./run.sh --sources properstar --listings 500   # go deeper on one source
  ./run.sh --countries paraguay,bolivia          # only these countries (Properstar, OLX, Mercado Libre)
  ./run.sh --sources homes --prefectures nagano  # one Japanese prefecture on HOME'S akiya bank
  ./run.sh --report-only                         # rebuild results.csv / report.html from saved data

Output (in this folder)
  results.csv   every listing, cheapest total price first, with a "flags" column for suspicious prices
  report.html   open in a browser: sortable, filterable table of the same data
  coverage.csv  per country/state/province on each site: results the site reports, what we fetched, why we stopped
  data/         raw scraped records + progress, so a stopped run resumes where it left off
"""
import argparse, csv, gzip, json, math, os, queue, random, re, shutil, signal, subprocess, sys, threading, time, unicodedata, warnings
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from html import unescape

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

# OLX: country -> (site, land-for-sale category id, ISO country, currency of the site's price filter, USD floor).
# Below the floor nearly every "price" is per m², per sotka (100 m²) or a placeholder. It's lower where real
# village plots sell for a few hundred dollars, higher where sellers often quote a price per unit.
OLX_SITES = {
    "poland": ("www.olx.pl", 24, "PL", "PLN", 1500), "ukraine": ("www.olx.ua", 1608, "UA", "UAH", 1000),
    "romania": ("www.olx.ro", 709, "RO", "EUR", 1000), "portugal": ("www.olx.pt", 410, "PT", "EUR", 500),
    "bulgaria": ("www.olx.bg", 529, "BG", "EUR", 500), "kazakhstan": ("www.olx.kz", 10, "KZ", "KZT", 500),
    "uzbekistan": ("www.olx.uz", 10, "UZ", "UZS", 1500),
}

# Mercado Libre land-for-sale searches. Its other country sites make visitors sign in first.
MELI_SITES = {
    "argentina": ("https://inmuebles.mercadolibre.com.ar/terrenos-lotes/venta/", "AR", "ARS"),
    "mexico": ("https://inmuebles.mercadolibre.com.mx/terrenos/venta/", "MX", "MXN"),
}

# Each site's houses-for-sale section, searched cheapest first like the land, for ruins, fixer-uppers and old farmhouses.
# A house search stops sooner (--house-listings) so a full run doesn't take twice as long.
OLX_HOUSE_CATS = {"poland": 18, "ukraine": 1602, "romania": 911, "portugal": 4779, "bulgaria": 531, "kazakhstan": 2983,
                  "uzbekistan": 18}
OLX_LOT_KEYS = ("land_area", "ploshaduchastka", "yspace", "plot")  # a house ad's lot size; "m" there is the floor area
MELI_HOUSE_URLS = {"argentina": "https://inmuebles.mercadolibre.com.ar/casas/venta/",
                   "mexico": "https://inmuebles.mercadolibre.com.mx/casas/venta/"}
KINDS = ("land", "houses")

# Ieichiba (家いちば): Japanese owners selling empty houses (akiya) and land themselves, often for under ¥1M. Its
# listings are grouped in price bands, newest first; we take every live listing up to ¥10M (about $65k).
IEICHIBA_BANDS = ("0-100manyen", "100-200manyen", "200-1000manyen")

# Zillow's search URLs use the state's postal code
US_STATE_ABBR = dict(zip(US_STATES, """
AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR
PA RI SC SD TN TX UT VT VA WA WV WI WY""".split()))

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
HOMES_CATEGORIES = {"land": "102", "houses": "101"}  # for sale only; the 30x categories are rentals

ALL_SOURCES = "properstar,castles,landwatch,kijiji,olx,mercadolibre,ieichiba,homes,zillow,immobiliare,encuentra24"

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


SQFT = r"(?:sq\.?\s*ft|sqft|square\s*feet|ft²|ft2)"
# a building's floor area or minimum size, which isn't the lot size: "2,500 sq. ft. of living space",
# "1,200 sq ft bungalow", "home must be a minimum of 1100 sq ft"
FLOOR_AREA_RX = re.compile(r"\b(?:minimum|min\.|at least)\s+(?:of\s+)?\d[\d,]*\.?\d*\s*" + SQFT +
                           r"|\d[\d,]*\.?\d*\s*" + SQFT + r"\.?\s*(?:of\s+)?(?:living|finished|heated|house|bungalow"
                           r"|cottage|cabin|chalet|shop|garage|barn|warehouse|greenhouse|office|dwelling|residence|interior"
                           r"|floor|home(?!\s*sites?)|building(?!\s+(?:lot|site|land)))")


def acres_from_text(text):
    """Pull a land area out of free text ('320 acres', '12.5 ha', '9,000 sq ft', '632 m²')."""
    if not text:
        return None
    t = re.sub(r"https?://\S+", " ", text.lower())  # a link's "-chemin-des-jardins-" doesn't make the text French
    t = FLOOR_AREA_RX.sub(" ", t)
    romance = bool(re.search(r"\b(de|des|la|le|du|à|superficie|terrain|terre|hectares? de|à vendre|venta|terreno)\b", t))
    # "1/4 acre", "2 1/2 acres" -> "0.25 acre", "2.5 acres" (otherwise the regex reads only the "4" or the "2")
    t = re.sub(r"\b(?:(\d+)\s+)?(\d+)\s*/\s*([1-9]\d*)(?=\s*(?:acres?|ac\b|hectares?|ha\b|hect))",
               lambda m: "%g" % (int(m.group(1) or 0) + int(m.group(2)) / int(m.group(3))), t)
    pats = [
        (r"((?:\d[\d,]*)?\.?\d+)\s*(?:acres?|ac\b|acre lot)", 1.0),
        (r"([\d][\d,]*\.?\d*)\s*(?:hectares?|ha\b|hect)", 2.4710538),
        (r"([\d][\d,]*\.?\d*)\s*" + SQFT, 1 / 43560.0),
        (r"(\d{1,3}(?:\.\d{3})+(?:,\d+)?|[\d][\d,]*\.?\d*)\s*(?:m²|m2\b|sq\.?\s*m\b|square\s*met)", 1 / ACRE_M2),
    ]
    for pat, factor in pats:
        m = re.search(pat, t)
        if m:
            raw = m.group(1).rstrip(".,")
            # "1.250 m²" and "1.250,00 m²": a dot thousands separator. No plot is 1.25 m², so this is safe for m²
            # (not for hectares, where "1.250 ha" may really be 1.25 ha)
            if factor == 1 / ACRE_M2 and re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", raw):
                raw = raw.replace(".", "").replace(",", ".")
            # "5,62 ha" and French/Spanish "1,140 hectare" use a decimal comma; no plot is "13,712" = 13.7 sq ft or m².
            # Only for hectares: "1,000 acres" is a thousand in every language, and "LA" (Louisiana) sets `romance`
            elif re.search(r",\d{1,2}$", raw) or (romance and factor > 1 and re.fullmatch(r"\d{1,3},\d{3}", raw)):
                raw = raw.replace(".", "").replace(",", ".")
            v = num(raw)
            if v and v > 0:
                return v * factor
    return None


US_STATE_CODES = set("""AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY
NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR""".split())
ZIP_RX = re.compile(r"\b([A-Z]{2}),?\s+(\d{5})\b")
_ZIPS = {}


def zip_location(text):
    """(lat, lon) of the US ZIP code in an address like 'Deming, NM, 88030', from the Census ZIP code
    centers in geo/us_zip_centroids.csv.gz. LandWatch listings have no coordinates, only addresses."""
    if not _ZIPS:
        try:
            with gzip.open(os.path.join(HERE, "geo", "us_zip_centroids.csv.gz"), "rt") as f:
                for line in f:
                    z, lat, lon = line.strip().split(",")
                    if z.isdigit():
                        _ZIPS[z] = (float(lat), float(lon))
        except OSError:
            _ZIPS["-"] = None  # file missing: don't retry for every listing
    for state, z in ZIP_RX.findall(text or ""):
        if state in US_STATE_CODES and z in _ZIPS:
            return _ZIPS[z]
    return None


def load_fx():
    """USD exchange rates, cached for the day."""
    path = os.path.join(DATA, "fx.json")
    today = datetime.now().strftime("%Y-%m-%d")
    cached = None
    try:
        with open(path) as f:
            cached = json.load(f)
        if cached.get("date") == today:
            return cached["rates"]
    except (OSError, ValueError, KeyError, AttributeError):  # missing, or left half-written by a killed run
        cached = None
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=20)
        rates = r.json()["rates"]
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"date": today, "rates": rates}, f)
        os.replace(tmp, path)  # atomic: a run killed mid-write can't leave a truncated fx.json
        return rates
    except Exception as e:
        if cached and cached.get("rates"):
            log("FX fetch failed (%s); using cached rates" % e)
            return cached["rates"]
        raise


def to_usd(amount, currency, rates):
    if amount is None:
        return None
    cur = (currency or "USD").upper()
    rate = rates.get(cur)
    return amount / rate if rate else None


# English, French and Spanish, plus the OLX languages: Polish, Ukrainian/Russian, Romanian, Bulgarian, Portuguese, Uzbek
PAYMENT_RX = re.compile(r"(\d\s*(?:/\s*)?month\b|/\s*mo\b|/\s*month|per month|a month|monthly|\$\s*\d+\s*down|down payment"
                        r"|cuotas|anticipo|financiad|financiaci|\braty\b|розстроч|рассроч|presta[çc][õo]es)", re.I)
WANTED_RX = re.compile(r"^\s*(wanted|wtb|looking for|iso\b|in search of|i will buy|will buy|kupi[eę]\b|куплю|cump[aă]r\b|compro\b|busco\b|procuro\b)", re.I)
MIN_PRICE = 1500  # USD-ish floor for Properstar and Mercado Libre searches; cheaper "prices" there are per-m2 or placeholders
TILE_SCALE = 0.5  # tiled windows draw pages at half size
CLAIM_RX = re.compile(r"\b(access deeds?|membership|timeshare|mining claims?|lode claims?|placer claims?|gold claims?|mineral claims?|mineral rights?|unpatented|lode gold|mining|prospecting)\b", re.I)
LEASE_RX = re.compile(r"(rent[- ]to[- ]own|\bfor lease\b|\b(?:sub-?)?lease[ds]?\b|\bleasing\b|\bfor rent\b|\brental\b|à louer|\btenants?\b|/\s*month|\bmonthly\b|en alquiler|se alquila|\barriendo\b"
                      r"|dzier[żz]aw|wynaj[eę]|do wynaj|оренд|аренд|\bздам\b|\bздаю\b|\bсдам\b|\bсдаю\b|под наем|[iî]nchiri|arrend|\balug|\bijara|\ben renta\b|\bse renta\b|\bin affitto\b|\baffittasi\b)", re.I)
PER_UNIT_RX = re.compile(r"(\bx|\bper|\bpor|/|\bel|\ble|\bpar|\bthe)\s*(m2\b|m²|mt2|mts2|metro\b(?!\s*(?:area|region|city|station|stop|line|bus|rail|access|transit))|sq\.?\s*m\b|vara|acre\b|hect|ha\b|sotk|square met)"
                         r"|(?:(?:\bza|\bза|\bpe|\bper|\bx)\s+|/\s*)(?:1\s*)?(m2\b|m²|mp\b|metr|сот|м2|м²|кв\.?\s*м|ar\b|ara\b)"
                         r"|\b(?:so|su)[‘'ʻ’`]?t(?:ig[‘'ʻ’`]?i|ixi|kasi)\s*\$?\s*\d|сотк[аи]\s*[-–—:]?\s*\$?\s*\d", re.I)  # Uzbek/Russian "its sotka [costs] 500"
# "1/8 udziału", "udział 1/4 w prawie własności", "1/2 interest": only part of the property is for sale
SHARE_RX = re.compile(r"(\b\d+\s*/\s*\d+\s*(?:udzia|cz[eę][sś]ci|w drodze|w prawie|частк|долю|доли|долі|share|interest)|udzia[łl]\w*\s+\d+\s*/\s*\d+|\(\s*1\s*/\s*\d+\s*\)"
                      r"|^\s*udzia[łl]|sprzeda\w*\s+udzia[łl]|udzia[łl]\w*\s+w\s+(?:działk|nieruchom|grunc|prawie)"
                      # "1/3 Ruína em ...", "Cota de 5/16 din casa", "1/2 идеална част от къща"
                      r"|^\s*[1-9]\s*/\s*(?:[2-9]|[1-9]\d)\s(?!\s*-?\s*(?:acres?|ac\b|ha\b|hect|miles?|mi\b|sq|m2|m²|lots?|sections?|of an? acre))|\bcota(?:\s*-?\s*parte|\s+de\s+\d)|\bquota[- ]parte|идеалн\w*\s+част)", re.I)  # not "+ udział w drodze"
ALLOTMENT_RX = re.compile(r"\brod\b|ogr[oó]d\w*\s+działkow|działkow\w*\s+ogr[oó]d", re.I)  # Polish allotment gardens (ROD)
CEMETERY_RX = re.compile(r"cemit[eé]rio|sepultur|jazigo|cementerio|cmentarz|cimitir|кладбищ|цвинтар|гробищ|\bnicho\b|b[oó]veda"
                         r"|cemetery\s+(?:plots?|lots?|spaces?|sites?)|private cemetery|burial|\bgrave\b|columbari", re.I)
NEAR_RX = re.compile(r"\b(near|next to|close to|junto|perto|cerca|frente|al lado|l[aâ]ng[aă]|около|біля|возле|koło|obok|przy|rua|calle|strada|ulica|ul\.|вул|ул\.)", re.I)
USD_IN_TEXT_RX = re.compile(r"(?:usd|u\$s|us\$|u\$d|d[oó]lares)\s*([\d.,]{3,})", re.I)  # "Lote ... Usd 880.000" priced in pesos


def placeholder_price(p):
    if p is None:
        return False
    d = str(int(p)) if float(p).is_integer() else ""
    return p <= 100 or (len(d) >= 3 and len(set(d)) == 1) or d in ("1234", "12345", "123456")


# one exact price under $5,000 on 5+ different-size plots is a placeholder or a per-m2 price, not a real total
PLACEHOLDER_UNDER_USD, PLACEHOLDER_SIZES = 5000, 5
AUCTION_RX = re.compile(r"\b(auction|tender|bid|starting bid)\b|przetarg|licytac|licita[tț]i|аукці|аукци|\bторги\b|subasta|leil[aã]o|leilões|all'asta|\basta giudiziaria", re.I)


def burial_plot(title):
    m = CEMETERY_RX.search(title or "")
    return bool(m) and not NEAR_RX.search(title[max(0, m.start() - 20):m.start()])  # "Rua do Cemitério" is a street


# sites where owners really do sell (or give away) houses and land for next to nothing, so a tiny or round price
# is the price: Japanese owners of empty houses pay to keep them and will take ¥0 or ¥10,000 to be rid of them
CHEAP_IS_REAL = ("ieichiba", "homes")
AKIYA = ("ieichiba", "homes")  # empty houses rank with the land: on these sites the house is what you buy


def flag(rec):
    f = []
    usd, acres = rec.get("price_usd"), rec.get("acres")
    real = rec.get("source") in CHEAP_IS_REAL
    # title_en: the English translation of a non-English title (see add_english), so English checks apply to it too
    text = " ".join(str(rec.get(k) or "") for k in ("title", "title_en", "description"))
    if WANTED_RX.search(rec.get("title") or "") or WANTED_RX.search(rec.get("title_en") or ""):
        f.append("wanted ad - not a listing")
    if usd is not None and usd < 300 and not real:
        f.append("price too low - likely monthly payment or per-m2 price")
    elif usd is not None and usd < 2000 and PAYMENT_RX.search(text):
        f.append("may be payment plan price")
    if CLAIM_RX.search(text):
        f.append("mining claim/mineral rights/access deed - not land ownership")
    if SHARE_RX.search(rec.get("title") or "") or SHARE_RX.search(rec.get("title_en") or ""):
        f.append("share of a property - not the whole plot")
    if burial_plot(rec.get("title")) or burial_plot(rec.get("title_en")):
        f.append("burial plot - not land you can use")
    if usd and (rec.get("currency") or "USD") != "USD":
        if any((num(n.replace(".", "")) or 0) > usd * 3 for n in USD_IN_TEXT_RX.findall(rec.get("title") or "")):
            f.append("wrong currency - the title gives a much higher US dollar price")
    if rec.get("source") == "olx" and rec.get("country") == "PL" and ALLOTMENT_RX.search(text):
        f.append("allotment garden plot - usage rights, not land ownership")
    if (rec.get("kind") == "houses" and rec.get("source") not in AKIYA) or "homes-for-sale" in (rec.get("url") or ""):
        f.append("house listing - not vacant land")
    if rec.get("source") == "kijiji" and usd is not None and usd < 3000:
        f.append("price too low for Canada - likely a deposit, lease or placeholder")
    if rec.get("source") == "properstar" and usd is not None and usd < 1500:
        f.append("price may be per m2/acre/hectare (agency listing under $1,500)")
    if LEASE_RX.search(text):
        f.append("lease/rental - not a sale price")
    if placeholder_price(rec.get("price")) and not real:
        f.append("placeholder price")
    if PER_UNIT_RX.search(text):
        f.append("price may be per m2/acre/hectare")
    if AUCTION_RX.search(text):
        f.append("auction/tender - price may be a minimum bid")
    if not acres:
        f.append("no land area listed")
    elif rec.get("usd_per_acre") is not None and rec["usd_per_acre"] < 10 and not f and not real:
        f.append("check: under $10/acre")
    return "; ".join(f)


# flags that don't stop a listing from showing on the site: a missing area, and (from a house search) being a house
OK_FLAGS = ("no land area listed", "house listing - not vacant land")


def believable_flags(flags):
    return all(f in OK_FLAGS for f in (flags or "").split("; ") if f)


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


# What kind of property a listing is, for the site cards and results.csv. Every source is scraped from its
# land-for-sale section, so condos and townhouses don't come up: the question is what's on the land.
LANDWATCH_TYPES = {"homes": "house", "homesite": "building lot", "farms-and-ranches": "farmland", "horse-property": "farmland",
                   "recreational-property": "recreational", "hunting-property": "recreational", "timberland-property": "forest",
                   "commercial-property": "commercial", "lakefront-property": "waterfront"}  # anything else: vacant land
LANDWATCH_TYPE_RX = re.compile(r"-(%s)-for-sale/" % "|".join(LANDWATCH_TYPES))
LANDWATCH_BEDS_RX = re.compile(r"\|\s*\d+\s*(?:beds?|baths?)\s*\||\|\s*[\d,]+\s*sqft\s*\|", re.I)  # "| 3 beds | • | 1 baths |" on the card
# a word between "with" and the building that doesn't mean it's only an idea: "with room for a home", "this lot is perfect for a cabin"
_WORD = (r"(?:(?!(?:for|to|your|future|dream|potential|room|space|plenty|ideal|perfect|great|build|building|views?|access|of|no|"
         r"place|property|land|lot|parcel|acreage|tract|spot|site|area|para|pour|projet|proyecto|projeto|possib\w*|posibil\w*|"
         r"autoriza\w*|proiect|pentru|под|для|без|bez|pod|former|ruin\w*|collapsed|burn(?:ed|t)|бывш\w*|колишн\w*|паднал\w*|"
         r"срутен\w*|разрушен\w*)\b)\S+\s+)")
# a house, cabin or other home already on the plot ("with a log cabin", "z domem", "с домом", "uyi bor"), not one sellers
# suggest building: "build your dream cottage" or "perfect for a cabin" is still vacant land
HOUSE_RX = re.compile(
    r"\b(?:with|w/|includes?|including|plus|has|existing|this|there is|there's)\s+" + _WORD + "{0,5}?"
    r"(?:(?<!well )(?<!pump )(?<!court )(?<!tree )(?<!bath )(?<!guard )(?<!storage )(?<!green )(?<!hoop )(?<!hen )(?<!chicken )"
    r"(?<!dog )(?<!boat )(?<!club )(?<!pool )house|home|farmhouse|cabin|cottage|chalet|bungalow|camp|mobile home|manufactured home"
    r"|modular home|yurt|dwelling|residence)\b"
    # not "Cabin/Home Site", "home plans", "House Creek", nor a word cut off where LandWatch shortens the description ("home | $124,900")
    r"(?!\s*(?:/\s*\w+\s*)?(?:sites?|plans?|projects?|permits?|potential|lots?|owners?|community|building|construct\w*|materials?|kits?|to\b|allowed"
    r"|ok\b|friendly|welcome|permitted|hook-?ups?|pads?|parking|creek|road|rd|street|st|lane|ln|drive|dr|avenue|ave|way|trail|hollow"
    r"|ridge|lake|river|mountain|\|))"
    r"|\b(?:house|home|cabin|cottage|farmhouse|chalet|bungalow)\s+(?:was\s+)?(?:originally\s+)?built in (?:18|19|20)\d\d\b"
    r"|\bthe\s+(?:existing\s+|old\s+)?(?:house|home|cabin|cottage|farmhouse|chalet|bungalow|mobile home)\s+(?:is|has|was|needs|sits|features)\b"
    r"|\bfixer[- ]upper|\bhandyman special"
    r"|\bavec\s+" + _WORD + r"{0,3}?(?:maison|chalet|camp|roulotte|b[aâ]tisse|r[ée]sidence|cabane)\b"
    r"|\b(?:con|incluye)\s+" + _WORD + r"{0,3}?(?:casa|caba[ñn]a|vivienda)\b|\bcasa\s+(?:con|y|más)\s+terreno"
    r"|\bcom\s+" + _WORD + r"{0,3}?(?:casa|moradia|vivenda)\b|\b(?:moradia|casa|vivenda)\s+t\d\b"
    r"|\bz\s+" + _WORD + r"{0,3}?(?:domem|domkiem)\b|\bdom\s+z\s+(?:\S+\s+)?działk|\bzabudowan|\bsiedlisk(?:o|a|iem)\b"
    r"|\bз\s+" + _WORD + r"{0,3}?(?:будинком|будиночком|хатою)|\b(?:є|стоїть)\s+" + _WORD + r"?(?:будинок|хата)\b"
    r"|\bбудинок\s+з\s+(?:\S+\s+)?ділянк"
    r"|\bс(?:ъс)?\s+" + _WORD + r"{0,3}?(?:домом|домиком|къща|вила)\b|\b(?:есть|стоит|имеется)\s+" + _WORD + r"?(?:дом|домик)\b"
    r"|\bдом\s+(?:с|на)\s+(?:\S+\s+)?участк|\bжилой дом\b|\bстара къща"
    r"|\bcu\s+" + _WORD + r"{0,3}?(?:cas[aă]|caban[aă])\b"
    r"|\buyi\s+bor|\buy\s+bilan|\bhovlili\b|\bhovli\s+sotiladi|\d+\s*xonali\s+(?:uy|hovli)|уйи\s+бор|уй\s+билан"
    r"|(?:ҳовли|ховли)\s+(?:\S+\s+){0,2}сотилади", re.I)
# just before a building: it's planned, ruled out or the neighbour's ("move an existing home", "approval for a T4 house",
# "não ... com casa", "подходяща за изграждане на къща с двор", "граничи с къща")
PLANNED_RX = re.compile(r"\b(?:build|move|bring|place|put|add|call|make|for|para|pour|pentru|за|под|для|під|pod|projet\w*|proyecto|projeto|plans?"
                        r"|construc\w*|budow\w*|изграждане|строител\w*|строительств\w*|будівництв\w*|граничи\w*|рядом|поруч"
                        r"|possib\w*|posibil\w*|variant\w*|schimb\w*|exchange|обмен\w*|обмін\w*|troca|permuta"
                        r"|not?|não|sin|nu|без|bez|ні|нет)\W+(?:\S+\s+){0,3}$", re.I)


def has_house(text):
    return any(not PLANNED_RX.search(text[max(0, m.start() - 40):m.start()]) for m in HOUSE_RX.finditer(text))
# a plot in a dacha or garden cooperative (Kazakhstan, Ukraine, Uzbekistan), with or without a summer house
DACHA_RX = re.compile(r"\bдач[аиуеыі]\b|\bдачн|\bсадов\w*\s+(?:участ|товарищ|общест)|\bсадівн|\bсадовод|\bdacha\b", re.I)
COMMERCIAL_RX = re.compile(
    r"\bcommercial(?:ly)?[- ](?:vacant\s+)?(?:lot|land|property|parcel|zon\w*|use|building lot|development)|\bhighway commercial"
    r"|\bzoned (?:commercial|industrial|c-?\d)\b|\bindustrial[- ](?:lot|land|park|property|parcel|zon\w*|use)|\blight industrial|\bmixed[- ]use"
    r"|\bterrain (?:commercial|industriel)|\b(?:terreno|lote|uso|zona)s?\s+(?:comercial|industrial)|\bteren\s+(?:industrial|comercial)"
    r"|\b(?:działk\w*|teren\w*|grunt\w*)\s+inwestycyjn|\b(?:przemysłow|usługow|komercyjn)\w*"
    r"|\b(?:комерційн|промислов)\w*\s+(?:призначення|нерухом|ділянк|забудов|об)|\bпід\s+комерці"
    r"|\b(?:коммерческ|промышленн)\w*\s+(?:назначени|недвижим|участ|объект)|\bпод\s+(?:бизнес|коммерци)"
    r"|\bпромишлен|\bтърговск|\bиндустриал", re.I)
LOT_RX = re.compile(
    r"\b(?:building|residential|city|town|village|serviced|subdivision|condominium|house|cottage|duplex)[- ](?:lots?|sites?)\b"
    r"|\b(?:building|residential) land\b|\bvacant lots?\b|\bhome ?sites?\b|\blots? in (?:a |the )?(?:condominium|subdivision)"
    r"|\bzoned (?:residential|r-?\d)\b|\bservices? (?:at|to) the lot|\bterrain (?:r[ée]sidentiel|constructible|[àa] b[aâ]tir)|\blot r[ée]sidentiel"
    r"|\blotes?\b|\bloteamento|\bterrenos? urbanos?|\burbaniza|\bpara (?:construir|constru[cç][aã]o)"
    r"|\bbudowlan|\bpod zabudow|\bwarunk\w* zabudowy"
    r"|\bИЖС\b|\bІЖБ\b|\bИЖБ\b|\bпод\s+(?:строительств|дом\b|застройк)|\bдля\s+(?:строительств|будівництв)|\bпід\s+(?:забудов|будівництв)"
    r"|\bжитлов\w+\s+забудов|\bintravilan|\bconstruibil|\bloc de cas|\bteren de construc|\bpentru construc"
    r"|\bУПИ\b|\bурегулиран|\bв регулация|\bдворно място", re.I)
FARM_RX = re.compile(
    r"\bfarm(?:land|s)?\b(?![- ](?:road|rd|lane|ln|drive|dr|street|st|market))|\branch(?:land|es)?\b(?![- ]?(?:style|home|house|road|rd))"
    r"|\bpasture|\bhay ?field|\bcrop ?land|\btillable|\bagricultural|\borchard|\bvineyard"
    r"|\bferme\b|\bterres? agricoles?|\bfinca\b|\bchacra|\bestancia|\bganader|\bagr[ií]cola|\brancho\b|\bherdade|\br[uú]stic[oa]s?\b"
    r"|\bolival|\bvinha\b|\broln\w*|\brolnicz|\bgospodarstw|\błąk[aię]\b|\bpastwisk"
    r"|\bОСГ\b|\bсільськогосп|\bсільгосп|\bпа[йї]\b|\bфермер|\bсельхоз|\bКФХ\b|\bЛПХ\b|\bпашн|\bсенокос"
    r"|\bextravilan|\barabil|\bagricol|\bf[aâ]na[tț]|\bp[aă][sș]un|\blivad|\bземеделск|\bнив[аи]\b|\bливад|\bлозе\b|\bfermer", re.I)
REC_RX = re.compile(
    r"\brecreation(?:al)?[- ](?:lot|land|property|parcel|acreage)|\brec lot|\bhunting[- ](?:land|property|tract|camp|acreage|parcel|ground)"
    r"|\bcampground|\bterrain de camping|\brv (?:lot|park|site)\b|\brekreacyjn|\bletniskow|\bрекреац|\bбаза отдыха|\bдля отдыха", re.I)
FOREST_RX = re.compile(
    r"\btimber ?land|\btimber tract|\bwood ?lot|\bforest ?land|\bterre [àa] bois|\b[ée]rabli[èe]re|\bsugar ?bush|\bmaple bush"
    r"|\bdziałk\w* leśn|\bgrunt\w* leśn|\bleśn\w* działk|\bteren forestier|\bлісов\w+ ділянк|\bлесн\w+ участ|\bгорск\w+ (?:територ|имот|земя)", re.I)


def property_type(rec):
    """LandWatch's own category (from the URL, or a house when the card lists beds, baths or square feet), otherwise
    the first match in the title, its English translation and the description. Free: no Claude calls."""
    if rec.get("kind") == "houses":  # from a site's houses-for-sale section
        return "house"
    # Properstar's description is only "Land Plot for sale, <place>", and place names like "Finca Gloriana" would mislead
    keys = ("title", "title_en") if rec.get("source") == "properstar" else ("title", "title_en", "description")
    text = " ".join(str(rec.get(k) or "") for k in keys)
    if rec.get("source") == "landwatch":
        m = LANDWATCH_TYPE_RX.search(rec.get("url") or "")
        kind = LANDWATCH_TYPES[m.group(1)] if m else "vacant land"
        if kind != "house" and (LANDWATCH_BEDS_RX.search(text) or has_house(text)):
            return "land with house"
        return kind
    if has_house(text):
        return "land with house"
    for kind, rx in (("dacha plot", DACHA_RX), ("commercial", COMMERCIAL_RX), ("building lot", LOT_RX), ("farmland", FARM_RX),
                     ("recreational", REC_RX), ("forest", FOREST_RX)):
        if rx.search(text):
            return kind
    return "vacant land"


# Special listings for the site's "Ruins & castles" filter, from free keyword rules (no Claude calls). A listing can
# have several: a ruined chapel is a ruin and a church.
# ruin: a ruin, an abandoned or derelict building, or a fixer-upper that needs a full renovation
RUIN_RX = re.compile(
    r"\bruin(?:s|ed)?\b|\bderelict\b|\babandoned\s+(?:\S+\s+)?(?:house|home|farmhouse|building|property|church|chapel|school|cabin"
    r"|cottage|barn|mill|hotel|village|homestead)\b|\bfixer[- ]?upper|\bhandyman(?:'s)? special|\btear[- ]?down\b|\buninhabitable"
    r"|\bneeds?\s+(?:a\s+)?(?:complete|total|full|major|extensive|lots of|some)?\s*(?:renovation|restoration|rebuild\w*|tlc|work)\b"
    r"|\b(?:renovation|restoration) project|\bto (?:renovate|restore)\b"
    r"|\bruines?\b|\b[àa] (?:r[ée]nover|restaurer|r[ée]habiliter)\b|\babandonn[ée]e?s?\b"  # French
    r"|\b(?<!cop[áa]n )ru[ií]nas?\b|\b(?:para|a) (?:reformar|restaurar|rehabilitar|reconstruir|recuperar|remodelar|reabilitar)\b"
    r"|\babandonad[ao]s?\b|\bdevolut[oa]s?\b|\b(?:precisa|necessita) de obras\b"  # Spanish, Portuguese
    r"|\bruder[ei]\b|\bda ristrutturare\b|\bdiroccat\w*"  # Italian
    r"|\bruine\b|\b(?:renovierungs|sanierungs)bed[üu]rftig|\babri(?:ss|ß)objekt|\babbruchhaus"  # German
    r"|\bruin[aă]\b|\bdo (?:kapitalnego |generalnego )?remontu\b|\bdo rozbi[óo]rki\b|\bopuszczon\w*|\bpustostan"  # Polish
    r"|\bde (?:renovat|demolat|reabilitat)\b|\bnecesit[aă] (?:renovare|repara[țt]ii)\b|\babandonat[aă]?\b"  # Romanian
    r"|\b(?:під|под|на) (?:знос|снос|разбор|розбір)\b|\bруїн\w*|\bруин\w*|\bразвалин\w*|\bзанедбан\w*|\bзаброшен\w*"
    r"|\bпокинут\w*|\bаварі?[йи]н\w*|\b(?:требует|потребує|нуждае се от)\s+(?:капитального\s+|капітального\s+|основен\s+)?ремонт"
    r"|\bза (?:ремонт|събаряне|реконструкция)\b|\bизоставен\w*|\bпорутен\w*|\bсрутен\w*"  # Ukrainian, Russian, Bulgarian
    r"|\bsnosga\b|\bbuzib olish"  # Uzbek: for demolition
    r"|空き家|空家|古民家|廃屋|廃墟|要修繕|要リフォーム|老朽|雨漏り|床(?:が)?抜け|傷みが|劣化|要解体|取り壊し|解体(?:が|を)?前提", re.I)  # Japanese
# castle: a castle, château, manor house or palace. Not a place named after one ("Castle Rock, CO", "Castelo Branco",
# "Château-Richer") or a street ("Rue du Château")
_PLACE_AFTER = r"(?!-|\s+(?-i:[A-Z])|\s+(?:de|do|da|dos|das|di|del|d')\b)"
CASTLE_RX = re.compile(
    r"\bcastles?\b(?!\s+(?:rock|dale|valley|hills?|creek|pines|park|peak|gate|danger|hayne|bay|road|rd|st|street|ave|lane|ln"
    r"|drive|dr|estates?|heights|crest|ridge|mountain|lake|view|point|county)\b)"
    r"|\bch[aâ]teaux?\b(?!-|\s+d'eau)|\bmanoir\b|\bmanor house\b|\bschloss\b|\bburg(?:anlage|ruine)\b"
    r"|\bcastel(?:o|lo)?\b(?!\s+(?:branco|novo|melhor|rodrigo|bom)\b)" + _PLACE_AFTER + r"|\bcastillo\b" + _PLACE_AFTER + r"|\bpal[áa]cio\b" + _PLACE_AFTER +
    r"|\bpalacete\b|\bdworek\b|\bconac\b|\bзамък\b|\bдворец\b|\bпалац\b", re.I)
# church: a church or chapel building for sale, not one nearby ("next to the church") or a street ("Church St")
CHURCH_RX = re.compile(
    r"\b(?:former|old|historic|converted|deconsecrated|decommissioned|abandoned|disused|redundant|vacant|stone|brick|wooden"
    r"|country|little|small|1[789]\d\ds?|century)\s+(?:\S+\s+)?(?:church|chapel|meeting ?house)\b"
    r"(?!\s+(?:rd|road|st|street|ave|lane|ln|drive|dr|hill|way|cemetery|camp)\b)"
    r"|\b(?:church|chapel)\s+(?:building|for sale|property|conversion|hall|sanctuary)\b|^\s*(?:church|chapel)\b(?!\s+(?:rd|road|st|street|hill))"
    r"|\bancienne?\s+(?:[ée]glise|chapelle|presbyt[èe]re)|\b[ée]glise\s+(?:d[ée]sacralis[ée]e|[àa] vendre)|\bchapelle\s+[àa] vendre"
    r"|\b(?:antigua|vieja|ex)[- ]?(?:iglesia|capilla)\b|\b(?:iglesia|capilla)\s+en venta\b"
    r"|\b(?:antiga|velha|ex)[- ]?(?:igreja|capela)\b|\b(?:igreja|capela)\s+(?:[àa] venda|para venda)\b"
    r"|\bex[- ]?chiesa\b|\bchiesa sconsacrata|\bchiesetta\b|\bentweihte kirche|\bkirche zu verkaufen"
    r"|\bdawn\w+\s+(?:ko[śs]ci[óo][łl]|kaplic|zb[óo]r)|\b(?:бывш|колишн)\w*\s+(?:церк|храм|костел|молитвен|часовн|каплиц)"
    r"|\b(?:fost[aă]|veche)\s+biseric|\bbiseric[aă]\s+de v[aâ]nzare|\bбивш\w*\s+(?:църква|храм|параклис)", re.I)
# just before a match: it's nearby, seen from the plot, ruled out or a sandcastle, not the property itself
NEARBY_RX = re.compile(r"(?:n['’]t|\b(?:near|nearby|next to|beside|behind|across from|opposite|close to|walk to|walking distance|minutes? (?:from|to)"
                       r"|from the|by the|views? (?:of|to)|overlook\w*|sand|bouncy|bounce|mayan?|roman|inca|ancient|archa?eolog\w*"
                       r"|no|not|without|não|sem|sin|nie|без|не|junto (?:a|ao|al)|perto d\w|cerca de\w*|frente a\w*|vista (?:para|al|a)\w*"
                       r"|pr[eè]s d\w*|rue|route|chemin|avenue|all[ée]e|impasse|obok|blisko|koło|рядом|поруч|біля|возле|lâng[aă]|style\w*|styl\w*"
                       r"|w stylu|type|like|such as|interesse|como))\W+(?:\S+\s+){0,2}$", re.I)
# just after a match: a street or place name ("Chateau Drive", "Church Lane", "Pedro del Castillo 790", "Conac Archia")
AFTER_RX = re.compile(r"^(?:,\s*(?-i:[A-Z]{2})\b|\s+(?:place|pl|drive|dr|road|rd|street|st|avenue|ave|lane|ln|way|court|ct|blvd|circle|hwy|highway)\b|\s*\d"
                      r"|-|\s+(?-i:(?!(?:For|In|With|And|On|Of|At|To|Sale|Property|Estate|House|Home|Ruins?|Building|Conversion)\b)[A-Z][a-z]))", re.I)
# a capitalised word just before a capitalised castle word makes it a name ("New Castle", "Viana do Castelo", "Lion Castle"),
# unless it's a word that describes the castle
NAME_BEFORE_RX = re.compile(r"(?-i:\b(?!(?:Medieval|Historic|Old|Ancient|Restored|Renovated|Ruined|French|Scottish|Irish|English|Italian"
                            r"|Spanish|Portuguese|German|Charming|Beautiful|Magnificent|Small|Little|Fairytale|Stone|For|Sale|A|The)\b)"
                            r"[A-Z][\w'’.]*\s+(?:(?:do|da|de|del|di|of|la|le)\s+)?)$")
TAG_RULES = (("ruin", RUIN_RX), ("castle", CASTLE_RX), ("church", CHURCH_RX))


def _tag_hit(tag, t, m):
    before, after = t[max(0, m.start() - 40):m.start()], t[m.end():m.end() + 20]
    if NEARBY_RX.search(before):
        return False
    if tag == "ruin":
        return not re.match(r"\s+by\b", after)  # "a view that won't be ruined by ..."
    if AFTER_RX.match(after):
        return False
    if tag == "castle" and re.search(r"\b(?:do|da|de|del|di)\s+$", before):  # "penalva do castelo" is a town
        return False
    return not (tag == "castle" and m.group(0)[:1].isupper() and NAME_BEFORE_RX.search(before))


def special_tags(rec):
    """Which of ruin / castle / church a listing is, from its title, English title and description."""
    keys = ("title", "title_en") if rec.get("source") == "properstar" else ("title", "title_en", "description")
    texts = [str(rec.get(k) or "") for k in keys]
    return [tag for tag, rx in TAG_RULES if any(_tag_hit(tag, t, m) for t in texts for m in rx.finditer(t))]


def finalize(rec, rates):
    down_only = rec.get("source") == "landwatch" and fix_down_payment(rec)
    if rec.get("source") == "kijiji":  # re-read area with the current parser (fixes older scrapes)
        a = acres_from_text(rec.get("title")) or acres_from_text(rec.get("description"))
        if a:
            rec["acres"] = a
    if rec.get("source") == "landwatch" and rec.get("lat") is None:  # place it at its ZIP code's center
        ll = zip_location(rec.get("title")) or zip_location(rec.get("description"))
        if ll:
            rec["lat"], rec["lon"] = ll
    rec["price_usd"] = to_usd(rec.get("price"), rec.get("currency"), rates)
    a = rec.get("acres")
    rec["acres"] = round(a, 4) if a else None
    rec["usd_per_acre"] = (rec["price_usd"] / a) if (rec["price_usd"] and a) else None
    rec["flags"] = flag(rec)
    rec["type"] = property_type(rec)
    rec["tags"] = ",".join(special_tags(rec))
    if down_only:
        rec["flags"] = "; ".join(x for x in (rec["flags"], "may be payment plan price - listed price is the down payment") if x)
    return rec


# ----------------------------------------------------------------------------- storage

class Store:
    """Append-only JSONL of listings + set of finished page URLs, so runs resume.
    A listing seen again on a later run is appended again; the newest line per URL wins, which keeps
    prices current and lets the site drop listings that haven't been seen lately.

    It also keeps a tally per search (one country, state or province on one site) in searches.jsonl: what each
    page added, the site's own result count and why the search stopped. A search stops once it has enough
    believable listings, so a resumed run needs the tally, and the coverage report is built from it."""

    def __init__(self):
        os.makedirs(DATA, exist_ok=True)
        self.path = os.path.join(DATA, "listings.jsonl")
        self.prog_path = os.path.join(DATA, "pages_done.txt")
        self.search_path = os.path.join(DATA, "searches.jsonl")
        self.urls = set()
        if os.path.exists(self.path):
            for line in open(self.path):
                try:
                    self.urls.add(json.loads(line)["url"])
                except Exception:
                    pass
        self.done = set(open(self.prog_path).read().split()) if os.path.exists(self.prog_path) else set()
        self.searches = {}
        if os.path.exists(self.search_path):
            for line in open(self.search_path):
                try:
                    self._tally(json.loads(line))
                except Exception:
                    pass
        self.rates = None  # set by main() before crawling, to judge which listings are believable
        self.lock = threading.Lock()

    def reset(self):
        """--fresh: forget finished pages and search tallies (the listings stay)."""
        self.done, self.searches = set(), {}
        open(self.prog_path, "w").close()
        open(self.search_path, "w").close()

    def _tally(self, e):
        """Apply one searches.jsonl entry (a page's counts, or why the search stopped) to its search's tally."""
        s = self.searches.setdefault(e["search"], {"pages": 0, "listings": 0, "big": 0, "cheap": {},
                                                   "total": None, "reached": None, "stop": None})
        if "stop" in e:
            s["stop"] = e["stop"]
            return
        s["pages"] += 1
        s["listings"] += e["n"]
        s["big"] += e["big"]
        for price, acres, ok in e["cheap"]:
            g = s["cheap"].setdefault(price, [set(), 0])
            g[0].add(acres)
            g[1] += ok
        if e.get("total") is not None:
            s["total"] = e["total"]
        if e.get("mid") is not None:
            s["reached"] = e["mid"]
        s["stop"] = None  # still going

    def believable(self, sid):
        """Listings of a search the site would show: no flags but a missing area, and not one of the
        placeholder prices that the same rule as build_outputs catches within the search."""
        s = self.searches.get(sid)
        if not s:
            return 0
        return s["big"] + sum(ok for sizes, ok in s["cheap"].values() if len(sizes) < PLACEHOLDER_SIZES)

    def page(self, sid, url, recs, total=None):
        """Save one search page: its listings, what it adds to the search's tally, and the page as done.
        total = how many results the site says the search has. Returns how many listings are new."""
        for r in recs:
            r["search"] = sid
        new = self.add(recs)
        METRICS.add("parsed", source=sid.split()[0], search=sid, n=len(recs), new=new)
        big, cheap, usd = 0, [], []
        for r in recs:
            f = finalize(dict(r), self.rates)
            ok = f["price_usd"] is not None and believable_flags(f["flags"])
            if f["price_usd"] is not None:
                usd.append(f["price_usd"])
            if f.get("price") and (f["price_usd"] or 0) < PLACEHOLDER_UNDER_USD:
                cheap.append([round(f["price"], 2), f.get("acres"), int(ok)])
            else:
                big += ok
        # results come cheapest first, so the middle price of the latest page is how far up the market the search got
        e = {"search": sid, "url": url, "n": len(recs), "big": big, "cheap": cheap, "total": total,
             "mid": round(sorted(usd)[len(usd) // 2], 2) if usd else None}
        with self.lock:
            self._tally(e)
            with open(self.search_path, "a") as f:
                f.write(json.dumps(e) + "\n")
        self.mark(url)
        return new

    def stop(self, sid, why):
        """Record why a search stopped: enough listings, end of results, page cap or fetch failed."""
        e = {"search": sid, "stop": why}
        with self.lock:
            self._tally(e)
            with open(self.search_path, "a") as f:
                f.write(json.dumps(e) + "\n")

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


# ----------------------------------------------------------------------------- metrics

BROWSER_USE_HOUR, PROXY_GB, DIRECT_GB = 0.02, 5.0, 0.20  # Browser Use cloud prices, USD (checked 2026-11-02)


def source_of(url):
    return next((s for s in ("properstar", "landwatch", "kijiji", "olx", "mercadolibre", "ieichiba") if s in url), "other")


def mins(secs):
    return "%dm%02ds" % divmod(int(secs), 60)


class Metrics:
    """Timings and bytes for every page fetched, every Chrome window and every phase of a run. Each event goes to
    logs/metrics-<time>.jsonl as it happens; summary() adds them up at the end to show where the time and data go."""

    def __init__(self):
        self.events, self.lock, self.t0, self.file = [], threading.Lock(), time.time(), None
        self.path = os.path.join(HERE, "logs", "metrics-%s.jsonl" % datetime.now().strftime("%Y-%m-%d-%H%M%S"))

    def add(self, kind, **e):
        e = dict(kind=kind, t=round(time.time() - self.t0, 2), worker=threading.current_thread().name, **e)
        with self.lock:
            self.events.append(e)
            if self.file is None:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self.file = open(self.path, "a")
            self.file.write(json.dumps(e) + "\n")
            self.file.flush()

    @contextmanager
    def phase(self, name):
        t = time.time()
        try:
            yield
        finally:
            self.add("phase", name=name, secs=round(time.time() - t, 2))

    def summary(self):
        """What the run spent its time and bandwidth on, as lines of text."""
        ev = list(self.events)
        fetches = [e for e in ev if e["kind"] == "fetch"]
        out = ["run took %s; phases: %s" % (mins(time.time() - self.t0), ", ".join(
            "%s %s" % (e["name"], mins(e["secs"])) for e in ev if e["kind"] == "phase"))]

        # seconds per page, by stage: nav = loading the page (or the HTTP request), ready = waiting for the listings
        # to appear, read = copying the HTML out, delay = polite pause before the next page, waits = blocks and retries
        listed = {}
        for e in ev:
            if e["kind"] == "parsed":
                listed.setdefault(e["source"], []).append(e["n"])
        fmt = "%-28s %5s %4s %6s | %5s %5s %5s %5s %5s | %7s %7s %4s | %6s"
        out += ["", fmt % ("fetches", "pages", "fail", "median", "nav", "ready", "read", "delay", "waits",
                           "KB/page", "MB", "req", "list/p")]
        groups = {}
        for e in fetches:
            groups.setdefault((e["via"], e["source"], e["what"]), []).append(e)
        for (via, src, what), es in sorted(groups.items()):
            n = len(es)
            avg = lambda k: "%.1f" % (sum(e.get(k) or 0 for e in es) / n)
            lp = listed.get(src) if what == "search" else None
            out.append(fmt % ("%s %s %s" % (via, src, what), n, sum(not e["ok"] for e in es),
                              "%.1fs" % sorted(e["secs"] for e in es)[n // 2], avg("nav_s"), avg("ready_s"),
                              avg("read_s"), avg("delay_s"), avg("wait_s"),
                              "%.0f" % (sum(e["bytes"] for e in es) / n / 1024), "%.1f" % (sum(e["bytes"] for e in es) / 1e6),
                              avg("requests") if via == "chrome" else "-", "%.0f" % (sum(lp) / len(lp)) if lp else "-"))

        bad = Counter()
        for e in fetches:
            for b in e.get("blocked") or []:
                bad["%s blocked (%s)" % (e["source"], b)] += 1
            if e.get("errors"):
                bad["%s fetch errors" % e["source"]] += e["errors"]
            if e.get("ready_hit") is False:
                bad["%s listings never appeared (waited 20s)" % e["source"]] += 1
            for st in e.get("statuses") or [e.get("status")]:
                if st and st != 200:
                    bad["%s HTTP %s" % (e["source"], st)] += 1
        if bad:
            out += ["", "problems: " + "; ".join("%s x%d" % kv for kv in bad.most_common())]

        jobs = sorted((e for e in ev if e["kind"] == "job"), key=lambda e: -e["secs"])
        if jobs:
            out += ["", "slowest browser jobs: " + ", ".join("%s %s %s (%d pages)" % (e["source"], e["key"], mins(e["secs"]),
                                                                                    e["pages"]) for e in jobs[:5])]

        wins = [e for e in ev if e["kind"] == "window"]
        http = sum(e["bytes"] for e in fetches if e["via"] == "http")
        if wins:
            hours = sum(w["open_s"] for w in wins) / 3600
            gb = sum(w["bytes"] for w in wins) / 1e9
            pages = sum(w["pages"] for w in wins)
            types, hosts = Counter(), Counter()
            for w in wins:
                types.update(w["types"])
                hosts.update(w.get("hosts") or {})
            out += ["", "chrome: %d windows, %.2f browser-hours, %.1fs to launch on average, busy %d%% of the time open "
                        "(the rest is idle at the end of the queue), %d pages, %.1f MB = %.0f KB/page" % (
                            len(wins), hours, sum(w["launch_s"] for w in wins) / len(wins),
                            100 * sum(w["busy_s"] for w in wins) / max(1, hours * 3600), pages, gb * 1e3,
                            gb * 1e6 / max(1, pages)),
                    "  downloaded by type: " + ", ".join("%s %.1f MB" % (t, b / 1e6) for t, b in types.most_common(7)),
                    "  biggest type + host: " + ", ".join("%s %.1f MB" % (h, b / 1e6) for h, b in hosts.most_common(12))]
            browser, proxy, direct = hours * BROWSER_USE_HOUR, gb * PROXY_GB, gb * DIRECT_GB
            out.append("  Browser Use cloud for this run: browser $%.3f + residential proxy $%.3f = $%.3f "
                       "(direct instead of proxy: $%.3f); per 1,000 Chrome pages: $%.2f" % (
                           browser, proxy, browser + proxy, browser + direct, (browser + proxy) / max(1, pages) * 1000))
        out.append("plain HTTP: %.1f MB" % (http / 1e6))
        return out

    def report(self):
        lines = self.summary()
        with open(self.path.replace(".jsonl", "-summary.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        for line in lines:
            log(line)
        log("metrics: %s (+ -summary.txt)" % os.path.relpath(self.path, HERE))


METRICS = Metrics()


# ----------------------------------------------------------------------------- fetchers

# JS checks that a page holds what its parser reads, so Browser.get can read it as soon as it's there
PROPERSTAR_READY = "() => [...document.scripts].some(s => s.textContent.includes('__INITIAL_STATE__'))"
LANDWATCH_READY = "() => !!document.querySelector('div[data-qa-placard]')"
LANDWATCH_AD_READY = ("() => [...document.scripts].some(s => s.type === 'application/ld+json'"
                      " && s.textContent.includes('RealEstateListing'))")


# Ads, analytics, maps, sign-in widgets, monitoring, web fonts and LandWatch's own scripts (its listings are in the
# HTML): about 88% of what Chrome downloaded, none of it needed to read the listings (measured with --test, 945 ->
# 110 KB a page). Blocked in Chrome itself, so its cache keeps working. --load-all turns this off.
BLOCK_URLS = ["*%s*" % h for h in (
    "googletagmanager.com", "google-analytics.com", "maps.googleapis.com", "mapsresources-pa.googleapis.com",
    "maps.gstatic.com", "doubleclick.net", "googlesyndication.com", "adtrafficquality.google", "facebook.net",
    "facebook.com/tr", "accounts.google.com", "go-mpulse.net", "id5-sync.com", "criteo.com", "criteo.net",
    "licdn.com", "listhub.net", "mxpnl.com", "msecnd.net", "pingdom.net", "fonts.gstatic.com", "fonts.googleapis.com",
    ".woff2", ".woff", ".ttf", ".otf", "landwatch.com/*.js")]


DRIVERS = set()  # Playwright driver processes of open Browsers; whatever is left at the end of a run gets stopped


def kill_tree(pid):
    """Kill a process and everything it started (a Playwright driver and its Chrome)."""
    pids, i = [pid], 0
    while i < len(pids):
        out = subprocess.run(["pgrep", "-P", str(pids[i])], capture_output=True, text=True).stdout
        pids += [int(p) for p in out.split()]
        i += 1
    for p in reversed(pids):
        try:
            os.kill(p, signal.SIGKILL)
        except Exception:
            pass


class Browser:
    """Real Google Chrome via Playwright. Several big portals block plain scripts and headless
    browsers, so by default a visible Chrome window opens and is reused for every page."""

    BLOCK_TITLES = ("access denied", "service unavailable", "just a moment", "attention required",
                    "are you a robot", "pardon our interruption", "azure waf", "verification required")

    def __init__(self, headless=False, delay=2.5, slot=None, block=True):
        """slot = (x, y, w, h) to show a small tiled window; None = off-screen and minimized.
        block = skip downloading BLOCK_URLS."""
        from playwright.sync_api import sync_playwright
        self.delay = delay
        self.headless = headless
        self.slot = slot
        self.label = ""
        self.block = block
        self.net = {"bytes": 0, "requests": 0, "types": Counter(), "hosts": Counter()}
        self.pages, self.busy, t = 0, 0.0, time.time()
        self.pw = sync_playwright().start()
        try:
            self.driver = self.pw._impl_obj._connection._transport._proc.pid  # Playwright internals (1.60)
            DRIVERS.add(self.driver)
        except Exception:
            self.driver = None
        self._launch()
        self.opened, self.launch_s = time.time(), time.time() - t

    def _launch(self):
        headless = self.headless
        args = ["--disable-blink-features=AutomationControlled",
                "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--blink-settings=imagesEnabled=false"]  # the parsers only read text, so pages load much lighter
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
        self._watch_network()
        if not headless and not self.slot:
            self._minimize()

    def _watch_network(self):
        """Count the bytes Chrome downloads (compressed, as they come over the network), by resource type, through
        the DevTools protocol. Iframes from other sites run in their own process and aren't counted."""
        types = {}
        self.doc_status = None

        def got(p):
            host = re.sub(r"^https?://([^/:]+).*", r"\1", (p.get("response") or {}).get("url") or "")
            types[p["requestId"]] = (p.get("type") or "Other", host)
            if p.get("type") == "Document" and self.doc_status is None:  # the page itself, not an iframe
                self.doc_status = (p.get("response") or {}).get("status")

        def done(p):
            n = int(p.get("encodedDataLength") or 0)
            self.net["bytes"] += n
            self.net["requests"] += 1
            kind, host = types.pop(p["requestId"], ("Other", ""))
            self.net["types"][kind] += n
            self.net["hosts"]["%s %s" % (kind, host)] += n

        try:
            cdp = self.ctx.new_cdp_session(self.page)
            cdp.send("Network.enable")
            if self.block:
                cdp.send("Network.setBlockedURLs", {"urls": BLOCK_URLS})
            cdp.on("Network.responseReceived", got)
            cdp.on("Network.loadingFinished", done)
        except Exception as e:
            log("  can't count Chrome's downloads (%s)" % str(e)[:80])

    def _minimize(self):
        """Minimize the Chrome window to the Dock so it never covers the screen."""
        try:
            cdp = self.ctx.new_cdp_session(self.page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "minimized"}})
        except Exception as e:
            log("  could not minimize Chrome window (%s)" % str(e)[:80])

    def get(self, url, ready=None, settle_ms=3500, what="search"):
        """The page's HTML. ready = a JS function that's true once the page holds what its parser reads: the page
        is then read right away (or after 20 s), instead of after a fixed wait and the whole page loading.
        Records how long each stage took and what it downloaded (what = "search" or "verify")."""
        t0 = t = time.time()
        b0, r0, types0 = self.net["bytes"], self.net["requests"], Counter(self.net["types"])
        secs = dict.fromkeys(("nav", "ready", "read", "delay", "wait"), 0.0)
        html, blocked, errors, ready_hit, stage = None, [], 0, None, "nav"

        def lap(name):  # the time since the last lap went to this stage
            nonlocal t
            now = time.time()
            secs[name] += now - t
            t = now

        for attempt in range(3):
            try:
                self.doc_status, stage = None, "nav"
                self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
                lap("nav")
                stage = "ready"
                if ready:
                    ready_hit = self._wait_for(ready, 20)
                else:
                    self.page.wait_for_timeout(settle_ms)
                    try:  # bot checks sometimes reload the page once
                        self.page.wait_for_load_state("load", timeout=20000)
                    except Exception:
                        pass
                lap("ready")
                stage = "read"
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
                    blocked.append(title[:40])
                    time.sleep(wait)
                    lap("wait")
                    continue
                html = self.page.content()
                self._show_label()
                lap("read")
                time.sleep(self.delay + random.random() * self.delay)
                lap("delay")
                break
            except Exception as e:
                lap(stage)
                errors += 1
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
                lap("wait")
        # bytes still arriving after the page was read are counted with the next page; window totals are exact
        self.pages += 1
        self.busy += time.time() - t0
        METRICS.add("fetch", via="chrome", source=source_of(url), what=what, url=url, ok=html is not None,
                    attempts=attempt + 1, blocked=blocked, errors=errors, status=self.doc_status, ready_hit=ready_hit,
                    secs=round(time.time() - t0, 2), html_kb=len(html) // 1024 if html else 0,
                    bytes=self.net["bytes"] - b0, requests=self.net["requests"] - r0,
                    types=dict(self.net["types"] - types0), **{k + "_s": round(v, 2) for k, v in secs.items()})
        return html

    def _wait_for(self, ready, seconds):
        end = time.time() + seconds
        while time.time() < end:
            try:
                if self.page.evaluate(ready):
                    return True
            except Exception:
                pass  # the page is navigating, e.g. a bot check reloading it
            self.page.wait_for_timeout(250)
        return False

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
        """Close Chrome. With several windows open, Playwright sometimes hangs for minutes after Chrome has already
        quit, and ignores a polite stop, so if closing takes over 15 s its driver process is killed and the run goes on."""
        driver = self.driver

        def stop_driver():
            kill_tree(driver)
            log("  Chrome didn't close within 15s; stopped it and its Playwright driver")

        METRICS.add("window", launch_s=round(self.launch_s, 2), open_s=round(time.time() - self.opened, 2),
                    busy_s=round(self.busy, 2), pages=self.pages, bytes=self.net["bytes"],
                    requests=self.net["requests"], types=dict(self.net["types"]), hosts=dict(self.net["hosts"]))
        timer = threading.Timer(15, stop_driver)
        timer.daemon, timer.name = True, threading.current_thread().name  # logs under the window's own name
        if driver:
            timer.start()
        try:
            for step in (self.browser.close, self.pw.stop):
                try:
                    step()
                except Exception:
                    pass
        finally:
            timer.cancel()
            DRIVERS.discard(driver)


class Plain:
    def __init__(self, delay=1.5):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

    def get(self, url, what="search"):
        t0, html, statuses, nbytes = time.time(), None, [], 0
        secs = dict.fromkeys(("nav", "delay", "wait"), 0.0)
        for attempt in range(3):
            t = time.time()
            try:
                r = self.s.get(url, timeout=30)
                secs["nav"] += time.time() - t
                statuses.append(r.status_code)
                nbytes += int(r.headers.get("Content-Length") or len(r.content))  # compressed size when the site sends it
                if r.status_code == 200:
                    t = time.time()
                    time.sleep(self.delay + random.random() * self.delay)
                    secs["delay"] += time.time() - t
                    html = r.text
                    break
                log("  HTTP %d (attempt %d)" % (r.status_code, attempt + 1))
                if r.status_code == 404:
                    break
            except Exception as e:
                secs["nav"] += time.time() - t
                log("  fetch error %s" % str(e)[:120])
            t = time.time()
            time.sleep(10 * (attempt + 1))
            secs["wait"] += time.time() - t
        METRICS.add("fetch", via="http", source=source_of(url), what=what, url=url, ok=html is not None,
                    attempts=attempt + 1, statuses=statuses, secs=round(time.time() - t0, 2), bytes=nbytes,
                    **{k + "_s": round(v, 2) for k, v in secs.items()})
        return html


# ----------------------------------------------------------------------------- sources

def search_id(source, key, kind):
    """'olx poland' for a land search, 'olx poland houses' for the same country's houses."""
    return "%s %s%s" % (source, key, "" if kind == "land" else " " + kind)


def search_pages(store, sid, a, cap=None, kind="land"):
    """Page numbers to fetch for one search, cheapest first, until it has a.listings believable listings
    (a.house_listings for houses) or reaches the page cap. The caller stops sooner at the end of the results or
    a failed fetch, and records why."""
    want = a.listings if kind == "land" else a.house_listings
    if (store.searches.get(sid) or {}).get("stop") == "end of results":
        return  # finished on an earlier run that this one resumes
    for p in range(1, min(a.pages, cap or a.pages) + 1):
        if STOP.is_set():
            return
        if store.believable(sid) >= want:
            store.stop(sid, "enough listings")
            return
        yield p
    if not STOP.is_set():
        store.stop(sid, "enough listings" if store.believable(sid) >= want else "page cap")


def parse_properstar(html, slug, kind="land"):
    """Listings on a Properstar search page and its result count; (None, 0) if the page has no search data."""
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>", html, re.S)
    if not m:
        return None, 0
    try:
        res = json.loads(m.group(1))["search"]["results"]
    except Exception:
        return None, 0
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
            "kind": kind,
        })
    return out, int(res.get("total") or 0)


def properstar_url(slug, p, kind="land"):
    if kind == "castles":  # Properstar's castle subtype: /buy/castle on its own isn't a real search and lists everything
        url = "https://www.properstar.com/%s/buy/house/castle/price-low-to-high" % slug
        return url + ("?p=%d" % p if p > 1 else "")
    # price.min skips the "EUR 5" listings that are really per-m2 prices or placeholders
    url = "https://www.properstar.com/%s/buy/%s/price-low-to-high?price.min=%d" % (
        slug, "land-plot" if kind == "land" else "house", MIN_PRICE)
    return url + ("&p=%d" % p if p > 1 else "")


def crawl_properstar_one(br, store, slug, a, kind="land"):
    """kind: land, houses, or castles (the castle subtype of houses, so castles beyond the cheapest houses show up)."""
    src = "castles" if kind == "castles" else "properstar"
    sid, total_new = search_id(src, slug, "land" if kind == "castles" else kind), 0
    for p in search_pages(store, sid, a, kind="land" if kind == "land" else "houses"):
        url = properstar_url(slug, p, kind)
        if url in store.done:
            continue
        br.label = "%s %s  page %d  (properstar)" % (slug, kind, p)
        html = br.get(url, ready=PROPERSTAR_READY)
        if not html:
            store.stop(sid, "fetch failed")
            break
        recs, total = parse_properstar(html, slug, "houses" if kind == "castles" else kind)
        if recs is None:  # a bot check or a new page layout, not the end of the results
            store.stop(sid, "fetch failed")
            break
        total_new += store.page(sid, url, recs, total)
        if not recs or p * 20 >= total:
            store.stop(sid, "end of results")
            break
    return total_new


def parse_landwatch(html, state, kind="land"):
    """Listings on a LandWatch search page, and the state's listing count ("79,070 Listings")."""
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
            "kind": kind,
        })
    m = re.search(r">\s*([\d,]+)\s+Listings?\s*<", html)
    return out, (int(num(m.group(1))) if m else None)


def crawl_landwatch_one(br, store, st, a, kind="land"):
    sid, total_new = search_id("landwatch", st, kind), 0
    for p in search_pages(store, sid, a, kind=kind):
        url = "https://www.landwatch.com/%s-land-for-sale/%ssort-price-low-high" % (st, "" if kind == "land" else "homes/")
        if p > 1:
            url += "/page-%d" % p
        if url in store.done:
            continue
        br.label = "%s %s  page %d  (landwatch)" % (st, kind, p)
        html = br.get(url, ready=LANDWATCH_READY)
        if not html:
            store.stop(sid, "fetch failed")
            break
        recs, total = parse_landwatch(html, st, kind)
        if not recs and total is None:
            # no listings and no "N Listings" count: a bot check or an error page, not the end of the results.
            # Leave it unmarked so a rerun retries it (at worst a real last page gets fetched again)
            log("landwatch %s %s page %d: no results data on the page (blocked?), skipping the rest" % (st, kind, p))
            store.stop(sid, "fetch failed")
            break
        total_new += store.page(sid, url, recs, total)
        if not recs:
            store.stop(sid, "end of results")
            break
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
            br = Browser(headless=a.headless, delay=a.delay, slot=slots[i], block=not a.load_all)
            while not STOP.is_set():
                try:
                    src, key, kind = q.get_nowait()
                except queue.Empty:
                    break
                fn = BROWSER_CRAWLERS[src]
                t, pages = time.time(), br.pages
                try:
                    new = fn(br, store, key, a, kind)
                except Exception as e:
                    log("%s %s %s failed: %s" % (src, key, kind, str(e)[:120]))
                    new = 0
                done[0] += 1
                METRICS.add("job", source=src, key=key, section=kind, secs=round(time.time() - t, 2), pages=br.pages - pages, new=new)
                log("%-10s [%d/%d] %-28s +%d listings" % (src, done[0], total, key if kind == "land" else key + " " + kind, new))
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


BROWSER_CRAWLERS = {  # sources that need a real Chrome window: fn(browser, store, key, a, kind) -> new listings
    "properstar": crawl_properstar_one,
    "castles": lambda br, store, key, a, kind: crawl_properstar_one(br, store, key, a, "castles"),
    "landwatch": crawl_landwatch_one,
    "immobiliare": lambda br, store, key, a, kind: crawl_search(br, store, "immobiliare", key, a, immobiliare_url,
                                                                parse_immobiliare),
    "encuentra24": lambda br, store, key, a, kind: crawl_search(br, store, "encuentra24", key, a, encuentra24_url,
                                                                parse_encuentra24),
    "homes": lambda br, store, key, a, kind: crawl_search(br, store, "homes", key, a, homes_url, parse_homes, kind),
}


def kijiji_acres(v):
    """Land area of one Kijiji ad: its "Size (acres)" field, else what the title or description says.
    Search pages keep the field in canonicalValues, an ad's own page in values."""
    acres = None
    for at in ((v.get("attributes") or {}).get("all") or []):
        vals = at.get("canonicalValues") or at.get("values")
        if at.get("canonicalName") == "areainacres" and vals:
            acres = num(vals[0])
    return acres or acres_from_text(v.get("title")) or acres_from_text(v.get("description"))


def parse_kijiji(html, prov, kind="land"):
    """Listings on a Kijiji search page, and the province's result count."""
    soup = BeautifulSoup(html, "lxml")
    nd = soup.find("script", id="__NEXT_DATA__")
    if not nd:
        return [], None
    try:
        apollo = json.loads(nd.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except Exception:
        return [], None
    total = next((v["pagination"].get("totalCount") for k, v in (apollo.get("ROOT_QUERY") or {}).items()
                  if k.startswith("searchResultsPageByUrl") and isinstance((v or {}).get("pagination"), dict)), None)
    out = []
    for k, v in apollo.items():
        if not (isinstance(v, dict) and "price" in v and "title" in v and v.get("url")):
            continue
        pr = v.get("price") or {}
        amt = pr.get("amount")
        if pr.get("type") != "FIXED" or not amt:
            continue
        acres = kijiji_acres(v)
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
            "kind": kind,
        })
    return out, total


def crawl_kijiji(fetch, store, provinces, a, kinds=("land",)):
    jobs = [(k, prov, loc) for k in kinds for prov, loc in provinces.items()]
    for pi, (kind, prov, loc) in enumerate(jobs, 1):
        sid, total_new, prev = search_id("kijiji", prov, kind), 0, set()
        cat = "b-land-for-sale/%s/%sc641" if kind == "land" else "b-house-for-sale/%s/%sc35"
        for p in search_pages(store, sid, a, kind=kind):
            seg = "" if p == 1 else "page-%d/" % p
            url = "https://www.kijiji.ca/" + cat % (prov, seg) + "%s?sort=priceAsc" % loc
            if url in store.done:
                continue
            html = fetch.get(url)
            if not html:
                store.stop(sid, "fetch failed")
                break
            recs, total = parse_kijiji(html, prov, kind)
            if not recs and "__APOLLO_STATE__" not in html:
                # a bot check or a changed layout, not a results page: leave it unmarked so a rerun retries it
                log("kijiji %s %s page %d: no results data on the page (blocked?), skipping the rest" % (prov, kind, p))
                store.stop(sid, "fetch failed")
                break
            urls = {r["url"] for r in recs}
            if not recs or urls <= prev:  # Kijiji repeats the last page past the end
                store.page(sid, url, [], total)
                store.stop(sid, "end of results")
                break
            prev = urls
            total_new += store.page(sid, url, recs, total)
        if STOP.is_set():
            return
        log("kijiji     [%d/%d] %-28s +%d listings" % (pi, len(jobs), sid.split(" ", 1)[1], total_new))


def olx_area_m2(params, keys=("m", "area_de_terreno_m2", "space", "land_area")):
    """Land area from OLX listing params: m², or sotkas (100 m²) on the ex-Soviet sites."""
    for k in keys:
        v = params.get(k) or {}
        n = num(v.get("key"))
        if n:
            return n * 100 if re.search(r"сот|sot", (v.get("label") or "").lower()) else n
    return None


def parse_olx(data, cc, kind="land"):
    out = []
    for o in data.get("data") or []:
        params = {p.get("key"): p.get("value") or {} for p in o.get("params") or []}
        pr = params.get("price") or {}
        if pr.get("type") not in ("price", "arranged") or not pr.get("value") or not o.get("url"):
            continue  # skips "free", "exchange" and price-on-request ads
        m2 = olx_area_m2(params) if kind == "land" else olx_area_m2(params, OLX_LOT_KEYS)
        loc = o.get("location") or {}
        geo = o.get("map") or {}
        desc = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", o.get("description") or ""))).strip()
        out.append({
            "source": "olx",
            "country": cc,
            "region": ", ".join(n for n in ((loc.get(k) or {}).get("name") for k in ("city", "region")) if n),
            "title": o.get("title"),
            "description": desc[:1500],
            "price": pr["value"],
            "currency": "USD" if pr.get("currency") == "UYE" else pr.get("currency"),  # UYE: Uzbek "conventional units" = US dollars
            "acres": (m2 / ACRE_M2) if m2 else None,
            "lat": geo.get("lat"), "lon": geo.get("lon"),
            "url": o["url"],
            "kind": kind,
        })
    return out


def crawl_olx(fetch, store, countries, a, rates, kinds=("land",)):
    jobs = [(k, c) for k in kinds for c in countries]
    for i, (kind, c) in enumerate(jobs, 1):
        site, cat, cc, cur, usd_floor = OLX_SITES[c]
        if kind != "land":  # under $1,000 a "house" is nearly always a rental, a share or a garden shed
            cat, usd_floor = OLX_HOUSE_CATS[c], max(usd_floor, 1000)
        sid, floor, total_new = search_id("olx", c, kind), int(usd_floor * rates.get(cur, 1)), 0
        for p in search_pages(store, sid, a, cap=25, kind=kind):  # the API stops at 1,000 results
            url = ("https://%s/api/v1/offers/?offset=%d&limit=40&category_id=%d"
                   "&sort_by=filter_float_price:asc&filter_float_price:from=%d" % (site, (p - 1) * 40, cat, floor))
            if url in store.done:
                continue
            try:
                data = json.loads(fetch.get(url) or "")
            except ValueError:
                store.stop(sid, "fetch failed")
                break
            recs = parse_olx(data, cc, kind)
            total_new += store.page(sid, url, recs, (data.get("metadata") or {}).get("visible_total_count"))
            if not recs or not (data.get("links") or {}).get("next"):
                store.stop(sid, "end of results")
                break
        if STOP.is_set():
            return
        log("olx        [%d/%d] %-28s +%d listings" % (i, len(jobs), sid.split(" ", 1)[1], total_new))


def meli_number(n):
    """A Mercado Libre number in either convention: Argentina writes '6.179' (thousands) and '1,5', Mexico
    writes '1,000' and '1.5'. A separator followed by exactly 3 digits groups thousands; otherwise it's a decimal."""
    n = n.strip(".,")
    if "." in n and "," in n:  # '1.250,50' or '1,250.50': the last separator is the decimal one
        dec = "," if n.rfind(",") > n.rfind(".") else "."
        return num(n.replace("." if dec == "," else ",", "").replace(",", "."))
    sep = "." if "." in n else "," if "," in n else None
    if sep and (n.count(sep) > 1 or re.search(r"\%s\d{3}$" % sep, n)):
        return num(n.replace(sep, ""))
    return num(n.replace(",", "."))


def meli_acres(attrs):
    """'6.179 m² totales' or '10 ha totales' -> acres. Built area ('161 m² cubiertos') is skipped."""
    for n, unit, kind in re.findall(r"([\d.,]+)\s*(m²|ha\b)\s*(\w*)", attrs):  # \b: '3 habitaciones' isn't 3 ha
        if kind.lower().startswith(("cubiert", "constru", "útil", "util")):
            continue
        v = meli_number(n)
        if v:
            return v * 2.4710538 if unit == "ha" else v / ACRE_M2
    return None


def parse_meli(html, cc, kind="land"):
    """Results embedded in a Mercado Libre search page -> (listings, page count, result count);
    (None, 0, None) if there are none."""
    i = html.find("_n.ctx.r=")
    if i < 0:
        return None, 0, None
    try:
        st = json.JSONDecoder().raw_decode(html, i + 9)[0]["appProps"]["pageProps"]["initialState"]
    except Exception:
        return None, 0, None
    out = []
    for res in st.get("results") or []:
        pc = res.get("polycard") or {}
        url = (pc.get("metadata") or {}).get("url")
        comp = {c.get("type"): c.get(c.get("type")) or {} for c in pc.get("components") or []}
        price = (comp.get("price") or {}).get("current_price") or {}
        if not url or not price.get("value"):
            continue
        title = (comp.get("title") or {}).get("text") or ""
        attrs = " | ".join((comp.get("attributes_list") or {}).get("texts") or [])
        out.append({
            "source": "mercadolibre",
            "country": cc,
            "region": (comp.get("location") or {}).get("text") or "",
            "title": title,
            "description": " | ".join(t for t in (title, attrs) if t),
            "price": price["value"],
            "currency": price.get("currency"),
            "acres": meli_acres(attrs),
            "lat": None, "lon": None,
            "url": (url if url.startswith("http") else "https://" + url).split("#")[0],
            "kind": kind,
        })
    total = ((st.get("melidata_track") or {}).get("event_data") or {}).get("total")
    return out, int((st.get("pagination") or {}).get("page_count") or 0), total


def crawl_meli(fetch, store, countries, a, rates, kinds=("land",)):
    jobs = [(k, c) for k in kinds for c in countries]
    for i, (kind, c) in enumerate(jobs, 1):
        base, cc, cur = MELI_SITES[c]
        if kind != "land":
            base = MELI_HOUSE_URLS[c]
        sid, floor, total_new = search_id("mercadolibre", c, kind), int(MIN_PRICE * rates.get(cur, 1)), 0
        for p in search_pages(store, sid, a, kind=kind):
            # cheapest first, above the floor (in local currency); page n starts at result 48*(n-1)+1
            url = "%s%s_OrderId_PRICE_PriceRange_%d-0_NoIndex_True" % (base, "_Desde_%d" % ((p - 1) * 48 + 1) if p > 1 else "", floor)
            if url in store.done:
                continue
            recs, page_count, total = parse_meli(fetch.get(url) or "", cc, kind)
            if recs is None:
                log("  mercadolibre %s: no results on the page (blocked, sign-in page or new layout)" % c)
                store.stop(sid, "fetch failed")
                break
            total_new += store.page(sid, url, recs, total)
            if not recs or p >= page_count:
                store.stop(sid, "end of results")
                break
        if STOP.is_set():
            return
        log("mercadolibre [%d/%d] %-26s +%d listings" % (i, len(jobs), sid.split(" ", 1)[1], total_new))


YEN_RX = re.compile(r"(?:([\d,.]+)\s*億)?\s*(?:([\d,.]+)\s*万)?\s*(?:([\d,]+))?\s*円")
JP_LAND_RX = re.compile(r"(?:土地|敷地|(?<![物床築有])面積)[^\d\n]{0,8}([\d,]+(?:\.\d+)?)\s*(㎡|m2|m²|平米|平方メートル|坪)")
# a building on the plot ("住宅地", a residential area, and "別荘を建てたい", to build a holiday home, don't count)
JP_HOUSE_RX = re.compile(r"建物(?:面積)?\s*[：:]?\s*[\d,.]+\s*(?:㎡|m2|m²|坪)|空き家|空家|古家|古屋|中古住宅|家屋|戸建|平屋|古民家|母屋"
                         r"|住宅付|家付|築\d+年|昭和\d+年築|平成\d+年築|築古|住宅です|和風住宅|建てた(?:家|住宅|和)")
JP_BARE_RX = re.compile(r"更地|何も建っておらず|建物はありません|建物なし")  # cleared land, nothing standing


def yen(text):
    """'1,980万円' -> 19800000, '1億2,000万円' -> 120000000, '0円' -> 0; None when there's no price ('応相談')."""
    m = YEN_RX.search(text or "")
    if not m or not any(m.groups()):
        return None
    oku, man, en = (num(g) or 0 for g in m.groups())
    return oku * 1e8 + man * 1e4 + en


def jp_acres(text):
    """Land area from a Japanese description: '土地：1,000.82㎡' or '土地 120坪' (1 tsubo = 3.30579 m²)."""
    m = JP_LAND_RX.search(text or "")
    if not m:
        return None
    v = num(m.group(1))
    return v * (3.30579 if m.group(2) == "坪" else 1) / ACRE_M2 if v else None


def parse_ieichiba(html):
    """Live listings on an Ieichiba page: (listings, how many cards the page had, ended ones included)."""
    soup = BeautifulSoup(html, "lxml")
    cards = soup.select("a.property__list-item")
    out = []
    for c in cards:
        if c.select_one(".property__list-publication-ended-image-main"):
            continue  # sold or withdrawn
        el = c.select_one(".property__list-item-price")
        price = yen(el.get_text(" ", strip=True)) if el else None
        if price is None:
            continue
        get = lambda sel: re.sub(r"\s+", " ", (c.select_one(sel) or BeautifulSoup("", "lxml")).get_text(" ", strip=True))
        title, desc = get(".property__list-item-heading h3"), get(".property__list-item-body").replace("もっと見る", "").strip()
        addr = c.select_one(".property__list-item-address")
        region = re.sub(r"\s+", " ", "".join(addr.find_all(string=True, recursive=False))).strip() if addr else ""
        out.append({
            "source": "ieichiba",
            "country": "JP",
            "region": region,
            "title": title,
            "description": desc[:1500],
            "price": price,
            "currency": "JPY",
            "acres": jp_acres(desc),
            "lat": None, "lon": None,
            "url": "https://www.ieichiba.com" + c["href"].split("#")[0],
            "kind": "houses" if JP_HOUSE_RX.search(title + " " + desc) and not JP_BARE_RX.search(title + " " + desc) else "land",
        })
    return out, len(cards)


def crawl_ieichiba(fetch, store, a):
    for i, band in enumerate(IEICHIBA_BANDS, 1):
        sid, total_new = "ieichiba " + band, 0
        for p in range(1, 101):  # a band has a few hundred listings, 10 a page; it ends with an empty page
            if STOP.is_set():
                return
            url = "https://www.ieichiba.com/tags/%s%s" % (band, "/page/%d" % p if p > 1 else "")
            if url in store.done:
                continue
            html = fetch.get(url)
            if not html:
                store.stop(sid, "fetch failed")
                break
            recs, n = parse_ieichiba(html)
            total_new += store.page(sid, url, recs)
            if not n:
                store.stop(sid, "end of results")
                break
        log("ieichiba   [%d/%d] %-28s +%d listings" % (i, len(IEICHIBA_BANDS), band, total_new))


def crawl_search(fetch, store, src, key, a, url_for, parse, kind="land"):
    """Page through one cheapest-first search. url_for(key, p, kind) gives the page URL; parse(html, key, kind)
    returns a list of records, or None when the page isn't a results page at all (a bot check or an error page):
    that page is left unmarked so a rerun retries it."""
    sid, total_new, seen = search_id(src, key, kind), 0, set()
    for p in search_pages(store, sid, a, kind=kind):
        url = url_for(key, p, kind)
        if url in store.done:
            continue
        fetch.label = "%s %s  page %d  (%s)" % (key, kind, p, src)
        html = fetch.get(url)
        if not html:
            store.stop(sid, "fetch failed")
            break
        recs = parse(html, key, kind)
        if recs is None:
            log("%s %s page %d: no results data on the page (blocked?), skipping the rest" % (src, key, p))
            store.stop(sid, "fetch failed")
            break
        urls = {r["url"] for r in recs if r.get("url")}
        if not recs or urls <= seen:  # empty, or the site repeats its last page past the end
            store.mark(url)
            store.stop(sid, "end of results")
            break
        seen |= urls
        total_new += store.page(sid, url, recs)
    return total_new


def strip_tags(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()


def eu_number(s):
    """'10.000' or '1.335,5' (European thousands dots / decimal comma) -> float."""
    s = (s or "").strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", s) or "," in s:
        s = s.replace(".", "").replace(",", ".")
    return num(s)


# ---- Zillow: lots and land in every US state (plain HTTP; results are JSON in __NEXT_DATA__)

def zillow_url(state, p, kind="land"):
    fs = {k: {"value": False} for k in ("sf", "tow", "mf", "con", "apa", "manu", "apco")}  # land only
    fs.update({"sort": {"value": "pricea"}, "price": {"min": 1000}})
    sq = {"pagination": {"currentPage": p} if p > 1 else {}, "isMapVisible": False, "isListVisible": True,
          "filterState": fs}
    return "https://www.zillow.com/%s/land/%s?searchQueryState=%s" % (
        US_STATE_ABBR[state].lower(), "%d_p/" % p if p > 1 else "",
        requests.utils.quote(json.dumps(sq, separators=(",", ":")), safe=""))


def parse_zillow(html, state, kind="land"):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        res = json.loads(m.group(1))["props"]["pageProps"]["searchPageState"]["cat1"]["searchResults"]["listResults"]
    except Exception:
        return None
    if not isinstance(res, list):  # null or missing: not a results page, so let crawl_search retry it
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
        })
    return out


def crawl_zillow(fetch, store, states, a):
    for i, st in enumerate(states, 1):
        if STOP.is_set():
            break
        n = crawl_search(fetch, store, "zillow", st, a, zillow_url, parse_zillow)
        log("zillow     [%d/%d] %-28s +%d listings" % (i, len(states), st, n))


# ---- Immobiliare.it: all of Italy (real Chrome; results are JSON in __NEXT_DATA__)

def immobiliare_url(key, p, kind="land"):
    # under €1,000 is almost all "price on request" placeholders and €1 auction lots
    return "https://www.immobiliare.it/vendita-terreni/%s/?criterio=prezzo&ordine=asc&prezzoMinimo=1000%s" % (
        key, "&pag=%d" % p if p > 1 else "")


def parse_immobiliare(html, key, kind="land"):
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
        })
    return out


# ---- HOME'S akiya bank: Japan (real Chrome, at the normal slow pace)

def homes_url(pref, p, kind="land"):
    """pref = "region/prefecture", kind = land or houses. Cheapest first; GET parameters work as well as the form."""
    return "https://www.homes.co.jp/akiyabank/%s/?sort=price_asc&categories%%5B%%5D=%s%s" % (
        pref, HOMES_CATEGORIES[kind], "&page=%d" % p if p > 1 else "")


def homes_m2(text):
    m = re.match(r"\s*([\d,.]+)\s*(m²|m2|㎡|坪)", text or "")
    if not m:
        return None
    v = num(m.group(1))
    return v * 3.305785 if (v and m.group(2) == "坪") else v


def parse_homes(html, pref, kind="land"):
    soup = BeautifulSoup(html, "html.parser")
    boxes = soup.select(".sec-resultList .mod-result-bukkenBox")
    if not boxes:
        title = soup.title.get_text() if soup.title else ""
        return [] if "空き家バンク" in title else None  # a real page with no results, or a block page
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
        name = addr.get_text(" ", strip=True) if addr else ""
        out.append({
            "source": "homes",
            "country": "JP",
            "region": spec.get("所在地", ""),
            "title": ("House: " if kind == "houses" else "Land: ") + (name or spec.get("所在地", "")),
            "description": " | ".join(x for x in ((cat.get_text(strip=True) if cat else ""),
                                                  (point.get_text(" ", strip=True).replace("Point", "", 1).strip()
                                                   if point else "")) if x),
            "price": price,
            "currency": "JPY",
            "acres": (m2 / ACRE_M2) if m2 else None,
            "lat": None, "lon": None,
            "url": a["href"].split("?")[0],
            "kind": kind,
        })
    return out


# ---- Encuentra24: Central America (real Chrome; Cloudflare blocks plain requests)

E24_CURRENCY = [("C$", "NIO"), ("₡", "CRC"), ("US$", "USD"), ("$", "USD"), ("Q", "GTQ"), ("L", "HNL")]


def encuentra24_url(cc, p, kind="land"):
    return "https://www.encuentra24.com/%s/real-estate-for-sale-lots-land%s?sort=f_price&dir=asc" % (
        ENCUENTRA24_COUNTRIES[cc], ".%d" % p if p > 1 else "")


def parse_encuentra24(html, cc, kind="land"):
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


def kijiji_full_ad(html, url):
    """The ad from its own Kijiji page, with the whole description as plain text. None if the ad is gone
    (Kijiji then shows a search page instead, with ?adRemoved=<id>)."""
    ad_id = url.rstrip("/").rsplit("/", 1)[-1]
    nd = BeautifulSoup(html, "lxml").find("script", id="__NEXT_DATA__")
    try:
        apollo = json.loads(nd.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except Exception:
        return None
    for v in apollo.values():
        if isinstance(v, dict) and str(v.get("id")) == ad_id and v.get("description"):
            text = unescape(re.sub(r"<[^>]+>", " ", v["description"]))  # REALTOR.ca ads end in links and a logo
            return dict(v, description=re.sub(r"\s+", " ", text).strip())
    return None


def verify_cheapest(store, a, n=60, rounds=4):
    """Open the cheapest believable LandWatch and Kijiji listings and re-check them against their full description.
    Search cards cut descriptions short, which hides 'Only $2,700 cash ... $380 down' style pricing on LandWatch,
    and on Kijiji, homes and land for rent whose price is the monthly rent ('... for tenants', 'Lease expires 2030').
    Only listings the site will show are checked. LandWatch ads open in several Chrome windows at once (--workers),
    and Kijiji's load over plain HTTP alongside them."""
    today = datetime.now().strftime("%Y-%m-%d")
    sources = [s for s in ("landwatch", "kijiji") if s in csv_list(a.sources, [])]
    plain, gone = Plain(delay=max(1.0, a.delay / 2)), set()
    queues = {s: queue.Queue() for s in sources}

    def recheck(r, raw, get):
        rec = dict(raw[r["url"]])
        rec["verified_at"] = datetime.now().isoformat(timespec="seconds")
        if r["source"] == "kijiji":
            ad = kijiji_full_ad(get(r["url"]) or "", r["url"])
            if not ad:  # taken down since the scrape (or wouldn't load): it drops off the site in a few days
                gone.add(r["url"])
                return
            rec["description"], rec["acres"] = ad["description"], kijiji_acres(ad)
        else:
            html = get(r["url"])
            desc = landwatch_full_description(html) if html else None
            if not desc:
                # removed, blocked or timed out: save nothing. Saving would stamp it scraped and verified today,
                # so a dead listing would never age off the site, and a blocked one wouldn't be retried
                return
            rec["description"] = desc
        store.add([rec])

    def worker(src):
        br = None
        try:
            while True:
                job = queues[src].get()
                if job is None:
                    return
                try:
                    if STOP.is_set():
                        continue
                    if src == "kijiji":  # these answer plain HTTP requests
                        recheck(job[0], job[1], lambda u: plain.get(u, what="verify"))
                    else:
                        if br is None:
                            br = Browser(headless=a.headless, delay=a.delay, slot=None, block=not a.load_all)
                        br.label = "verify " + job[0]["url"][-12:]
                        recheck(job[0], job[1], lambda u: br.get(u, ready=LANDWATCH_AD_READY, what="verify"))
                except Exception as e:
                    log("  verifying %s failed: %s" % (job[0]["url"], str(e)[:120]))
                finally:
                    if br and queues[src].empty():  # nothing left for now: close the window, reopen it next round
                        br.close()
                        br = None
                    queues[src].task_done()
        finally:
            if br:
                br.close()

    threads = [(s, threading.Thread(target=worker, args=(s,), name="V%s%d" % (s[0].upper(), i + 1), daemon=True))
               for s in sources for i in range(max(1, a.workers) if s == "landwatch" else 1)]
    for _, t in threads:
        t.start()
    try:
        for rnd in range(rounds):
            rates = load_fx()
            everything = store.all()
            cutoff = site_cutoff(everything) or ""
            raw = {r["url"]: r for r in everything if r.get("source") in sources and (r.get("scraped_at") or "") >= cutoff}
            recs = sorted((finalize(dict(r), rates) for r in raw.values()),
                          key=lambda r: (r["price_usd"] is None, r["price_usd"] or 0))
            todo = []
            for src in sources:
                top = [r for r in recs if r["source"] == src and r["price_usd"] and r["url"] not in gone
                       and r["flags"] in ("", "no land area listed")][:n]
                todo += [r for r in top if not (r.get("verified_at") or "").startswith(today)]
            if not todo or STOP.is_set():
                break
            log("verifying %d of the cheapest %d listings per site (%s, round %d)" % (
                len(todo), n, ", ".join(sorted({r["source"] for r in todo})), rnd + 1))
            for r in todo:
                queues[r["source"]].put((r, raw))
            for q in queues.values():
                q.join()
    except KeyboardInterrupt:
        STOP.set()  # the workers skip what's left in their queues
        raise
    finally:
        for s, _ in threads:
            queues[s].put(None)
        for _, t in threads:
            t.join()


# ----------------------------------------------------------------------------- translation

FRENCH_RX = re.compile(r"[éèêàâùûôîç]|\b(terrains?|vendre|vendu|terres?|boisé|érablière|près|avec|secteur|zoné|pieds)\b", re.I)
CYRILLIC_RX = re.compile(r"[Ѐ-ӿ]")
# common non-English words: Properstar titles are usually English, but some come in German, French, Spanish...
FOREIGN_RX = re.compile(r"\b(de|del|la|las|los|el|en|con|para|venta|vendo|vende|se|mit|und|von|vom|im|zum|zur|der|die|das|di|"
                        r"della|vendesi|edificabile|terreno|terrain|à|avec|pour|sur|com|uma|um|het|een|van|met|och|med|på|na|do|ze|za)\b", re.I)
TRANSLATE_PROMPT = ("You translate real-estate listing titles and place names into English. The input is a JSON array of "
                    "strings. Reply with only a JSON array of the same length, in the same order: each string as short, "
                    "natural English. Keep numbers and prices as written, and write units in English (соток/сот -> sotka, "
                    "га -> ha, кв.м -> m²). Write names of towns, regions and streets in Latin letters (transliterate "
                    "Cyrillic), so no Cyrillic is left. Return strings that are already English unchanged.")


def translate(texts):
    """English versions of texts, from Claude: the claude CLI with the Haiku model, 80 texts per call.
    Cached in data/translations.json so each text is only sent once; anything untranslated stays as it is."""
    path = os.path.join(DATA, "translations.json")
    try:
        cache = json.load(open(path))
    except (OSError, ValueError):
        cache = {}
    todo = sorted({t for t in texts if t and t not in cache})
    claude = shutil.which("claude")
    if not todo or not claude:
        if todo:
            log("translation skipped: the claude CLI isn't installed (%d texts stay as they are)" % len(todo))
        return cache
    batches = [[]]
    for t in todo:
        if len(batches[-1]) >= 80 or sum(map(len, batches[-1])) + len(t) > 6000:
            batches.append([])
        batches[-1].append(t)
    log("translating %d texts to English with Claude (%d batches)" % (len(todo), len(batches)))
    lock, failed = threading.Lock(), []

    def run(batch):
        if len(failed) >= 3 or STOP.is_set():  # e.g. not signed in or out of usage: don't keep trying
            return
        out, err = None, ""
        try:
            p = subprocess.run([claude, "-p", "--model", "haiku", "--tools", "", "--strict-mcp-config",
                                "--no-session-persistence", "--setting-sources", "", "--system-prompt", TRANSLATE_PROMPT],
                               input=json.dumps(batch, ensure_ascii=False), capture_output=True, text=True, timeout=300)
            err = (p.stderr or p.stdout).strip()
            m = re.search(r"\[.*\]", p.stdout, re.S)
            out = json.loads(m.group(0)) if m else None
        except (subprocess.TimeoutExpired, ValueError) as e:
            err = str(e)
        if isinstance(out, list) and len(out) == len(batch) and all(isinstance(x, str) for x in out):
            with lock:
                cache.update(zip(batch, out))
        else:
            failed.append(batch)
            log("  translation batch failed: %s" % (err[:150] or "reply wasn't a matching JSON array"))

    try:
        with ThreadPoolExecutor(4) as ex:
            list(ex.map(run, batches))
    finally:
        with open(path, "w") as f:
            json.dump(cache, f, ensure_ascii=False)
    return cache


JAPANESE_RX = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
JP_PREFS = dict(zip(
    "北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 新潟 富山 石川 福井 山梨 長野 岐阜 静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 "
    "和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 熊本 大分 宮崎 鹿児島 沖縄".split(),
    "Hokkaido Aomori Iwate Miyagi Akita Yamagata Fukushima Ibaraki Tochigi Gunma Saitama Chiba Tokyo Kanagawa Niigata Toyama "
    "Ishikawa Fukui Yamanashi Nagano Gifu Shizuoka Aichi Mie Shiga Kyoto Osaka Hyogo Nara Wakayama Tottori Shimane Okayama "
    "Hiroshima Yamaguchi Tokushima Kagawa Ehime Kochi Fukuoka Saga Nagasaki Kumamoto Oita Miyazaki Kagoshima Okinawa".split()))
JP_PREF_RX = re.compile(r"^(北海道|東京都|京都府|大阪府|[^都道府県]{2,3}県)")
JP_SUFFIX_RX = re.compile(r"(?:\s+(?:city|town|village|ward|district|county|prefecture)|-(?:shi|machi|cho|chō|mura|son|gun|ku|ken))$", re.I)


def jp_place(rec):
    """'栃木県那須町' + its translation ('Tochigi Prefecture, Nasu Town' or 'Nasu-machi, Tochigi-ken') -> 'Nasu, Tochigi':
    the prefecture from a fixed table, the town from the translation without its 'Town'/'-shi'."""
    region = rec.get("region") or ""
    m = JP_PREF_RX.match(region)
    pref = JP_PREFS.get(re.sub(r"[都道府県]$", "", m.group(1)) if m and m.group(1) != "北海道" else "北海道") if m else None
    if not pref:
        return rec.get("region_en") or region
    parts = [JP_SUFFIX_RX.sub("", x.strip()) for x in re.split(r",", rec.get("region_en") or "") if x.strip()]
    town = next((x for x in parts if x and x.lower() != pref.lower() and not JAPANESE_RX.search(x)), "")
    if not town and len(region) > len(m.group(1)) and sum(x.lower() == pref.lower() for x in parts) > 1:
        town = pref  # "富山県富山市": Toyama City, Toyama
    return "%s, %s" % (town, pref) if town else pref


def add_english(recs):
    """Set title_en on non-English listings (OLX, Mercado Libre, French Kijiji ads, foreign-looking Properstar
    titles, anything in Cyrillic, Ieichiba's Japanese) and region_en on place names in Cyrillic or Japanese, for the site."""
    titled = [r for r in recs if r.get("title") and (r.get("source") in ("olx", "mercadolibre", "ieichiba", "homes", "immobiliare", "encuentra24") or CYRILLIC_RX.search(r["title"])
                                                     or (r.get("source") == "kijiji" and FRENCH_RX.search(r["title"]))
                                                     or (r.get("source") == "properstar" and FOREIGN_RX.search(r["title"])))]
    placed = [r for r in recs if CYRILLIC_RX.search(r.get("region") or "") or JAPANESE_RX.search(r.get("region") or "")]
    tr = translate([r["title"] for r in titled] + [r["region"] for r in placed])
    for r in titled:
        if tr.get(r["title"], r["title"]) != r["title"]:
            r["title_en"] = tr[r["title"]]
    for r in placed:
        if r["region"] in tr:
            r["region_en"] = tr[r["region"]]
    for r in recs:
        if r.get("source") in AKIYA:
            r["region_en"] = jp_place(r)


# ----------------------------------------------------------------------------- output

COLS =["price_usd", "usd_per_acre", "acres", "type", "tags", "country", "region", "title", "price", "currency",
        "source", "flags", "url", "lat", "lon", "scraped_at"]


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


def build_outputs(store, english=True):
    rates = load_fx()
    recs = store.all()
    cutoff = site_cutoff(recs)
    if english and cutoff:  # before finalize, so the price checks also read the English titles
        add_english([r for r in recs if (r.get("scraped_at") or "") >= cutoff])
    recs = [finalize(r, rates) for r in recs]
    # one exact price on many different-size plots -> a placeholder or a per-m2 price, not a real total
    same = {}
    for r in recs:
        if r.get("price") and (r.get("price_usd") or 0) < PLACEHOLDER_UNDER_USD:
            same.setdefault((r.get("source"), r.get("country"), round(r["price"], 2)), set()).add(r.get("acres"))
    for r in recs:
        k = (r.get("source"), r.get("country"), round(r.get("price") or 0, 2))
        if k in same and len(same[k]) >= PLACEHOLDER_SIZES and r.get("source") not in CHEAP_IS_REAL:
            r["flags"] = "; ".join(x for x in (r["flags"], "placeholder price - same price on many different-size plots") if x)
    # a "total" price that would only buy ~20 m2 at that country's typical price per m2 is a per-m2 price
    m2p = {}
    for r in recs:
        if r["price_usd"] and r.get("acres") and not r["flags"] and r.get("kind") != "houses":  # land prices only
            m2p.setdefault(r.get("country"), []).append(r["price_usd"] / (r["acres"] * ACRE_M2))
    med_m2 = {c: sorted(v)[len(v) // 2] for c, v in m2p.items() if len(v) >= 20}
    for r in recs:
        m = med_m2.get(r.get("country"))
        if m and r["price_usd"] and r["price_usd"] < 20 * m and r.get("source") not in CHEAP_IS_REAL:
            r["flags"] = "; ".join(x for x in (r["flags"], "price may be per m2 (would buy under 20 m2 at local prices)") if x)
    # far below everything else in the same country -> usually a wrong area or a per-m2 price
    by = {}
    for r in recs:
        if r["usd_per_acre"] and not r["flags"]:
            by.setdefault(r.get("country"), []).append(r["usd_per_acre"])
    med = {c: sorted(v)[len(v) // 2] for c, v in by.items() if len(v) >= 20}
    for r in recs:
        m = med.get(r.get("country"))
        if m and r["usd_per_acre"] and r["usd_per_acre"] < m / 250 and r.get("source") not in CHEAP_IS_REAL:
            r["flags"] = "; ".join(x for x in (r["flags"], "far below other listings in this country") if x)
    recs.sort(key=lambda r: r.get("scraped_at") or "", reverse=True)  # newest first among equal prices, so a
    recs.sort(key=lambda r: (r["price_usd"] is None, r["price_usd"] or 0))  # repost keeps its live URL; cheapest first
    # same parcel reposted under a new URL ("Lot (PID 123) X" vs "(PID 123) X") -> keep one
    seen, uniq = {}, []
    for r in recs:
        title = r.get("title") or ""
        t = re.sub(r"[\W_]", "", re.sub(r"^\s*lot\b", "", title.lower()))  # keeps Japanese and Cyrillic letters
        # region too: two 'Terreno en venta' at one price in different towns are different parcels. Normalized like
        # the title, because a Kijiji repost's address can gain or lose a leading 'Lot' as well
        region = re.sub(r"[\W_]", "", re.sub(r"^\s*lot\b", "", (r.get("region") or "").lower()))
        key = (r.get("source"), r.get("country"), region, round(r.get("price") or 0), r.get("acres"))
        # a title with no letters or digits ('•', '-', '') says nothing about which parcel it is
        if t and any(same_title(t, title, *prev, r.get("source")) for prev in seen.get(key, ())):
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
    # escape <, > and & so a scraped title containing "</script>" can't end the data script and inject HTML
    data = json.dumps(slim).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = tpl.replace("/*DATA*/[]", data).replace(
        "{{GENERATED}}", datetime.now().strftime("%Y-%m-%d %H:%M"))
    open(os.path.join(HERE, "report.html"), "w").write(html)
    build_site(recs, rates)
    write_coverage(store, recs, cutoff)
    clean = [r for r in recs if r["price_usd"] and r["flags"] in ("", "no land area listed")]  # land the site shows
    log("wrote %d listings (%d with a believable price) -> results.csv, report.html" % (len(recs), len(clean)))
    for r in clean[:15]:
        log("  $%9.0f  %9s ac  %-3s %-22s %s" % (
            r["price_usd"], "%.2f" % r["acres"] if r["acres"] else "?", r["country"], (r["region"] or "")[:22], r["url"]))


SITE = os.path.join(HERE, "site")
FLAG_CODES = [("price too low", "L"), ("payment plan", "P"), ("auction", "A"), ("wanted ad", "W"),
              ("no land area", "N"), ("under $10/acre", "C"), ("lease/rental", "R"), ("placeholder", "X"),
              ("per m2/acre", "U"), ("price may be per m2", "U"), ("far below other", "O"),
              ("mining claim", "M"), ("house listing", "H"), ("too low for Canada", "L"),
              ("share of a property", "S"), ("allotment garden", "S"), ("burial plot", "S"), ("wrong currency", "L")]


def site_codes(flags):
    """One letter per kind of warning in a listing's flags text, each letter once."""
    return "".join(dict.fromkeys(c for txt, c in FLAG_CODES if txt in (flags or "")))


TYPE_CODES = {"vacant land": "V", "building lot": "L", "farmland": "F", "forest": "T", "recreational": "R", "waterfront": "W",
              "commercial": "C", "dacha plot": "G", "land with house": "B", "house": "H"}  # labels are in site_template.html
TAG_CODES = {"ruin": "R", "castle": "K", "church": "C"}  # special_tags(); labels are in site_template.html
# where each site keeps the town: LandWatch in its address "[street, ]Deming, NM, 88030[, Luna County]", Kijiji before
# the province or postal code, OLX before the region name ("Vila de Rei, Castelo Branco"), Mercado Libre before the
# state ("Terranova Elite, Apodaca, Nuevo León"), Properstar on its own
US_ADDRESS_RX = re.compile(r"^(?:[^,]+, )*?([^,]+), ([A-Z]{2}), \d{5}(?:, [^,]+)?$")
CA_TOWN_RX = re.compile(r"([^,]+),\s*(?:(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b|[A-Z]\d[A-Z] ?\d[A-Z]\d\b)")
TOWN_PREFIX_RX = re.compile(r"^(?:vil\.|village(?: of)?|town(?: of)?)\s+", re.I)  # Bulgarian "с."/"гр." once translated


def town(rec):
    """(town, state or province code) of one listing. Only LandWatch and Kijiji addresses give the code."""
    src, place, state = rec.get("source"), rec.get("region_en") or rec.get("region") or "", None
    if src == "landwatch":  # the address is the title, or one of the " | " parts of the card text
        parts = ((rec.get("title") or "") + " | " + (rec.get("description") or "")).split(" | ")
        m = next((m for m in (US_ADDRESS_RX.match(p.strip()) for p in parts) if m), None)
        name, state = m.groups() if m else ("", None)
    elif src == "kijiji":
        m = CA_TOWN_RX.search(place)
        name, state = m.groups() if m else ("", None)
    elif src == "olx":
        name = place.rsplit(", ", 1)[0]
    elif src == "mercadolibre":
        parts = [p.strip() for p in place.split(",") if p.strip()]
        name = parts[-2] if len(parts) > 1 else ""
    else:
        name = place
    name = TOWN_PREFIX_RX.sub("", name.strip())
    if name.isupper():  # "COME BY CHANCE"
        name = re.sub(r"\w[\w']*", lambda m: m.group(0).capitalize(), name.lower())
    return name, state


def towns(recs):
    """Town of each listing, for the site's City filter. In the US and Canada, where town names repeat from state to
    state, it reads "Deming, NM"; listings whose address has no state (Properstar, a few Kijiji ads) take the state
    of the nearest listing that has one. Spellings that differ only in case become the most common one."""
    found = [town(r) for r in recs]
    known = {}
    for r, (name, st) in zip(recs, found):
        if st and r.get("lat") is not None:
            known.setdefault(r["country"].upper(), set()).add((r["lat"], r["lon"], st))
    names = []
    for r, (name, st) in zip(recs, found):
        pts = known.get((r.get("country") or "").upper())
        if name and not st and pts and r.get("lat") is not None:
            k = math.cos(math.radians(r["lat"]))  # a degree of longitude shrinks away from the equator
            st = min(pts, key=lambda p: (p[0] - r["lat"]) ** 2 + ((p[1] - r["lon"]) * k) ** 2)[2]
        names.append("%s, %s" % (name, st) if name and st else name)
    keys = [((r.get("country") or "").upper(), n.lower()) for r, n in zip(recs, names)]
    spelling = {}
    for (key, n), _ in Counter(zip(keys, names)).most_common():
        spelling.setdefault(key, n)
    return [spelling[key] for key in keys]


def site_cutoff(recs, fresh_days=3):
    """The site shows listings seen within a few days of the newest scrape."""
    stamps = [r.get("scraped_at") for r in recs if r.get("scraped_at")]
    return (datetime.fromisoformat(max(stamps)) - timedelta(days=fresh_days)).isoformat() if stamps else None


def place_key(name):
    """'São Brás de Alportel' -> 'sao bras de alportel', for matching town names across spellings."""
    name = unicodedata.normalize("NFKD", name.lower())
    return " ".join(re.sub(r"[^\w]+", " ", "".join(ch for ch in name if not unicodedata.combining(ch))).split())


def town_populations(places):
    """{country: {town: population}} for the City list, which shows the biggest towns first. Towns ("Deming, NM" in
    the US and Canada) are looked up in geo/town_population.tsv.gz, built from GeoNames by tools/build_town_population.py:
    every place with 1,000+ people, under all its names. A place's own name beats an old or foreign one ("Sredets" is
    a small town, not Sofia), and a name shared by several places takes the biggest one."""
    want = {}
    for cc, label in places:
        name, _, state = label.rpartition(", ") if cc in ("US", "CA") else (label, "", "")
        want.setdefault((cc, state, place_key(name)), []).append(label)
    best = {}  # (country, town) -> (matched its own name, population)
    try:
        with gzip.open(os.path.join(HERE, "geo", "town_population.tsv.gz"), "rt", encoding="utf-8") as f:
            for line in f:
                cc, state, pop, names = line.rstrip("\n").split("\t")
                names = names.split("|")
                own = {place_key(x) for x in names[:2]}  # its name and ASCII name; the rest are alternate names
                for n in own | {place_key(x) for x in names[2:]}:
                    for label in want.get((cc, state, n), ()):
                        best[cc, label] = max(best.get((cc, label), (False, 0)), (n in own, int(pop)))
    except OSError:
        log("geo/town_population.tsv.gz is missing: the City list stays A to Z")
    out = {}
    for (cc, label), (_, pop) in best.items():
        out.setdefault(cc, {})[label] = pop
    return out


def build_site(recs, rates):
    """Write site/index.html + site/data.json: every listing seen in the last few days of runs, plus the
    exchange rates its US dollar prices were converted with, for the site's Currency list."""
    os.makedirs(SITE, exist_ok=True)
    cutoff = site_cutoff(recs)
    if not cutoff:
        return
    recs = [r for r in recs if (r.get("scraped_at") or "") >= cutoff and r.get("price_usd") is not None]
    rows, countries = [], set()
    for r, place in zip(recs, towns(recs)):
        codes = site_codes(r.get("flags"))
        if "W" in codes:
            continue
        rnd = lambda v, d: None if v is None else round(v, d)
        cc = (r.get("country") or "").upper() if len(r.get("country") or "") == 2 else (r.get("country") or "")
        countries.add(cc)
        rows.append([rnd(r.get("usd_per_acre"), 2), rnd(r["price_usd"], 0), rnd(r.get("acres"), 3), cc,
                     (r.get("region_en") or r.get("region") or "")[:60], (r.get("title_en") or r.get("title") or "")[:110],
                     r.get("source"), codes, r["url"], rnd(r.get("lat"), 4), rnd(r.get("lon"), 4),
                     r["title"][:110] if r.get("title_en") else None,  # the original title, when translated
                     TYPE_CODES.get(r.get("type"), "V"), place,
                     rnd(r.get("price"), 2), (r.get("currency") or "USD").upper(),  # the price in its own currency
                     "".join(TAG_CODES[t] for t in (r.get("tags") or "").split(",") if t)])
    data = {"generated": datetime.now().astimezone().isoformat(timespec="seconds"),
            "countries": len(countries), "rows": rows, "rates": rates,
            "populations": town_populations({(row[3], row[13]) for row in rows if row[13]})}
    with open(os.path.join(SITE, "data.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"))
    with open(os.path.join(HERE, "site_template.html")) as f:
        html = f.read()
    with open(os.path.join(SITE, "index.html"), "w") as f:
        f.write(html)
    open(os.path.join(SITE, ".nojekyll"), "w").close()
    log("site: %d listings from %d countries -> site/" % (len(rows), len(countries)))


COVERAGE_COLS = ["source", "search", "site_total", "fetched", "fetched_pct", "on_site", "pages", "reached_usd", "stopped"]
SOURCE_ORDER = ["properstar", "castles", "landwatch", "zillow", "kijiji", "olx", "mercadolibre", "ieichiba", "homes",
                "immobiliare", "encuentra24"]


def write_coverage(store, recs, cutoff):
    """coverage.csv, one row per search (a country, state or province on one site): the results the site reports
    (above our price floor where the search has one), how many we fetched, how many believable ones the site shows,
    the price the cheapest-first crawl got up to and why it stopped. The log gets a summary per source, plus the
    countries that rely on a single source or have few listings: the places to look for sources we're missing."""
    shown = [r for r in recs if (r.get("scraped_at") or "") >= (cutoff or "")
             and r["price_usd"] and believable_flags(r["flags"])]
    on_site = Counter(r.get("search") for r in shown)
    rows = []
    for sid, s in store.searches.items():
        src, key = sid.split(" ", 1)
        rows.append({"source": src, "search": key, "site_total": s["total"], "fetched": s["listings"],
                     "fetched_pct": round(100.0 * s["listings"] / s["total"], 1) if s["total"] else None,
                     "on_site": on_site[sid], "pages": s["pages"],
                     "reached_usd": None if s["reached"] is None else round(s["reached"]),
                     "stopped": s["stop"] or "interrupted"})
    rank = {s: i for i, s in enumerate(SOURCE_ORDER)}
    rows.sort(key=lambda r: (rank.get(r["source"], len(rank)), r["search"]))
    with open(os.path.join(HERE, "coverage.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COVERAGE_COLS)
        w.writeheader()
        w.writerows(rows)
    if not rows:
        log("coverage: no search tallies yet (they're kept from the next scrape on)")
    else:
        log("coverage: %d searches -> coverage.csv" % len(rows))
    for src in sorted({r["source"] for r in rows}, key=lambda s: rank.get(s, len(rank))):
        mine = [r for r in rows if r["source"] == src]
        known = [r for r in mine if r["site_total"]]
        fetched, total = sum(r["fetched"] for r in known), sum(r["site_total"] for r in known)
        log("  %-12s %3d searches: %s | fetched %s of the %s listings the site reports (%.1f%%)" % (
            src, len(mine), ", ".join("%d %s" % (n, why) for why, n in Counter(r["stopped"] for r in mine).most_common()),
            "{:,}".format(fetched), "{:,}".format(total), 100.0 * fetched / total if total else 0))
    for why, label in (("page cap", "hit the page cap first"), ("fetch failed", "fetch failed")):
        hit = ["%s %s (%d believable)" % (r["source"], r["search"], r["on_site"]) for r in rows if r["stopped"] == why]
        if hit:
            log("  %s: %s%s" % (label, ", ".join(hit[:15]), " and %d more" % (len(hit) - 15) if len(hit) > 15 else ""))
    by_cc = {}
    for r in shown:
        c = r.get("country") or ""
        by_cc.setdefault(c.upper() if len(c) == 2 else c, Counter())[r.get("source")] += 1
    thin = sorted((sum(n.values()), c) for c, n in by_cc.items() if sum(n.values()) < 10)
    log("  site: %d countries, %d from a single source, %d with under 10 believable listings%s" % (
        len(by_cc), sum(1 for n in by_cc.values() if len(n) == 1), len(thin),
        " (%s%s)" % (", ".join("%s %d" % (c, n) for n, c in thin[:15]), "..." if len(thin) > 15 else "") if thin else ""))


# ----------------------------------------------------------------------------- main

def csv_list(s, default):
    return [x.strip() for x in s.split(",") if x.strip()] if s else default


# --test: a few minutes' sample of every source, to measure speed, bandwidth and blocks (see the metrics summary)
TEST_DEFAULTS = dict(states="arizona,maine", countries="portugal,mexico", provinces="nova-scotia", pages=2,
                     verify=5, workers=2, hidden=True, fresh=True, no_translate=True)


def main():
    global DATA
    ap = argparse.ArgumentParser(description="Find the cheapest land listings worldwide.")
    ap.add_argument("--sources", default=ALL_SOURCES, help="comma-separated, default: " + ALL_SOURCES)
    ap.add_argument("--listings", type=int, default=250,
                    help="believable listings to collect per country/state/province on each site before moving on")
    ap.add_argument("--house-listings", type=int, default=60,
                    help="believable listings to collect from each site's houses-for-sale section per country/state/province")
    ap.add_argument("--no-houses", action="store_true", help="only search the land-for-sale sections")
    ap.add_argument("--pages", type=int, default=30,
                    help="most pages per country/state/province (20-48 listings each), however few are believable")
    ap.add_argument("--countries", help="country slugs for Properstar, OLX and Mercado Libre, comma-separated (default: all)")
    ap.add_argument("--states", help="US state slugs for LandWatch and Zillow, e.g. new-mexico,arizona (default: all)")
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
    ap.add_argument("--load-all", action="store_true",
                    help="let Chrome download ads, analytics, maps and fonts too (they're blocked by default)")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--verify", type=int, default=60,
                    help="after scraping, open this many of the cheapest listings to re-check their price (0 = off)")
    ap.add_argument("--verify-only", action="store_true", help="only run the verification step, then rebuild outputs")
    ap.add_argument("--no-translate", action="store_true",
                    help="don't translate new non-English titles for the site (uses the claude CLI)")
    ap.add_argument("--test", action="store_true",
                    help="small sample run for measuring: 2 states, 2 countries, 1 province, 2 pages each, 5 verified, "
                         "2 hidden windows (any of these can still be set). Uses data-test/ and leaves the site alone")
    a = ap.parse_args()
    if a.test:
        ap.set_defaults(**TEST_DEFAULTS)  # explicit options still win
        a = ap.parse_args()
        test_dir = os.path.join(HERE, "data-test")
        shutil.rmtree(test_dir, ignore_errors=True)
        os.makedirs(test_dir)
        if os.path.exists(os.path.join(DATA, "fx.json")):
            shutil.copy(os.path.join(DATA, "fx.json"), test_dir)
        DATA = test_dir
        log("test run: %s, pages=%d, verify=%d, workers=%d (data in data-test/)" % (
            a.sources, a.pages, a.verify, a.workers))

    store = Store()
    if a.fresh:
        store.reset()
    if a.verify_only:
        verify_cheapest(store, a, a.verify)
    elif not a.report_only:
        sources = csv_list(a.sources, [])
        countries = csv_list(a.countries, PROPERSTAR_COUNTRIES)
        states = csv_list(a.states, US_STATES)
        provs = {p: KIJIJI_PROVINCES[p] for p in csv_list(a.provinces, list(KIJIJI_PROVINCES))}
        unknown = set(sources) - set(ALL_SOURCES.split(","))
        if unknown:
            ap.error("unknown source(s): %s (choose from %s)" % (",".join(sorted(unknown)), ALL_SOURCES))
        e24 = [c.upper() for c in csv_list(a.central_america, list(ENCUENTRA24_COUNTRIES))]
        prefs = [next((h for h in HOMES_PREFECTURES if h.endswith("/" + x)), x)  # "nagano" is enough
                 for x in csv_list(a.prefectures, HOMES_PREFECTURES)]
        log("starting: sources=%s, %d believable listings per search, at most %d pages (%d already-scraped listings on disk)" % (
            ",".join(sources), a.listings, a.pages, len(store.urls)))
        br = None
        try:
            rates = store.rates = load_fx()
            wanted = lambda sites: [c for c in sites if not a.countries or c in countries]
            kinds = ("land",) if a.no_houses else KINDS
            plain = []  # sites that answer plain HTTP requests: one thread each, running alongside the Chrome windows
            if "kijiji" in sources:
                plain.append((crawl_kijiji, (provs, a, kinds)))
            if "olx" in sources:
                plain.append((crawl_olx, (wanted(OLX_SITES), a, rates, kinds)))
            if "mercadolibre" in sources:
                plain.append((crawl_meli, (wanted(MELI_SITES), a, rates, kinds)))
            if "ieichiba" in sources and (not a.countries or "japan" in countries):
                plain.append((crawl_ieichiba, (a,)))
            if "zillow" in sources:
                plain.append((crawl_zillow, (states, a)))
            def timed(fn):
                def run(*args):
                    with METRICS.phase(fn.__name__.replace("crawl_", "http ")):
                        fn(*args)
                return run

            threads = [threading.Thread(target=timed(fn), args=(Plain(delay=max(1.0, a.delay / 2)), store) + args,
                                        name="P%d" % (i + 1), daemon=True) for i, (fn, args) in enumerate(plain)]
            for t in threads:
                t.start()
            # on each site the land searches come before the house searches
            japan = not a.countries or "japan" in countries
            per_source = {
                "landwatch": [(st, k) for k in kinds for st in states],
                "properstar": [(c, k) for k in kinds for c in countries],
                "castles": [(c, "houses") for c in countries],
                "immobiliare": [("italia", "land")] if not a.countries or "italy" in countries else [],
                "encuentra24": [(cc, "land") for cc in e24],
                "homes": [(pf, k) for k in kinds for pf in prefs] if japan else [],
            }
            lists = [[(src, key, k) for key, k in keys] for src, keys in per_source.items() if src in sources]
            jobs = []
            for i in range(max([len(l) for l in lists] or [0])):  # interleave so no single site gets every window at once
                for l in lists:
                    jobs += l[i:i + 1]
            if jobs:
                log("%d browser jobs across %d Chrome windows" % (len(jobs), min(a.workers, len(jobs))))
                with METRICS.phase("chrome pool"):
                    run_pool(jobs, store, a)
            while any(t.is_alive() for t in threads):
                for t in threads:
                    t.join(0.5)
            if a.verify and not STOP.is_set():
                with METRICS.phase("verify"):
                    verify_cheapest(store, a, a.verify)
        except KeyboardInterrupt:
            STOP.set()
            log("stopped by user - progress is saved; rerun the same command to resume")
        finally:
            if br:
                br.close()
    for d in list(DRIVERS):  # a Chrome window still open here (its thread crashed or was stopped): close it
        kill_tree(d)
        log("closed a Chrome window left open")
    if a.test:
        log("test run: results, report and site left as they were")
    else:
        with METRICS.phase("outputs"):
            build_outputs(store, english=not a.no_translate)
    if any(e["kind"] == "fetch" for e in METRICS.events):
        METRICS.report()


if __name__ == "__main__":
    main()
