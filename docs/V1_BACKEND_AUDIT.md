# V1 Backend Audit

Audit date: 2026-08-08. Traced from `backend/orchestrator/graph.py` outward.

**Size.** 18 agent modules, 58 services, 18 model modules, 7 API route modules,
6 orchestrator modules, 12 security, 12 learning.

> **`CLAUDE.md` is out of date.** Several of its P0 items are already done. The
> disagreements are listed in §4. Trust this document over `CLAUDE.md`.

---

## 1. The graph as actually built

16 nodes. Entry `prepare_repo`, three exits to `END`.

```
prepare_repo → environment_precheck → index_repository → parallel_intel
             → layer1_fan_in
                   ├── after_environment ──[blocking]──► halt_environment ──► END
                   └──────────────────────[else]──────► reproduction_gate
reproduction_gate → investigate ⇄ (should_reinvestigate, max 2)
                  → blast_scope → engineer_context → plan_fixes → generate_code
                  → validate_mutation
                        ├── validate_security ──► route_pr ──► END
                        ├── increment_retry ──► generate_code
                        └── route_pr ──► END
                    validate_security ── fail+budget ──► increment_retry
```

**Two findings that contradict older documentation:**

1. **The environment gate is at `layer1_fan_in`, not right after the probe.**
   A0.7 runs early (`prepare_repo → environment_precheck`) but `after_environment`
   is only consulted after A0.5, A1, A2 and A3 have run. This is deliberate and
   documented in `graph.py:135-138`: static analysis reads source and needs no
   installed dependencies, so it still produces real findings; only code
   *execution* stops. **Consequence for the UI: a blocked run legitimately shows
   A1/A2/A3 as `completed`.** That is not a bug.

2. **`reproduction_gate` still flows unconditionally to `investigate`**
   (`graph.py:140`). It is a stage, not a gate. Reproduction failure sets
   `force_draft_pr` and is handled at *routing* time. `CLAUDE.md` §2.1 already
   flagged this; it remains true.

---

## 2. Agent inventory

| ID | Name | Module | Purpose | Input | Output | Exec status | Failure | Retry | Routing impact | API | V1 visible now | V1 needed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A0** | — | — | — | — | — | **does not exist** — `a0_orchestrator.py` deleted; `PipelineRunner` does this | — | — | — | — | n/a | n/a |
| **A0.5** | Repository Indexing | `repository_intelligence.py` | Knowledge graph, repair memory | repo clone | `repository_index` | runs when `repository_intelligence_enabled` | advisory — error appended, run continues | none | none | `/agents?surface=v2`, `/api/knowledge/*` | 🔴 no card | 🟡 desirable |
| **A0.7** | Environment Precheck | `a0_7_environment.py` | Can this repo's tests run? | repo clone | `environment` report | runs unless `stub_mode` or disabled | probe error → recorded, run continues (never blocks) | none | **`blocking` → `halt_environment`** | on `/runs/{id}`, `/agents` | ✅ `environment` card | ✅ done |
| **A1** | Repository Intelligence | `a1_semantic_mapper.py` | SIG: roles, imports, criticality | repo | `sig` | always | raises → run fails | none | none | `/agents`, `/runs/{id}/sig` | ✅ `repo-intel` | ✅ |
| **A2** | Dependency Analyzer | `a2_dependency_analyzer.py` | CVE reachability via OSV | `requirements.txt`, SIG | `cve_report` | always | swallows | none | none | `/agents` | ✅ `deps` | ✅ |
| **A3** | Static Analysis | `a3_static_analysis.py` | bandit+semgrep+ruff, ranked | repo, SIG | `static_report` | always | stubs **only** when tool absent (`code == -1`) ✅ | none | none | `/agents` | ✅ `static` | ✅ |
| **A3.5** | Failure Reproduction | `a3_5_reproduction.py` | Run pytest, capture baseline | repo | `reproduction` | after gate passes | `INFRA_ERROR` → `force_draft_pr` | none | forces draft | `/agents` | ✅ `reproduce` | ✅ |
| **A4** | Root Cause | `a4_evidence_investigator.py` | Brief + verified citations | all evidence | `root_cause` | always | LLM fail → deterministic stub | **≤2 re-investigations** | `force_draft_pr` on exhaustion | `/agents` | ✅ `root` | ✅ |
| **A5** | Blast Radius | `a5_blast_graph.py` | BFS scope, target resolution | SIG, root cause | `blast_graph` | always | — | none | none | `/agents` | ✅ `blast` | ✅ |
| **A5.5** | Context Engineering | `a5_5_context_engineering.py` | Minimal privacy-checked context | blast, evidence | `context_packages` | always | degrades | none | none | `/runs/{id}/context`, `surface=v2` | 🔴 no card | 🟡 desirable |
| **A6** | Repair Planner | `a6_fix_dag_planner.py` | Dependency-ordered fix DAG | findings, blast | `fix_dag` | always | topo-sort fallback | none | none | `/runs/{id}/plan` | 🟡 summary only | 🟡 full DAG |
| **A7** | Patch Generator | `a7_code_generation.py` | Generate patches + integrity guard | context, plan | `patch_bundle` | always | stub = no-op patch | **retry loop target** | none | `/runs/{id}/patch` | 🟡 filenames only | 🟡 diff view |
| **A8** | Mutation Validation | `a8_mutation_validator.py` | Scoped tests + mutmut | patch | `mutation_result` | always | `validation_failure` → retry | drives `increment_retry` | `correctness` axis | `/agents`, `/attempts` | ✅ `mutation` | ✅ |
| **A9** | Security Re-scan | `a9_security_rescan.py` | Post-patch scanner diff | patch, A3 baseline | `security_result` | after A8 passes | new finding → reject | retry within budget | `security` axis | `/agents` | ✅ `security` | ✅ |
| **A10** | Mergeability | `a10_mci_scorer.py` + `a10_routing.py` | MCI, 4 axes, route, publish PR | everything | `pr_decision`, `proof_bundle` | terminal | — | none | **the decision** | `/agents`, `/report` | ✅ `merge` | ✅ |

