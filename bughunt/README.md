# bughunt

A loop that looks for bugs in landfinder, proves each one with a failing test, and fixes it.
Each stage is a fresh `claude -p` session, so the session that found a bug never grades it.

```
hunt ──▶ group ──▶ verify ──▶ fix ──▶ gate
 5 code sections   one per     a failing    one commit   full suite must
 × 5 bug kinds     root cause  test, or     per root     pass; the loop
 run in parallel               it's dropped cause        reruns it itself
```

```sh
git checkout -b bughunt-$(date +%F)              # it refuses to run on main
python3 bughunt/bughunt.py                       # one full round
python3 bughunt/bughunt.py --rounds 3            # stops early after a round with nothing confirmed
python3 bughunt/bughunt.py --chunks outputs --categories security
python3 bughunt/bughunt.py --no-fix              # hunt + verify only, then fix yourself
```

Needs the `claude` CLI, signed in. Nothing is pushed or merged: review the branch, then merge it.

## Files

- `prompts/hunt.md`, `group.md`, `verify.md`, `fix.md`: what each stage is told. Edit these to steer it.
- `chunks.json`: the code sections and bug categories. Add a category to point the next round somewhere new.
- `findings.json`: the ledger. Every finding, its root cause, verdict, test file and fixing commit.
  Hunters are shown the known root causes so later rounds look elsewhere.

## Round 1 (2026-11-08)

25 hunting passes reported 40 findings. 38 were confirmed with a failing test, and came down to
10 root causes. All 10 are fixed on the `bughunt` branch, one commit each, with the 99 existing
tests still passing.

| Root cause | Findings |
|---|---|
| Area parsing: "1/4 acre" read as 4 acres, "2,500 acres … LA" as 2.5, "1.500 m²" as 1.5 m² | 9 |
| Mercado Libre "3 habitaciones" read as 3 ha, Mexican "0.25 ha" as 25 ha; Ieichiba building area taken as land | 9 |
| `</script>` in a scraped title injects HTML into report.html (the public site was already safe) | 5 |
| De-duplication kept a reposted listing's dead URL, and merged untitled or generic-titled parcels | 4 |
| A blocked Kijiji or LandWatch page was recorded as "end of results", skipping that province/state | 3 |
| A failed LandWatch re-check was saved as fresh, so dead listings never aged off the site | 3 |
| Visitor counter returns 500 on a `null` body | 2 |
| "the Metro area" flagged as a per-m² price | 1 |
| A truncated fx.json crashed every later run | 1 |
| A null Zillow `listResults` ended the Zillow thread | 1 |

What round 1 changed in the loop: parallel hunters reported the same defect up to 5 times, so there
is now a `group` stage before verify, and fixes are made per root cause. The fix gate also now leaves
out the still-failing tests of other unfixed bugs (otherwise no fix could ever pass it), and a
verifier's "confirmed" only counts if the loop sees its test fail.

One thing to check on the next real run: the LandWatch fix treats a page with no listings and no
"N Listings" count as blocked. If LandWatch's real last page has no count, `coverage.csv` will say
"fetch failed" for those states instead of "end of results" (one extra page fetch per run, no data lost).
