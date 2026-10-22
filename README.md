# landfinder

Find the cheapest land for sale anywhere on earth. landfinder scrapes land listings from big listing
portals, converts every price to **USD per acre**, and ranks them cheapest first, with a link to each
live listing.

## Sources

| Source | Coverage | How it's fetched |
|---|---|---|
| [Properstar](https://www.properstar.com) | ~150 countries, land plots | real Chrome via Playwright (site has a bot check) |
| [LandWatch](https://www.landwatch.com) | all 50 US states | real Chrome via Playwright |
| [Kijiji](https://www.kijiji.ca) | every Canadian province/territory | plain HTTP requests |

Each search is sorted cheapest first. Properstar results start at $500, because cheaper "prices" there
are almost always per-m² figures or placeholders.

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
./run.sh --countries paraguay,bolivia,argentina  # only these Properstar countries
./run.sh --states new-mexico,arizona,nevada      # only these US states (LandWatch)
./run.sh --provinces nova-scotia,saskatchewan    # only these provinces (Kijiji)
./run.sh --report-only                           # rebuild outputs from saved data
./run.sh --fresh                                 # re-scrape pages already done
./run.sh --workers 6                             # more windows in parallel (default 4)
./run.sh --tile full                             # tile windows over the whole screen
./run.sh --hidden                                # no visible windows: minimized to the Dock
```

Stopping with Ctrl-C is safe: progress is saved in `data/`, and running the same command again resumes.

## Output

- **`report.html`**: open it in a browser for a sortable, filterable table of every listing.
- **`results.csv`**: the same data, cheapest USD/acre first.
- **`data/`**: raw scraped records, exchange rates and crawl progress.

The `flags` column marks listings to double-check:

- `price too low`: probably a monthly payment or a per-m² price
- `may be payment plan price`: the text mentions "/mo", "down", and so on
- `auction/tender`: the price may be a minimum bid
- `wanted ad`: someone looking to buy, not a listing
- `no land area listed`: price per acre can't be computed

Exchange rates come from [open.er-api.com](https://open.er-api.com) and are cached daily.

## Notes

- Listing data belongs to the sites it comes from. Use this for personal research, keep the default
  delays between page loads, and check each site's terms.
- Cheap per-acre land is often remote, landlocked or unbuildable, and many countries restrict
  foreign ownership. Verify access, zoning, title and ownership rules before buying anything.
- `tools/` has small probe scripts used to work out Properstar's URL parameters.
