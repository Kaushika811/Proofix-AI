# Trust-Gating Fix — Implementation Summary

## Problem

The repair pipeline had four trust-gating violations:

1. **Validation exhaustion** — After retry budget was spent, A10 could still route `auto_mergeable` PRs.
2. **A4 reinvestigation** — Counter was not persisted across loops, enabling infinite reinvestigation.
3. **GitHub flow** — PRs were opened without creating/pushing a branch (`create_branch_and_commit` was dead code).
4. **Missing flags** — No explicit `validation_exhausted` or `reinvestigation_exhausted` state.

## Solution

### New state flags (`RunStateModel`)

| Flag | Set when |
|------|----------|
| `validation_exhausted` | `retry_count >= max_retries` and mutation or security validation still failing |
| `reinvestigation_exhausted` | A4 reaches max reinvestigations with unverified citations |

Both flags force `force_draft_pr = True`. A10 checks them **before** score-based routing and never returns `auto_mergeable`.

### A4 reinvestigation cap

- Loads `reinvestigation_count` from prior `state.root_cause`.
- Allows up to **2 reinvestigations** (`MAX_REINVESTIGATIONS = 2`).
- On exhaustion: sets `evidence_incomplete` on `RootCauseBrief`, `reinvestigation_exhausted`, and `force_draft_pr`.

### Validation retry routing

- `edges.py` uses `Settings.max_retries` (default 3).
- When budget is spent, graph routes to `route_pr` (unchanged topology).
- `apply_trust_gates_before_pr()` runs in the `route_pr` graph node before A10.

### GitHub workflow

A10 now calls `GitHubPRService.publish_fix()`:

```
create_branch_and_commit → create_pr
```

Order: **Create Branch → Apply Patch → Commit → Push → Create PR**

Dry-run mode skips git/network but preserves call order.

## Files modified

| File | Change |
|------|--------|
| `backend/state/schema.py` | Added `validation_exhausted`, `reinvestigation_exhausted` |
| `backend/models/root_cause.py` | Added `evidence_incomplete` |
| `backend/orchestrator/trust_gating.py` | **New** — gate helpers and exhaustion detection |
| `backend/orchestrator/edges.py` | Shared validation helpers; reinvestigation guards |
| `backend/orchestrator/graph.py` | Apply trust gates in `route_pr` node |
| `backend/agents/a4_evidence_investigator.py` | Persist counter; exhaustion flags |
| `backend/agents/a10_mci_scorer.py` | Trust-gated routing; `publish_fix` workflow |
| `backend/services/github_pr.py` | `publish_fix()`; improved branch checkout/push |
| `tests/unit/test_trust_gating.py` | **New** — 15 unit tests |
| `docs/TRUST_GATING.md` | **New** — this document |

## Unchanged

- Agent workflow topology (A0→A10 graph shape)
- A4/A5/A6/A8/A9 output contracts
- Retry loop: A8/A9 failure → `increment_retry` → A7
- `max_retries` config (default 3)

## Test coverage added

`tests/unit/test_trust_gating.py`:

- Edge routing at retry budget boundary
- Reinvestigation edge guards and exhaustion
- `apply_trust_gates_before_pr` for validation and evidence exhaustion
- A10 draft-only routing when flags set
- A10 `auto_mergeable` when flags clear and scores pass
- `publish_fix` branch→PR order and failure abort
- A4 counter persistence and exhaustion flag setting

Run:

```bash
pytest tests/unit/test_trust_gating.py tests/integration/test_pipeline.py -v
```
