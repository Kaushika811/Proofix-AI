"""Every seeded Fault #3 case is measured by the same local runner."""

import pytest

from benchmarks.verification.benchmark import run_benchmark


@pytest.fixture(scope="module")
def cases():
    return {case["case"]: case for case in run_benchmark()["cases"]}


@pytest.mark.parametrize("name,located,status,reason,route", [
    ("supported", True, "supported", "source_and_acceptance_test_agree", "blast_scope"),
    ("false_claim_valid_citation", True, "contradicted",
     "source_and_acceptance_test_agree", "recovery_stop"),
    ("irrelevant_citation", True, "incomplete",
     "test_does_not_assert_claimed_behavior", "recovery_stop"),
    ("conflicting_runtime", True, "incomplete", "source_runtime_conflict", "recovery_stop"),
    ("unavailable_execution", True, "unavailable", "execution_unavailable", "recovery_stop"),
    ("incomplete_investigation", True, "incomplete",
     "no_independent_acceptance_test", "recovery_stop"),
    ("unstructured_false_claim", True, "incomplete",
     "claim_outside_supported_grammar", "blast_scope"),
    ("missing_source", False, "incomplete", "source_not_located", "recovery_stop"),
])
def test_verification_case(cases, name, located, status, reason, route):
    case = cases[name]
    assert case["source_located"] is located
    assert case["claim_status"] == status
    assert case["claim_reason"] == reason
    assert case["projected_diagnosis_status"] == case["diagnosis_status"]
    assert case["patch_route"] == route
    assert case["presented_as_confirmed"] is (status == "supported")
    if status != "supported":
        assert "diagnosis_unverified" in case["draft_reason_codes"]


def test_false_claim_with_valid_location_is_not_supported(cases):
    false = cases["false_claim_valid_citation"]
    assert false["source_location_method"] == "verified_exact"
    assert false["evidence_stance"] == "contradicting"
    assert false["diagnosis_status"] == "contradicted"


def test_unavailable_execution_is_not_contradictory_evidence(cases):
    unavailable = cases["unavailable_execution"]
    assert unavailable["reproduction_status"] == "INFRA_ERROR"
    assert unavailable["claim_status"] == "unavailable"
    assert unavailable["evidence_stance"] == "neutral"
    assert unavailable["claim_status"] != cases["false_claim_valid_citation"]["claim_status"]


def test_source_runtime_conflict_cannot_confirm_diagnosis(cases):
    conflict = cases["conflicting_runtime"]
    assert conflict["claim_status"] == "incomplete"
    assert conflict["evidence_stance"] == "neutral"
    assert conflict["patch_route"] == "recovery_stop"


def test_benchmark_measures_location_and_claims_separately():
    metrics = run_benchmark()["metrics"]
    assert metrics["cases"] == 8
    assert metrics["source_location_correct"] == 8
    assert metrics["claim_classification_correct"] == 8
    assert (metrics["supported"], metrics["contradicted"],
            metrics["incomplete"], metrics["unavailable"]) == (1, 1, 5, 1)
    assert metrics["false_supports"] == 0
    assert metrics["non_supported_as_confirmed"] == 0
    assert metrics["contradicted_to_patch"] == 0
