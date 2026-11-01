# ProoFix — Production Certification

Validation against seven real upstream repositories. No fixtures, no stubs, real
LLM calls, real subprocesses. Every number measured; nothing estimated.

**Status: CONDITIONAL PASS — four defects found and fixed during validation, two
capability areas remain unverified. Phase 5 recommendation is qualified; see §7.**

> **Update.** §7.1 (auto-merge never executed) and §7.2 (A9 rarely executed) were
> subsequently closed by real execution. A fifth defect — a mutmut version
> incompatibility that made auto-merge structurally unreachable — was found and
> fixed in the process. See **Positive Routing Validation** at the end of this
> document, plus §12 for negative-path runs. `diff_only` and a failing security
> score remain verified-at-the-gate but unobserved in live execution.

> **Phase 9 round (2026-08-09).** A fresh certification pass against five real
> public GitHub repositories, covering all three terminal outcomes. **Four new
> defects found, none yet fixed** — one of them severe enough to change the
> recommendation. See **§13**. Until D-1 and D-2 are closed, the status below
> should be read as **FAIL for hosted use on macOS**, not conditional pass.

---

## 1. Repositories under test

| Repository | Size | Language | Result |
|---|---|---|---|
| Flask (prepared) | 236 files, 83 py | python | **completed** — all 12 agents |
| FastAPI | 3,136 files, 1,136 py | python | blocked — `httpx2`, `dirty_equals`, `inline_snapshot` |
| Django | 7,080 files, 2,928 py | python | blocked — `asgiref`, `sqlparse` |
| React | 7,201 files | node | blocked — unsupported language |
| Next.js (monorepo) | 31,097 files | node | blocked — unsupported language |
| Spring Boot | 11,875 files | jvm | blocked — unsupported language |

Flask was deliberately prepared (`pip install -e .`) so at least one repository
would exercise the full agent chain. The rest were left as cloned — which is how
a user's repository actually arrives.

---

## 2. Defects found during this validation

All four were found by running real repositories, not by reading code. All four
are fixed and re-verified.

### 2.1 Environment probe blocked a prepared repository `[critical]`

Flask — fully installed, 483 tests collecting cleanly — was reported
`not_prepared` and blocked. The probe was sampling the repository's imports and
tripping on:

- `_typeshed` — exists only under `TYPE_CHECKING`, never importable at runtime
- `flaskr`, `task_app`, `js_example` — example apps *inside* the repo
- `asgiref`, `celery`, `docutils` — docs extras the test suite never imports

**A false block is the worst failure mode this component has**: it refuses to
analyse a repository that would have worked, and the user has no recourse.

