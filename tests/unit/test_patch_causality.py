"""Fault 4: structural and executable evidence for a selected patch."""

from unittest.mock import AsyncMock, patch

import pytest

from backend.services.acceptance_test_integrity import pin_acceptance_test
from backend.services.patch_causality import evaluate_patch_causality
from backend.services.runtime_patch_prompt import changed_python_symbols, validate_patch_integrity


ORIGINAL = "def check(value):\n    return False\n\ndef unrelated():\n    return 1\n"
PATCHED = "def check(value):\n    return value > 0\n\ndef unrelated():\n    return 2\n"
TEST = "from pkg.logic import check\n\ndef test_issue():\n    assert check(1) is True\n"


def _repo(tmp_path, patched=PATCHED):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "logic.py").write_text(patched)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_issue.py").write_text(TEST)
    return pin_acceptance_test(tmp_path, "tests/test_issue.py::test_issue")


def _bundle(patched=PATCHED):
    return [{"file": "pkg/logic.py", "original": ORIGINAL, "patched": patched,
             "target_symbol": "check"}]


@pytest.mark.parametrize("patched", [
    "# explanation only\n" + ORIGINAL,
    ORIGINAL.replace("return False", "return  False"),
])
def test_comments_and_formatting_are_not_behavioral(patched):
    assert validate_patch_integrity(ORIGINAL, patched, file="pkg/logic.py") == (
        False, "non_behavioral_change"
    )


def test_unrelated_function_rejected_but_target_change_accepted():
    unrelated = ORIGINAL.replace("return 1", "return 2")
    assert validate_patch_integrity(ORIGINAL, unrelated, file="pkg/logic.py",
                                    target_symbol="check") == (False, "unrelated_target_symbol")
    assert validate_patch_integrity(ORIGINAL, PATCHED, file="pkg/logic.py",
                                    target_symbol="check") == (True, None)
    assert changed_python_symbols(ORIGINAL, PATCHED) == {"check", "unrelated"}


@pytest.mark.asyncio
async def test_original_fails_patch_passes_and_target_reversion_restores_failure(tmp_path):
    pinned = _repo(tmp_path)
    evidence = await evaluate_patch_causality(tmp_path, _bundle(), pinned, target_symbol="check")
    assert evidence["behavioral_change_detected"] is True
    assert evidence["patch_relevance_established"] is True
    assert evidence["original"]["outcome"] == "behavioral_assertion_failed"
    assert evidence["patched"]["outcome"] == "passed"
    assert evidence["counterfactual"]["outcome"] == "behavioral_assertion_failed"
    assert evidence["causal_effect_supported"] is True
    assert evidence["acceptance_test_passed"] is True
    assert evidence["source_hashes"]["pkg/logic.py"]["counterfactual"]
    assert evidence["test_hash"] == pinned["sha256"]


@pytest.mark.asyncio
async def test_counterfactual_pass_means_selected_change_was_not_causal(tmp_path):
    original = "def helper():\n    return False\n\ndef check(value):\n    return helper()\n"
    patched = "def helper():\n    return True\n\ndef check(value):\n    return bool(helper())\n"
    pinned = _repo(tmp_path, patched)
    evidence = await evaluate_patch_causality(tmp_path, [{
        "file": "pkg/logic.py", "original": original, "patched": patched,
        "target_symbol": "check",
    }], pinned, target_symbol="check")
    assert evidence["original"]["outcome"] == "behavioral_assertion_failed"
    assert evidence["patched"]["outcome"] == "passed"
    assert evidence["counterfactual"]["outcome"] == "passed"
    assert evidence["causal_effect_supported"] is False


@pytest.mark.asyncio
async def test_modified_acceptance_test_stops_without_execution(tmp_path):
    pinned = _repo(tmp_path)
    (tmp_path / "tests" / "test_issue.py").write_text(TEST.replace("True", "False"))
    with patch("backend.services.patch_causality.run_command", new_callable=AsyncMock) as runner:
        evidence = await evaluate_patch_causality(tmp_path, _bundle(), pinned, target_symbol="check")
    runner.assert_not_awaited()
    assert evidence["causal_effect_supported"] is None
    assert "changed" in evidence["reason"]


