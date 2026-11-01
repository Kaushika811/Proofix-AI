"""Phase 7 — one owner for `force_draft_pr`, and every reason on screen.

The flag used to be written from three places: A3.5 on failed reproduction, A4
on unverified citations, and `trust_gating` on exhausted validation. A flag
written from three places has no single moment at which it is true, and
answering "why is this a draft?" meant reading three files and knowing which
had run.

Both agent writes were derivable from state those agents already published, so
the flag is now computed from that evidence, once, immediately before routing.
The agents record observations; the gate decides what they mean.
"""

import pytest

from backend.orchestrator.trust_gating import (
    apply_trust_gates_before_pr,
    draft_reasons,
    reproduction_draft_reason,
)
from backend.state.schema import RunStateModel

CONFIRMED = {"status": "CONFIRMED", "reexecution_is_targeted": True}


def _state(**overrides) -> RunStateModel:
    base = dict(run_id="r1", repo_path="repo", reproduction=dict(CONFIRMED))
    base.update(overrides)
    return RunStateModel(**base)


class TestSingleWriter:
    def test_no_agent_writes_the_flag(self):
        """Grep is the test: a second writer reintroduces the defect silently."""
        from pathlib import Path

        backend = Path(__file__).parent.parent.parent / "backend"
        writers = []
        for path in backend.rglob("*.py"):
            if path.name == "trust_gating.py":
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                # Assignment to the flag on the run state, in any form.
                if "force_draft_pr = " in stripped and "state." in stripped:
                    writers.append(f"{path.name}:{number}")

        assert writers == [], f"force_draft_pr assigned outside trust_gating: {writers}"

    def test_a_clean_run_is_not_a_draft(self):
        model = apply_trust_gates_before_pr(_state(), max_retries=3)

        assert model.force_draft_pr is False
        assert draft_reasons(model, max_retries=3) == []

    def test_the_flag_is_assigned_not_or_ed(self):
        """A stale `True` must not survive a pass that finds no reason.

        Or-ing would make the flag monotonic, so a run that recovered — say a
        retry that finally reproduced — would stay a draft on the strength of an
        earlier evaluation.
        """
        model = _state(force_draft_pr=True)

        assert apply_trust_gates_before_pr(model, max_retries=3).force_draft_pr is False


class TestReasonsAreDerived:
    def test_unreproduced_run_is_a_draft_with_a_reason(self):
        model = apply_trust_gates_before_pr(
            _state(reproduction={"status": "UNCONFIRMED"}), max_retries=3
        )

        assert model.force_draft_pr is True
        assert [r.code for r in draft_reasons(model, 3)] == ["reproduction_unconfirmed"]

    @pytest.mark.parametrize(
        "status,code",
        [
            ("UNCONFIRMED", "reproduction_unconfirmed"),
            ("INFRA_ERROR", "reproduction_infra_error"),
            ("NO_TESTS", "reproduction_no_tests"),
        ],
    )
    def test_each_reproduction_failure_has_its_own_reason(self, status, code):
        reason = reproduction_draft_reason({"status": status})

        assert reason is not None
        assert reason.code == code
        assert reason.detail.endswith("before merge.")

    def test_a_confirmed_reproduction_produces_no_reason(self):
        assert reproduction_draft_reason(CONFIRMED) is None

    def test_an_absent_reproduction_produces_no_reason(self):
        """A run that has not reached A3.5 has not failed it."""
        assert reproduction_draft_reason({}) is None

    def test_unverified_citations_are_a_reason(self):
        model = apply_trust_gates_before_pr(
            _state(root_cause={"evidence_incomplete": True}), max_retries=3
        )

        assert model.force_draft_pr is True
        assert model.reinvestigation_exhausted is True
        assert [r.code for r in draft_reasons(model, 3)] == ["citations_unverified"]

    def test_exhausted_validation_is_a_reason(self):
        model = apply_trust_gates_before_pr(
            _state(retry_count=3, mutation_result={"pytest_passed": False}), max_retries=3
        )

        assert model.force_draft_pr is True
        assert model.validation_exhausted is True
        assert [r.code for r in draft_reasons(model, 3)] == ["validation_exhausted"]

    def test_every_reason_is_reported_not_just_the_first(self):
        """The defect this fixes on the UI side: A10's `review_note` carries one.

        A run blocked for three independent reasons showed one of them, and the
        others were unrecoverable from any client.
        """
        model = apply_trust_gates_before_pr(
            _state(
                retry_count=3,
                mutation_result={"pytest_passed": False},
                root_cause={"evidence_incomplete": True},
                reproduction={"status": "NO_TESTS"},
            ),
            max_retries=3,
        )

        assert [r.code for r in draft_reasons(model, 3)] == [
            "validation_exhausted",
            "citations_unverified",
            "reproduction_no_tests",
        ]

    def test_reasons_are_stable_across_repeated_evaluation(self):
        model = _state(reproduction={"status": "NO_TESTS"})

        first = draft_reasons(model, 3)
        apply_trust_gates_before_pr(model, max_retries=3)
        assert draft_reasons(model, 3) == first


class TestAgentsRecordWithoutDeciding:
    @pytest.mark.asyncio
    async def test_a3_5_records_the_status_and_not_the_decision(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        from backend.agents.a3_5_reproduction import A35ReproductionAgent
        from backend.config import Settings

        store = MagicMock()
        store.append_event = AsyncMock()
        store.set_json = AsyncMock()
        agent = A35ReproductionAgent(store, Settings(stub_mode=True))
        agent.emit_status = AsyncMock()

        async def no_tests(*args, **kwargs):
            return 5, "no tests ran", ""

        monkeypatch.setattr("backend.agents.a3_5_reproduction.run_command", no_tests)

        state = RunStateModel(
            run_id="r1", repo_path=str(tmp_path), repo_clone_path=str(tmp_path)
        )
        result = await agent.run(state)

        # The observation is recorded…
        assert (result.reproduction or {}).get("status") != "CONFIRMED"
        # …and the routing consequence is not the agent's to draw.
        assert result.force_draft_pr is False
        # The gate draws it, from exactly that observation.
        assert apply_trust_gates_before_pr(result, max_retries=3).force_draft_pr is True


class TestReportPublishesEveryReason:
    def test_draft_reasons_reach_the_run_report(self):
        from backend.services.ui_projection import build_run_report

        state = _state(
            status="completed",
            retry_count=3,
            mutation_result={"pytest_passed": False},
            reproduction={"status": "NO_TESTS"},
        )
        apply_trust_gates_before_pr(state, max_retries=3)

        report = build_run_report(state, [])
        codes = [r["code"] for r in report["draftReasons"]]

        assert codes == ["validation_exhausted", "reproduction_no_tests"]
        assert all(r["detail"] for r in report["draftReasons"])

    def test_a_clean_run_publishes_no_reasons(self):
        from backend.services.ui_projection import build_run_report

        report = build_run_report(_state(status="completed"), [])

        assert report["draftReasons"] == []
