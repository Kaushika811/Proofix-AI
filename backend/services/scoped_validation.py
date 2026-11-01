"""Two-phase scoped pytest validation for A8."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.models.validation import ValidationFailure
from backend.services.acceptance_test_integrity import acceptance_test_integrity_error
from backend.services.issue_test_generation import generated_test_integrity_error, run_with_fresh_bytecode
from backend.services.reproduction_commands import FULL_SUITE_COMMAND, build_targeted_reproduction_command
from backend.services.reproduction_parser import extract_failed_nodeids, load_pytest_report
from backend.services.subprocess_runner import PYTHON, run_command
from backend.services.validation_failure_parser import parse_validation_failure, validation_report_path


def validation_regression_report_path(run_id: str) -> Path:
    return validation_report_path(run_id).with_name(
        validation_report_path(run_id).name.replace(".json", "_regression.json")
    )


def compare_new_failures(current_failures: set[str], baseline_failures: set[str]) -> list[str]:
    return sorted(current_failures - baseline_failures)


#: pytest itself never started. `python -m pytest` exits 1 with this on stderr
#: when the target repository has no pytest installed, which is the ordinary
#: case for a cloned repository whose dependencies were never installed.
_PYTEST_MISSING_MARKERS = (
    "No module named pytest",
    "No module named 'pytest'",
)

#: pytest's own exit codes for "I could not run", as opposed to "tests failed":
#: 4 is a usage error, 5 is "no tests collected". Neither is evidence about a
#: patch. (0 pass, 1 tests failed, 2 interrupted, 3 internal error.)
_PYTEST_CANNOT_RUN_CODES = frozenset({4, 5})


def pytest_ran(exit_code: int, stdout: str, stderr: str) -> bool:
    """Did pytest actually execute tests?

    A non-zero exit is not by itself a verdict on a patch. If pytest was never
    importable, or collected nothing, then nothing was measured — and a score
    derived from it would be a failing grade for work that was never examined.
    This is the distinction A8 needs and could not previously make.
    """
    if exit_code in _PYTEST_CANNOT_RUN_CODES:
        return False
    blob = f"{stdout}\n{stderr}"
    return not any(marker in blob for marker in _PYTEST_MISSING_MARKERS)


def _pytest_collection_completed(exit_code: int, stdout: str, stderr: str, report: dict | None) -> bool:
    """Whether pytest completed collection and produced usable evidence."""
    if not pytest_ran(exit_code, stdout, stderr) or report is None:
        return False
    blob = f"{stdout}\n{stderr}".lower()
    return not any(marker in blob for marker in (
        "error during collection",
        "importerror while loading conftest",
        "importerror while importing test module",
        "modulenotfounderror",
        "collection failed",
    ))


@dataclass
class ScopedValidationOutcome:
    target_test_passed: bool | None
    regression_tests_passed: bool | None
    pytest_passed: bool
    patch_retry_required: bool
    new_failures: list[str]
    pre_existing_failures: list[str]
    validation_failure: ValidationFailure | None
    failure_brief_needed: bool
    pytest_reexecution_command: str
    target_stdout: str = ""
    target_stderr: str = ""
    regression_stdout: str = ""
    regression_stderr: str = ""
    #: False when pytest never ran. `pytest_passed` is False in that case too,
    #: but for a different reason, and only this field tells them apart.
    #: Defaults True so an outcome built by an older caller is unchanged.
    pytest_available: bool = True


async def run_scoped_validation(
    repo: Path,
    run_id: str,
    *,
    target_test: str | None,
    baseline_failures: list[str],
    timeout: int = 120,
    generated_test: dict | None = None,
    acceptance_test: dict | None = None,
) -> ScopedValidationOutcome:
    baseline = set(baseline_failures)
    integrity_error = generated_test_integrity_error(repo, generated_test)
    if integrity_error:
        return _unverified_generated_test(integrity_error, target_test, baseline_failures)
    integrity_error = acceptance_test_integrity_error(repo, acceptance_test)
    if integrity_error:
        return _unverified_generated_test(integrity_error, target_test, baseline_failures)

    if not target_test:
        return await _run_full_suite_fallback(repo, run_id, baseline_failures, timeout)

    target_report_path = validation_report_path(run_id)
    if target_report_path.exists():
        target_report_path.unlink()

    target_cmd = build_targeted_reproduction_command(target_test)
    target_args = [
        PYTHON,
        "-m",
        "pytest",
        target_test,
        "-v",
        "--tb=short",
        "--json-report",
        f"--json-report-file={target_report_path}",
    ]
    runner = run_with_fresh_bytecode if generated_test else run_command
    target_code, target_stdout, target_stderr = await runner(target_args, cwd=repo, timeout=timeout)
    integrity_error = acceptance_test_integrity_error(repo, acceptance_test)
    if integrity_error:
        return _unverified_generated_test(integrity_error, target_test, baseline_failures)
    target_test_passed = target_code == 0
    if generated_test:
        integrity_error = generated_test_integrity_error(repo, generated_test)
        if integrity_error:
            return _unverified_generated_test(integrity_error, target_test, baseline_failures)
        report = load_pytest_report(target_report_path) or {}
        selected = next((t for t in report.get("tests", []) if t.get("nodeid") == target_test), {})
        if target_code == 0 and (selected.get("outcome") != "passed" or
                                (selected.get("call") or {}).get("outcome") != "passed"):
            return _unverified_generated_test(
                "Generated test did not execute and pass; skipped or missing tests are unverified",
                target_test, baseline_failures,
            )

    if not target_test_passed:
        validation_failure = parse_validation_failure(
            exit_code=target_code,
            stdout=target_stdout,
            stderr=target_stderr,
            report_path=target_report_path,
            repo_root=repo,
            validation_stage="mutation",
            failing_test_hint=target_test,
        )
        validation_failure = validation_failure.model_copy(
            update={
                "target_test_passed": False,
                "regression_tests_passed": None,
                "new_failures": [],
                "pre_existing_failures": sorted(baseline),
            }
        )
        return ScopedValidationOutcome(
            target_test_passed=False,
            regression_tests_passed=None,
            pytest_passed=False,
            patch_retry_required=True,
            new_failures=[],
            pre_existing_failures=sorted(baseline),
            validation_failure=validation_failure,
            failure_brief_needed=True,
            pytest_reexecution_command=target_cmd,
            target_stdout=target_stdout,
            target_stderr=target_stderr,
            pytest_available=pytest_ran(target_code, target_stdout, target_stderr),
        )

    regression_report_path = validation_regression_report_path(run_id)
    if regression_report_path.exists():
        regression_report_path.unlink()

    regression_args = [
        PYTHON,
        "-m",
        "pytest",
        "-v",
        "--tb=short",
        "--json-report",
        f"--json-report-file={regression_report_path}",
    ]
    regression_code, regression_stdout, regression_stderr = await runner(
        regression_args,
        cwd=repo,
        timeout=timeout,
    )
    regression_report = load_pytest_report(regression_report_path)
    integrity_error = generated_test_integrity_error(repo, generated_test)
    if integrity_error:
        return _unverified_generated_test(integrity_error, target_test, baseline_failures)
    integrity_error = acceptance_test_integrity_error(repo, acceptance_test)
    if integrity_error:
        return _unverified_generated_test(integrity_error, target_test, baseline_failures)
    collection_completed = _pytest_collection_completed(
        regression_code, regression_stdout, regression_stderr, regression_report
    )
    current_failures = set(extract_failed_nodeids(regression_report))
    new_failures = compare_new_failures(current_failures, baseline)
    pre_existing_still_failing = sorted(current_failures & baseline)
    # Existing baseline failures may remain, but collection/import failures are
    # never evidence of a passing regression suite.
    regression_tests_passed = collection_completed and len(new_failures) == 0
    pytest_passed = regression_tests_passed

    validation_failure = None
    if not regression_tests_passed:
        validation_failure = parse_validation_failure(
            exit_code=regression_code,
            stdout=regression_stdout,
            stderr=regression_stderr,
            report_path=regression_report_path,
            repo_root=repo,
            validation_stage="mutation",
            failing_test_hint=new_failures[0] if new_failures else None,
        )
        validation_failure = validation_failure.model_copy(
            update={
                "target_test_passed": True,
                "regression_tests_passed": False,
                "new_failures": new_failures,
                "pre_existing_failures": sorted(baseline),
            }
        )

    return ScopedValidationOutcome(
        target_test_passed=True,
        regression_tests_passed=regression_tests_passed,
        pytest_passed=pytest_passed,
        patch_retry_required=False,
        new_failures=new_failures,
        pre_existing_failures=pre_existing_still_failing,
        validation_failure=validation_failure,
        failure_brief_needed=False,
        pytest_reexecution_command=f"{target_cmd} && {FULL_SUITE_COMMAND}",
        target_stdout=target_stdout,
        target_stderr=target_stderr,
        regression_stdout=regression_stdout,
        regression_stderr=regression_stderr,
    )


def _unverified_generated_test(reason: str, target_test: str | None, baseline: list[str]) -> ScopedValidationOutcome:
    return ScopedValidationOutcome(
        target_test_passed=None, regression_tests_passed=None, pytest_passed=False,
        patch_retry_required=False, new_failures=[], pre_existing_failures=baseline,
        validation_failure=ValidationFailure(failing_test=target_test, assertion_message=reason),
        failure_brief_needed=False, pytest_reexecution_command="", pytest_available=False,
    )


async def _run_full_suite_fallback(
    repo: Path,
    run_id: str,
    baseline_failures: list[str],
    timeout: int,
) -> ScopedValidationOutcome:
    report_path = validation_report_path(run_id)
    if report_path.exists():
        report_path.unlink()

    code, stdout, stderr = await run_command(
        [
            PYTHON,
            "-m",
            "pytest",
            "-v",
            "--tb=short",
            "--json-report",
            f"--json-report-file={report_path}",
        ],
        cwd=repo,
        timeout=timeout,
    )
    report = load_pytest_report(report_path)
    collection_completed = _pytest_collection_completed(code, stdout, stderr, report)
    current_failures = set(extract_failed_nodeids(report))
    baseline = set(baseline_failures)
    new_failures = compare_new_failures(current_failures, baseline) if baseline else sorted(current_failures)
    pytest_passed = collection_completed and (code == 0 if not baseline else len(new_failures) == 0)

    validation_failure = None
    failure_brief_needed = False
    if not pytest_passed:
        validation_failure = parse_validation_failure(
            exit_code=code,
            stdout=stdout,
            stderr=stderr,
            report_path=report_path,
            repo_root=repo,
            validation_stage="mutation",
        )
        validation_failure = validation_failure.model_copy(
            update={
                "target_test_passed": None,
                "regression_tests_passed": pytest_passed,
                "new_failures": new_failures,
                "pre_existing_failures": sorted(baseline),
            }
        )
        failure_brief_needed = bool(new_failures or not baseline)

    return ScopedValidationOutcome(
        target_test_passed=None,
        regression_tests_passed=pytest_passed,
        pytest_passed=pytest_passed,
        patch_retry_required=failure_brief_needed,
        new_failures=new_failures,
        pre_existing_failures=sorted(current_failures & baseline) if baseline else [],
        validation_failure=validation_failure,
        failure_brief_needed=failure_brief_needed,
        pytest_reexecution_command=FULL_SUITE_COMMAND,
        regression_stdout=stdout,
        regression_stderr=stderr,
        pytest_available=pytest_ran(code, stdout, stderr),
    )
