# V1 Bug Register

Audit date: 2026-08-08. Deduplicated across `CLAUDE.md`, the historical QA
report, git history, and direct code inspection. Anything `CLAUDE.md` lists that
is already fixed is recorded in `V1_BACKEND_AUDIT.md` §4, not here.

Severity: **S1** wrong output a user would act on · **S2** materially misleading
or missing · **S3** quality/robustness · **S4** cosmetic.

---

## Backend

| ID | Sev | Layer | Description | Root cause | Evidence | Status | Required fix | Phase |
|---|---|---|---|---|---|---|---|---|
| **B-B01** | **S1** | scoring | Unmeasured axes render as measured zeros; the zero is averaged into the composite that gates merges | `security.get("security_score", 0.0)` and `_pct(default=0.0)` | Historical QA §7.5; same class as the old mutation-score bug | ✅ fixed | Return `None` for unmeasured; exclude from `measured_mean`; project as "Not measured" | 6 |
| **B-B02** | S2 | A9 | A patch that shifts an existing finding down one line registers it as **new** and rejects the patch | Finding key includes line number: `f"{file}:{line}:{message[:50]}"` | `a9_security_rescan.py:34` | ✅ fixed | Key on file + normalized message; compare line separately | 6 |
| **B-B16** | **S1** | scoring | An absent scanner scored `security_score = 100.0` — `bandit is not installed` read as `this patch is safe` and cleared `SECURITY_TECHNICAL_THRESHOLD` (90) | A9 returned `[]` for both "clean" and "could not execute"; the score is derived from the count | found during Phase 6; same family as B-B01, opposite direction | ✅ fixed | Track `executed` per scanner; `security_score = None` when none ran | 6 |
| **B-B03** | S2 | prompts | Repository-specific literals in generic code inject false expectations on non-JWT bugs | JWT/expiry strings survived the cleanup pass | `runtime_patch_prompt.py`, `retry_brief_builder.py`; `config.py:20 github_repo_name="vulnapi"` | ✅ fixed | Strip literals; make the repo name required config | 5 |
| **B-B04** | S2 | privacy | JWT reached `acceptance_criteria[2]` with `privacy_guard_status:"clean"`, zero redactions | Guard covers extracted code, not acceptance criteria | Historical QA §7.8 | ✅ fixed | Extend guard to every string leaving the process | 8 |
| **B-B05** | S2 | telemetry | Per-run cost/token attribution impossible; every `AuditEvent.run_id` is `""` | `run_id` never passed to `LLMGateway.complete()` | G9, historical QA §7.6 | ✅ already fixed; now tested | Thread `run_id`/`agent_id` through the gateway | 8 |
| **B-B06** | S3 | A7 | No rollback: if plan 1 writes and plan 2 fails, A8 validates a half-patched clone | No transaction around multi-file writes | `CLAUDE.md` §3 A7 | ✅ fixed | Snapshot + restore on failure | 5 |
| **B-B07** | S3 | A7 | LLM exception falls back to `apply_stub_plan`, which returns the **original content** and records it as a `PatchCandidate` | No-op treated as a patch | `CLAUDE.md` §3 A7 | ✅ fixed | Record a failed attempt, not a candidate | 5 |
| **B-B08** | S3 | A7 | Redis patch lock TTL 60 s while the operation is 1–3 LLM calls | TTL shorter than the work | `CLAUDE.md` §3 A7 | ✅ fixed | Extend/renew lease | 5 |
| **B-B09** | S3 | graph | `reproduction_gate` flows unconditionally to `investigate`; an unreproducible bug still costs a full patch cycle | Gate enforced at routing, not execution | `graph.py:140` | ✅ decided: surface, not enforce | Reasons published via `draft_reasons`; the diff on an unreproducible bug is retained deliberately | 7 |
| **B-B10** | S3 | trust gates | `force_draft_pr` written in three places (A3.5, A4, `apply_trust_gates_before_pr`) | Diffuse authority | `CLAUDE.md` T3 | ✅ fixed | Consolidate into `trust_gating.py` | 7 |
| **B-B11** | S3 | services | `citation_validator.py` is a pass-through shim with its own test file | Dead indirection | file present | ✅ fixed | Fold `coerce_llm_citations` into the verifier; delete | 7 |
| **B-B12** | **S1** | security | pytest/bandit/semgrep/mutmut/ruff run unsandboxed against cloned code with the host interpreter | No isolation | `CLAUDE.md` §7 | 🔴 open | Sandbox before any hosted deployment | 8 |
| **B-B13** | S2 | scale | In-memory `WSBroadcaster` + `MemorySaver`: two replicas lose events, no resume after restart | Single-process assumptions | `CLAUDE.md` §7 | 🔴 open | Redis broadcaster + checkpointer | 8 |
| **B-B14** | S3 | hygiene | `clone_or_copy_repo` leaks a full repo copy per run | temp dir never cleaned | `CLAUDE.md` §7 | ✅ fixed | Clean up on terminal state | 8 |
| **B-B15** | S3 | API | `/events` capped at 500 with no `after=` cursor | G6 | historical QA | ✅ fixed | Add cursor when a run exceeds the cap | 8 |
| **B-B17** | **S1** | privacy | `scan_text` redacts whole **file paths** as email addresses, destroying every traceback frame that names an absolute path containing `@` — including the top-weighted `STACK_FRAME_EXACT` ranking signal | `scan_text` strips `^`/`$` off `_VALUE_RULES` and runs unanchored; de-anchored, `[^@\s]+@[^@\s]+\.[A-Za-z]{2,}` spans slashes and matches `/opt/homebrew/Cellar/python@3.14/…/__init__.py` in full | Phase 9 run `46cd2ca6`: 4 frames → `<REDACTED_EMAIL>:407`, guard reported `masked`/4 redactions; A5.5 resolved `target_function: None` and A7 patched an unrelated module | 🔴 open | Anchor the email rule per token; stop sharing one rule table across two matching disciplines | 9 |
| **B-B18** | S2 | A0.7 | `ready` is reported when `pytest --collect-only` succeeds but the package under test is not installed; the resulting `PackageNotFoundError` is recorded as a `CONFIRMED` bug | Collection imports test modules from the repo root and needs no installed distribution — "collection succeeded" ≠ "the project is installed" | Phase 9 run `46cd2ca6` on `toolz`: the only failing test is the suite's own `test_has_version` installation self-check | 🔴 open | Treat `PackageNotFoundError` naming the repository's own distribution as an environment verdict, not a reproduction | 9 |
| **B-B19** | S2 | UI/labels | All three blocking statuses render `"Environment not prepared"`; for `unsupported` and `no_manifest` the badge contradicts the reason printed directly beneath it | Label hardcoded at three sites rather than derived from `environment.status` | Phase 9: `sindresorhus/is` (node), `github/gitignore` (no manifest) | 🔴 open | Derive the label from the probe status at `ui_projection.py:420`, `runner.py:122`, `Workspace.tsx:1228` | 9 |
| **B-B20** | S4 | state | `currentAgent` is stale on every terminal run — both blocked and completed runs report `"fan-in"` | Terminal nodes (`halt_environment`, `route_pr`) do not clear or update the pointer | Phase 9: runs `46cd2ca6` (completed at A10) and `bd2d1e7f` (halted at A0.7) | 🔴 open | Clear or set the pointer on terminal transition | 9 |

