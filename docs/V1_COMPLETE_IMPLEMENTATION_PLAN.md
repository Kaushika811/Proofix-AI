# ProoFix V1 — Complete Implementation Plan

Audit date: 2026-08-08. **V1 is the only frontend. V2 is cancelled.**

Companion documents: `V1_FRONTEND_AUDIT.md`, `V1_BACKEND_AUDIT.md`,
`V1_API_DATA_CONTRACT.md`, `V1_BUG_REGISTER.md`, `V1_TEST_COVERAGE.md`,
`V1_PHASE_STATUS.md`.

---

## 0. Governing rules

1. **One frontend.** All UI work happens in `frontend/src/components/proofix/**`,
   `frontend/src/lib/**`, `frontend/src/routes/**`. Never recreate
   `src/components/v2`, `src/lib/v2`, `src/design`, or any `/v2/*` route.
2. **Absent is not zero.** `None` → "Not measured" / "not scored" / "—".
   Never `unknown → success`, `blocked → failed`, `running → completed`.
3. **The backend owns the words.** Reasons and labels render verbatim.
4. **Terminal state comes from `status` + lifecycle events**, never from
   "A10 completed".
5. **Do not weaken the environment precheck.**
6. One phase at a time: execute → test → verify → next.

---

## 1. V2 decommission plan

**Already executed (uncommitted).** 124 files, ~19,000 lines removed:
`src/components/v2/**` (55), `src/lib/v2/**` (14), `src/design/**` (48), three
routes, the `VITE_FEATURE_WORKSPACE_V2` flag, the `tokens.css` import in
`styles.css`, packages `@xyflow/react` / `shiki` / `framer-motion`, and the two
V2 roadmap docs.

**Deliberately retained:**

| Kept | Why |
|---|---|
| All backend endpoints created during V2 (`/stages`, `/context`, `/plan`, `/patch`, knowledge, learning, security) | Backend capability ≠ V2 frontend. Several are V1's Phase 2/4/5 targets. |
| Lifecycle events | V1 now depends on them for terminal state. |
| `surface=v1\|v2` parameter | An API contract, not a frontend. It is the only thing publishing A0.5 and A5.5. Collapsing it is product work (Phase 2/4). |
| `@tanstack/react-query` | `routes/__root.tsx` mounts `QueryClientProvider`. |
| `docs/HISTORICAL_2026-08_WORKSPACE_V2_QA.md` | §7 records still-open backend defects measured against 34 real runs. Banner marks it obsolete. |

**Verified:** V1 → V2 imports were zero before deletion; V2 → V1 was one
(`API_BASE_URL`). Repo-wide grep for `components/v2`, `lib/v2`,
`FEATURE_WORKSPACE_V2`, `v2.runs`, `@/design` returns **0** outside the
historical doc. `routeTree.gen.ts` regenerated: only `/` and `/runs/$runId`.

---

## 2. The real GitHub repository flow

What happens when a GitHub URL is entered, traced through
`orchestrator/graph.py` and `nodes.py`:

1. `POST /api/runs` → run id, `status=pending`.
2. **`prepare_repo`** — `git clone` into a temp dir (leaked, B-B14), resolve
   source roots, record base SHA. *A private/nonexistent repo fails here:
   `GitCommandError … Repository not found` → `status=failed`.*
3. **`environment_precheck` (A0.7)** — probes for a dependency manifest, the
   language, and whether a test runner is importable. Writes `environment`.
   **Never blocks by itself.**
4. **`index_repository` (A0.5)** — knowledge graph, advisory.
5. **`parallel_intel` (A1‖A2‖A3)** → **`layer1_fan_in`** — static intelligence.
   Needs no installed dependencies, so it produces real findings even on an
   unprepared repository.
6. **`after_environment`** — the actual gate. `environment.blocking` →
   `halt_environment` → `status="blocked"`, no `pr_decision`, `run.blocked`
   emitted, END.
7. Otherwise: reproduction → investigation → blast → context → plan → patch →
   mutation → security → routing → PR.

