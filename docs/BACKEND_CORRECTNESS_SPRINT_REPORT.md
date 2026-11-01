# Backend Correctness Sprint — Production Readiness Report

Two correctness issues, both closed. Every number below was measured against the
running pipeline with real LLM calls; nothing is estimated.

**Verdict: both tasks complete and validated. Ready to continue development.**

---

## Task 1 — Unmeasured scoring

### The defect, precisely

`0.0` was the spelling of two opposite facts.

A9 finding four new security issues produces `security_score = 0.0` — a real
measurement of a bad outcome. A9 never running leaves `state.security_result`
empty, and every consumer read `security.get("security_score", 0.0)` — also
`0.0`. The code could not tell them apart, so:

- a run that **skipped** the security re-scan was reported as having **failed**
  it (`Security 0%`, red ring);
- that fabricated zero was averaged into the trust score with a fixed
  denominator of four, so a run with two measured axes reported half its true
  score;
- `hard_draft_reason` printed `"Low axis scores: security=0"` — an accusation
  about code for a check that never ran.

### Every site found and fixed

| Site | Was | Now |
|---|---|---|
| `a10_mci_scorer.py:33-34` | `.get("correctness_score", 0.0)` / `.get("security_score", 0.0)` | no default — absence stays `None` |
| `a10_routing.py:44,46` | `(x or 0.0) < THRESHOLD` | `meets_threshold(x, …)` — absence never satisfies a gate |
| `a10_routing.py` hard-draft | `axis.security < 80` → "security=0" | `below_threshold` for measured; a separate "Not measured: …" note for absent |
| `a10_routing.py` route | `if val < SCORE_THRESHOLD` over all four | measured-low and unmeasured reported separately |
| `models/pr.py` `AxisScores` | `float = 0.0` ×4 | `Score = None` ×4, plus a `trust` property |
| `models/validation.py` | `correctness_score: float = 0.0`, `security_score: float = 0.0` | both `float \| None = None` |
| `a8_mutation_validator.py` | scored correctness with **zero patches** | `has_patch` gate — nothing to be correct about |
| `ui_projection._trust_score` | `sum(4 axes) / 4` with `_pct` zeros | `measured_mean` — denominator is the measurements |
| `ui_projection._tone` | `None` → `"bad"` (red) | `None` → `"unknown"` |
| `ui_projection` metrics/fields | `_pct(...)` → `0` | `_score()` / `_score_text()` → `null` / `"Not measured"` |
| Frontend `TrustMetric.value` | `number` | `number \| null`; `ProgressRing` draws no arc, shows `—` |
| Frontend `trustScore` | `number` | `number \| null`; "No axis was measured…" instead of "below threshold" |

New module `backend/services/measurement.py` holds the semantics:

```
measured    a number the pipeline computed. A measured 0.0 is a real, bad result
            and keeps participating in scoring.
unmeasured  None. Never coerced, averaged, or compared to a threshold.
failed      a kind of measured — it has a number and stays in the arithmetic.
```

The asymmetry that matters, and is tested explicitly: `meets_threshold(None)` is
`False` **and** `below_threshold(None)` is `False`. An unmeasured axis neither
clears a bar nor fails one. Getting only the first right is what the old
`or 0.0` did — correct verdict, fabricated number, wrong message.

### Verified arithmetic

```
measured_mean([100, 80, None, None]) == 90.0     (old behaviour: 45.0)
measured_mean([100, 0.0])            == 50.0     measured zeros still count
measured_mean([None, None])          is None
```

---

## Task 2 — Environment precheck wired

### Placement

The probe **executes immediately after clone** (`prepare_repo →
environment_precheck`). The **gate** sits at reproduction
(`layer1_fan_in → after_environment → reproduction_gate | halt_environment`).

That split is deliberate. A1/A2/A3 read source and need no installed
dependencies, so they produce genuine findings on an unprepared repository —
the semantic graph, CVE reachability, static analysis. What stops is everything
that must *execute* code. Discarding the static intelligence would have made the
failure state less informative than it needs to be.

### What was built

- `services/environment_probe.py` — manifest detection + runner/import probing
- `models/environment.py` — `EnvironmentReport`, `DetectedManifest`
- `agents/a0_7_environment.py` — A0.7
- `orchestrator/edges.after_environment` — the gate
- `orchestrator/nodes.halt_environment` — terminal `blocked` state
- `RunStatus` gains `"blocked"`; `RunLifecycleType` gains `"run.blocked"`
- `RunStateModel.environment` **and** `RunState.environment` (T4: two places or
  it is silently dropped at the graph boundary)
- Frontend: `TerminalState.kind` gains `"blocked"`, `EnvironmentBlockedPanel`

### Guarantees held

- **Never installs anything.** `suggested_command` is a string published for a
  human. Installing from a cloned repository runs that repository's build hooks
  on the host, and the pipeline's subprocesses are not sandboxed.
