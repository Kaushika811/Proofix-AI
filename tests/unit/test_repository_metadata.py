"""Tests for repository identity on the run projection (G4).

The workspace header joins a run to its repository — learned profiles, repair
memory and the digital twin are all keyed by `repositoryId`. Identity must be
stable across runs and must never be invented when it was not observed.
"""

from backend.services.repair_memory import repository_id as compute_repository_id
from backend.services.ui_projection import build_workspace_header, repository_identity
from backend.state.schema import RunStateModel

RUN_ID = "b41f0f4e-77a1-4a0e-9c4d-a1d2f3e4b5c6"


def _state(**overrides) -> RunStateModel:
    base = dict(run_id=RUN_ID, repo_path="/tmp/clones/vulnapi", status="completed")
    base.update(overrides)
    return RunStateModel(**base)


def test_identity_prefers_the_index_a05_published():
    pointer = {
        "repository_id": "published-id",
        "head_sha": "abc123def456",
        "repository_hash": "hash-9",
    }
    identity = repository_identity(_state(), pointer)

    assert identity["repositoryId"] == "published-id"
    assert identity["headSha"] == "abc123def456"
    assert identity["repositoryHash"] == "hash-9"


def test_identity_falls_back_to_the_canonical_function():
    # A0.5 disabled or not yet run. The fallback must produce the same id A0.5
    # itself would, or the two layers would disagree about what a repository is.
    identity = repository_identity(_state(), None)
    assert identity["repositoryId"] == compute_repository_id("/tmp/clones/vulnapi")


def test_identity_is_stable_across_runs_of_the_same_repository():
    first = repository_identity(_state(run_id="run-1"), None)
    second = repository_identity(_state(run_id="run-2"), None)
    assert first["repositoryId"] == second["repositoryId"]


def test_head_sha_falls_back_to_the_recorded_base_commit():
    identity = repository_identity(_state(base_commit_sha="deadbeef"), None)
    assert identity["headSha"] == "deadbeef"


def test_unobserved_head_sha_is_null_rather_than_guessed():
    identity = repository_identity(_state(), None)
    assert identity["headSha"] is None
    assert identity["repositoryHash"] is None


def test_missing_repo_path_yields_no_repository_id():
    identity = repository_identity(_state(repo_path=""), None)
    assert identity["repositoryId"] is None


def test_header_carries_identity_alongside_the_existing_contract():
    header = build_workspace_header(
        _state(), [], {"repository_id": "rid", "head_sha": "sha"}
    )

    # Existing V1 keys are untouched.
    for key in ("repository", "branch", "shortRunId", "retries", "executionTime", "decisionLabel"):
        assert key in header

    assert header["repositoryId"] == "rid"
    assert header["headSha"] == "sha"
    assert header["repositoryName"] == header["repository"]


def test_header_without_a_pointer_still_returns_every_key():
    header = build_workspace_header(_state(), [])
    for key in ("repositoryId", "repositoryName", "headSha", "repositoryHash"):
        assert key in header
