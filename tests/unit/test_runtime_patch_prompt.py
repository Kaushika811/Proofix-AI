"""Unit tests for runtime-evidence A7 patch prompt construction."""

from pathlib import Path

import pytest

from backend.models.blast import BlastGraphResult
from backend.models.patch import PatchPlan
from backend.models.root_cause import Citation, RootCauseBrief
from backend.models.validation import RetryBrief
from backend.services.runtime_patch_prompt import (
    build_retry_prompt_section,
    build_runtime_patch_prompt,
    derive_runtime_behaviors,
    enrich_patch_plan_from_runtime,
    extract_relevant_code,
    has_placeholder_text,
    has_semantic_diff,
    infer_target_function,
    is_abbreviated_patch,
    is_no_op_patch,
    missing_top_level_definitions,
    validate_patch_integrity,
)

VULNAPI = Path(__file__).parent.parent.parent / "vulnapi"
AUTH_SOURCE = (VULNAPI / "vulnapi/auth.py").read_text(encoding="utf-8")


@pytest.fixture
def root_cause() -> RootCauseBrief:
    return RootCauseBrief(
        summary="Expired JWT tokens are accepted",
        root_cause="validate_token() accepts expired JWT tokens because expiry (exp) is never validated.",
        citations=[
            Citation(file="vulnapi/auth.py", line=19, claim="missing expiry validation", verified=False),
        ],
        stack_evidence='File "vulnapi/auth.py", line 19, in validate_token',
    )


@pytest.fixture
def reproduction() -> dict:
    return {
        "status": "CONFIRMED",
        "failing_test": "tests/test_auth.py::test_expired_token_rejected",
        "failing_file": "tests/test_auth.py",
        "exception_type": "AssertionError",
        "exception_message": "assert True is False",
        "traceback": 'File "tests/test_auth.py", line 27, in test_expired_token_rejected',
    }


@pytest.fixture
def blast() -> BlastGraphResult:
    return BlastGraphResult(
        origins=["vulnapi/auth.py"],
        auto_patch_scope=["vulnapi/auth.py"],
    )


@pytest.fixture
def base_plan() -> PatchPlan:
    return PatchPlan(
        file="vulnapi/auth.py",
        root_cause="validate_token() accepts expired JWT tokens because expiry (exp) is never validated.",
        required_behavior_change="Reject expired tokens",
    )


def test_enrich_patch_plan_from_vulnapi_fixtures(base_plan, root_cause, reproduction, blast):
    enriched = enrich_patch_plan_from_runtime(
        base_plan,
        root_cause,
        reproduction,
        blast,
        source=AUTH_SOURCE,
        repo_path=VULNAPI,
    )
    assert enriched.target_file == "vulnapi/auth.py"
    assert enriched.target_function == "validate_token"
    assert enriched.failing_test == "tests/test_auth.py::test_expired_token_rejected"
    assert "pytest tests/test_auth.py::test_expired_token_rejected passes" in enriched.acceptance_criteria
    assert enriched.runtime_evidence
    assert enriched.expected_behavior


def test_runtime_evidence_in_prompt(base_plan, root_cause, reproduction, blast):
    plan = enrich_patch_plan_from_runtime(
        base_plan, root_cause, reproduction, blast, source=AUTH_SOURCE, repo_path=VULNAPI
    )
    prompt = build_runtime_patch_prompt(plan, AUTH_SOURCE, "", str(VULNAPI))
    assert "CONFIRMED" in prompt or "failing_test" in prompt or "Runtime Evidence" in prompt
    assert "test_expired_token_rejected" in prompt


def test_target_function_in_prompt(base_plan, root_cause, reproduction, blast):
    plan = enrich_patch_plan_from_runtime(
        base_plan, root_cause, reproduction, blast, source=AUTH_SOURCE, repo_path=VULNAPI
    )
    prompt = build_runtime_patch_prompt(plan, AUTH_SOURCE, "", str(VULNAPI))
    assert "validate_token" in prompt
    assert "## Target Function" in prompt


def test_failing_test_in_acceptance_criteria(base_plan, root_cause, reproduction, blast):
    plan = enrich_patch_plan_from_runtime(
        base_plan, root_cause, reproduction, blast, source=AUTH_SOURCE, repo_path=VULNAPI
    )
    assert "pytest tests/test_auth.py::test_expired_token_rejected passes" in plan.acceptance_criteria


def test_expected_behaviour_in_prompt(base_plan, root_cause, reproduction, blast):
    plan = enrich_patch_plan_from_runtime(
        base_plan, root_cause, reproduction, blast, source=AUTH_SOURCE, repo_path=VULNAPI
    )
    prompt = build_runtime_patch_prompt(plan, AUTH_SOURCE, "", str(VULNAPI))
    assert "exp" in prompt.lower() or "Reject" in prompt


def test_no_op_patch_detection():
    original = "def validate_token():\n    return True\n"
    assert is_no_op_patch(original, original) is True
    assert is_no_op_patch(original, original + " ") is True
    assert is_no_op_patch(original, "def validate_token():\n    return False\n") is False


def test_semantic_diff_detected():
    original = AUTH_SOURCE
    patched = original.replace(
        "# Missing: if payload.get(\"exp\", 0) < time.time(): return False",
        "if payload.get(\"exp\", 0) < time.time():\n        return False",
    )
    assert has_semantic_diff(original, patched) is True


