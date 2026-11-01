"""Execute generated tests against real buggy/fixed Python, without model calls."""

import hashlib
import shlex
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from backend.agents.a3_5_reproduction import A35ReproductionAgent
from backend.agents.a5_blast_graph import A5BlastGraphAgent
from backend.agents.a10_mci_scorer import A10MCIScorerAgent
from backend.api.routes.ui import CreateRunBody
from backend.config import Settings
from backend.models.sig import FileNode, SemanticIntentGraph
from backend.services.issue_targeting import resolved_issue_target
from backend.services.issue_test_generation import (
    GeneratedTestOutput,
    _static_validation_errors,
    generate_issue_test,
    generated_test_integrity_error,
    generated_test_replay_command,
    validate_test_code,
)
from backend.services.proof_bundle import build_verification_bundle, generate_verification_workflow
from backend.services.scoped_validation import run_scoped_validation
from backend.services.subprocess_runner import PYTHON, run_command
from backend.state.schema import RunStateModel

from benchmarks.generator.benchmark import (
    SQL_TEST,
    _clone,
    _execute,
    _prepare_database,
    _state,
    cases,
)

TEST_CODE = (
    "from pricing import total\n\ndef test_reported_issue():\n    assert total(10, 2) == 20\n"
)


@pytest.fixture
def setup_issue(tmp_path, monkeypatch):
    (tmp_path / "pricing.py").write_text("def total(price, count):\n    return price + count\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_existing.py").write_text("def test_existing():\n    assert True\n")
    sig = SemanticIntentGraph(
        repo_path=str(tmp_path),
        files={
            "pricing.py": FileNode(path="pricing.py", role="internal-util"),
            "tests/test_existing.py": FileNode(path="tests/test_existing.py", role="test-only"),
        },
    )
    state = RunStateModel(
        run_id=f"generated-{tmp_path.name}",
        repo_path=str(tmp_path),
        sig=sig.model_dump(mode="json"),
        issue_contract={
            "description": "total adds instead of multiplying",
            "target_test": "",
            "expected_behavior": "total(10, 2) must be 20",
            "target_file": "",
        },
    )
    structured = AsyncMock(
        return_value=GeneratedTestOutput(
            code=TEST_CODE, rationale="Two units must cost twice the unit price"
        )
    )
    monkeypatch.setattr("backend.services.issue_test_generation.LLMService.structured", structured)
    settings = Settings(
        stub_mode=False, llm_provider="anthropic", anthropic_api_key="test-key", github_dry_run=True
    )
    store = AsyncMock()
    store.get_json.return_value = None
    return state, settings, store, structured


def test_api_accepts_expectation_without_existing_test():
    body = CreateRunBody(
        url="repo", issue_hint="wrong total", expected_behavior="Multiply price by count"
    )
    assert body.issue_contract()["target_test"] == ""
    with pytest.raises(ValidationError):
        CreateRunBody(url="repo", expected_behavior="Multiply price by count")


@pytest.mark.parametrize(
    "code",
    [
        "def test_reported_issue():\n    assert False\n",
        "import pytest\n@pytest.mark.skip\ndef test_reported_issue():\n    assert total() == 1\n",
        "def test_reported_issue():\n    return\n    assert total() == 1\n",
        "import os\ndef test_reported_issue():\n    assert total() == 1\n",
        "from pricing import total\ndef test_reported_issue(monkeypatch):\n    monkeypatch.setattr(total, 'x', 1)\n    assert total() == 1\n",
    ],
)
def test_rejects_vacuous_or_unsupported_generated_tests(code):
    with pytest.raises(ValueError):
        validate_test_code(code)


