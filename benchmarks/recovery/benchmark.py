"""Controlled LangGraph recovery benchmark with real A8/pytest execution.

Upstream investigation, patch proposals, A9, and A10 are scripted so each
failure category can be reproduced deterministically. The graph edges, A8,
generated-test integrity check, pytest subprocess, and persisted state are real.
No model or network service is called.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import fakeredis.aioredis

from backend.agents.a8_mutation_validator import A8MutationValidatorAgent
from backend.config import Settings
from backend.orchestrator.graph import build_graph
from backend.services.evidence_recovery import (
    contract_hash, record_rejected_duplicate, repeats_failed_patch,
)
from backend.services.mutation_parser import MutationOutcome
from backend.services.scoped_validation import run_scoped_validation
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel

TEST_PATH = "tests/test_proofix_recovery.py"
NODEID = f"{TEST_PATH}::test_reported_issue"
ORIGINAL = "def validate_token(exp):\n    return True\n"
BAD_ONE = "def validate_token(exp):\n    return exp != 0\n"
BAD_TWO = "def validate_token(exp):\n    return exp < 100\n"
CORRECT = "def validate_token(exp):\n    return exp > 0\n"
BAD_ENVIRONMENT = (
    "def validate_token(exp):\n"
    "    import missing_recovery_benchmark_dependency\n"
    "    return exp > 0\n"
)
BEHAVIOR_TEST = (
    "from auth import validate_token\n\n"
    "def test_reported_issue():\n"
    "    assert validate_token(-1) is False\n"
    "    assert validate_token(1) is True\n"
)

CASES = (
    "implementation", "wrong_target", "diagnosis", "environment",
    "insufficient", "duplicate", "budget",
)


def _proposals(case: str) -> list[tuple[str, str]]:
    if case == "wrong_target":
        return [("other.py", "value = 1\n"), ("auth.py", CORRECT)]
    if case == "budget":
        return [("auth.py", BAD_ONE), ("auth.py", BAD_TWO)]
    if case == "duplicate":
        return [("auth.py", BAD_ONE), ("auth.py", BAD_ONE)]
    if case == "environment":
        return [("auth.py", BAD_ENVIRONMENT)]
    return [("auth.py", BAD_ONE), ("auth.py", CORRECT)]


async def run_case(case: str) -> dict:
    if case not in CASES:
        raise ValueError(f"Unknown recovery benchmark case: {case}")
    with tempfile.TemporaryDirectory(prefix=f"proofix-recovery-{case}-") as directory:
        repo = Path(directory)
        (repo / "tests").mkdir()
        (repo / "auth.py").write_text(ORIGINAL, encoding="utf-8")
        (repo / "other.py").write_text("value = 0\n", encoding="utf-8")
        content = BEHAVIOR_TEST
        (repo / TEST_PATH).write_text(content, encoding="utf-8")
        generated = {
            "status": "generated", "path": TEST_PATH, "nodeid": NODEID,
            "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(),
        }
        issue = {
            "description": "Expired tokens must be rejected",
            "target_test": NODEID,
            "expected_behavior": "Past expiry returns False; future expiry returns True",
            "target_file": "auth.py",
        }
        run_id = str(uuid.uuid4())
        settings = Settings(stub_mode=True, github_dry_run=True, max_retries=1 if case == "budget" else 3)
        client = fakeredis.aioredis.FakeRedis(decode_responses=False)
        store = RedisStore(client, settings)
        trace: list[str] = []
        proposals = _proposals(case)
        generation_count = 0

        class ScriptedNodes:
            def __init__(self, store: RedisStore, settings: Settings):
                self.store = store
                self.a8 = A8MutationValidatorAgent(store, settings)

                async def unavailable_mutation(
                    _repo, _bundle, *, target_test=None, baseline_failures=None,
                ):
                    return MutationOutcome(
                        status="unavailable", unavailable_reason="Not run in routing benchmark"
                    ), ""

                self.a8._run_mutmut = unavailable_mutation

            async def prepare_repo(self, state):
                trace.append("prepare_repo")
                state.repo_clone_path = str(repo)
                return state

            async def environment_precheck(self, state):
                trace.append("environment_precheck")
                return state

            async def index_repository(self, state):
                trace.append("index_repository")
                return state

            async def parallel_intel(self, state):
                trace.append("parallel_intel")
                return state

            async def layer1_fan_in(self, state):
                trace.append("layer1_fan_in")
                return state

            async def reproduction_gate(self, state):
                trace.append("reproduction_gate")
                state.reproduction = {
                    "status": "CONFIRMED", "failing_test": NODEID,
                    "pre_existing_failures": [],
                    "traceback": "AssertionError: expired token accepted",
                }
                return state

            async def investigate(self, state):
                trace.append("investigate")
                revised = trace.count("investigate") > 1
                state.root_cause = {
                    "root_cause": "expiry comparison missing" if revised else "expiry logic suspected",
                    # Initial evidence permits one patch; reinvestigation then
                    # fails to resolve the newly exposed evidence gap.
                    "evidence_incomplete": case == "insufficient" and revised,
                }
                state.investigation = {
                    "status": "partial" if case == "insufficient" else "complete",
                    "evidence": ([{
                        "category": "source", "status": "present", "stance": "contradicting",
                    }] if case == "diagnosis" and not revised else []),
                }
                return state

            async def blast_scope(self, state):
                trace.append("blast_scope")
                state.blast_graph = {
                    "target_resolution": {"resolved_path": "auth.py"},
                    "auto_patch_scope": ["auth.py"],
                }
                return state

            async def engineer_context(self, state):
                trace.append("engineer_context")
                return state

            async def plan_fixes(self, state):
                trace.append("plan_fixes")
                state.fix_dag = {"nodes": [{"issue_id": "reported-issue", "files": ["auth.py"]}]}
                return state

            async def generate_code(self, state):
                nonlocal generation_count
                trace.append("generate_code")
                file, patched = proposals[min(generation_count, len(proposals) - 1)]
                generation_count += 1
                original = ORIGINAL if file == "auth.py" else "value = 0\n"
                bundle = {"patches": [{"file": file, "original": original, "patched": patched}]}
                if state.recovery_contract_hash and contract_hash(state) != state.recovery_contract_hash:
                    state.recovery_blocked_reason = "Issue contract or acceptance test changed"
                    return state
                if repeats_failed_patch(bundle, state.recovery_attempts):
                    record_rejected_duplicate(state, bundle)
                    return state
                (repo / file).write_text(patched, encoding="utf-8")
                state.patch_bundle = bundle
                return state

            async def validate_mutation(self, state):
                trace.append("validate_mutation")
                if case == "insufficient":
                    # The gap is discovered during validation, after the
                    # initial investigation permitted a first patch.
                    state.root_cause["evidence_incomplete"] = True
                return await self.a8.run(state)

            async def validate_security(self, state):
                trace.append("validate_security")
                state.security_result = {"rejected": False, "security_score": None}
                return state

            async def route_pr(self, state):
                trace.append("route_pr")
                state.status = "completed"
                state.pr_decision = {"pr_type": "draft", "review_note": "Controlled benchmark"}
                return state

        initial = RunStateModel(
            run_id=run_id, repo_path=str(repo), repo_clone_path=str(repo),
            issue_contract=issue, generated_test=generated,
        )
        await store.save_state(initial)
        baseline = await run_scoped_validation(
            repo, f"{run_id}-baseline", target_test=NODEID,
            baseline_failures=[], generated_test=generated,
        )
        with patch("backend.orchestrator.graph.GraphNodes", ScriptedNodes):
            graph = build_graph(store, settings).compile()
            final_state = await graph.ainvoke(initial.model_dump(exclude_none=False),
                                              {"recursion_limit": 50})
        final = RunStateModel.model_validate(final_state)
        saved = await store.load_state(run_id)
        await client.aclose()
        if saved is None:
            raise AssertionError("Graph did not persist recovery state")
        return {
            "case": case,
            "baseline": {
                "target_test_passed": baseline.target_test_passed,
                "failure": (baseline.validation_failure or {}).assertion_message
                if baseline.validation_failure else None,
            },
            "status": final.status,
            "trace": trace,
            "attempts": final.recovery_attempts,
            "retry_count": final.retry_count,
            "contract_preserved": contract_hash(final) == contract_hash(initial),
            "generated_test_unchanged": (repo / TEST_PATH).read_text(encoding="utf-8") == content,
            "persisted_attempts": saved.recovery_attempts == final.recovery_attempts,
            "final_target_passed": (final.mutation_result or {}).get("target_test_passed"),
            "recovery_blocked_reason": final.recovery_blocked_reason,
        }


async def run_benchmark() -> dict:
    cases = [await run_case(case) for case in CASES]
    return {
        "mode": "controlled_graph_recovery",
        "scope": "Real LangGraph routing, A8, pytest, integrity checks, and persistence; scripted A0-A7 proposals and A9-A10",
        "live_model_measured": False,
        "cases": cases,
    }


def main() -> None:
    result = asyncio.run(run_benchmark())
    output = Path(__file__).with_name("results.json")
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "cases": len(result["cases"]),
        "statuses": {case["case"]: case["status"] for case in result["cases"]},
        "output": str(output),
    }))


if __name__ == "__main__":
    main()
