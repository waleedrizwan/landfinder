#!/usr/bin/env python3
"""
landfinder — scrape the cheapest land listings on earth and rank them by total price in USD.
Each site's houses-for-sale section is searched too, for ruins, fixer-uppers, old farmhouses and the odd castle.

Sources
  properstar    worldwide portal (~100+ countries), land plots sorted cheapest first
  landwatch     all 50 US states, sorted cheapest first
  kijiji        every Canadian province, land-for-sale sorted cheapest first
  realtor       REALTOR.ca, Canada's MLS: vacant land and houses per province, cheapest first
  olx           classifieds in Poland, Ukraine, Romania, Portugal, Bulgaria, Kazakhstan, Uzbekistan
  mercadolibre  classifieds in Argentina and Mexico
  ieichiba      Japanese owners selling empty houses (akiya) and land themselves
  homes         HOME'S akiya bank: land and empty houses in all 47 Japanese prefectures (houses ranked with the land)
  castles       Properstar's castle listings in the same countries as properstar
  zillow        lots and land in all 50 US states
  immobiliare   land for sale across Italy
  encuentra24   lots and land in Costa Rica, Panama, Nicaragua, Honduras, Guatemala and El Salvador

Usage
  ./run.sh                                       # everything: 250 listings per country/state/province, up to $200,000
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
import argparse, csv, glob, gzip, json, math, os, queue, random, re, shutil, signal, subprocess, sys, threading, time, unicodedata, warnings
from collections import Counter, deque
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

# REALTOR.ca (Canada's MLS): the map page's listing API takes a lat/lon box, so each province is searched by a box
# around it and kept to the listings the API says are in that province (the boxes overlap at the borders).
# slug -> (lat min, lat max, lon min, lon max, province codes)
REALTOR_BOXES = {
    "alberta": (48.9, 60.1, -120.1, -109.9, ("AB",)), "british-columbia": (48.2, 60.1, -139.2, -114.0, ("BC",)),
    "manitoba": (48.9, 60.1, -102.2, -88.9, ("MB",)), "new-brunswick": (44.5, 48.2, -69.2, -63.6, ("NB",)),
    "newfoundland": (46.4, 60.6, -68.0, -52.4, ("NL",)), "nova-scotia": (43.2, 47.2, -66.6, -59.5, ("NS",)),
    "ontario": (41.5, 57.0, -95.3, -74.2, ("ON",)), "prince-edward-island": (45.8, 47.2, -64.6, -61.8, ("PE",)),
    "quebec": (44.8, 62.8, -79.9, -56.9, ("QC",)), "saskatchewan": (48.9, 60.1, -110.2, -101.2, ("SK",)),
    "territories": (59.9, 83.3, -141.2, -60.9, ("YT", "NT", "NU")),
}
CA_PROVINCE_CODES = {
    "alberta": "AB", "british columbia": "BC", "manitoba": "MB", "new brunswick": "NB", "newfoundland & labrador": "NL",
    "newfoundland and labrador": "NL", "nova scotia": "NS", "ontario": "ON", "prince edward island": "PE", "quebec": "QC",
    "québec": "QC", "saskatchewan": "SK", "yukon": "YT", "northwest territories": "NT", "nunavut": "NU",
}
REALTOR_PER_PAGE = 50  # the API's page size; it stops at 600 records per query, so a province gives up to 12 pages
REALTOR_MIN_CAD = 1000  # $1 and $100 placeholders sit below this

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
# listings are grouped in price bands, newest first; we take every live listing up to ¥100M (about $630k).
IEICHIBA_BANDS = ("0-100manyen", "100-200manyen", "200-1000manyen", "1000-10000manyen")

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

# Places searched deeper than the rest by default (--more): California has ~23,500 lots on LandWatch alone.
MORE_DEFAULT = "california=2000"

ALL_SOURCES = "properstar,castles,landwatch,kijiji,realtor,olx,mercadolibre,ieichiba,homes,zillow,immobiliare,encuentra24"

AREA_TO_M2 = {"SquareMeter": 1.0, "SquareFoot": 0.09290304, "Hectare": 10000.0,
              "Acre": ACRE_M2, "SquareKilometer": 1e6, "SquareYard": 0.83612736}


STOP = threading.Event()
_LOG_LOCK = threading.Lock()


def log(msg):
    name = threading.current_thread().name
    tag = "" if name == "MainThread" else "%s " % name
    line = "[%s] %s%s" % (datetime.now().strftime("%H:%M:%S"), tag, msg)
    with _LOG_LOCK:
        print(line, flush=True)
    LIVE.lines.append(line)


# ----------------------------------------------------------------------------- helpers

def num(s):
    """float of '1,250' or 3.5; None for text, and for nan/inf (which would reach data.json as NaN, which no
    browser's JSON.parse accepts: one such value takes the whole site down)."""
    try:
        v = float(str(s).replace(",", "").strip())
    except Exception:
        return None
    return v if math.isfinite(v) else None


def safe_url(u):
    """Only http(s) links go on the site: a listing URL is put in an href as-is, so any other scheme
    (javascript:, data:) copied from a listing site would run or render in the visitor's browser."""
    return isinstance(u, str) and u.startswith(("http://", "https://"))


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
    try:
        usd = amount / rate if rate else None
    except TypeError:
        return None
    return usd if usd is not None and math.isfinite(usd) else None


# English, French and Spanish, plus the OLX languages: Polish, Ukrainian/Russian, Romanian, Bulgarian, Portuguese, Uzbek
PAYMENT_RX = re.compile(r"(\d\s*(?:/\s*)?month\b|/\s*mo\b|/\s*month|per month|a month|monthly|\$\s*\d+\s*down|down payment"
                        r"|cuotas|anticipo|financiad|financiaci|\braty\b|розстроч|рассроч|presta[çc][õo]es)", re.I)
WANTED_RX = re.compile(r"^\s*(wanted|wtb|looking for|iso\b|in search of|i will buy|will buy|kupi[eę]\b|куплю|cump[aă]r\b|compro\b|busco\b|procuro\b)", re.I)
MIN_PRICE = 1500  # USD-ish floor for Properstar and Mercado Libre searches; cheaper "prices" there are per-m2 or placeholders
TILE_SCALE = 0.5  # tiled windows draw pages at half size
CLAIM_RX = re.compile(r"\b(access deeds?|membership|timeshare|mining claims?|lode claims?|placer claims?|gold claims?|mineral claims?|mineral rights?|unpatented|lode gold|mining|prospecting)\b", re.I)
LEASE_RX = re.compile(r"(rent[- ]to[- ]own|\bfor lease\b|\bto let\b|\bp/m\b|\bpcm\b|\b(?:sub-?)?lease[ds]?\b|\bleasing\b|\bleasehold\b|\bground rent\b|\bfor rent\b|\brental\b|\brenting\b"
                      r"|\b(?:and|or|&|/)\s*rent\b|à louer|\btenants?\b|/\s*month|\bmonthly\b|en alquiler|se alquila|\balquiler\b|\barriendo\b"
                      r"|dzier[żz]aw|wynaj[eę]|do wynaj|оренд|аренд|\bздам\b|\bздаю\b|\bсдам\b|\bсдаю\b|под наем|[iî]nchiri|arrend|\balug|\bijara"
                      # Mexican agencies write "renta" for rent ("Terreno en venta o renta"); Properstar translates it as "sale and rent"
                      r"|\ben renta\b|\bse renta\b|\brentar\b|\brenta\b|\bse rentan\b"
                      # Turkish, Italian, German, Dutch, Czech and Croatian rentals
                      r"|kiral[ıi]k|\baffitt|\bmiete\b|vermiet|\bte huur\b|pron[aá]jem|\bnajam\b|iznajm"
                      # Ukrainian/Russian "is being let" ("Здається будинок"), Bulgarian "отдава се" and holiday lets by the
                      # night, Bolivian anticrético (a deposit for the use of a house, handed back at the end)
                      r"|\bзда[єе]ться|\bсда[её]тся|(?<!да )\bсе отдава\b|\bотдавам\b|anticr[eé]tic"
                      # commercial space quoted the way leases are: "$17.50/sf/yr", "NNN"
                      r"|/\s*(?:sf|sq\.?\s?ft\.?)\s*/\s*(?:yr|year|mo|month)\b|\bper (?:sq\.?\s?ft\.?|square foot) per (?:year|month)\b|(?-i:\bNNN\b)|\btriple net\b"
                      # a home whose land is rented: mobile homes on a park pad, cottages on leased lots
                      r"|(?<!no )(?<!no pad or )(?<!no lot or )\b(?:pad|lot|site) (?:rent|fees?)\b|\bterrain lou[ée])", re.I)
# Japanese leasehold (借地権付き): the house is sold, the land under it stays rented. Only the title counts: descriptions
# say "賃貸も可能" (could also be rented out) about land that is for sale.
JP_LEASEHOLD_RX = re.compile(r"借地")
# the bare word in a title ("Private Unit - Casa Dojolu — Rent"); descriptions say "rent it out" about land for sale
RENT_TITLE_RX = re.compile(r"\brent\b|\brenta\b|\balquiler\b|\baluguel\b|\bwynajem\b|\bаренда\b|\bоренда\b", re.I)
RENT_TITLE_UNDER_USD = 15000
# REALTOR.ca feeds commercial land for lease into Kijiji's land-for-sale section, priced at the monthly rent, with nothing
# in the ad saying so ("Great functional open space in an industrial area", CAD 5,000). An agent's industrial or commercial
# lot in Canada doesn't sell for that; a real sale ad talks of buyers, offers or the seller.
REALTOR_RX = re.compile(r"\(id:\d+\)|MLS®|\bMLS\b")
MLS_NUMBER_RX = re.compile(r"\bMLS\W{0,4}([A-Z]{0,2}\d{6,10})\b", re.I)  # "MLS® 202627276", "MLS# A1299992", "MLS #10392452"
LEASE_USE_RX = re.compile(r"\b(industrial|commercial|development land|storage|parking|warehouse|retail|offices?|business|employment"
                           r"|yard space|open space|laydown|outdoor storage)\b", re.I)
SALE_WORDS_RX = re.compile(r"\b(purchas\w*|buyers?|sellers?|all offers|offers? to purchase|sale|sold|own(?:ership)?|invest\w*|title|deed|taxes)\b", re.I)
KIJIJI_LEASE_UNDER_USD = 8000  # the leases seen run CAD 5,000-8,500 a month; Prairie-town commercial lots sell from ~CAD 15,000
PER_UNIT_RX = re.compile(r"(\bx|\bper|\bpor|/|\bel|\ble|\bpar|\bthe)\s*(m2\b|m²|mt2|mts2|metro\b(?!\s*(?:area|region|city|station|stop|line|bus|rail|access|transit))|sq\.?\s*m\b|vara|acre\b|ac\b|hect|ha\b|sotk|square met)"
                         r"|(?:(?:\bza|\bза|\bpe|\bper|\bx)\s+|/\s*)(?:1\s*)?(m2\b|m²|mp\b|mkw\b|m\s*kw\b|metr|сот|м2|м²|кв\.?\s*м|ar\b|ara\b"
                         r"|га\b|гектар|hektar|hectar|ha\b|dekar|декар|дка\b)"
                         r"|\bна\s+(?:1\s*)?(?:декар|дка\b|сотк|кв\.?\s*м|м2|м²)"  # Bulgarian "цена на декар"
                         r"|\b(?:por|x|c/)\s*(?:manzana|mz)\b"  # Central American "precio por manzana" (0.7 ha)
                         r"|\$\s?\d[\d.,]*\s*k?\s*(?:an|a|per|/)\s*(?:acre\b|ac\b|hectare|ha\b|m2\b|m²)"  # "$1,500 an acre", "$1,500/ac"
                         r"|\b(?:so|su)[‘'ʻ’`]?t(?:ig[‘'ʻ’`]?i|ixi|kasi)\s*\$?\s*\d|сотк[аи]\s*[-–—:]?\s*\$?\s*\d", re.I)  # Uzbek/Russian "its sotka [costs] 500"
# "1/8 udziału", "udział 1/4 w prawie własności", "1/2 interest": only part of the property is for sale
SHARE_RX = re.compile(r"(\b\d+\s*/\s*\d+\s*(?:udzia|cz[eę][sś]ci|w drodze|w prawie|частк|долю|доли|долі|share|interest)|udzia[łl]\w*\s+\d+\s*/\s*\d+|\(\s*1\s*/\s*\d+\s*\)"
                      r"|^\s*udzia[łl]|sprzeda\w*\s+udzia[łl]|udzia[łl]\w*\s+w\s+(?:działk|nieruchom|grunc|prawie)"
                      # "1/3 Ruína em ...", "Cota de 5/16 din casa", "1/2 идеална част от къща"
                      r"|^\s*[1-9]\s*/\s*(?:[2-9]|[1-9]\d)\s(?!\s*-?\s*(?:acres?|ac\b|ha\b|hect|miles?|mi\b|sq|m2|m²|lots?|sections?|of an? acre))|\bcota(?:\s*-?\s*parte|\s+de\s+\d)|\bquota[- ]parte|идеалн\w*\s+част)", re.I)  # not "+ udział w drodze"
# timeshare weeks and resort fractions ("1/4 share ownership suite", "Fractional Ownership cottage", "50% Undivided
# Ownership"): anywhere in the text, since the title is usually just the address
FRACTION_RX = re.compile(r"\btime[- ]?shar\w+|\bfractional (?:ownership|interest|share|land share)|\b(?:quarter|half|full)[- ]share\b"
                         r"|\b1/\d+\s*(?:th\s+)?(?:share|ownership|fractional|undivided|interest)\b|\bshare ownership\b"
                         r"|\d+\s?%\s+undivided|\bundivided\s+(?:\d+\s*/\s*\d+\s+|\d+\s?%\s+|one[- ]\w+\s+)?(?:interest|ownership|share)\b", re.I)
# French/Belgian viager, Italian nuda proprietà: the price is a down payment ("bouquet") or excludes the right to live there
ANNUITY_RX = re.compile(r"\bviager|\blife annuity|\brente viag[eè]re|\bnue[- ]propri[ée]t[ée]|\bnuda propriet[aà]|\bbare ownership\b|\blijfrente", re.I)
# a cabin, kiosk or trailer sold on its own, for the buyer to haul away
MOVE_RX = re.compile(r"(?<!prepare )\b(?:to|must) be (?:moved|relocated)\b|\b(?:buyer|purchaser) (?:to|must|will) (?:move|relocate)\b"
                     r"|\bà déménager\b", re.I)
# Polish static caravans ("domek holenderski na kołach"): in the title only, since plots are sold with one standing on them
MOVE_TITLE_RX = re.compile(r"holendersk|\bna kołach\b|\bprzyczep\w* kemping|\bdom(?:ek|ki) mobiln", re.I)
ALLOTMENT_RX = re.compile(r"\brod\b|ogr[oó]d\w*\s+działkow|działkow\w*\s+ogr[oó]d", re.I)  # Polish allotment gardens (ROD)
CEMETERY_RX = re.compile(r"cemit[eé]rio|sepultur|jazigo|cementerio|cmentarz|cimitir|кладбищ|цвинтар|гробищ|\bnicho\b|b[oó]veda"
                         r"|cemetery\s+(?:plots?|lots?|spaces?|sites?)|private cemetery|burial|\bgrave\b|columbari", re.I)
# Central American memorial parks ("Lote en Camposanto La Piedad", "Jardines del Recuerdo", "espacio para 3 tumbas"),
# read in the title and description of a grave-sized or cheap plot only: Camposanto is also a town near Modena
MEMORIAL_RX = re.compile(r"campo\s*santo|\btumbas?\b|\btombs?\b|\bnichos?\b|jard[ií]n\w* del recuerdo|jard[ií]n de paz"
                         r"|gard\w+ of (?:the )?remembrance|burial (?:niche|plot|space)", re.I)
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
# Homes listed at a monthly rent: a sale is never under $40 per m2 of floor space (a Bulgarian village house at
# EUR 3,000 for 80 m2 is $44), while rents run $5-30 per m2 a month; a Manhattan rent is higher per m2, but no home
# in a rich country sells for under $10,000. Cheap ruins in Portugal, Italy, Spain, France and the Balkans are real,
# so those countries aren't on the list; Japan's empty houses are handled by CHEAP_IS_REAL.
HOUSE_MIN_USD_M2, HOUSE_FLOOR_USD = 40, 10000
# Under this, the house listings seen were holiday lets, monthly rents, placeholders ("T3 house, garden, garage" at
# EUR 1,300) and cabins sold without land; a ruin sold as a ruin can really cost this little, so those stay.
HOUSE_ANYWHERE_USD = 3000
PRICEY_COUNTRIES = set("US CA AU NZ GB IE DE NL BE LU AT CH DK SE NO FI IS SG HK AE QA KW IL KR AW KY BM".split())


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
    if SHARE_RX.search(rec.get("title") or "") or SHARE_RX.search(rec.get("title_en") or "") or FRACTION_RX.search(text):
        f.append("share of a property - not the whole plot")
    if ANNUITY_RX.search(text):
        f.append("share of a property - life annuity or bare ownership, not the full price")
    if MOVE_RX.search(text) or MOVE_TITLE_RX.search(rec.get("title") or ""):
        f.append("building only - to be moved, no land")
    if (burial_plot(rec.get("title")) or burial_plot(rec.get("title_en"))
            or (((acres or 0) < 0.01 or (usd or 0) < 10000) and MEMORIAL_RX.search(text))):
        f.append("burial plot - not land you can use")
    if usd and (rec.get("currency") or "USD") != "USD":
        if any((num(n.replace(".", "")) or 0) > usd * 3 for n in USD_IN_TEXT_RX.findall(rec.get("title") or "")):
            f.append("wrong currency - the title gives a much higher US dollar price")
    if rec.get("source") == "olx" and rec.get("country") == "PL" and ALLOTMENT_RX.search(text):
        f.append("allotment garden plot - usage rights, not land ownership")
    house = (rec.get("kind") == "houses" and rec.get("source") not in AKIYA) or "homes-for-sale" in (rec.get("url") or "")
    if house:
        f.append("house listing - not vacant land")
    # a home priced at a few dollars per m2 of floor, or a few thousand dollars where no home sells for that, is a
    # monthly rent fed to the site as a sale (MLS leases on Properstar, "1020 Vail View Drive" timeshare weeks)
    if usd and rec.get("living_m2") and usd / rec["living_m2"] < HOUSE_MIN_USD_M2 and not real:
        f.append("lease/rental - under $%d per m2 of floor space is a monthly rent, not a house price" % HOUSE_MIN_USD_M2)
    elif house and usd is not None and usd < HOUSE_FLOOR_USD and rec.get("country") in PRICEY_COUNTRIES and not real:
        f.append("lease/rental - no home in this country sells for under $%s; this is a monthly rent" % format(HOUSE_FLOOR_USD, ","))
    if (house and usd is not None and usd < HOUSE_ANYWHERE_USD and not real and "lease/rental" not in "; ".join(f)
            and "ruin" not in special_tags(rec)):
        f.append("price too low for a house - likely a rent, a deposit or a placeholder")
    if rec.get("source") == "kijiji" and usd is not None and usd < 3000:
        f.append("price too low for Canada - likely a deposit, lease or placeholder")
    elif (rec.get("source") == "kijiji" and usd is not None and usd < KIJIJI_LEASE_UNDER_USD and REALTOR_RX.search(text)
          and LEASE_USE_RX.search(text) and not SALE_WORDS_RX.search(text)):
        f.append("lease/rental - a REALTOR.ca commercial lot at a monthly rent, not a sale price")
    if rec.get("source") in ("properstar", "encuentra24") and usd is not None and usd < 1500:
        f.append("price may be per m2/acre/hectare (agency listing under $1,500)")
    titles = " | ".join(str(rec.get(k) or "") for k in ("title", "title_en"))
    # a bare "rent" in the title counts at a rent-sized price; above that it's "rent it out" about an income property
    if (LEASE_RX.search(text) or (usd is not None and usd < RENT_TITLE_UNDER_USD and RENT_TITLE_RX.search(titles))
            or JP_LEASEHOLD_RX.search(rec.get("title") or "")):
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


MONTH_RX = r"\s*(?:/|per|a|each|pcm|p/m)?\s*(?:mo\b|month|p/m\b|pcm\b|pm\b)"


def monthly_rent(rec):
    """True when the text quotes the listing's own price as a monthly amount: '4,500 per month', '4500 p/m',
    '$4,500 a month'. Only the price itself counts, so a sale that mentions taxes of $50 a month stays a sale."""
    p = rec.get("price")
    if not p:
        return False
    text = " ".join(str(rec.get(k) or "") for k in ("title", "title_en", "description"))
    amt = re.escape("{:,.0f}".format(p)).replace(",", ",?")
    return bool(re.search(r"(?<![\d.])\$?\s?%s(?!\d)%s" % (amt, MONTH_RX), text, re.I))


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
    if rec.get("source") == "kijiji" and not rec.get("acres"):  # no "Size (acres)" field: what the text says
        # (only then: the text also mentions nearby parks and farms, "minutes from the 300 acre park")
        rec["acres"] = acres_from_text(rec.get("title")) or acres_from_text(rec.get("description"))
    if rec.get("source") == "landwatch" and ((rec.get("title") or "").strip() in ("", "•") or "living_m2" not in rec):
        # home cards saved before landwatch_card(): the title was the "•" between beds and baths, and no floor area
        m = re.search(r"\|\s*[\d.,]+\s*acres?\s*\|(.*)", rec.get("description") or "", re.S)
        addr, living = landwatch_card(m.group(1)) if m else ("", None)
        if (rec.get("title") or "").strip() in ("", "•") and addr:
            rec["title"] = addr
        if living and not rec.get("living_m2"):
            rec["living_m2"] = living
    if rec.get("source") == "landwatch" and rec.get("lat") is None:  # place it at its ZIP code's center
        ll = zip_location(rec.get("title")) or zip_location(rec.get("description"))
        if ll:
            rec["lat"], rec["lon"] = ll
    rec["price_usd"] = to_usd(rec.get("price"), rec.get("currency"), rates)
    a = rec.get("acres")
    a = rec["acres"] = round(a, 4) if isinstance(a, (int, float)) and math.isfinite(a) and a > 0 else None
    rec["usd_per_acre"] = (rec["price_usd"] / a) if (rec["price_usd"] and a) else None
    rec["flags"] = flag(rec)
    if monthly_rent(rec) and "lease/rental" not in rec["flags"]:
        rec["flags"] = "; ".join(x for x in (rec["flags"], "lease/rental - the price is a monthly rent") if x)
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
                                                   "total": None, "reached": None, "stop": None, "cursor": None})
        if "cursor" in e:  # where a price-band search (LandWatch, Zillow) is, so a stopped run resumes there
            s["cursor"] = e["cursor"]
            if e.get("total") is not None:
                s["total"] = e["total"]
            return
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
        page_ok(source_of(url))
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
        """Record why a search stopped: enough listings, end of results, price reached, page cap or fetch failed."""
        e = {"search": sid, "stop": why}
        with self.lock:
            self._tally(e)
            with open(self.search_path, "a") as f:
                f.write(json.dumps(e) + "\n")

    def note(self, sid, cursor, total=None):
        """Record where a price-band search is (and the site's result count for the whole search, once known)."""
        e = {"search": sid, "cursor": cursor}
        if total is not None:
            e["total"] = total
        with self.lock:
            self._tally(e)
            with open(self.search_path, "a") as f:
                f.write(json.dumps(e) + "\n")

    def add(self, recs, seen=True):
        """Append listings. seen=False keeps their scraped_at: a listing re-read for verification wasn't seen in a
        search, and stamping it now would move the site's 3-day window past everything scraped before it."""
        new = 0
        with self.lock, open(self.path, "a") as f:
            for r in recs:
                if not safe_url(r.get("url")):
                    continue
                if r["url"] not in self.urls:
                    self.urls.add(r["url"])
                    new += 1
                if seen or not r.get("scraped_at"):
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
    return next((s for s in ("properstar", "landwatch", "kijiji", "realtor", "olx", "mercadolibre", "ieichiba", "zillow",
                             "immobiliare", "encuentra24", "homes") if s in url), "other")


