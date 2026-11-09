# landfinder: efficiency and bottleneck audit

You are auditing **landfinder** (this repo) for speed and compute cost. It scrapes the cheapest land listings from five
portals, converts prices to US dollars, ranks them by total price and publishes a static site (cheapest-land.pages.dev).
A full refresh takes an hour or more on the owner's Mac with four Chrome windows open. The daily scheduled run is
switched off.

**Goal:** find what would cut the time and compute of a daily refresh by a large factor (think 5–10x, not 10%), and say
plainly whether a different approach, architecture, runtime, host or programming language would get there. Compute means
wall-clock time, CPU and RAM on the Mac (mostly Chrome), bandwidth, and Claude tokens. The build step calls the `claude`
CLI to translate titles, and the owner watches that usage.

Keep what the site delivers: the same sources and searches, about 250 believable listings per search, the price checks,
and the verification of the cheapest listings. If a recommendation gives any of that up, label it as a trade-off for the
owner to decide.

## How it works today (check it, don't trust it)

Read README.md first. All the code is in landfinder.py (~2,000 lines). It has uncommitted work in progress, so refer to
functions by name, not by line number.

1. **Crawl.** `main()` starts one thread per plain-HTTP site: `crawl_kijiji` (11 provinces), `crawl_olx` (7 OLX country
   domains, one after another) and `crawl_meli` (2 Mercado Libre domains, one after another). Meanwhile `run_pool()`
   runs 197 browser jobs (LandWatch for 50 US states, Properstar for 147 countries) across 4 Chrome windows driven by
   Playwright (`Browser`). Each search goes cheapest first until it has 250 believable listings (`--listings`), runs out
   of results, or reaches 30 pages (`--pages`). After every page, `Browser.get` sleeps 2.5–5 s and `Plain.get` sleeps
   1.25–2.5 s (`--delay 2.5`).
2. **Verify.** `verify_cheapest()` reopens the 60 cheapest believable LandWatch listings (in Chrome) and Kijiji listings
   (over HTTP) to read their full description, in up to 4 rounds. Each round reloads every stored listing and runs
   `finalize()` on them.
3. **Build.** `build_outputs()` translates new non-English titles (`translate()`: `claude -p --model haiku`, 80 titles
   per call, 4 calls at once, cached in data/translations.json). It then runs `finalize()` on every listing (flags,
   property type, ZIP location) and writes results.csv, report.html, coverage.csv and, through `build_site()`,
   site/data.json. The site shows listings seen in the last 3 days (`site_cutoff()`).
4. **Publish.** publish.sh deploys site/ with wrangler. daily.sh runs `./run.sh --fresh --hidden && ./publish.sh`.

Storage: data/listings.jsonl is append-only. Every fetch appends the listing again, and the newest line per URL wins.
data/pages_done.txt and data/searches.jsonl let an interrupted run resume; `--fresh` clears them.

Instrumentation exists already. The `Metrics` class logs every fetch (seconds spent on nav, ready, read, delay and wait,
and bytes by resource type), every Chrome window and every phase to logs/metrics-<time>.jsonl, and prints a summary at
the end of a run. `./run.sh --test` does a small sample run (2 states, 2 countries, 1 province, 2 pages each, 5 listings
verified, 2 hidden windows) in data-test/ and leaves the site alone.