**Fix.** Sampling asked the wrong question. `pytest --collect-only` imports every
test module and its transitive dependencies, so it answers the real one —
authoritatively, with no heuristics. Missing modules are now read from
collection output rather than guessed from source. Exit code 5 ("no tests
collected") is explicitly *not* blocking: the environment is fine, the suite is
empty, and that is A3.5's `NO_TESTS` to report.

Verified after fix: Flask `ready`; FastAPI and Django correctly blocked with
their genuinely-missing packages named.

### 2.2 A firewall rejection failed the entire run `[high]`

Django reached A4 and raised
`SecurityRejection: prompt exposes repository or host internals: host_path`.
The exception propagated out of the LangGraph node and failed a run that had
already completed A0.5, A1, A2, A3 and A3.5 successfully.

A6 and A7 both guard their LLM calls and fall back. **A4 was the only LLM agent
that did not** — and it holds a complete deterministic fallback (`_stub_brief`)
that was never reached.

**Fix.** A4 now degrades to the deterministic brief on any LLM failure, records
the error on the run, and emits a `retry` status so the degradation is visible
rather than silent. Three regression tests cover `SecurityRejection`, generic
failures, and the requirement that the failure is recorded.

### 2.3 A failed run discarded its own progress `[high]`

`PipelineRunner.execute` saved `state` on failure — the snapshot taken *before*
the graph ran. Every node persists as it goes, so this overwrote all of it. A run
that failed at A4 came back reporting no environment report, no SIG, no static
findings and no reproduction, none of which was true.

This is why the failed Django run showed `environment: None` despite the probe
having run and stored its verdict.

**Fix.** The failure path re-reads the latest state and annotates it, rather than
replacing it with a stale object.

### 2.4 Duplicate React key `[low]`

A `key` collision warning appeared once on Flask's repository stage during the
paced drain. It did not reproduce deterministically, so the root cause was not
isolated. The class was eliminated instead: `evidence.fields` and `metrics` are
backend arrays with no uniqueness guarantee on `label`, and both were keyed on
it. Both are now index-suffixed. Three subsequent loads produced no warning.

**Stated plainly:** this one is hardened, not diagnosed.

---

## 3. Verified scenarios

| Capability | Result |
|---|---|
| Environment probe — prepared repo | Flask → `ready`, full pipeline runs |
| Environment probe — missing deps | FastAPI, Django → blocked, packages named, `pip install -e ".[dev]"` published |
| Environment probe — unsupported language | React (node), Next.js (node), Spring Boot (jvm) → blocked, language named |
| Environment probe — no manifest | blocked with reason, no command suggested |
| Blocked state | 20 runs; no downstream agent ran in any |
| Pipeline halt | A3.5/A4/A5/A5.5/A6–A10 never executed after a blocking verdict |
| Every agent | A0.5, A1, A2, A3, A3.5, A4, A5, A5.5, A6, A7, A8, A10 on Flask |
| Retry loop | Flask: 3 retries, attempts distinguishable in the feed |
| Trust score | `0.57` = mean(70, 45) on Flask — measured axes only, verified arithmetically |
| Unmeasured axes | Correctness and Security `null`, never `0` |
| Websocket | `run.started` → `run.blocked` live; **0 duplicate sequences** |
| Replay | Resets 60 → 1, refills progressively, 0 errors |
| Playback | Normal/Presentation switch, persists, notice shown |
| Activity feed | 60 rows, attempt-numbered, filterable |
| Virtualization | DOM node count *falls* after scrolling (2450 → 1994) |
| Graph | Rendered on every stage load; no crashes |
| Error states | 500 on `/agents` → named alert with stage, status, endpoint, retry |
| Accessibility | 200 focusable controls, **0 unlabelled**, 0 positive tabindex, 25/25 focus rings |
| Responsive | 5 viewports × 3 stages, tablet → 2560px ultra-wide: no overflow |
| Reduced motion | 0 running animations, content still renders |
| Browser console | 42 page loads, **0 errors** (excluding 404-as-fact) |
| Backend logs | **0 errors, 0 tracebacks** after fixes |

---

## 4. Performance

42 page loads across six real repositories, Chromium, 1440×900 and 1600×1000.

### Rendering

| Metric | Min | Max | Mean |
|---|---|---|---|
| **FPS** | 60.1 | 60.5 | **60.3** |
| p95 frame | 17.3ms | 18.0ms | — |
| Worst frame | 18.4ms | 33.4ms | — |

The 60 FPS target holds on every repository and every stage, including Django
(43,451 graph nodes) and Next.js (31,097 files). The worst frame — one 33ms
frame, i.e. a single dropped frame — occurred on stage entry, when the lazy
chunk mounts.

### Memory

| Metric | Min | Max | Mean |
|---|---|---|---|
| JS heap | 44.0 MB | 141.8 MB | 59.4 MB |

The 141.8 MB peak is Flask's investigation stage, the only run with a full agent
chain and 60 events. Heap did not grow after scrolling.

### Network (per page load, settled)

| Metric | Min | Max |
|---|---|---|
| API requests | 39 | 85 |
| Transferred | 150 KB | 828 KB |

Against the ~580 requests measured before the previous sprint's fixes, on
comparable pages.

### Load time

| Repository | Repository stage |
|---|---|
| Flask | 1.2 s |
| React / Next.js / Spring Boot | 1.7 s |
| FastAPI | 1.8 s |
| Django | 4.9 s |

Django's 4.9s reflects a 43k-node knowledge graph, not a rendering cost — FPS is
unaffected.

### Backend indexing

| Repository | Files | KG nodes | Build | KG memory |
|---|---|---|---|---|
| Flask | 82 | 1,158 | 90 ms | 0.6 MB |
| FastAPI | 658 | 6,001 | 546 ms | 3.3 MB |
| Django | 2,913 | 43,451 | 2,452 ms | 28.7 MB |
| Next.js | 0 py | 14 | 5,489 ms | 2.7 KB |

Next.js's 5.5s with 14 nodes is the workspace scan walking 31,097 files to
discover no Python — the cost is the filesystem walk, not the graph.

### Virtualization

Node counts **fall** after scrolling to the bottom of every scrollable region:

| Repository | Before | After |
|---|---|---|
| Flask | 2,450 | 1,994 |
| FastAPI | 1,891 | 1,811 |
| Django | 1,925 | 1,851 |
| React | 1,473 | 1,365 |

Windowing is doing real work; rendering does not grow with data.

---

## 5. Browser support

Verified: **Chromium 141** (Playwright), light and dark themes, 834–2560px.

**Not verified: Firefox and Safari.** The codebase uses `color-mix()`,
`@media (prefers-color-scheme)`, `useSyncExternalStore`, CSS nesting and
`pathLength` — all supported in current Firefox and Safari on paper, none
exercised here. `performance.memory` is Chromium-only, so the memory numbers
above do not transfer.

---

## 6. Known limitations

1. **Python only.** Other languages are detected so the product can name what it
   is declining. React, Next.js and Spring Boot are correctly blocked, not
   analysed.
2. **The probe uses `python` from PATH**, exactly as A3.5 does. The verdict
   therefore describes the interpreter the *backend* was launched with. A
   backend started outside a virtualenv will block repositories that would work
   inside one.
3. **Collection is not execution.** `--collect-only` imports test modules; a
   dependency needed only at *runtime* inside a test body will pass the precheck
   and fail at reproduction.
4. **Blocked runs still store partial artifacts** from the agents that did run.
   Harmless, but the run's state is not empty.
5. **Next.js scan cost** is 5.5s of filesystem walk for a repository with no
   Python. Bounded, but it scales with file count, not relevance.
6. **`suggested_command` is never executed** — by design, and it must stay that
   way until subprocesses are sandboxed.

---

## 7. Remaining risks — and why this is a conditional pass

Two of the capabilities requested for testing **could not be verified**, because
no run in a 50-run corpus has ever exercised them.

### 7.1 Merge routing is only partially verified `[HIGH]`

Across every run ever recorded on this backend:

```
Draft PR: 26    Environment not prepared: 20    Failed: 4
auto_mergeable: 0    diff_only: 0
```

**The auto-merge path has never executed on real data.** Its logic is covered by
unit tests — including this sprint's tri-state gate tests — but no real
repository has produced a patch that cleared every gate. What is verified is that
routing correctly *refuses* to auto-merge: on absent measurements, on low scores,
on failed validation. What is unverified is that it correctly *permits* one.

### 7.2 A9 security rescan almost never runs `[MEDIUM]`

A9 executed in **1 of 15 sampled runs**. It is skipped whenever mutation
validation fails, which is the common path. Its scoring, its baseline diff, and
its `security_score` are therefore largely unexercised in production, and the
Security axis is `null` on nearly every run.

### 7.3 Other standing risks

- **Subprocesses are unsandboxed.** pytest, bandit, semgrep, mutmut, ruff and
  now `pytest --collect-only` all execute repository code with the host
  interpreter. This is the blocking issue for hosting, ahead of everything else
  in this document, and it is a prerequisite for ever running
  `suggested_command`.
- **The prompt firewall rejects host paths**, and real repositories contain
  path-like strings. A4 now degrades instead of failing, but the *quality* of
  degraded output is lower and this will recur on other agents.
- **Repository clones are never cleaned up.** Six clones consumed 587 MB during
  this validation alone.
- **Single-process.** `WSBroadcaster` is an in-memory dict and the checkpointer
  is `MemorySaver`; two API replicas would split clients and no run can resume.
- **Firefox and Safari untested** (§5).

---

## 8. Recommendation

**Qualified go for Phase 5**, with one gate.

Everything that was testable passed, after four real defects were found and
fixed. Crash count is zero across 42 page loads on six real repositories, 60 FPS
holds under a 43k-node graph, memory is stable, virtualization works, and the
honesty guarantees hold end to end — no fabricated scores, no invented averages,
no pipeline continuation past a blocked environment.

**The gate: do not ship auto-merge until §7.1 is closed.** The routing code
refuses correctly and is unit-tested, but a decision path that has never once
executed against a real repository should not be the mechanism that merges code
into someone's repository unattended. Constructing a repository that legitimately
reaches `auto_mergeable` — a real bug, a real patch, passing validation, clean
security rescan — is a prerequisite, not a formality. It also happens to be the
one scenario that would exercise A9 (§7.2).

Phase 5 (Repair Planning) does not depend on that path, so it can proceed in
parallel.

---

# Positive Routing Validation

Added after the conditional pass, to close §7.1 (no run had ever reached a
positive routing path) and §7.2 (A9 had executed in 1 of 15 sampled runs).

**Result: `auto_mergeable` reached, reproducibly, by real execution.
`diff_only` not reached end-to-end — diagnosed below, not faked.**

## 1. The blocker that made auto-merge unreachable

Before any fixture work, `auto_mergeable` was **structurally impossible** for a
reason unrelated to patch quality.

`correctness_score = 60 + mutation_score × 40`, and the auto-merge gate is 80 —
so it requires `mutation_score ≥ 0.5`. Mutation scoring had never once produced
a value. The cause:

```python
["python", "-m", "mutmut", "run", "--paths-to-mutate", patch_file]
```

`--paths-to-mutate` is a **mutmut 2** flag. The installed mutmut is **3.6.0**,
which rejects it outright (`Error: No such option '--paths-to-mutate'`) and
loads its configuration at *import* time — before argparse runs — so no CLI flag
can reach it at all. The project declares `mutmut>=2.5.0`, so both majors are in
scope and the invocation was correct for neither in practice.

Every run therefore took the `unavailable` branch and scored a flat
`CORRECTNESS_MUTATION_UNAVAILABLE = 70`, ten points below the gate, **regardless
of how good the patch or the test suite was**.

**Fix — and why it is not a weakening.** A8 now writes `source_paths` and
`paths_to_mutate` into the clone's `setup.cfg` (merging, never clobbering;
deferring entirely if the project configures mutmut in `pyproject.toml`) and
invokes `python -m mutmut run` without the dead flag. No threshold moved. The
change makes the gate **stricter in effect**: previously every patch received the
same 70 whatever its quality, and now a weak suite scores low and is correctly
refused. Verified in isolation before any pipeline run: 21 mutants generated,
21 killed, `status=scored`, `mutation_score=1.0`.

