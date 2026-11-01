# A7 Code Generation — Migration Notes

## Summary

`A7CodeGenerationAgent` was refactored from filename-heuristic patch generation to an evidence-driven **PatchPlan → LLM → PatchCandidate** pipeline. No changes were made to A4, A5, A6, A8, or A9 contracts.

## What changed

### Removed

| Removed logic | Location (before) |
|---------------|-------------------|
| `if "auth" in file_path` token expiry injection | `_stub_patch` |
| `if "api" in file_path` SQLi string replace | `_stub_patch` |
| `if "config" in file_path` hardcoded secret replace | `_stub_patch` |
| `if "utils" in file_path` pickle → json swap | `_stub_patch` |
| `if "middleware" in file_path` ADMIN_MODE disable | `_stub_patch` |
| `_apply_security_fix` string-replace path traversal hack | `_generate_patch` |
| Filename-based fallback in LLM error path | `_generate_patch` |

### Added

| Component | Purpose |
|-----------|---------|
| `PatchPlan` model ([`backend/models/patch.py`](../backend/models/patch.py)) | Structured repair intent per file |
| [`backend/agents/a7_patch_engine.py`](../backend/agents/a7_patch_engine.py) | Plan building, prompt construction, stub generation |
| [`tests/unit/test_a7_patch_engine.py`](../tests/unit/test_a7_patch_engine.py) | Unit tests for plan engine |

## New data flow

```
RootCauseBrief + BlastGraphResult + RetryBrief + scope files
        ↓
   build_patch_plans() → list[PatchPlan]
        ↓
   build_llm_prompt(plan, original, style_exemplar)
        ↓
   LLM structured output → PatchLLMOutput
        ↓
   ast.parse validation → PatchCandidate + BehavioralContract
        ↓
   PatchBundle (unchanged contract)
```

## PatchPlan fields

| Field | Source |
|-------|--------|
| `file` | Blast graph `auto_patch_scope` or static finding |
| `root_cause` | `RootCauseBrief.root_cause` / `summary` |
| `required_behavior_change` | Root cause + file-specific citation claim |
| `security_constraints` | `RetryBrief.security_constraint` |
| `validation_goals` | `RetryBrief.violated_contract`, `assertion_failure`, stack hints |
| `stack_evidence` | `RootCauseBrief.stack_evidence` |
| `blast_context` | Matching `BlastGraphResult.scope` entries |

## Stub mode behavior (changed)

**Before:** Filename heuristics modified files differently per path (`auth.py`, `config.py`, etc.).

**After:** `apply_stub_plan()` returns **original content unchanged** and derives the behavioral contract from the `PatchPlan` only. This keeps stub mode repository-agnostic; integration tests still complete the pipeline without path-specific mutations.

## Production mode

Set `STUB_MODE=false` and provide `ANTHROPIC_API_KEY`. Patches are generated exclusively from `PatchPlan` + original file content via the LLM — no filename rules.

## Unchanged

- `PatchBundle`, `PatchCandidate`, `BehavioralContract` schemas (additive `PatchPlan` only)
- AST validation (`ast.parse`)
- Diff generation via `generate_diff_from_patches`
- Redis `patches` key storage
- A7 lock acquisition / file write behavior
- LangGraph node wiring (`generate_code`)

## Upgrade checklist

1. Ensure A4 produces populated `RootCauseBrief` with citations before A7 runs.
2. Ensure A5 populates `BlastGraphResult.auto_patch_scope`.
3. On validation retry, A8/A9 `RetryBrief` fields flow into `PatchPlan.security_constraints` and `validation_goals`.
4. Run `pytest tests/unit/test_a7_patch_engine.py` after deploy.

## Breaking changes

None for external API consumers. Internal: any code importing `_stub_patch` or `_apply_security_fix` from A7 will fail — those private methods were removed.
