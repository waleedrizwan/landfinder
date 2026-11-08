Fix one confirmed bug in landfinder.

Bug:
{FINDING}

A failing test demonstrates it: {TEST_FILE}

Rules:
- Make the smallest change that makes that test pass. Do not refactor or tidy nearby code.
- Do not weaken, skip or edit the test, and do not change other tests.
- Then run the whole non-browser suite: python3 -m pytest -m "not e2e" -q
  Everything must pass. If your fix breaks another test, rethink the fix.
- If you can't fix it within the rules, revert your changes and say why.

Answer with JSON only:
{"id": "{ID}", "status": "fixed" | "gave_up", "summary": "one sentence on the change",
 "files_changed": [...]}
