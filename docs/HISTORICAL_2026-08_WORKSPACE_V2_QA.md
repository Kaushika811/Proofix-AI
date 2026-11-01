# HISTORICAL — Workspace V2 Production Readiness Report (August 2026)

> ## ⚠️ OBSOLETE. DO NOT IMPLEMENT ANYTHING IN THIS DOCUMENT.
>
> **Workspace V2 was cancelled and deleted from this repository.** V1
> (`frontend/src/components/proofix/**`) is the only frontend product. Every
> frontend finding below refers to code that no longer exists, and the phase
> roadmap it belonged to (`WORKSPACE_V2_IMPLEMENTATION.md`,
> `WORKSPACE_REDESIGN.md`) has been removed. **Do not resurrect V2.** Do not
> create `src/components/v2`, `src/lib/v2`, `src/design` or any `/v2/*` route.
>
> **This file is retained for one reason only:** §7's *backend* findings were
> measured against 34 real runs and several remain open and V1-relevant —
> unmeasured axes rendering as measured zeros (which changes routing outcomes),
> G9 cost attribution, G11 per-citation verification, and the privacy-guard gap
> in acceptance criteria. Those are tracked as backend work. Read §7 for that;
> ignore everything else.

Production QA & Polish Sprint. Every number below was measured against the
running backend with 34 real runs; nothing is estimated.

**Verdict: ready for demo and internal use. Not ready for untrusted multi-tenant
hosting** — for reasons that are backend and pre-existing, listed in §7.

---

## 1. Method

Four independent audits, deliberately not "read the code and judge it":

| Audit | Scope | Tool |
|---|---|---|
| Contract | every V2 endpoint × 34 runs, vs. declared TS types | `contract_audit.py` |
| Index safety | `noUncheckedIndexedAccess` across the codebase | `tsc` |
| Payload shape | every agent's `visualization` across 34 runs | `viz_audit.py` |
| Browser | 70 page loads across outcomes; console, uncaught, network | Playwright/Chromium |

Plus targeted tests for refetch behaviour, connection recovery, playback,
responsiveness at 5 viewports, accessibility, and induced backend failures.

**One methodology note, because it nearly produced a false finding.** An early
fault-injection test routed `**/agents**` to a 500 and the stage appeared to
crash into the router's error boundary. It was the *test* that was wrong: that
glob also matches Vite's dev module URLs under `src/components/v2/agents/`, so
it was blocking source code, not the API. Matching the API path precisely
(`/api/runs/{id}/agents`) showed the product handling the 500 correctly. Fault
injection has to be aimed narrowly or it measures the harness.

---

## 2. Bugs found and fixed

### 2.1 Refetch storm — 580 requests per page load `[critical, fixed]`

One page view of a **finished** run:

```
153x /stages   153x /agents   112x /attempts   82x /context   77x /runs/{id}
```

Three compounding causes:

1. **The debounce never debounced.** `INVALIDATE_DEBOUNCE_MS = 300` against a
   460ms drain cadence — every frame landed in its own window and flushed
   alone. The batching was decorative.
2. **`invalidateForFrame` was called per agent** but invalidated three
   run-scoped keys that do not depend on which agent reported. Twelve pending
   agents produced twelve identical invalidations.
3. **Finished runs were refetched per frame.** A terminal run's projections
   cannot change; its draining frames are history being *told*, not state
   changing.

**Fixes.** Debounce derived from the active cadence (so presentation mode cannot
silently regress it); `invalidateForFrames` takes the whole batch, making the
per-agent loop unexpressible; per-frame invalidation skipped when REST already
reports the run terminal.

**Measured: 580 → 50 requests. 11.6×.** Same page, same method.

### 2.2 Backend failures rendered as "Pending" `[high, fixed]`

Of 25 V2 components calling `useQuery`, **3 handled the query's error and 22 did
not**. A 500, a dropped backend, or a knowledge graph that was never built all
rendered as `Pending` — "waiting for data" and "the server returned an error"
are opposite facts and the product showed the reassuring one.

Twelve of 34 runs 404 on the knowledge-graph endpoints, so this was live, not
theoretical.

**Fix.** New `<QueryBoundary>` — the counterpart to `DataBoundary`. Distinguishes
loading / 404-as-fact / real error / data. Errors name the stage, the agent, the
status, the endpoint, and offer retry (§4 of the brief: never a dead end).
Adopted in the knowledge-graph and Mission Control panels; the three bespoke
inline error handlers were replaced so one mechanism governs.