def mins(secs):
    return "%dm%02ds" % divmod(int(secs), 60)


class Metrics:
    """Timings and bytes for every page fetched, every Chrome window and every phase of a run. Each event goes to
    logs/metrics-<time>.jsonl as it happens; summary() adds them up at the end to show where the time and data go."""

    def __init__(self):
        self.events, self.lock, self.t0, self.file = [], threading.Lock(), time.time(), None
        self.run = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.path = os.path.join(HERE, "logs", "metrics-%s.jsonl" % self.run)

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
        t, main = time.time(), threading.current_thread().name == "MainThread"
        LIVE.phases[name] = t
        if main:  # the HTTP sites' phases run in their own threads alongside; the dashboard shows those per thread
            LIVE.phase = name
        try:
            yield
        finally:
            self.add("phase", name=name, secs=round(time.time() - t, 2))
            if main:
                LIVE.phase = None

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

    def run_summary(self, args=""):
        """This run in one line for data/runs.jsonl: pages, failures and blocks per site, for the dashboard's history."""
        with self.lock:
            ev = list(self.events)
        pages, failed, blocks = Counter(), Counter(), Counter()
        for e in ev:
            if e["kind"] == "fetch":
                pages[e["source"]] += 1
                failed[e["source"]] += not e["ok"]
            elif e["kind"] == "block":
                blocks[e["source"]] += 1
        return dict(run=self.run, start=datetime.fromtimestamp(self.t0).isoformat(timespec="seconds"),
                    secs=round(time.time() - self.t0), args=args, pages=dict(pages), failed=dict(failed), blocks=dict(blocks),
                    gave_up=[e["source"] for e in ev if e["kind"] == "gave_up"])

    def report(self, args=""):
        lines = self.summary()
        with open(self.path.replace(".jsonl", "-summary.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        for line in lines:
            log(line)
        log("metrics: %s (+ -summary.txt)" % os.path.relpath(self.path, HERE))
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "runs.jsonl"), "a") as f:
            f.write(json.dumps(self.run_summary(args)) + "\n")