## 2. Fixture

`/private/tmp/positive-fixture` — `statskit`, a two-function statistics module
with a seven-test suite. Deterministic, no network, no LLM dependence in the
fixture itself.

```python
def average(values):
    """An empty series has no mean; the reporting layer treats
    "no samples" as zero rather than an error, so this must not raise."""
    return sum(values) / len(values)          # ZeroDivisionError on []
```

Baseline: **1 failing test, 6 passing.**

**One design decision is worth stating.** The first fixture expressed the bug as
an *assertion* failure (a discount function returning an unclamped value). It
reached A4 correctly but A7 patched nothing, because every frame in the
traceback was inside the test file — an assertion failure produces no source
frame — so target resolution selected `tests/test_pricing.py` and the blast
scope found 0 auto-patchable files. Re-expressing the bug as an exception raised
*from the source module* gave the resolver a real frame to anchor on. That is
fixture design, not gate weakening; it is also a limitation worth recording
(§7 below).

## 3. The run

| | |
|---|---|
| Run ID | `193b9de3-75fd-4a8e-a9dd-3497d5eaf5d1` |
| Reproduced | `c679ab27-f10c-4a72-a83a-568b90b02b84`, `a0bd0c9e-424f-45b9-83ad-db4f99366eb0` |
| Status | `completed` |
| Duration | 8.2 s |
| Retries | **0** |
| Agents executed | **13** — A0.5, A1, A2, A3, A3.5, A4, A5, A5.5, A6, A7, A8, A9, A10 |
| Proof bundle | `sha256:4e9b01f4…9acf` |

