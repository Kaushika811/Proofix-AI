"""Conservative, local corroboration for a small class of observable claims.

This is not a natural-language truth detector. It recognizes only literal-return
claims backed by a single, unchanged acceptance assertion and a pytest JSON
report. Everything else remains incomplete rather than acquiring support from
an LLM explanation or a citation's location.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from backend.services.acceptance_test_integrity import acceptance_test_integrity_error
from backend.services.reproduction_parser import load_pytest_report

ClaimStatus = Literal["supported", "contradicted", "incomplete", "unavailable"]
_CLAIM = re.compile(r"^([A-Za-z_]\w*)\((.*)\) returns (.+?)\.?$")
_OBSERVED = re.compile(
    r"\bassert\s+(True|False|None|-?\d+|'[^']*'|\"[^\"]*\")\s+"
    r"(?:is|==)\s+(True|False|None|-?\d+|'[^']*'|\"[^\"]*\")"
)
_UNKNOWN = object()


@dataclass(frozen=True)
class ClaimAssessment:
    status: ClaimStatus
    reason: str


_BLOCKING_GAPS = frozenset({
    "source_runtime_conflict",
    "runtime_test_evidence_conflicts",
    "test_does_not_assert_claimed_behavior",
    "acceptance_test_not_tied_to_issue",
    "acceptance_test_integrity_failed",
    "no_independent_acceptance_test",
    "execution_unavailable",
})


def blocking_evidence_gap(assessments: list[ClaimAssessment]) -> bool:
    """A concrete mismatch or unavailable required evidence stops target repair."""
    return any(assessment.reason in _BLOCKING_GAPS for assessment in assessments)


def diagnosis_status(root_cause: str, citations: list[dict]) -> ClaimStatus:
    """A broader explanation is supported only when it is exactly the proven claim."""
    statuses = {str(c.get("claim_support_status")) for c in citations}
    if "contradicted" in statuses:
        return "contradicted"
    if "unavailable" in statuses:
        return "unavailable"
    if (citations and statuses == {"supported"}
            and root_cause.strip() in {str(c.get("claim") or "").strip() for c in citations}):
        return "supported"
    return "incomplete"


def _literal(node: ast.AST) -> object:
    value = ast.literal_eval(node)
    if type(value) not in (bool, int, str, type(None)):
        raise ValueError("Only primitive literal values are supported")
    return value


def _same(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def _claim_parts(claim: str) -> tuple[str, tuple[object, ...], object] | None:
    match = _CLAIM.fullmatch(claim.strip())
    if not match:
        return None
    try:
        call = ast.parse(f"f({match.group(2)})", mode="eval").body
        if not isinstance(call, ast.Call) or call.keywords or len(call.args) > 4:
            return None
        args = tuple(_literal(arg) for arg in call.args)
        claimed = _literal(ast.parse(match.group(3), mode="eval").body)
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return None
    return match.group(1), args, claimed


def _source_result(source: str, function: str, line: int, arity: int) -> object:
    """Prove a constant return only; no calls, decorators, branches or globals."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or node.name != function:
            continue
        if not (node.lineno <= line <= (node.end_lineno or node.lineno)):
            continue
        arguments = node.args
        if (node.decorator_list or arguments.defaults or arguments.kw_defaults
                or arguments.vararg or arguments.kwarg or arguments.kwonlyargs
                or len(arguments.posonlyargs) + len(arguments.args) != arity):
            return _UNKNOWN
        if len(node.body) != 1 or not isinstance(node.body[0], ast.Return):
            return _UNKNOWN
        try:
            return _literal(node.body[0].value)
        except (ValueError, TypeError, MemoryError, RecursionError):
            return _UNKNOWN
    return _UNKNOWN


def _test_expectation(
    source: str, module: str, function: str, test_name: str, args: tuple[object, ...]
) -> object:
    """Accept only an import and one simple assertion; skip fixtures and mocks."""
    tree = ast.parse(source)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    if (len(imports) != 1 or len(functions) != 1
            or len(tree.body) != 2 or imports[0].module != module
            or not any(alias.name == function and alias.asname is None for alias in imports[0].names)
            or functions[0].name != test_name or functions[0].decorator_list
            or functions[0].args.args or len(functions[0].body) != 1):
        return _UNKNOWN
    assertion = functions[0].body[0]
    if not isinstance(assertion, ast.Assert) or not isinstance(assertion.test, ast.Compare):
        return _UNKNOWN
    comparison = assertion.test
    if (len(comparison.ops) != 1 or not isinstance(comparison.ops[0], (ast.Is, ast.Eq))
            or len(comparison.comparators) != 1 or not isinstance(comparison.left, ast.Call)):
        return _UNKNOWN
    call = comparison.left
    if (not isinstance(call.func, ast.Name) or call.func.id != function
            or call.keywords or len(call.args) != len(args)):
        return None
    try:
        if any(not _same(_literal(node), value) for node, value in zip(call.args, args)):
            return _UNKNOWN
        return _literal(comparison.comparators[0])
    except (ValueError, TypeError, MemoryError, RecursionError):
        return _UNKNOWN


