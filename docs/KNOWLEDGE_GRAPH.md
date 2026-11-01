# Repository Knowledge Graph (Phase 4)

Evolution of the Phase 3 Repository Intelligence layer from six parallel
analyses into one connected, queryable graph. Additive throughout: no LangGraph
change, no `RunState` change, no existing API change, and every consumer treats
the graph's absence as normal.

---

## 1. Architecture

```
                      Git Repository
                            │
              ┌─────────────▼──────────────┐
              │  workspace_layout          │  monorepo / nested repo / languages
              └─────────────┬──────────────┘
                            ▼
              ┌────────────────────────────┐
              │  repository_indexer        │  hash → delta → incremental parse
              └─────────────┬──────────────┘
                            ▼
    ┌───────────────────────────────────────────────────┐
    │            RepositoryIntelligence                 │   ← the store
    │  RepositoryGraph  CallGraph  Ownership            │     (Phase 3)
    │  GitHistory  Documentation  RepairMemory          │
    └───────────────────────┬───────────────────────────┘
                            │  7 adapters — reference, never copy
                            ▼
    ┌───────────────────────────────────────────────────┐
    │       RepositoryKnowledgeGraph  (the view)        │
    │   Repository · Package · File · Class · Function  │
    │   Method · API · Test · Config · Owner · Commit   │
    │   Document · Repair · Capability                  │
    └───┬────────────┬────────────┬────────────┬────────┘
        │            │            │            │
   QueryEngine  CapabilityLayer  RiskEngine  ArchitectureAnalyzer
        │            │            │            │
        └────────────┴──────┬─────┴────────────┘
                            ▼
              graph_export (JSON · GraphML · DOT · Mermaid)
              /api/knowledge/*      A5.5 tie-break signals
```

**The graph is a view, not a store.** Nodes carry identity plus the attributes
traversal needs; the substance stays in the six structures, which remain the
single place each is maintained. Duplicating them would create two sources of
truth that drift the moment one is updated incrementally and the other is not.

---

## 2. Node and edge schema

**14 node types.** `repository`, `package`, `file`, `class`, `function`,
`method`, `api`, `test`, `config`, `owner`, `commit`, `document`, `repair`,
`capability`.

**19 edge types**, grouped by what they mean:

| Group | Edges |
|---|---|
| Structure | `CONTAINS` `DEFINES` `IMPORTS` `DEPENDS_ON` `INHERITS` `IMPLEMENTS` `REFERENCES` |
| Behaviour | `CALLS` `EXPOSES` |
| Verification | `TESTS` `VALIDATES` |
| History | `MODIFIED` `AUTHORED` `OWNS` `CO_CHANGED` |
| Knowledge | `DESCRIBES` `FIXED` `AFFECTS` `PART_OF` |

Every edge carries `weight`, `provenance` (which source analysis justified it)
and `evidence` (a human-readable reason). Node ids are carried across unchanged
from `RepositoryGraph` — `function:pkg/auth.py::validate` — which is what lets
every adapter attach without a translation table.

### The edges that justify the layer

These are cross-structure: no single analysis could produce them.

```
Commit    ──MODIFIED──▶  File        history × repository graph
Document  ──DESCRIBES─▶  Function    documentation × repository graph
Repair    ──FIXED─────▶  Function    repair memory × repository graph
Test      ──VALIDATES─▶  Function    call graph × test classification
Owner     ──OWNS──────▶  File        ownership × repository graph
API       ──EXPOSES───▶  Function    decorators × repository graph
```

`MODIFIED` lands on *files*, not functions. Git history here is file-resolution;
attributing a commit to a particular function would be a guess, and a fabricated
edge under a risk score is exactly what this layer must not produce.

---

## 3. Query engine

Deterministic traversal, total ordering, no LLM. 20 queries including all nine
required: `functions_in_file`, `files_owned_by`, `historical_bug_hotspots`,
`documentation_for`, `related_functions`, `co_changed_files`,
`functions_called_by`, `validated_repairs`, `recent_high_churn_modules`.

Also: `callers_of`, `call_chain`, `supporting_tests`, `owners_of`,
`unowned_files`, `commits_touching`, `repairs_touching`, `api_surface`,
`classes_in_file`.

