Fix one confirmed bug in landfinder.

Root cause: {ROOT_CAUSE}

Reports of it (several hunters may have found different symptoms of the same defect):
{FINDINGS}

Failing tests that demonstrate it: {TEST_FILES}

Rules:
- Fix the root cause with the smallest change that makes all of those tests pass. Do not
  refactor or tidy nearby code.
- Do not weaken, skip or edit any test, and do not change other tests.
- Then run the whole non-browser suite: python3 -m pytest -m "not e2e" -q
  Everything must pass. If your fix breaks an existing test, that test is telling you about a
  case your fix gets wrong (not that the test is outdated): rethink the fix.
- If you can't fix it within the rules, revert your changes and say why.

Answer with JSON only:
{"status": "fixed" | "gave_up", "summary": "one sentence on the change",
 "files_changed": [...]}