Three consecutive runs produced identical routing, scores and axis values — the
result is reproducible, not a single lucky sample.

## 4. Evidence chain

| Stage | Evidence |
|---|---|
| **Environment** | `ready` — pytest collected the suite without import errors |
| **Reproduction** | `CONFIRMED`, confidence 90%, 1 baseline failure, targeted nodeid → `reproduction_confidence = exact_test` |
| **Root cause** | `statskit.py:8`, citation **verified** |
| **Patch** | **1 file** (`statskit.py`), +2 / −2 |
| **Patch content** | `if not values:` / `    return 0.0` — a real source repair, not a test edit |
| **A8 validation** | pytest passed, **0 new failures** |
| **A8 mutation** | **21 mutants, 21 killed, 0 survived → `mutation_score = 1.00`** |
| **Correctness** | **100.0** / threshold 80 |
| **A9 security** | **executed**, 0 new findings, `rejected = no` |
| **Security score** | **100.0** / technical threshold 90 |
| **Fidelity** | 100.0 (MCI verification passed, no phantom changes) |
| **Scope safety** | 75.0 |
| **Trust score** | **0.94** = mean(100, 100, 100, 75) / 100 — all four axes measured |
| **Routing** | **`auto_mergeable` → "Auto Merge"** |
| **Decision reason** | A4's own root-cause narrative (no draft reason — every gate passed) |

Evidence flags, all satisfied:

```
[x] Runtime reproduced        [x] Root cause confirmed
[x] Blast radius analyzed     [x] Mutation validation passed
[x] Security re-scan clean
```

## 5. A9 semantics — both sides verified

**Unmeasured (live evidence).** Run `8929de5f` (`cfgkit` fixture) produced no
patch, so A9 was skipped:

- `security: null` in the axis payload — not `0`
- rendered **"Not measured"**, tone `unknown` (not the failure tint)
- trust = **0.95 = mean(100, 90)** — a two-axis denominator, not four
- routing did **not** auto-merge

**Measured (live evidence).** The `statskit` run above: `security = 100.0`,
entered the denominator (0.94 = mean of four), and permitted the auto-merge path
with every other gate passing.

**Failing security.** Asserted at the routing layer across `0, 25, 50, 75, 89.9`
— all refuse auto-merge — plus `rejected=True` refusing regardless of score, and
`90.0` exactly at the threshold passing. `tests/test_positive_routing_gates.py`,
22 tests.

**Stated limitation:** the failing-security case was *not* produced end-to-end.
Doing so requires A7 to write a patch that introduces a new bandit/semgrep
finding, which a fixture cannot specify. It is gate-verified, not
execution-verified, and this distinction is deliberate rather than glossed.

