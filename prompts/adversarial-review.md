# landfinder: adversarial review

You are reviewing **landfinder** (this repo) as an attacker and as a hostile data source, not as a helpful colleague.
Your job is to break it: find inputs, sequences and conditions under which it publishes wrong prices, leaks what it
shouldn't, lets a stranger past the password, corrupts its own data, or hurts the owner's machine or accounts.
Assume every finding you report will be checked, so only report what you have confirmed by reading the code path end
to end, or by running it. Say "unconfirmed" on anything else.

## What it is

Read README.md first. `landfinder.py` (~2,400 lines) scrapes the cheapest land and house listings from Properstar,
LandWatch, Kijiji, OLX, Mercado Libre and Ieichiba, converts prices to USD, filters and dedupes them, translates titles
through the `claude` CLI, and writes a static site to `site/` (index.html + data.json). `publish.sh` deploys `site/` to
Cloudflare Pages. `functions/_middleware.js` is a password gate in front of the whole site. `functions/api/visitors.js`
is a visitor counter backed by a D1 database (`wrangler.toml`). The repo is on GitHub.

Do not take the comments at their word. Each file opens with a comment saying what it guarantees; treat those as
claims to falsify.

## Attack from these positions

Take each in turn. For every one, write down the attack you tried, the code path it goes through, and the outcome.

**1. A stranger on the internet, no password.** Target `functions/_middleware.js` and the Pages deployment.
- Is anything reachable without the cookie: static files, `data.json`, the API, Cloudflare's own paths, HEAD/OPTIONS,
  other methods, trailing-slash or case variants, encoded paths?
- The session cookie is an HMAC of a fixed string under the password, identical for every visitor and valid until the
  password changes. What does that buy an attacker who sees one cookie once? What about the 30-day lifetime?
- Can `/login` be brute-forced? Count what stops it: there is no server-side state by design.
- Attack `safeNext`: find a value that passes the regex and still sends a browser off-site (think backslashes,
  schemes without `//`, protocol-relative tricks, newline or tab characters), or one that causes a redirect loop.
- Look at what the gate does when `SITE_PASSWORD` is set but empty, or when `env` is missing on a preview deployment.
- The site's contents are also in git (`site/index.html`, `site/data.json`). Does the password gate protect anything
  that the repository doesn't already expose? Say so plainly.

**2. A visitor with the password who wants to abuse the API.** Target `functions/api/visitors.js`.
- Inflate or stall the counter: spoof `cf-connecting-ip`, vary the user agent, replay `{first: true}`, send a huge or
  malformed body, hit it with many concurrent requests. What does the module-level `ready` flag do across isolates?
- Can the day-rollover logic double count, lose the salt, or pin everyone to one hash?
- Cost: how many D1 writes can one client cause per second, and what does that do to the free tier?

**3. A listing site that wants to poison the results.** Target the parsers (`parse_*`), `finalize`, `to_usd`,
`placeholder_price`, `fix_down_payment`, `flag`, `believable_flags`, `collapse_similar`, `verify_cheapest`.
- Craft a listing (title, price string, area, attributes, description) that lands at the top of the site at a wrong
  price: a rent shown as a sale, a per-m² price shown as a total, a down payment, a currency mis-read, an area that
  turns $/acre absurd, a duplicate that collapses the wrong way so the cheaper real listing disappears.
- Scraped text goes into `site_template.html`. Find every place a title, region, description or URL is inserted and
  check how it's escaped. Build a title or URL that runs script, breaks out of an attribute, or `javascript:` links.
- Scraped titles and place names are sent to Claude in `translate()` with `TRANSLATE_PROMPT`. Write a listing title
  that makes the model return something other than a translation, or breaks the JSON contract, and trace what the
  cache in `data/translations.json` then serves forever.
- `unicodedata`, `title_key`, `place_key`: find homoglyph or normalisation inputs that merge different listings or
  split the same one.

**4. A failure in the middle of a run.** Target `Store`, `search_pages`, `run_pool`, `verify_cheapest`, resume logic.
- Kill the process after a partial write to `listings.jsonl`, `pages_done.txt` or `searches.jsonl`. What does the next
  run load, skip, or double-count? Is "newest line per URL wins" true under concurrent appends from the crawl threads?
- Two runs at once (the launchd job and a manual one, or two Claude sessions): what breaks?
- A site returns 200 with an empty or error page: does the search get marked done with zero listings, and does the
  site then drop that whole country as "not seen lately"?
- `fresh_days` cutoff: can a stale run publish an empty site, or a site missing a source, without failing loudly?

**5. The owner's machine and accounts.** Target `kill_tree`, `screen_area`, `Browser`, `translate`, `publish.sh`,
`run.sh`, `daily.sh`, `install_daily.sh`.
- `kill_tree` walks `pgrep -P`. Can PID reuse or a race kill the wrong process? `osascript` is run against Finder.
- The `claude` CLI is invoked with `--tools ""` and a strict MCP config. Confirm from the actual command line that a
  scraped title cannot get the model a tool, a file, or network. Check the timeout and what a hung call does to the run.
- `publish.sh` runs `wrangler pages deploy --commit-dirty=true`. What ends up deployed from a dirty tree: `data/`,
  `.dev.vars`, logs, the password? List exactly what is in `site/` at deploy time and what Pages would serve.
- Secrets: grep the tree and git history for the site password, Cloudflare tokens, the D1 id, or API keys.

## Rules

- Confirm before you report. Trace the code, or run `python3 -I` against a copied input, or `wrangler pages dev` for the
  functions. Quote the lines (by function name, since the file has uncommitted edits).
- Rank by what an attacker gains, not by how clever it is. A wrong price at the top of the site matters more than a
  theoretical timing side channel.
- Give a one-line reproduction for each finding: the input or request, and the wrong output.
- Don't rewrite the project. For each finding, state the smallest fix, and say whether it changes behaviour the
  owner relies on (such as the "no server-side state" design of the gate).
- Finish with the three things you'd fix first and the one thing you tried hardest to break and couldn't.
