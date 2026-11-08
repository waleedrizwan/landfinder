You are verifying one suspected bug in landfinder. You did not find it, and you should be
sceptical: most suspected bugs turn out to be wrong.

Suspected bug:
{FINDING}

Do this:
1. Read the code involved and its callers. Decide whether the trigger can actually happen
   with real scraped data (not only with a contrived call nothing in the code makes).
2. Write a minimal pytest test in tests/test_bughunt_{ID}.py that FAILS on the current code
   because of this bug, and would pass once it is fixed. Use the existing fixtures in
   tests/conftest.py (rates, workdir, FakeStore). No network, no browser.
3. Run it: python3 -m pytest tests/test_bughunt_{ID}.py -q
4. Do not modify landfinder.py or any existing file.

Answer with JSON only:
{"id": "{ID}", "verdict": "confirmed" | "not_reproducible" | "not_a_bug",
 "test_file": "tests/test_bughunt_{ID}.py" or null,
 "reason": "one or two sentences: what the test shows, or why it isn't a bug"}

If the verdict is not "confirmed", delete the test file you wrote.