---

## 3. Terminal statuses and lifecycle

`RunStateModel.status` ∈ `pending | running | validation_retry | completed |
failed | blocked` (`state/schema.py:14`).

`RunLifecycleEvent.type` ∈ `run.started | run.completed | run.failed |
run.blocked` (`state/events.py:38`), persisted on a dedicated Redis list and
published on the shared pub/sub channel.

`blocked` is produced by exactly one path: `after_environment` sees
`environment.blocking` → `halt_environment` sets `status = "blocked"` and writes
**no** `pr_decision`. `PipelineRunner.execute` special-cases it so the blanket
"if not completed, mark completed" coercion cannot relabel it.

---

## 4. Documentation vs implementation disagreements

`CLAUDE.md`'s roadmap is stale. Verified against the tree:

| `CLAUDE.md` claim | Reality |
|---|---|
| P0-1 "mutation score is a hardcoded `0.5`" | **FIXED.** `services/mutation_parser.py` exists; A8 reads `outcome.mutation_score`, `survived_mutants`, `total_mutants`, `status == "scored"`, and leaves `correctness_score = None` when there is no patch. |
| P0-2 "A3 substitutes stub findings" | **FIXED.** Stub paths gated on `code == -1` (tool genuinely absent) at `a3:125,179,224`. |
| P0-4 "extract `path_resolution`" | **DONE.** `services/path_resolution.py` exists with tests. |
| P0-6 "extract LLM gateway" | **DONE.** `services/llm_gateway.py` exists with telemetry tests. |
| P0-12 "delete `a0_orchestrator.py`" | **DONE.** File gone, no references. |
| P0-3 "remove repo-specific literals" | **PARTIAL.** `config.py:20 github_repo_name = "vulnapi"` remains; JWT/expiry literals remain in `runtime_patch_prompt.py` and `retry_brief_builder.py`. |
| P0-5 "A9 line-number-sensitive key" | **OPEN.** `a9:34` still keys on `file:line:message[:50]`. |
| P1-12 "delete `citation_validator` shim" | **OPEN.** File still present. |
| §2.1 "reproduction gate is not a gate" | **STILL TRUE.** |

**Action:** `CLAUDE.md` should be refreshed. Not done in this audit (read-only).

---

## 5. Backend defects still open

Sourced from `docs/HISTORICAL_2026-08_WORKSPACE_V2_QA.md` §7 (measured against
34 real runs) and re-verified where cheap:

- **Unmeasured axes render as measured zeros.** `security_score` defaults to
  `0.0`; `_pct(default=0.0)`. A skipped security re-scan shows 0 % and drags the
  composite. **This changes routing outcomes.** Highest-severity open item.
- **G9** — `run_id` never reaches `LLMGateway.complete()`, so every
  `AuditEvent.run_id` is `""` and per-run cost/token attribution is impossible.
- **G11** — per-citation `verified` and per-file `propagation_confidence` are
  aggregated away before reaching any client.
- **Privacy guard gap** — a JWT reached `acceptance_criteria[2]` with
  `privacy_guard_status: "clean"` and zero redactions; the guard appears to
  cover extracted code only.
- **Unsandboxed subprocesses** — pytest/bandit/semgrep/mutmut/ruff run against
  cloned code with the host interpreter. Blocking for hosted multi-tenant use.
- **Single-process assumptions** — in-memory `WSBroadcaster`, `MemorySaver`
  checkpointer. No horizontal scale, no resume after restart.
- **Clone leak** — `clone_or_copy_repo` copies into a temp dir never cleaned up.
