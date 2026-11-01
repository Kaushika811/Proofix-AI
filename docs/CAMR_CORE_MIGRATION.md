# CAMR Core Strengthening — Implementation Summary

## Overview

A4, A5, and A6 were strengthened with multi-source evidence fusion, multi-origin blast traversal, and real fix-DAG dependency edges — without changing LangGraph topology or downstream API contracts.

---

## A4 — Root Cause Investigation

### Inputs (all four sources)
| Source | Used for |
|--------|----------|
| Stack trace | `traceback` / `stack_trace` from reproduction |
| Static findings | `static_report.prioritized` |
| CVEs | `cve_report.findings` (Critical classification) |
| Runtime evidence | Structured A3.5 fields (`status`, `failing_test`, `exception_*`, etc.) |

### New outputs on `RootCauseBrief`
| Field | Description |
|-------|-------------|
| `confidence` | 0.0–1.0 weighted score from evidence diversity |
| `evidence_refs` | List of `EvidenceReference` (source, ref_id, file, line, claim, weight) |
| `runtime_evidence` | Snapshot of structured reproduction fields |
| `cve_context` | Critical CVE IDs referenced in analysis |

### Service
`backend/services/root_cause_builder.py` — evidence collection, confidence scoring, summary synthesis.

---

## A5 — Blast Graph

### Changes
- **Multi-origin**: traverses from **all verified citations** (falls back to all citations if none verified)
- **`hop_count`**: BFS depth from each origin (max 3 hops)
- **`propagation_confidence`**: `base_confidence × 0.85^hop_count`
- **`origin`**: which citation file seeded the scope entry
- **`origins`**: list of seed files on `BlastGraphResult`

### Service
`backend/services/blast_traversal.py` — replaces inline single-origin BFS in A5.

---

## A6 — Fix DAG Planner

### Changes
- **Real dependency edges** from:
  - SIG import graph (upstream file fixes before downstream)
  - CVE reachability (CVE fix nodes before app files importing the package)
- **CVE consumption**: uses `CVEReachabilityReport.findings` with `classification == Critical`
- **Meaningful execution order**: NetworkX topological sort over dependency edges (LLM order as optional override)
- **`conflict_batches`**: issues sharing the same file (exposed to A7 via existing `fix_dag` state key)
- **`dependency_edges`**: new explicit edge list on `FixDAGPlan`

### Service
`backend/services/fix_dag_builder.py`

---

## Schema Changes (additive)

### `RootCauseBrief`
+ `confidence`, `evidence_refs`, `runtime_evidence`, `cve_context`
+ `EvidenceReference` model

### `ScopedFile`
+ `hop_count`, `origin`

### `BlastGraphResult`
+ `origins`

### `FixDAGPlan`
+ `dependency_edges`
+ `DependencyEdge` model

All existing fields preserved with defaults.

---

## Files Modified

| File | Change |
|------|--------|
| `backend/models/root_cause.py` | EvidenceReference, confidence, runtime/cve fields |
| `backend/models/blast.py` | hop_count, origin, origins |
| `backend/models/fix_dag.py` | DependencyEdge, dependency_edges |
| `backend/services/root_cause_builder.py` | **New** |
| `backend/services/blast_traversal.py` | **New** |
| `backend/services/fix_dag_builder.py` | **New** |
| `backend/agents/a4_evidence_investigator.py` | Multi-source analysis |
| `backend/agents/a5_blast_graph.py` | Multi-origin traversal |
| `backend/agents/a6_fix_dag_planner.py` | Real DAG edges + topo order |
| `backend/agents/a7_patch_engine.py` | Blast context includes hop count |
| `tests/unit/test_camr_core.py` | **New** — 8 unit tests |

---

## Test Coverage Added

`tests/unit/test_camr_core.py`:
- Multi-source evidence ref collection (stack, finding, CVE, runtime)
- Confidence scoring
- Verified-citation origin resolution
- Multi-origin blast traversal with hop decay
- Import-graph and CVE dependency edges
- Conflict batch detection

Run:
```bash
pytest tests/unit/test_camr_core.py tests/integration/test_pipeline.py -v
```

---

## Preserved

- LangGraph flow: `investigate → blast_scope → plan_fixes → generate_code`
- State keys: `root_cause`, `blast_graph`, `fix_dag`
- A7/A8/A9/A10 consumption patterns (additive fields only)
- Agent class names and `run()` signatures