Every query records its latency into `metrics.query_total_ms`.

---

## 4. Capability layer

Five deterministic signals — filename, import, route, documentation,
configuration — over a 14-domain generic vocabulary. Nothing is inferred from
meaning: a file joins "Payments" only because something in the repository writes
the word down, never because its code looks transactional.

Confidence comes from how many *independent kinds* of signal agree, not how
often one fires. Ten files named `auth_*.py` is still one kind of evidence.

**Coverage dilution.** A "capability" spanning most of the repository has matched
generic vocabulary rather than isolated a feature — `store`, `model` and `query`
are ubiquitous in some codebases. Above 35% coverage confidence is scaled down
proportionally (and the scaling is itself reported as evidence). Not applied
below 8 files, where coverage is arithmetic rather than signal.

---

## 5. Risk engine

Nine signals, fixed weights, additive, every contribution itemised:

| Signal | Weight | Provenance |
|---|---|---|
| `bug_history` | 0.22 | history |
| `churn` | 0.16 | history |
| `repair_history` | 0.14 | repair memory |
| `mutation_failure` | 0.12 | repair memory |
| `complexity` | 0.12 | AST (McCabe) |
| `fan_in` | 0.10 | call graph |
| `low_ownership` | 0.08 | ownership |
| `fan_out` | 0.03 | call graph |
| `no_documentation` | 0.03 | documentation |

**Two signals are inverted, deliberately.** Concentrated recent ownership
*reduces* risk — a file with a clear maintainer is understood; one split six ways
is where a change surprises someone. Documentation likewise: absence contributes
risk, presence contributes nothing, so a well-commented but churning module can
never outrank a quiet one.

Bands: `low` <0.20, `moderate` <0.40, `elevated` <0.60, `high` ≥0.60.

Risk is per *module*. Function-level risk is not produced — git history is
file-resolution, and a function-level score would claim precision the evidence
does not support.

---

## 6. Architecture analyzer

Eight detectors: god objects (by symbol count, total complexity, or class
breadth), circular dependencies (iterative DFS, rotation-invariant deduplication),
over-centralized utilities, dead modules, orphan files, unowned modules,
high-risk APIs, unused code.

**Honest limitation.** `dead_module` and `unused_code` detect *statically
unreferenced* code, not unreachable code. Python resolves dynamically:
`importlib`, decorator registration, entry points, and config-string dispatch all
produce false positives. Both detectors exclude what they can recognise (API
surface, tests, package initialisers, dunders, decorated callables) and report
the rest as a **candidate** with severity — never a verdict. Deleting on this
signal alone would break working repositories.

---

## 7. Explainability

Enforced by the type system, not by convention. Every recommendation carries an
`Explanation` of `Evidence` records:

```python
Evidence(signal, value, contribution, detail, provenance, edges)
```

`explain_risk()` and `explain_hotspot()` flatten to the mandatory shape:
`why` / `signals` / `edges` / `evidence`. A test asserts that no assessment,
capability or hotspot is ever emitted without evidence — a black-box score is
precisely what this layer must not produce.

---

## 8. Incremental updates and cache

Two layers, both keyed so there is no invalidation to get wrong:

| Layer | Key | Storage |
|---|---|---|
| Index | `repository_id` + `repository_hash` in payload | Redis, cross-run |
| Repair memory | `repository_id` | Redis, 1-year TTL |
| Knowledge graph | `repository_id:repository_hash` | in-process LRU (8) |

`repository_hash` folds in HEAD plus the worktree diff hash, so any file change —
including one A7 writes mid-run — produces a different key and a rebuild. The
graph is never mutated, so an entry is either current or unreachable.

