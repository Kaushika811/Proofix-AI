# Controlled claim-verification benchmark

Run from the repository root:

```bash
PYTHONPATH=. .venv/bin/python benchmarks/verification/benchmark.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests/integration/test_verification_benchmark.py
```

This offline benchmark seeds eight isolated Python cases and passes scripted
root-cause text through the real A4 investigator. The source locator and claim
checker are measured separately. Where execution is available, the runner uses
pytest and its JSON report for the unchanged issue acceptance test. It makes no
LLM call and uses no external repository or service.

| Case | Source located | Claim outcome | Why |
| --- | --- | --- | --- |
| Literal return matching observed failure | Yes | Supported | Simple source return, pinned assertion and pytest value agree |
| False return claim at a valid citation | Yes | Contradicted | Observed value differs from the claim |
| Citation to another function | Yes | Incomplete | The issue test does not exercise that function |
| Source/runtime conflict | Yes | Incomplete | A measured result from an earlier source version differs from the current source |
| Execution unavailable | Yes | Unavailable | There is no behavioral observation |
| No independent acceptance test | Yes | Incomplete | Source alone is insufficient |
| Unstructured false side-effect claim | Yes | Incomplete | The checker cannot prove or disprove the claim |
| Missing source | No | Incomplete | The cited source could not be located |

[results.json](results.json) records each classification, source-location
method, investigation route, draft reasons and report confirmation flag. On
this seeded set, source-location decisions were **8/8** and claim outcomes
were **8/8** against the reviewed labels: one supported, one contradicted,
five incomplete and one unavailable. No false claim was presented as confirmed.
The contradicted claim stopped after the benchmark's bounded reinvestigation;
concrete evidence gaps also stopped. One claim outside the supported grammar
could still reach patch planning **as an unconfirmed hypothesis**, with a draft
review gate. It was not presented as a confirmed diagnosis.

The supported result covers only an undecorated top-level function returning a
primitive literal, an imported one-assertion acceptance test tied to the issue,
and an observed `AssertionError` in pytest's JSON report. The classifier does
not establish semantic truth for branches, side effects, stateful behavior,
mocked tests, arbitrary natural-language explanations, or independent real
repositories. Those capabilities remain unmeasured; unsupported claims remain
incomplete. A citation's matching file, line or symbol never grants support by
itself. The benchmark's scripted explanations are controlled inputs, not a
live-model quality measure.
