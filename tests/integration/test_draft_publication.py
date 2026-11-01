"""User-triggered publication with real local Git and a mocked GitHub API."""

from unittest.mock import AsyncMock

import fakeredis.aioredis
import pytest
from git import Repo
from httpx import ASGITransport, AsyncClient

from backend.agents.a10_mci_scorer import A10MCIScorerAgent
from backend.api.deps import get_settings_dep
from backend.config import Settings
from backend.main import create_app
from backend.services.github_pr import GitHubPRService
from backend.services.draft_publication import publish_draft
from backend.state.redis_store import RedisStore
from backend.state.schema import RunStateModel


async def prepared_run(tmp_path, monkeypatch):
    remote = Repo.init(tmp_path / "remote.git", bare=True)
    work = tmp_path / "work"
    repo = Repo.init(work)
    repo.git.config("user.email", "proofix@example.invalid")
    repo.git.config("user.name", "ProoFix Test")
    (work / "app.py").write_text("def valid():\n    return False\n")
    repo.index.add(["app.py"])
    repo.index.commit("baseline")
    base = repo.head.commit.hexsha
    repo.create_remote("origin", str(tmp_path / "remote.git"))
    patched = "def valid():\n    return True\n"
    (work / "app.py").write_text(patched)
    settings = Settings(stub_mode=True, github_dry_run=False, github_token="dummy")
    store = RedisStore(fakeredis.aioredis.FakeRedis(), settings)
    state = RunStateModel(
        run_id="click-publication",
        repo_path=str(work),
        repo_clone_path=str(work),
        base_commit_sha=base,
        patch_bundle={
            "patches": [
                {
                    "file": "app.py",
                    "original": "def valid():\n    return False\n",
                    "patched": patched,
                }
            ]
        },
        reproduction={"status": "CONFIRMED"},
        mutation_result={"pytest_passed": True},
    )
    monkeypatch.setattr(GitHubPRService, "_publication_target", lambda *args: "team/submitted")
    agent = A10MCIScorerAgent(store, settings)
    agent.emit_status = AsyncMock()
    state = await agent.run(state)
    await store.save_state(state)
    return state, store, settings, repo, remote


@pytest.mark.asyncio
async def test_report_does_not_publish_then_click_creates_one_draft(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        GitHubPRService,
        "create_pr",
        lambda self, **kw: calls.append(kw) or "https://github.com/team/submitted/pull/1",
    )
    state, store, settings, repo, remote = await prepared_run(tmp_path, monkeypatch)
    assert state.publication_result["status"] == "ready"
    assert state.pr_decision["pr_url"] is None
    assert not calls
    assert not list(remote.refs)
    app = create_app()
    app.state.redis = store.client
    app.dependency_overrides[get_settings_dep] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/runs/click-publication/draft-pr")
        assert first.status_code == 200, first.text
        assert first.json()["publication"]["status"] == "published"
        assert first.json()["prUrl"].endswith("/1")
        again = await client.post("/api/runs/click-publication/draft-pr")
        assert again.status_code == 200
    assert len(calls) == 1 and calls[0]["draft"] is True
    assert calls[0]["repository"] == "team/submitted"
    assert remote.commit("refs/heads/proofix-fix-click-pu").hexsha == repo.head.commit.hexsha
    await store.client.aclose()


@pytest.mark.asyncio
async def test_retry_reuses_prepared_commits_after_api_failure(tmp_path, monkeypatch):
    state, store, settings, repo, _ = await prepared_run(tmp_path, monkeypatch)
    original_head = repo.head.commit.hexsha

    def fail(*args, **kwargs):
        raise RuntimeError("private provider text")

    monkeypatch.setattr(GitHubPRService, "create_pr", fail)
    failed = await publish_draft(store, settings, state.run_id)
    assert failed.publication_result["status"] == "failed"
    assert "private" not in failed.publication_result["reason"]
    monkeypatch.setattr(
        GitHubPRService, "create_pr", lambda *a, **kw: "https://github.com/team/submitted/pull/1"
    )
    published = await publish_draft(store, settings, state.run_id)
    assert published.publication_result["status"] == "published"
    assert repo.head.commit.hexsha == original_head
    await store.client.aclose()


