# Environment Precheck — design

Design record for the A0.7 environment precheck and the `blocked` terminal
state. **Current and active** — the precheck it describes is implemented in
`backend/agents/a0_7_environment.py` and its result is rendered by the V1
workspace's `environment` agent card.

---

## 1. The observed problem

Point ProoFix at a public GitHub repository and the run ends in about eight
seconds having done nothing. Measured on a real run (`click`, 40 events, 8.1s):

```
+1.0s  A3.5  Reproduction infrastructure error
+7.1s  A5    Blast scope: 0 files auto-patchable
+7.1s  A5.5  Context: 0 function(s) from 0 file(s)
+8.0s  A6    Fix plan: 0 steps
+8.0s  A7    Generated 0 patches from 0 plans     ← ×4, once per retry
+8.0s  A8    validation on an empty patch set
```

The cause is not a bug in any agent. A freshly cloned repository has no
installed dependencies, so `python -m pytest` cannot run, and A3.5 correctly
classifies the outcome `INFRA_ERROR`. Everything downstream then executes
faithfully on empty input.

Two distinct failures come out of that, and they need separating:

**(a) The pipeline does work it knows is futile.** `graph.py:115` is
unconditional:

```python
graph.add_edge("reproduction_gate", "investigate")
```

So investigation, blast analysis, context engineering, planning, patch
generation and validation all run — including the full retry loop — on a bug
that was never reproduced. `CLAUDE.md` already records this ("The reproduction
gate is not a gate"), and the cost is real: A4 and A7 are the two most expensive
LLM calls in the system, and both are spent here.

**(b) The UI cannot explain why.** Seven stages report completion having
produced nothing. Each individual statement is true — the blast scope really is
0 files — but the composite reads as "ProoFix analysed your repository and found
nothing wrong," when the truth is "ProoFix could not run your test suite." Those
are opposite conclusions and the product currently shows the reassuring one.

The failure is also announced in the wrong vocabulary. What the user sees today
is a root-cause narrative built around
`/opt/homebrew/opt/python@3.14/bin/python3.14: No module named pytest` — the
pipeline's own environment leaking into a field meant for the *repository's*
defect, with a host path in a citation.

---

## 2. Should ProoFix detect manifests before reproduction?

**Yes, but manifest detection alone is the wrong gate.**

A manifest tells you what the project *declares*, not whether the environment
can *run* it. `click` has a `pyproject.toml`; the run still failed, because
having a manifest and having an importable `pytest` are unrelated facts. Gating
on "is there a `requirements.txt`" would pass exactly the runs that fail today.

So the precheck answers two questions, in order:

1. **What kind of project is this?** — manifest and language detection, so the
   product can say "this is a Rust project and ProoFix only repairs Python"
   rather than failing obscurely inside pytest.
2. **Can its tests actually execute here?** — the operative gate. For Python
   that is: does the interpreter that will run the suite have `pytest`
   importable, and do the project's own top-level imports resolve?

Question 2 is cheap — one subprocess, no network — and it is the one that
predicts the failure.

---

## 3. What the precheck produces

A new deterministic service, `backend/services/environment_probe.py`, plus a
model. No LLM call, consistent with A5.5's rule.

```python
class DetectedManifest(BaseModel):
    path: str                    # repo-relative
    kind: str                    # "requirements.txt" | "pyproject.toml" | ...
    language: str                # "python" | "node" | "rust" | "go" | ...

class EnvironmentReport(BaseModel):
    status: Literal[
        "ready",             # tests can run
        "not_prepared",      # recognised project, dependencies not installed
        "no_test_runner",    # recognised project, no runner ProoFix can drive
        "unsupported",       # language ProoFix cannot repair
        "no_manifest",       # nothing declaring dependencies at all
    ]
    language: str | None
    manifests: list[DetectedManifest]
    test_runner: str | None            # "pytest"
    test_runner_available: bool
    missing_imports: list[str]         # sampled from the project's own imports
    reason: str                        # the backend's own words, user-facing
    suggested_command: str | None      # SUGGESTED. Never executed.
    blocking: bool
```

`suggested_command` is a string the UI displays for the user to run themselves —
`pip install -e ".[dev]"`, `pip install -r requirements.txt`. Per the QA brief,
**automatic installation is explicitly out of scope**, and the field is named so
that shipping it later is a deliberate act rather than a slip.

Detection covers, in priority order: `pyproject.toml`, `requirements*.txt`,
`Pipfile`, `setup.py`, `uv.lock`, `poetry.lock` (python); `package.json`
(node); `Cargo.toml` (rust); `go.mod` (go); `Gemfile` (ruby); `pom.xml`,
`build.gradle` (jvm). Only Python resolves to a drivable `test_runner` today —
the rest exist so the product can name the language it is declining, which is
information the user can act on.

---

## 4. Where it goes in the graph

Between repository preparation and reproduction:

```
prepare_repo → index_repository → parallel_intel → layer1_fan_in
                                                        ↓
                                              environment_precheck        ← new
                                                        ↓
                                    ┌───────────────────┴────────┐
                          blocking  │                            │ ready
                                    ▼                            ▼
                            halt_environment              reproduction_gate
                                    │                            ↓
                                   END                      investigate
```

Placing it after `layer1_fan_in` rather than before `parallel_intel` is
deliberate. A1/A2/A3 are static — they read source, they do not execute it — so
they produce genuinely useful output (the semantic graph, CVE reachability,
static findings) even in an unprepared environment. A repository whose
dependencies are not installed can still be usefully *understood*, and throwing
that away would make the failure state less informative than it needs to be.

What stops is everything that depends on **executing** the code: reproduction,
and every agent downstream of it.

### The graceful stop

`halt_environment` sets a terminal state and ends the run. It does not raise,
and it is not `failed` — the pipeline worked correctly; the target was not
runnable. That distinction should survive into the run status, so a new terminal
state is warranted:

```
status:        "blocked"
decisionLabel: "Environment not prepared"
```

Downstream stages are marked `skipped` with the precheck's reason. **The
frontend already supports this** — `BackendStageStatus` includes `"skipped"`,
and `ui_projection` already renders skipped stages — so the UI cost of the
honest path is near zero.

---

## 5. UX

The rule from §8 of the QA brief applies: never a blank panel, never a fake
number, always a reason.

**Stage rail.** Repository Understanding completes normally. Investigation
onward render `Skipped`, each carrying the precheck's reason rather than a
generic label. A user scanning the rail sees immediately that the run stopped by
decision, not by accident.

**A dedicated stage panel** where reproduction's evidence would be:

```
┌────────────────────────────────────────────────────────────┐
│  ⚠  Environment not prepared                               │
│                                                            │
│  ProoFix could not run this repository's test suite, so    │
│  no failure could be reproduced and no repair was          │
│  attempted.                                                │
│                                                            │
│  Detected      Python · pyproject.toml, requirements.txt   │
│  Test runner   pytest — not importable                     │
│  Missing       pytest, click, pallets_sphinx_themes        │
│                                                            │
│  To prepare this repository:                               │
│    pip install -e ".[dev]"                                 │
│                                                            │
│  ProoFix does not install dependencies. Prepare the        │
│  environment, then start a new run.                        │
└────────────────────────────────────────────────────────────┘
```

Every line is a field on `EnvironmentReport`. Nothing is templated prose with a
value slotted in, per the convention already used for root-cause summaries.

**The final report.** Instead of four axis scores that are all zero — which
currently read as "your code scored 0 for security" when nothing was measured —
the report states the run was blocked, shows what the static agents *did* learn
(semantic graph, CVE reachability, static findings), and shows the axes as
`Not measured`. This is the same distinction the mutation score fix made earlier
in this sprint: an absent measurement must never render as a measured zero.

**Not a dead end** (§4). The panel offers: copy the suggested command, re-run
once prepared, and view what the static analysis did find.

---

## 6. What this changes, and why each change is necessary

Per the sprint rule on backend changes:

| Change | Why it is necessary | Risk |
|---|---|---|
| New `services/environment_probe.py` | Nothing today can answer "can this repo's tests run" before trying | None — additive, no caller until wired |
| New `models/environment.py` | The report needs a typed shape like every other artifact | None — additive |
| New `RunStateModel.environment: dict \| None` | Every artifact crosses the graph as a dict (T4); this follows the existing pattern | Low — defaulted, so stored states still deserialise. **Must also be added to the `RunState` TypedDict** or it is silently dropped (T4) |
| New `environment_precheck` node + conditional edge | This is the actual fix. Without it the pipeline still burns two LLM calls per unprepared repo | **Medium — this changes run outcomes.** Runs that previously produced a draft PR from an unreproduced bug will now stop. That is the intent, but it is a behaviour change and deserves a flag |
| New terminal status `blocked` | `failed` would be wrong — nothing failed. Reusing `completed` would be a lie | Low, but every `status` consumer must handle it: `ui_projection`, the V2 `RunHeader`, trust gating |

**Recommendation: ship it behind `settings.environment_precheck_enabled`,
defaulting off**, matching how A5.5 was introduced (shadow → opt-in → default).
In shadow mode the precheck runs and publishes its report without gating, so its
accuracy can be measured against real runs before it is allowed to stop one.
That answers the obvious risk — a precheck that wrongly reports "not prepared"
would block runs that would have succeeded — with evidence rather than
confidence.

---

## 7. What I would not do

- **Do not auto-install.** Out of scope by instruction, and rightly: running
  `pip install` against an arbitrary cloned repository executes that
  repository's `setup.py` on the host. The pipeline already runs unsandboxed
  subprocesses (§7 of `CLAUDE.md` flags this as the blocking security issue for
  hosting); adding dependency installation would widen that hole considerably.
  It should land after sandboxing, not before.

- **Do not gate on manifest presence alone.** Explained in §2 — it would pass
  precisely the runs that fail.

- **Do not make the precheck an LLM call.** It is a file-existence check and one
  subprocess. Same rule as A5.5.