@pytest.mark.asyncio
async def test_generation_reproduces_and_resolves_source_then_validates_same_test(
    setup_issue, tmp_path
):
    state, settings, store, structured = setup_issue
    state = await A35ReproductionAgent(store, settings).run(state)
    assert state.reproduction["status"] == "CONFIRMED", state.reproduction
    assert state.generated_test["before_status"] == "CONFIRMED"
    assert state.reproduction["failing_test"] == state.generated_test["nodeid"]
    assert state.generated_test["nodeid"] not in state.reproduction["pre_existing_failures"]
    assert structured.await_count == 1
    state = await A5BlastGraphAgent(store, settings).run(state)
    assert resolved_issue_target(state) == "pricing.py"

    # A real patch changes production behavior; the generated test is frozen.
    (tmp_path / "pricing.py").write_text("def total(price, count):\n    return price * count\n")
    outcome = await run_scoped_validation(
        tmp_path,
        state.run_id,
        target_test=state.generated_test["nodeid"],
        baseline_failures=state.reproduction["pre_existing_failures"],
        generated_test=state.generated_test,
    )
    assert outcome.target_test_passed is True
    assert outcome.regression_tests_passed is True
    assert generated_test_integrity_error(tmp_path, state.generated_test) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source, test_code",
    [
        ("def total(price, count):\n    return price * count\n", TEST_CODE),  # already passes
        (
            "def total(price, count):\n    return price + count\n",
            TEST_CODE.replace("total(10, 2)", "total(10)"),
        ),  # invented API
        (
            "def total(price, count):\n    return price + count\n",
            TEST_CODE.replace("from pricing", "from missing_module"),
        ),
    ],
)
async def test_non_bug_failures_and_already_passing_tests_do_not_authorize_repair(
    setup_issue, tmp_path, source, test_code
):
    state, settings, store, structured = setup_issue
    (tmp_path / "pricing.py").write_text(source)
    structured.return_value = GeneratedTestOutput(code=test_code, rationale="A candidate assertion")
    state = await A35ReproductionAgent(store, settings).run(state)
    assert state.reproduction["status"] != "CONFIRMED"
    assert resolved_issue_target(state) is None


@pytest.mark.asyncio
async def test_generation_unavailable_does_not_select_unrelated_test(setup_issue, tmp_path):
    state, _, store, structured = setup_issue
    (tmp_path / "tests/test_existing.py").write_text("def test_existing():\n    assert False\n")
    state = await A35ReproductionAgent(store, Settings(stub_mode=True)).run(state)
    assert state.generated_test["status"] == "unavailable"
    assert state.reproduction["status"] == "UNCONFIRMED"
    assert state.reproduction["failing_test"] is None
    structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_assertion_unrelated_to_any_repository_api(setup_issue, tmp_path):
    state, settings, _, structured = setup_issue
    structured.return_value = GeneratedTestOutput(
        code="def test_reported_issue():\n    actual = 12\n    assert actual == 20\n",
        rationale="A false assertion",
    )
    result = await generate_issue_test(state, tmp_path, settings)
    assert result["status"] == "rejected"
    assert "repository API" in result["reason"]
    assert result["attempts"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first, error",
    [
        (
            "from unittest.mock import patch\nfrom pricing import total\n"
            "def test_reported_issue():\n    with patch('pricing.total'):\n"
            "        assert total(10, 2) == 20\n",
            "Test imports an unsupported module",
        ),
        (
            "from pricing import total\ndef test_reported_issue():\n"
            "    assert total(make_price(), 2) == 20\n",
            "Test references undefined helper or name: make_price",
        ),
        (
            "from pricing import total\ndef test_reported_issue():\n"
            "    assert total('valid.token.here', 2) == 20\n",
            "Test uses a placeholder or redacted value instead of real test data",
        ),
    ],
)
async def test_static_rejection_retries_once_with_exact_error_and_keeps_both_candidates(
    setup_issue, tmp_path, first, error
):
    state, settings, _, structured = setup_issue
    structured.side_effect = [
        GeneratedTestOutput(code=first, rationale="first attempt"),
        GeneratedTestOutput(code=TEST_CODE, rationale="real API behavior"),
    ]
    result = await generate_issue_test(state, tmp_path, settings)
    assert result["status"] == "generated"
    assert result["attempts"] == 2
    assert result["candidates"][0] == {
        "code": first,
        "rationale": "first attempt",
        "status": "rejected",
        "reason": error,
        "validation_errors": [error],
    }
    assert result["candidates"][1]["status"] == "accepted"
    assert (tmp_path / result["path"]).read_text() == TEST_CODE
    assert structured.await_count == 2
    assert error in structured.await_args_list[1].args[0]
    assert first not in structured.await_args_list[1].args[0]
    if "undefined helper" in error:
        assert "explicitly import a listed existing helper" in structured.await_args_list[1].args[0]
    system = structured.await_args_list[0].kwargs["system"]
    assert "Do not define helper functions" in system
    assert "Do not import os" in system
    assert "Cover every explicit clause" in system