- **A failing probe never blocks.** `after_environment` stops only on an
  explicit `blocking: true`. A probe that errors, or a disabled precheck, leaves
  the pipeline exactly as it was — a diagnostic must not become a new failure
  mode.
- **Skipped in `stub_mode`**, following the convention every other agent uses
  for real tooling (A1, A3, A4, A6, A7). Gating there would block stubbed runs
  on the host's environment rather than the repository's.
- **`blocked` is not `failed`.** Nothing failed; the pipeline declined to
  continue. The runner preserves the status (it previously coerced everything to
  `completed`), and the UI uses the retry tint, not the failure tint.

---

## Two defects that only final validation could find

Both were found by running the real pipeline, not by reading code.

### 1. An importable pytest is not sufficient

The probe returned `ready` the moment `pytest` imported. A fixture declaring
`fastapi` and `redis` with neither installed **sailed through the precheck and
ran the whole pipeline**, because the host happened to have pytest — precisely
the case the precheck exists to catch.

Fixed: the repository's own imports are now sampled and checked too, whether or
not the runner is present. The `reason` also names the real problem — claiming
"pytest is not importable" when pytest is fine would send the reader after the
wrong thing.

### 2. The report never reached the client

`environment` was stored on the run state but absent from
`build_workspace_header`, so the client knew a run had stopped and not why —
the exact state this sprint set out to remove. Now published verbatim.

Both are covered by regression tests.

---

## Final validation

Five scenarios, real pipeline, real LLM.

| Scenario | Status | Environment | Trust | Axes |
|---|---|---|---|---|
| missing dependencies | `blocked` | `not_prepared` | `null` | all `null` |
| no manifest | `blocked` | `no_manifest` | `null` | all `null` |
| unsupported language (rust) | `blocked` | `unsupported` | `null` | all `null` |
| prepared repo, no tests | `completed` | `ready` | `0.80` | 2 measured, 2 `null` |
| prepared repo with a bug | `completed` | `ready` | `0.86` | 4 measured |

**19/19 assertions passed**, including:

- no downstream agent ran on any blocked run (A3.5, A4, A5, A5.5, A6–A10)
- every blocked run published a `reason`
- no blocked run produced a trust score
- no unmeasured axis rendered as `0`
- for every completed run, `trustScore == mean(measured axes)` — verified
  arithmetically against the published axes, not asserted

Note the fourth row: "no tests" measured only fidelity and scope safety and
scored `0.80` on those two. Under the old arithmetic that run would have
reported `0.40`, penalised by half for two measurements nobody took.

### Regression

| Check | Result |
|---|---|
| Backend suite | **1888 passed**, 1 skipped, 1 failed |
| Frontend `tsc` + build | clean |
| Browser sweep | 28 page loads, **0 crashes**, 0 uncaught errors |
| Blocked-run UI | panel renders, `pip install …` shown, no fabricated `0%` |
| Live websocket | `run.started` → `run.blocked`, **0 duplicate sequences** |
| Replay | resets 41 → 1, refills at presentation cadence, 0 errors |

The single failure is `test_reproduction_command_stable_10_of_10`, which asserts
"vulnapi must be a git repo with HEAD". It **pre-exists this sprint** — verified
by stashing every change and watching it still fail — and is an environmental
precondition of this checkout, not a code defect.

Two existing tests were updated rather than worked around, because both encoded
the bug being fixed:

- `test_run_report_survives_an_empty_run` asserted `trustScore == 0.0` for a run
  that measured nothing. It now asserts `None`.
- `test_empty_patch_bundle_is_unavailable_not_scored` gained an assertion that
  correctness is `None` too — mutation was already honest there, correctness
  was not.

---

## Known limitations

- **Import sampling is bounded** — 25 files, 8 missing modules. A repository
  whose only missing dependency appears in the 26th file will pass the precheck
  and fail at reproduction. Deliberate: the probe runs on every run, and the
  goal is a representative answer, not an exhaustive one.
- **Python only.** Other languages are detected so the product can name what it
  is declining, but only Python resolves to a drivable test runner.
- **The probe uses `python` from PATH**, exactly as A3.5 does. That is the
  point — it predicts A3.5's behaviour — but it means the verdict describes the
  interpreter the *backend* was launched with.
- **Blocked runs still write a proof bundle of nothing.** Not addressed; A10
  never runs, so no PR decision is recorded, but the run's stored state carries
  empty artifacts from the agents that did run.

## Recommendations

1. The `frontend/.git` question (T9 in `CLAUDE.md`) is still open and will
   bite on the next commit.
2. `QueryBoundary` adoption remains partial (6 of 22 components); the two
   shared-query surfaces cover the stage views.
3. Sandboxing subprocess execution remains the blocking issue for hosting,
   ahead of everything else — and it is a prerequisite for ever running
   `suggested_command` automatically.