def assess_claim_support(
    repo: Path,
    citation: dict,
    *,
    issue_contract: dict | None,
    reproduction: dict | None,
    acceptance_test: dict | None,
) -> ClaimAssessment:
    """Classify one citation without executing source or trusting model verdicts."""
    if not citation.get("source_located"):
        return ClaimAssessment("incomplete", "source_not_located")
    parts = _claim_parts(str(citation.get("claim") or ""))
    if parts is None:
        return ClaimAssessment("incomplete", "claim_outside_supported_grammar")
    function, args, claimed = parts
    if not issue_contract or not acceptance_test:
        return ClaimAssessment("incomplete", "no_independent_acceptance_test")
    nodeid = acceptance_test.get("nodeid")
    if not nodeid or nodeid != issue_contract.get("target_test"):
        return ClaimAssessment("incomplete", "acceptance_test_not_tied_to_issue")
    if acceptance_test_integrity_error(repo, acceptance_test):
        return ClaimAssessment("unavailable", "acceptance_test_integrity_failed")

    relative = str(citation.get("file") or "")
    source_path = (repo / relative).resolve()
    test_path = (repo / str(acceptance_test.get("path") or "")).resolve()
    if not source_path.is_relative_to(repo.resolve()) or not test_path.is_relative_to(repo.resolve()):
        return ClaimAssessment("incomplete", "source_or_test_outside_repository")
    try:
        source = source_path.read_text(encoding="utf-8")
        test_source = test_path.read_text(encoding="utf-8")
        line = int(citation.get("line"))
        actual_source = _source_result(source, function, line, len(args))
        module = relative.removesuffix(".py").replace("/", ".")
        test_name = str(nodeid).split("::", 1)[1]
        expected = _test_expectation(test_source, module, function, test_name, args)
    except (OSError, SyntaxError, TypeError, ValueError, IndexError):
        return ClaimAssessment("incomplete", "source_or_test_cannot_be_checked")
    if actual_source is _UNKNOWN:
        return ClaimAssessment("incomplete", "source_behavior_not_proven")
    if expected is _UNKNOWN:
        return ClaimAssessment("incomplete", "test_does_not_assert_claimed_behavior")

    repro = reproduction or {}
    if repro.get("status") in {"INFRA_ERROR", "NO_TESTS"}:
        return ClaimAssessment("unavailable", "execution_unavailable")
    if (repro.get("status") != "CONFIRMED" or repro.get("failing_test") != nodeid
            or repro.get("evidence_source") != "pytest_report"
            or repro.get("exit_code") != 1):
        return ClaimAssessment("incomplete", "targeted_behavior_not_reproduced")
    report_path = repro.get("report_path")
    report = load_pytest_report(Path(report_path)) if report_path else None
    selected = next((test for test in (report or {}).get("tests", [])
                     if test.get("nodeid") == nodeid), None)
    call = (selected or {}).get("call") or {}
    if not selected or selected.get("outcome") != "failed" or call.get("outcome") != "failed":
        return ClaimAssessment("unavailable", "targeted_pytest_report_unavailable")
    crash_message = str((call.get("crash") or {}).get("message") or "")
    if not crash_message.startswith(("assert ", "AssertionError")):
        return ClaimAssessment("incomplete", "failure_not_behavioral_assertion")
    text = "\n".join((crash_message,
                      str(call.get("longrepr") or "")))
    observed = _OBSERVED.search(text)
    if not observed:
        return ClaimAssessment("incomplete", "runtime_value_not_observable")
    try:
        actual_runtime = _literal(ast.parse(observed.group(1), mode="eval").body)
        runtime_expected = _literal(ast.parse(observed.group(2), mode="eval").body)
    except (SyntaxError, ValueError, TypeError):
        return ClaimAssessment("incomplete", "runtime_value_not_observable")
    if not _same(runtime_expected, expected) or _same(actual_runtime, expected):
        return ClaimAssessment("incomplete", "runtime_test_evidence_conflicts")
    if not _same(actual_runtime, actual_source):
        return ClaimAssessment("incomplete", "source_runtime_conflict")
    return ClaimAssessment("supported" if _same(claimed, actual_runtime) else "contradicted",
                           "source_and_acceptance_test_agree")
