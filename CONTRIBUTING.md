# Contributing

Thanks for helping. Bug reports, new listing sources and fixes to misread prices are all welcome.

## Setting up

```sh
./test.sh -m "not e2e"   # creates .venv, installs requirements-dev.txt, runs the fast tests
./test.sh                # everything, including the browser tests (needs: .venv/bin/playwright install chromium)
```

Everything lives in `landfinder.py`; the README's [How it works](README.md#%EF%B8%8F-how-it-works) section is the map.

## Making a change

- **Keep the tests offline.** Parsers are tested against trimmed copies of real pages in `tests/fixtures/` (with
  contact details removed). Never add a test that fetches a live site.
- **A bug fix comes with a regression test** in `tests/regressions/`, named `test_<id>_<what_broke>.py`.
- **A new source** follows [Adding a source](README.md#adding-a-source) and comes with a parser test and fixture.
- **Be polite to the sites.** Keep the default delays between page loads and don't raise the parallelism defaults.
- Run `./test.sh` before opening a pull request; CI runs the same suite on Python 3.9 and 3.12.

## Commit messages

Start with the area it touches, then say what changed in plain words, e.g. `Site: the currency picker moves into
the filter bar` or `Kijiji: a block page no longer ends the search`.
