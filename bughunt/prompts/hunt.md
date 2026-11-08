You are hunting for real bugs in landfinder, a Python scraper that ranks the cheapest land
listings on earth by total USD price and publishes them to a static site.

Scope for this pass: {SCOPE}
Bug categories to focus on: {CATEGORIES}

Already known — do not report these again:
{KNOWN}

Rules:
- Report only concrete bugs: wrong output, a crash, data loss, a wrong price/area/flag, a
  listing ranked when it shouldn't be (or hidden when it shouldn't be), a security hole.
  No style, naming, refactors, performance nits or "could be cleaner".
- Every finding needs: file and line, the exact input or sequence that triggers it,
  expected vs actual behaviour, and why it matters to someone using the site.
- Trace the code yourself; read callers with grep before claiming an assumption is broken.
  You may run small Python snippets (`python3 -c "import landfinder as lf; ..."`) to check
  a parser or helper on a concrete input. Never run a scrape, open a browser, or hit the network.
- If you find nothing real, return an empty list. An empty list is a good result.
- Do not edit any files.

Landfinder-specific things worth checking:
- Number parsing: thousands vs decimal separators across locales (1.250,00 / 1,250.00 /
  1 250 / ¥ / 万), currency symbols, ranges ("$5,000 - $10,000"), "per m²", "/mo", "down".
- Area parsing and unit conversion (acres, ha, m², sq ft, sotka, tsubo, 坪), zero/None areas.
- FX: missing currency in rates, rates as units-per-USD (division vs multiplication).
- Flags: a suspect price that ends up "believable", or a believable one that gets hidden.
- De-duplication and resume (Store): records lost, duplicated, or overwritten on resume.
- Output building: CSV/JSON/HTML escaping, None handling, sorting with missing values,
  XSS in titles that reach site_template.html or report_template.html.

Output: a JSON array and nothing else, each item:
{"file": "...", "line": N, "function": "...", "category": "...", "trigger": "...",
 "expected": "...", "actual": "...", "impact": "...", "confidence": "high|medium|low"}