@pytest.mark.asyncio
async def test_second_request_cannot_publish_while_first_is_running(tmp_path, monkeypatch):
    from fastapi import HTTPException

    state, store, settings, _, _ = await prepared_run(tmp_path, monkeypatch)
    # Same distributed lease used by a different server process.
    await store.client.set(f"bugfix:{state.run_id}:publication-lock", "other-request", ex=120)
    with pytest.raises(HTTPException) as error:
        await publish_draft(store, settings, state.run_id)
    assert error.value.status_code == 409
    assert await store.client.get(f"bugfix:{state.run_id}:publication-lock") == b"other-request"
    await store.client.aclose()


@pytest.mark.asyncio
async def test_changed_checkout_is_not_published(tmp_path, monkeypatch):
    state, store, settings, repo, _ = await prepared_run(tmp_path, monkeypatch)
    (tmp_path / "work" / "app.py").write_text("def valid(): return None\n")
    result = await publish_draft(store, settings, state.run_id)
    assert result.publication_result["status"] == "failed"
    assert result.pr_decision["pr_url"] is None
    await store.client.aclose()


@pytest.mark.asyncio
async def test_interrupted_publication_becomes_retryable_without_overwriting_state(
    tmp_path, monkeypatch
):
    from backend.services.draft_publication import refresh_publication_status

    state, store, _, _, _ = await prepared_run(tmp_path, monkeypatch)
    state.publication_result["status"] = "publishing"
    await store.save_state(state)
    projected = await refresh_publication_status(store, state)
    assert projected.publication_result["status"] == "failed"
    assert "Retry" in projected.publication_result["reason"]
    assert (await store.load_state(state.run_id)).publication_result["status"] == "publishing"
    await store.client.aclose()


@pytest.mark.asyncio
async def test_runner_retains_prepared_checkout(tmp_path, monkeypatch):
    from backend.orchestrator.runner import PipelineRunner

    state, store, settings, _, _ = await prepared_run(tmp_path, monkeypatch)
    removed = []
    monkeypatch.setattr(
        "backend.orchestrator.runner.discard_clone", lambda path: removed.append(path)
    )
    await PipelineRunner(store, settings)._discard_clone(state.run_id)
    assert not removed
    await store.client.aclose()


def test_expired_prepared_checkouts_are_cleaned(tmp_path, monkeypatch):
    from backend.services.draft_publication import cleanup_expired_publications, retain_checkout
    from backend.services.git_service import CLONE_PREFIX

    expired = tmp_path / f"{CLONE_PREFIX}expired"
    fresh = tmp_path / f"{CLONE_PREFIX}fresh"
    for path in [expired, fresh]:
        Repo.init(path)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    retain_checkout(str(expired), -1)
    retain_checkout(str(fresh), 3600)
    cleanup_expired_publications()
    assert not expired.exists()
    assert fresh.exists()


@pytest.mark.asyncio
async def test_missing_checkout_is_explained_before_user_clicks(tmp_path, monkeypatch):
    from backend.services.draft_publication import refresh_publication_status

    state, store, _, _, _ = await prepared_run(tmp_path, monkeypatch)
    state.repo_clone_path = str(tmp_path / "expired-checkout")
    projected = await refresh_publication_status(store, state)
    assert projected.publication_result["status"] == "disabled"
    assert projected.publication_result["prepared_head_sha"] is None
    assert "new repair run" in projected.publication_result["reason"]
    await store.client.aclose()


@pytest.mark.asyncio
async def test_publication_cannot_switch_repositories_after_preparation(tmp_path, monkeypatch):
    state, store, settings, _, _ = await prepared_run(tmp_path, monkeypatch)
    monkeypatch.setattr(GitHubPRService, "_publication_target", lambda *args: "team/other")
    result = await publish_draft(store, settings, state.run_id)
    assert result.publication_result["status"] == "failed"
    assert result.pr_decision["pr_url"] is None
    await store.client.aclose()
