# QA report: `qa-sweep` branch

Date: 2026-10-22. All work is on the `qa-sweep` branch: nothing was pushed, merged, deployed or scraped.

## What was tested

**Site (`site/`), served locally with `python3 -m http.server 8000 --directory site`**, driven with headless
Playwright (Chromium headless shell). Tested at 1440x900 and 390x844, in light and dark colour schemes, plus a
spot check at 800px.

- Hero, runners-up, country ranking (30 countries), map, legend, footer, and the lazy table load on scroll and on
  the `#all` link.
- Every control: search, country select, max price, min acres (including negative and nonsense values), "Show
  flagged", every sort header in both directions, "Show 50 more", choosing a country in the ranking, the theme
  toggle (and that it survives a reload), and a map popup in dark mode.
- Empty state (a search with no matches) and error states (`data.json` returning 500; the Leaflet CDN blocked).
- Keyboard: tabbed through all controls and checked each one shows a focus outline.
- Console errors, page errors, failed requests and HTTP errors on every walk; horizontal overflow at each width.
- Links: the in-page anchors, the GitHub link, the CDN and map-tile hosts, and a random sample of listing URLs
  from each source (read-only requests).
- **Live site** (https://cheapest-land.pages.dev): the same walk, read-only.
- **`report.html`**: built from the current data into a scratch file, then filters, sorting and paging tested at
  both widths.

The data used was the existing `site/data.json` from the October 22 run (25,017 listings, 103 countries). `data/`
is not on this machine, so no full rebuild from raw records was possible (see "Blocked" below).

No console errors or failed requests turned up on any walk, local or live.

## Issues found

| # | Issue | Status |
|---|---|---|
| 1 | `build_outputs` wrote the HTML report to `report.page`, so the `report.html` that the README and log describe was never created. | **Fixed** (fe94547) |
| 2 | The flag "price may be per m2 (would buy under 20 m2 at local prices)" matched no entry in `FLAG_CODES`, so it got an empty site code and those listings counted as **clean** on the site (hero, ranking, map, table), while `results.csv` treated them as suspect. | **Fixed** (7af99b8): mapped to `U`; regression tests added |
| 3 | A Kijiji price under $300 matched both "price too low" and "too low for Canada", giving code `LL`. The table showed the same warning twice. | **Fixed** (7af99b8): codes are de-duplicated |
| 4 | Kijiji reposts were not de-duplicated when Kijiji had cut a title off at 64 characters ("Lot (PID 70257423) … Nova Scot" vs "(PID 70257423) … Nova Scotia"). Three such pairs are on the live site. | **Fixed** (b8999cc): merges only when one Kijiji title is a cut-off copy of the other. Checked against the full current data: it merges exactly those 3 pairs. A simpler prefix rule would have wrongly merged different lots ("Parcel 1/2/3", "Lot 25/26"). |
| 5 | "Show 50 more" was always visible: `.more{display:block}` overrode the `hidden` attribute. It showed before the data loaded and when nothing matched (clicking it did nothing). Also on the live site. | **Fixed** (0605812) |
| 6 | No matches showed an empty table with no explanation. | **Fixed**: "No listings match these filters." plus a "Clear filters" link |
| 7 | Phones had no way to sort: the table header (where the sort buttons are) is hidden below 720px. | **Fixed**: a small "Sort" menu shown only on narrow screens; header buttons and menu share one `sort()` |
| 8 | Phone filter layout: narrow inputs with empty space beside them, and the search placeholder was cut off. | **Fixed**: two-column grid with full-width inputs |
| 9 | Desktop: the search placeholder was cut off ("words in"), and the Country label sat 3px lower than the others because the select was shorter. | **Fixed** (0605812, 8567359): shorter placeholder, wider search box, equal control heights |
| 10 | When `data.json` failed to load, the count said so but the map stayed an empty grey box. | **Fixed**: the map shows a message too |
| 11 | Choosing a country in the ranking kept the previous "Show more" page depth. | **Fixed**: resets to 50 rows |
| 12 | Areas under 0.01 acres showed as "0.00". | **Fixed**: shows "<0.01" (JS and Python formatters) |
| 13 | Search matched the word "null" on listings with no title. | **Fixed** |
| 14 | No favicon. Cloudflare Pages answers unknown paths, `/favicon.ico` included, with `index.html` (status 200), so browsers got HTML instead of an icon. | **Fixed** (5ef8210): inline SVG favicon |
| 15 | `report.html` opened sorted by USD/acre, though everything else ranks by total price, and "Hide flagged" also hid listings whose only flag is "no land area listed", which the CSV and site count as believable. | **Fixed** (fa112a8) |
| 16 | The module docstring still said listings are ranked by USD per acre. | **Fixed** (8dd04b2) |
| 17 | **The live site runs an older build than `main`.** It renders the hero in the browser (crawlers and no-JS users see "Loading today's listings…") and loads the 5 MB `data.json` on every visit. Commit 7eb8324 on `main` already fixes both but hasn't been deployed. | **Not fixed (by design)**: no deploys were allowed. It will go live with the next daily run, or a `./publish.sh` after merging. |
| 18 | A LandWatch listing priced at $47 that is really "$47/mo" (its full text also says "Cash Price: $1,450") gets hidden rather than repriced to $1,450, because `fix_down_payment` returns at the monthly-payment check before it looks for a cash price. The README says the cash price is used when the text gives one. | **Not fixed**: this is a judgement call on the ranking heuristics, and hiding is the safe failure. A test pins the safety property (it never ranks at $47). To reprice instead, move the `/mo` early return below the `CASH_RX` lookup in `fix_down_payment`. |
| 19 | Absurd prices at the expensive end, only visible when sorting most-expensive first: a $1.7 billion plot in Honduras, and a Madagascar listing titled "200 HECTARES" whose Properstar area is under 0.01 acres ($702 billion/acre). No flag looks at the top end. | **Not fixed**: source data errors, and outside the cheapest-first purpose of the site. An upper sanity flag (e.g. over $50M total, or over $10M/acre) would be a small follow-up. |
| 20 | Unknown URLs on the live site return the homepage with status 200 (Cloudflare Pages' fallback when no `404.html` exists). | **Not fixed**: harmless for users. Adding a small `site/404.html` in `build_site` would make them real 404s. |
| 21 | LandWatch and Properstar listing links return 403 to `curl`. | **Not a bug**: their bot checks (the README already says they need a real browser). Kijiji, GitHub, the CDN and map tiles all return 200. Listing links couldn't be checked automatically for dead listings. |
| 22 | The site's `data.json` has flag codes in an order the current code doesn't produce (e.g. `NRXL`), so it was built by a slightly different version of `landfinder.py`. | **Note only**: it will be regenerated on the next run. |

Each fix was re-checked in the browser after it was made (`site/index.html` rebuilt from the existing
`site/data.json` with the new `tools/rebuild_site.py`), and a final full walk at all three settings was clean.

## Code changes besides fixes

- `build_site` was split: `write_site(rows, countries, stamp)` now fills the template. The output is byte-for-byte
  identical (checked against the original `site/index.html`). The one difference: the `generated` stamp in
  `data.json` and the "Updated …" line now come from one `now()` call instead of two microseconds apart.
- `site_codes(flags)` and `same_title(...)` were pulled out as small functions so they can be tested.
- New `tools/rebuild_site.py` rebuilds `site/index.html` from `site/data.json` without scraping.

## Test coverage added

`./test.sh` (or `.venv/bin/python -m pytest`): **100 tests, all passing, about 3 seconds**, no network.

- `tests/test_helpers.py` (33): `num`, `acres_from_text` (acres, ha, sq ft, m², decimal commas in
  French/Spanish), `to_usd`, `placeholder_price`.
- `tests/test_flags.py` (32): every flag `flag()` can raise, the down-payment fix, the Kijiji area re-read, the
  real "$47/mo" LandWatch listing, flag-text→site-code mapping, a check that **every** flag text has a site code
  (catches issue 2 if a new flag is added), and that codes are unique.
- `tests/test_parsers.py` (9): Properstar, LandWatch and Kijiji parsers and `landwatch_full_description`,
  against trimmed real pages in `tests/fixtures/` (fetched once on 2026-10-22; phone numbers, agent names and
  addresses replaced), plus empty and blocked pages.
- `tests/test_outputs.py` (22): `build_outputs` end to end in a temp dir: results.csv order, report.html, per-m²
  detection, placeholder detection, repost de-duplication, wanted ads dropped, the 3-day freshness window, the hero,
  the country ranking, template placeholders all filled, the formatters, and an empty site.
- `tests/test_e2e.py` (4, marked `e2e`): builds a site from the fixtures and loads it in headless Chromium at
  desktop and phone sizes (hero, ranking, lazy table, filters, empty state, sorting, phone sort menu, no overflow,
  no page errors, the data-failure message), plus a smoke test of the real `site/` build when one exists. Pages are
  served through Playwright routing on a fake origin: no port is opened and third-party requests are blocked.

I ran the e2e tests against the original template and they fail there (on issues 5 and 7), so they do catch those
regressions. The flag-code tests fail against the original `landfinder.py` too.

## Blocked or skipped

- **No `data/` folder on this machine**, so the full pipeline (`--report-only`) couldn't run on real raw records,
  and issues 2–4 couldn't be applied to the published data here. They take effect on the next scrape.
  `build_outputs` is covered end to end by tests with fixture data instead.
- Per instructions, no scrape, daily job, `publish.sh` or `install_daily.sh` was run.
- Playwright 1.60 (installed in `.venv`) expects Chromium build 1223, but the cache has 1243/1248. Everything here
  used the cached 1248 headless shell; `tests/test_e2e.py` falls back to it automatically. Nothing was downloaded.

## For you to do by hand

1. Review and merge `qa-sweep` into `main` (13 commits plus this report).
2. Deploy: the next 6:00 daily run will rebuild and publish, or run `./publish.sh` after merging. This also ships
   the server-rendered hero from 7eb8324 that isn't live yet (issue 17).
3. Decide on issues 18–20 (cash-price repricing, an upper price sanity flag, a 404 page).
4. Optional: `.venv/bin/playwright install chromium` so the e2e tests use Playwright's own browser rather than
   the fallback.
5. Local state: `.venv/` was created (gitignored). `site/index.html` was rebuilt from the same `data.json` with the
   new template; it's gitignored and gets regenerated by every run. The original build was backed up during the
   session in the scratch directory, which may be gone by now.