**Why a real repository reaches "Environment not prepared":** A0.7 sets
`blocking` when it cannot establish that the test suite can execute — most
commonly **no dependency manifest** at the root or one level below
(`status: "no_manifest"`, the exact case reproduced with `quant_med`), or a
manifest with no importable test runner. The precheck **never installs
anything**: installing from a cloned repository would execute that repository's
build hooks on an unsandboxed host.

**What a repository needs to proceed:** a discoverable dependency manifest
(`requirements.txt`, `pyproject.toml`, …), an importable test runner (pytest),
and dependencies already installed in the environment the backend runs in.
**Do not weaken this gate.** The correct long-term fix is sandboxed preparation
(Phase 8), not a lowered bar.

---

## 3. Phases

### Phase 0 — Commit the V1-only baseline ✅/🟡

**Goal.** A clean, committed, V1-only repository to build phases on.

**Current state.** V2 removal is done but uncommitted, alongside the blocked-run
fix — 145 changed paths in one working tree with no rollback point.

**Why required.** No phase should start on an uncommitted 19k-line deletion.

**Files.** No new source changes beyond: delete `AGENT_SUMMARY_BULLETS` and
`RETRY_ATTEMPTS` from `data.ts` + their re-export in `mocks/index.ts` (B-F04);
one `prettier --fix` pass (B-F07); refresh `CLAUDE.md`'s stale roadmap
(`V1_BACKEND_AUDIT.md` §4).

**Backend.** None. **Frontend.** Dead-fixture deletion only. **API.** None.

**Tests.** Existing suites are the net: backend 1954, frontend 20.

**Manual verification.** `/`, `/runs/{blocked}`, `/runs/{completed}`,
`/runs/{failed}` render; `/v2/*` and `/design` 404; mock mode renders.

**Acceptance.** Repo-wide V2 grep clean · tsc clean · build ✓ · lint ≤ baseline ·
all tests pass · committed on a branch.

**Dependencies.** None. **Risks.** Low — deletion is already verified.
**Complexity.** S.

---

### Phase 1 — Execution experience: errors, retry, resilience 🟡

**Goal.** The workspace tells the truth about *itself*, not just about the run.

**Current state.** Terminal states are correct (verified against three real
runs). But every REST failure is swallowed (B-F01), there is no retry once a run
settles, no WebSocket reconnect (B-F03), one page-level loading state (B-F08),
and `severity` renders `"LOW"` when nothing was scanned (B-F09).

**Why required.** This is the largest remaining gap between "V1 looks right" and
"V1 is trustworthy". A user cannot currently distinguish *no data* from *broken
data* — the same class of defect as the fabricated values already removed.

**Files.** `useRunData.ts`, `liveEventStream.ts`, `Workspace.tsx`,
`RunReport.tsx` (B-F02), `data.ts` + `StatusBadge.tsx` (B-F05),
`backend/services/ui_projection.py` (`_severity_label` only, B-F09).

**Backend.** One change: `severity` reports absence when `prioritized` is empty.

**Frontend.** Per-model `{data, error, loading}`; a visible error + retry
control per panel; backoff WebSocket reconnect that **never** infers completion
from a close; `blocked` added to `AgentStatus`; `RunReport`'s mock default removed.

**API/contract.** No new endpoints.

**Tests.** Add jsdom + `@testing-library/react`. `Workspace` renders Blocked /
Failed / Completed; REST failure shows error + retry; null trust score renders
"—"; mock mode renders; reconnect resumes without duplicating journal lines.

**Manual verification.** Kill the backend mid-run → error surfaces, retry works.
Restart it → socket reconnects, journal resumes. Reload a settled blocked run →
still Blocked.

**Acceptance.** No swallowed errors · reconnect works · no fixture data reachable
in live mode · component tests green.

**Dependencies.** Phase 0. **Risks.** Medium — reconnect can duplicate events;
the paced-queue bookkeeping must be respected. **Complexity.** M.

---

### Phase 2 — Repository Intelligence surface 🟡

**Goal.** Show what A0.5 actually built, and the repository's identity.

**Current state.** A1/A2/A3 have cards. A0.5 runs, publishes a knowledge graph
and repair memory, and has **no V1 card** — it is only on the `v2` API surface.
`repositoryId`/`headSha`/`repositoryHash` are fetched and never displayed.

