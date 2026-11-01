"""Phase 5 — the patch write path, and the prompt that drives it.

Four defects, all of the same family as the scoring bugs Phase 6 closed: the
pipeline stating something it had not established.

  B-B03  the prompt told the model what "fixed" means using one fixture
         repository's semantics, on every repository
  B-B06  an exception mid-generation left writes on disk that no bundle
         recorded, and A8 validated them
  B-B07  a failed LLM call was recorded as "the model returned an unchanged
         file", erasing the real cause
  B-B08  the patch lease was shorter than the work it guarded
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.agents.a7_code_generation import A7CodeGenerationAgent
from backend.models.root_cause import RootCauseBrief
from backend.models.validation import ValidationFailure
from backend.services.retry_brief_builder import build_retry_brief
from backend.services.runtime_patch_prompt import derive_runtime_behaviors

# Bugs that have nothing to do with tokens, auth, or expiry.
UNRELATED_BUGS = [
    ("tests/test_pagination.py::test_page_size", "off-by-one in the page offset calculation"),
    ("tests/test_parser.py::test_null_field", "unexpected None dereferenced in the parser"),
    ("tests/test_export.py::test_csv", "the export path joins with the wrong separator"),
    ("tests/test_cache.py::test_evict", "eviction never runs because the timer is not started"),
]


class TestPromptStatesOnlyWhatWasObserved:
    """B-B03. The severity here was in the matching, not just the wording.

    `_expected_from_test_name` fired on `"exp" in root_cause_text` — which
    matches "unexpected", "export", "explicit", "experiment". So the expected
    behaviour handed to the patch model was "Reject tokens whose exp timestamp
    is earlier than time.time()" for a null dereference, in the one section of
    the prompt that says what success looks like.
    """

    @pytest.mark.parametrize("failing_test,cause", UNRELATED_BUGS)
    def test_no_token_semantics_are_invented(self, failing_test, cause):
        _current, expected, _acceptance = derive_runtime_behaviors(
            RootCauseBrief(summary=cause, root_cause=cause),
            {
                "status": "CONFIRMED",
                "failing_test": failing_test,
                "exception_type": "AssertionError",
                "exception_message": "assert 3 == 4",
            },
        )

        lowered = expected.lower()
        for invented in ("token", "expiry", "exp timestamp", "time.time()", "jwt"):
            assert invented not in lowered, f"{invented!r} invented for: {cause}"

    def test_expected_behaviour_is_built_from_the_evidence(self):
        _current, expected, _acceptance = derive_runtime_behaviors(
            RootCauseBrief(summary="off-by-one", root_cause="offset starts at 1, not 0"),
            {
                "status": "CONFIRMED",
                "failing_test": "tests/test_pagination.py::test_page_size",
                "exception_type": "AssertionError",
                "exception_message": "assert 3 == 4",
            },
        )

        assert "tests/test_pagination.py::test_page_size" in expected
        assert "AssertionError" in expected
        assert "assert 3 == 4" in expected
        assert "offset starts at 1, not 0" in expected

    def test_an_auth_bug_still_reads_correctly(self):
        """Removing the guess must not lose the real case it was aimed at.

        The difference is where the words come from: A4's conclusion, not a
        keyword table.
        """
        cause = "validate_token never compares the exp claim to the current time"
        _current, expected, _acceptance = derive_runtime_behaviors(
            RootCauseBrief(summary=cause, root_cause=cause),
            {
                "status": "CONFIRMED",
                "failing_test": "tests/test_auth.py::test_expired_token_rejected",
                "exception_type": "AssertionError",
                "exception_message": "expired token accepted",
            },
        )

        assert cause in expected
        assert "tests/test_auth.py::test_expired_token_rejected" in expected

    def test_thin_evidence_produces_a_thin_prompt_not_a_guess(self):
        _current, expected, _acceptance = derive_runtime_behaviors(
            RootCauseBrief(), {"status": "UNCONFIRMED"}
        )

        assert expected == "The AssertionError it currently raises must not occur."

    @pytest.mark.parametrize("failing_test,cause", UNRELATED_BUGS)
    def test_retry_brief_invents_no_semantics_either(self, failing_test, cause):
        brief = build_retry_brief(
            ValidationFailure(
                failing_test=failing_test,
                assertion_message="AssertionError: assert 3 == 4",
                expected_value="4",
                actual_value="3",
                validation_stage="mutation",
            ),
            attempt=1,
            reproduction={"failing_test": failing_test},
        )

        blob = f"{brief.expected_behaviour} {brief.retry_instruction}".lower()
        for invented in ("jwt", "expired_token", "validate_token"):
            assert invented not in blob

    def test_pytest_expected_value_is_passed_through_unedited(self):
        brief = build_retry_brief(
            ValidationFailure(
                failing_test="tests/test_auth.py::test_token",
                assertion_message="AssertionError",
                expected_value="False",
                actual_value="True",
                validation_stage="mutation",
            ),
            attempt=1,
        )

        assert brief.expected_behaviour == "False"


AUTH_SOURCE = "def validate(token):\n    return True\n"
OTHER_SOURCE = "def helper():\n    return 1\n"


def _agent(tmp_path: Path) -> A7CodeGenerationAgent:
    agent = A7CodeGenerationAgent(MagicMock(), MagicMock())
    agent.settings = MagicMock(stub_mode=False, llm_configured=lambda: True)
    agent.store = MagicMock()
    agent.store.acquire_lock = AsyncMock(return_value=True)
    agent.store.renew_lock = AsyncMock(return_value=True)
    agent.store.append_event = AsyncMock()
    agent.store.set_json = AsyncMock()
    agent.store.release_lock = AsyncMock()
    agent.emit_status = AsyncMock()
    return agent


class TestRollback:
    """B-B06. A plan producing no patch is ordinary; an exception is not."""

    def test_rollback_restores_every_written_file(self, tmp_path):
        first = tmp_path / "a.py"
        second = tmp_path / "b.py"
        first.write_text("patched a\n", encoding="utf-8")
        second.write_text("patched b\n", encoding="utf-8")

        restored = A7CodeGenerationAgent._rollback(
            {first: AUTH_SOURCE, second: OTHER_SOURCE}
        )

        assert first.read_text() == AUTH_SOURCE
        assert second.read_text() == OTHER_SOURCE
        assert set(restored) == {str(first), str(second)}

    def test_a_file_that_cannot_be_restored_is_not_claimed_as_restored(self, tmp_path):
        """The report says what happened, not what was attempted."""
        missing = tmp_path / "gone" / "deep" / "c.py"

        restored = A7CodeGenerationAgent._rollback({missing: "x"})

        assert restored == []

    def test_rollback_of_nothing_is_not_an_error(self):
        assert A7CodeGenerationAgent._rollback({}) == []

    @pytest.mark.asyncio
    async def test_an_exception_mid_generation_leaves_the_clone_untouched(
        self, monkeypatch, tmp_path
    ):
        """The end-to-end shape: plan 1 writes, plan 2 explodes.

        Without rollback the clone keeps plan 1's write while
        `state.patch_bundle` is never set — so A8 validates a change no bundle
        records and no scoring accounts for.
        """
        from backend.agents.a7_patch_engine import PatchLLMOutput
        from backend.state.schema import RunStateModel

        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "a.py").write_text(AUTH_SOURCE, encoding="utf-8")
        (pkg / "b.py").write_text(OTHER_SOURCE, encoding="utf-8")

        agent = _agent(tmp_path)

        class Plan:
            def __init__(self, file):
                self.file = file
                self.validation_goals = []
                self.acceptance_criteria = ""
                self.required_behavior_change = ""

        monkeypatch.setattr(
            "backend.agents.a7_code_generation.build_patch_plans",
            lambda *a, **k: [Plan("pkg/a.py"), Plan("pkg/b.py")],
        )
        monkeypatch.setattr(
            "backend.agents.a7_code_generation.get_style_exemplar", lambda *a, **k: (None, "")
        )

        calls = {"n": 0}

        async def generate(plan, original, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return (
                    PatchLLMOutput(
                        patched_content="def validate(token):\n    return False\n",
                        contract_assertion="c",
                        contract_location=plan.file,
                    ),
                    {},
                )
            raise RuntimeError("gateway exploded")

        monkeypatch.setattr(agent, "_generate_from_plan", generate)
        monkeypatch.setattr(agent, "_load_intelligence", AsyncMock(return_value=None))
        monkeypatch.setattr(
            "backend.agents.a7_code_generation.load_context_package",
            AsyncMock(return_value=None),
        )

        state = RunStateModel(
            run_id="r1", repo_path=str(tmp_path), repo_clone_path=str(tmp_path)
        )

        with pytest.raises(RuntimeError):
            await agent.run(state)

        # Both files are exactly as A7 found them.
        assert (pkg / "a.py").read_text() == AUTH_SOURCE
        assert (pkg / "b.py").read_text() == OTHER_SOURCE
        assert state.patch_bundle is None
        assert state.errors and state.errors[0]["agent"] == "A7"


class TestLockLease:
    """B-B08. The lease was 60 s; one LLM call can exceed that on its own."""

    def test_default_lease_outlasts_a_single_plan(self):
        from backend.state.redis_store import RedisStore

        assert RedisStore.LOCK_TTL_SECONDS >= 600

    @pytest.mark.asyncio
    async def test_a_store_without_renewal_is_treated_as_holding_the_lease(self, tmp_path):
        """Renewal is newer than the fakes callers inject. Absent it, behave
        exactly as before rather than failing a run over a missing capability."""
        agent = _agent(tmp_path)
        agent.store = MagicMock(spec=["acquire_lock", "release_lock"])

        assert await agent._renew_lock("r1") is True

    @pytest.mark.asyncio
    async def test_a_lost_lease_is_reported(self, tmp_path):
        agent = _agent(tmp_path)
        agent.store.renew_lock = AsyncMock(return_value=False)

        assert await agent._renew_lock("r1") is False
