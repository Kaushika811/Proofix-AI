"""Parse pytest JSON reports into structured reproduction evidence."""

from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from backend.models.reproduction import ReproductionResult, ReproductionStatus

TRACE_LIMIT = 5000


def pytest_report_path(run_id: str) -> Path:
    safe_id = run_id.replace("/", "_").replace("\\", "_")
    return Path(tempfile.gettempdir()) / f"pytest_{safe_id}.json"


def load_pytest_report(report_path: Path) -> dict | None:
    if not report_path.exists():
        return None
    try:
        return json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def parse_pytest_report(
    report: dict | None,
    exit_code: int,
    stdout: str,
    stderr: str,
    report_path: Path,
    repo_root: Path | None = None,
    requested_test: str | None = None,
) -> ReproductionResult:
    """Classify reproduction outcome and extract runtime evidence from pytest output."""
    report_path_str = str(report_path)
    result = _classify(report, exit_code, stdout, stderr, report_path_str, repo_root, requested_test)
    result.requested_test = requested_test

    # Execution metadata every branch shares — attached once here rather than
    # repeated at each return, so no branch can forget to carry it.
    result.exit_code = exit_code
    result.timed_out = exit_code == -1 and stderr.strip() == "timeout"
    result.stdout = stdout[:TRACE_LIMIT] if stdout else None
    result.stderr = stderr[:TRACE_LIMIT] if stderr else None
    if report is not None:
        summary = report.get("summary") or {}
        result.tests_collected = summary.get("collected")
        result.tests_passed = summary.get("passed")
        result.tests_failed = summary.get("failed")
        if isinstance(report.get("duration"), (int, float)):
            result.duration_seconds = float(report["duration"])
    return result


def _classify(
    report: dict | None,
    exit_code: int,
    stdout: str,
    stderr: str,
    report_path_str: str,
    repo_root: Path | None,
    requested_test: str | None,
) -> ReproductionResult:
    if exit_code == -1:
        detail = stderr.strip() or stdout.strip() or "pytest subprocess failed"
        return ReproductionResult(
            status=ReproductionStatus.INFRA_ERROR,
            confidence=0.0,
            report_path=report_path_str,
            infra_detail=detail[:500],
        )

    if report is None:
        detail = "pytest JSON report missing or unreadable"
        if stderr.strip():
            detail = stderr.strip()[:500]
        # A text traceback cannot prove which collected test corresponds to a
        # developer-selected issue. Never substitute an unrelated failure.
        fallback = None if requested_test else _extract_from_text(stdout + stderr, repo_root)
        if fallback:
            fallback.report_path = report_path_str
            fallback.infra_detail = detail
            return fallback
        return ReproductionResult(
            status=ReproductionStatus.INFRA_ERROR,
            confidence=0.0,
            report_path=report_path_str,
            infra_detail=detail,
        )

    exitcode = report.get("exitcode", exit_code)
    summary = report.get("summary") or {}
    collected = summary.get("collected")
    if collected is None:
        collected = len(report.get("tests") or [])

    if exitcode == 5 or collected == 0:
        return ReproductionResult(
            status=ReproductionStatus.NO_TESTS,
            confidence=0.0,
            report_path=report_path_str,
            infra_detail="No tests collected by pytest",
        )

    if exitcode in (2, 3, 4):
        detail = stderr.strip() or stdout.strip() or f"pytest exit code {exitcode}"
        return ReproductionResult(
            status=ReproductionStatus.INFRA_ERROR,
            confidence=0.0,
            report_path=report_path_str,
            infra_detail=detail[:500],
        )

    tests = report.get("tests") or []
    if requested_test:
        selected = next((t for t in tests if t.get("nodeid") == requested_test), None)
        if selected is None:
            return ReproductionResult(
                status=ReproductionStatus.UNCONFIRMED,
                confidence=0.0,
                report_path=report_path_str,
                infra_detail=f"Requested test was not collected: {requested_test}",
            )
        if selected.get("outcome") not in {"failed", "error"}:
            return ReproductionResult(
                status=ReproductionStatus.UNCONFIRMED,
                confidence=0.0,
                report_path=report_path_str,
                infra_detail=f"Requested test did not fail: {requested_test}",
            )
        return _from_failed_test(selected, report_path_str, repo_root)

    failed = [t for t in tests if t.get("outcome") in {"failed", "error"}]
    if failed:
        return _from_failed_test(failed[0], report_path_str, repo_root)

    if exitcode == 0:
        return ReproductionResult(
            status=ReproductionStatus.UNCONFIRMED,
            confidence=0.0,
            report_path=report_path_str,
        )

    fallback = _extract_from_text(stdout + stderr, repo_root)
    if fallback:
        fallback.report_path = report_path_str
        return fallback

    return ReproductionResult(
        status=ReproductionStatus.INFRA_ERROR,
        confidence=0.0,
        report_path=report_path_str,
        infra_detail=f"Unexpected pytest exit code {exitcode}",
    )


