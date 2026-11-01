"""Unit tests for two-phase scoped A8 validation."""

from unittest.mock import AsyncMock, patch
from backend.services.acceptance_test_integrity import pin_acceptance_test

import pytest

from backend.services.reproduction_parser import extract_failed_nodeids
from backend.services.scoped_validation import compare_new_failures, run_scoped_validation


def test_extract_failed_nodeids():
    report = {
        "tests": [
            {"nodeid": "tests/test_auth.py::test_ok", "outcome": "passed"},
            {"nodeid": "tests/test_auth.py::test_fail", "outcome": "failed"},
            {"nodeid": "tests/test_config.py::test_secret_from_env", "outcome": "failed"},
        ]
    }
    assert extract_failed_nodeids(report) == [
        "tests/test_auth.py::test_fail",
        "tests/test_config.py::test_secret_from_env",
    ]


def test_compare_new_failures_ignores_pre_existing():
    baseline = {
        "tests/test_auth.py::test_expired_token_rejected",
        "tests/test_config.py::test_secret_from_env",
    }
    current = {
        "tests/test_config.py::test_secret_from_env",
    }
    assert compare_new_failures(current, baseline) == []


def test_compare_new_failures_detects_regression():
    baseline = {"tests/test_auth.py::test_expired_token_rejected"}
    current = {
        "tests/test_auth.py::test_expired_token_rejected",
        "tests/test_api.py::test_new_break",
    }
    assert compare_new_failures(current, baseline) == ["tests/test_api.py::test_new_break"]


TARGET_FAIL_STDOUT = "FAILED tests/test_auth.py::test_expired_token_rejected"
TARGET_PASS_STDOUT = "passed"
REGRESSION_STDOUT = "FAILED tests/test_config.py::test_secret_from_env"


@pytest.mark.asyncio
async def test_target_failure_triggers_patch_retry(tmp_path):
    repo = tmp_path

    async def fake_run(cmd, cwd, timeout):
        joined = " ".join(cmd)
        if "test_expired_token_rejected" in joined:
            return 1, TARGET_FAIL_STDOUT, "E AssertionError: assert True is False"
        return 0, REGRESSION_STDOUT, ""

    with patch("backend.services.scoped_validation.run_command", AsyncMock(side_effect=fake_run)):
        with patch(
            "backend.services.scoped_validation.load_pytest_report",
            side_effect=[
                {
                    "tests": [
                        {
                            "nodeid": "tests/test_auth.py::test_expired_token_rejected",
                            "outcome": "failed",
                            "call": {
                                "crash": {"message": "AssertionError: assert True is False"},
                                "longrepr": "E AssertionError: assert True is False",
                            },
                        }
                    ]
                },
            ],
        ):
            outcome = await run_scoped_validation(
                repo,
                "run-1",
                target_test="tests/test_auth.py::test_expired_token_rejected",
                baseline_failures=[
                    "tests/test_auth.py::test_expired_token_rejected",
                    "tests/test_config.py::test_secret_from_env",
                ],
            )

    assert outcome.target_test_passed is False
    assert outcome.patch_retry_required is True
    assert outcome.failure_brief_needed is True
    assert outcome.validation_failure is not None
    assert outcome.validation_failure.failing_test == "tests/test_auth.py::test_expired_token_rejected"


@pytest.mark.asyncio
async def test_changed_acceptance_test_stops_before_pytest(tmp_path):
    path = tmp_path / "tests" / "test_issue.py"
    path.parent.mkdir()
    path.write_text("def test_issue():\n    assert False\n", encoding="utf-8")
    pinned = pin_acceptance_test(tmp_path, "tests/test_issue.py::test_issue")
    path.write_text("def test_issue():\n    assert True\n", encoding="utf-8")
    with patch("backend.services.scoped_validation.run_command", new_callable=AsyncMock) as runner:
        outcome = await run_scoped_validation(
            tmp_path, "tampered", target_test=pinned["nodeid"],
            baseline_failures=[], acceptance_test=pinned,
        )
    runner.assert_not_awaited()
    assert outcome.pytest_available is False
    assert outcome.target_test_passed is None
    assert "changed" in outcome.validation_failure.assertion_message


