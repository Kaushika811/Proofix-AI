"""Small, reproducible benchmark for ProoFix's issue-to-test generator.

The default calibration uses disclosed, fixed test candidates and makes no
model request. --live invokes the configured model and requires permission to
send the selected repository source to that provider.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

from backend.config import Settings
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.security.secret_scanner import scan_text
from backend.services.issue_test_generation import (
    GeneratedTestOutput,
    _context,
    generate_issue_test,
    run_with_fresh_bytecode,
)
from backend.services.reproduction_parser import load_pytest_report
from backend.services.subprocess_runner import PYTHON, run_command
from backend.state.schema import RunStateModel

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "vulnapi"
ALLOWED_CONTEXT_MARKERS = {
    "SOURCE vulnapi/auth.py",
    "SOURCE vulnapi/middleware.py",
    "SOURCE vulnapi/api.py",
    "TEST CONVENTION tests/test_auth.py",
}

AUTH_TEST = """import base64
import json
import time
from vulnapi.auth import validate_token

def test_reported_issue():
    expired_payload = base64.urlsafe_b64encode(json.dumps({"sub": "user1", "exp": int(time.time()) - 3600}).encode()).decode().rstrip("=")
    valid_payload = base64.urlsafe_b64encode(json.dumps({"sub": "user1", "exp": int(time.time()) + 3600}).encode()).decode().rstrip("=")
    assert validate_token("header." + expired_payload + ".signature") is False
    assert validate_token("header." + valid_payload + ".signature") is True
"""

ADMIN_TEST = """import base64
import json
import time
from vulnapi.middleware import admin_middleware

def test_reported_issue():
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "user1", "exp": int(time.time()) + 3600}).encode()).decode().rstrip("=")
    token = "header." + payload + ".signature"
    assert admin_middleware({}, "/admin/users") is False
    assert admin_middleware({"Authorization": "Bearer " + token}, "/admin/users") is True
"""

SQL_TEST = """from vulnapi.api import lookup_user

def test_reported_issue():
    assert lookup_user("1 OR 1=1") is None
    user = lookup_user("1")
    assert user is not None
    assert user["name"] == "admin"
"""

VACUOUS_TEST = """from vulnapi.auth import validate_token

def test_reported_issue():
    assert validate_token("malformed") is False
