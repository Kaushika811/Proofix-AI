"""Controlled local Git proof publication; no GitHub request is made."""

import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from git import Repo

from backend.config import Settings
from backend.services.github_pr import GitHubPRService
from backend.services.proof_bundle import (
    build_verification_bundle, generate_verification_workflow, proof_artifact_hash,
    proof_bundle_relative_path, workflow_relative_path,
)
from backend.state.schema import RunStateModel

BASE = "def valid():\n    return False\n"
PATCH = "def valid():\n    return True\n"


def _setup(tmp_path):
    remote = Repo.init(tmp_path / "remote.git", bare=True)
    work = tmp_path / "work"
    repo = Repo.init(work)
    repo.git.config("user.email", "proofix@example.invalid")
    repo.git.config("user.name", "Proofix Test")
    (work / "app.py").write_text(BASE)
    repo.index.add(["app.py"])
    repo.index.commit("base")
    base = repo.head.commit.hexsha
    repo.create_remote("origin", str(remote.working_tree_dir or tmp_path / "remote.git"))
    (work / "app.py").write_text(PATCH)
    state = RunStateModel(run_id="publication", repo_path=str(work), base_commit_sha=base,
                          reproduction={"status": "CONFIRMED", "reexecution_command": "python -m pytest tests/test_app.py"},
                          reproduction_confidence="exact_test",
                          mutation_result={"pytest_passed": True})
    bundle = build_verification_bundle(state)
    settings = Settings(github_dry_run=False, github_token="dummy",
                        github_repo_owner="example", github_repo_name="sample")
    service = GitHubPRService(settings)
    # Local bare remote exercises real push/commit without a GitHub write.
    service._publication_target = lambda repo: "example/sample"
    return repo, remote, service, bundle


def _publish(repo, service, bundle):
    return service.publish_fix_with_proof(
        repo_path=repo.working_tree_dir, branch="proofix-test",
        patch_files={"app.py": PATCH}, bundle=bundle, commit_message="fix: valid",
        title="fix: valid", draft=True, why_section="wrong result",
        what_section="return True", expected_source_hashes={
            "app.py": {"patched": hashlib.sha256(PATCH.encode()).hexdigest()}
        },
    )


def test_proof_commit_preserves_tested_code_and_body_matches_manifest(tmp_path, monkeypatch):
    repo, remote, service, bundle = _setup(tmp_path)
    bodies = []
    monkeypatch.setattr(service, "create_pr", lambda **kwargs: bodies.append(kwargs["body"]) or
                        "https://github.com/example/sample/pull/1")
    result, final = _publish(repo, service, bundle)
    assert result.status == "published", result.reason
    assert result.tested_patch_sha == repo.head.commit.parents[0].hexsha
    assert result.published_head_sha == repo.head.commit.hexsha
    assert result.tested_patch_sha != result.published_head_sha
    assert remote.commit("refs/heads/proofix-test").hexsha == result.published_head_sha
    assert repo.git.merge_base("--is-ancestor", result.tested_patch_sha,
                               result.published_head_sha) == ""
    assert result.pr_url and result.pr_url.endswith("/1")
    assert result.proof_artifact_hash == proof_artifact_hash(final)
    assert result.tested_patch_sha in bodies[0]
    assert result.published_head_sha in bodies[0]
    assert result.proof_artifact_hash in bodies[0]
    artifact = repo.git.show(f"{result.published_head_sha}:{proof_bundle_relative_path(final)}")
    assert json.loads(artifact)["bundle_hash"] == final.bundle_hash
    assert json.loads(artifact)["steps"][1]["patch_commit"] == result.tested_patch_sha
    assert bundle.steps[1].patch_commit == ""  # caller's preliminary snapshot was not mutated


def test_workflow_runs_on_source_test_and_proof_changes_and_checks_current_head(tmp_path):
    _, _, _, bundle = _setup(tmp_path)
    final = bundle.model_copy(deep=True)
    for step in final.steps:
        step.patch_commit = "a" * 40
    yaml = generate_verification_workflow(final)
    assert "types: [opened, synchronize, reopened]" in yaml
    assert "paths:" not in yaml  # any PR update, including source-only or test-only
    assert "github.event.pull_request.head.sha" in yaml
    assert "Source or test changed after tested commit" in yaml
    assert proof_bundle_relative_path(final) in yaml
    assert workflow_relative_path(final) in yaml