### 2.3 Shared-query failures had no surface `[high, fixed]`

Eight panels across the stage views read `agentsQuery`, and four more read
`contextQuery`. Giving each its own `<QueryBoundary>` would have rendered the
*same* failure eight times — technically honest, practically unreadable.

**Fix.** One notice per shared query, mounted above the panels that depend on
it: `AgentsUnavailableNotice` in `StageViewSlot`, `ContextUnavailableNotice` in
`ContextStageView`. Each panel's own `Waiting`/`Pending` states stay meaningful
for the case they actually describe — the agent has not reported yet.

**Verified** by returning 500 from those endpoints:

| Stage | Alerts | Retry | Crashed |
|---|---|---|---|
| repository | 1 — "Agent results could not be loaded · Repository Understanding · 500" | yes | no |
| investigation | 1 — same, naming Investigation | yes | no |
| context | 2 — agents + "context package could not be loaded · A5.5 · 500" | yes | no |

And with the backend entirely unreachable, three named alerts ("Could not load
the risk analysis · Repository Intelligence · A1 — The request never reached the
backend"), retry offered, no crash, zero uncaught errors.

### 2.4 Retry rows indistinguishable `[medium, fixed]`

A run with four repair attempts rendered four rows reading "Generated 0 patches
from 0 plans", same second, identical in every visible respect. Checked against
`/events`: those were four genuine executions (sequences 15, 19, 23, 27) — the
store was right and the feed gave no way to know it.

**Fix.** `ActivityEntry.attempt`, assigned from the store's own attempt counter
(not re-derived), rendered as `#n` only once a run has actually retried.

### 2.5 `rejection.correctnessScore` crash `[high, fixed earlier this session]`

Backend sends `float | None`; frontend typed it `number` and called `.toFixed(0)`.
Same class as the `threshold` crash — latent until a run recorded no correctness
score. Type widened so the compiler enforces it.

---

## 3. Assumptions removed

- **"V2 might contain mock data."** Verified: `src/components/v2`, `src/lib/v2`
  and `src/design` contain **zero** mock references. Mocks are V1-only and
  gated on `DATA_SOURCE`.
- **"Absent backend fields will crash the UI."** Contract audit across 34 runs ×
  every endpoint: **zero** violations. No field typed required is ever absent.
- **"`noUncheckedIndexedAccess` findings are latent crashes."** 29 findings in
  V2, each inspected: **all 29 provably safe** (modulo-bounded palette indexes,
  loop-bounded reads, an explicit `roots.length === 0` guard). Not "fixed" —
  satisfying the compiler there would have added noise, not safety.
- **"Agent `visualization` payloads vary unpredictably."** Shapes are consistent
  per agent across all 34 runs. The variance that exists (`path: []` vs
  `path: [{…}]`) is empty-vs-populated, which every consumer already handles.

---

## 4. Crashes

**Zero.** 70 page loads spanning completed / failed / draft outcomes: no
uncaught exceptions, no error boundaries, no empty bodies. Every non-2xx logged
was a 404 the UI treats as a fact, or a 502 during a deliberate outage test.

---

## 5. Measurements

### Network (one page load of a terminal run, 8s settle)

| | Before | After |
|---|---|---|
| `/stages` | 153 | 11 |
| `/agents` | 153 | 11 |
| `/attempts` | 112 | 10 |
| `/context` | 82 | 6 |
| `/runs/{id}` | 77 | 6 |
| **Total** | **~580** | **~50** |

### Responsive — 5 viewports × 3 stages

| Viewport | Result |
|---|---|
| tablet 834×1112 | no overflow |
| laptop 1280×800 | no overflow |
| desktop 1440×900 | no overflow |
| small-desktop 1536×864 | no overflow |
| ultra-wide 2560×1080 | no overflow |

Neither document-level horizontal scroll nor any element escaping its container
(excluding deliberate `overflow-x` scrollers).

### Accessibility

| Check | Result |
|---|---|
| Focusable controls | 89 |
| Unlabelled controls | **0** |
| Positive `tabindex` | **0** (DOM order preserved) |
| Focus rings visible | **25/25** sampled |
| Heading structure | `h1` then `h2`×7 — no skipped levels |
| ⌘K opens palette / Escape closes | both pass |
| Reduced motion | **0** running animations, content still renders, 0 errors |

### Connection recovery

Backend killed mid-session and restarted: **39 frames received, 0 repeated
sequences.** No duplicate timeline or activity entries. The store's
sequence-dedupe holds.

**Caveat, stated plainly:** this was tested against a *terminal* run, whose
socket is already closed, so the reconnect banner and backoff path were **not
exercised**. Testing that needs a run long enough to kill mid-flight; every
GitHub run currently finishes in ~8s (§6). This is the one item in the brief I
could not verify properly.

### Playback and replay

Mode switch updates `aria-checked`, shows the standing notice, persists to
`localStorage`. Replay resets the store (48 rows → 1) and refills progressively
at presentation cadence (~1.1s/frame). 0 uncaught errors.

---

## 6. The environment problem (item 12)

Designed, not wired. Full reasoning in `docs/ENVIRONMENT_PRECHECK_DESIGN.md`.

**Key finding: manifest detection alone is the wrong gate.** `click` has a
`pyproject.toml` and still failed — having a manifest and having an importable
`pytest` are unrelated facts. Gating on manifest presence would pass precisely
the runs that fail today.

Built and tested `backend/services/environment_probe.py` + `models/environment.py`
— additive, no caller, changes no behaviour. Against ProoFix itself it correctly
reports `not_prepared`, because it invokes `python` from PATH exactly as A3.5
does. Testing it caught a real bug (`pytest` listed twice), now fixed.

**Not wired into the graph, by design.** That changes run outcomes: runs which
currently produce a draft PR from an unreproduced bug would stop. Recommended
behind `settings.environment_precheck_enabled`, default off, shadow-mode first —
the same path A5.5 took.

---

## 7. Remaining issues

### Frontend

1. **Panel-level error handling is now stage-level for the two shared queries**
   (§2.3), plus `<QueryBoundary>` in the knowledge-graph and Mission Control
   panels. Panels reading *other* queries individually — `FrameworkDetection`
   (`learnRepoQuery`), `NodeInspector`, `FileInspector`, `RepositoryTree` — are
   not converted; they degrade to `Pending` rather than naming a failure.
2. **Reconnect path unverified** (§5).
3. **`~11` refetches of a terminal run still occur** — down from 153, but the
   floor is not 1. Diminishing returns; not chased.
4. **React render-count profiling not done.** Network was measured; render
   counts were not.

### Backend (out of sprint scope, flagged)

5. **Unmeasured axes render as measured zeros.** `security_score =
   security.get("security_score", 0.0)` and `_pct(value, default=0.0)` mean a
   security re-scan that never ran shows **0%** — and `_trust_score` averages
   that zero into the composite, so runs are penalised for measurements nobody
   took. This is the same class as the fabricated mutation score. **This one
   changes routing outcomes**, so it needs a decision, not a patch.
6. **G9** — `run_id` never reaches `LLMGateway`, so cost/token attribution is
   impossible. Three header fields render `Unavailable` because of it.
7. **G11** — per-citation `verified` and per-file `propagation_confidence` never
   reach the client; only aggregates do.
8. **Privacy guard gap** — a JWT reached `acceptance_criteria[2]` with
   `privacy_guard_status: "clean"` and zero redactions. The guard appears to
   cover extracted code only, not acceptance criteria as specified.
9. **Subprocesses are unsandboxed** — pytest, bandit, semgrep, mutmut and ruff
   run against cloned repository code with the host interpreter. This is the
   blocking issue for hosting, ahead of everything else here.

---

## 8. Known limitations

- **V2 has no landing page.** Runs are started from V1; V2 is entered with a run
  id. Blueprint puts run-launching in Phase 5.
- **Presentation mode still accelerates under deep backlog** (threshold 40).
  Deliberate: a 300-frame history at 1.1s/frame is five minutes.
- **Replay is offered only for terminal runs.** Replaying a live one would
  contend with the socket for the same store.
- **The prompt firewall blocks `/Users/`, `/home/`, `/root/` paths**, so runs
  targeting repositories under a home directory fail at A4 with
  `SecurityRejection: host_path`. Correct behaviour, surprising in local
  development.

---

## 9. Recommendations before Phase 5

**Do first — they change what the product asserts:**

1. Decide on the unmeasured-axis question (§7.5). Auto-merge eligibility currently
   rests on averaging in zeros for measurements never taken.
2. Wire the environment precheck in shadow mode and measure its accuracy before
   letting it gate anything.

**Do next — mechanical:**

3. Finish `QueryBoundary` adoption across the remaining 16 components.
4. Verify the reconnect path against a deliberately long-running run.

**Then Phase 5.**