@pytest.mark.asyncio
async def test_retry_stops_after_second_static_rejection_without_writing_test(
    setup_issue, tmp_path
):
    state, settings, _, structured = setup_issue
    invalid = "from pricing import total\ndef test_reported_issue():\n    assert total(missing(), 2) == 20\n"
    structured.return_value = GeneratedTestOutput(code=invalid, rationale="undefined helper")
    result = await generate_issue_test(state, tmp_path, settings)
    assert result["status"] == "rejected"
    assert result["attempts"] == 2
    assert len(result["candidates"]) == 2
    assert all(item["status"] == "rejected" for item in result["candidates"])
    assert structured.await_count == 2
    assert not list((tmp_path / "tests").glob("test_proofix_*.py"))


@pytest.mark.asyncio
async def test_retry_receives_all_exact_static_errors_without_candidate_source(
    setup_issue, tmp_path
):
    state, settings, _, structured = setup_issue
    first = (
        "from pricing import total\ndef test_reported_issue():\n"
        "    assert total(missing(), '<REDACTED_EMAIL>') == 20\n"
    )
    structured.side_effect = [
        GeneratedTestOutput(code=first, rationale="bad test data"),
        GeneratedTestOutput(code=TEST_CODE, rationale="real values"),
    ]
    result = await generate_issue_test(state, tmp_path, settings)
    assert result["status"] == "generated"
    errors = result["candidates"][0]["validation_errors"]
    assert errors == [
        "Test references undefined helper or name: missing",
        "Test uses a placeholder or redacted value instead of real test data",
    ]
    retry_prompt = structured.await_args_list[1].args[0]
    assert all(f"- {error}" in retry_prompt for error in errors)
    assert first not in retry_prompt


@pytest.mark.asyncio
async def test_existing_token_helper_is_identified_and_generated_test_discriminates_patches(
    setup_issue,
):
    _, settings, _, structured = setup_issue
    case = cases()[0]
    code = (
        "import time\nfrom vulnapi.auth import validate_token\n"
        "from tests.test_auth import _make_token\n\n"
        "def test_reported_issue():\n"
        "    assert validate_token(_make_token(exp=int(time.time()) - 3600)) is False\n"
        "    assert validate_token(_make_token(exp=int(time.time()) + 3600)) is True\n"
    )
    structured.return_value = GeneratedTestOutput(code=code, rationale="real existing token helper")
    with tempfile.TemporaryDirectory(prefix="proofix-generator-bench-") as folder:
        repo = Path(folder).resolve()
        _clone(repo)
        state = _state(repo, "verified-token-helper", case)
        artifact = await generate_issue_test(state, repo, settings)
        assert artifact["status"] == "generated"
        assert artifact["attempts"] == 1
        assert "from tests.test_auth import _make_token" in structured.await_args.args[0]
        digest = artifact["sha256"]
        assert (await _execute(repo, artifact["nodeid"], None))[
            "outcome"
        ] == "behavioral_assertion_failed"
        for source, expected in (
            (case.correct_source, "passed"),
            (case.wrong_source, "behavioral_assertion_failed"),
            (case.unrelated_source, "behavioral_assertion_failed"),
        ):
            (repo / case.source_file).write_text(source)
            assert (await _execute(repo, artifact["nodeid"], None))["outcome"] == expected
            assert hashlib.sha256((repo / artifact["path"]).read_bytes()).hexdigest() == digest