@pytest.mark.parametrize("changed_path", ["app.py", "tests/test_app.py"])
def test_later_source_or_test_commit_is_stale_against_tested_sha(tmp_path, monkeypatch, changed_path):
    repo, _, service, bundle = _setup(tmp_path)
    monkeypatch.setattr(service, "create_pr", lambda **kwargs: "https://example.invalid/pull/1")
    result, _ = _publish(repo, service, bundle)
    assert result.status == "published"
    path = tmp_path / "work" / changed_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# changed after validation\n")
    repo.index.add([changed_path])
    repo.index.commit("post-proof change")
    changed = set(repo.git.diff("--name-only", result.tested_patch_sha, repo.head.commit.hexsha).splitlines())
    assert changed_path in changed
    assert changed_path not in {proof_bundle_relative_path(bundle), workflow_relative_path(bundle)}


def test_later_proof_only_change_breaks_artifact_hash(tmp_path, monkeypatch):
    repo, _, service, bundle = _setup(tmp_path)
    monkeypatch.setattr(service, "create_pr", lambda **kwargs: "https://example.invalid/pull/1")
    result, final = _publish(repo, service, bundle)
    assert result.status == "published"
    path = tmp_path / "work" / proof_bundle_relative_path(final)
    path.write_text(path.read_text() + "\n")
    assert hashlib.sha256(path.read_bytes()).hexdigest() != result.proof_artifact_hash


def test_push_failure_is_failed_not_published(tmp_path):
    repo, _, service, bundle = _setup(tmp_path)
    repo.delete_remote(repo.remote("origin"))
    result, final = _publish(repo, service, bundle)
    assert result.status == "failed"
    assert result.pr_url is None
    assert result.published_head_sha is None
    assert result.tested_patch_sha == final.steps[1].patch_commit
    assert result.reason.startswith("GitHub push failed (")


def test_unavailable_base_commit_is_rejected_before_push(tmp_path):
    repo, _, service, bundle = _setup(tmp_path)
    for step in bundle.steps:
        step.base_commit = "f" * 40
    result, _ = _publish(repo, service, bundle)
    assert result.status == "failed"
    assert result.pr_url is None
    assert result.published_head_sha is None


def test_pr_creation_failure_retains_unpublished_evidence(tmp_path, monkeypatch):
    repo, _, service, bundle = _setup(tmp_path)

    def fail(**kwargs):
        raise RuntimeError("token=secret")

    monkeypatch.setattr(service, "create_pr", fail)
    result, final = _publish(repo, service, bundle)
    assert result.status == "failed"
    assert result.pr_url is None
    assert result.published_head_sha
    assert result.proof_artifact_hash == proof_artifact_hash(final)
    assert "secret" not in result.reason


def test_stale_remote_head_or_pr_body_is_rejected(tmp_path, monkeypatch):
    repo, _, service, bundle = _setup(tmp_path)
    monkeypatch.setattr(service, "_verify_remote_branch", lambda *args: (_ for _ in ()).throw(ValueError("stale")))
    result, _ = _publish(repo, service, bundle)
    assert result.status == "failed"
    assert result.published_head_sha is None

    repo2, _, service2, bundle2 = _setup(tmp_path / "second")
    monkeypatch.setattr(service2, "format_pr_body_with_proof", lambda *args, **kwargs: "stale")
    result2, _ = _publish(repo2, service2, bundle2)
    assert result2.status == "failed"
    assert result2.pr_url is None


def test_disabled_and_dry_run_are_distinct(tmp_path):
    repo, _, _, bundle = _setup(tmp_path)
    disabled = GitHubPRService(Settings(github_dry_run=False, github_token=""))
    dry = GitHubPRService(Settings(github_dry_run=True, github_token=""))
    disabled_result, _ = _publish(repo, disabled, bundle)
    dry_result, _ = _publish(repo, dry, bundle)
    assert disabled_result.status == "disabled"
    assert dry_result.status == "dry_run"
    assert disabled_result.pr_url is None and dry_result.pr_url is None


def test_existing_pr_body_is_updated_on_republication(tmp_path, monkeypatch):
    _, _, service, _ = _setup(tmp_path)
    existing = MagicMock(html_url="https://github.com/example/sample/pull/1")
    github_repo = MagicMock()
    github_repo.get_pulls.return_value = [existing]
    client = MagicMock()
    client.get_repo.return_value = github_repo
    monkeypatch.setitem(sys.modules, "github", SimpleNamespace(Github=lambda token: client))

    url = service.create_pr(title="fix", body="new proof identity", branch="proofix-test")
    assert url == existing.html_url
    existing.edit.assert_called_once_with(body="new proof identity")
    github_repo.create_pull.assert_not_called()