## Frontend

| ID | Sev | Layer | Description | Root cause | Evidence | Status | Required fix | Phase |
|---|---|---|---|---|---|---|---|---|
| **B-F01** | **S1** | data | Every REST failure is silently swallowed; a failed `/report` is indistinguishable from a run that produced none. No retry once the run settles | `Promise.allSettled` with no rejection surface | `useRunData.ts:125` | ✅ fixed | Per-model error state + visible retry | 1 |
| **B-F02** | S2 | fixtures | `RunReport` defaults its `report` prop to `MOCK_RUN_REPORT` — another repo's trust scores render as real if the prop is ever omitted | Default parameter | `RunReport.tsx:13` | ✅ fixed | Require the prop; render an empty state | 1 |
| **B-F03** | S2 | transport | No WebSocket reconnect. A dropped socket silently degrades to 2.5 s polling | `onclose` deliberately unhandled | `liveEventStream.ts` | ✅ fixed | Backoff reconnect that never infers completion from a close | 1 |
| **B-F04** | S3 | fixtures | `AGENT_SUMMARY_BULLETS` (11 hardcoded `{text, ok:true}` claims) and `RETRY_ATTEMPTS` exported, imported by nothing | Leftover from the pre-backend UI | `data.ts:571,597` | ✅ fixed | Delete both | 0 |
| **B-F05** | S3 | types | `AgentStatus` has no `blocked` member; blocked runs borrow the `draft` tone | Union predates the state | `data.ts:3`, `StatusBadge.tsx` | ✅ fixed | Add `blocked` with its own tone | 1 |
| **B-F06** | S3 | a11y | No `prefers-reduced-motion` handling; continuous pulses + scroll choreography | Never implemented | repo-wide | ✅ fixed | Honour the query | 8 |
| **B-F07** | S4 | lint | 435 pre-existing prettier errors (`components/ui/**`, `__root.tsx`, `mocks/repositories.ts`) | Repo never formatted | `npm run lint` | ✅ fixed | One `--fix` commit | 0 |
| **B-F08** | S3 | UX | One page-level loading state; no per-panel skeletons or empty-vs-failed distinction | Single `loading` boolean | `useRunData.ts` | 🟡 partial | Per-model states ✅; per-panel **skeletons** still not built — a loading panel shows stale/empty content, it just no longer hides a failure | 1 |
| **B-F09** | S2 | UX | `severity` renders `"LOW"` when nothing was scanned — asserts a measurement never taken | `_severity_label` defaults | `ui_projection.py` | ✅ fixed | "Not measured" when `prioritized` is empty | 1 |
| **B-F10** | S3 | perf | Every 2.5 s poll re-fetches 5 endpoints and JSON-stringifies each for change detection | `setIfChanged` deep compare | `useRunData.ts:63` | ✅ fixed | Cheaper comparison; back off when idle | 8 |

## Resolved this session (do not re-open)

| ID | Description | Fixed by |
|---|---|---|
| R-01 | Blocked run stuck on `Status · Running`; journal never settled | `runLifecycle.ts` + `liveEventStream` terminal detection |
| R-02 | Terminal state inferred from "A10 completed" | inference deleted |
| R-03 | A0.7 invisible in V1 | `environment` card registered |
| R-04 | `write EPIPE` traceback on normal browser disconnect | `ws.py` quiet-disconnect handling |
| R-05 | Blocked runs kept the socket open forever | `blocked` added to terminal sets |
| R-06 | Severity floor of `"LOW"` claimed a clean scan on runs where A3 never ran | `_severity_label` returns `NOT_MEASURED` |
| R-07 | Blocked runs wore the draft badge in four places (header status, header decision, executive-summary chip, run report) | `blocked` `AgentStatus` + `--status-blocked` tokens |