@pytest.mark.asyncio
async def test_sql_result_requires_none_guard_then_wrong_patch_fails_by_assertion(setup_issue):
    _, settings, _, structured = setup_issue
    case = cases()[2]
    unsafe = (
        "from vulnapi.api import lookup_user\n\n"
        "def test_reported_issue():\n"
        "    assert lookup_user('1 OR 1=1') is None\n"
        "    user = lookup_user('1')\n"
        "    assert user['name'] == 'admin'\n"
    )
    structured.side_effect = [
        GeneratedTestOutput(code=unsafe, rationale="missing positive-control guard"),
        GeneratedTestOutput(code=SQL_TEST, rationale="guarded real lookup"),
    ]
    with tempfile.TemporaryDirectory(prefix="proofix-generator-bench-") as folder:
        repo = Path(folder).resolve()
        _clone(repo)
        assert await _prepare_database(repo) is None
        state = _state(repo, "guarded-sql-result", case)
        artifact = await generate_issue_test(state, repo, settings)
        assert artifact["status"] == "generated"
        assert artifact["attempts"] == 2
        assert artifact["candidates"][0]["validation_errors"] == [
            "Assert an API result is not None before accessing its fields"
        ]
        assert "assert that the result is not None" in structured.await_args_list[1].args[0]
        assert (await _execute(repo, artifact["nodeid"], None))[
            "outcome"
        ] == "behavioral_assertion_failed"
        for source, expected in (
            (case.correct_source, "passed"),
            (case.wrong_source, "behavioral_assertion_failed"),
            (case.unrelated_source, "behavioral_assertion_failed"),
        ):
            (repo / case.source_file).write_text(source)
            assert (await _execute(repo, artifact["nodeid"], None))["outcome"] == expected


@pytest.mark.parametrize(
    "guard, should_reject",
    [
        ("", True),
        ("    assert user is not None or True\n", True),
        ("    assert user is not None\n", False),
    ],
)
def test_sql_field_access_requires_standalone_none_guard(setup_issue, guard, should_reject):
    state, _, _, _ = setup_issue
    code = (
        "from pricing import total\n\ndef test_reported_issue():\n"
        "    user = total(10, 2)\n" + guard + "    assert user['name'] == 'admin'\n"
    )
    errors = _static_validation_errors(code, state)
    assert (
        "Assert an API result is not None before accessing its fields" in errors
    ) is should_reject


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expected",
    [
        "Handle admin access appropriately.",
        "Fix it",
        "",
        "It should work correctly",
        "Return the right value",
        "The result is False",
    ],
)
async def test_ambiguous_expectation_abstains_without_model_call(setup_issue, tmp_path, expected):
    state, settings, _, structured = setup_issue
    state.issue_contract["expected_behavior"] = expected
    result = await generate_issue_test(state, tmp_path, settings)
    assert result["status"] == "needs_clarification"
    assert result["attempts"] == 0
    assert result["candidates"] == []
    structured.assert_not_awaited()


@pytest.mark.asyncio
async def test_clear_contract_calls_model_and_explicit_model_abstention_is_recorded(
    setup_issue, tmp_path
):
    state, settings, _, structured = setup_issue
    structured.return_value = GeneratedTestOutput(
        code="",
        rationale="Expected result is unclear",
        needs_clarification=True,
        clarification_question="What should total(10, 2) return?",
    )
    result = await generate_issue_test(state, tmp_path, settings)
    assert result["status"] == "needs_clarification"
    assert result["attempts"] == 1
    assert result["reason"] == "What should total(10, 2) return?"
    assert structured.await_count == 1
    assert not list((tmp_path / "tests").glob("test_proofix_*.py"))


@pytest.mark.asyncio
async def test_test_is_never_overwritten_and_provider_failure_is_bounded(setup_issue, tmp_path):
    state, settings, _, structured = setup_issue
    artifact = await generate_issue_test(state, tmp_path, settings)
    assert artifact["status"] == "generated"
    duplicate = await generate_issue_test(state, tmp_path, settings)
    assert duplicate["status"] == "rejected"
    assert (tmp_path / artifact["path"]).read_text() == TEST_CODE
    structured.side_effect = TimeoutError()
    failed = await generate_issue_test(state, tmp_path, settings)
    assert failed["status"] == "unavailable"
    assert failed["attempts"] == 1


