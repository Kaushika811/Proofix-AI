"""Layer 5 proof-of-fix bundle assembly and GitHub Actions workflow generation.

This module makes ZERO LLM calls — verification is structurally independent of generation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from backend.models.proof import (
    VerificationBundle,
    VerificationStep,
)
from backend.state.schema import RunStateModel


def _canonical_steps_json(steps: list[VerificationStep]) -> str:
    payload = [step.model_dump(mode="json") for step in steps]
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def compute_bundle_hash(steps: list[VerificationStep]) -> str:
    return hashlib.sha256(_canonical_steps_json(steps).encode("utf-8")).hexdigest()


def _resolve_issue_id(state: RunStateModel) -> str:
    fix_dag = state.fix_dag or {}
    patch_bundle = state.patch_bundle or {}
    order = fix_dag.get("execution_order") or []
    if order:
        return str(order[0])
    if patch_bundle.get("issue_id"):
        return str(patch_bundle["issue_id"])
    return "fix-0"


def _reproduction_expected_before(reproduction: dict, is_targeted: bool) -> str:
    status = reproduction.get("status", "")
    if status == "CONFIRMED":
        return "exit code 1 (test fails)" if is_targeted else "exit code 1 (suite has failures)"
    if status == "UNCONFIRMED":
        return "exit code 0 (suite passes)" if not is_targeted else "exit code 0 (test passes)"
    return f"exit code non-zero or zero per A3.5 status {status}"


def _reproduction_expected_after(mutation: dict, is_targeted: bool) -> str:
    if mutation.get("pytest_passed"):
        return "exit code 0 (test passes)" if is_targeted else "exit code 0 (suite passes)"
    return "exit code non-zero (validation did not pass pytest)"


def _mutation_expected(mutation: dict) -> str:
    if not mutation.get("reexecution_command"):
        return "mutmut skipped or not run"
    if mutation.get("mutant_survived"):
        return "exit code non-zero (mutants survived)"
    if mutation.get("mutation_score") is None and not mutation.get("mutant_survived"):
        return "mutmut skipped or timeout (document actual A8 outcome)"
    return "exit code 0 (mutants killed)"


def _security_expected(security: dict) -> str:
    new_count = len(security.get("new_findings") or [])
    if new_count == 0:
        return "0 new findings vs baseline"
    return f"{new_count} new findings vs baseline (A9 recorded delta)"


def build_verification_bundle(
    state: RunStateModel,
    patch_commit: str = "",
) -> VerificationBundle:
    """Assemble verification steps from data already captured by upstream agents."""
    reproduction = state.reproduction or {}
    mutation = state.mutation_result or {}
    security = state.security_result or {}
    base_commit = state.base_commit_sha or ""
    issue_id = _resolve_issue_id(state)

    repro_cmd = reproduction.get("reexecution_command") or "python -m pytest -v --tb=long"
    reproduction_confidence = state.reproduction_confidence
    repro_targeted = reproduction_confidence == "exact_test"
    repro_timeout = int(reproduction.get("reexecution_timeout_seconds") or 120)

    mut_cmd = mutation.get("reexecution_command") or ""
    mut_timeout = int(mutation.get("reexecution_timeout_seconds") or 60)

    sec_cmd = security.get("reexecution_command") or ""
    sec_timeout = int(security.get("reexecution_timeout_seconds") or 150)

    steps = [
        VerificationStep(
            name="reproduction_before",
            command=repro_cmd,
            base_commit=base_commit,
            patch_commit=patch_commit,
            expected_result=_reproduction_expected_before(reproduction, repro_targeted),
            timeout_seconds=repro_timeout,
            is_targeted=repro_targeted,
        ),
        VerificationStep(
            name="reproduction_after",
            command=repro_cmd,
            base_commit=base_commit,
            patch_commit=patch_commit,
            expected_result=_reproduction_expected_after(mutation, repro_targeted),
            timeout_seconds=repro_timeout,
            is_targeted=repro_targeted,
        ),
        VerificationStep(
            name="mutation_test",
            command=mut_cmd,
            base_commit=base_commit,
            patch_commit=patch_commit,
            expected_result=_mutation_expected(mutation),
            timeout_seconds=mut_timeout,
            is_targeted=True,
        ),
        VerificationStep(
            name="security_delta",
            command=sec_cmd,
            base_commit=base_commit,
            patch_commit=patch_commit,
            expected_result=_security_expected(security),
            timeout_seconds=sec_timeout,
            is_targeted=True,
        ),
    ]

    bundle = VerificationBundle(
        issue_id=issue_id,
        steps=steps,
        reproduction_confidence=reproduction_confidence,
        created_at=datetime.utcnow(),
        llm_involved_in_verification=False,
    )
    bundle.bundle_hash = compute_bundle_hash(steps)
    return bundle


def write_proof_bundle(repo_path: str | Path, bundle: VerificationBundle) -> Path:
    root = Path(repo_path)
    out_dir = root / ".proof-of-fix"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{sanitize_workflow_id(bundle.issue_id)}.json"
    out_path.write_text(
        json.dumps(bundle.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )
    return out_path


def serialize_verification_bundle(bundle: VerificationBundle) -> str:
    return json.dumps(bundle.model_dump(mode="json"), indent=2, default=str)


def proof_artifact_hash(bundle: VerificationBundle) -> str:
    return hashlib.sha256(serialize_verification_bundle(bundle).encode("utf-8")).hexdigest()


def sanitize_workflow_id(issue_id: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", issue_id)
    return cleaned[:80] or "fix"


def _yaml_shell(cmd: str) -> str:
    return cmd.replace("\\", "\\\\").replace('"', '\\"')


def generate_verification_workflow(bundle: VerificationBundle) -> str:
    """Verify the current PR head contains exactly the tested code and proof."""
    workflow_id = sanitize_workflow_id(bundle.issue_id)
    proof_path = proof_bundle_relative_path(bundle)
    workflow_path = workflow_relative_path(bundle)
    artifact_hash = proof_artifact_hash(bundle)
    before = next(s for s in bundle.steps if s.name == "reproduction_before")
    after = next(s for s in bundle.steps if s.name == "reproduction_after")
    mutation = next(s for s in bundle.steps if s.name == "mutation_test")
    security = next(s for s in bundle.steps if s.name == "security_delta")

    repro_label = (
        "exact failing test"
        if bundle.reproduction_confidence == "exact_test"
        else "full suite (lower confidence — no single failing test identified)"
    )

    mut_if = "false" if not mutation.command else "true"
    sec_if = "false" if not security.command else "true"

    return f"""name: Verify Proof of Fix ({workflow_id})

