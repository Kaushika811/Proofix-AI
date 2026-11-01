"""Offline full-graph agent-path preflight on calibrated, isolated defects.

Stub mode makes no model call. In particular, A7 deliberately returns the
original file, so this measures wiring and safe stops, not patch quality.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import uuid
from pathlib import Path

import fakeredis.aioredis

from benchmarks.recovery.agent_cases import AgentCase, cases
from backend.config import Settings
from backend.orchestrator.runner import PipelineRunner
from backend.security.secret_scanner import scan_text
from backend.services.scoped_validation import run_scoped_validation
from backend.state.redis_store import RedisStore

OUTPUT = Path(__file__).with_name("agent-preflight.json")
TEST_PATH = "tests/test_reported_issue.py"


async def _calibrate(repo: Path, case: AgentCase) -> dict:
    source = repo / case.source_path
    outcomes = {}
    for label, content in (
        ("original", case.source),
        ("correct_fix", case.correct_source),
        ("incorrect_patch", case.incorrect_source),
    ):
        source.write_text(content, encoding="utf-8")
        result = await run_scoped_validation(
            repo, str(uuid.uuid4()), target_test=case.issue["target_test"],
            baseline_failures=[],
        )
        failure = result.validation_failure
        message = failure.assertion_message if failure else None
        outcomes[label] = {
            "target_test_passed": result.target_test_passed,
            "pytest_available": result.pytest_available,
            "assertion_failure": bool(message and "assert" in message.lower()),
            "regression_tests_passed": result.regression_tests_passed,
        }
    source.write_text(case.source, encoding="utf-8")
    if not (
        outcomes["original"]["assertion_failure"]
        and outcomes["incorrect_patch"]["assertion_failure"]
        and outcomes["correct_fix"]["target_test_passed"]
        and outcomes["correct_fix"]["regression_tests_passed"]
        and all(item["pytest_available"] for item in outcomes.values())
    ):
        raise AssertionError(f"Case {case.name} failed behavioral calibration: {outcomes}")
    return outcomes


async def _run_case(case: AgentCase, store: RedisStore, settings: Settings) -> dict:
    if any(scan_text(text).findings for text in (
        case.source, case.test, case.description, case.expected_behavior,
    )):
        raise ValueError(f"Case {case.name} contains detected sensitive content")
    with tempfile.TemporaryDirectory(prefix=f"proofix-agent-{case.name}-") as directory:
        repo = Path(directory)
        source = repo / case.source_path
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(case.source, encoding="utf-8")
        if len(source.parts) > len(repo.parts) + 1:
            (source.parent / "__init__.py").write_text("", encoding="utf-8")
        test_file = repo / TEST_PATH
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(case.test, encoding="utf-8")
        # A0.7 requires a dependency manifest before it will run reproduction.
        # This fixture needs only the already-installed test runner.
        (repo / "requirements.txt").write_text("pytest\n", encoding="utf-8")
        test_hash = hashlib.sha256(case.test.encode()).hexdigest()
        calibration = await _calibrate(repo, case)
        for cache in repo.rglob("__pycache__"):
            shutil.rmtree(cache)
        shutil.rmtree(repo / ".pytest_cache", ignore_errors=True)
        allowed = {case.source_path, TEST_PATH, "requirements.txt"}
        if source.parent != repo:
            allowed.add(str((source.parent / "__init__.py").relative_to(repo)))
        actual = {str(path.relative_to(repo)) for path in repo.rglob("*") if path.is_file()}
        if actual != allowed:
            raise AssertionError(f"Case {case.name} repository contains unexpected files: {actual - allowed}")

        run_id = str(uuid.uuid4())
        await store.init_run(
            run_id, str(repo),
            issue_hint=case.description, issue_contract=case.issue,
        )
        result = await PipelineRunner(store, settings).execute(run_id)
        target = (result.blast_graph or {}).get("target_resolution") or {}
        attempts = result.recovery_attempts or []
        if test_file.read_text(encoding="utf-8") != case.test:
            raise AssertionError(f"Case {case.name} acceptance test changed")
        mutation = result.mutation_result or {}
        return {
            "case": case.name,
            "prior_generator_fixture": case.prior_generator_fixture,
            "independent_real_world_defect": False,
            "source_scope": [case.source_path, TEST_PATH],
            "source_sha256": hashlib.sha256(case.source.encode()).hexdigest(),
            "test_sha256": test_hash,
            "issue_contract": case.issue,
            "calibration": calibration,
            "environment_status": (result.environment or {}).get("status"),
            "environment_reason": (result.environment or {}).get("reason"),
            "reproduction_status": (result.reproduction or {}).get("status"),
            "resolved_target": target.get("resolved_path"),
            "root_cause_source": (result.investigation or {}).get("root_cause_source"),
            "planning_order_source": (result.fix_dag or {}).get("ordering_source"),
            "patch_count": len((result.patch_bundle or {}).get("patches", [])),
            "attempt_count": len(attempts),
            "failure_categories": [a.get("failure_category") for a in attempts],
            "recovery_decisions": [a.get("recovery_decision") for a in attempts],
            "status": result.status,
            "contract_preserved": result.issue_contract == case.issue,
            "target_test_passed": mutation.get("target_test_passed"),
            "regression_tests_passed": mutation.get("regression_tests_passed"),
            "mutation_status": mutation.get("mutation_status"),
            "repair_correctness": None,
            "semantic_review": "pending_human_review" if not settings.stub_mode else "not_applicable_stub",
            "wrong_target_recovery": None,
            "diagnosis_recovery": None,
            "validation_failure_recovery": None,
        }


async def run_preflight() -> dict:
    settings = Settings(stub_mode=True, github_dry_run=True, max_retries=1)
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    try:
        store = RedisStore(client, settings)
        results = [await _run_case(case, store, settings) for case in cases()]
        return {
            "mode": "offline_real_agents_stub_a7",
            "live_model_measured": False,
            "independent_real_world_defects_measured": 0,
            "case_count": len(results),
            "cases": results,
        }
    finally:
        await client.aclose()


def main() -> None:
    result = asyncio.run(run_preflight())
    OUTPUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "case_count": result["case_count"],
        "statuses": {case["case"]: case["status"] for case in result["cases"]},
        "output": str(OUTPUT),
    }))


if __name__ == "__main__":
    main()