def _from_failed_test(
    test: dict,
    report_path: str,
    repo_root: Path | None,
) -> ReproductionResult:
    for phase in ("setup", "teardown"):
        if (test.get(phase) or {}).get("outcome") == "failed":
            return ReproductionResult(
                status=ReproductionStatus.INFRA_ERROR, confidence=0.0,
                failing_test=test.get("nodeid"), report_path=report_path,
                infra_detail=f"Test {phase} failed; application behavior was not established",
            )
    call = test.get("call") or {}
    crash = call.get("crash") or {}
    traceback_text = call.get("longrepr") or _format_traceback(call.get("traceback") or [])

    failing_file = None
    failing_line = crash.get("lineno") or test.get("lineno")
    tb = call.get("traceback") or []
    if tb and tb[0].get("path"):
        # pytest's own traceback frames carry absolute filesystem paths, not
        # repo-relative ones — this branch previously used the raw path
        # unmodified, leaking the host's directory layout into `failing_file`.
        failing_file = _relative_path(tb[0]["path"], repo_root)
        if tb[0].get("lineno"):
            failing_line = tb[0]["lineno"]

    if not failing_file:
        raw_path = crash.get("path") or ""
        if raw_path:
            failing_file = _relative_path(raw_path, repo_root)

    crash_message = crash.get("message") or ""
    exception_type, exception_message = _split_exception(crash_message)
    if not exception_type:
        tb_entries = call.get("traceback") or [{}]
        tb_msg = tb_entries[0].get("message") if tb_entries else None
        if tb_msg:
            exception_type, exception_message = _split_exception(tb_msg)

    if exception_type and exception_type.rsplit(".", 1)[-1] in {
        "ImportError", "ModuleNotFoundError", "PackageNotFoundError",
        "DistributionNotFound", "ConnectionError", "TimeoutError",
        "OperationalError", "DatabaseError", "FileNotFoundError",
    }:
        return ReproductionResult(
            status=ReproductionStatus.INFRA_ERROR, confidence=0.0,
            failing_test=test.get("nodeid"), report_path=report_path,
            exception_type=exception_type, exception_message=exception_message,
            infra_detail=f"Runtime prerequisite failed: {exception_type}: {exception_message}",
        )

    clipped_tb = traceback_text[:TRACE_LIMIT] if traceback_text else None

    return ReproductionResult(
        status=ReproductionStatus.CONFIRMED,
        failing_test=test.get("nodeid"),
        exception_type=exception_type or None,
        exception_message=exception_message or None,
        failing_file=failing_file,
        failing_line=failing_line,
        traceback=clipped_tb,
        stack_trace=clipped_tb,
        confidence=0.9,
        report_path=report_path,
        evidence_source="pytest_report",
    )


def _extract_from_text(text: str, repo_root: Path | None) -> ReproductionResult | None:
    if not text.strip():
        return None

    node_match = re.search(r"(tests/\S+::\S+)", text)
    file_match = re.search(r'File "([^"]+)", line (\d+)', text)
    exc_match = re.search(r"(\w+Error|\w+Exception|Failed): (.+)", text)

    if not node_match and not file_match and exit_code_hint(text) is False:
        return None

    failing_file = None
    failing_line = None
    if file_match:
        raw = file_match.group(1)
        failing_file = _relative_path(raw, repo_root)
        failing_line = int(file_match.group(2))

    exception_type = None
    exception_message = None
    if exc_match:
        exception_type = exc_match.group(1)
        exception_message = exc_match.group(2).strip()

    clipped = text[:TRACE_LIMIT]
    return ReproductionResult(
        status=ReproductionStatus.CONFIRMED,
        failing_test=node_match.group(1) if node_match else None,
        exception_type=exception_type,
        exception_message=exception_message,
        failing_file=failing_file,
        failing_line=failing_line,
        traceback=clipped,
        stack_trace=clipped,
        confidence=0.7,
        evidence_source="output_text",
    )


def exit_code_hint(text: str) -> bool:
    return bool(re.search(r"FAILED|AssertionError|Error|Exception", text))


def _split_exception(message: str) -> tuple[str, str]:
    if ": " in message:
        etype, msg = message.split(": ", 1)
        return etype.strip(), msg.strip()
    return message.strip(), ""


def _format_traceback(entries: list[dict]) -> str:
    lines = []
    for entry in entries:
        path = entry.get("path", "?")
        lineno = entry.get("lineno", "?")
        message = entry.get("message", "")
        lines.append(f'  File "{path}", line {lineno}, in {message}')
    return "\n".join(lines)


def _relative_path(raw_path: str, repo_root: Path | None) -> str:
    path = Path(raw_path)
    if repo_root:
        try:
            return str(path.resolve().relative_to(repo_root.resolve()))
        except ValueError:
            pass
    parts = path.parts
    for idx, part in enumerate(parts):
        if part in ("tests", "src", "vulnapi") or part.endswith(".py"):
            return str(Path(*parts[idx:]))
    return path.name


def extract_failed_nodeids(report: dict | None) -> list[str]:
    if not report:
        return []
    return [
        t["nodeid"]
        for t in report.get("tests", [])
        if t.get("outcome") == "failed" and t.get("nodeid")
    ]
