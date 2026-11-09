# landfinder efficiency audit

*2 November 2026. Covers the working copy of landfinder.py as it stood at 21:00, with the uncommitted changes from
several sessions, including ready-checks and Chrome request blocking. Nothing in the repo was changed: every run used
a scratch copy of the project. Numbers marked "est." are extrapolations; the rest are measured, with the source next to
them or in the appendix. The measuring scripts and raw outputs are in the shared notes folder
(`~/Library/Application Support/Claude/side-session-notes/local_8b293b0e-fb10-4560-b6af-051ffe00de5d/efficiency-audit/`).*

## 1. Verdict

1. **Where the time goes:** a full refresh is about 1,420 Chrome page loads (LandWatch, Properstar and the verify step), each followed by a deliberate 2.5–5 s pause. The pauses are about 60% of the run, and they set each site's request rate. So the run gets shorter only by loading fewer pages: page speed, Python and the language hardly matter.
2. **Today:** the working copy should take about **38 min** per full run (est.; the committed code took about 69). For that time it keeps roughly one CPU core and 4.6 GB of memory busy, almost all of it Chrome. Translating new titles costs 330k–590k Claude tokens a day, 62% of them "thinking" the task doesn't need.
3. **After the recommended changes:** quick wins bring a run to **~28 min** and 120k–220k tokens: verify only what changed, translate without thinking, skip never-listed countries, drop the 15 s close hang, finalize only what changed. A daily newest-first pass with a full pass once a week brings it to **~6.5 min on six days out of seven, ~10 min a day on average**. That is about 4x less than today's working copy and 7x less than the committed code, with ~75% fewer page loads.
4. **Language or architecture:** no rewrite. Go, Rust or Node would save under a minute per run, because Python spends only ~50 s on the critical path, and Chrome does about 90% of the CPU work whatever language drives it. Yes to changing the crawl strategy (deltas), and to one Chrome with tabs if memory matters.
5. **For you to decide:** Properstar's robots.txt asks every crawler for 10 s between requests; landfinder sends one every 1.4–2.5 s. Kijiji's and Mercado Libre's robots.txt disallow the sort, filter and paging URLs landfinder uses. The delta design is also what would make following those rules affordable (section 5).

## 2. Time budget of a full run

The working copy, all sources, `--fresh`, 4 hidden windows. Estimated from the per-page means of three `--test` runs
with request blocking, the page counts of the 1–2 November runs, and separate measurements of each component.

| Stage | Seconds | % of run | Source |
|---|---:|---:|---|
| **Chrome pool**: LandWatch 596 pages + Properstar ~700 pages, 4 windows | **1,597** | **70%** | |
| – deliberate pause after each page (2.5–5 s, mean 3.7 s) | 1,197 | 53% | `--test` metrics: means over 19 LandWatch and 17 Properstar pages |
| – navigation: request, server and HTML (LandWatch 0.63 s, Properstar 1.28 s) | 318 | 14% | same |
| – ready waits, incl. Properstar's bot check on each window's first page (4.4 s) | 13 | 0.6% | same |
| – copying the HTML out, parsing and storing (Python) | 23 | 1.0% | metrics `read_s` + parser timings (appendix A5) |
| – retries ("page is navigating", 5 s each) and blocks | ~10 | 0.4% | 1–3 per full run in the 1–2 Nov logs; no blocks in 5 test runs |
| – Chrome launch (1.1–1.6 s) and the 3 s stagger | 8 | 0.4% | metrics `window` events |
| – windows idle at the end of the queue | 13 | 0.6% | simulation of `run_pool()` with the measured page counts |
| – Chrome close hang (15 s timeout) | 15 | 0.7% | both recent test runs hit it (4 of 4 and 1 of 2 windows) |
| **Verify**: 4 rounds, ~128 LandWatch ads in Chrome, ~70 Kijiji ads over HTTP | **280** | **12%** | est.: round sizes 60/37/22/9 on 1 Nov; 4.47 s per LandWatch ad, 2.48 s per Kijiji ad; 7.3 s re-finalize per round; 16 window launches |
| **Translate** new titles: 25–45 batches of 80, 4 at a time | **360** | **16%** | one batch measured at 41.7 s; batch counts from the 1–2 Nov logs |
| **Outputs**: `build_outputs()` finalizes 47.5k listings, writes CSV, HTML and site | **19** | **0.8%** | cProfile on the full store |
| **Deploy**: `publish.sh` | **13** | **0.6%** | daily log of 25 Oct |
| HTTP sites (Kijiji 3.4 min, OLX 5.1, Mercado Libre 0.6), in parallel | 0 | 0% | 2 Nov run log: done long before Chrome |
| **Total** | **~2,270 (38 min)** | **100%** | |