## 6. `diff_only` — reached at the routing layer, not end-to-end

`diff_only` differs from `auto_mergeable` by exactly one predicate. From
`route_pr_decision`, once `technical_validation_passed` is true:

```python
if citation_review_needed(state):                    return "diff_only", …
if state.reproduction_confidence == "full_suite":    return "diff_only", …
return "auto_mergeable", None
```

Both branches are live and tested, but neither is fixture-controllable:

- **`full_suite`** requires a `CONFIRMED` reproduction from which *no* test
  nodeid can be recovered. Two routes were tried. A test failing in a session
  fixture yields pytest outcome `error` rather than `failed`, which bypasses
  `_from_failed_test` — but `_extract_from_text` then recovers the nodeid from
  the console output anyway, and confidence resolves to `exact_test`. Producing
  `full_suite` would mean breaking pytest's reporting itself, which is
  environment sabotage rather than a realistic repository.
- **`citation_review_needed`** requires A4's citations to remain unverified after
  two reinvestigations *while the patch still succeeds*. Unverified citations do
  occur naturally — run `8929de5f` shows "Root cause confirmed" unchecked — but
  whether the LLM emits a verifiable citation is not something a fixture
  determines, and in that run A7 produced no patch, so it hard-drafted on
  exhausted retries instead.

`diff_only` is therefore **verified as a reachable branch** (both predicates
tested against real state shapes) but **not observed in a live run**. It is the
strictly weaker claim of the two: it is the same evidence chain as the
auto-merge run with one additional caution flag, and that entire chain is now
proven live.

## 7. Remaining limitations from this validation

1. **Assertion-only failures resolve to the test file.** A bug that returns a
   wrong value without raising produces a traceback containing only test frames,
   and target resolution anchors there — blast scope then finds 0 auto-patchable
   files and no patch is generated. This is a real coverage gap for a large class
   of bugs, and it is why the fixture raises. `CLAUDE.md` §5 already flags target
   resolution as the P0 extraction; this is concrete evidence for that.
2. **`diff_only` unobserved live** (§6).
3. **Failing security unobserved live** (§5).
4. **Scope safety was 75, not 90**, on the auto-merge run — one file required
   human review in the blast scope. It cleared the 80 correctness/security gates
   because `scope_risk` has no hard threshold of its own (`CLAUDE.md` notes two
   of four axes are informational). Worth knowing: a run can auto-merge with the
   lowest of its four axes below the score threshold.
5. **The fixture is small.** 2 functions, 7 tests, 21 mutants. It proves the
   chain executes and scores honestly; it does not prove behaviour at scale.

## 8. Effect on the §7 risk register

| Risk | Before | Now |
|---|---|---|
| §7.1 auto-merge never executed | **open** | **closed** — reached, reproducibly, with real measurements |
| §7.1 diff_only never executed | open | **partially closed** — branch verified, live run not achieved (§6) |
| §7.2 A9 rarely runs | **open** | **closed for the positive path** — A9 executed and measured; both measured and unmeasured semantics verified |

## 9. Test results

| Suite | Result |
|---|---|
| `tests/test_positive_routing_gates.py` (new, 22 tests) | pass |
| `tests/unit/test_a8_mutation_scoring.py` | pass |
| Full backend suite | **1920 passed**, 1 failed |

The single failure remains `test_reproduction_command_stable_10_of_10`
("vulnapi must be a git repo with HEAD") — a pre-existing environmental
precondition of this checkout, unrelated to these changes.

## 10. Browser verification — run `193b9de3`

All seven V2 stages plus the V1 report surface:

| Check | Result |
|---|---|
| Crashes | **0** |
| Uncaught errors | **0** |
| Console errors | **0** |
| Routing label | **"Auto Merge"** on every stage |
| Mutation score displayed | **1.00** |
| "Not measured" occurrences | **0** — correct, every axis was measured |
| Fabricated zeros | **none** |

Every `0%` on screen was located and traced to A5.5's **measured** token
reduction (`original_tokens == reduced_tokens == 45` on a single-function file)
— the backend's own message reads "0% token reduction". A measured zero, shown
as zero, which is exactly the intended behaviour.

## 11. Recommendation update

The §8 gate — *"do not ship auto-merge until §7.1 is closed"* — **is now
closed for the mechanism**: a real repository, a real bug, a real patch, real
mutation and security measurements, and a legitimate `auto_mergeable` decision,
reproduced three times.

Two qualifications stand:

- The proving fixture is deliberately small and its bug raises an exception.
  Assertion-only failures still do not reach patch generation (§7.1), so
  auto-merge coverage in practice is narrower than this result alone suggests.
