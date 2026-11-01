# V1 API and Data Contract

Audit date: 2026-08-08.

---

## 1. Endpoint inventory

### Run surface (`backend/api/routes/ui.py`)

| Endpoint | Purpose | Response | V1 uses? | V1 needs? | Status |
|---|---|---|---|---|---|
| `GET /api/repositories` | Sidebar: repos → runs | `SidebarRepo[]` | ✅ | ✅ | OK |
| `POST /api/repositories/validate` | Validate URL/path before run | `RepoMetadata` | ✅ | ✅ | OK |
| `POST /api/runs` | Create run | `{run_id, status, repository}` | ✅ | ✅ | OK |
| `GET /api/runs` | List runs | header + status per run | ✗ | ✗ | sidebar covers it |
| `GET /api/runs/{id}` | Header: repo, branch, retries, decision label, **environment**, **lifecycle**, **status**, repository identity | dict | ✅ | ✅ | OK — carries terminal truth |
| `GET /api/runs/{id}/summary` | Executive summary | `ExecutiveSummaryModel` | ✅ | ✅ | OK |
| `GET /api/runs/{id}/agents?surface=v1\|v2` | Agent cards | `AgentEntry[]` | ✅ (v1) | ✅ | OK |
| `GET /api/runs/{id}/stages?surface=` | Stage roll-up | `{stages:[…]}` | ✗ | 🟡 | unused by V1 |
| `GET /api/runs/{id}/context` | A5.5 package | 404 until emitted | ✗ | 🟡 | **no consumer** |
| `GET /api/runs/{id}/plan` | A6 full DAG | 404 until emitted | ✗ | 🟡 | **no consumer** |
| `GET /api/runs/{id}/patch` | A7 bundle, both sides | 404 until emitted | ✗ | 🟡 | **no consumer** |
| `GET /api/runs/{id}/attempts` | Repair attempts | `RepairAttemptsModel` | ✅ | ✅ | OK |
| `GET /api/runs/{id}/report` | Final report | `RunReportModel` | ✅ | ✅ | OK |
| `GET /api/runs/{id}/events` | Agent timeline (cap 500) | `AgentStatusEvent[]` | ✅ | ✅ | no `after=` cursor (G6) |
| `POST /api/runs/{id}/chat` | Q&A over run state | `{answer}` | ✅ | ✅ | OK |

### Other surfaces

| Router | Endpoints | V1 uses? | Verdict |
|---|---|---|---|
| `runs.py` | `POST /`, `GET /{id}`, `/{id}/sig`, `/{id}/events`, `/{id}/proof/{issue_id}` | ✗ | Keep — `/sig` and `/proof` are real capability with no V1 consumer |
| `knowledge.py` | 7 (metrics, capabilities, risk, hotspots, query, export, formats) | ✗ | Keep — A0.5 output |
| `learning.py` | 12 (dashboard, metrics, repositories, organization, templates, patterns, repairs, reviews, outcomes) | ✗ | Keep |
| `security.py` | 11 (dashboard, metrics, policies, routing, timeline, audit, compliance, encryption) | ✗ | Keep — run-scoped filters return empty until G9 |
| `ws.py` | `WS /ws/runs/{id}` | ✅ | OK — replays history + lifecycle, then live |

**Summary: V1 consumes 10 of ~46 endpoints.** Nothing V1 needs is missing from
the backend. Every gap in §2 is a *frontend* gap.

---

## 2. WebSocket contract

`WS /ws/runs/{run_id}`:

1. On connect: replays up to 500 `AgentStatusEvent`s, then the lifecycle list.
2. Live: agent + lifecycle frames from Redis pub/sub and the in-process
   broadcaster, deduped over a 512-frame window.
3. Idle every 2 s: re-reads state; if terminal and no authoritative lifecycle
   frame was delivered, sends the **legacy fallback**
   `{"type": "run.completed", "status": "<real status>"}` — the name is fixed
   for backwards compatibility and **`status` carries the truth**.
4. Keep-alive `{"type":"ping"}` every 30 s.
5. Expected disconnects (EPIPE, reset, abort, post-close `RuntimeError`) are
   handled quietly; unexpected errors propagate.

Frames distinguished by shape: only lifecycle/ping frames carry `type`.

---

## 3. Field-level data contract

| UI value | Backend field | Transformation | Nullable | Absent-state behaviour | Real today? | Required change |
|---|---|---|---|---|---|---|
| Repository | `state.repo_path` | `repo_display_name` — tail, strip `.git` | no | `"repository"` | ✅ | — |
| Branch | git branch of clone | `repo_branch` | yes | `"—"` | ✅ | — |
| Commit / headSha | `base_commit_sha` or index pointer | passthrough | yes | `null` | ✅ | 🟡 **not displayed** |
| Runtime / execution time | min/max event timestamps | `_fmt_duration` | no | `"—"` | ✅ (derived) | — |
| Attempts | `retry_count + 1` | `total_attempts` | no | 1 | ✅ (derived) | — |
| Retries | `state.retry_count` | passthrough | no | 0 | ✅ | — |
| Current agent | `state.current_agent` | passthrough | yes | `""` | ✅ | — |
| Agent status | events + `state.status` | `_agent_status`; terminal run with no events ⇒ `skipped` | no | `running`/`skipped` | ✅ | — |
| Findings | `static_report.prioritized` | count/list | yes | `[]` | ✅ | — |
| Severity | top finding `severity` | `_severity_label` thresholds | yes | `"LOW"` | 🟡 | **`"LOW"` when nothing was scanned asserts a measurement.** Should be "Not measured". |
| Root cause | `root_cause.root_cause` | passthrough | yes | `""` | ✅ | — |
| Confidence | `root_cause.confidence` | `×100` | yes | `"not measured"` | ✅ | — |
| Files affected | `blast_graph.scope` | count | yes | 0 | ✅ | — |
| Mutation score | `mutation_result` via `mutation_parser` | real parse | yes | `"not scored"` | ✅ | — |
| Security score | `security_result.security_score` | **`.get(..., 0.0)`** | yes | **`0.0`** | 🔴 | **B-B01 — unmeasured renders as measured zero** |
| Trust score | mean of measured axes | `measured_mean` | yes | `null` → `"—"` | ✅ | — |
| Final decision | `pr_decision.pr_type` / `status` | `run_decision` | no | `blocked`→"Environment not prepared", else "Pending" | ✅ | — |
| Evidence flags | `report.evidence[]` `{ok,text}` | backend-authored | no | `[]` | ✅ | — |
| Reason (blocked) | lifecycle `reason` ∥ `environment.reason` | verbatim | yes | `null`, panel hidden | ✅ | — |
| Reason (failed) | lifecycle `run.failed.reason` | verbatim | yes | `null` | ✅ | — |

---

## 4. Contract rules that must not regress

1. Absent ≠ zero. `None` renders "Not measured" / "not scored" / "—".
2. Reasons are the backend's words, verbatim. The client composes none.
3. Terminal state comes from `status` **and** the lifecycle list. Never from
   "A10 completed".
4. `AgentEntry.status = "skipped"` means the pipeline routed around the stage —
   never render it as success or failure.
5. The legacy WS fallback frame is named `run.completed` regardless of outcome.
   **Read `status`.**