METRICS = Metrics()


class Live:
    """What the run is doing right now, for the dashboard window (dashboard.py, fed through data/progress.json).
    The numbers come from METRICS' events; this only tracks each thread's current page."""

    def __init__(self):
        self.lock = threading.Lock()
        self.lines = deque(maxlen=80)  # the latest log lines
        self.workers = {}  # thread name -> what it's fetching now
        self.phases = {}  # phase name -> when it started
        self.phase, self.jobs_total, self.args, self.store, self.t0, self.ended = None, 0, "", None, time.time(), False
        self.step = None  # the step of the current phase, once the searches are done: dict(text, done, total, since)

    def now(self, url, label="", what="search"):
        with self.lock:
            self.workers[threading.current_thread().name] = dict(label=label or url, url=url, what=what, since=time.time(), busy=True)

    def set_step(self, text=None, done=None, total=None):
        """What the run is doing after the searches (verifying, translating, writing the outputs), with how far it has
        got when it can count: shown in place of the search progress, so the window doesn't look finished or stuck."""
        with self.lock:
            if text is None:
                self.step = None
            else:
                since = self.step["since"] if self.step and self.step["text"] == text else time.time()
                self.step = dict(text=text, done=done, total=total, since=since)

    def idle(self, note="finished"):
        with self.lock:
            self.workers[threading.current_thread().name] = dict(label=note, url="", what="", since=time.time(), busy=False)

    def snapshot(self):
        now = time.time()
        with METRICS.lock:
            ev = list(METRICS.events)
        sites, per_worker = {}, Counter()
        for e in ev:
            if e["kind"] == "fetch":
                s = sites.setdefault(e["source"], dict(pages=0, failed=0, blocked=0, passed=0, verify=0, recent=0, new=0, secs=0.0, via=e["via"]))
                s["pages"] += 1
                s["failed"] += not e["ok"]
                s["passed"] += bool(e.get("blocked")) and e["ok"]
                s["verify"] += e["what"] == "verify"
                s["secs"] += e["secs"]
                s["recent"] += METRICS.t0 + e["t"] > now - 60
                per_worker[e["worker"]] += 1
            elif e["kind"] == "parsed":
                sites.setdefault(e["source"], dict(pages=0, failed=0, blocked=0, passed=0, verify=0, recent=0, new=0, secs=0.0, via=""))["new"] += e["new"]
            elif e["kind"] == "block":
                sites.setdefault(e["source"], dict(pages=0, failed=0, blocked=0, passed=0, verify=0, recent=0, new=0, secs=0.0, via=e["via"]))["blocked"] += 1
        blocks = [dict(time=datetime.fromtimestamp(METRICS.t0 + e["t"]).strftime("%H:%M:%S"), source=e["source"], via=e["via"],
                       how=e["how"], detail=e["detail"], url=e["url"], worker=e["worker"])
                  for e in ev if e["kind"] == "block"][::-1][:60]
        history = []
        try:
            with open(os.path.join(DATA, "runs.jsonl")) as f:
                history = [json.loads(line) for line in f if line.strip()][-30:]
        except (OSError, ValueError):
            pass
        history.append(dict(METRICS.run_summary(self.args), live=True))
        done = sum(e["kind"] == "job" for e in ev)
        eta = None
        if self.phase == "chrome pool" and done >= 3 and self.jobs_total > done:
            eta = (now - self.phases["chrome pool"]) / done * (self.jobs_total - done)
        with self.lock:
            workers = [dict(name=n, pages=per_worker[n], since_s=round(now - w["since"]), **w) for n, w in self.workers.items()]
            step = dict(self.step, since_s=round(now - self.step["since"])) if self.step else None
            step and step.pop("since")
        workers.sort(key=lambda w: (w["name"][0] != "W", len(w["name"]), w["name"]))
        return dict(started=datetime.fromtimestamp(self.t0).strftime("%H:%M:%S"), elapsed_s=round(now - self.t0),
                    phase=self.phase, step=step, stopping=STOP.is_set(), args=self.args, jobs=dict(total=self.jobs_total, done=done),
                    eta_s=round(eta) if eta else None, listings_new=sum(s["new"] for s in sites.values()),
                    on_disk=len(self.store.urls) if self.store else None, workers=workers,
                    sites=[dict(source=k, **v) for k, v in sorted(sites.items())], blocks=blocks, history=history,
                    given_up=sorted(GIVEN_UP), give_up_after=GIVE_UP_AFTER, ended=self.ended, log=list(self.lines))


LIVE = Live()
_BLOCK_LOCK = threading.Lock()
WALLS = Counter()  # site -> blocked pages in a row (a page that parsed resets it)
GIVEN_UP = set()  # sites skipped for the rest of the run: they blocked GIVE_UP_AFTER pages in a row
GIVE_UP_AFTER = 3  # --give-up; 0 = keep trying every page


