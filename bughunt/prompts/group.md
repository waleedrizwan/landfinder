You are de-duplicating bug reports for landfinder. Several hunters ran in parallel, so the
same underlying bug is often reported many times with different wording and examples.

Root causes already in the ledger (from earlier rounds):
{EXISTING}

New findings:
{FINDINGS}

Group the new findings by root cause: two findings share a root cause when one code change
would fix both (same regex, same missing check, same wrong branch). Different symptoms of one
defect are one group; one symptom in two different functions is two groups.

If a new finding is the same as an existing root cause above, put it in a group whose
"existing" field names that root cause; it won't be verified or fixed again.

You may read the code to decide. Do not edit any files.

Answer with JSON only:
{"groups": [{"root_cause": "short description of the defect", "existing": null or "<existing root cause>",
             "ids": ["B041", "B045"], "representative": "B041"}]}
Every new finding id must appear in exactly one group. The representative is the finding with
the clearest, most realistic trigger.