- Before auto-merge is enabled against repositories that matter, the
  failing-security path (§5) should be observed live, not only gate-tested.

Phase 5 remains unblocked.

---

## 12. Addendum — negative-path runs

Three further real runs, attempting to observe a *failing* security score live.
That specific case was still not reached, but the runs established things the
single auto-merge run could not.

### 12.1 Mutation scoring now discriminates

The strongest evidence that the mutmut fix (§1) tightened rather than loosened
the gate. Two runs, same pipeline, same thresholds:

| Run | Fixture | Mutation score | Correctness | Outcome |
|---|---|---|---|---|
| `193b9de3` | `statskit` | **1.00** (21/21 killed) | **100** | `auto_mergeable` |
| `012a05c2` | `digestkit` | **0.50** (1 survivor) | **40** | retry → `draft` |

Before the fix both would have scored a flat `70`, indistinguishable. The second
run's patch deleted the code its tests exercised, a mutant survived, correctness
fell to 40, and A8 correctly forced a retry. **A weak patch is now refused on
evidence rather than passed on a substituted constant.**

### 12.2 The phantom-change gate fires live

Run `a2382e16`: correctness 100, security 100 — and still `draft`.

```
Fidelity: 50    Decision reason: "Phantom changes detected between PR
                description and diff. Manual verification required."
```

A patch that passed both hard technical gates was refused because MCI
verification found the description asserted changes the diff did not contain.
This is a hard-draft reason firing on real output, previously unobserved.

### 12.3 Unmeasured security, three more times

Runs `012a05c2`, `8929de5f` and the earlier `5c2ea0cf` all skipped A9:

- `security: null`, displayed **"Not measured"**
- trust `0.60` = mean(40, 50, 90) — a **three**-axis denominator
- trust `0.95` = mean(100, 90) — a **two**-axis denominator

Different denominators on different runs, each matching exactly the axes that
were measured. The arithmetic is not a special case for one shape.

### 12.4 Why failing security stayed out of reach

The plan was to exploit a **documented A9 defect**: its finding key is
`file:line:message`, so a patch inserting lines above an existing finding shifts
it and A9 reads it as new. Two fixtures were built around a real bandit finding
(`B324`, weak MD5).

Both failed for the same reason — **A7's patches did not grow the file**:

| Fixture | Finding placement | Patch | Effect |
|---|---|---|---|
| `digestkit` v1 | separate function below | +2 / −2 (net 0) | nothing shifted |
| `digestkit` v2 | inside the patched function | +2 / −5 (net −3) | finding **deleted**, not shifted |

A removed finding is not a new finding, so A9 stayed clean in both. Forcing a net
insertion would mean dictating A7's output, which is exactly the mocking the
brief prohibits.

**Status unchanged:** a failing security score is verified at the gate layer
(five values, `rejected=True`, and the exact threshold — `tests/test_positive_routing_gates.py`)
and remains unobserved in live execution. Recorded as a limitation, not resolved.

### 12.5 Live-observation ledger

| Path | Live? | Evidence |
|---|---|---|
| `auto_mergeable` | **yes** | `193b9de3` + 2 reproductions |
| `draft` — retries exhausted | **yes** | `012a05c2`, `8929de5f` |
| `draft` — phantom changes | **yes** | `a2382e16` |
| `draft` — unmeasured axes | **yes** | `5c2ea0cf` |
| A9 measured, passing | **yes** | `193b9de3` (100) |
| A9 unmeasured | **yes** | 4 runs |
| Mutation measured, passing | **yes** | 1.00 → correctness 100 |
| Mutation measured, failing | **yes** | 0.50 → correctness 40 |
| `diff_only` | no | gate-verified only (§6) |
| A9 measured, failing | no | gate-verified only (§12.4) |

---

# 13. Phase 9 — Final QA round (2026-08-09)

Five real public GitHub repositories, cloned as a user's repository actually
arrives — nothing installed, nothing prepared. Real Mistral calls
(`codestral-latest`), real subprocesses, `GITHUB_DRY_RUN=true` so no pull
request was published. All three terminal outcomes reached.

Every earlier round in this document validated the *decision* layer. This round
validated the *evidence* layer, and that is where it found things.

## 13.1 Runs

| Repository | Outcome | Wall clock | Environment verdict |
|---|---|---|---|
| `sindresorhus/is` | blocked | 5.1 s | `unsupported` — node |
| `github/gitignore` | blocked | 5.4 s | `no_manifest` |
| `pallets/itsdangerous` | blocked | 7.9 s | `not_prepared` — `freezegun` |
| `pytoolz/toolz` | **completed** | 35.1 s | `ready` |
| `pytoolz/<nonexistent>` | failed | 3.9 s | — (clone failed) |

`toolz` executed the full chain: A0.7 → A0.5 → A1 → A2 → A3 → A3.5 → A4 → A5 →
A5.5 → A6 → A7 → A8 → A10, three retries, ending `draft`.

