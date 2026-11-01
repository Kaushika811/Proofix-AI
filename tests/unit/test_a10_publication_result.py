from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.a10_mci_scorer import A10MCIScorerAgent
from backend.config import Settings
from backend.models.pr import PublicationResult
from backend.services.ui_projection import run_decision
from backend.state.schema import RunStateModel


@pytest.mark.asyncio
async def test_publication_exception_keeps_validated_evidence_but_reports_unpublished(monkeypatch, tmp_path):
    store = MagicMock()
    store.set_json = AsyncMock()
    store.append_event = AsyncMock()
    agent = A10MCIScorerAgent(store, Settings(stub_mode=True, github_dry_run=False))
    state = RunStateModel(
        run_id="publication-error", repo_path=str(tmp_path), repo_clone_path=str(tmp_path),
        patch_bundle={"patches": [{"file": "app.py", "patched": "def fixed(): return True\n"}]},
        reproduction={"status": "CONFIRMED"},
        mutation_result={"pytest_passed": True, "correctness_score": 100.0},
    )

    def fail(*args, **kwargs):
        raise RuntimeError("secret token")

    monkeypatch.setattr("backend.agents.a10_mci_scorer.GitHubPRService.publish_fix_with_proof", fail)
    result = await agent.run(state)
    assert result.status == "completed"  # repair assessment can finish
    assert result.publication_result["status"] == "failed"
    assert result.pr_decision["pr_url"] is None
    assert "unpublished" in result.pr_decision["review_note"]
    assert "secret" not in result.publication_result["reason"]
    assert result.proof_bundle is not None
    assert run_decision(result) == ("draft", "Review required")


@pytest.mark.asyncio
async def test_root_cause_test_reference_is_not_a_phantom_patch_change(monkeypatch, tmp_path):
    """The pre-existing failure context is not a claim that a test was edited."""
    store = MagicMock()
    store.set_json = AsyncMock()
    store.append_event = AsyncMock()
    agent = A10MCIScorerAgent(store, Settings(stub_mode=True, github_dry_run=True))
    diff = (
        "--- a/shopcore/auth.py\n+++ b/shopcore/auth.py\n"
        "@@ -3 +3 @@\n-    return expires_at >= 0\n"
        "+    return expires_at > now\n"
    )
    state = RunStateModel(
        run_id="context-not-phantom", repo_path=str(tmp_path),
        root_cause={
            "summary": "tests/test_auth.py shows that is_session_active accepts expired sessions",
            "diagnosis_status": "incomplete",
        },
        patch_bundle={
            "patches": [{"file": "shopcore/auth.py", "patched": "def is_session_active(): pass\n"}],
            "diff_text": diff,
        },
    )
    monkeypatch.setattr(
        "backend.agents.a10_mci_scorer.GitHubPRService.publish_fix_with_proof",
        lambda _self, **kwargs: (PublicationResult(status="dry_run"), kwargs["bundle"]),
    )

    result = await agent.run(state)

    assert result.pr_decision["phantom_changes_detected"] is False
    assert "tests/test_auth.py" in result.pr_decision["description_why"]
    assert "shopcore/auth.py" in result.pr_decision["description_what"]
    assert result.root_cause["diagnosis_status"] == "incomplete"


@pytest.mark.asyncio
async def test_missing_pr_url_cannot_be_reported_as_published(monkeypatch, tmp_path):
    store = MagicMock()
    store.set_json = AsyncMock()
    store.append_event = AsyncMock()
    agent = A10MCIScorerAgent(store, Settings(stub_mode=True, github_dry_run=False))
    state = RunStateModel(
        run_id="publication-no-url", repo_path=str(tmp_path),
        patch_bundle={"patches": [{"file": "app.py", "patched": "def fixed(): return True\n"}]},
    )
    monkeypatch.setattr(
        "backend.agents.a10_mci_scorer.GitHubPRService.publish_fix_with_proof",
        lambda _self, **kwargs: (PublicationResult(
            status="published", tested_patch_sha="a" * 40,
            published_head_sha="b" * 40, proof_artifact_hash="c" * 64,
        ), kwargs["bundle"]),
    )
    result = await agent.run(state)
    assert result.publication_result["status"] == "failed"
    assert result.pr_decision["pr_url"] is None