def test_retry_prompt_includes_validation_failure():
    from backend.models.validation import ValidationFailure

    failure = ValidationFailure(
        failing_test="tests/test_auth.py::test_expired_token_rejected",
        assertion_message="AssertionError: assert True is False",
        expected_value="False",
        actual_value="True",
        validation_stage="mutation",
    )
    retry = RetryBrief(
        attempt=1,
        assertion_failure="AssertionError: assert True is False",
        violated_contract="token expiry must be checked",
        validation_failure=failure,
        expected_behaviour="validate_token(expired_token) is False",
        actual_behaviour="True",
        retry_instruction=(
            "Previous attempt still accepted expired JWT tokens.\n"
            "pytest `tests/test_auth.py::test_expired_token_rejected` failed.\n"
            "Generate a DIFFERENT implementation."
        ),
    )
    previous = {
        "original": AUTH_SOURCE,
        "patched": AUTH_SOURCE,
    }
    section = build_retry_prompt_section(retry, {"pytest_passed": False}, previous, 1)
    assert "Validation failure" in section
    assert "test_expired_token_rejected" in section
    assert "Expected:" in section
    assert "Actual: True" in section
    assert "DIFFERENT implementation" in section


def test_retry_prompt_empty_on_first_attempt(base_plan, root_cause, reproduction, blast):
    plan = enrich_patch_plan_from_runtime(
        base_plan, root_cause, reproduction, blast, source=AUTH_SOURCE, repo_path=VULNAPI
    )
    prompt = build_runtime_patch_prompt(plan, AUTH_SOURCE, "", str(VULNAPI), complete_original=AUTH_SOURCE)
    section = build_retry_prompt_section(RetryBrief(attempt=1), None, None, 0)
    assert section == ""
    assert "Retry — previous attempt failed" not in prompt


def test_extract_relevant_code_scopes_to_function():
    scoped = extract_relevant_code(AUTH_SOURCE, "validate_token")
    assert "def validate_token" in scoped
    assert "get_token_subject" not in scoped


def test_infer_target_function_from_root_cause(base_plan, root_cause, reproduction):
    fn = infer_target_function(base_plan, root_cause, reproduction, AUTH_SOURCE)
    assert fn == "validate_token"


def test_derive_runtime_behaviors(root_cause, reproduction):
    current, expected, acceptance = derive_runtime_behaviors(root_cause, reproduction)
    assert "CONFIRMED" in current or "AssertionError" in current
    assert "exp" in expected.lower()
    assert "test_expired_token_rejected" in acceptance


def test_has_placeholder_text_detects_abbreviation():
    abbreviated = AUTH_SOURCE + "\n# ... remainder of file unchanged (validate_token is the only function to modify) ..."
    assert has_placeholder_text(abbreviated) is True
    assert has_placeholder_text("# unchanged\npass") is True
    assert has_placeholder_text(AUTH_SOURCE) is False


def test_missing_top_level_definitions():
    abbreviated = """
import base64
import json
import time

def validate_token(token: str) -> bool:
    return True
"""
    missing = missing_top_level_definitions(AUTH_SOURCE, abbreviated)
    assert "decode_token" in missing
    assert "get_token_subject" in missing


def test_is_abbreviated_patch():
    abbreviated = AUTH_SOURCE.split("def get_token_subject")[0] + "# ... remainder of file unchanged ..."
    assert is_abbreviated_patch(AUTH_SOURCE, abbreviated) is True

    patched = AUTH_SOURCE.replace(
        "# Missing: if payload.get(\"exp\", 0) < time.time(): return False",
        "if payload.get(\"exp\", 0) < time.time():\n        return False",
    )
    assert is_abbreviated_patch(AUTH_SOURCE, patched) is False


def test_validate_patch_integrity_rejects_abbreviated():
    abbreviated = "import time\n\ndef validate_token():\n    return True\n\n# ... remainder unchanged ..."
    ok, reason = validate_patch_integrity(AUTH_SOURCE, abbreviated)
    assert ok is False
    assert reason == "abbreviated"


def test_validate_patch_integrity_accepts_complete_fix():
    patched = AUTH_SOURCE.replace(
        "# Missing: if payload.get(\"exp\", 0) < time.time(): return False",
        "if payload.get(\"exp\", 0) < time.time():\n        return False",
    )
    ok, reason = validate_patch_integrity(AUTH_SOURCE, patched)
    assert ok is True
    assert reason is None


def test_runtime_prompt_includes_complete_original(base_plan, root_cause, reproduction, blast):
    plan = enrich_patch_plan_from_runtime(
        base_plan, root_cause, reproduction, blast, source=AUTH_SOURCE, repo_path=VULNAPI
    )
    prompt = build_runtime_patch_prompt(plan, AUTH_SOURCE, "", str(VULNAPI), complete_original=AUTH_SOURCE)
    assert "Original complete file" in prompt
    assert "decode_token" in prompt
    assert "get_token_subject" in prompt
    assert "Never use placeholders" in prompt


def test_extract_relevant_code_does_not_add_placeholder():
    scoped = extract_relevant_code(AUTH_SOURCE, "validate_token")
    assert "remainder of file unchanged" not in scoped