@pytest.mark.asyncio
async def test_execution_error_is_inconclusive(tmp_path):
    pinned = _repo(tmp_path)
    with patch("backend.services.patch_causality.run_command",
               AsyncMock(return_value=(-1, "", "timeout"))):
        evidence = await evaluate_patch_causality(tmp_path, _bundle(), pinned, target_symbol="check")
    assert evidence["original"]["outcome"] == "execution_error"
    assert evidence["causal_effect_supported"] is None


@pytest.mark.asyncio
async def test_unstable_test_is_inconclusive(tmp_path):
    pinned = _repo(tmp_path)
    calls = 0

    async def unstable(cmd, cwd, timeout):
        nonlocal calls
        calls += 1
        report = next(item.split("=", 1)[1] for item in cmd if item.startswith("--json-report-file="))
        import json
        from pathlib import Path
        failed = calls % 2 == 1
        Path(report).write_text(json.dumps({"tests": [{
            "nodeid": pinned["nodeid"], "outcome": "failed" if failed else "passed",
            "call": {"outcome": "failed" if failed else "passed",
                     "crash": {"message": "AssertionError: unstable"} if failed else {}},
        }]}))
        return (1 if failed else 0), "", ""

    with patch("backend.services.patch_causality.run_command", AsyncMock(side_effect=unstable)):
        evidence = await evaluate_patch_causality(tmp_path, _bundle(), pinned, target_symbol="check")
    assert evidence["original"]["outcome"] == "nondeterministic"
    assert evidence["causal_effect_supported"] is None


@pytest.mark.asyncio
async def test_dynamic_expanded_operands_do_not_change_failure_identity(tmp_path):
    _repo(tmp_path)
    (tmp_path / "tests/test_issue.py").write_text(
        "import os\nfrom pkg.logic import check\n\ndef test_issue():\n"
        "    assert check(os.getpid()) is True\n"
    )
    pinned = pin_acceptance_test(tmp_path, "tests/test_issue.py::test_issue")
    evidence = await evaluate_patch_causality(
        tmp_path, _bundle(), pinned, target_symbol="check",
        expected_failure="assert False is True\n + where False = check(123)",
        expected_failure_file="tests/test_issue.py", expected_failure_line=5,
    )
    assert evidence["original"]["assertion"] != evidence["counterfactual"]["assertion"]
    assert evidence["causal_effect_supported"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("expected,line", [
    ("assert True is False", 4),
    ("assert False is True", 99),
])
async def test_different_baseline_assertion_or_location_cannot_confirm(tmp_path, expected, line):
    pinned = _repo(tmp_path)
    evidence = await evaluate_patch_causality(
        tmp_path, _bundle(), pinned, target_symbol="check",
        expected_failure=expected, expected_failure_file="tests/test_issue.py",
        expected_failure_line=line,
    )
    assert evidence["causal_effect_supported"] is None
    assert "differs" in evidence["reason"]


@pytest.mark.asyncio
async def test_different_counterfactual_assertion_location_cannot_confirm(tmp_path):
    original = "def check(value):\n    return False\n\ndef unrelated():\n    return True\n"
    patched = "def check(value):\n    return True\n\ndef unrelated():\n    return False\n"
    _repo(tmp_path, patched)
    (tmp_path / "tests/test_issue.py").write_text(
        "from pkg.logic import check, unrelated\n\ndef test_issue():\n"
        "    assert unrelated() is False\n    assert check(1) is True\n"
    )
    pinned = pin_acceptance_test(tmp_path, "tests/test_issue.py::test_issue")
    evidence = await evaluate_patch_causality(tmp_path, [{
        "file": "pkg/logic.py", "original": original, "patched": patched,
        "target_symbol": "check",
    }], pinned)
    assert evidence["patched"]["outcome"] == "passed"
    assert evidence["causal_effect_supported"] is None