"""


@dataclass(frozen=True)
class Case:
    name: str
    source_file: str
    description: str
    expected_behavior: str
    controlled_test: str
    correct_source: str
    wrong_source: str
    unrelated_source: str
    environment: dict[str, str] | None = None
    init_database: bool = False


def cases() -> list[Case]:
    auth = (SOURCE / "vulnapi/auth.py").read_text()
    middleware = (SOURCE / "vulnapi/middleware.py").read_text()
    api = (SOURCE / "vulnapi/api.py").read_text()
    return [
        Case(
            "expired_token",
            "vulnapi/auth.py",
            "An expired token is accepted. Reject expired tokens and keep unexpired tokens valid.",
            "validate_token returns False when exp is in the past and True when exp is in the future.",
            AUTH_TEST,
            auth.replace("import json\n", "import json\nimport time\n").replace(
                '    # Missing: if payload.get("exp", 0) < time.time(): return False\n    return True',
                '    if payload.get("exp", 0) < time.time():\n        return False\n    return True',
            ),
            auth.replace("    return True\n", "    return False\n", 1),
            auth.replace('return payload.get("sub") if payload else None', "return None"),
        ),
        Case(
            "admin_bypass",
            "vulnapi/middleware.py",
            "Admin mode bypasses authentication for admin routes. The controlled test runner sets ADMIN_MODE=true.",
            "With ADMIN_MODE=true, unauthenticated admin requests are denied and valid-token requests are allowed.",
            ADMIN_TEST,
            middleware.replace(
                "            return True  # BUG: bypasses auth entirely",
                "            return check_auth(headers)",
            ),
            middleware.replace(
                "            return True  # BUG: bypasses auth entirely", "            return False"
            ),
            middleware.replace(
                '    if path.startswith("/public"):\n        return True',
                '    if path.startswith("/public"):\n        return False',
            ),
            environment={"ADMIN_MODE": "true"},
        ),
        Case(
            "sql_injection",
            "vulnapi/api.py",
            "lookup_user accepts an injected SQL expression as an ID.",
            "lookup_user('1 OR 1=1') returns None while lookup_user('1') still returns the admin record.",
            SQL_TEST,
            api.replace(
                'query = f"SELECT * FROM users WHERE id = {user_id}"\n    cursor = conn.execute(query)',
                'query = "SELECT * FROM users WHERE id = ?"\n    cursor = conn.execute(query, (user_id,))',
            ),
            api.replace(
                "    conn = get_connection()\n    # Vulnerable: direct string interpolation",
                "    return None\n    conn = get_connection()\n    # Vulnerable: direct string interpolation",
            ),
            api.replace(
                "query = f\"SELECT * FROM users WHERE name LIKE '%{name_filter}%'\"",
                "query = \"SELECT * FROM users WHERE name LIKE 'nothing'\"",
            ),
            init_database=True,
        ),
    ]


def _clone(repo: Path) -> None:
    shutil.copytree(SOURCE / "vulnapi", repo / "vulnapi")
    shutil.copytree(SOURCE / "tests", repo / "tests")


def _state(repo: Path, run_id: str, case: Case) -> RunStateModel:
    files = {
        str(path.relative_to(repo)): FileNode(
            path=str(path.relative_to(repo)),
            role="test-only" if "tests" in path.relative_to(repo).parts else "internal-util",
        )
        for path in repo.rglob("*.py")
    }
    sig = SemanticIntentGraph(repo_path=str(repo), files=files)
    return RunStateModel(
        run_id=run_id,
        repo_path=str(repo),
        sig=sig.model_dump(mode="json"),
        issue_contract={
            "description": case.description,
            "expected_behavior": case.expected_behavior,
            "target_test": "",
            "target_file": case.source_file,
        },
    )


async def _prepare_database(repo: Path) -> str | None:
    code, _stdout, stderr = await run_command(
        [PYTHON, "-c", "from vulnapi.api import init_db; init_db()"], cwd=repo, timeout=20
    )
    return None if code == 0 else f"database setup failed: {stderr[-300:]}"


async def _execute(repo: Path, nodeid: str, environment: dict[str, str] | None) -> dict:
    report_file = repo / "proofix-benchmark-report.json"
    report_file.unlink(missing_ok=True)
    cmd = [
        PYTHON,
        "-m",
        "pytest",
        nodeid,
        "-q",
        "--tb=short",
        "--json-report",
        f"--json-report-file={report_file}",
    ]
    # ADMIN_MODE belongs to the case fixture, never to the generated test.
    # run_with_fresh_bytecode owns cache isolation; the extra env is set only
    # for this sequential subprocess and restored immediately afterward.
    import os

    before = {key: os.environ.get(key) for key in environment or {}}
    try:
        os.environ.update(environment or {})
        code, _stdout, _stderr = await run_with_fresh_bytecode(cmd, cwd=repo, timeout=30)
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    report = load_pytest_report(report_file) or {}
    selected = next((item for item in report.get("tests", []) if item.get("nodeid") == nodeid), {})
    call = selected.get("call") or {}
    message = (call.get("crash") or {}).get("message", "")
    if code == 0 and selected.get("outcome") == "passed" and call.get("outcome") == "passed":
        outcome = "passed"
    elif (
        code == 1
        and call.get("outcome") == "failed"
        and message.startswith(("assert ", "AssertionError", "Failed: DID NOT RAISE"))
    ):
        outcome = "behavioral_assertion_failed"
    else:
        outcome = "environment_or_execution_failure"
    return {
        "outcome": outcome,
        "exit_code": code,
        "pytest_outcome": selected.get("outcome"),
        "failure_type": message.split(":", 1)[0][:80] if message else None,
        "detail": (message[:300] or "Execution failed without pytest crash detail")
        if outcome == "environment_or_execution_failure"
        else None,
    }


async def _candidate(
    state: RunStateModel, repo: Path, settings: Settings, code: str | None
) -> dict:
    if code is None:
        # Fail closed before calling the external provider. The exact context
        # constructed by the generator must use only reviewed fixture files.
        try:
            context = _context(state, repo)
        except (ValueError, OSError) as exc:
            return {"status": "skipped_unsafe_context", "reason": str(exc), "attempts": 0}
        markers = {
            line
            for line in context.splitlines()
            if line.startswith(("SOURCE ", "TEST CONVENTION "))
        }
        expected_source = f"SOURCE {(state.issue_contract or {}).get('target_file', '')}"
        if not markers <= ALLOWED_CONTEXT_MARKERS or expected_source not in markers:
            return {
                "status": "skipped_unsafe_context",
                "reason": "Model context contains a disallowed or missing source file",
                "attempts": 0,
            }
        if (
            not scan_text(context).clean
            or not scan_text(
                f"{(state.issue_contract or {}).get('description', '')}\n"
                f"{(state.issue_contract or {}).get('expected_behavior', '')}"
            ).clean
        ):
            return {
                "status": "skipped_unsafe_context",
                "reason": "Model context or issue text contains potential sensitive data",
                "attempts": 0,
            }
        return await generate_issue_test(state, repo, settings)

    async def fixed_output(*_args, **_kwargs) -> GeneratedTestOutput:
        return GeneratedTestOutput(
            code=code, rationale="Controlled benchmark candidate; no model call"
        )

    with patch("backend.services.issue_test_generation.LLMService.structured", fixed_output):
        return await generate_issue_test(state, repo, settings)


async def _run_case(case: Case, mode: str, settings: Settings) -> dict:
    with tempfile.TemporaryDirectory(prefix="proofix-generator-bench-") as folder:
        repo = Path(folder).resolve()
        _clone(repo)
        if case.init_database:
            setup_error = await _prepare_database(repo)
            if setup_error:
                return {
                    "case": case.name,
                    "generation": "not_attempted",
                    "setup_error": setup_error,
                }
        state = _state(repo, f"bench-{case.name}", case)
        artifact = await _candidate(
            state, repo, settings, case.controlled_test if mode == "calibration" else None
        )
        result: dict = {
            "case": case.name,
            "generation_mode": mode,
            "issue": case.description,
            "expected_behavior": case.expected_behavior,
            "generation": artifact["status"],
            "generation_reason": artifact.get("reason"),
            "candidate_code": artifact.get("content"),
            "candidate_rationale": artifact.get("rationale"),
            "attempts": artifact.get("attempts"),
            "candidates": artifact.get("candidates", []),
        }
        if artifact["status"] != "generated":
            return result
        result["original"] = await _execute(repo, artifact["nodeid"], case.environment)
        variants = {
            "correct_fix": case.correct_source,
            "incorrect_patch": case.wrong_source,
            "unrelated_patch": case.unrelated_source,
        }
        for name, source in variants.items():
            (repo / case.source_file).write_text(source)
            result[name] = await _execute(repo, artifact["nodeid"], case.environment)
        if case.init_database:
            (repo / "vulnapi/data.db").unlink(missing_ok=True)
            (repo / case.source_file).write_text(case.correct_source)
            result["missing_database_probe"] = await _execute(
                repo, artifact["nodeid"], case.environment
            )
        result["behavioral_test_validity"] = (
            (
                result["original"]["outcome"] == "behavioral_assertion_failed"
                and result["correct_fix"]["outcome"] == "passed"
            )
            if mode == "calibration"
            else None
        )
        result["semantic_review"] = (
            "controlled_candidate_with_known_expected_behavior"
            if mode == "calibration"
            else "pending_human_review"
        )
        result["correct_fix_detected"] = (
            result["original"]["outcome"] == "behavioral_assertion_failed"
            and result["correct_fix"]["outcome"] == "passed"
        )
        result["incorrect_patch_rejected"] = (
            result["original"]["outcome"] == "behavioral_assertion_failed"
            and result["incorrect_patch"]["outcome"] == "behavioral_assertion_failed"
        )
        result["unrelated_patch_rejected"] = (
            result["original"]["outcome"] == "behavioral_assertion_failed"
            and result["unrelated_patch"]["outcome"] == "behavioral_assertion_failed"
        )
        return result


async def run(mode: str) -> dict:
    settings = (
        Settings(stub_mode=False)
        if mode == "live"
        else Settings(
            stub_mode=False,
            llm_provider="anthropic",
            anthropic_api_key="calibration-no-egress",
        )
    )
    results = [await _run_case(case, mode, settings) for case in cases()]
    # A test that exercises *an* application call but misses the issue must be
    # detected by the baseline passing. It is a separate adversarial probe.
    auth = cases()[0]
    with tempfile.TemporaryDirectory(prefix="proofix-generator-vacuous-") as folder:
        repo = Path(folder).resolve()
        _clone(repo)
        state = _state(repo, "bench-vacuous", auth)
        artifact = await _candidate(state, repo, settings, VACUOUS_TEST)
        vacuous = {
            "generation_mode": "controlled_gate_probe",
            "generation": artifact["status"],
            "generation_reason": artifact.get("reason"),
            "candidate_code": artifact.get("content"),
        }
        if artifact["status"] == "generated":
            vacuous["original"] = await _execute(repo, artifact["nodeid"], None)
            vacuous["detected_as_unconfirmed"] = vacuous["original"]["outcome"] == "passed"

    # Deliberately vague: the current API accepts any nonempty expectation.
    # The controlled candidate demonstrates whether generation itself asks for
    # clarification. No score for this is inferred from the three clear cases.
    vague = cases()[1]
    vague = Case(
        **{
            **vague.__dict__,
            "description": "Admin mode behaves oddly.",
            "expected_behavior": "Handle admin access appropriately.",
        }
    )
    with tempfile.TemporaryDirectory(prefix="proofix-generator-ambiguous-") as folder:
        repo = Path(folder).resolve()
        _clone(repo)
        state = _state(repo, "bench-ambiguous", vague)
        artifact = await _candidate(
            state, repo, settings, ADMIN_TEST if mode == "calibration" else None
        )
        ambiguous = {
            "issue": vague.description,
            "expected_behavior": vague.expected_behavior,
            "generation": artifact["status"],
            "generation_reason": artifact.get("reason"),
            "attempts": artifact.get("attempts"),
            "candidates": artifact.get("candidates", []),
            "abstained_for_ambiguity": artifact["status"] == "needs_clarification",
            "rejected_by_test_validator": artifact["status"] == "rejected",
        }
    evaluated = [r for r in results if r.get("original") is not None]
    return {
        "mode": mode,
        "dataset": "Three seeded, executable defects in ProoFix's vulnapi fixture",
        "model_quality_measured": mode == "live",
        "live_model_attempted": mode == "live",
        "cases": results,
        "vacuous_test_probe": vacuous,
        "ambiguous_expectation_probe": ambiguous,
        "metrics": {
            "semantic_validity": len(results) if mode == "calibration" else None,
            "execution_behavioral_validity": sum(
                r.get("correct_fix_detected", False) for r in evaluated
            )
            if evaluated
            else None,
            "behavioral_test_validity": (
                sum(r.get("behavioral_test_validity") is True for r in results)
                if mode == "calibration"
                else None
            ),
            "correct_fix_detection": sum(r.get("correct_fix_detected", False) for r in evaluated)
            if evaluated
            else None,
            "incorrect_patch_rejection": sum(
                r.get("incorrect_patch_rejected", False) for r in evaluated
            )
            if evaluated
            else None,
            "unrelated_patch_rejection": sum(
                r.get("unrelated_patch_rejected", False) for r in evaluated
            )
            if evaluated
            else None,
            "clear_cases": len(results),
            "candidates_generated": sum(r["generation"] == "generated" for r in results),
            "candidates_rejected": sum(r["generation"] == "rejected" for r in results),
            "patch_evaluations": len(evaluated),
            "environment_related_false_failures": sum(
                r.get("missing_database_probe", {}).get("outcome")
                == "environment_or_execution_failure"
                for r in results
            ),
            "environment_failures_counted_as_behavior": sum(
                r.get("missing_database_probe", {}).get("outcome") == "behavioral_assertion_failed"
                for r in results
            ),
            "vacuous_test_detected": vacuous.get("detected_as_unconfirmed"),
            "ambiguous_request_abstained": ambiguous["abstained_for_ambiguity"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("calibration", "live"), default="calibration")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = asyncio.run(run(args.mode))
    output = args.output or ROOT / f"benchmarks/generator/{args.mode}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"mode": result["mode"], "metrics": result["metrics"], "output": str(output)}))


if __name__ == "__main__":
    main()