def page_title(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    return re.sub(r"\s+", " ", unescape(m.group(1))).strip() if m else ""


def via_of(fetch):
    return "chrome" if isinstance(fetch, Browser) else "http"


def note_block(source, kind, url, detail="", via="chrome"):
    """Record that a site refused or challenged us, i.e. took us for a bot: a bot-check page in Chrome (kind "wall":
    Properstar's Azure WAF, Cloudflare's "Just a moment", "Access Denied"), an HTTP 401/403/429/503 ("http 403"), a
    results page with no listing data on it ("no data": a captcha or sign-in page, or a new layout), or an API that
    answered with a wall instead of JSON. The event goes to the run's metrics and to data/blocks.jsonl, which the
    dashboard reads to show when, where and how often each site has blocked us, this run and across runs."""
    e = dict(time=datetime.now().isoformat(timespec="seconds"), run=METRICS.run, source=source, via=via, how=kind,
             detail=re.sub(r"\s+", " ", detail or "").strip()[:80], url=url)
    METRICS.add("block", **{k: v for k, v in e.items() if k not in ("time", "run")})
    gave_up = False
    with _BLOCK_LOCK:
        os.makedirs(DATA, exist_ok=True)
        with open(os.path.join(DATA, "blocks.jsonl"), "a") as f:
            f.write(json.dumps(e) + "\n")
        WALLS[source] += 1
        if GIVE_UP_AFTER and WALLS[source] >= GIVE_UP_AFTER and source not in GIVEN_UP:
            GIVEN_UP.add(source)
            gave_up = True
    if gave_up:  # retrying every remaining page would cost ~3 min each and only send the site more refused requests
        METRICS.add("gave_up", source=source, after=WALLS[source])
        log("%s has blocked %d pages in a row: skipping the rest of its pages this run (its listings on disk stay on "
            "the site; the next run tries again; --give-up 0 keeps trying)" % (source, WALLS[source]))


def page_ok(source):
    """A page from this site parsed (or a verify page loaded): its run of blocks is over."""
    with _BLOCK_LOCK:
        WALLS[source] = 0


def given_up(url_or_source):
    return (source_of(url_or_source) if "/" in url_or_source else url_or_source) in GIVEN_UP


def save_progress():
    """Write what the run is doing now to data/progress.json, atomically, for the dashboard window (dashboard.py)."""
    tmp = os.path.join(DATA, "progress.json.tmp")
    os.makedirs(DATA, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(LIVE.snapshot(), f)
    os.replace(tmp, os.path.join(DATA, "progress.json"))


def write_progress(every=2.0):
    """Keep data/progress.json current in a background thread, and honour the window's Stop button: a file called
    data/stop ends the run the way Ctrl-C does (the pages in progress finish, then the outputs are built)."""
    stop_file = os.path.join(DATA, "stop")
    if os.path.exists(stop_file):  # left over from an earlier run
        os.remove(stop_file)

    def run():
        while True:
            try:
                if os.path.exists(stop_file):
                    os.remove(stop_file)
                    if not STOP.is_set():
                        STOP.set()
                        log("stop requested from the dashboard - finishing the pages in progress, then saving")
                save_progress()
            except Exception as e:
                log("progress file: %s" % str(e)[:100])
            time.sleep(every)

    threading.Thread(target=run, name="progress", daemon=True).start()


def dashboard_python():
    """A Python with a usable Tk (8.6+) for the window: this one, else a Homebrew one. macOS's own Python ships Tk 8.5,
    which hangs on recent macOS; dashboard.py needs only the standard library, so any Python with Tk will do."""
    candidates = [sys.executable] + sorted(glob.glob("/opt/homebrew/bin/python3.1[0-9]"), reverse=True) + ["/usr/local/bin/python3"]
    for py in candidates:
        if not os.path.exists(py):
            continue
        try:
            p = subprocess.run([py, "-c", "import tkinter; print(tkinter.TkVersion)"], capture_output=True, text=True, timeout=20)
            if p.returncode == 0 and float(p.stdout.strip() or 0) >= 8.6:
                return py
        except Exception:
            pass
    return None


def open_dashboard():
    """Open the dashboard window (dashboard.py, a Tk app) as its own process; it reads data/progress.json."""
    py = dashboard_python()
    if not py:
        log("no dashboard window: no Python with Tk 8.6+ found (brew install python-tk@3.12); the run goes on without it")
        return
    try:
        subprocess.Popen([py, os.path.join(HERE, "dashboard.py"), DATA], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        log("couldn't open the dashboard window: %s" % str(e)[:100])


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
        if given_up(url):
            return None
        t0 = t = time.time()
        LIVE.now(url, self.label, what)
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
                    note_block(source_of(url), "wall", url, title, "chrome")
                    if given_up(url):  # that was the site's last chance this run: don't wait for a retry
                        break
                    time.sleep(wait)
                    lap("wait")
                    continue
                html = self.page.content()
                self._show_label()
                lap("read")
                if what == "verify":
                    page_ok(source_of(url))
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
        quit, and ignores a polite stop, so Chrome gets 15 s to close and then its driver 2 s to stop (Chrome is
        usually gone by then, and only the driver hangs); past that the driver process is killed and the run goes on."""
        driver = self.driver
        METRICS.add("window", launch_s=round(self.launch_s, 2), open_s=round(time.time() - self.opened, 2),
                    busy_s=round(self.busy, 2), pages=self.pages, bytes=self.net["bytes"],
                    requests=self.net["requests"], types=dict(self.net["types"]), hosts=dict(self.net["hosts"]))
        try:
            for step, limit, what in ((self.browser.close, 15, "Chrome didn't close"),
                                      (self.pw.stop, 2, "the Playwright driver didn't stop")):
                def stop_driver(what=what, limit=limit):
                    kill_tree(driver)
                    log("  %s within %ds; stopped it and its Playwright driver" % (what, limit))

                timer = threading.Timer(limit, stop_driver)
                timer.daemon, timer.name = True, threading.current_thread().name  # logs under the window's own name
                if driver:
                    timer.start()
                try:
                    step()
                except Exception:
                    pass
                finally:
                    timer.cancel()
        finally:
            DRIVERS.discard(driver)


class Plain:
    def __init__(self, delay=1.5):
        self.delay = delay
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

    def get(self, url, what="search"):
        if given_up(url):
            return None
        t0, html, statuses, nbytes = time.time(), None, [], 0
        LIVE.now(url, "", what)
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
                    if what == "verify":
                        page_ok(source_of(url))
                    break
                log("  HTTP %d (attempt %d)" % (r.status_code, attempt + 1))
                if r.status_code in (401, 403, 429, 503):  # refused, rate-limited or a bot wall
                    note_block(source_of(url), "http %d" % r.status_code, url, page_title(r.text), "http")
                if r.status_code == 404 or given_up(url):
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


def parse_more(spec):
    """'california=2000,texas=1000' -> {"california": 2000, "texas": 1000}: searches to take further than --listings."""
    out = {}
    for part in csv_list(spec, []):
        key, _, n = part.partition("=")
        if not n.strip().isdigit() or not key.strip():
            raise ValueError("--more takes place=listings pairs like california=2000, not %r" % part)
        out[key.strip().lower()] = int(n)
    return out


def search_limits(sid, a, kind="land"):
    """(believable listings wanted, most pages) for one search. A place named in --more gets its own listing count,
    and its house search and page cap grow by the same ratio, on every site that searches that place."""
    want, pages = (a.listings if kind == "land" else a.house_listings), a.pages
    more = (getattr(a, "more", None) or {}).get(sid.split()[1] if " " in sid else "")
    if more and more > a.listings:
        ratio = more / a.listings
        want, pages = math.ceil(want * ratio), math.ceil(pages * ratio)
    return want, pages


def search_pages(store, sid, a, cap=None, kind="land"):
    """Page numbers to fetch for one search, cheapest first, until it has a.listings believable listings
    (a.house_listings for houses; more for a place in --more) or reaches the page cap. The caller stops sooner at
    the end of the results or a failed fetch, and records why."""
    want, pages = search_limits(sid, a, kind)
    if (store.searches.get(sid) or {}).get("stop") == "end of results":
        return  # finished on an earlier run that this one resumes
    for p in range(1, min(pages, cap or pages) + 1):
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
        m2 = living = None
        for v in [area] + (area.get("values") or []):
            unit = ((v.get("unit") or {}).get("id"))
            if unit not in AREA_TO_M2:
                continue
            if v.get("land") and m2 is None:
                m2 = v["land"] * AREA_TO_M2[unit]
            if (v.get("living") or v.get("internal")) and living is None:  # a home's floor space
                living = (v.get("living") or v.get("internal")) * AREA_TO_M2[unit]
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
            "living_m2": round(living, 1) if living else None,
            "lat": loc.get("latitude"),
            "lon": loc.get("longitude"),
            "url": "https://www.properstar.com/listing/%s" % L.get("id"),
            "kind": kind,
        })
    return out, int(res.get("total") or 0)


def properstar_url(slug, p, kind="land", band=None):
    """band = (lowest, highest) price; Properstar's price filter is in USD whatever currency a listing is in."""
    if kind == "castles":  # Properstar's castle subtype: /buy/castle on its own isn't a real search and lists everything
        url = "https://www.properstar.com/%s/buy/house/castle/price-low-to-high" % slug
        return url + ("?p=%d" % p if p > 1 else "")
    # price.min skips the "EUR 5" listings that are really per-m2 prices or placeholders
    url = "https://www.properstar.com/%s/buy/%s/price-low-to-high?price.min=%d" % (
        slug, "land-plot" if kind == "land" else "house", band[0] if band else MIN_PRICE)
    if band:
        url += "&price.max=%d" % band[1]
    return url + ("&p=%d" % p if p > 1 else "")


def crawl_properstar_one(br, store, slug, a, kind="land"):
    """kind: land, houses, or castles (the castle subtype of houses, so castles beyond the cheapest houses show up).
    Castles are few and dear, so they're read cheapest first as they come; land and houses go by price slice."""
    if kind != "castles":
        return crawl_sliced(store, search_id("properstar", slug, kind), a, kind,
                            lambda band, p: properstar_url(slug, p, kind, band),
                            html_loader(br, "properstar", slug, kind, parse_properstar, PROPERSTAR_READY), 20, floor=MIN_PRICE)
    sid, total_new = search_id("castles", slug, "land"), 0
    for p in search_pages(store, sid, a, kind="houses"):
        url = properstar_url(slug, p, kind)
        if url in store.done:
            continue
        br.label = "%s %s  page %d  (properstar)" % (slug, kind, p)
        html = br.get(url, ready=PROPERSTAR_READY)
        if not html:
            store.stop(sid, "fetch failed")
            break
        recs, total = parse_properstar(html, slug, "houses")
        if recs is None:  # a bot check or a new page layout, not the end of the results
            log("properstar %s page %d: no listing data on the page (blocked?), skipping the rest" % (slug, p))
            note_block("properstar", "no data", url, page_title(html))
            store.stop(sid, "fetch failed")
            break
        total_new += store.page(sid, url, recs, total)
        if not recs or p * 20 >= total:
            store.stop(sid, "end of results")
            break
    return total_new


def landwatch_card(rest):
    """(address, floor area in m2) from a LandWatch card's text after its acreage: land cards go straight to the
    address, home cards put "3 beds | • | 2 baths | • | 1,650 sqft" first."""
    parts = [x.strip() for x in rest.split(" | ")]
    spec = re.compile(r"^(?:[\d.,]+\s*(?:beds?|baths?|sq\.? ?ft|sqft)\b|[•·|]*$)", re.I)
    addr = next((x for x in parts if not spec.match(x)), "")
    sqft = next((num(m.group(1)) for m in (re.match(r"^([\d,]+)\s*(?:sq\.? ?ft|sqft)\b", x, re.I) for x in parts[:8]) if m), None)
    if not any(re.match(r"^[\d.,]+\s*(?:beds?|baths?)\b", x, re.I) for x in parts[:8]):
        sqft = None  # a land card: the figure is the lot again ("9.51 acres | 414,255 sqft")
    return addr, (round(sqft * 0.092903, 1) if sqft and sqft <= 20000 else None)  # bigger: the lot or the whole building


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
        addr, living = landwatch_card(text[am.end():]) if am else ("", None)
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
            "living_m2": living,
        })
    m = re.search(r">\s*([\d,]+)\s+Listings?\s*<", html)
    if not out and not m:
        return None, None  # neither listings nor a result count: not a search page (an error page served with 200)
    return out, (int(num(m.group(1))) if m else None)


# Every search is spread over the prices up to --reach instead of taking only the cheapest listings: a market's
# cheapest 250 lots are all desert parcels or village plots at a few thousand dollars, and land at $100,000-$200,000
# is still cheap. The range is cut into equal price slices and each slice gives an equal share of the listings
# wanted, cheapest first within the slice; what a thin slice can't give, the next ones make up. A market with no
# more listings under --reach than the search wants is read straight through instead.
REACH_DEFAULT = 200_000
SLICES_MOST, SLICE_SHARE = 20, 12  # at most 20 slices, and few enough that each one's share is 12 listings or more


def slice_count(want):
    return max(1, min(SLICES_MOST, want // SLICE_SHARE))


def price_slices(n, reach):
    """[(lowest, highest price in USD), ...]: n equal slices covering 0..reach."""
    return [(reach * i // n, reach * (i + 1) // n - 1) for i in range(n)]


def slice_target(store, sid, want, i, n):
    """Believable listings the search should have once slice i of n is done: the slice's own share on top of what
    the search has (a page holds more than a share, and that surplus mustn't let the next slices be skipped), or
    more when earlier slices fell short."""
    return max(store.believable(sid) + math.ceil(want / n), math.ceil(want * (i + 1) / n))


def crawl_sliced(store, sid, a, kind, url_for, load, per_page, max_pages=20, currency="USD", floor=0,
                 most_slices=SLICES_MOST):
    """One search, spread over the price slices up to --reach.
    url_for(band, p) = the URL of page p of the listings priced within band = (lowest, highest), in the site's
    currency, cheapest first. load(url, band, p) fetches and parses it: (listings, the site's result count for the
    band or None, how many results the page held before any were filtered out), or None when the fetch failed or the
    page wasn't a results page (load reports that itself). floor = the site's lowest price worth searching, in its
    currency; most_slices = fewer, wider slices for a site that allows few requests. A stopped run resumes at the
    slice it was on."""
    want, cap = search_limits(sid, a, kind)
    reach = getattr(a, "reach", REACH_DEFAULT)
    rate = 1 if currency == "USD" else ((getattr(store, "rates", None) or {}).get(currency) or 1)
    tally = store.searches.get(sid) or {}
    if tally.get("stop") == "end of results":
        return 0  # finished on an earlier run that this one resumes
    total_new, fetched, cur = 0, 0, tally.get("cursor")
    if cur and cur[0] >= cur[1]:
        return 0  # every slice was done on an earlier run
    if not cur:  # first, the whole range: how many listings it holds decides between slices and reading it through
        whole = (floor, max(floor, int(reach * rate) - 1))
        url = url_for(whole, 1)
        res = load(url, whole, 1)
        fetched += 1
        if res is None:
            store.stop(sid, "fetch failed")
            return 0
        recs, total, raw = res
        cur = [0, 1 if total is not None and total <= want else min(most_slices, slice_count(want))]
        if cur[1] == 1 and url not in store.done:
            if recs:
                total_new += store.page(sid, url, recs)
            if total <= per_page or not raw:  # and that one page was all of it
                cur[0] = 1
        store.note(sid, cur, total)
    start, n = cur
    slices = price_slices(n, reach)
    # the page cap is shared out between the slices (two pages each at least), so a site where few listings on a
    # page are believable can't spend it all on the cheap slices and never reach the dear ones. A cap too small to
    # share (--test's two pages) is simply the most pages the search fetches
    quick = cap < n
    allowance = max(2, cap // n)
    capped = False
    for i in range(start, n):
        band = (max(int(slices[i][0] * rate), floor), int((slices[i][1] + 1) * rate) - 1)
        target, seen = (want if n == 1 else slice_target(store, sid, want, i, n)), set()
        for p in range(1, max_pages + 1) if band[1] >= band[0] else ():
            if STOP.is_set():
                return total_new
            if store.believable(sid) >= target:
                break
            if n == 1 or quick:
                if fetched >= cap:
                    store.stop(sid, "page cap")
                    return total_new
            elif p > allowance:
                capped = True  # this slice has had its pages: on to the next
                break
            url = url_for(band, p)
            if url in store.done:
                continue
            res = load(url, band, p)
            fetched += 1
            if res is None:
                store.stop(sid, "fetch failed")
                return total_new
            recs, total, raw = res
            urls = {r["url"] for r in recs}
            if not raw or (recs and urls <= seen):  # empty, or the site repeats its last page past the end
                store.mark(url)
                break
            seen |= urls
            total_new += store.page(sid, url, recs)  # recs may be empty: a page of results that were all filtered out
            if total is not None and p * per_page >= total:
                break
        store.note(sid, [i + 1, n])
    store.stop(sid, "enough listings" if store.believable(sid) >= want else "page cap" if capped else "price reached")
    return total_new


def band_label(band):
    return "%s-%s" % (format(band[0], ","), format(band[1] + 1, ","))


def html_loader(fetch, src, key, kind, parse, ready=None):
    """load() for crawl_sliced on a site whose results are parsed from the page's HTML: parse(html, key, kind) gives
    the listings, or (listings, result count); None instead of the listings = not a results page."""
    def load(url, band, p):
        fetch.label = "%s %s  %s page %d  (%s)" % (key, kind, band_label(band), p, src)
        html = fetch.get(url, ready=ready) if ready else fetch.get(url)
        if not html:
            return None
        got = parse(html, key, kind)
        recs, total = got if isinstance(got, tuple) else (got, None)
        if recs is None:
            log("%s %s %s page %d: no listing data on the page (blocked?), skipping the rest" % (src, key, band_label(band), p))
            note_block(src, "no data", url, page_title(html), via_of(fetch))
            return None
        return recs, total, len(recs)
    return load


# LandWatch pages through at most 500 results of any search (20 pages of 25); a search with more answers with a
# sample thinned across its whole price range (which suits a slice: its share is spread over the slice), and pages
# past the 20th repeat. Past a search's last result the page is padded with "similar" listings from outside it.
LANDWATCH_MAX, LANDWATCH_PER_PAGE = 500, 25


def landwatch_url(st, kind="land", band=None, p=1):
    """A state's search page, cheapest first. band = (lowest, highest) price in USD."""
    url = "https://www.landwatch.com/%s-land-for-sale/%s" % (st, "" if kind == "land" else "homes/")
    if band:
        url += "price-%d-%d/" % band
    url += "sort-price-low-high"
    return url + ("/page-%d" % p if p > 1 else "")


def crawl_landwatch_one(br, store, st, a, kind="land"):
    parse = html_loader(br, "landwatch", st, kind, parse_landwatch, LANDWATCH_READY)

    def load(url, band, p):
        res = parse(url, band, p)
        if res is None:
            return None
        recs, total, _ = res
        if total is None:  # no result count on the page: go by whether it's full
            total = LANDWATCH_MAX if len(recs) >= LANDWATCH_PER_PAGE else (p - 1) * LANDWATCH_PER_PAGE + len(recs)
        recs = recs[:max(0, min(total, LANDWATCH_MAX) - (p - 1) * LANDWATCH_PER_PAGE)]  # without the padding
        return recs, total, len(recs)
    return crawl_sliced(store, search_id("landwatch", st, kind), a, kind, lambda band, p: landwatch_url(st, kind, band, p),
                        load, LANDWATCH_PER_PAGE)


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
    total = LIVE.jobs_total = len(jobs)
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
                if given_up("properstar" if src == "castles" else src):
                    done[0] += 1
                    METRICS.add("job", source=src, key=key, section=kind, secs=0, pages=0, new=0, skipped=True)
                    log("%-10s [%d/%d] %-28s skipped: the site is blocking us" % (src, done[0], total, key if kind == "land" else key + " " + kind))
                    continue
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
            LIVE.idle("finished" if not STOP.is_set() else "stopped")
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
    "realtor": lambda br, store, key, a, kind: crawl_realtor(br, store, key, a, kind),  # defined below
    "immobiliare": lambda br, store, key, a, kind: crawl_sliced(
        store, search_id("immobiliare", key, kind), a, kind, lambda band, p: immobiliare_url(key, p, kind, band),
        html_loader(br, "immobiliare", key, kind, parse_immobiliare), 25, currency="EUR", floor=1000),
    "encuentra24": lambda br, store, key, a, kind: crawl_sliced(
        store, search_id("encuentra24", key, kind), a, kind, lambda band, p: encuentra24_url(key, p, kind, band),
        html_loader(br, "encuentra24", key, kind, parse_encuentra24), 30),
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
        return None, None  # not a search page (a block page served with 200): the caller retries later
    try:
        apollo = json.loads(nd.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except Exception:
        return None, None
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


def kijiji_url(prov, loc, p, kind="land", band=None):
    cat = "b-land-for-sale/%s/%sc641" if kind == "land" else "b-house-for-sale/%s/%sc35"
    url = "https://www.kijiji.ca/" + cat % (prov, "" if p == 1 else "page-%d/" % p) + "%s?sort=priceAsc" % loc
    return url + ("&price=%d__%d" % band if band else "")


def crawl_kijiji(fetch, store, provinces, a, kinds=("land",)):
    jobs = [(k, prov, loc) for k in kinds for prov, loc in provinces.items()]
    for pi, (kind, prov, loc) in enumerate(jobs, 1):
        sid = search_id("kijiji", prov, kind)
        total_new = crawl_sliced(store, sid, a, kind, lambda band, p: kijiji_url(prov, loc, p, kind, band),
                                 html_loader(fetch, "kijiji", prov, kind, parse_kijiji), 40, currency="CAD")
        if STOP.is_set():
            return
        log("kijiji     [%d/%d] %-28s +%d listings" % (pi, len(jobs), sid.split(" ", 1)[1], total_new))


# ---- REALTOR.ca: Canada's MLS (real Chrome; the map page's own listing API, called from inside the page, which
# carries the site's cookies. Plain requests to the API get a bot wall.)

REALTOR_HOME = "https://www.realtor.ca/map#view=list"
REALTOR_API = "https://api2.realtor.ca/Listing.svc/PropertySearch_Post"


def realtor_url(prov, p, kind="land", band=None):
    """Not a page anyone can open: the key the store remembers a fetched API page by. band = (lowest, highest) CAD."""
    return "%s?province=%s&kind=%s%s&page=%d" % (REALTOR_API, prov, kind, "&price=%d-%d" % band if band else "", p)


def realtor_params(prov, p, kind="land", band=None):
    lat0, lat1, lon0, lon1, _ = REALTOR_BOXES[prov]
    q = {"LatitudeMin": lat0, "LatitudeMax": lat1, "LongitudeMin": lon0, "LongitudeMax": lon1, "ZoomLevel": 5,
         "TransactionTypeId": 2, "Sort": "1-A", "PriceMin": band[0] if band else REALTOR_MIN_CAD,  # for sale, price low to high
         "Currency": "CAD",
         "RecordsPerPage": REALTOR_PER_PAGE, "CurrentPage": p, "ApplicationId": 1, "CultureId": 1, "Version": "7.0"}
    if band:
        q["PriceMax"] = band[1]
    if kind == "land":
        q["PropertySearchTypeId"] = 6  # vacant land
    else:
        q["PropertySearchTypeId"] = 1  # residential
        q["BuildingTypeId"] = 1  # houses (not condos, mobile homes or lots)
    return {k: str(v) for k, v in q.items()}


def realtor_post(br, prov, p, kind="land", band=None):
    """One page of results from the API, as the parsed JSON, or None when the call failed or came back as a wall."""
    t = time.time()
    if "realtor.ca" not in (br.page.url or ""):  # the API answers the site's own pages, with their cookies
        if not br.get(REALTOR_HOME, settle_ms=6000):
            return None
    br.label = "%s %s  %spage %d  (realtor)" % (prov, kind, band_label(band) + " " if band else "", p)
    status, text = None, ""
    try:
        status, text = br.page.evaluate("""async ([url, params]) => {
            const r = await fetch(url, {method: 'POST', body: new URLSearchParams(params), credentials: 'include'});
            return [r.status, await r.text()]; }""", [REALTOR_API, realtor_params(prov, p, kind, band)])
    except Exception as e:
        log("  realtor %s page %d: %s" % (prov, p, str(e)[:100]))
    data = None
    if status == 200 and text.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    METRICS.add("fetch", via="chrome", source="realtor", what="search", url=realtor_url(prov, p, kind, band), ok=data is not None,
                attempts=1, blocked=[], errors=int(data is None), status=status, ready_hit=None, secs=round(time.time() - t, 2),
                html_kb=len(text) // 1024, bytes=len(text), requests=1, types={}, nav_s=0, ready_s=0,
                read_s=round(time.time() - t, 2), delay_s=0, wait_s=0)
    if data is None:
        log("  realtor %s page %d: no listing data (HTTP %s, %s)" % (prov, p, status, text[:60].replace("\n", " ")))
        if status:  # the API answered, but not with listings: a wall page or a refusal (None = the call itself failed)
            note_block("realtor", "no data" if status == 200 else "http %d" % status, realtor_url(prov, p, kind, band),
                       page_title(text) or text[:60], "chrome")
        return None
    time.sleep(br.delay + random.random() * br.delay)
    return data


def realtor_acres(land):
    """Land.SizeTotal: '60 x 100 FT|under 1/2 acre' (the lot's dimensions), '10 - 49.99 acres' (the lower bound),
    '56101298 sqft|Unknown', '970.6 m2'. None when the listing gives no size."""
    for seg in [x.strip() for x in ((land or {}).get("SizeTotal") or "").split("|")]:
        m = re.match(r"([\d.,]+)\s*x\s*([\d.,]+)\s*(ft|feet|m)\b", seg, re.I)
        if m:
            w, l = num(m.group(1)), num(m.group(2))
            if w and l:
                return w * l / (43560.0 if m.group(3).lower().startswith("f") else ACRE_M2)
        m = re.match(r"([\d.,]+)\s*-\s*[\d.,]+\s*(.*)", seg)
        if m:
            seg = m.group(1) + " " + m.group(2)
        v = acres_from_text(re.sub(r"^(?:under|over|about)\s+", "", seg, flags=re.I))  # "under 1/2 acre" is the site's own size band
        if v:
            return v
    return None


def parse_realtor(data, prov, kind="land"):
    """Listings in one API page, and how many the API says the province box has."""
    if not isinstance(data, dict) or "Results" not in data:
        return None, None
    total = (data.get("Paging") or {}).get("TotalRecords")
    codes = REALTOR_BOXES[prov][4]
    out = []
    for r in data.get("Results") or []:
        prop = r.get("Property") or {}
        price = num(str(prop.get("PriceUnformattedValue") or ""))
        if not price or not r.get("RelativeDetailsURL"):  # the search asks for sales only; a lease shows no sale price
            continue
        code = CA_PROVINCE_CODES.get((r.get("ProvinceName") or "").strip().lower())
        if code not in codes:  # the box reaches into a neighbouring province
            continue
        addr = prop.get("Address") or {}
        parts = [x.strip() for x in (addr.get("AddressText") or "").split("|")]
        street, place = (parts[0], parts[-1]) if len(parts) > 1 else ("", parts[0])
        # "Sault Ste. Marie, Ontario P6B0B6" -> "Sault Ste. Marie, ON, P6B0B6", the shape Kijiji's REALTOR.ca ads have
        m = re.match(r"(.*?),\s*([A-Za-z &.]+?)\s*([A-Z]\d[A-Z] ?\d[A-Z]\d)?$", place)
        town_name = m.group(1).strip() if m else place
        region = ", ".join(x for x in (town_name, code, (m.group(3) or "").replace(" ", "") if m else "") if x)
        land = r.get("Land") or {}
        desc = " | ".join(x for x in (r.get("PublicRemarks"), prop.get("Type"), land.get("SizeTotal"), land.get("AccessType"),
                                      (r.get("Building") or {}).get("Type"), "MLS® %s" % r["MlsNumber"] if r.get("MlsNumber") else "") if x)
        out.append({
            "source": "realtor",
            "country": "CA",
            "region": region,
            "title": street or "%s, %s" % (prop.get("Type") or "Property", town_name),
            "description": desc,
            "price": price,
            "currency": "CAD",
            "acres": realtor_acres(land),
            "lat": num(str(addr.get("Latitude") or "")), "lon": num(str(addr.get("Longitude") or "")),
            "url": "https://www.realtor.ca" + r["RelativeDetailsURL"],
            "kind": kind,
        })
    return out, total


def crawl_realtor(br, store, prov, a, kind="land"):
    def load(url, band, p):
        data = realtor_post(br, prov, p, kind, band)
        if data is None:
            return None
        recs, total = parse_realtor(data, prov, kind)
        if recs is None:
            return None
        # recs may be empty with results on the page: a page of listings all over the province's border
        return recs, total, len(data.get("Results") or [])
    # the API caps a query at 600 records (12 pages of 50), and on 2026-11-09 it refused a browser's calls after about
    # six of them, so a province gets 4 wide slices instead of 20: 5 calls with the first look at the whole range
    return crawl_sliced(store, search_id("realtor", prov, kind), a, kind, lambda band, p: realtor_url(prov, p, kind, band),
                        load, REALTOR_PER_PAGE, max_pages=12, currency="CAD", floor=REALTOR_MIN_CAD, most_slices=4)


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
        living = None if kind == "land" else num((params.get("m") or {}).get("key"))  # a house ad's "m" is its floor space
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
            "living_m2": living or None,
            "lat": geo.get("lat"), "lon": geo.get("lon"),
            "url": o["url"],
            "kind": kind,
        })
    return out


def olx_url(site, cat, p, band):
    """band = (lowest, highest) price in the currency of the site's price filter."""
    return ("https://%s/api/v1/offers/?offset=%d&limit=40&category_id=%d&sort_by=filter_float_price:asc"
            "&filter_float_price:from=%d&filter_float_price:to=%d" % (site, (p - 1) * 40, cat, band[0], band[1]))


def crawl_olx(fetch, store, countries, a, rates, kinds=("land",)):
    jobs = [(k, c) for k in kinds for c in countries]
    for i, (kind, c) in enumerate(jobs, 1):
        site, cat, cc, cur, usd_floor = OLX_SITES[c]
        if kind != "land":  # under $1,000 a "house" is nearly always a rental, a share or a garden shed
            cat, usd_floor = OLX_HOUSE_CATS[c], max(usd_floor, 1000)
        sid = search_id("olx", c, kind)

        def load(url, band, p):
            fetch.label = "%s %s  %s page %d  (olx)" % (c, kind, band_label(band), p)
            try:
                data = json.loads(fetch.get(url) or "")
            except ValueError:
                return None
            recs = parse_olx(data, cc, kind)
            total = (data.get("metadata") or {}).get("visible_total_count")
            if not (data.get("links") or {}).get("next"):
                total = (p - 1) * 40 + len(recs)  # the last page
            return recs, total, len(recs)
        # the API stops at 1,000 results: 25 pages of 40
        total_new = crawl_sliced(store, sid, a, kind, lambda band, p: olx_url(site, cat, p, band), load, 40,
                                 max_pages=25, currency=cur, floor=int(usd_floor * rates.get(cur, 1)))
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


def meli_living_m2(attrs):
    """'161 m² cubiertos' / '120 m² construidos' -> a home's floor space in m²."""
    for n, kind in re.findall(r"([\d.,]+)\s*m²\s*(\w*)", attrs):
        if kind.lower().startswith(("cubiert", "constru", "útil", "util")):
            return num(n.replace(".", "").replace(",", "."))
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
            "living_m2": meli_living_m2(attrs) or None,
            "lat": None, "lon": None,
            "url": (url if url.startswith("http") else "https://" + url).split("#")[0],
            "kind": kind,
        })
    total = ((st.get("melidata_track") or {}).get("event_data") or {}).get("total")
    return out, int((st.get("pagination") or {}).get("page_count") or 0), total


def meli_url(base, p, band):
    """Cheapest first within band = (lowest, highest) price in local currency; page n starts at result 48*(n-1)+1."""
    return "%s%s_OrderId_PRICE_PriceRange_%d-%d_NoIndex_True" % (base, "_Desde_%d" % ((p - 1) * 48 + 1) if p > 1 else "",
                                                                band[0], band[1])


def crawl_meli(fetch, store, countries, a, rates, kinds=("land",)):
    jobs = [(k, c) for k in kinds for c in countries]
    for i, (kind, c) in enumerate(jobs, 1):
        base, cc, cur = MELI_SITES[c]
        if kind != "land":
            base = MELI_HOUSE_URLS[c]
        sid = search_id("mercadolibre", c, kind)

        def load(url, band, p):
            fetch.label = "%s %s  %s page %d  (mercadolibre)" % (c, kind, band_label(band), p)
            html = fetch.get(url)
            recs, page_count, total = parse_meli(html or "", cc, kind)
            if recs is None and not STOP.is_set():  # now and then one page comes back as its sign-in page
                time.sleep(15)
                html = fetch.get(url)
                recs, page_count, total = parse_meli(html or "", cc, kind)
            if recs is None:
                log("  mercadolibre %s: no results on the page (blocked, sign-in page or new layout)" % c)
                if html:
                    note_block("mercadolibre", "no data", url, page_title(html), "http")
                return None
            if p >= page_count:
                total = (p - 1) * 48 + len(recs)  # the last page
            return recs, total, len(recs)
        total_new = crawl_sliced(store, sid, a, kind, lambda band, p: meli_url(base, p, band), load, 48,
                                 currency=cur, floor=int(MIN_PRICE * rates.get(cur, 1)))
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
            if not n and p == 1:  # the first page of a band always has cards: this is a block or error page
                log("ieichiba %s: no listings on the first page (blocked?), skipping the band" % band)
                note_block("ieichiba", "no data", url, page_title(html), "http")
                store.stop(sid, "fetch failed")
                break
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
            note_block(src, "no data", url, page_title(html), via_of(fetch))
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

ZILLOW_MIN_USD = 1000  # $1 and $100 placeholders sit below this
ZILLOW_PER_PAGE, ZILLOW_PAGES = 41, 20  # Zillow answers HTTP 400 past page 24 of a search (about 1,000 results)


def zillow_url(state, p, kind="land", floor=ZILLOW_MIN_USD, ceil=None):
    fs = {k: {"value": False} for k in ("sf", "tow", "mf", "con", "apa", "manu", "apco")}  # land only
    fs.update({"sort": {"value": "pricea"}, "price": dict({"min": floor}, **({"max": ceil} if ceil else {}))})
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


def zillow_page(html, state, kind="land"):
    """(listings, how many results Zillow says the search has)."""
    m = re.search(r'"totalResultCount":(\d+)', html)
    return parse_zillow(html, state, kind), (int(m.group(1)) if m else None)


def crawl_zillow_one(fetch, store, st, a):
    return crawl_sliced(store, search_id("zillow", st, "land"), a, "land",
                        lambda band, p: zillow_url(st, p, floor=band[0], ceil=band[1]),
                        html_loader(fetch, "zillow", st, "land", zillow_page), ZILLOW_PER_PAGE,
                        max_pages=ZILLOW_PAGES, floor=ZILLOW_MIN_USD)


def crawl_zillow(fetch, store, states, a):
    for i, st in enumerate(states, 1):
        if STOP.is_set():
            break
        n = crawl_zillow_one(fetch, store, st, a)
        log("zillow     [%d/%d] %-28s +%d listings" % (i, len(states), st, n))


# ---- Immobiliare.it: all of Italy (real Chrome; results are JSON in __NEXT_DATA__)

def immobiliare_url(key, p, kind="land", band=None):
    # under €1,000 is almost all "price on request" placeholders and €1 auction lots
    return "https://www.immobiliare.it/vendita-terreni/%s/?criterio=prezzo&ordine=asc&prezzoMinimo=%d%s%s" % (
        key, band[0] if band else 1000, "&prezzoMassimo=%d" % band[1] if band else "", "&pag=%d" % p if p > 1 else "")


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


def encuentra24_url(cc, p, kind="land", band=None):
    """band = (lowest, highest) price in USD."""
    return "https://www.encuentra24.com/%s/real-estate-for-sale-lots-land%s?%ssort=f_price&dir=asc" % (
        ENCUENTRA24_COUNTRIES[cc], ".%d" % p if p > 1 else "", "q=f_price.%d-%d%%7Cf_currency.USD&" % band if band else "")


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


def properstar_full_listing(html, url):
    """A Properstar listing from its own page: the agency's full description (every language it comes in, so the
    price checks can read it) and whether the listing has ended. None if the page has no listing data."""
    lid = url.rstrip("/").rsplit("/", 1)[-1]
    m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});?\s*</script>", html or "", re.S)
    try:
        L = json.loads(m.group(1))["entities"]["listing"][lid]
    except Exception:
        return None
    texts = []
    for key in ("title", "descriptionFull", "description"):
        for t in L.get(key) or []:
            s = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", (t or {}).get("text") or ""))).strip()
            if s and s not in texts:
                texts.append(s)
    return {"description": " | ".join(texts), "archived": bool(L.get("isArchived"))}


VERIFY_SOURCES = ("landwatch", "kijiji", "properstar")
VERIFY_READY = {"landwatch": LANDWATCH_AD_READY, "properstar": PROPERSTAR_READY}


def verify_cheapest(store, a, n=60, rounds=4):
    """Open the cheapest believable LandWatch, Kijiji and Properstar listings and re-check them against their full
    description. Search cards cut descriptions short, which hides 'Only $2,700 cash ... $380 down' style pricing on
    LandWatch, and on Kijiji, homes and land for rent whose price is the monthly rent ('... for tenants', 'Lease expires
    2030'). Properstar search results carry no description at all, and its agencies post rentals and per-m2 prices as
    sales ('$100 pesos m2 renta'); the listing page has the full text. Only listings the site will show are checked.
    LandWatch and Properstar pages open in several Chrome windows at once (--workers), Kijiji's load over plain HTTP."""
    today = datetime.now().strftime("%Y-%m-%d")
    sources = [s for s in VERIFY_SOURCES if s in csv_list(a.sources, [])]
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
        elif r["source"] == "properstar":
            L = properstar_full_listing(get(r["url"]) or "", r["url"])
            if not L or L["archived"]:  # ended, or the page wouldn't load: leave it for the next run
                gone.add(r["url"])
                return
            rec["description"] = " | ".join(x for x in (rec.get("description"), L["description"]) if x)  # keeps the search title
        else:
            html = get(r["url"])
            desc = landwatch_full_description(html) if html else None
            if not desc:
                # removed, blocked or timed out: save nothing. Saving would stamp it verified today, so a dead
                # listing would never be re-checked and a blocked one wouldn't be retried
                return
            rec["description"] = desc
        store.add([rec], seen=False)

    def worker(src):
        br = None
        try:
            while True:
                job = queues[src].get()
                if job is None:
                    return
                try:
                    if STOP.is_set() or given_up(src):
                        continue
                    if src == "kijiji":  # these answer plain HTTP requests
                        recheck(job[0], job[1], lambda u: plain.get(u, what="verify"))
                    else:
                        if br is None:
                            br = Browser(headless=a.headless, delay=a.delay, slot=None, block=not a.load_all)
                        br.label = "verify " + job[0]["url"][-12:]
                        recheck(job[0], job[1], lambda u: br.get(u, ready=VERIFY_READY[src], what="verify"))
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

    chrome = [s for s in sources if s in VERIFY_READY]  # the Chrome windows are shared out between these sources
    threads = [(s, threading.Thread(target=worker, args=(s,), name="V%s%d" % (s[0].upper(), i + 1), daemon=True))
               for s in sources for i in range(max(1, a.workers // len(chrome)) if s in chrome else 1)]
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
            step = "re-checking the prices of the cheapest listings (round %d of up to %d)" % (rnd + 1, rounds)
            while any(q.unfinished_tasks for q in queues.values()) and not STOP.is_set():
                left = sum(q.unfinished_tasks for q in queues.values())
                LIVE.set_step(step, len(todo) - left, len(todo))
                time.sleep(1)
            for q in queues.values():
                q.join()
        LIVE.set_step(None)
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
    lock, failed, finished = threading.Lock(), [], [0]
    step = "translating %s titles to English with Claude" % format(len(todo), ",")
    LIVE.set_step(step, 0, len(batches))

    def run(batch):
        if len(failed) >= 3 or STOP.is_set():  # e.g. not signed in or out of usage: don't keep trying
            return
        out, err = None, ""
        try:
            # no extended thinking: titles don't need it, and with it a batch takes ~40 s and 5x the tokens
            p = subprocess.run([claude, "-p", "--model", "haiku", "--effort", "low", "--tools", "", "--strict-mcp-config",
                                "--no-session-persistence", "--setting-sources", "", "--system-prompt", TRANSLATE_PROMPT],
                               input=json.dumps(batch, ensure_ascii=False), capture_output=True, text=True, timeout=300,
                               env=dict(os.environ, MAX_THINKING_TOKENS="0"))
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
        with lock:
            finished[0] += 1
            LIVE.set_step(step + (" (%d batches failed)" % len(failed) if failed else ""), finished[0], len(batches))

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
        "source", "flags", "url", "lat", "lon", "scraped_at", "similar"]


# words that vary between two postings of the same address: "4 SANDPIPER AVENUE Manitouwadge" / "4 Sandpiper AVE Manitouwadge"
ADDRESS_WORDS_RX = re.compile(r"\b(street|st|avenue|ave?|road|rd|drive|dr|crescent|cres|boulevard|blvd|court|crt|ct|lane|ln|highway|hwy"
                              r"|route|rte|place|pl|trail|trl|circle|cir|lots?|unit|parcel|county)\b", re.I)


def title_key(t):
    """A title reduced to what identifies the parcel: no "(TB-73)" board codes, street-type words, case or punctuation.
    Keeps Japanese and Cyrillic letters."""
    t = re.sub(r"\([^)]*\)", " ", (t or "").lower())
    return re.sub(r"[\W_]", "", ADDRESS_WORDS_RX.sub(" ", re.sub(r"^\s*lot\b", "", t)))


def collapse_similar(recs):
    """One row per parcel. The same land turns up several times: an agency's listing fed to Properstar twice under
    different titles, a REALTOR.ca lot on two MLS boards ("(TB-73)"), a Kijiji ad reposted under a new id, the same
    plot on OLX and Properstar, or a seller's row of identical lots posted one ad each. Listings in the same country
    at the same US dollar price are one parcel when they share a title (per site, or in English across sites), a
    location and size, the same opening description text and size, or the same MLS number. The fullest listing of each group stays, with
    similar = how many others it stands for. recs come cheapest first."""
    parent = list(range(len(recs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first = {}
    for i, r in enumerate(recs):
        cc, usd, acres = r.get("country"), round(r.get("price_usd") or 0), r.get("acres")
        a2 = None if acres is None else round(acres, 2)
        # Two 'Terreno en venta' at one price in different towns are different parcels, so the region is part of the
        # title keys; a title with no letters or digits ('•', '-', '') says nothing about which parcel it is
        region = title_key(r.get("region"))
        tk = title_key(r.get("title"))
        keys = [("t", r.get("source"), region, tk, acres)] if tk else []
        ek = title_key(r.get("title_en") or r.get("title"))
        if ek:
            keys.append(("e", title_key(r.get("region_en") or r.get("region")), ek, a2))
        if r.get("lat") is not None and r.get("source") != "landwatch":  # LandWatch pins sit at the ZIP code's center
            keys.append(("g", round(r["lat"], 3), round(r["lon"], 3), a2))
        desc = re.sub(r"[\W_]", "", (r.get("description") or "").lower())
        if len(desc) >= 60:
            keys.append(("d", r.get("source"), desc[:120], acres))
        m = MLS_NUMBER_RX.search((r.get("title") or "") + " " + (r.get("description") or ""))
        if m:  # the same MLS listing on REALTOR.ca and in Kijiji's REALTOR.ca feed
            keys.append(("m", m.group(1).upper()))
        for k in keys:
            k = (cc, usd) + k
            if k in first:
                parent[find(i)] = find(first[k])
            else:
                first[k] = i
    groups = {}
    for i in range(len(recs)):
        groups.setdefault(find(i), []).append(i)
    out = []
    for members in groups.values():  # a flagged copy stands for the group (what one copy says applies to the parcel), else the fullest
        best = min(members, key=lambda i: (believable_flags(recs[i].get("flags")), recs[i].get("acres") is None, recs[i].get("lat") is None, i))
        recs[best]["similar"] = len(members) - 1
        out.append(best)
    return [recs[i] for i in sorted(out)]


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
    LIVE.set_step("reading the saved listings")
    recs = [r for r in store.all() if safe_url(r.get("url"))]  # older data may hold links in other schemes
    cutoff = site_cutoff(recs)
    if english and cutoff:  # before finalize, so the price checks also read the English titles
        add_english([r for r in recs if (r.get("scraped_at") or "") >= cutoff])
    LIVE.set_step("checking prices and merging duplicates")
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
    recs = collapse_similar(recs)
    LIVE.set_step("writing results.csv, report.html and the site")
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
    LIVE.set_step(None)
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
              ("share of a property", "S"), ("allotment garden", "S"), ("burial plot", "S"), ("wrong currency", "L"),
              ("building only", "S")]


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
    elif src in ("kijiji", "realtor"):
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


def provinces(recs):
    """State, province or region of each listing, for the site's Province/state list. LandWatch's region is the US
    state. Others take the province of the nearest place with 1,000+ people in the same country
    (geo/admin1_points.tsv.gz, built from GeoNames by tools/build_admin1.py); listings without a location take the
    last part of their address ("Nasu, Tochigi"), spelled as GeoNames spells it when the names match."""
    grid, spell = {}, {}  # (country, lat cell, lon cell) -> [(lat, lon, province)]; (country, key) -> province
    want = {(r.get("country") or "").upper() for r in recs}
    try:
        with gzip.open(os.path.join(HERE, "geo", "admin1_points.tsv.gz"), "rt", encoding="utf-8") as f:
            for line in f:
                cc, lat, lon, prov = line.rstrip("\n").split("\t")
                if cc in want:
                    lat, lon = float(lat), float(lon)
                    grid.setdefault((cc, math.floor(lat), math.floor(lon)), []).append((lat, lon, prov))
                    spell[cc, place_key(prov)] = prov
    except OSError:
        log("geo/admin1_points.tsv.gz is missing: provinces come from addresses only")

    def nearest(cc, lat, lon):
        k = math.cos(math.radians(lat))
        for reach in (1, 2, 4):  # the cells around the listing, then further out where places are sparse
            pts = [p for i in range(-reach, reach + 1) for j in range(-reach, reach + 1)
                   for p in grid.get((cc, math.floor(lat) + i, math.floor(lon) + j), ())]
            if pts:
                return min(pts, key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lon) * k) ** 2)[2]
        return ""

    out = []
    for r in recs:
        cc = (r.get("country") or "").upper()
        place = (r.get("region_en") or r.get("region") or "").strip()
        prov = ""
        if r.get("source") == "landwatch":
            prov = place
        elif r.get("lat") is not None and r.get("lon") is not None:
            prov = nearest(cc, r["lat"], r["lon"])
        if not prov and r.get("source") in ("olx", "ieichiba", "mercadolibre") and ", " in place:
            last = place.rsplit(",", 1)[1].strip()
            if cc == "AR" and re.match(r"bs\.? ?as\b|buenos aires\b", last, re.I):  # "Bs.As. G.B.A. Norte"
                last = "Buenos Aires"
            key = place_key(re.sub(r"\b(region|oblast|province|prefecture|county)\b", "", last, flags=re.I))
            prov = spell.get((cc, key)) or spell.get((cc, place_key(last))) or last
        out.append(prov[:60])
    return out


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


DESC_SOURCES = {"landwatch", "kijiji", "zillow", "realtor"}  # sites whose descriptions are in English and say more than the title
BLURB_CHARS = 140


def site_blurb(r):
    """The start of the listing's description for the site's list, cut at a word: only from DESC_SOURCES (the other
    sites' descriptions are in other languages, which would cost Claude usage to translate, or just repeat the title),
    and not French Kijiji ads. None when there's nothing to add."""
    if r.get("source") not in DESC_SOURCES:
        return None
    d = " ".join((r.get("description") or "").split())
    title = " ".join((r.get("title") or "").split())
    if not d or d.lower().startswith(title.lower()[:40]) or (r.get("source") == "kijiji" and FRENCH_RX.search(d[:200])):
        return None
    if r.get("source") == "zillow":  # "Lot / Land for sale | 256 days on Zillow | 6,098 sqft": the facts after the first part
        parts = [p.strip() for p in d.split("|")[1:] if p.strip() and "--" not in p]
        return " · ".join("Listed " + p if p.endswith(" ago") else p for p in parts) or None
    if len(d) <= BLURB_CHARS:
        return d
    return d[:BLURB_CHARS].rsplit(" ", 1)[0].rstrip(",.;:-–") + "…"


SITE_PART_BYTES = 12_000_000  # rows per site data file: well under Cloudflare Pages' 25 MiB file limit
SITE_FIRST_BYTES = 2_000_000  # data.json's share: the cheapest listings, small so the page can show them quickly


def build_site(recs, rates):
    """Write site/index.html + site/data.json: every listing seen in the last few days of runs, plus the
    exchange rates its US dollar prices were converted with, for the site's Currency list."""
    os.makedirs(SITE, exist_ok=True)
    cutoff = site_cutoff(recs)
    if not cutoff:
        return
    newest = datetime.fromisoformat(max(r["scraped_at"] for r in recs if r.get("scraped_at")))  # before the filter: it may keep nothing
    recs = [r for r in recs if (r.get("scraped_at") or "") >= cutoff and r.get("price_usd") is not None]
    rows, countries = [], set()
    for r, place, prov in zip(recs, towns(recs), provinces(recs)):
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
                     "".join(TAG_CODES[t] for t in (r.get("tags") or "").split(",") if t), prov,
                     r.get("similar") or 0,  # how many near-identical listings this row stands for (collapse_similar)
                     site_blurb(r)])
    if newest < datetime.now() - timedelta(days=1):
        log("site: WARNING - the newest listing was scraped %s; the site is being rebuilt from old data" % newest.strftime("%Y-%m-%d %H:%M"))
    data = {"generated": newest.astimezone().isoformat(timespec="seconds"),  # shown as "Updated ..." on the site
            "countries": len(countries), "rows": rows, "rates": rates,
            "populations": town_populations({(row[3], row[13]) for row in rows if row[13]})}
    # cheapest first, and data.json gets only the first SITE_FIRST_BYTES of them, so the page can show those while
    # the rest download. They go to data-2.json, data-3.json... (each under SITE_PART_BYTES: Cloudflare Pages refuses
    # a file over 25 MiB), which data.json lists under "more" and index.html names so it fetches them alongside data.json
    rows.sort(key=lambda row: row[1])
    parts, size = [[]], 0
    for row in rows:
        n = len(json.dumps(row, separators=(",", ":"), allow_nan=False)) + 1
        if size + n > (SITE_FIRST_BYTES if len(parts) == 1 else SITE_PART_BYTES) and parts[-1]:
            parts.append([])
            size = 0
        parts[-1].append(row)
        size += n
    for old in glob.glob(os.path.join(SITE, "data-*.json")):
        os.remove(old)
    more = ["data-%d.json" % (i + 2) for i in range(len(parts) - 1)]
    for name, part in zip(more, parts[1:]):
        with open(os.path.join(SITE, name), "w") as f:
            json.dump({"rows": part}, f, separators=(",", ":"), allow_nan=False)
    data["rows"] = parts[0]
    if more:
        data["more"] = more
    with open(os.path.join(SITE, "data.json"), "w") as f:
        json.dump(data, f, separators=(",", ":"), allow_nan=False)
    with open(os.path.join(HERE, "site_template.html")) as f:
        html = f.read()
    assert "const MORE = [];" in html, "site_template.html lost the MORE line build_site fills in"
    html = html.replace("const MORE = [];", "const MORE = %s;" % json.dumps(more), 1)
    with open(os.path.join(SITE, "index.html"), "w") as f:
        f.write(html)
    open(os.path.join(SITE, ".nojekyll"), "w").close()
    for path in glob.glob(os.path.join(HERE, "legal", "*")):  # privacy.html, terms.html, licenses.html, legal.css
        shutil.copy(path, SITE)
    log("site: %d listings from %d countries -> site/" % (len(rows), len(countries)))


COVERAGE_COLS = ["source", "search", "site_total", "fetched", "fetched_pct", "on_site", "pages", "reached_usd", "stopped"]
SOURCE_ORDER = ["properstar", "castles", "landwatch", "zillow", "kijiji", "realtor", "olx", "mercadolibre", "ieichiba", "homes",
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
    global DATA, GIVE_UP_AFTER
    ap = argparse.ArgumentParser(description="Find the cheapest land listings worldwide.")
    ap.add_argument("--sources", default=ALL_SOURCES, help="comma-separated, default: " + ALL_SOURCES)
    ap.add_argument("--listings", type=int, default=250,
                    help="believable listings to collect per country/state/province on each site before moving on")
    ap.add_argument("--house-listings", type=int, default=60,
                    help="believable listings to collect from each site's houses-for-sale section per country/state/province")
    ap.add_argument("--no-houses", action="store_true", help="only search the land-for-sale sections")
    ap.add_argument("--pages", type=int, default=30,
                    help="most pages per country/state/province (20-48 listings each), however few are believable")
    ap.add_argument("--more", default=MORE_DEFAULT, metavar="PLACE=N",
                    help="places to search deeper than --listings, as state/country/province=listings, comma-separated "
                         "(default: %s; '' for none). Their house searches and page cap grow by the same ratio" % MORE_DEFAULT)
    ap.add_argument("--reach", type=int, default=REACH_DEFAULT, metavar="USD",
                    help="spread each search evenly over the prices up to this, instead of taking only the cheapest "
                         "listings (default: %d)" % REACH_DEFAULT)
    ap.add_argument("--countries", help="country slugs for Properstar, OLX and Mercado Libre, comma-separated (default: all)")
    ap.add_argument("--states", help="US state slugs for LandWatch and Zillow, e.g. new-mexico,arizona (default: all)")
    ap.add_argument("--central-america", help="Encuentra24 countries, e.g. CR,PA (default: %s)" % ",".join(ENCUENTRA24_COUNTRIES))
    ap.add_argument("--prefectures", help="Japanese prefectures for HOME'S akiya bank, e.g. nagano,shimane (default: all)")
    ap.add_argument("--provinces", help="provinces for Kijiji and REALTOR.ca, e.g. nova-scotia,saskatchewan (default: all)")
    ap.add_argument("--workers", type=int, default=8, help="Chrome windows scraping in parallel (about 1.1 GB of memory each)")
    ap.add_argument("--tile", choices=["right", "full"], default="right",
                    help="tile the windows over the right half (default) or the whole screen")
    ap.add_argument("--hidden", action="store_true", help="no visible windows: one per worker, minimized to the Dock")
    ap.add_argument("--headless", action="store_true", help="fully headless Chrome (more likely to get blocked)")
    ap.add_argument("--delay", type=float, default=2.5, help="base seconds between page loads")
    ap.add_argument("--give-up", type=int, default=3, metavar="N",
                    help="skip the rest of a site's pages this run once it has blocked N pages in a row (0 = keep trying)")
    ap.add_argument("--no-dashboard", action="store_true",
                    help="don't open the progress window (the run still writes data/progress.json for it)")
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

    try:
        a.more = parse_more(a.more)
    except ValueError as e:
        ap.error(str(e))
    GIVE_UP_AFTER = a.give_up
    store = Store()
    if not a.report_only:
        LIVE.store, LIVE.args = store, " ".join(sys.argv[1:]) or "(defaults)"
        write_progress()
        if not a.no_dashboard:
            open_dashboard()
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
        if a.more:
            log("searching deeper: %s" % ", ".join("%s %d listings" % kv for kv in sorted(a.more.items())))
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
                    LIVE.idle()
                return run

            threads = [threading.Thread(target=timed(fn), args=(Plain(delay=max(1.0, a.delay / 2)), store) + args,
                                        name="P%d" % (i + 1), daemon=True) for i, (fn, args) in enumerate(plain)]
            for t in threads:
                t.start()
            # on each site the land searches come before the house searches
            japan = not a.countries or "japan" in countries
            per_source = {
                "landwatch": [(st, k) for k in kinds for st in states],
                "realtor": [(pv, k) for k in kinds for pv in provs],
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
        METRICS.report(LIVE.args)
    if not a.report_only:
        LIVE.ended = True
        save_progress()


if __name__ == "__main__":
    main()
