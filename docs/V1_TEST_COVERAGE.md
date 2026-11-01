# V1 Test Coverage Audit

Audit date: 2026-08-08.

---

## 1. What exists

**Backend — 77 unit files + 6 integration files. 1,954 passing, 4 skipped, 1 failing.**

Integration: `test_pipeline.py`, `test_run_api_surfaces.py`,
`test_run_lifecycle_pipeline.py`, `test_stage_status_pipeline.py`,
`test_ws_lifecycle.py`, `test_ws_blocked_and_disconnect.py`.

Strong areas — one test file per service, as the convention requires:
routing/trust gates, mutation parsing, scoped validation, citation verification,
path resolution, LLM gateway + telemetry, context ranking/extraction/cache,
privacy guard, PII, secret scanner, prompt firewall, knowledge graph, learning,
repair memory, UI projection, agent registry, stage status, run lifecycle.

**Frontend — 2 files, 20 tests.** Both added 2026-08-08:
`runLifecycle.test.ts` (15) and `liveEventStream.test.ts` (5). Cover the
running / completed / failed / blocked mapping, blocked-with-no-downstream-agents,
lifecycle-only, REST-status-only, no-invented-reason, and the legacy fallback frame.

**Infrastructure note:** vitest was added in this session. Before that the
frontend had **no test runner at all**.

---

## 2. Known failing / flaky / environmental

| Test | Nature | Detail |
|---|---|---|
| `tests/unit/test_reproduction_stability_gate.py::test_reproduction_command_stable_10_of_10` | **Environmental, pre-existing** | `AssertionError: vulnapi must be a git repo with HEAD`. The `vulnapi/` fixture is not a git repository in this checkout. Verified to fail identically on a stashed tree — not caused by any recent change. |

No flaky tests observed across repeated full runs this session.

---

## 3. Coverage gaps

### Frontend (largest gap by far)

| Area | Covered? | Note |
|---|---|---|
| Terminal-state mapping | ✅ | 20 tests |
| Successful run, end to end | 🔴 | no component-level render test |
| Blocked run rendering | 🟡 | mapping tested; **no test that `Workspace` renders "Status · Blocked"** |
| Failed run rendering | 🟡 | same |
| Retry sequence | 🔴 | none |
| WebSocket reconnect | 🔴 | none (feature does not exist — B-F03) |
| REST failure → user-visible error | 🔴 | none (behaviour does not exist — B-F01) |
| Nullable value rendering ("Not measured") | 🔴 | none |
| `AgentVisualization` payload variants | 🔴 | 1,340 lines, 11 visualizations, **zero tests** |
| `RunReport` with null trust score | 🔴 | none |
| Mock mode still renders | 🔴 | none |
| Route rendering / no error boundary | 🔴 | none |

**No component rendering tests exist at all.** There is no
`@testing-library/react` or jsdom environment configured — only pure-module
tests. Adding component tests requires new dev dependencies.

### Backend

| Area | Covered? | Note |
|---|---|---|
| Blocked run: state, projection, WS, lifecycle | ✅ | strong |
| Environment precheck | ✅ | `test_environment_precheck.py` |
| Routing gates, unmeasured axes | ✅ | `test_routing_unmeasured.py`, `test_positive_routing_gates.py` |
| Mutation parsing | ✅ | `test_mutation_parser.py` |
| Patch generation + integrity | ✅ | 3 files |
| Security re-scan | 🟡 | `test_security_rescan_commands.py`; **no test for the line-shift false rejection (B-B02)** |
| `security_score` unmeasured (B-B01) | 🔴 | none — the highest-severity open bug is untested |
| Real GitHub clone → run | 🔴 | none; all tests use local fixtures or fakes |
| A7 partial-write rollback | 🔴 | none (feature absent — B-B06) |
| Multi-replica / reconnect | 🔴 | none |
| Sandbox escape | 🔴 | none |

---

## 4. Recommended additions, by phase

**Phase 0** — none (deletion only; existing suites are the regression net).

**Phase 1** — frontend component tests (needs jsdom + testing-library):
`Workspace` renders Blocked / Failed / Completed correctly; REST failure shows
an error and a retry control; null trust score renders "—"; mock mode renders.

**Phase 5** — A7 rollback; prompt contains no JWT/vulnapi literals for a
non-JWT repository.

**Phase 6** — `security_score` is `None` when the re-scan did not run and is
excluded from the composite (B-B01); a patch that shifts a finding by one line
is **not** rejected (B-B02).

**Phase 8** — WS reconnect; `run_id` reaches `LLMGateway`; privacy guard covers
acceptance criteria; clone cleanup on terminal state.

**Phase 9** — one end-to-end test against a real public GitHub repository
covering the blocked path and one covering a completed path.
