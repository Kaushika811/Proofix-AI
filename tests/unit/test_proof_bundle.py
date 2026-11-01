"""Unit tests for Layer 5 proof bundle assembly."""


import pytest
from pydantic import ValidationError

from backend.models.proof import VerificationBundle
from backend.services.proof_bundle import (
    build_verification_bundle,
    generate_verification_workflow,
)
from backend.state.schema import RunStateModel


def _sample_state(**overrides) -> RunStateModel:
    base = {
        "run_id": "run-1",
        "repo_path": "/tmp/repo",
        "base_commit_sha": "abc123" * 5 + "abcd",
        "reproduction": {
            "status": "CONFIRMED",
            "reexecution_command": "python -m pytest tests/t.py::test_x -v --tb=long",
            "reexecution_is_targeted": True,
            "reexecution_timeout_seconds": 120,
        },
        "mutation_result": {
            "pytest_passed": True,
            "mutant_survived": False,
            "reexecution_command": "python -m mutmut run --paths-to-mutate src/a.py && python -m mutmut results",
            "reexecution_timeout_seconds": 60,
        },
        "security_result": {
            "rejected": False,
            "new_findings": [],
            "reexecution_command": "bandit -f json -q -r 'src/' && semgrep --config=auto --json 'src/'",
            "reexecution_timeout_seconds": 150,
        },
        "fix_dag": {"execution_order": ["finding-1"]},
        "reproduction_confidence": "exact_test",
    }
    base.update(overrides)
    return RunStateModel(**base)


def test_build_bundle_exact_test_confidence():
    bundle = build_verification_bundle(_sample_state(), patch_commit="patch" * 8 + "pppp")
    assert bundle.reproduction_confidence == "exact_test"
    assert bundle.llm_involved_in_verification is False
    before = next(s for s in bundle.steps if s.name == "reproduction_before")
    assert before.is_targeted is True
    assert "test fails" in before.expected_result


def test_build_bundle_full_suite_confidence():
    state = _sample_state(
        reproduction={
            "status": "CONFIRMED",
            "reexecution_command": "python -m pytest -v --tb=long",
            "reexecution_is_targeted": False,
            "reexecution_timeout_seconds": 120,
        },
        reproduction_confidence="full_suite",
    )
    bundle = build_verification_bundle(state, patch_commit="patch" * 8 + "pppp")
    assert bundle.reproduction_confidence == "full_suite"
    before = next(s for s in bundle.steps if s.name == "reproduction_before")
    assert before.is_targeted is False
    assert "suite has failures" in before.expected_result


def test_bundle_hash_changes_when_is_targeted_flips():
    state = _sample_state()
    bundle_a = build_verification_bundle(state, patch_commit="c" * 40)
    state.reproduction_confidence = "full_suite"
    bundle_b = build_verification_bundle(state, patch_commit="c" * 40)
    assert bundle_a.bundle_hash != bundle_b.bundle_hash


def test_bundle_reproduction_confidence_matches_state():
    state = _sample_state(reproduction_confidence="exact_test")
    bundle = build_verification_bundle(state, patch_commit="c" * 40)
    assert bundle.reproduction_confidence == state.reproduction_confidence


def test_bundle_hash_is_deterministic():
    state = _sample_state()
    b1 = build_verification_bundle(state, patch_commit="d" * 40)
    b2 = build_verification_bundle(state, patch_commit="d" * 40)
    assert b1.bundle_hash == b2.bundle_hash


def test_literal_false_cannot_be_true():
    bundle = VerificationBundle(issue_id="x", steps=[], bundle_hash="h")
    assert bundle.llm_involved_in_verification is False
    with pytest.raises(ValidationError):
        VerificationBundle(issue_id="x", steps=[], bundle_hash="h", llm_involved_in_verification=True)


def test_workflow_checks_current_head_and_pins_tested_patch():
    bundle = build_verification_bundle(_sample_state(), patch_commit="e" * 40)
    yaml_text = generate_verification_workflow(bundle)
    assert "github.event.pull_request.head.sha" in yaml_text
    assert "git merge-base --is-ancestor" in yaml_text
    assert "paths:" not in yaml_text
    assert bundle.steps[0].base_commit in yaml_text
    assert bundle.steps[1].patch_commit in yaml_text
    assert "ANTHROPIC" not in yaml_text.upper()


def test_proof_bundle_module_has_no_llm_imports():
    from pathlib import Path

    source = Path("backend/services/proof_bundle.py").read_text(encoding="utf-8").lower()
    assert "anthropic" not in source
    assert "openai" not in source
    assert "llm" not in source.split("zero llm")[0] or "zero llm calls" in source