@pytest.mark.asyncio
async def test_acceptance_test_changed_during_pytest_cannot_pass(tmp_path):
    path = tmp_path / "tests" / "test_issue.py"
    path.parent.mkdir()
    path.write_text("def test_issue():\n    assert False\n", encoding="utf-8")
    pinned = pin_acceptance_test(tmp_path, "tests/test_issue.py::test_issue")

    async def fake_run(*args, **kwargs):
        path.write_text("def test_issue():\n    assert True\n", encoding="utf-8")
        return 0, "1 passed", ""

    with patch("backend.services.scoped_validation.run_command", AsyncMock(side_effect=fake_run)) as runner:
        outcome = await run_scoped_validation(
            tmp_path, "tampered-midrun", target_test=pinned["nodeid"],
            baseline_failures=[], acceptance_test=pinned,
        )
    runner.assert_awaited_once()
    assert outcome.pytest_available is False
    assert outcome.target_test_passed is None
    assert "changed" in outcome.validation_failure.assertion_message


@pytest.mark.asyncio
async def test_pre_existing_config_failure_does_not_trigger_patch_retry(tmp_path):
    repo = tmp_path
    call_count = {"n": 0}

    async def fake_run(cmd, cwd, timeout):
        call_count["n"] += 1
        joined = " ".join(cmd)
        if "test_expired_token_rejected" in joined:
            return 0, TARGET_PASS_STDOUT, ""
        return 1, REGRESSION_STDOUT, ""

    with patch("backend.services.scoped_validation.run_command", AsyncMock(side_effect=fake_run)):
        with patch(
            "backend.services.scoped_validation.load_pytest_report",
            side_effect=[
                {"tests": [{"nodeid": "tests/test_auth.py::test_expired_token_rejected", "outcome": "passed"}]},
                {
                    "tests": [
                        {"nodeid": "tests/test_config.py::test_secret_from_env", "outcome": "failed"},
                    ]
                },
            ],
        ):
            outcome = await run_scoped_validation(
                repo,
                "run-2",
                target_test="tests/test_auth.py::test_expired_token_rejected",
                baseline_failures=[
                    "tests/test_auth.py::test_expired_token_rejected",
                    "tests/test_config.py::test_secret_from_env",
                ],
            )

    assert outcome.target_test_passed is True
    assert outcome.regression_tests_passed is True
    assert outcome.pytest_passed is True
    assert outcome.patch_retry_required is False
    assert outcome.failure_brief_needed is False
    assert outcome.new_failures == []
    assert call_count["n"] == 2


# -- pytest ran, or pytest could not run --------------------------------------


def test_pytest_ran_distinguishes_a_failure_from_an_absence():
    """The distinction A8's correctness score depends on.

    Exit code 1 with real output is a verdict about the code. Exit code 1 with
    "No module named pytest" is the absence of a verdict, and scoring it as one
    published a hard zero for a patch nothing had examined.
    """
    from backend.services.scoped_validation import pytest_ran

    # A genuine test failure — pytest ran and reported.
    assert pytest_ran(1, "1 failed, 3 passed in 0.4s", "") is True
    assert pytest_ran(0, "4 passed in 0.3s", "") is True

    # pytest was never importable in the target repository.
    assert pytest_ran(1, "", "/usr/bin/python3: No module named pytest") is False
    assert pytest_ran(1, "", "ModuleNotFoundError: No module named 'pytest'") is False

    # pytest's own "I could not run" exit codes: 4 usage error, 5 nothing collected.
    assert pytest_ran(4, "", "") is False
    assert pytest_ran(5, "no tests ran in 0.01s", "") is False

    # An interrupted or crashed run did execute; it is not an absence of pytest.
    assert pytest_ran(2, "", "KeyboardInterrupt") is True
    assert pytest_ran(3, "", "INTERNALERROR") is True
