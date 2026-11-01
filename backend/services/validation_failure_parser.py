"""Parse pytest output into structured ValidationFailure records."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from backend.models.validation import ValidationFailure
from backend.services.reproduction_parser import load_pytest_report

TRACE_LIMIT = 5000
ASSERT_ACTUAL_EXPECTED_RE = re.compile(
    r"assert\s+(.+?)\s+is\s+(True|False|None)",
    re.IGNORECASE,
)
ASSERT_EQ_RE = re.compile(r"assert\s+(.+?)\s*==\s*(.+)")


def validation_report_path(run_id: str) -> Path:
    safe_id = run_id.replace("/", "_").replace("\\", "_")
    return Path(tempfile.gettempdir()) / f"pytest_validation_{safe_id}.json"


def parse_validation_failure(
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    report_path: Path | None = None,
    repo_root: Path | None = None,
    validation_stage: str = "mutation",
    mutation_result: dict | None = None,
    security_result: dict | None = None,
    failing_test_hint: str | None = None,
) -> ValidationFailure:
    report = load_pytest_report(report_path) if report_path else None
    failure = ValidationFailure(
        pytest_stdout=stdout[:TRACE_LIMIT],
        pytest_stderr=stderr[:TRACE_LIMIT],
        validation_stage=validation_stage,  # type: ignore[arg-type]
        mutation_result=mutation_result,
        security_result=security_result,
    )

    if report:
        failed = [t for t in report.get("tests", []) if t.get("outcome") in {"failed", "error"}]
        if failed:
            chosen = _pick_failed_test(failed, failing_test_hint)
            return _from_failed_test(
                chosen,
                failure,
                repo_root=repo_root,
            )

    text = stdout + "\n" + stderr
    return _from_text(text, failure, failing_test_hint=failing_test_hint)


def _from_failed_test(
    test: dict,
    failure: ValidationFailure,
    repo_root: Path | None,
) -> ValidationFailure:
    phase = next((p for p in ("setup", "call", "teardown")
                  if (test.get(p) or {}).get("outcome") == "failed"), "call")
    failure.pytest_phase = phase
    call = test.get(phase) or {}
    crash = call.get("crash") or {}
    longrepr = call.get("longrepr") or ""
    if isinstance(longrepr, dict):
        longrepr = longrepr.get("reprcrash", {}).get("message", "") or str(longrepr)

    traceback_text = longrepr if isinstance(longrepr, str) else ""
    tb_entries = call.get("traceback") or []
    if tb_entries and not traceback_text:
        traceback_text = "\n".join(
            f'  File "{e.get("path", "?")}", line {e.get("lineno", "?")}, in {e.get("message", "")}'
            for e in tb_entries
        )

    crash_message = crash.get("message") or ""
    assertion_message = crash_message or _extract_assertion_line(traceback_text)
    exception = re.search(r"\b([\w.]*(?:Error|Exception))\s*:", assertion_message or traceback_text)
    failure.exception_type = exception.group(1) if exception else None

    failure.failing_test = test.get("nodeid")
    failure.assertion_message = assertion_message or None
    failure.traceback = (traceback_text or failure.traceback or "")[:TRACE_LIMIT] or None

    expected, actual = _extract_expected_actual(assertion_message, traceback_text)
    failure.expected_value = expected
    failure.actual_value = actual
    return failure


def _from_text(
    text: str,
    failure: ValidationFailure,
    failing_test_hint: str | None = None,
) -> ValidationFailure:
    node_match = re.search(r"(tests/\S+::\S+)", text)
    failure.failing_test = failing_test_hint or (node_match.group(1) if node_match else None)

    assertion_line = _extract_assertion_line(text)
    failure.assertion_message = assertion_line or None
    failure.traceback = text[:TRACE_LIMIT] if text.strip() else failure.traceback

    expected, actual = _extract_expected_actual(assertion_line or "", text)
    failure.expected_value = expected
    failure.actual_value = actual
    return failure


def _extract_assertion_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("E ") and "assert" in stripped:
            return stripped[2:].strip()
        if "AssertionError" in stripped:
            return stripped
    match = re.search(r"(AssertionError: .+)", text)
    return match.group(1).strip() if match else ""


def _extract_expected_actual(assertion_message: str, context: str) -> tuple[str | None, str | None]:
    combined = f"{assertion_message}\n{context}"

    err_is_match = re.search(r"E\s+assert\s+(\S+)\s+is\s+(True|False|None)", combined, re.I)
    src_is_match = re.search(r">\s+assert\s+(.+?)\s+is\s+(True|False|None)", combined, re.I)
    if err_is_match and src_is_match:
        actual = err_is_match.group(1)
        expected = src_is_match.group(2)
        subject = src_is_match.group(1).strip()
        return f"{subject} is {expected}", actual

    if err_is_match:
        actual = err_is_match.group(1)
        expected = err_is_match.group(2)
        return expected, actual

    eq_match = ASSERT_EQ_RE.search(combined)
    if eq_match:
        return eq_match.group(2).strip(), eq_match.group(1).strip()

    simple = re.search(r"assert\s+(\S+)\s+is\s+(True|False|None)", combined, re.I)
    if simple:
        return simple.group(2), simple.group(1)

    return None, None


def _pick_failed_test(failed: list[dict], failing_test_hint: str | None) -> dict:
    if failing_test_hint:
        for test in failed:
            if test.get("nodeid") == failing_test_hint:
                return test
    return failed[0]
