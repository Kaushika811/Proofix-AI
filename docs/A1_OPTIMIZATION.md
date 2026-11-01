# A1 Semantic Mapper Optimization

Production optimization of Agent A1 to reduce LLM usage and AST work while preserving identical downstream behaviour for A2–A10.

## Before → After

| Metric | Before (live mode) | After (typical) |
|--------|-------------------|-----------------|
| LLM calls per run | 1 per production file (e.g. **120**) | **0–1** (batch ambiguous only) |
| AST parses per run | 1 per file | **N** on cache miss, **0** on cache hit |
| LLM reduction | — | **~80–90%** on typical repos |
| Repeat run (same commit) | Full re-classify | Cache hit: **0 parse, 0 LLM** |

### vulnapi example (6 production files, ~1 ambiguous)

| | Before | After (miss) | After (hit) |
|---|--------|--------------|-------------|
| LLM calls | 6 | 0–1 | 0 |
| AST parses | 6 | 6 | 0 |

---

## Architecture

```
scan production files
  → parse_python_file() once per file  → ParsedModule
  → build ImportGraph from ParsedModules
  → role_classifier (filename → AST → ambiguous queue)
  → 0 or 1 batch LLM call
  → recompute churn + criticality
  → SemanticIntentGraph (unchanged schema)
```

Shared parser: [`backend/services/python_ast_parser.py`](../backend/services/python_ast_parser.py)

---

## Classification pipeline

### Stage 1 — Filename (`role_source: filename`, confidence 1.0)

Obvious paths: `auth`, `middleware`, `api`, `config`, `database`, etc.

### Stage 2 — AST (`role_source: ast`, confidence 0.60–0.90)

Signals from `ParsedModule`: imports, functions, classes, decorators, docstring.

### Stage 3 — Ambiguous basenames

Default set includes `engine.py`, `core.py`, `utils.py`, etc. Queued for batch LLM unless AST confidence ≥ `ROLE_HIGH_CONFIDENCE_THRESHOLD` (0.95).

### Batch LLM (`role_source: llm`)

One structured call for entire ambiguous queue. On failure: AST fallback at confidence 0.50 — pipeline never stops.

---

## SIG cache

**Redis key:** `sig_cache:v1:{repo_hash}`

**Repo hash (git):** `SHA256(HEAD + worktree_diff_hash + source_roots)`

**Cached:** roles, imports, edges, exported symbols, full `ParsedModule` per file

**Never cached:** churn, criticality, `generated_at`

**Cache hit:** zero parses, zero LLM — only churn/criticality recomputed.

**Stub mode:** cache read/write skipped entirely.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ROLE_CONFIDENCE_THRESHOLD` | `0.85` | Accept local classification |
| `ROLE_HIGH_CONFIDENCE_THRESHOLD` | `0.95` | Override ambiguous basename gate |
| `SIG_CACHE_ENABLED` | `true` | Enable cross-run SIG cache |
| `SIG_CACHE_TTL` | `604800` | Cache TTL (seconds, 7 days) |
| `ALWAYS_LLM_FILENAMES` | (built-in set) | Comma-separated override |

---

## Metrics (`a1_metrics` on A1 completed event)

| Field | Description |
|-------|-------------|
| `total_files` | Files in SIG |
| `filename_classified` / `ast_classified` / `llm_classified` | Count by `role_source` |
| `ambiguous_files` | Files queued for LLM |
| `llm_calls` | 0 or 1 |
| `batch_size` | Ambiguous files in batch LLM call |
| `llm_calls_saved` | vs per-file baseline |
| `cache_hit` / `cache_miss` | Cache result |
| `cache_version` | e.g. `v1` |
| `cache_age_seconds` | Age of cached payload |
| `cached_files` | Files loaded from cache |
| `parse_count` | AST parses this run (0 on full cache hit) |
| `ast_build_ms`, `role_classification_ms`, `cache_lookup_ms`, `llm_ms`, `total_a1_ms` | Timing |

---

## Failure handling

Batch LLM failure → log warning → AST fallback (`confidence=0.50`, `role_source=ast`) → A1 completes normally.

---

## Migration notes

- **Transparent to A2–A10:** `SemanticIntentGraph` schema unchanged
- **Stub mode:** unchanged behaviour (local classifier only, no cache)
- **WebSocket:** additive `a1_metrics` on A1 `completed` payload only
