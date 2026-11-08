# landfinder

Find the cheapest land for sale anywhere on earth. landfinder scrapes land listings from big listing
portals, converts every price to US dollars, and ranks them by **total price**, i.e. the cheapest piece of land you can
actually buy. Price per acre is shown too. Every row links to the live listing.

## Sources

| Source | Coverage | How it's fetched |
|---|---|---|
| [Properstar](https://www.properstar.com) | ~150 countries, land plots and houses | real Chrome via Playwright (site has a bot check) |
| [LandWatch](https://www.landwatch.com) | all 50 US states | real Chrome via Playwright |
| [Kijiji](https://www.kijiji.ca) | every Canadian province/territory | plain HTTP requests |
| [OLX](https://www.olx.pl) | Poland, Ukraine, Romania, Portugal, Bulgaria, Kazakhstan, Uzbekistan | OLX's JSON search API |
| [Mercado Libre](https://inmuebles.mercadolibre.com.ar) | Argentina, Mexico | plain HTTP requests |
| [Ieichiba](https://www.ieichiba.com) (家いちば) | Japan: empty houses (akiya) and land sold by their owners | plain HTTP requests |

Each search is sorted cheapest first. Properstar and Mercado Libre results start at $1,500, because cheaper
"prices" there are almost always per-m² figures or placeholders. OLX searches start at $500 to $1,500 depending
on the country (`OLX_SITES` in `landfinder.py`): real village plots in Bulgaria, Portugal and Kazakhstan sell for
a few hundred dollars, while in Ukraine, Uzbekistan and Poland a price that low is nearly always per m² or per
sotka (100 m²). Mercado Libre's other countries ask visitors to sign in, so they aren't included.

The sites that answer plain HTTP requests (Kijiji, OLX, Mercado Libre) each run in their own thread alongside the
Chrome windows, so adding one barely changes how long a run takes.

## Quick start (macOS)

Needs Python 3.9+ and Google Chrome.

```sh
git clone https://github.com/waleedrizwan/landfinder.git
cd landfinder
./run.sh            # first run creates .venv and installs dependencies
```

By default, 4 small Chrome windows open tiled on the right half of your screen. Each one scrapes a
different country or state at the same time, and a banner at the top shows what it's working on. Pages
are drawn at half size, so sites still serve their desktop layout. If a window gets closed, the script
reopens it. A full run takes about an hour with 4 windows.

## Usage

```sh
./run.sh                                         # everything: 250 believable listings per search
./run.sh --sources properstar --listings 500     # go deeper on one source
./run.sh --countries paraguay,bolivia,argentina  # only these countries (Properstar, OLX, Mercado Libre)
./run.sh --sources olx,mercadolibre              # only some sources
./run.sh --states new-mexico,arizona,nevada      # only these US states (LandWatch)
./run.sh --provinces nova-scotia,saskatchewan    # only these provinces (Kijiji)
./run.sh --report-only                           # rebuild outputs from saved data
./run.sh --report-only --no-translate            # ...without translating new titles (no claude CLI calls)
./run.sh --fresh                                 # re-scrape pages already done
./run.sh --workers 6                             # more windows in parallel (default 4)
./run.sh --tile full                             # tile windows over the whole screen
./run.sh --hidden                                # no visible windows: minimized to the Dock
./run.sh --pages 50                              # allow more pages per search (default 30)
```

A search is one country, state or province on one site. It runs cheapest first until it has 250 believable
listings (`--listings`): ones the site shows by default, with no flag except a missing land area, and not one
exact low price repeated on many different-size plots. So when a seller fills the cheapest pages with a
placeholder price, the search keeps going past them. It also stops when the results run out, or after 30 pages
(`--pages`).

After scraping, landfinder opens the 60 cheapest believable listings on LandWatch, Kijiji and Properstar and
re-reads their full description, because search results cut it short (Properstar's carry none at all). That
catches sellers who enter a down payment or monthly payment as the price ("Only $2,700 cash! ... $380 down").
When the text gives the real cash price, that price is used; otherwise the listing is hidden. It also catches
rentals: real estate agents' REALTOR.ca listings include homes and land for rent, priced at the monthly rent,
which land in Kijiji's land-for-sale section, and Properstar's agencies post land for rent as "for sale" with
the rent as the price ("Terreno en venta o renta", "$100 pesos m2 renta"). Use `--verify 0` to skip this step
or `--verify-only` to run it alone. It follows `--sources`, so `--verify-only --sources kijiji` checks Kijiji
alone, without opening Chrome.

Rentals are also caught from the words sellers use, in a dozen languages ("for lease", "renta", "wynajem",
"оренда", 借地...), and on Kijiji by what the ad is: a REALTOR.ca commercial or industrial lot in Canada at a
few thousand dollars, with no talk of buyers, offers or the seller, is a lease at its monthly rent.

The same parcel often turns up several times: an agency's listing fed to Properstar twice under different
titles, a REALTOR.ca lot on two MLS boards, a Kijiji ad reposted under a new id, the same plot on OLX and
Properstar, or a seller posting a row of identical lots one ad each. Listings in the same country at the same
price are folded into one when they share a title, a location and size, or the same opening description text
and size. The site shows the fullest one with "+2 similar" after the site name.

Stopping with Ctrl-C is safe: progress is saved in `data/`, and running the same command again resumes.

## Website and daily updates

Every run also builds a small static website in `site/`: every listing seen in the last 3 days, cheapest
first, with search and filters, plus a map. On the map, nearby listings group into bubbles showing how many
there are and the cheapest price; click one to zoom in, click a price pin to see the listing, or pick a
country or city to jump to it. The live copy is at **https://cheapest-land.pages.dev**.

Picking a country adds a City list: that country's towns that have listings under the other filters. The town comes
from LandWatch's address, the place before the province or postal code on Kijiji, before the region on OLX and before
the state on Mercado Libre, and Properstar's city. In the US and Canada, where town names repeat from state to state,
towns read "Deming, NM"; Properstar listings there have no state, so they take the state of the nearest listing that has one.
The list puts the biggest towns first. Populations come from [GeoNames](https://www.geonames.org) (CC BY 4.0): every
place with 1,000+ people, matched by any of its names, kept in `geo/town_population.tsv.gz` (rebuild it with
`tools/build_town_population.py`). Smaller towns, and the ones no name matches, follow A to Z.

Prices show in US dollars until you pick another currency from the Currency list, which has every currency
open.er-api.com gives a rate for. The browser remembers the choice, and the map pins and price filters switch with it.
Each card names its currency, and a listing posted in a different currency shows its own price under the converted
one ("Converted from 16,500 CAD"). Prices are converted at the exchange rates from the day the site was built.

Each card is tagged with the kind of property: vacant land, building lot, farmland, forest, recreational,
waterfront, commercial, dacha plot, land with house, or house. The Type filter shows one kind. LandWatch tags come
from LandWatch's own category, or "land with house" when the card lists beds, baths or square feet. For the other
sites, the tag comes from words in the title, its English translation and the description, in each site's languages.
So the tag is a good guess, not a guarantee: "build your dream cottage" still counts as vacant land, and a house the
seller doesn't mention goes unnoticed. Tagging makes no Claude calls. House listings stay hidden with the flagged ones
unless you pick House in the Type filter or one of the ruins and castles below.

Besides land, every search also runs in the same site's houses-for-sale section, cheapest first: Properstar's houses,
LandWatch's homes, Kijiji's houses for sale, OLX's houses and Mercado Libre's *casas*. The cheapest houses on earth are
mostly ruins, abandoned farmhouses and fixer-uppers, which is what these searches are for. A house search stops at 60
believable listings (`--house-listings`) instead of 250, so the house searches take much less time than the land ones. `--no-houses` searches land only.

The "Ruins, castles & churches" group in the Type of property filter picks out special listings, found by keywords in the title, its English translation and the
description, in each site's languages (no Claude calls):

- **Ruins & fixer-uppers**: a ruin, an abandoned or derelict building, a house for demolition or one that needs a full
  renovation ("ruína", "à rénover", "da ristrutturare", "do remontu", "под снос", "за събаряне", "空き家", "fixer-upper").
- **Castles & manors**: castle, château, manoir, castelo, castillo, palácio, conac, замък. Places and streets named
  after one ("New Castle, IN", "Viana do Castelo", "Chateau Drive") don't count.
- **Churches & chapels**: a former, old or converted church or chapel, or one for sale ("former church building",
  "antiga igreja"), not one nearby ("next to the old church") or a street ("Church Lane").

A listing can have more than one, and each shows as a tag under its type. Every Japanese akiya (空き家, "empty house") counts as a
ruin.

Ieichiba is a Japanese board where owners sell empty houses and land themselves. landfinder takes every live listing
up to ¥10M (about $65k) from its price bands (`/tags/0-100manyen` and up). Prices there really are that low: owners pay
yearly tax on houses nobody wants, so ¥0 to ¥300,000 is common. Ieichiba listings skip the "price too low" and
placeholder checks, and a ¥0 listing shows as Free. Land area comes from the description ("土地：1,000㎡", or 坪,
3.3 m² each). Titles and towns are translated like the other non-English sources. Castles and churches are rare at the cheap end:
searches go up from the cheapest listing, so a $400,000 château only turns up in a country with few houses for sale.

LandWatch listings come without coordinates, so they're pinned at their ZIP code's center, from the US Census
ZIP code file in `geo/us_zip_centroids.csv.gz` (2024 ZCTA Gazetteer). Mercado Libre listings have no location
and only appear in the list.

Everything on the site is in English. Titles that aren't (OLX, Mercado Libre, French Kijiji ads, the odd German
or Spanish Properstar title) and place names in Cyrillic are translated with Claude when the site is built: the
`claude` CLI with the Haiku model, 80 texts per call. Translations are cached in `data/translations.json`, so each
text is only sent once and a daily run only translates new listings. Hover a title on the site to see the
original; searching in the original language still works. `--no-translate` skips the step, and without the
`claude` CLI the original text is kept.

- `./publish.sh` deploys `site/` to Cloudflare Pages with `wrangler`. Run `npx wrangler login` once first.
- The site asks for a password (`functions/_middleware.js`): every page, `data.json` and the API answer only to a
  browser that has logged in, and the login sets a cookie that lasts 30 days. The password is the project's
  `SITE_PASSWORD` secret; set or change it with `npx wrangler pages secret put SITE_PASSWORD --project-name cheapest-land`
  (changing it logs everyone out). For local testing put `SITE_PASSWORD=...` in `.dev.vars` (not committed) and run
  `npx wrangler pages dev site`.
- `./install_daily.sh` schedules `daily.sh` with launchd: a fresh scrape at 6:00 every morning, with
  windows hidden in the Dock, then a deploy. If the Mac is asleep at that time, macOS runs the job when it
  wakes. Logs go to `logs/`. Use `./install_daily.sh 7 30` to change the time, or `./install_daily.sh off`
  to remove the job.

## Output

- **`report.html`**: open it in a browser for a sortable, filterable table of every listing.
- **`results.csv`**: the same data, cheapest total price first. The `type` column is the property type shown on the site,
  and `tags` lists which of ruin, castle and church it is.
- **`coverage.csv`**: one row per search, to show how much of each market the site covers: how many listings the
  site itself reports (above our price floor, where the search has one), how many we fetched (`fetched_pct` of
  them), how many believable ones the site shows, the price the cheapest-first crawl got up to (`reached_usd`, the
  middle price on its last page) and why it stopped (`enough listings`, `end of results`, `page cap` or `fetch
  failed`). The log sums it up per source and names the countries that rely on a single source or have under 10
  believable listings, which is where to look for sources we're missing.
- **`data/`**: raw scraped records, exchange rates and crawl progress.

The `flags` column marks listings to double-check:

- `price too low`: probably a monthly payment or a per-m² price
- `may be payment plan price`: the text mentions "/mo", "down", and so on
- `auction/tender`: the price may be a minimum bid
- `wanted ad`: someone looking to buy, not a listing
- `share of a property`, `allotment garden`, `burial plot`: not a whole plot you'd own outright
- `wrong currency`: the title gives a much higher US dollar price than the listed one
- `no land area listed`: price per acre can't be computed

The text checks cover English, French and Spanish plus the OLX languages (Polish, Ukrainian, Russian, Romanian,
Bulgarian, Portuguese, Uzbek), e.g. leases, payment plans, auctions and prices per m² or per sotka.

Exchange rates come from [open.er-api.com](https://open.er-api.com) and are cached daily.

## Adding a source

1. Write `parse_<site>()`, which turns one search page into listing dicts (`source`, `country`, `region`,
   `title`, `description`, `price`, `currency`, `acres`, `lat`, `lon`, `url`), and `crawl_<site>()`, which walks
   the page numbers `search_pages()` gives it (cheapest first, until there are enough believable listings) and
   saves each page with `store.page()`, along with the site's own result count. It calls `store.stop()` when the
   results run out or a fetch fails. `crawl_olx` is a short example.
2. Register it in `main()`: sites that answer plain HTTP requests go in the `plain` list (one thread each),
   sites that need a real browser go in the `jobs` passed to `run_pool`.
3. Add it to the default `--sources`, and to `srcName` and the Source filter in `site_template.html`.
4. Run it alone first (`./run.sh --sources <site> --pages 2 --verify 0`) and look at the cheapest listings in
   `results.csv`. New sites usually need a price floor or a few extra flags before their cheapest listings are real.

## Notes

- Listing data belongs to the sites it comes from. Use this for personal research, keep the default
  delays between page loads, and check each site's terms.
- Cheap per-acre land is often remote, landlocked or unbuildable, and many countries restrict
  foreign ownership. Verify access, zoning, title and ownership rules before buying anything.
- `tools/` has small probe scripts used to work out Properstar's URL parameters.