**Why required.** A0.5 is real work the user paid for and cannot see. This is
also the decision point for collapsing the two API surfaces.

**Files.** `ui_projection.py` (registry surfaces), `data.ts`,
`AgentVisualization.tsx`, `Workspace.tsx` header.

**Backend.** Publish A0.5 to the product surface; decide whether `surface`
collapses to one value.

**Frontend.** An `intelligence` card + header identity fields.

**Tests.** Registry surface tests; card renders honestly when A0.5 was disabled.

**Acceptance.** A0.5 visible with real graph counts · disabled A0.5 renders
absence, not zeros.

**Dependencies.** Phase 1. **Risks.** Low. **Complexity.** M.

---

### Phase 3 — Investigation / Evidence / Blast Radius ✅ COMPLETE

A3.5, A4, A5 execute, publish typed payloads, have cards and visualizations, and
are backend-tested. Citation verification is deterministic. **No work planned.**
Re-open only if `V1_BUG_REGISTER.md` gains an entry here.

---

### Phase 4 — Context Engineering & Repair Planning 🔴

**Goal.** Make A5.5 and A6 visible.

**Current state.** A5.5 produces context packages with ranking breakdowns and
redaction records; `/runs/{id}/context` serves them; **no V1 consumer**. A6's
full DAG is at `/runs/{id}/plan`; the planner card shows a summary only.

**Why required.** A5.5 is the privacy boundary — redactions are the evidence
that secrets did not reach the LLM. That belongs on screen.

**Files.** `data.ts`, `AgentVisualization.tsx`, `visualizationTypes.ts`,
`useRunData.ts`, `lib/api.ts`, `runService.ts`.

**Backend.** None — endpoints exist.

**Frontend.** `context` card (package, ranking, `redactions`,
`privacy_guard_status`); planner card reads the real DAG.

**Tests.** 404-before-emission renders "Pending"; redactions listed.

**Acceptance.** Real context data on screen · a 404 never renders as empty success.

**Dependencies.** Phases 1–2. **Risks.** Low. **Complexity.** M.

---

### Phase 5 — Patch Generation 🟡

**Goal.** Show the actual diff; make patch generation safe.

**Current state.** Patch card lists filenames. `/runs/{id}/patch` serves both
sides. Backend defects: repo-specific literals (B-B03), no rollback (B-B06),
no-op stub recorded as a candidate (B-B07), short lock TTL (B-B08).

**Why required.** The diff is the product. And B-B03 actively misleads the patch
LLM on any non-JWT repository.

**Files.** `a7_code_generation.py`, `a7_patch_engine.py`,
`runtime_patch_prompt.py`, `retry_brief_builder.py`, `config.py`; frontend patch
card + a diff renderer.

**Backend.** Strip JWT/`vulnapi` literals; add rollback; stop recording no-ops;
fix the lock lease.

**Frontend.** Diff view — **no `shiki`**; a plain unified diff with CSS is
sufficient and avoids re-adding a 2 MB dependency.

**Tests.** Prompt contains no JWT/vulnapi literals for a non-JWT repo; partial
write rolls back; no-op is not a candidate.

**Acceptance.** Real diffs render · no repo-specific literals · A8 never sees a
half-patched clone.

**Dependencies.** Phase 4. **Risks.** Medium — rollback touches the patch write
path. **Complexity.** L.

---

### Phase 6 — Validation & scoring correctness ⛔ BLOCKED-BLOCKING

**Goal.** Every number that gates a merge is a real measurement.

**Current state.** The fabricated `0.5` mutation score is **already fixed**
(`mutation_parser.py`). But **B-B01 is open**: `security_score` defaults to
`0.0` and `_pct(default=0.0)`, so an axis nobody measured contributes a zero to
the composite that gates auto-merge. B-B02: a patch shifting a finding one line
is rejected as a new vulnerability.

**Why required.** This is the highest-severity open defect in the repository. It
is the same class as the mutation-score bug the project already treated as its
most serious finding, and it **changes routing outcomes**. Every report Phase 7
renders is suspect until this lands.

**Files.** `a9_security_rescan.py`, `a10_routing.py`, `services/measurement.py`,
`ui_projection.py`.

