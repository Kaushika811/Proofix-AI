# Controlled recovery benchmark

Run from the repository root:

```bash
PYTHONPATH=. .venv/bin/python benchmarks/recovery/benchmark.py
PYTHONPATH=. .venv/bin/python -m pytest -q tests/integration/test_recovery_benchmark.py
```

The runner creates a temporary Python repository and an unchanged acceptance
test for an expiry bug. Its original code fails the test by assertion in every
case. Scripted patch proposals then enter the **compiled ProoFix LangGraph**.
A8 runs the real scoped pytest validation and generated-test integrity checks.
The graph records each attempt in Redis-backed run state and follows the actual
conditional edges. The runner checks the issue contract, test source, and
persisted attempt history before deleting the temporary repository.

The results in [results.json](results.json) are from the controlled run on
2026-10-20. They are routing and safety evidence, **not a live-model quality
score**.

| Injected case | First A8 classification | Observed route | Outcome |
| --- | --- | --- | --- |
| Incorrect implementation | implementation failure | A7 patch revision | Second patch passed the target test |
| Wrong patch file | wrong target | A5 target resolution → A6 planning → A7 | Second patch passed the target test |
| Contradicted diagnosis | incorrect diagnosis | A4 investigation → A5/A6/A7 | Second patch passed the target test |
| Patch introduces missing import | environment failure | Safe stop | Blocked after one validation; import error was not behavioral evidence |
| Evidence remains incomplete | insufficient evidence | A4 investigation → safe stop | Blocked without a second patch |
| Identical failed patch proposed | implementation failure | A7 duplicate guard → safe stop | Blocked; second proposal recorded as not executed |
| Retry budget exhausted | implementation failure twice | One A7 revision → safe stop | Blocked after two distinct failed patches |

The insufficient-evidence scenario begins with enough scripted evidence to
reach its first patch. It injects a new evidence gap at A8, then confirms that
reinvestigation stops before another patch. An investigation already marked
incomplete now stops before initial patch planning.

Across all seven cases, the original acceptance test failed by assertion, its
source and the issue contract remained unchanged, and the final attempt history
matched the persisted run state. The three successful routes ended with the
same test passing. The environment case first reproduced the behavioral failure;
the patch then introduced `ModuleNotFoundError`, which A8 classified as an
execution failure.

This benchmark scripts A0–A7 proposals and A9–A10 outcomes so it can trigger
each route deterministically without an LLM, a network service, or a GitHub PR.
The wrong-target patch is deliberately injected past A7's normal target guard.
Mutation testing is marked unavailable rather than scored. These results do
not measure whether live agents choose the right target, diagnosis, or patch on
an independent repository, or whether a human would accept the final repair.

## Actual-agent offline preflight

Run `PYTHONPATH=. .venv/bin/python benchmarks/recovery/agent_preflight.py` to
exercise the complete graph with its actual agent implementations on three
isolated, calibrated cases: the existing expired-token fixture, a new
pagination off-by-one defect, and a new percentage-discount defect. Each
temporary repository contains only the target source, a behavioral test, and a
minimal `requirements.txt` naming `pytest` so the real environment gate can
verify collection.
The two new cases are seeded fixtures, **not independent real-world defects**.
Before starting the graph, the preflight checks the unchanged test against the
original source, a reviewed correct fix, and a deliberately incorrect patch.
Original and incorrect versions must fail by assertion; the correct version
must pass. This is separate from the scripted routing benchmark. It uses
`stub_mode=True`, so it makes no model request or GitHub PR. Its compact,
source-free result is in
[agent-preflight.json](agent-preflight.json).

All three cases reproduced and resolved the expected source file, then blocked
after one attempt with no patch. A7's stub deliberately returns the original
file unchanged; this result does **not** measure patch quality or
evidence-driven recovery by a live model. Independent real-world defects,
semantic diagnosis quality, correct repair rate, and recovery after a real
failed patch remain unmeasured.

The prepared [live_agent_probe.py](live_agent_probe.py) selects only the
expired-token case, disables GitHub publishing, learned prompt context, and
raw-prompt storage, and records semantic review as pending. It requires
**separate, explicit authorization** for repair-agent calls to Mistral. An
initial attempted run, preserved as
[live-agent-probe-before-manifest.json](live-agent-probe-before-manifest.json),
stopped at A0.7 because the isolated repository lacked a dependency manifest;
an A1 model call failed, and reproduction, patch generation, and recovery were
not measured. The fixture now passes A0.7 and confirms reproduction in a local
no-model check. No further live call was made after automatic approval review
rejected broader Mistral processing under the earlier generator-only
authorization.

Run `PYTHONPATH=. .venv/bin/python benchmarks/recovery/prompt_preflight.py`
to inspect the **actual security-approved prompt boundary** without sending
anything to a provider. The gateway's approval/sanitization runs, then the
preflight stops before transport. [prompt-preflight.json](prompt-preflight.json)
stores only hashes and presence checks, never prompt text. On 2026-10-20 it
captured four approved prompts, from A1, A4, A6, and A7. The local scanner
found no secrets or host paths in those approved payloads. A4 included the
issue and selected test references; A7 included the complete target source.
No approved prompt contained the complete test or named `requirements.txt`.
These checks establish the observed boundary for this one isolated fixture;
they do not prove the prompts' diagnoses are correct or establish safety for
other repositories. They also do not authorize a model call.

The previous Mistral authorization covers only the regression-test generator
benchmark and its reviewed `auth.py`, `middleware.py`, `api.py`, and
`test_auth.py` snippets. A live A4–A8 evaluation is a different data transfer:
the current agents construct prompts from reproduction traces, findings,
evidence references, context packages, and complete target files. Do not run
that evaluation under the generator authorization. First select and review an
independent local defect set, inspect the exact prompts and minimize them to
approved source/test snippets, then obtain separate authorization for those
specific A4–A8 model calls. Keep raw prompts and secrets out of result files;
record only case IDs, hashes, classifications, routes, and test outcomes.
