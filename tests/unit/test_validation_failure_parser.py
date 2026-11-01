"""Unit tests for structured validation failure parsing."""

from pathlib import Path

from backend.services.validation_failure_parser import parse_validation_failure

SAMPLE_FAILED_TEST = {
    "nodeid": "tests/test_auth.py::test_expired_token_rejected",
    "lineno": 23,
    "outcome": "failed",
    "call": {
        "outcome": "failed",
        "crash": {
            "path": "/repo/vulnapi/tests/test_auth.py",
            "lineno": 27,
            "message": "AssertionError: assert True is False",
        },
        "traceback": [{"path": "tests/test_auth.py", "lineno": 27, "message": "AssertionError"}],
        "longrepr": ">       assert validate_token(token) is False\nE       assert True is False",
    },
}


def test_parse_validation_failure_from_json_report(tmp_path: Path):
    report = {
        "exitcode": 1,
        "summary": {"collected": 4, "failed": 1},
        "tests": [SAMPLE_FAILED_TEST],
    }
    report_path = tmp_path / "pytest_validation_run.json"
    report_path.write_text(__import__("json").dumps(report), encoding="utf-8")

    failure = parse_validation_failure(
        exit_code=1,
        stdout="FAILED tests/test_auth.py::test_expired_token_rejected",
        stderr="",
        report_path=report_path,
        validation_stage="mutation",
    )

    assert failure.failing_test == "tests/test_auth.py::test_expired_token_rejected"
    assert "assert True is False" in (failure.assertion_message or "")
    assert failure.expected_value == "validate_token(token) is False"
    assert failure.actual_value == "True"
    assert failure.validation_stage == "mutation"
    assert failure.traceback is not None


def test_parse_validation_failure_from_stdout_fallback():
    stdout = """
tests/test_auth.py::test_expired_token_rejected FAILED
>       assert validate_token(token) is False
E       AssertionError: assert True is False
"""
    failure = parse_validation_failure(
        exit_code=1,
        stdout=stdout,
        stderr="",
        validation_stage="mutation",
        failing_test_hint="tests/test_auth.py::test_expired_token_rejected",
    )

    assert failure.failing_test == "tests/test_auth.py::test_expired_token_rejected"
    assert failure.actual_value == "True"
    assert failure.expected_value == "False"