Incremental index: hash → diff → classify added/deleted/modified/**renamed**
(content-based) → re-parse only touched files → reuse git history when HEAD is
unchanged → reassemble graphs. Reassembly is full on purpose: call resolution is
global, and `test_incremental_graph_matches_a_full_rebuild` holds the guarantee.

---

## 9. Benchmarks

Measured on `backend/` (98 files, 2,382 graph nodes, 11,199 edges):

| Operation | Time | Target |
|---|---|---|
| Index — full rebuild | 261 ms | — |
| Index — unchanged (cache hit) | **44 ms** | <50 ms ✓ |
| Index — incremental (+1 file) | **40 ms** | <300 ms ✓ |
| Graph build | 13–52 ms | — |
| Graph — cached lookup | 0.0006 ms | — |
| Query — `functions_in_file` | 0.002 ms | — |
| Query — `related_functions` | 0.091 ms | — |
| Graph memory | ~1.9 MiB | — |

---

## 10. Enterprise support

| Case | Handling |
|---|---|
| Nested repositories | Detected by `.git`, **excluded** from the parent index and reported. Their history belongs to another project; indexing them corrupts ownership and every risk score derived from it. |
| Monorepos | ≥2 sibling packages, or any package under `packages/ services/ apps/ libs/`. Package paths become additional source roots. |
| Multiple packages | 10 manifest types (`pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, …), each implying a language. |
| Multiple languages | 20 suffixes counted; `primary_language` reported. Only Python is AST-parsed — stated plainly rather than implied. |
| Microservices | Each service directory is a package with its own source root. |

---

## 11. API

All additive, all under `/api/knowledge`:

```
GET /api/knowledge/{run_id}/metrics                    nodes, edges, degree, latency, cache, memory, coverage, workspace
GET /api/knowledge/{run_id}/capabilities               inferred groups with confidence + evidence
GET /api/knowledge/{run_id}/risk?limit=20              per-module risk, itemised
GET /api/knowledge/{run_id}/hotspots                   architectural findings
GET /api/knowledge/{run_id}/query/{name}?file=&qualname=
GET /api/knowledge/{run_id}/export/{view}?fmt=json|graphml|dot|mermaid
GET /api/knowledge/formats                             discoverability
```

Views: `repository`, `dependency`, `call`, `ownership`, `repair`,
`architecture`, `hotspots`. Views are capped by node degree (default 300) —
a 2,382-node graph is not renderable, and `truncated` reports when this happened
rather than showing part of a graph as if it were the whole.

---

## 12. Settings

```python
knowledge_graph_enabled: bool = True        # off ⇒ exact Phase 3 behaviour
knowledge_graph_top_risks: int = 10
knowledge_graph_hotspots_per_kind: int = 5
knowledge_graph_max_related: int = 5
repository_cache_version: str = "v2"        # bumped: FunctionSpan gained complexity
sig_cache_key_version: str = "v3"           # bumped for the same reason
```

---

## 13. Constraints honoured

No LLM. No embeddings. No vector database. No semantic search. Everything
deterministic — asserted by tests, and by the absence of the relevant imports in
every module in this layer.

**A5.5 integration.** Graph proximity (`graph_related`, `graph_validated`) is
scored *inside* the existing capped repository-intelligence group
(`MAX_REPOSITORY_INTELLIGENCE_CONTRIBUTION = 0.45`), which sits below
`W_REPRODUCTION_EVIDENCE` (0.60) and well below `W_RUNTIME_STACK` (0.90).
Repository knowledge remains tie-break evidence by construction, not by
convention — `test_graph_signals_are_inside_the_capped_group` proves a traceback
still wins against every graph signal at maximum.

---

## 14. Tests

324 new tests across 8 files:

| File | Tests | Covers |
|---|---|---|
| `test_knowledge_graph.py` | 58 | adapters, edge typing, traversal, metrics, consistency |
| `test_knowledge_queries.py` | 49 | every query, ordering, empty cases |
| `test_graph_export.py` | 41 | views, capping, 4 formats, escaping |
| `test_risk_engine.py` | 39 | each signal, inversions, bounds, explainability |
| `test_architecture_analyzer.py` | 41 | 8 detectors, false-positive exclusions |
| `test_knowledge_integration.py` | 35 | cache, agents, A5.5, performance, API |
| `test_workspace_layout.py` | 34 | monorepos, nested repos, languages |
| `test_capability_layer.py` | 27 | signals, confidence, dilution, attachment |

Full suite: **974 passed**, 1 pre-existing environmental failure
(`test_reproduction_stability_gate` needs a `vulnapi` git fixture).