## 13.2 What the interpreter fix closed first

Before any of the above, a bare `"python"` was argv[0] at seven subprocess call
sites — the probe, A3.5, three in scoped validation, two mutmut calls. That
resolves through `PATH`, which is whatever the server process inherited and is
**not** the interpreter ProoFix runs under. The probe reported
`No module named 'pytest'` while ProoFix's own interpreter had pytest installed,
inside a message claiming to describe *"the interpreter ProoFix would run its
tests with"* — the one thing it was not.

`subprocess_runner.PYTHON = sys.executable` closes it, with a test that fails if
any of those modules reintroduces a bare interpreter. The probe and reproduction
now answer the same question by construction. **This is why the four blocked
verdicts above can be trusted at all**; before it, every one of them was a
coin toss on the server's `PATH`.

## 13.3 Defects found

### D-1 — the privacy guard eats absolute file paths out of tracebacks `[critical]`

`scan_text` reuses `_VALUE_RULES` — the rule table written for `scan_source`,
where each pattern matches a whole Python string literal — by **stripping the
`^` and `$` anchors** and running an unanchored `finditer`. That is harmless for
the prefix-shaped rules (PEM, JWT, `AKIA…`). For the email rule it is not:

```
^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$   →   [^@\s]+@[^@\s]+\.[A-Za-z]{2,}
```

De-anchored, `[^@\s]+` spans slashes. Any absolute path containing `@` and
ending in a dotted alphabetic extension matches **in full**, and the subsequent
`result.replace(value, …)` removes every occurrence of it.

Observed on run `46cd2ca6`, `toolz`. The reproduction's traceback:

```
/opt/homebrew/Cellar/python@3.14/3.14.0_1/Frameworks/Python.framework/
  Versions/3.14/lib/python3.14/importlib/metadata/__init__.py:407: StopIteration
```

reached A5.5's context package — and the patch prompt — as:

```
<REDACTED_EMAIL>:407: StopIteration
```

Four frames, all four destroyed, ledger reporting four `email_address`
redactions and `privacy_guard_status: "masked"`. **The guard believes it did its
job.** Reproduced directly:

| Input | Output |
|---|---|
| `/opt/homebrew/Cellar/python@3.14/…/__init__.py:407: StopIteration` | `<REDACTED_EMAIL>:407: StopIteration` |
| `/Users/dana@corp/project/app.py:12: ValueError` | `<REDACTED_EMAIL>:12: ValueError` |
| `File "/srv/build@2/pkg/mod.py", line 9, in handler` | `File <REDACTED_EMAIL>", line 9, in handler` |
| `contact ada@example.com for help` | `contact <REDACTED_EMAIL> for help` (correct) |

**Why this is critical rather than cosmetic.** The traceback is the pipeline's
strongest deterministic signal: `CLAUDE.md` §6.4 scores `STACK_FRAME_EXACT` at
`1.00`, above every other input. Destroying the frame paths does not merely
degrade a display — it removes the top-weighted ranking evidence before ranking
runs. On this run the consequence was visible end to end:

```
target_file:     toolz/compatibility.py     ← a deprecated Python-2 shim
target_function: None                       ← none could be inferred
```

A7 then spent **four attempts and 9,687 tokens** patching a module with nothing
to do with the failure, all four were rejected, and the run ended `draft`.

On Homebrew macOS the interpreter path contains `@` **by construction**
(`python@3.14`), so this fires on every run whose traceback enters the stdlib.
Elsewhere it needs only a user or build path containing `@`.

The fix is not to weaken detection: anchor the email rule per-token rather than
per-line, and stop treating one shared rule table as valid under two different
matching disciplines.

### D-2 — `ready` does not mean the package under test is installed `[high]`

`toolz` was reported `ready` and it was not. `pytest --collect-only` succeeded —
collection imports test modules from the repository root and needs no
*installed* distribution — while the package's own metadata was absent. The one
failing test was the repository's self-check:

```python
def test_has_version():
    # If this test fails, then toolz probably isn't installed properly.
    # For local development, try `pip install -e .` from the project directory
```

A3.5 recorded it as `CONFIRMED` at 90 % confidence. It is not a bug; it is the
suite saying the environment is wrong — precisely what A0.7 exists to say, and
A0.7 had just said the opposite.

This is the exact mirror of §2.1's false block. That round fixed a probe that
was too strict by moving from import sampling to `--collect-only`; this round
shows `--collect-only` is too permissive in the other direction. **A false
`ready` is cheaper than a false block but not free:** it produced a fabricated
repair target and a full four-attempt retry loop.

The narrow fix is to recognise `importlib.metadata.PackageNotFoundError` naming
the repository's own distribution as an environment verdict rather than a
reproduction. The general one is that "collection succeeded" and "the project is
installed" are different claims and A0.7 currently makes the second on evidence
for the first.