Work already under way: on 2026-11-02 another Claude session ("Cloud browser cost and parallelization") was adding Chrome
request blocking (`BLOCK_URLS`: ad, analytics, map and font hosts, plus LandWatch's own scripts) and pricing a cloud
browser. Check `ListAgents` and `git diff` for its state and build on its numbers instead of redoing them. Don't run
tests at the same time as it: both use data-test/.

## Evidence so far (2026-11-02; re-measure before relying on it)

- **Per page**, from a `--test` run before request blocking was added (logs/metrics-2026-11-02-202933.jsonl, small
  sample):

  | fetch | total | request or navigation | waiting for listings | deliberate sleep |
  |---|---|---|---|---|
  | Chrome, LandWatch search | 4.3 s | 0.3 s | 0.03 s | 3.9 s |
  | Chrome, Properstar search | 5.7 s | 0.75 s | 1.0 s | 3.8 s |
  | Chrome, LandWatch listing (verify) | 5.1 s | 0.9 s | 0.01 s | 4.1 s |
  | HTTP, Kijiji / OLX / Mercado Libre | 2.4–2.7 s | 0.5–0.75 s | – | 1.7–2.2 s |

  So 65–90% of every page is sleep, not work. Chrome pages downloaded 0.56–1.1 MB each, 70–85% of it JavaScript.
- **Critical path.** In the Nov 2 run, the three HTTP sites were done within 5 minutes. The Chrome pool had finished 96
  of its 197 jobs after 29 minutes, where the log ends in Playwright driver EPIPE errors. A later run fetched 439 Chrome
  pages in under 9 minutes, which matches wall time ≈ Chrome pages × 4.7 s ÷ 4 windows.
- **Pages.** LandWatch needed 6–18 pages per state (median 12) to collect 250 believable listings, so about 600 per
  run. Of 27 Properstar countries in that run, 6 returned nothing and 14 fewer than 20 listings, yet each costs at least
  one Chrome page load on every run.
- **Nov 1 run** (older code, `--pages 10`): crawl 41 min, then verify 18 min. Translating 5,098 new titles took 64
  claude calls and 6 min.
- **CPU**, on the 47,527 stored listings: loading listings.jsonl takes 0.4 s; `finalize()` on all of them takes 14 s,
  about 6 s of it in `property_type()`. `build_outputs()` runs it on every listing, and `verify_cheapest()` runs it on
  all LandWatch and Kijiji listings in every round.
- **Storage.** listings.jsonl is 96 MB: 135,482 lines for 47,527 unique URLs, growing by every listing fetched on every
  run. report.html is 18 MB, results.csv 12 MB, and site/data.json 9.7 MB, which every site visitor downloads in full.
- The .venv runs Python 3.9.6 (the macOS system Python, end of life since October 2025).

## Ground rules

- **Measure before you recommend.** Back every claim with a number from a run, a profile, a log or the metrics files.
  Label estimates and state their assumptions.
- **Don't disturb work in progress.** The working tree holds uncommitted changes from several sessions, so don't edit
  tracked files during the audit. Before starting any run, check that no other is active (`pgrep -f landfinder.py`).
  Use `--test`, and a scratch copy of the project (with data/) for profiling. Never write to data/ or site/ in this
  folder, and never run publish.sh. Ask the owner before a full-size run: it takes an hour or more and opens Chrome on
  their Mac.
- **Don't buy speed by being less polite.** Keep each site's request rate at or below today's. If a change would raise
  it, list it separately as a policy decision. Follow each site's terms. Don't propose CAPTCHA solving or new ways
  around bot protection: where a site blocks plain clients, the browser stays.
- **Zero-token options first.** For anything involving Claude, give the free option before the paid one, with token
  counts.

## What to find out

Build the time budget first, then work through the levers in order of how much they could save.

1. **Time budget of a full run.** Split wall time into deliberate sleep, network, page rendering and ready waits, block
   waits and retries, Chrome launch, close and crashes, windows idle at the end of the queue, verify, translate, outputs
   and deploy. Find the critical path. Measure Chrome's CPU and RAM with `ps` or `top` during a `--test` run. Use a full
   run only with the owner's OK; otherwise extrapolate from `--test` and the logs in logs/. Present it as a table:
   stage, seconds, % of wall time, where the number comes from.
2. **Fewer page loads.** Count page loads per source and per search, and how many bought nothing: searches that come
   back empty run after run, pages whose listings are all stored already at the same price, and listings re-verified at
   an unchanged price (a listing is re-verified unless it was verified *today*). Evaluate delta crawls (a newest-first
   sort for new listings, with the full cheapest-first pass weekly), skipping a search's deeper pages when its first
   page hasn't changed since the last run, visiting slow-changing searches less often, bigger page sizes where a site
   allows them, one global Properstar search instead of 147 per country, and whether 250 listings / 30 pages is the
   right stop rule. Any plan that re-fetches less often must also change how the site drops listings, since it keeps
   only those seen in the last 3 days.
3. **Less browser.** For Properstar and LandWatch, re-test whether a browser is needed at all: a plain request for the
   same URL, the JSON the page loads itself, sitemaps or feeds. LandWatch's listings are there ~0.03 s after
   DOMContentLoaded, which suggests they come in the server HTML. Where Chrome stays, start from the request-blocking
   results, then compare one Chrome with several tabs against 4 Chrome processes, and count the 3 s launch stagger and
   the 15 s close timeout.
4. **Scheduling.** Sleeps are per window or thread, not per site. Once the critical path is known, decide whether one
   scheduler with a per-host rate limit (asyncio with httpx or aiohttp, or threads with per-host token buckets) would
   shorten the run or only tidy it, given that the HTTP sites already finish early. Check whether running the longest
   jobs first trims the tail where windows sit idle.
5. **Verification.** Cost per round and per day. Keep a record keyed on (url, price, description) so unchanged listings
   aren't reopened every day. Check whether LandWatch listing pages load without Chrome.
6. **CPU.** Profile `build_outputs()` and `verify_cheapest()` with cProfile or py-spy. Look at regexes compiled per
   record (`fix_down_payment()` builds patterns from the price), `finalize()` rerun on listings that haven't changed,
   BeautifulSoup where `json.loads` on embedded JSON would do, and what Python 3.12/3.13, orjson or selectolax buy.
7. **Storage and outputs.** How fast listings.jsonl grows per run and per year, and its load time at that size. Compare
   compaction with SQLite (one row per listing plus a price history). Ask whether report.html and results.csv need
   rebuilding every run. For site/data.json: its size per visitor, Cloudflare Pages' 25 MiB per-file limit, and a
   smaller format (columnar, split by country, only the fields the page uses).
8. **Translation.** Tokens and seconds per batch and per daily run. Compare free options (a local translation model,
   translating only what the site shows) with Haiku through the API using the Message Batches API and prompt caching,
   and with today's claude CLI.
9. **Where it runs.** Compare everything on the Mac (minutes of Mac time per day), the HTTP sites on a free scheduler
   (GitHub Actions cron, Cloudflare Workers cron) or a small VPS, and the Chrome sites on a cloud browser (the `Metrics`
   summary already estimates Browser Use's price). Datacenter IPs get blocked more often than a home connection, so say
   what you'd test first. Give $/month and setup effort for each.
10. **Language and runtime.** From the measured CPU share, what's the most a rewrite in Go, Rust or Node could save per
    run, compared with the same fixes in Python? Answer in minutes, not adjectives.

## Deliverable

Write the report to docs/efficiency-report.md (don't commit it) and summarize it in the chat:

1. A five-line verdict: where the time goes, the projected run time and compute after the recommended changes, and yes
   or no on changing language or architecture.
2. The time-budget table.
3. Changes ranked by saving per unit of effort. For each: what to change (file and function), the evidence, the
   expected saving (minutes, CPU/RAM, MB, tokens), effort (S/M/L), the risk to coverage, data quality, blocking or
   politeness, and how to confirm it worked.
4. Quick wins (under a day each), then bigger bets.
5. What you couldn't measure, with the command or experiment that would.

Stop after the report and ask the owner which changes to make. Don't commit, push or deploy.
