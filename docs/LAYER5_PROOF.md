# Layer 5 — Proof-of-Fix Verification

Layer 5 ships **re-executable proof** in every PR — verification runs with **zero LLM calls**.

## Artifacts per fix

| File | Purpose |
|------|---------|
| `.proof-of-fix/{issue_id}.json` | Tamper-evident `VerificationBundle` (SHA256 hash over steps) |
| `.github/workflows/verify-{issue_id}.yml` | GitHub Actions workflow — literal SHAs from bundle JSON only |

## Key guarantees

- `llm_involved_in_verification: false` — structurally fixed in Pydantic (`Literal[False]`)
- Workflow checkouts use **bundle `base_commit` / `patch_commit`** — never `github.event.pull_request.head.sha`
- `is_targeted` + `reproduction_confidence` distinguish exact-test vs full-suite fallback proof

## Routing cap

`reproduction_confidence` is derived once in `apply_trust_gates_before_pr()` (before A10) and stored on `RunStateModel`. `full_suite` is included in `trust_gates_block_auto_merge()` — routing caps at `diff_only`, never `auto_mergeable`. `build_verification_bundle()` reads the same state field (no re-derivation).

## API

```
GET /runs/{run_id}/proof/{issue_id}
```

## Hard gate

```bash
pytest tests/unit/test_reproduction_stability_gate.py -m proof_gate -v
```

Must pass 10/10 identical exit codes before trusting reproduction proof.

## Upstream capture

| Agent | Field |
|-------|-------|
| A3.5 | `reexecution_command`, `reexecution_is_targeted` |
| A8 | `reexecution_command`, `pytest_reexecution_command` |
| A9 | `reexecution_command` (shell-quoted paths) |
| prepare_repo | `base_commit_sha` |
