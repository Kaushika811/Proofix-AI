# Regression-test generator benchmark

This benchmark uses three executable, deliberately seeded defects from the
repository's `vulnapi` fixture: expired-token acceptance, an admin-mode auth
bypass, and SQL injection in user lookup. Each case has a reviewed correct fix,
an incorrect patch, and an unrelated patch. The runner executes the **same
unchanged candidate test** against all four source versions in isolated copies.
The SQL case also removes the database to check whether an environment failure
is mistaken for a reproduced bug.

Run the local calibration without a model or network call:

```bash
PYTHONPATH=. .venv/bin/python benchmarks/generator/benchmark.py --mode calibration
```

The complete outcomes and candidate test source are in [calibration.json](calibration.json).
For this calibration, all three controlled candidate tests failed by assertion
on the original code, passed after the correct fix, and still failed after the
incorrect and unrelated patches. The absent database produced one execution
failure and was **not** counted as a behavioral assertion. A vacuous test that
only checked an unrelated malformed token passed on the original code; the
baseline gate correctly left it unconfirmed. A deliberately ambiguous request
now returns `needs_clarification` without a model call: **1/1 abstained**.

These are **harness calibration results, not a score for the live model**. The
three tests were controlled, reviewed inputs to the generator's validation
path. `behavioral_test_validity` means that the candidate checked the stated
behavior, failed by assertion before repair, and passed after the reviewed fix.
For model-produced tests, semantic validity still requires a separate human
review; the runner leaves that field unscored. A passing test alone cannot
establish that the user's expected behavior was specified correctly.

The live mode uses the configured Mistral service:

```bash
PYTHONPATH=. .venv/bin/python benchmarks/generator/benchmark.py --mode live
```

Under the user's limited benchmark authorization, the runner checks each exact
prompt context before a model call. Only `vulnapi/auth.py`,
`vulnapi/middleware.py`, or `vulnapi/api.py` may be sent as source; the auth
cases may also include `tests/test_auth.py` as a test convention. A detected
secret, disallowed file, or missing target source skips that case. The runner
does not send the repository, environment files, or credentials. Generated
tests and patch variants execute in local temporary copies.

The original live run returned four candidates, all rejected before pytest.
Each imported `unittest.mock.patch`, and some used undefined helpers or mocked
away the reported behavior. The generator now asks for direct API calls and
all explicitly expected outcomes, checks undefined names and placeholder
literals before execution, and makes at most one regeneration call with all
exact static errors. It records both candidates and their errors. The original
validator's prohibited imports, patching, and side-effect checks are unchanged.

The latest live run on 2026-10-20 is in [live.json](live.json). The preceding
samples are preserved in [live-before-token-guard.json](live-before-token-guard.json)
and [live-prior.json](live-prior.json). Both preceding samples accepted two of
three candidates; the newest accepts all three. The generator now identifies
helpers actually defined in the supplied test convention, so the expired-token
candidate can explicitly import `_make_token` from `tests.test_auth`. A separate
pre-execution check requires an explicit `is not None` assertion before a
returned API value is indexed. The unchanged validator still rejects a second
test function, prohibited imports, and patching. Helper importability in other
repositories remains unverified until execution.

| Measure | Live result |
| --- | --- |
| Clear candidates accepted by pre-execution checks | 3/3 |
| Execution behavioral validity: original assertion failure and correct-fix pass | 3/3 executed |
| Semantic validity | Unmeasured; `pending_human_review` for all three |
| Correct-fix detection | 3/3 executed |
| Incorrect-patch rejection by assertion | 3/3 executed |
| Unrelated-patch rejection by assertion | 3/3 executed |
| Missing-database failure | 1 `sqlite3.OperationalError`, not counted as behavior |
| Vacuous-test detection | 1/1 controlled gate probe; baseline passed, so unconfirmed |
| Ambiguity handling | 1/1; `needs_clarification` before any model call |

In the latest sample, the SQL test first asserts its valid-input result is not
`None`, then checks its `name`. The wrong patch fails at that assertion instead
of raising `TypeError`. The immediately preceding sample did raise `TypeError`
and counted only 1/2 incorrect-patch assertion failures. The latest token test
imports the existing helper, checks expired and unexpired tokens, and fails by
assertion on both wrong and unrelated patches. All three latest candidates were
replayed through the current static checks after a final guard refinement.
Execution outcomes do not substitute for an independent semantic review of
each model-authored assertion. These few seeded defects and varying live
responses do not establish reliability on independent repositories.

The seeded fixture is a useful first regression set. It does not establish
performance on independent repositories or previously unseen bugs.