### D-3 — the blocked decision label contradicts the reason under it `[medium]`

`"Environment not prepared"` is hardcoded at three sites
(`ui_projection.py:420`, `runner.py:122`, `Workspace.tsx:1228`) and applied to
all three blocking statuses. Two of them are not about preparation:

| Probe status | Reason shown | Label shown |
|---|---|---|
| `not_prepared` | dependencies are not installed | Environment not prepared ✅ |
| `unsupported` | *"This is a node project. ProoFix … repairs Python only"* | Environment not prepared ❌ |
| `no_manifest` | *"No dependency manifest was found"* | Environment not prepared ❌ |

Observed on `sindresorhus/is` and `github/gitignore`. Nothing about a node
repository is unprepared, and no preparation will change the verdict — the label
sends the user to do work that cannot help, directly over a sentence explaining
why. The reason is right; the badge above it is wrong.

### D-4 — `currentAgent` is stale on every terminal run `[low]`

Both the blocked and the completed runs report `currentAgent: "fan-in"` after
finishing. `toolz` ended at A10; the blocked runs ended at `halt_environment`.
Neither is `fan-in`. The lifecycle record is correct, so nothing user-facing is
known to depend on it today — filed so it is not discovered later by something
that does.

## 13.4 Measurements

Per-agent token and cost attribution, run `46cd2ca6`, read from the audit log by
`run_id` — **B-B05 verified live**, not just unit-tested.

| Agent | Calls | Tokens | Cost | Latency |
|---|---|---|---|---|
| A1 | 1 | 2,714 | $0.001211 | 4.7 s |
| A4 | 3 | 12,647 | $0.004342 | 12.7 s |
| A6 | 1 | 242 | $0.000104 | 1.0 s |
| A7 | 4 | 9,687 | $0.003760 | 10.7 s |
| **Total** | **9** | **25,290** | **$0.009417** | 33.0 s wall |

Two things this contradicts:

* **A7 is not the most expensive call in the pipeline.** `CLAUDE.md` §6.8
  assumes it is; on this run **A4 cost 31 % more than A7** (12,647 tokens vs
  9,687) despite A7 running one more time.
* **A4's reinvestigation loop bought nothing.** Three calls, 12,647 tokens, and
  `Verified = 0` of 2 citations — the run ended with `citations_unverified` as a
  draft reason. This is the failure `CLAUDE.md` §3 predicts verbatim: the
  re-prompt is identical because the verification result is computed and then
  discarded rather than fed back. It is now measured rather than predicted, and
  it is the cheapest large saving available in the pipeline.

Environment-blocked runs terminate in **5–8 s** for €0 in LLM spend, including a
full A1/A2/A3 static pass. That is A0.7 working as designed.

## 13.5 Test suites at certification

| Suite | Result |
|---|---|
| Backend `pytest tests` | **2,078 passed**, 1 failed |
| Frontend `vitest run` | **66 passed** (7 files) |
| `tsc --noEmit` | clean |
| `vite build` | succeeds |

The single backend failure is `test_reproduction_stability_gate`, unchanged and
still environmental: it asserts `get_head_sha(vulnapi)` is non-empty, the
`vulnapi/` fixture has no `.git`, and its skip guard checks only that the
directory exists. Confirmed identical on a stashed tree.

## 13.6 Not verified in this round

* **Browser pass.** No browser automation exists in this repository — there is
  no Playwright, and `vitest` runs on jsdom. Type-check, unit tests and a
  production build all pass, which is not the same claim as "the console is
  clean and no error boundary trips". §5 and §10's browser results stand from
  their own rounds and were **not** re-verified here.
* **Sandboxing (B-B12)** and **the Redis checkpointer (B-B13)** remain open by
  decision, not oversight. Every subprocess in this round ran against cloned
  code with the host interpreter.
* **`diff_only`** and **a failing security score** are still gate-verified only,
  unchanged from §12.5.

## 13.7 Verdict

**The routing layer certified in earlier rounds held.** Every decision this
round reached was correct *given the evidence it was handed* — blocked runs
blocked for the right reason, the failed run failed, and the completed run
correctly refused to merge a patch that did not pass.

**The evidence layer did not hold.** D-1 silently deletes the highest-weighted
input to target selection on any host whose paths contain `@`, and D-2 lets a
misconfigured environment through as a confirmed bug. Together they produced a
run that looked healthy at every stage — twelve agents completed, cost
attributed, guard status reported — and repaired a file chosen from evidence
that had been destroyed before ranking saw it.

A trust-gated system whose gates are sound and whose evidence is corrupted is
worse than one that fails loudly, because the gates make the output look
earned. **D-1 must be fixed before any further certification claim is made.**
