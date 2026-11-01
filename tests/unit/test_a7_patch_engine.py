import pytest

from backend.agents.a7_patch_engine import (
    PatchLLMOutput,
    apply_stub_plan,
    build_llm_prompt,
    build_patch_plans,
    contract_from_plan,
)
from backend.models.blast import BlastGraphResult, ScopedFile
from backend.models.patch import PatchPlan
from backend.models.root_cause import Citation, RootCauseBrief
from backend.models.validation import RetryBrief


@pytest.fixture
def root_cause() -> RootCauseBrief:
    return RootCauseBrief(
        summary="Token expiry not validated",
        root_cause="validate_token() skips exp check",
        citations=[
            Citation(file="src/myapp/auth.py", line=42, claim="missing expiry validation", verified=True),
        ],
        stack_evidence="File src/myapp/auth.py, line 42, in validate_token",
        affected_modules=["src/myapp/auth.py"],
    )


@pytest.fixture
def blast() -> BlastGraphResult:
    return BlastGraphResult(
        scope=[
            ScopedFile(path="src/myapp/auth.py", direction="forward", propagation_confidence=0.85, risk_score=0.7),
        ],
        auto_patch_scope=["src/myapp/auth.py"],
    )


def test_build_patch_plans(root_cause, blast):
    plans = build_patch_plans(["src/myapp/auth.py"], root_cause, blast, None)
    assert len(plans) == 1
    plan = plans[0]
    assert plan.file == "src/myapp/auth.py"
    assert "validate_token" in plan.root_cause
    assert "missing expiry validation" in plan.required_behavior_change
    assert "line 42" in plan.stack_evidence or "validate_token" in plan.stack_evidence


def test_build_patch_plans_with_retry_brief(root_cause, blast):
    retry = RetryBrief(
        attempt=1,
        security_constraint="must not introduce path traversal near utils.py:10",
        violated_contract="token.expiry must be checked",
        assertion_failure="AssertionError: expired token accepted",
    )
    plans = build_patch_plans(["src/myapp/auth.py"], root_cause, blast, retry)
    plan = plans[0]
    assert "path traversal" in plan.security_constraints[0]
    assert any("token.expiry" in g for g in plan.validation_goals)


def test_apply_stub_plan_does_not_use_filename():
    plan = PatchPlan(
        file="src/myapp/auth.py",
        root_cause="fix expiry",
        required_behavior_change="check exp before validation",
    )
    original = "def validate_token():\n    return True\n"
    output = apply_stub_plan(plan, original)
    assert output.patched_content == original
    assert "check exp" in output.contract_assertion or "expiry" in output.contract_assertion.lower() or "behavior" in output.contract_assertion.lower()


def test_apply_stub_plan_same_for_any_path():
    """Stub output must not vary based on filename alone."""
    original = "x = 1\n"
    plan_auth = PatchPlan(file="src/myapp/auth.py", root_cause="r", required_behavior_change="c")
    plan_utils = PatchPlan(file="src/myapp/utils.py", root_cause="r", required_behavior_change="c")
    assert apply_stub_plan(plan_auth, original).patched_content == apply_stub_plan(plan_utils, original).patched_content


def test_build_llm_prompt_includes_plan_fields(root_cause, blast):
    plan = build_patch_plans(["src/myapp/auth.py"], root_cause, blast, None)[0]
    prompt = build_llm_prompt(plan, "def foo(): pass", "# exemplar")
    assert "Root cause:" in prompt
    assert "Security constraints" in prompt
    assert "Validation goals" in prompt
    assert "src/myapp/auth.py" in prompt


def test_contract_from_plan():
    plan = PatchPlan(
        file="pkg/module.py",
        root_cause="bug",
        required_behavior_change="fix input validation",
        validation_goals=["assert sanitized input"],
    )
    contract = contract_from_plan(plan)
    assert contract.location == "pkg/module.py"
    assert "sanitized input" in contract.assertion


def test_patch_llm_output_model():
    out = PatchLLMOutput(
        patched_content="def f(): return 1",
        contract_assertion="must return 1",
        contract_location="pkg/f.py",
    )
    assert out.patched_content.startswith("def")


def test_validate_python_ast_gate():
    import ast

    def validate_python(code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False

    assert validate_python("def ok(): pass\n") is True
    assert validate_python("def broken(:\n") is False