**Backend.** Unmeasured axes → `None`, excluded from `measured_mean`; A9 finding
key drops the line number.

**Frontend.** Render "Not measured" for null axes.

**Tests.** A skipped re-scan yields `security_score is None` and does not lower
the composite; a one-line shift is not a new finding.

**Manual verification.** A run with a skipped re-scan must not be penalised.

**Acceptance.** No unmeasured value contributes to a gate · no false rejections.

**Dependencies.** Phase 0 (can run early — consider promoting).
**Risks.** Medium — changes routing outcomes by design; existing expectations
may need updating. **Complexity.** M.

---

### Phase 7 — Final decision & report 🟡

**Goal.** The report is complete and its authority is single-sourced.

**Current state.** Report renders and is nullable-aware. Open: `force_draft_pr`
written in three places (B-B10); `citation_validator` shim (B-B11);
`reproduction_gate` isn't a gate (B-B09).

**Files.** `trust_gating.py`, A3.5, A4, `citation_validator.py`, `RunReport.tsx`.

**Backend.** Consolidate trust-gate authority; delete the shim; decide B-B09.

**Frontend.** Surface *why* a run became a draft, in one place.

**Acceptance.** One owner for `force_draft_pr` · draft reasons visible.

**Dependencies.** Phase 6. **Risks.** Low. **Complexity.** M.

---

### Phase 8 — Production hardening 🔴

**Goal.** Safe to host.

**Scope.** Sandbox subprocess execution (**B-B12 — blocks any hosted
deployment**); Redis broadcaster + checkpointer (B-B13); thread `run_id` to
`LLMGateway` (B-B05); extend the privacy guard to acceptance criteria (B-B04);
clone cleanup (B-B14); `/events` cursor (B-B15); `prefers-reduced-motion`
(B-F06); poll cost (B-F10).

**Dependencies.** Phases 1–7. **Risks.** High — sandboxing changes execution.
**Complexity.** XL.

---

### Phase 9 — Final QA / certification 🔴

**Scope.** End-to-end runs against real public GitHub repositories covering
blocked, failed and completed; browser pass (console clean, no error boundary,
responsive, a11y); performance measurement; refresh `PRODUCTION_CERTIFICATION.md`.

**Dependencies.** All. **Complexity.** L.

---

## 4. Recommended order

`0 → 1 → 6 → 2 → 4 → 5 → 7 → 8 → 9`

**Phase 6 is deliberately pulled forward, ahead of 2/4/5.** It is the only
open defect that makes the product produce *wrong decisions* rather than
*incomplete displays*. Building three more display phases on top of a scoring
bug means every screen added in between shows numbers that may be wrong.

---

## WHAT I SHOULD DO NEXT

**Execute Phase 0 — Commit the V1-only baseline.**

One phase. Nothing else.

Concretely:

1. Create a branch (`main` is the default branch; do not commit directly).
2. Delete `AGENT_SUMMARY_BULLETS` and `RETRY_ATTEMPTS` from
   `frontend/src/components/proofix/data.ts` and their re-export in
   `frontend/src/mocks/index.ts` (B-F04) — hardcoded `ok: true` evidence claims
   that nothing imports.
3. Run `npx prettier --write` once across `frontend/src` (B-F07).
4. Refresh `CLAUDE.md`'s roadmap to match reality (`V1_BACKEND_AUDIT.md` §4):
   mark the mutation score, A3 stubs, `path_resolution`, `llm_gateway` and
   `a0_orchestrator` items done.
5. Verify: `tsc --noEmit`, `vite build`, `npm run lint` (≤ 475-problem baseline),
   `vitest run` (20), `pytest tests` (1954 passing; the `vulnapi` git-fixture
   failure is pre-existing and environmental).
6. Commit the V2 removal, the blocked-run fix and these audit docs together,
   with a message that records what was deleted and why.

**Why this first.** There are 145 uncommitted changed paths including a
19,000-line deletion. Until that is committed there is no rollback point, and
every later phase inherits an unreviewable diff. Phase 0 is small, fully
verified, and creates the baseline the rest of the plan measures against.

**Do not start Phase 1 in the same pass.**
