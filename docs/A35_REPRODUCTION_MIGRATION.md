# A3.5 Reproduction Agent — Migration Notes

## Summary

A3.5 now produces **structured runtime evidence** with explicit status classification and run-specific pytest JSON reports.

## Breaking vs backward-compatible changes

### Backward compatible (preserved)

| Field | Behavior |
|-------|----------|
| `reproduced` | Auto-derived from `status` (`CONFIRMED` → `True`) |
| `stack_trace` | Mirrored from `traceback` when only one is set |
| `force_draft_pr` | Auto-set: `False` for `CONFIRMED`, `True` otherwise |
| `failing_test` | Still populated on confirmed reproduction |
| `confidence` | Still populated (0.9 JSON, 0.7 text fallback) |
| `state.reproduction` dict contract | Same Redis/state key; additive fields only |

### New fields

| Field | Type | Description |
|-------|------|-------------|
| `status` | `ReproductionStatus` | `CONFIRMED`, `UNCONFIRMED`, `INFRA_ERROR`, `NO_TESTS` |
| `exception_type` | `str \| null` | e.g. `AssertionError` |
| `exception_message` | `str \| null` | Exception detail |
| `failing_file` | `str \| null` | Repo-relative path |
| `failing_line` | `int \| null` | Line number |
| `traceback` | `str \| null` | Full pytest longrepr (preferred) |
| `report_path` | `str \| null` | Run-specific JSON report path |
| `infra_detail` | `str \| null` | Error detail for infra failures |

## Report path change

| Before | After |
|--------|-------|
| `/tmp/pytest_report.json` (shared) | `{tempdir}/pytest_{run_id}.json` (run-specific) |

Concurrent pipeline runs no longer overwrite each other's pytest reports.

## Status semantics

| Status | Meaning | `force_draft_pr` |
|--------|---------|------------------|
| `CONFIRMED` | At least one failing test — vulnerability reproduced | `False` |
| `UNCONFIRMED` | Tests ran, all passed | `True` |
| `INFRA_ERROR` | Subprocess failure, missing report, pytest internal error | `True` |
| `NO_TESTS` | Zero tests collected (`exitcode` 5) | `True` |

## Downstream consumer updates

| Consumer | Change |
|----------|--------|
| **A4** | Prefers `traceback` over `stack_trace`; uses `failing_file`/`failing_line` for stub citations |
| **A10** | Status-specific draft PR review notes for `UNCONFIRMED`, `INFRA_ERROR`, `NO_TESTS` |
| **API `/runs`** | Unchanged — still exposes `force_draft_pr` from state |

## New module

`backend/services/reproduction_parser.py` — pytest JSON parsing and status classification (unit-tested independently of subprocess).

## Upgrade checklist

1. Consumers reading `stack_trace` should prefer `traceback` (both populated for compatibility).
2. Gate logic should use `status` or `reproduced` — not raw pytest exit codes.
3. Do not rely on `/tmp/pytest_report.json`; use `report_path` from `ReproductionResult` if needed for debugging.
4. Run `pytest tests/unit/test_reproduction_parser.py -v` after upgrade.

## Files modified

- `backend/models/reproduction.py`
- `backend/services/reproduction_parser.py` (new)
- `backend/agents/a3_5_reproduction.py`
- `backend/agents/a4_evidence_investigator.py`
- `backend/agents/a10_mci_scorer.py`
- `tests/unit/test_reproduction_parser.py` (new)
- `tests/unit/test_models.py`
