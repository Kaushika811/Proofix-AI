"""Classify validation evidence and retain an auditable, bounded repair history."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from backend.state.schema import RunStateModel

FailureCategory = Literal[
    "implementation_failure", "wrong_target", "incorrect_diagnosis",
    "environment_failure", "insufficient_evidence",
]

_EXECUTION_ERROR = re.compile(
    r"\b(?:ImportError|ModuleNotFoundError|TimeoutExpired|TimeoutError|"
    r"OperationalError|DatabaseError|ConnectionError|FileNotFoundError|"
    r"PackageNotFoundError|DistributionNotFound|CollectionError)\b|"
    r"no module named|error during collection|timed out|no such table|"
    r"generated test.*(?:integrity|unverified|changed)", re.I,
)
_EXCEPTION_NAME = re.compile(r"\b([A-Za-z_][\w.]*(?:Error|Exception))\s*:")
_ASSERTION = re.compile(r"\bAssertionError\b|(?:^|\n)\s*(?:E\s+)?assert\b", re.I)


def patch_hash(bundle: dict | None) -> str | None:
    patches = (bundle or {}).get("patches") or []
    if not patches:
        return None
    content = sorted((p.get("file", ""), p.get("patched", "")) for p in patches)
    return hashlib.sha256(json.dumps(content, separators=(",", ":")).encode()).hexdigest()


def repeats_failed_patch(bundle: dict | None, attempts: list[dict]) -> bool:
    digest = patch_hash(bundle)
    return bool(digest and any(
        attempt.get("patch_hash") == digest and attempt.get("failure_category")
        for attempt in attempts
    ))


def contract_hash(state: RunStateModel) -> str:
    test = state.generated_test or {}
    pinned = {
        "issue_contract": state.issue_contract,
        "test": {k: test.get(k) for k in ("content", "path", "nodeid", "sha256")},
        "acceptance_test": state.acceptance_test,
    }
    return hashlib.sha256(json.dumps(pinned, sort_keys=True, default=str).encode()).hexdigest()


def classify_failure(state: RunStateModel) -> tuple[FailureCategory, str]:
    result = state.mutation_result or {}
    failure = result.get("validation_failure") or state.validation_failure or {}
    trace = "\n".join(str(failure.get(k) or "") for k in (
        "assertion_message", "traceback", "pytest_stdout", "pytest_stderr"
    ))
    exceptions = [match.group(1) for match in _EXCEPTION_NAME.finditer(trace)]
    if (not result.get("pytest_available", True) or _EXECUTION_ERROR.search(trace)
            or failure.get("pytest_phase") in {"setup", "teardown", "collection"}):
        return "environment_failure", "Validation raised an execution or environment error"
    if result.get("target_test_passed") is None or not patch_hash(state.patch_bundle):
        return "insufficient_evidence", "No verified target-test result or patch"
    issue = state.issue_contract or {}
    resolution = (state.blast_graph or {}).get("target_resolution") or {}
    patched_files = {p.get("file") for p in (state.patch_bundle or {}).get("patches") or []}
    target = resolution.get("resolved_path") or issue.get("target_file")
    if issue and (not target or target not in patched_files or
                  (issue.get("target_file") and target != issue["target_file"])):
        return "wrong_target", "Patch does not cover the resolved issue target"
    investigation = state.investigation or {}
    if any(item.get("stance") == "contradicting" and item.get("status") == "present"
           and item.get("category") in {"source", "reproduction"}
           for item in investigation.get("evidence") or []):
        return "incorrect_diagnosis", "Recorded source or runtime evidence contradicts the diagnosis"
    if (state.root_cause or {}).get("evidence_incomplete") or result.get("mutant_survived"):
        return "insufficient_evidence", "Root-cause or mutation evidence is incomplete"
    if result.get("target_test_passed") is False:
        if _ASSERTION.search(trace):
            return "implementation_failure", "Target behavior still fails by assertion"
        if exceptions or failure.get("exception_type"):
            return "implementation_failure", "Target execution raised an application exception"
        return "insufficient_evidence", "Target failure has no classified execution evidence"
    if result.get("regression_tests_passed") is False:
        return "implementation_failure", "Regression suite failed after target test passed"
    return "insufficient_evidence", "Validation did not establish a behavioral failure"


def record_attempt(state: RunStateModel, max_retries: int) -> None:
    """Record the observed attempt and decide recovery without editing its contract."""
    result = state.mutation_result or {}
    failed = not result.get("pytest_passed") or bool(result.get("mutant_survived"))
    category, reason = classify_failure(state) if failed else (None, "Validation passed")
    digest = patch_hash(state.patch_bundle)
    history = list(state.recovery_attempts)
    previous = history[-1] if history else None
    repeated = failed and repeats_failed_patch(state.patch_bundle, history)
    if not failed:
        route = "validate_security"
    elif not digest:
        # A repository-only analysis may have no repair candidate. Keep its
        # existing A10 review path; a targeted issue with no patch is blocked.
        route = "recovery_stop" if state.issue_contract else "route_pr"
    elif repeated or state.retry_count >= max_retries:
        route = "recovery_stop"
        reason = "Identical failed patch repeated" if repeated else "Recovery budget exhausted"
    else:
        route = {
            "implementation_failure": "generate_code",
            "wrong_target": "blast_scope",
            "incorrect_diagnosis": "investigate",
            "environment_failure": "recovery_stop",
            "insufficient_evidence": "investigate",
        }[category]
    failure = result.get("validation_failure") or state.validation_failure or {}
    target = ((state.blast_graph or {}).get("target_resolution") or {}).get("resolved_path")
    diagnosis = (state.root_cause or {}).get("root_cause")
    attempt = {
        "number": len(history) + 1, "target": target, "diagnosis": diagnosis,
        "patch_hash": digest, "failing_test": failure.get("failing_test"),
        "failure": failure.get("assertion_message") or failure.get("traceback"),
        "validation_status": "executed",
        "failure_category": category, "recovery_decision": route,
        "reason": reason,
        "changed_from_previous": None if previous is None else {
            "target": target != previous.get("target"),
            "diagnosis": diagnosis != previous.get("diagnosis"),
            "patch": digest != previous.get("patch_hash"),
        },
    }
    state.recovery_attempts = [*history, attempt]
    state.recovery_decision = {"route": route, "category": category, "reason": reason}
    if state.recovery_contract_hash is None:
        state.recovery_contract_hash = contract_hash(state)


def record_rejected_duplicate(state: RunStateModel, bundle: dict) -> None:
    """Record a repeated proposal without pretending pytest ran a second time."""
    previous = state.recovery_attempts[-1] if state.recovery_attempts else {}
    patches = bundle.get("patches") or []
    target = patches[0].get("file") if patches else None
    diagnosis = (state.root_cause or {}).get("root_cause")
    digest = patch_hash(bundle)
    reason = "Generated patch exactly repeats a failed attempt; validation was skipped"
    state.recovery_attempts = [*state.recovery_attempts, {
        "number": len(state.recovery_attempts) + 1,
        "target": target, "diagnosis": diagnosis, "patch_hash": digest,
        "failing_test": None, "failure": None,
        "validation_status": "not_executed", "failure_category": None,
        "recovery_decision": "recovery_stop", "reason": reason,
        "changed_from_previous": {
            "target": target != previous.get("target"),
            "diagnosis": diagnosis != previous.get("diagnosis"),
            "patch": digest != previous.get("patch_hash"),
        },
    }]
    state.recovery_decision = {"route": "recovery_stop", "category": None, "reason": reason}
    state.recovery_blocked_reason = reason


def record_security_failure(state: RunStateModel, max_retries: int) -> None:
    """A9 finishes the same patch attempt; mark it failed for duplicate detection."""
    security = state.security_result or {}
    if not security.get("rejected") or not state.recovery_attempts:
        return
    attempt = dict(state.recovery_attempts[-1])
    failure = security.get("validation_failure") or {}
    attempt.update(
        failure_category="implementation_failure",
        failure=failure.get("assertion_message") or failure.get("traceback") or
                "Patch introduced a security finding",
        recovery_decision="generate_code" if state.retry_count < max_retries else "route_pr",
        reason="Patch introduced a security finding",
    )
    state.recovery_attempts = [*state.recovery_attempts[:-1], attempt]
    state.recovery_decision = {
        "route": attempt["recovery_decision"], "category": "implementation_failure",
        "reason": attempt["reason"],
    }
