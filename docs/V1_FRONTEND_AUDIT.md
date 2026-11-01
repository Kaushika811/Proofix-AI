# V1 Frontend Audit

Audit date: 2026-08-08. Source of truth: the implementation, not prior docs.

**Scope.** `frontend/src/components/proofix/**` (24 files, 6,280 lines),
`frontend/src/routes/**`, `frontend/src/lib/**`, `frontend/src/mocks/**`,
`frontend/src/styles.css`, `frontend/package.json`.

---

## 1. Architecture in one paragraph

Two routes (`/`, `/runs/$runId`) both render `<Workspace>`. `Workspace` composes
every other component. Data arrives on two independent paths that never merge:
**`useRunData`** polls five REST endpoints every 2.5 s and owns all view models;
**`useExecutionRun`** is a pure reducer over an `ExecutionEvent` stream produced
by `liveEventStream` (API mode) or `mockEventStream` (mock mode), and owns only
*pacing* — which card is active and how many of its lines have been revealed.
`runService` is the single seam that switches between fixtures and the backend
via `VITE_DATA_SOURCE`.

The split is deliberate and sound: the backend is authoritative for content, the
event stream only animates it. Keep it.

---

## 2. Component inventory

| Component | Lines | Displays | Data source | REST | WS | Mock path | Notes |
|---|---|---|---|---|---|---|---|
| `Workspace.tsx` | 991 | Shell, header, executive summary, journal, run report mount | `useRunData` + `useExecutionRun` | ✅ | indirect | ✅ | Owns all layout + scroll choreography. Largest surface. |
| `AgentVisualization.tsx` | 1340 | 11 bespoke per-agent visualizations | `entry.visualization` from `/agents` | ✅ | ✗ | ✅ | Typed by `visualizationTypes.ts`. Renders nothing when payload absent. |
| `data.ts` | 608 | — | — | ✗ | ✗ | **fixtures only** | `AGENTS`, `RETRY_ATTEMPTS`, `AGENT_SUMMARY_BULLETS`. See §4. |
| `RunReport.tsx` | 396 | Final report panel: trust axes, files, evidence, proof bundle | `/runs/{id}/report` | ✅ | ✗ | ✅ | **Default param is `MOCK_RUN_REPORT`** — see B-F02. |
| `Sidebar.tsx` | 300 | Repositories → runs, status chips | `/repositories` | ✅ | ✗ | ✅ | Refetched on run completion. |
| `liveEventStream.ts` | 298 | — | `/events` + `/runs/{id}` + WS | ✅ | ✅ | — | Paced drain queue; terminal detection from status+lifecycle. |
| `AgentCard.tsx` | 278 | One agent: lines, metrics, evidence, visualization | props from `/agents` | — | — | — | Pure presentational. |
| `NewRunScreen.tsx` | 231 | URL/path input, validation, submit | `/repositories/validate`, `POST /runs` | ✅ | ✗ | ✅ | Only screen with real form error surfacing. |
| `ChatPanel.tsx` | 173 | Q&A dock | `POST /runs/{id}/chat` | ✅ | ✗ | ✅ | Suggestions are static strings (acceptable affordance). |
| `useRunData.ts` | 171 | — | 5 endpoints + `/repositories` | ✅ | ✗ | ✅ | `Promise.allSettled`, 2.5 s poll, stops when run settles. |
| `visualizationTypes.ts` | 169 | — | — | — | — | — | Discriminated union; nullable-aware. |
| `runLifecycle.ts` | 163 | — | `/runs/{id}` status + lifecycle | — | — | — | The single terminal-state mapping. Added 2026-08-08. |
| `useExecutionRun.ts` | 144 | — | event stream | ✗ | ✗ | ✅ | Pure reducer. Returns `done` + `settledState`. |
| `AnalyzingSequence.tsx` | 119 | Pre-run animation while `POST /runs` resolves | `waitFor` promise | — | — | ✅ | Purely decorative; gates on the real promise. |
| `RetrySequence.tsx` | 93 | Repair attempts timeline | `/runs/{id}/attempts` | ✅ | ✗ | ✅ | |
| `ProgressRing.tsx` | 90 | Circular score | props | — | — | — | `?? 0` is an animation start value, not data. |
| `AgentRail.tsx` | 80 | Horizontal stage rail | props | — | — | — | |
| `emptyModels.ts` | 78 | — | — | — | — | — | **The honesty layer.** Live mode starts here, not from fixtures. |
| `mockEventStream.ts` | 76 | — | — | ✗ | ✗ | ✅ | Owns all mock timing. |
| `AnimatedNumber.tsx` | 55 | Number tween | props | — | — | — | |
| `StatusBadge.tsx` | 40 | Status chip | props | — | — | — | Keyed on `AgentStatus`; has no `blocked` member (see B-F05). |
| `StatusIcon.tsx` | 31 | Status glyph | props | — | — | — | |

