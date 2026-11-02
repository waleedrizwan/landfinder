# landfinder

Find the cheapest land for sale anywhere on earth. landfinder scrapes land listings from big listing
portals, converts every price to US dollars, and ranks them by **total price**, i.e. the cheapest piece of land you can
actually buy. Price per acre is shown too. Every row links to the live listing.

## Sources

| Source | Coverage | How it's fetched |
|---|---|---|
| [Properstar](https://www.properstar.com) | ~150 countries, land plots | real Chrome via Playwright (site has a bot check) |
| [LandWatch](https://www.landwatch.com) | all 50 US states | real Chrome via Playwright |
| [Kijiji](https://www.kijiji.ca) | every Canadian province/territory | plain HTTP requests |
| [OLX](https://www.olx.pl) | Poland, Ukraine, Romania, Portugal, Bulgaria, Kazakhstan, Uzbekistan | OLX's JSON search API |
| [Mercado Libre](https://inmuebles.mercadolibre.com.ar) | Argentina, Mexico | plain HTTP requests |

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
reopens it. A full run (10 pages per country/state/province) takes under an hour with 4 windows.

## Usage

```sh
./run.sh                                         # everything, 10 pages each
./run.sh --sources properstar --pages 25         # go deeper on one source
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
```

After scraping, landfinder opens the 60 cheapest believable listings and re-reads their full description,
because search results cut it short. That catches sellers who enter a down payment or monthly payment as
the price ("Only $2,700 cash! ... $380 down"). When the text gives the real cash price, that price is used;
otherwise the listing is hidden. Use `--verify 0` to skip this step or `--verify-only` to run it alone.

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

Prices show in US dollars until you pick another currency from the Currency list, which has every currency
open.er-api.com gives a rate for. The browser remembers the choice, and the map pins and price filters switch with it.
Each card names its currency, and a listing posted in a different currency also shows its own price ("Listed at
16,500 CAD"). Prices are converted at the exchange rates from the day the site was built.

Each card is tagged with the kind of property: vacant land, building lot, farmland, forest, recreational,
waterfront, commercial, dacha plot, land with house, or house. The Type filter shows one kind. LandWatch tags come
from LandWatch's own category, or "land with house" when the card lists beds, baths or square feet. For the other
sites, the tag comes from words in the title, its English translation and the description, in each site's languages.
So the tag is a good guess, not a guarantee: "build your dream cottage" still counts as vacant land, and a house the
seller doesn't mention goes unnoticed. Tagging makes no Claude calls. House listings stay hidden with the flagged ones
unless you pick House in the Type filter.

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
- `./install_daily.sh` schedules `daily.sh` with launchd: a fresh scrape at 6:00 every morning, with
  windows hidden in the Dock, then a deploy. If the Mac is asleep at that time, macOS runs the job when it
  wakes. Logs go to `logs/`. Use `./install_daily.sh 7 30` to change the time, or `./install_daily.sh off`
  to remove the job.

## Output

- **`report.html`**: open it in a browser for a sortable, filterable table of every listing.
- **`results.csv`**: the same data, cheapest total price first. The `type` column is the property type shown on the site.
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
   the pages cheapest first and saves them with `store.add()` / `store.mark()`. `crawl_olx` is a short example.
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
