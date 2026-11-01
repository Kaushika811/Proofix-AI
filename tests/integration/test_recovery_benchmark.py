"""Real graph and pytest evidence for controlled recovery decisions."""

import pytest

from benchmarks.recovery.benchmark import run_benchmark


@pytest.mark.asyncio
async def test_recovery_routes_execute_and_preserve_contract():
    result = await run_benchmark()
    cases = {case["case"]: case for case in result["cases"]}
    assert result["live_model_measured"] is False
    assert len(cases) == 7

    for case in cases.values():
        assert case["baseline"]["target_test_passed"] is False
        assert "assert" in case["baseline"]["failure"]
        assert case["contract_preserved"] is True
        assert case["generated_test_unchanged"] is True
        assert case["persisted_attempts"] is True
        assert all(attempt["patch_hash"] for attempt in case["attempts"])

    for name, category, route in (
        ("implementation", "implementation_failure", "generate_code"),
        ("wrong_target", "wrong_target", "blast_scope"),
        ("diagnosis", "incorrect_diagnosis", "investigate"),
    ):
        case = cases[name]
        assert case["status"] == "completed"
        assert case["final_target_passed"] is True
        assert len(case["attempts"]) == 2
        assert case["attempts"][0]["failure_category"] == category
        assert case["attempts"][0]["recovery_decision"] == route
        assert case["attempts"][1]["failure_category"] is None
        assert case["trace"].count("validate_mutation") == 2

    wrong = cases["wrong_target"]["trace"]
    first_validation = wrong.index("validate_mutation")
    assert wrong[first_validation + 1:first_validation + 5] == [
        "blast_scope", "engineer_context", "plan_fixes", "generate_code",
    ]
    assert cases["wrong_target"]["trace"].count("plan_fixes") == 2

    diagnosis = cases["diagnosis"]
    assert diagnosis["trace"].count("investigate") == 2
    assert diagnosis["attempts"][1]["changed_from_previous"]["diagnosis"] is True

    environment = cases["environment"]
    assert environment["status"] == "blocked"
    assert environment["attempts"][0]["failure_category"] == "environment_failure"
    assert environment["attempts"][0]["recovery_decision"] == "recovery_stop"
    assert "ModuleNotFoundError" in environment["attempts"][0]["failure"]
    assert environment["trace"].count("validate_mutation") == 1

    insufficient = cases["insufficient"]
    assert insufficient["status"] == "blocked"
    assert insufficient["attempts"][0]["failure_category"] == "insufficient_evidence"
    assert insufficient["trace"].count("investigate") == 2
    assert insufficient["trace"].count("generate_code") == 1

    duplicate = cases["duplicate"]
    assert duplicate["status"] == "blocked"
    assert duplicate["trace"].count("generate_code") == 2
    assert duplicate["trace"].count("validate_mutation") == 1
    assert len(duplicate["attempts"]) == 2
    assert duplicate["attempts"][1]["validation_status"] == "not_executed"
    assert duplicate["attempts"][1]["failure_category"] is None
    assert duplicate["attempts"][1]["patch_hash"] == duplicate["attempts"][0]["patch_hash"]
    assert "repeats" in duplicate["recovery_blocked_reason"]

    budget = cases["budget"]
    assert budget["status"] == "blocked"
    assert budget["retry_count"] == 1
    assert budget["trace"].count("validate_mutation") == 2
    assert budget["attempts"][1]["recovery_decision"] == "recovery_stop"
    assert budget["attempts"][1]["changed_from_previous"]["patch"] is True