`routes/`: `__root.tsx` (123) mounts `QueryClientProvider`, 404 and error
boundaries. `index.tsx` (20) and `runs.$runId.tsx` (18) both render `Workspace`.

`lib/`: `api.ts` (59) — `ENDPOINTS` registry + `apiFetch`. `runService.ts` (162)
— the mock/API seam. `error-reporting.ts`, `error-capture.ts`, `error-page.ts`.

---

## 3. Behaviour audit

**Loading.** `useRunData` exposes one boolean. `Workspace` renders a single
`WorkspaceLoading` ("Loading run…") for the whole page. There is no per-panel
skeleton, so a slow `/report` blanks nothing — it just stays empty. 🟡

**Error.** `apiFetch` throws on non-2xx. `useRunData` swallows every rejection
via `Promise.allSettled` and **surfaces nothing** — a persistently failing
`/summary` is indistinguishable from a run that has not produced one. This is
the single largest UX defect in V1. 🔴 (B-F01)

**Retry.** None at the fetch layer. Recovery is incidental: the 2.5 s poll
re-attempts while the run is live, and stops entirely once the run settles. A
terminal run whose `/report` failed on the last poll shows an empty report
forever with no way to retry short of a page reload. 🔴 (B-F01)

**Terminal state.** Correct as of 2026-08-08. `runLifecycle.resolveRunLifecycle`
reads `status` **and** the lifecycle event list; `done` in `Workspace` means
"the run is over", not "the replay finished". ✅

**WebSocket.** `liveEventStream` opens one socket per run, ignores `close`
deliberately (a close is not evidence of completion), and has **no reconnect**.
A dropped socket degrades to REST polling, which is survivable but means the
journal stops animating until the next poll cycle. 🟡 (B-F03)

**Empty states.** `emptyModels.ts` is well designed — live mode starts blank so
nothing on screen came from a fixture. But blank renders as *empty*, not as
"waiting" or "unavailable", so absence and failure look identical. 🟡

**Responsive / a11y.** Journal is single-column and adapts. Not audited against
a screen reader. No `prefers-reduced-motion` handling anywhere — the journal has
continuous pulse animations and scroll choreography. 🟡

---

## 4. Mock/fixture surface

`data.ts` (608 lines) and `mocks/**` are the mock-mode fixtures. They are
**correctly isolated** — `useRunData` selects `EMPTY_*` when `isLive`. Three
specific leaks remain:

1. **`RunReport.tsx:13`** — `report = MOCK_RUN_REPORT` as a default parameter.
   `Workspace` always passes `runData.report`, so it does not fire today, but
   any future call site omitting the prop renders vulnapi's trust scores as if
   real. Latent. (B-F02)
2. **`data.ts:597` `AGENT_SUMMARY_BULLETS`** — 11 hardcoded `{text, ok}` pairs
   ("Runtime failure reproduced.", `ok: true`). Re-exported by `mocks/index.ts`
   and **imported by nothing**. Dead, but it is exactly the class of literal
   that caused the original false-evidence bug. Delete. (B-F04)
3. **`data.ts:571` `RETRY_ATTEMPTS`** — same: exported, unused. Delete.

`ChatPanel`'s `LIVE_CHAT_SUGGESTIONS` are static prompt strings. Those are UI
affordances, not asserted facts — acceptable.

---

## 5. Known bugs (frontend)

See `V1_BUG_REGISTER.md` for the full register. Frontend IDs: **B-F01**
(errors invisible), **B-F02** (`MOCK_RUN_REPORT` default), **B-F03** (no WS
reconnect), **B-F04** (dead fixture literals), **B-F05** (`AgentStatus` has no
`blocked` member — blocked runs borrow the `draft` tone), **B-F06** (no
`prefers-reduced-motion`), **B-F07** (435 pre-existing prettier errors).

---

## 6. Missing functionality

Backend data with **no V1 consumer at all**:

| Backend capability | Endpoint | V1 status |
|---|---|---|
| Context package (A5.5) | `/runs/{id}/context` | 🔴 no card, no consumer |
| Repair DAG (A6, full) | `/runs/{id}/plan` | 🔴 planner card shows summary only |
| Patch bundle, both sides | `/runs/{id}/patch` | 🔴 patch card shows filenames only |
| Stage roll-up | `/runs/{id}/stages` | 🔴 unused; V1 uses a flat rail |
| Repository indexing (A0.5) | `/agents?surface=v2` | 🔴 no V1 card |
| Knowledge graph | `/api/knowledge/*` | 🔴 unused |
| Learning / repair memory | `/api/learning/*` | 🔴 unused |
| Security posture | `/api/security/*` | 🔴 unused |
| Repository identity | on `/runs/{id}` | 🟡 fetched, never displayed |
| SIG export | `/runs/{id}/sig` | 🔴 unused |
| Proof bundle by issue | `/runs/{id}/proof/{issue_id}` | 🔴 unused |