@pytest.mark.parametrize('url', [
    'https://github.com/team/submitted.git',
    'git@github.com:team/submitted.git',
    'ssh://git@github.com/team/submitted.git',
])
def test_destination_comes_from_submitted_origin(tmp_path, url):
    repo = Repo.init(tmp_path / 'target')
    repo.create_remote('origin', url)
    assert GitHubPRService._publication_target(repo) == 'team/submitted'


def test_different_push_destination_is_rejected(tmp_path):
    repo = Repo.init(tmp_path / 'target')
    repo.create_remote('origin', 'https://github.com/team/submitted.git')
    repo.git.config('remote.origin.pushurl', 'https://github.com/team/other.git')
    with pytest.raises(ValueError):
        GitHubPRService._publication_target(repo)


def test_pr_uses_run_destination_and_its_default_branch(monkeypatch):
    github_repo = MagicMock(default_branch='develop')
    github_repo.get_pulls.return_value = []
    github_repo.create_pull.return_value.html_url = 'https://github.com/team/submitted/pull/4'
    client = MagicMock()
    client.get_repo.return_value = github_repo
    monkeypatch.setitem(sys.modules, 'github', SimpleNamespace(Github=lambda token: client))
    service = GitHubPRService(Settings(github_token='dummy', github_dry_run=False,
                                     github_repo_owner='wrong', github_repo_name='wrong'))
    service.create_pr(title='fix', body='proof', branch='repair', draft=True,
                      repository='team/submitted')
    client.get_repo.assert_called_once_with('team/submitted')
    github_repo.create_pull.assert_called_once_with(title='fix', body='proof', head='repair', base='develop', draft=True)


@pytest.mark.asyncio
async def test_real_mutation_then_publication_does_not_commit_tool_artifacts(tmp_path, monkeypatch):
    from backend.agents.a8_mutation_validator import A8MutationValidatorAgent
    repo, remote, service, bundle = _setup(tmp_path)
    root = tmp_path / 'work'
    (root / 'test_app.py').write_text('from app import valid\n\ndef test_valid():\n    assert valid() is True\n')
    repo.index.add(['test_app.py'])
    repo.index.commit('add acceptance test')
    agent = A8MutationValidatorAgent(MagicMock(), Settings(stub_mode=True))
    outcome, _ = await agent._run_mutmut(root, {'patches': [{'file': 'app.py'}]})
    assert outcome.status == 'scored', outcome.unavailable_reason
    assert outcome.killed_mutants > 0
    monkeypatch.setattr(service, 'create_pr', lambda **kwargs: 'https://github.com/example/sample/pull/1')
    result, _ = _publish(repo, service, bundle)
    assert result.status == 'published', result.reason
    assert remote.commit('refs/heads/proofix-test').hexsha == result.published_head_sha
    assert not (root / 'setup.cfg').exists()
    assert not (root / 'mutants').exists()


def test_https_auth_is_temporary_and_does_not_change_origin(tmp_path):
    repo = Repo.init(tmp_path / 'auth')
    url = 'https://github.com/team/submitted.git'
    repo.create_remote('origin', url)
    service = GitHubPRService(Settings(github_token='test-token', github_dry_run=False))
    with service._git_auth(repo):
        assert repo.git._environment['GIT_TERMINAL_PROMPT'] == '0'
        assert repo.git._environment['GIT_CONFIG_KEY_0'] == 'http.https://github.com/.extraheader'
        assert repo.git._environment['GIT_CONFIG_VALUE_0'].startswith('AUTHORIZATION: basic ')
        assert repo.remote('origin').url == url
    assert 'GIT_CONFIG_VALUE_0' not in repo.git._environment
    assert 'test-token' not in (tmp_path / 'auth' / '.git' / 'config').read_text()


@pytest.mark.parametrize(("diagnostic", "expected"), [
    ("refusing to allow a Personal Access Token to create or update workflow without workflow scope", "workflow update"),
    ("fatal: Authentication failed", "authentication failed"),
    ("remote: Permission to owner/repo denied", "denied repository access"),
    ("remote: GH013: Repository rule violations", "repository rules"),
    ("fatal: Could not resolve host: github.com", "could not reach GitHub"),
    ("! [rejected] branch (non-fast-forward)", "conflicting history"),
    ("unexpected error", "could not be classified"),
])
def test_push_failure_hint_classifies_without_exposing_credentials(diagnostic, expected):
    from git.exc import GitCommandError
    secret = "private-token-value"
    error = GitCommandError(["git", "push", "https://" + secret + "@github.com/x/y"], 128, stderr=diagnostic)
    hint = GitHubPRService._push_failure_hint(error)
    assert expected in hint
    assert secret not in hint