on:
  pull_request:
    types: [opened, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write

jobs:
  verify-proof:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          ref: ${{{{ github.event.pull_request.head.sha }}}}

      - name: Verify current head and proof identity
        env:
          PR_HEAD: ${{{{ github.event.pull_request.head.sha }}}}
        run: |
          test "$(git rev-parse HEAD)" = "$PR_HEAD"
          test "$(sha256sum '{proof_path}' | cut -d' ' -f1)" = '{artifact_hash}'
          git cat-file -e '{after.patch_commit}^{{commit}}'
          git merge-base --is-ancestor '{after.patch_commit}' "$PR_HEAD"
          while IFS= read -r changed; do
            case "$changed" in
              '{proof_path}'|'{workflow_path}') ;;
              *) echo "Source or test changed after tested commit: $changed"; exit 1 ;;
            esac
          done < <(git diff --name-only '{after.patch_commit}' "$PR_HEAD")

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install verification tools
        run: |
          python -m pip install --upgrade pip
          pip install pytest pytest-json-report mutmut bandit semgrep

      - name: Reproduction before (base commit — expect failure)
        id: repro_before
        run: |
          git checkout {_yaml_shell(before.base_commit)}
          set +e
          timeout {before.timeout_seconds} bash -lc "{_yaml_shell(before.command)}"
          code=$?
          if [ "$code" -ne 1 ]; then
            echo "Expected a test failure at base commit {before.base_commit[:12]}, received exit code $code"
            exit 1
          fi

      - name: Reproduction after (patch commit — expect pass)
        id: repro_after
        run: |
          git checkout {_yaml_shell(after.patch_commit)}
          timeout {after.timeout_seconds} bash -lc "{_yaml_shell(after.command)}"

      - name: Mutation test (patch commit)
        id: mutation
        if: {mut_if}
        run: |
          git checkout {_yaml_shell(mutation.patch_commit)}
          timeout {mutation.timeout_seconds} bash -lc "{_yaml_shell(mutation.command)}"

      - name: Security delta (patch commit)
        id: security
        if: {sec_if}
        run: |
          git checkout {_yaml_shell(security.patch_commit)}
          timeout {security.timeout_seconds} bash -lc "{_yaml_shell(security.command)}"

      - name: Post verification summary
        if: always()
        uses: actions/github-script@v7
        with:
          github-token: ${{{{ secrets.GITHUB_TOKEN }}}}
          script: |
            const body = [
              '## Proof-of-fix verification (zero LLM calls)',
              '',
              'Reproduction mode: **{repro_label}**',
              '',
              '| Step | Pinned SHA | Expected | Result |',
              '|------|------------|----------|--------|',
              '| reproduction_before | `{before.base_commit[:12]}` | {before.expected_result} | ' + '${{{{ steps.repro_before.outcome }}}}' + ' |',
              '| reproduction_after | `{after.patch_commit[:12]}` | {after.expected_result} | ' + '${{{{ steps.repro_after.outcome }}}}' + ' |',
              '| mutation_test | `{mutation.patch_commit[:12]}` | {mutation.expected_result} | ' + '${{{{ steps.mutation.outcome }}}}' + ' |',
              '| security_delta | `{security.patch_commit[:12]}` | {security.expected_result} | ' + '${{{{ steps.security.outcome }}}}' + ' |',
              '',
              'Bundle hash: `{bundle.bundle_hash}`',
              '',
              'Tested code SHA is an ancestor of the current PR head; source and tests are unchanged since that SHA.',
            ].join('\\n');
            github.rest.issues.createComment({{
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body,
            }});
"""


def proof_bundle_relative_path(bundle: VerificationBundle) -> str:
    return f".proof-of-fix/{sanitize_workflow_id(bundle.issue_id)}.json"


def workflow_relative_path(bundle: VerificationBundle) -> str:
    workflow_id = sanitize_workflow_id(bundle.issue_id)
    return f".github/workflows/verify-{workflow_id}.yml"