**Critical path:** loading the store → Chrome pool (26.6 min) → verify (4.7) → translate (6) → outputs → deploy. The
HTTP sites never are. With the pauses inside verify, about 60% of the run is deliberate sleep.

**The committed code** (HEAD `aa2bbef`) took about 69 min: a Chrome page then took 8.2 s per window instead of
~4.7 (a fixed 3.5 s wait plus the full page load, measured on the 2 Nov 19:23 run: ~863 pages in 29.5 min), the
older verify step took 18 min (1 Nov log), and translation 6 min. The uncommitted ready-checks and request blocking
have already roughly halved a run; no full run of them exists yet.

### Compute per full run (working copy)

| Resource | Amount | Source |
|---|---|---|
| CPU | ~25–35 CPU-min for Chrome and its Playwright drivers (1.0–1.4 CPU-s per page) and ~3 CPU-min for Python: about 0.8 of one core for the whole run | `ps` sampler on a 4-window `--test`: Chrome 57.9 CPU-s (85%), drivers 7.0 (10%), Python 3.4 (5%) for 45 Chrome and 17 HTTP pages |
| Memory | 4.6 GB resident with 4 windows (median; peak 5.0 GB): Chrome 3.85 GB, the 4 Playwright drivers 0.6 GB, Python 0.11 GB. `build_outputs()` peaks at up to ~0.85 GB | same sampler; `/usr/bin/time -l` on the profile |
| Bandwidth | ~260 MB. Chrome ~156 MB: LandWatch 58 KB a page, Properstar 158 KB (1.3 MB for each window's first page). HTTP ~103 MB: Mercado Libre 1.5 MB a page, Kijiji 446 KB, OLX 235 KB. Before request blocking: ~1.5 GB | metrics summaries |
| Claude tokens | 330k–590k a day for 25–45 batches. Per batch: 3.0k in, 10.1k out, of which 8.2k thinking. $0.14–0.25 a day at API list price | one batch measured with `--output-format json` (the CLI's `haiku` is `claude-haiku-5-5`) |
| Disk | listings.jsonl grows ~30 MB a run (~35k lines): ~11 GB a year at one run a day | listings.jsonl timestamps |

## 3. Changes, ranked by saving per unit of effort

| # | Change | Saves | Effort | Main risk |
|---|---|---|---|---|
| 1 | Translate without thinking | ~4.3 min a run, 63% of tokens | S | slightly more literal titles |
| 2 | Verify only what changed | ~4.2 min, ~180 page loads a run | S–M | an edit to the long description only is caught within a week, not a day |
| 3 | Don't wait 15 s for Chrome to close | 15 s a run | S | none |
| 4 | Visit the 44 never-listed Properstar countries weekly | ~1 min, 44 page loads a run | S | a first listing there shows up to a week late |
| 5 | Finalize only what changed; build outputs from the site window | ~45 s a run now, minutes later | S–M | results.csv drops listings unseen for 3+ days |
| 6 | Compact listings.jsonl | 2 s now; ~3.5 min a run and 11 GB a year later | S | the fetch-by-fetch history goes |
| 7 | Python 3.12 venv | ~20% of Python CPU | S | reinstalling dependencies |
| 8 | Read LandWatch's embedded JSON | ~10 CPU-s; exact map pins | S–M | a format change on LandWatch's side |
| 9 | Stop a site after repeated blocks | 0 normally; up to ~100 min when a site blocks | S | a short block costs that site a day's refresh |
| B1 | Daily newest-first deltas, full pass weekly | ~28 → ~10 min a day | L | sold listings stay up to 7 days (3 today) |
| B2 | Rotate searches: each every other day | ~28 → ~16 min a day | S–M | new listings up to 2 days late (1 today) |
| B3 | One Chrome with 4 tabs | ~2 GB of memory | M | one crash stops all 4 tabs |
| B4 | One scheduler with a per-host rate limit | nothing at today's rates | M | only gains by raising request rates |
| B5 | Move off the Mac | Mac time | M | datacenter IPs get blocked |
| B6 | Rewrite in Go, Rust or Node | under 1 min a run | L | not recommended |

### 3a. Quick wins (under a day each)

**1. Translate without thinking.** `translate()`
- *Change:* add `--effort low` to the `claude -p` call and run it with `MAX_THINKING_TOKENS=0` in its environment.
- *Evidence:* the same 80-title batch (3,459 characters), three ways:

  | Setting | Time | Output tokens (of which thinking) | Cost at list price |
  |---|---:|---:|---:|
  | today | 41.7 s | 10,090 (8,203) | $0.0057 |
  | `--effort low` | 30.3 s | 7,722 (5,863) | $0.0040 |
  | `--effort low` + `MAX_THINKING_TOKENS=0` | 11.5 s | 1,865 (0) | $0.0015 |

  Input is ~3,040 tokens either way. The CLI writes it to a 1-hour cache that no later batch reads, since each
  batch's titles differ; that doubles the input price, but at Haiku's prices it's $0.0006 a batch.
- *Saving:* translation ~6 → ~1.7 min a run; 330k–590k → 120k–220k tokens a day; ~$0.14–0.25 → $0.04–0.07 a day at
  list price (on a Claude subscription it counts against plan usage instead).
- *Effort:* S, two lines.
- *Risk to data quality:* the wording differed from today's output on 57 of 80 titles, mostly equivalent. In a spot
  check of 8, one got worse: "Продам дачу сад здоровье" was "Selling dacha, garden Zdorovye" and became "Dacha garden
  for sale, health." Translations are cached, so only new titles change.
- *Confirm:* one call with `--output-format json` shows `thinkingTokens: 0`; read 50 new titles on the site.
- *Free options first* (0 tokens):
  - Transliterate Cyrillic place names locally (an ICU transliterator or a small table). They are 16% of the texts:
    1,538 of 9,846 in the current site window, and the prompt only asks Claude to transliterate them anyway.
  - Translate only titles of listings the site shows by default: 79% of titles, so 21% fewer.
  - Translate everything with a local model (NLLB-200-distilled-600M through CTranslate2, or Argos Translate): 0 tokens,
    ~1–2 GB of memory; speed and quality on these titles untested.
- *Paid alternative:* the Message Batches API with Haiku 5.5 and thinking off would cost ~$0.0006 a batch (~$0.02 a
  day), but it needs an API key and asynchronous code. Prompt caching can't help: the only shared prefix is the
  ~150-token instructions, below Haiku 5.5's 512-token minimum.

**2. Verify only what changed.** `verify_cheapest()`
- *Change:* (a) skip a listing whose price and card description match its last verification, re-checking at most
  weekly; (b) keep each worker's Chrome open across rounds instead of closing it whenever its queue empties; (c)
  between rounds, re-finalize only the listings just verified, instead of `store.all()` plus `finalize()` on all ~19k
  LandWatch and Kijiji listings.
- *Evidence:* of 159 re-verifications so far, 154 (97%) found the same price and description. Each round re-finalizes
  19,250 listings (7.3 s) and opens 4 new Chrome windows: 16 launches a run. The Kijiji leg (one thread, ~60 ads at
  2.5 s) is the longest part of round 1.
- *Saving:* verify ~4.7 → ~0.5 min; ~120 Chrome and ~60 HTTP page loads a run.
- *Effort:* S–M, half a day.
- *Risk:* a seller who edits only the long description, not the price or the card text, is re-checked within a week
  instead of a day. Coverage is unchanged; politeness improves.
- *Confirm:* after the first day, the "verifying N of the cheapest 60" lines show N of about 2–6 per site, and "chrome
  landwatch verify" in the metrics summary drops from ~128 pages to ~10.

**3. Don't wait 15 s for Chrome to close.** `Browser.close()`
- *Change:* after `browser.close()` returns, give `pw.stop()` ~2 s, then stop the driver as today.
- *Evidence:* both recent test runs logged "Chrome didn't close within 15s" (4 of 4 windows at 21:17, 1 of 2 at
  20:37). The sampler found no Chrome process at all during those seconds: Chrome had quit, and only the Playwright
  driver was hanging.
- *Saving:* 15 s a run at the end of the Chrome pool, more if it ever hits a verify round.
- *Effort:* S. *Risk:* none; the driver kill stays. *Confirm:* the message is gone from the log, and "chrome pool"
  ends within ~2 s of the last job.

**4. Visit the never-listed Properstar countries weekly.** `main()`, `crawl_properstar_one()`
- *Change:* skip a Properstar country whose search found nothing on its last few runs, except once a week; keep it in
  coverage.csv.
- *Evidence:* 44 of the 147 country slugs haven't returned a listing in any of the 5 runs since 22 October, and each
  costs a Chrome page load every run.
- *Saving:* 44 page loads ≈ 55 s a run, ~7 MB.
- *Effort:* S. *Risk:* a first listing in one of those countries reaches the site up to a week late. *Confirm:*
  "chrome properstar search" shows ~656 pages instead of ~700.

**5. Finalize only what changed, and build outputs from the site window.** `finalize()`, `build_outputs()`, `fix_down_payment()`
- *Change:* cache `finalize()` results per stored line, keyed on a hash of the fields it reads, the FX date and a
  rules version. Build results.csv and report.html from the site window, or only with `--report-only`. In
  `fix_down_payment()`, match the payment pattern once with a generic number group instead of compiling a new regex
  for every price.
- *Evidence (cProfile, full store, Python 3.9):* `build_outputs()` takes 19.2 s, of which `finalize()` 14.0 s (`flag()`
  7.0, `property_type()` 5.1 with `has_house()` 4.1). `fix_down_payment()` compiled 7,713 regexes, most of its 0.5 s.
  `finalize()` runs on every listing ever seen, and results.csv (12 MB) and report.html (18 MB) hold all 45k of them.
- *Saving:* ~45 s of critical-path CPU a run today (outputs 19 → ~5 s, verify rounds 29 → ~1 s). Without it, the cost
  grows with every URL ever seen: at 0.5–1M URLs after a year of daily runs, `finalize()` alone would take 2–4.5 min
  a pass.
- *Effort:* S–M. *Risk:* results.csv no longer lists listings unseen for 3+ days (the site already drops them); a
  stale cache if the rules change without the version being bumped. *Confirm:* the "outputs" phase in the metrics
  summary is under 5 s.

**6. Compact listings.jsonl.** `Store`
- *Change:* at the end of a run, rewrite listings.jsonl to one line per URL (the latest, plus `first_seen` and the
  prices it has had). The SQLite version of this is a bigger job (one row per listing plus a price-history table)
  that also suits B1.
- *Evidence:* 135,482 lines for 47,527 URLs. A run appends ~35k lines, 99% of them at an unchanged price. Compacted
  today: 99 → 37 MB, parse 0.33 → 0.12 s. Each run parses the file about 6 times (`Store()`, 4 verify rounds,
  `build_outputs()`).
- *Saving:* ~2 s a run now; after a year of daily runs, ~3.5 min a run of parsing and ~11 GB of disk.
- *Effort:* S. *Risk:* the fetch-by-fetch history goes; price changes stay. *Confirm:* the line count equals the
  number of unique URLs after each run.

**7. Python 3.12.** `.venv`, `run.sh`
- *Evidence:* `finalize()` on all 47.5k listings takes 13.1 s on 3.9.6, 10.3 s on 3.12.16 and 11.4 s on 3.14.7. The
  .venv runs the macOS system Python 3.9, end of life since October 2025. orjson would save under 0.2 s per store
  load: not worth a dependency.
- *Saving:* ~20% of Python CPU (~30 CPU-s a run, ~10 s of it on the critical path).
- *Effort:* S (`/opt/homebrew/bin/python3.12 -m venv`, and `run.sh` names the interpreter). *Risk:* reinstalling
  dependencies. *Confirm:* time `--report-only --no-translate` before and after.

**8. Read LandWatch's embedded JSON.** `parse_landwatch()`
- *Change:* read `<script id="__SERVER_STATE__">` → `searchPage.searchResults.propertyResults` (price, acres,
  address, `latitude`/`longitude`, `hasHouse`, `types`, `lastUpdated`) plus each card's ld+json description,
  instead of running BeautifulSoup over the whole page.
- *Evidence:* 17.7 → 1.3 ms a page. All 25 listings on a sample page carry exact coordinates; today LandWatch pins sit
  at their ZIP code's centre.
- *Saving:* ~10 CPU-s a run, off the critical path. The bigger gain is data quality: exact map pins and LandWatch's
  own house flag.
- *Effort:* S–M. *Risk:* LandWatch changes the format, the same risk as with the card markup. *Confirm:* both parsers
  give the same records on 5 saved pages.

**9. Stop a site after repeated blocks.** `Browser.get()`, `run_pool()`
- *Change:* once 3 searches in a row on one site end blocked, skip that site's remaining searches for the run, and say
  so in the log.
- *Evidence:* a headless test (appendix A4) was blocked on every page. Each blocked page waits 20 + 40 + 60 s before
  giving up, so 10 searches took 9 min 11 s and found nothing. A site that blocks a whole run would add ~100 min
  (197 searches × ~2 min ÷ 4 windows) and send ~600 refused requests.
- *Saving:* nothing on a normal day; up to ~100 min and a lot of refused requests on a bad one.
- *Effort:* S. *Risk:* a block that clears within minutes costs that site the day's refresh (the site keeps its last
  listings for 3 days). *Confirm:* `--test --headless`, which gets blocked, finishes in about 2 minutes.

### 3b. Bigger bets

**B1. Daily newest-first deltas, with a full pass weekly.** `main()`, the `crawl_*` functions, `Store`, `site_cutoff()`
- *Change:* on most days, read each search newest-first until a page holds only listings already stored, and add
  what's new:
  - LandWatch: `/{state}-land-for-sale/sort-newest`
  - Properstar: `/{country}/buy/land-plot/most-recent?price.min=1500&price.max=<top of the last full pass>`, which sorts
    by update date, so it should also surface price changes
  - OLX: `sort_by=created_at:desc`
  - Kijiji: its default order

  Run the full cheapest-first pass weekly, or every 3 days. The site then keeps a listing until its search's next full
  pass no longer finds it, instead of for 3 days.
- *Evidence:* the cheap end changes slowly. Between the 1 and 2 Nov runs (~30 h apart), within the price range both
  reached, LandWatch had 4.4% new listings, 1.2% gone and 0.35% repriced; Properstar 7.9% new, 0.8% gone and 0.02%
  repriced. Page 1 was identical for 33 of 38 Properstar countries and 25 of 50 LandWatch states. Of all re-fetched
  listings, 99.2–99.9% came back at the same price. Both portals have a newest-first sort (probed: LandWatch
  `sortOrderId` 6, Properstar sort `-updateDate`). Arizona's first newest-first page spanned about a week of updates,
  and 16 of its 25 listings were already in the cheap range landfinder stores, so one page a day per state probably
  covers it; that's what the week of measuring would settle.
- *Saving (est.):* Chrome page loads on a delta day ~1,260 → ~190 (LandWatch 1–2 pages a state, Properstar 1 per
  country with listings); a run ~28 → ~6.5 min. Averaged over a week with one full pass: ~10 min, ~65 MB and 6–8
  CPU-min a day.
- *Effort:* L, about a week, including a week of measuring pages per search per day first. LandWatch's exact
  newest-first order, and whether a big state needs more than 1–2 pages a day, aren't known yet.
- *Risk:* *freshness:* a sold or withdrawn listing can stay on the site up to 7 days after it disappears (up to 3
  today), and LandWatch price cuts can show up to a week late. *Coverage:* unchanged after each full pass (~250
  believable listings per search). *Politeness:* ~75% fewer page loads.
- *Confirm:* for a week, log pages per search per day; on one day run both a delta and a full pass and compare what
  the site would show.

**B2. Rotate searches.** `main()`
- *Change:* crawl half the searches on even days and the other half on odd days. Keep the 3-day cutoff: it counts back
  from the newest scrape, so a search visited every other day keeps its listings, and sold listings drop when they do
  today.
- *Saving:* ~28 → ~16 min a day; page loads, CPU and bandwidth halved.
- *Effort:* S–M. *Risk:* new listings and price changes reach the site after 0–2 days instead of 0–1, 12 h later on
  average. If a day's run is skipped, half the searches go 4 days unvisited and their listings drop off for a day, so
  use a 4-day cutoff or one per search. *Confirm:* coverage.csv `on_site` stays ~250 per search on both days.

B2 is the cheap version of B1; the two don't add up.

**B3. One Chrome with 4 tabs.** `Browser`, `run_pool()`, `verify_cheapest()`
- *Change:* drive 4 pages in one Chrome from one asyncio loop (`playwright.async_api`), instead of 4
  `sync_playwright()` instances in 4 threads.
- *Evidence:* with 4 instances, each Chrome's browser, GPU, network and storage processes take ~540 MB and each
  Playwright driver ~150 MB; the tabs themselves take 1.83 GB in all.
- *Saving:* ~2 GB of the 4.6 GB resident (less in real memory, since resident size counts pages that processes
  share); 3 fewer launches, close hangs and driver processes. The 2 Nov full run ended in EPIPE errors from those
  drivers. Run time doesn't change at the same request rates.
- *Effort:* M, 1–2 days. *Risk:* one browser crash stops all tabs (the relaunch code exists). *Confirm:* the sampler
  on a 4-window `--test`.

**B4. One scheduler with a per-host rate limit.** `run_pool()`
- *Evidence:* simulating `run_pool()` with the measured page counts (4.7 s per LandWatch page, 5.5 s per Properstar
  page):
  - today's queue: 28.1 min, with 51 window-seconds idle at the end
  - longest jobs first: 27.9 min, with 6 s idle
  - a limiter holding each site to today's *average* rate: 35.5 min, slower, because today Properstar speeds up once
    LandWatch is done
  - a limiter holding each site to today's *peak* rate (LandWatch a page every 1.25 s, Properstar every 1.37 s): 22.8
    min with 4 windows, 23.3 with 2
- *Verdict:* it would tidy the code and halve the windows, but it saves time only by running each site at its peak all
  the time. That's a politeness decision, and for Properstar it goes the wrong way (section 5). The idle tail is ~13
  s, so ordering jobs differently isn't worth doing.

**B5. Where it runs**

| Option | Mac time a day | $ a month | Setup | Test first |
|---|---|---|---|---|
| Everything on the Mac (today) | ~38 min; ~28 after quick wins; ~10 with B1 | $0, plus Claude usage | none | – |
| HTTP sites on a GitHub Actions cron | only HTTP time, which is off the critical path | $0 on a public repo; ~150 of a private repo's 2,000 free minutes | S–M: keep the store between runs (committed state or Actions cache) | one `--sources kijiji,olx,mercadolibre --test` from a runner |
| A small VPS running everything, Chrome under Xvfb (e.g. Hetzner CX22) | 0 | ~$5 (price not re-checked), plus a proxy if blocked | M | LandWatch (Akamai) and Properstar (Azure WAF) already refuse plain clients from your home IP, and datacenter IPs often get blocked even in a real browser: one `--test` from the VPS, then the summary's "problems" line |
| A cloud browser (Browser Use) for LandWatch and Properstar | ~5 min (HTTP sites) | ~$0.82 a run at today's pages (~$25 a month); ~$6 a month with B1 | M: connect Playwright over CDP | one `--test` through it |
| Cloudflare Workers cron | – | $0–5 | L: a rewrite in JS, and the free plan's 10 ms of CPU per run can't parse a 1.5 MB Mercado Libre page | not recommended |

The cloud-browser figure is ~2.1 browser-hours at $0.02 plus ~156 MB of proxy traffic at $5/GB. The other session's
$1.60 a run assumed ~2,800 Chrome pages; the stored data shows ~1,420.

**B6. Language and runtime**
- Python uses ~3 CPU-min a run, but only ~50 s of it is on the critical path: verify re-finalizing 29 s, outputs 19
  s, store loads 2 s. The rest overlaps with the pauses. Chrome and its drivers do about 90% of the CPU work, and that
  stays the same whatever language drives them.
- So a Go or Rust rewrite could save at most ~50 s of a 38-minute run today, and ~10 s after quick wins 2 and 5. Node
  would remove the hop from Python to the Playwright driver (~2 CPU-min a run), but not wall time. Python 3.12 takes
  another ~20% off what's left, for free.
- **Verdict: no.**

## 4. Projected run time and compute (est.)

| Scenario | Chrome page loads | Run time | Chrome CPU | Bandwidth | Claude tokens a day |
|---|---:|---:|---:|---:|---:|
| Committed code | ~1,420 | ~69 min | not measured | ~1.5 GB | 330k–590k |
| Working copy (today) | ~1,420 | ~38 min | 25–35 min | ~260 MB | 330k–590k |
| + quick wins 1–8 | ~1,260 | ~28 min | 22–30 min | ~230 MB | 120k–220k |
| + B2 rotation | ~630 | ~16 min | 11–15 min | ~120 MB | 120k–220k |
| + B1 deltas, delta day | ~190 | ~6.5 min | 3–5 min | ~40 MB | 120k–220k |
| + B1 deltas, weekly full pass | ~1,260 | ~28 min | 22–30 min | ~230 MB | 120k–220k |
| + B1 deltas, average a day | ~340 | ~10 min | 6–8 min | ~65 MB | 120k–220k |

Memory stays at ~4.6 GB during a crawl, or ~2.6 GB with B3. B1's bandwidth assumes the HTTP sites go newest-first
too. Running translation during the crawl instead of after it would take a further ~1.7 min off every row.

## 5. Site rules: a decision for you

The robots.txt rules that apply to every automated agent (`User-agent: *`), read on 2 Nov. Properstar's and
LandWatch's were read through Chrome, since they refuse plain clients.

| Site | Rule | What landfinder does | What following it would cost |
|---|---|---|---|
| Properstar | `Crawl-delay: 10` | a page load every 1.4–2.5 s (0.4 a second on average, 0.73 at peak) | a full pass of ~700 pages takes ~2 h (one window, mostly idle); with B1, ~103 pages ≈ 17 min a day |
| Kijiji | `Disallow: /*?*sort=*` and `/*?*price=*` | every search URL has `?sort=priceAsc` | no cheapest-first order. Newest-first paging (`/page-N/`) is allowed and fits B1, but building the cheap range from scratch means walking all ~28k listings once (~700 pages) |
| Mercado Libre (.ar, .mx) | `Disallow: /*_OrderId_`, `/*_NoIndex_True`, `/*_Desde_` (and `_PriceRange_` on .ar) | search URLs use all of them | no allowed cheapest-first URL; the official API needs a registered app's token |
| LandWatch | `Allow: /`, `Disallow: /api/`; no delay for `*` (Bing gets 2 s, AI crawlers 5 s) | ~0.6 page loads a second, 0.8 at peak | nothing |
| OLX | `Allow: /api/v1/offers/` | uses that endpoint | nothing |

Properstar and Mercado Libre also block Anthropic's and OpenAI's crawlers by name. landfinder isn't one of them: it
identifies as Chrome. I didn't read the sites' terms of service, and I changed nothing.

## 6. Not measured, and how to measure it

| What | How |
|---|---|
| A full run of the working copy: the 38-min estimate, the ~700 Properstar pages, verify's real length | `./run.sh --fresh --hidden` (~40 min with 4 hidden Chrome windows, so it needs your OK), with `python3 sampler.py <pid> out.json` from the notes folder for CPU and memory |
| Delta pages per search per day | for a week, read each search newest-first until a page has nothing new; log the pages |
| Whether LandWatch takes a price cap in a `sort-newest` URL | 1–3 test page loads |
| Whether LandWatch starts blocking pages that never run its scripts (the other session's open risk) | the "problems" line of the next full run |
| Whether datacenter IPs get through | one `--test` from a GitHub Actions runner or a VPS |
| Local translation quality and speed | NLLB-200-distilled-600M or Argos on 500 cached titles, against Claude's |
| Translations a day in steady state | the "translating N texts" line over the next 3–5 daily runs (the 1–2 Nov counts include backlog) |
| Real memory rather than resident size | `footprint` or Activity Monitor during a run |
| Deploy time with today's 9.7 MB data.json | `time ./publish.sh` (not run, by the rules) |
| Why the 2 Nov 19:23 run stopped at job 96 | its log ends in EPIPE errors, which the drivers print when the Python process dies; the log doesn't say whether it was killed or crashed |
| Python 3.13, orjson, selectolax | not installed. 3.12 and 3.14 were measured, and reading the embedded JSON makes selectolax unnecessary |
| The sites' terms of service | not read |

## Appendix: evidence

**A1. Page loads in a full run**

| Source | Pages | How counted |
|---|---:|---|
| LandWatch search | 596 (50 states, median 12, max 19) | timestamps in listings.jsonl, 2 Nov 19:23 run |
| Properstar search | ~700 (est.) | 36 countries need ~14 pages (≥190 listings on 1 Nov), 67 need 1–10, 44 have never had a listing |
| LandWatch verify | ~128 | 60 + 37 + 22 + 9 on 1 Nov |
| Kijiji search / verify | 78 / ~70 | searches.jsonl of the 2 Nov 20:16 run; verify est. |
| OLX | 116 | same |
| Mercado Libre | 16 | same |

**A2. Per page** (means over the three `--test` runs with request blocking, 2 Nov)

| Fetch | n | Total s | Navigation | Ready | Pause | KB |
|---|---:|---:|---:|---:|---:|---:|
| Chrome, LandWatch search | 19 | 4.38 | 0.63 | 0.00 | 3.72 | 58 |
| Chrome, LandWatch verify | 34 | 4.47 | 0.57 | 0.00 | 3.88 | 49 |
| Chrome, Properstar, later pages | 17 | 5.03 | 1.28 | 0.05 | ~3.7 | 158 |
| Chrome, Properstar, each window's first page (403, then a bot check) | 6 | 8.53 | 0.24 | 4.44 | ~3.7 | 1,295 |
| HTTP, Kijiji search | 5 | 2.69 | 0.70 | – | 2.00 | 446 |
| HTTP, Kijiji verify | 10 | 2.48 | 0.64 | – | 1.84 | 246 |
| HTTP, Mercado Libre | 5 | 2.39 | 0.78 | – | 1.61 | 1,512 |
| HTTP, OLX | 5 | 2.08 | 0.57 | – | 1.51 | 235 |

**A3. What changes between runs** (listings.jsonl, within the price range both runs reached)

| 1 → 2 Nov, ~30 h | LandWatch | Properstar | Mercado Libre | OLX |
|---|---:|---:|---:|---:|
| listings in range | 12,978 | 4,257 | 768 | 5,617 |
| never seen before | 576 (4.4%) | 335 (7.9%) | 24 (3.1%) | 2,747 (49%) |
| gone | 146 (1.2%) | 31 (0.8%) | 70 (8.6%) | 590 (17%) |
| repriced | 45 (0.35%) | 1 (0.02%) | 0 | 24 (0.4%) |
| page 1 identical | 25 of 50 | 33 of 38 | – | – |

- OLX's cheapest-first results reshuffle between requests: 10.6% of them were "new" between two runs 55 min apart.
- From 25 to 26 Oct (24 h, 21 LandWatch states): 0.9% new, 5.2% gone, 0.2% repriced.
- Of all 88,000 re-fetched listings, 99.2% (LandWatch) to 99.9% (Mercado Libre) came back at the same price as the
  time before.

**A4. Probes** (one request each, 3 s apart; the Chrome ones through landfinder's own `Browser` at its normal delays)
- Plain HTTP: LandWatch's search page, an ad and robots.txt all return HTTP 403 from AkamaiGHost, with and without
  landfinder's browser user agent. Properstar's search page returns HTTP 429 to the default client and a 403 "Azure
  WAF" page to landfinder's user agent. So the browser stays for both. Nothing here tries to get around the checks.
- In Chrome:
  - Properstar ignores `pageSize=100`: its state still says `pageSize: 20`, and it caps any search at 2,000 results.
    `/most-recent` sorts by `-updateDate`.
  - LandWatch ignores `?pageSize=100` and keeps 25 a page. `/sort-newest` works (`sortOrderId` 6).
  - LandWatch's page embeds `__SERVER_STATE__` JSON with all 25 listings, including coordinates.
- Headless Chrome (`--headless`, the same 4-window `--test`): every LandWatch page got Akamai's "Access Denied" (403)
  and every Properstar page a 429 "Service unavailable", so all 10 searches came back empty. CPU and memory fell (24
  CPU-s, 3.7 GB) only because nothing loaded. Right after, the normal minimized window still got 25 LandWatch
  listings and 20 Properstar ones. Playwright passes `--headless` to the real Chrome, and the user agent then says
  "HeadlessChrome". Hiding that would be getting around the sites' bot checks, so the visible-but-minimized window
  stays.

**A5. Python CPU** (the full store of 47,527 listings, Python 3.9.6, Apple M4)

| Step | Time |
|---|---|
| `Store()` / `store.all()` | 0.36 s / 0.41 s |
| `finalize()` on every listing | 12.7–14.0 s (268 µs each): `flag()` 7.0, `property_type()` 5.1 (`has_house()` 4.1), `fix_down_payment()` 0.5, mostly compiling a regex per price (7,713 compiles) |
| one verify round: `store.all()` + `finalize()` on 19,250 listings | 7.3 s |
| `build_outputs(english=False)` | 19.2 s: `finalize()` 14.0, `build_site()` 3.8 (`town_populations()` 2.7) |
| `parse_landwatch()` / the embedded JSON | 17.7 / 1.3 ms a page |
| `parse_kijiji()` / `parse_properstar()` / `parse_meli()` / `landwatch_full_description()` | 14.2 / 4.2 / 2.8 / 6.0 ms a page |

**A6. The 4-window test run** (scratch copy: `--test --workers 4 --pages 3`, 4 states, 6 countries, 97 s)
- CPU by process: Chrome renderers 30.0 s, Chrome browser 16.8, GPU 8.3, network 1.9, storage 0.7, Playwright drivers
  7.0, Python 3.4: 68.3 CPU-s in all.
- Resident memory with 4 windows: 4.63 GB median, 5.0 GB peak.
- 20 Chrome launches: 4 for the pool and 16 for verify, 4 per round.

**A7. The site's data.json:** 9.7 MB for 35,224 rows. Each visitor downloads ~2.0 MB of it brotli-compressed (2.5 MB
gzip). That's 37% of Cloudflare Pages' 25 MiB per-file limit, and the 3-day window bounds it. URLs are 30% of the raw
size. Writing it as UTF-8 instead of `\u` escapes saves 0.6 MB raw and nothing compressed. Nothing to do until a new
source pushes it past ~15 MB; then split the rows by country and load the chosen country on demand.
