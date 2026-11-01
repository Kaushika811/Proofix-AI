# Repository Assumptions Removal Report

Generated after refactor to make the ProoFix backend repository-agnostic.

## Summary

All hardcoded `vulnapi/` scan paths and production-prefix assumptions were removed from the backend. The system now discovers Python source roots automatically and uses them for static analysis, CVE reachability, and import graph construction.

**Backend `grep vulnapi` result:** Only [`backend/config.py`](backend/config.py) retains `vulnapi` as the default **GitHub PR target repo name** (`github_repo_name`) — this is deployment configuration, not source-code layout assumption (out of scope per plan).

---

## Hardcoded Locations Found (Before)

| # | File | Line(s) | Before | After |
|---|------|---------|--------|-------|
| 1 | `backend/agents/a3_static_analysis.py` | 64, 94, 120 | `repo / "vulnapi"` for bandit/semgrep/ruff | `get_scan_targets()` → multiple scan paths |
| 2 | `backend/agents/a3_static_analysis.py` | 83 | `(repo / "vulnapi").glob("*.py")` stub | `iter_python_files(repo, source_roots)` |
| 3 | `backend/agents/a3_static_analysis.py` | 113–115 | `vulnapi/api.py` SQLi stub | Generic scan for `f"SELECT` in any production file |
| 4 | `backend/agents/a9_security_rescan.py` | 71, 88 | `repo / "vulnapi"` | `get_scan_targets()` |
| 5 | `backend/services/sig_helpers.py` | 25, 32 | `production_prefixes=("vulnapi/",)` | `is_production_file()` + SIG `source_roots` + role filter |
| 6 | `backend/services/ast_import_graph.py` | 11–13 | Full-repo unscoped rglob | Scoped to `source_roots` via `repo_layout` excludes |

---

## Files Modified

| File | Change | Why |
|------|--------|-----|
| **NEW** [`backend/services/repo_layout.py`](backend/services/repo_layout.py) | Source root discovery + scan helpers | Central repo-agnostic layout detection |
| [`backend/state/schema.py`](backend/state/schema.py) | Added `source_roots: list[str]` | Persist discovered roots through LangGraph state |
| [`backend/models/sig.py`](backend/models/sig.py) | Added `source_roots` to SIG | Share roots with A2/A3/A9 via Redis SIG key |
| [`backend/orchestrator/nodes.py`](backend/orchestrator/nodes.py) | `prepare_repo` calls `discover_source_roots` | Discover once before parallel Layer 1 |
| [`backend/services/ast_import_graph.py`](backend/services/ast_import_graph.py) | Optional `source_roots` param; uses `is_excluded_path` | Scope import graph to production code |
| [`backend/services/sig_helpers.py`](backend/services/sig_helpers.py) | Removed `production_prefixes`; role + root aware reachability | CVE reachability works on any repo layout |
| [`backend/agents/a1_semantic_mapper.py`](backend/agents/a1_semantic_mapper.py) | Pass roots to graph builder; store on SIG | A1 produces layout-aware SIG |
| [`backend/agents/a2_dependency_analyzer.py`](backend/agents/a2_dependency_analyzer.py) | Emit `source_roots` in completion payload | Observability only; reachability via sig_helpers |
| [`backend/agents/a3_static_analysis.py`](backend/agents/a3_static_analysis.py) | Dynamic scan targets + generalized stubs | SAST on any Python repo |
| [`backend/agents/a9_security_rescan.py`](backend/agents/a9_security_rescan.py) | Dynamic scan targets | Post-patch scan matches A3 scope |
| **NEW** [`tests/unit/test_repo_layout.py`](tests/unit/test_repo_layout.py) | Discovery matrix tests | Validates src/app/backend/flat layouts |
| [`tests/unit/test_sig_helpers.py`](tests/unit/test_sig_helpers.py) | Generic `src/myapp/` paths | Decouple tests from demo repo |
| [`tests/unit/test_mci.py`](tests/unit/test_mci.py) | Generic diff paths | Decouple tests from demo repo |
| [`tests/unit/test_models.py`](tests/unit/test_models.py) | Generic repo path | Decouple tests from demo repo |
| [`tests/fixtures/sample_sig.json`](tests/fixtures/sample_sig.json) | Generic SIG fixture | Decouple fixtures from demo repo |

**Not modified (intentionally):**

| File | Reason |
|------|--------|
| `backend/config.py` | GitHub PR target default, not source layout |
| `backend/agents/a7_code_generation.py` | Filename heuristics, not `vulnapi/` path assumptions |
| `vulnapi/` demo repo | Remains as demo target |
| `tests/integration/test_pipeline.py` | Uses vulnapi as valid test **input** fixture |

---

## Source Root Discovery

### Algorithm ([`backend/services/repo_layout.py`](backend/services/repo_layout.py))

1. **Container layouts:** `src/`, `app/`, `backend/`, `lib/` — detect packages within
2. **Top-level packages:** directories with `__init__.py` or `.py` modules (excluding tests, scripts, venv, etc.)
3. **Flat layout:** `*.py` at repo root → root prefix `""`
4. **Fallback:** `[""]` if nothing matched

### Example outputs

| Repository layout | Detected `source_roots` |
|-------------------|-------------------------|
| vulnapi demo (`vulnapi/vulnapi/*.py`) | `["vulnapi/"]` |
| src layout (`src/myapp/*.py`) | `["src/myapp/"]` |
| app layout (`app/*.py`) | `["app/"]` |
| flat (`main.py` at root) | `[""]` |

---

## API / Contract Preservation

- No FastAPI route signature changes
- LangGraph node graph unchanged (A0–A10 workflow preserved)
- `source_roots` added as optional field on `RunStateModel` and `SemanticIntentGraph` (backward compatible)
- Agent I/O Pydantic contracts unchanged; additive SIG field only

---

## Validation

- [x] `grep -r vulnapi backend/` — only `config.py` (GitHub default, out of scope)
- [x] Unit tests for `repo_layout`, `sig_helpers`, models, MCI
- [x] Integration test uses `./vulnapi` as fixture input (valid)
- [x] A2 reachability uses SIG `source_roots` + role filter (urllib3 decoy in vulnapi stays Informational when not imported)

---

## Shared Helpers

| Function | Used by |
|----------|---------|
| `discover_source_roots(repo_path)` | `prepare_repo`, A1 fallback |
| `get_scan_targets(state, repo, sig_data)` | A3, A9 |
| `resolve_source_roots(...)` | A3, A9 event payloads |
| `is_production_file(rel, roots)` | sig_helpers, ast_import_graph, iter_python_files |
| `iter_python_files(repo, roots)` | A3 stub scanners |