@pytest.mark.asyncio
async def test_modified_test_cannot_count_as_passing_or_be_published(
    setup_issue, tmp_path, monkeypatch
):
    state, settings, store, _ = setup_issue
    state = await A35ReproductionAgent(store, settings).run(state)
    (tmp_path / state.generated_test["path"]).write_text(
        "def test_reported_issue():\n    assert True\n"
    )
    outcome = await run_scoped_validation(
        tmp_path,
        state.run_id,
        target_test=state.generated_test["nodeid"],
        baseline_failures=[],
        generated_test=state.generated_test,
    )
    assert outcome.target_test_passed is None
    assert outcome.pytest_available is False
    state.patch_bundle = {"patches": [{"file": "pricing.py", "patched": "fixed"}]}
    publish = AsyncMock(side_effect=AssertionError("must not publish"))
    monkeypatch.setattr(
        "backend.agents.a10_mci_scorer.GitHubPRService.publish_fix_with_proof", publish
    )
    result = await A10MCIScorerAgent(store, settings).run(state)
    assert result.pr_decision["pr_url"] is None
    assert "changed" in result.pr_decision["review_note"]
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_skipped_generated_test_is_unverified_even_with_exit_zero(setup_issue, tmp_path):
    state, settings, _, _ = setup_issue
    artifact = await generate_issue_test(state, tmp_path, settings)
    (tmp_path / "tests/conftest.py").write_text(
        "import pytest\ndef pytest_collection_modifyitems(items):\n"
        "    for item in items:\n        item.add_marker(pytest.mark.skip(reason='skip all'))\n"
    )
    outcome = await run_scoped_validation(
        tmp_path,
        state.run_id,
        target_test=artifact["nodeid"],
        baseline_failures=[],
        generated_test=artifact,
    )
    assert outcome.target_test_passed is None
    assert not outcome.pytest_passed


@pytest.mark.asyncio
async def test_proof_replays_identical_generated_test_on_base_and_patch(setup_issue, tmp_path):
    state, settings, _, _ = setup_issue
    artifact = await generate_issue_test(state, tmp_path, settings)
    command = generated_test_replay_command(artifact)
    args = shlex.split(command)
    args[0] = PYTHON
    (tmp_path / artifact["path"]).unlink()  # original commit has no generated test
    before, _, _ = await run_command(args, cwd=tmp_path)
    assert before == 1
    assert not (tmp_path / artifact["path"]).exists()  # leave base clean for checkout of patch
    (tmp_path / artifact["path"]).write_text(TEST_CODE)  # tracked in patch commit
    (tmp_path / "pricing.py").write_text("def total(price, count):\n    return price * count\n")
    after, _, _ = await run_command(args, cwd=tmp_path)
    assert after == 0
    assert (
        hashlib.sha256((tmp_path / artifact["path"]).read_bytes()).hexdigest() == artifact["sha256"]
    )
    (tmp_path / "pricing.py").write_text("def total():\n    pass\n")
    invalid, _, _ = await run_command(args, cwd=tmp_path)
    assert invalid == 2  # TypeError is not reproduced behavior.
    state.reproduction = {"reexecution_command": command, "status": "CONFIRMED"}
    bundle = build_verification_bundle(state)
    assert all(step.command == command for step in bundle.steps[:2])
    assert 'if [ "$code" -ne 1 ]' in generate_verification_workflow(bundle)


@pytest.mark.asyncio
async def test_published_candidate_contains_frozen_test(setup_issue, tmp_path, monkeypatch):
    state, settings, store, _ = setup_issue
    state.generated_test = await generate_issue_test(state, tmp_path, settings)
    state.patch_bundle = {"patches": [{"file": "pricing.py", "patched": "fixed"}]}
    published = {}

    def publish(_self, **kwargs):
        published.update(kwargs)
        return None, kwargs["bundle"]

    monkeypatch.setattr(
        "backend.agents.a10_mci_scorer.GitHubPRService.publish_fix_with_proof", publish
    )
    await A10MCIScorerAgent(store, settings).run(state)
    assert published["patch_files"][state.generated_test["path"]] == TEST_CODE
