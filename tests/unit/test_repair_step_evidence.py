from backend.services.repair_step_evidence import patch_fingerprint, step_evidence
from backend.state.schema import RunStateModel
from backend.services.ui_projection import build_repair_plan


def state():
    bundle = {"patches": [{"file": "a.py", "original": "old", "patched": "new"}]}
    digest = patch_fingerprint(bundle)
    bundle["application_evidence"] = {"patch_hash": digest, "files": {"a.py": {
        "targetFunction": "foo", "expectedOutcome": "reject expired tokens", "diff": "-old\n+new"}}}
    return RunStateModel(run_id="evidence", patch_bundle=bundle,
        mutation_result={"validated_patch_hash": digest, "pytest_available": True, "pytest_passed": True},
        fix_dag={"nodes": [{"issue_id": "one", "files": ["a.py"]}, {"issue_id": "two", "files": ["b.py"]}], "execution_order": ["one", "two"]})


def test_projection_links_only_recorded_files_and_labels_bundle_validation():
    steps = build_repair_plan(state())["steps"]
    assert steps[0]["evidence"]["execution"] == "applied"
    assert steps[0]["evidence"]["validation"] == "passed"
    assert steps[0]["evidence"]["validationScope"] == "patch_bundle"
    assert steps[1]["evidence"]["execution"] == "unavailable"
    assert steps[1]["evidence"]["validation"] == "unavailable"


def test_stale_validation_cannot_validate_replacement_patch():
    s = state()
    s.mutation_result["validated_patch_hash"] = "previous-attempt"
    evidence = step_evidence(s, {"files": ["a.py"]})
    assert evidence["execution"] == "applied"
    assert evidence["validation"] == "unavailable"


def test_changed_patch_invalidates_application_record():
    s = state()
    s.patch_bundle["patches"][0]["patched"] = "replacement"
    assert step_evidence(s, {"files": ["a.py"]})["execution"] == "unavailable"


def test_partial_application_and_environment_failure():
    s = state()
    s.mutation_result["pytest_available"] = False
    evidence = step_evidence(s, {"files": ["a.py", "b.py"]})
    assert evidence["execution"] == "partially_applied"
    assert evidence["validation"] == "unavailable"


def test_legacy_results_do_not_gain_inferred_status():
    s = state()
    s.patch_bundle.pop("application_evidence")
    assert step_evidence(s, {"files": ["a.py"]})["execution"] == "unavailable"


def test_failed_tests_are_exposed_for_matching_bundle():
    s = state()
    s.mutation_result["pytest_passed"] = False
    assert step_evidence(s, {"files": ["a.py"]})["validation"] == "failed"
