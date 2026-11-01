"""A9 differential re-scan: what counts as a *new* vulnerability.

A9 rejects a patch when the post-patch scan contains a finding the baseline did
not. Everything therefore rests on finding identity across two scans of a file
whose lines have moved, and on `security_score`, which A10 compares against
`SECURITY_TECHNICAL_THRESHOLD` (90.0) to decide auto-merge eligibility.

Two behaviours are pinned here:

  * **A line shift is not a new finding.** Inserting a line above a pre-existing
    finding moved it, and the old `file:line:message` key read that as newly
    introduced — rejecting a patch over code it never touched.
  * **A scan that did not run scores `None`, not 100.** `bandit` being absent is
    not evidence of safety, and 100 cleared the auto-merge gate outright.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.agents.a10_routing import SECURITY_TECHNICAL_THRESHOLD
from backend.agents.a9_security_rescan import (
    A9SecurityRescanAgent,
    finding_key,
    introduced_from_reconciliation,
    new_findings_by_multiplicity,
    reconcile,
    reconciliation_lanes,
)
from backend.config import Settings
from backend.models.validation import SecurityRescanResult
from backend.services.measurement import meets_threshold
from backend.state.schema import RunStateModel

HARDCODED = "Possible hardcoded password: 'hunter2'"
ASSERT_USED = "Use of assert detected. The enclosed code will be removed when compiling to optimised byte code."


def bandit_finding(file: str, line: int, message: str) -> dict:
    return {"tool": "bandit", "file": file, "line": line, "message": message, "severity": 0.7}


def keyed(findings: list[dict], tool: str = "bandit") -> list[tuple]:
    return [(finding_key(f, tool), f) for f in findings]


class TestFindingIdentity:
    def test_line_is_not_part_of_identity(self):
        """The defect, at its smallest: the same finding, two lines lower."""
        before = bandit_finding("pkg/auth.py", 12, HARDCODED)
        after = bandit_finding("pkg/auth.py", 14, HARDCODED)

        assert finding_key(before, "bandit") == finding_key(after, "bandit")

    def test_message_is_normalized_not_truncated(self):
        """Two bandit issues sharing an opening clause are different findings.

        The old key truncated at 50 characters, so these collided — and a
        collision means a real new vulnerability is silently accepted.
        """
        long_a = "Use of insecure MD2, MD4, MD5, or SHA1 hash function detected."
        long_b = "Use of insecure MD2, MD4, MD5, or SHA1 hash function in ssl module."
        assert long_a[:50] == long_b[:50]

        assert finding_key(bandit_finding("a.py", 1, long_a), "bandit") != finding_key(
            bandit_finding("a.py", 1, long_b), "bandit"
        )

    def test_whitespace_and_case_do_not_split_a_finding(self):
        assert finding_key(bandit_finding("a.py", 1, "Possible  hardcoded\npassword"), "bandit") == (
            finding_key(bandit_finding("a.py", 1, "possible hardcoded password"), "bandit")
        )

    def test_path_form_does_not_split_a_finding(self):
        assert finding_key(bandit_finding("./pkg/auth.py", 1, HARDCODED), "bandit") == (
            finding_key(bandit_finding("pkg/auth.py", 9, HARDCODED), "bandit")
        )

    def test_same_finding_from_two_tools_stays_distinct(self):
        f = bandit_finding("a.py", 1, HARDCODED)
        assert finding_key(f, "bandit") != finding_key(f, "semgrep")


class TestNewFindingDetection:
    def test_shifted_finding_is_not_new(self):
        baseline = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])
        post = keyed([bandit_finding("pkg/auth.py", 14, HARDCODED)])

        assert new_findings_by_multiplicity(baseline, post) == []

    def test_genuinely_new_finding_is_reported(self):
        baseline = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])
        post = keyed(
            [
                bandit_finding("pkg/auth.py", 14, HARDCODED),
                bandit_finding("pkg/auth.py", 20, ASSERT_USED),
            ]
        )

        new = new_findings_by_multiplicity(baseline, post)
        assert [f["message"] for f in new] == [ASSERT_USED]

    def test_a_second_copy_of_an_existing_finding_is_new(self):
        """Set difference loses this: one hardcoded password became two."""
        baseline = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])
        post = keyed(
            [
                bandit_finding("pkg/auth.py", 12, HARDCODED),
                bandit_finding("pkg/auth.py", 40, HARDCODED),
            ]
        )

        new = new_findings_by_multiplicity(baseline, post)
        assert len(new) == 1
        # Reported at the line the baseline cannot account for, so the retry
        # brief points at the copy the patch added.
        assert new[0]["line"] == 40

    def test_a_resolved_finding_does_not_go_negative(self):
        baseline = keyed(
            [bandit_finding("pkg/auth.py", 12, HARDCODED), bandit_finding("pkg/auth.py", 40, HARDCODED)]
        )
        post = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])

        assert new_findings_by_multiplicity(baseline, post) == []

    def test_empty_baseline_makes_every_finding_new(self):
        post = keyed([bandit_finding("pkg/auth.py", 1, HARDCODED)])
        assert len(new_findings_by_multiplicity([], post)) == 1


class TestReconciliation:
    """The comparison A9 performs, kept rather than reduced to its verdict.

    `new_findings` alone asks the reader to take on trust that a finding two
    lines lower is the same finding. These pin the record that makes it
    checkable: which post occurrence paired with which baseline one, and by
    how much it moved.
    """

    def test_a_pure_line_shift_is_a_carried_pair_not_an_introduction(self):
        baseline = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])
        post = keyed([bandit_finding("pkg/auth.py", 14, HARDCODED)])

        [rec] = reconcile(baseline, post)

        assert rec.entry["matched"] == [
            {"baseline_index": 0, "post_index": 0, "line_delta": 2}
        ]
        assert rec.entry["introduced_indexes"] == []
        assert rec.entry["resolved_indexes"] == []

    def test_multiplicity_one_to_two_carries_one_and_introduces_one(self):
        """The case set difference gets wrong, rendered whole.

        One hardcoded password became two: one occurrence pairs with the
        baseline, the surplus one dangles — and it is the one at the line the
        baseline cannot account for.
        """
        baseline = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])
        post = keyed(
            [
                bandit_finding("pkg/auth.py", 12, HARDCODED),
                bandit_finding("pkg/auth.py", 40, HARDCODED),
            ]
        )

        [rec] = reconcile(baseline, post)

        assert rec.entry["introduced_indexes"] == [1]
        assert rec.entry["post"][1] == {"line": 40}
        assert rec.entry["matched"] == [
            {"baseline_index": 0, "post_index": 0, "line_delta": 0}
        ]

    def test_an_occurrence_on_its_original_line_pairs_with_itself_first(self):
        """Two occurrences, one moved: the pairing must show the smallest shift.

        Pairing by scan order would report the stationary one as having moved
        +28 and the moved one as -28 — two fabricated deltas where the evidence
        supports one delta of +2.
        """
        baseline = keyed(
            [bandit_finding("pkg/auth.py", 12, HARDCODED), bandit_finding("pkg/auth.py", 40, HARDCODED)]
        )
        post = keyed(
            [bandit_finding("pkg/auth.py", 40, HARDCODED), bandit_finding("pkg/auth.py", 14, HARDCODED)]
        )

        [rec] = reconcile(baseline, post)

        deltas = sorted(m["line_delta"] for m in rec.entry["matched"])
        assert deltas == [0, 2]
        assert rec.entry["introduced_indexes"] == []

    def test_a_removed_occurrence_is_recorded_as_resolved(self):
        baseline = keyed(
            [bandit_finding("pkg/auth.py", 12, HARDCODED), bandit_finding("pkg/auth.py", 40, HARDCODED)]
        )
        post = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])

        [rec] = reconcile(baseline, post)

        assert rec.entry["resolved_indexes"] == [1]
        assert rec.entry["introduced_indexes"] == []

    def test_a_key_only_the_baseline_had_still_gets_an_entry(self):
        baseline = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])

        [rec] = reconcile(baseline, [])

        assert rec.entry["post"] == []
        assert rec.entry["resolved_indexes"] == [0]

    def test_the_displayed_message_is_the_raw_one_not_the_comparison_key(self):
        """The key is casefolded and whitespace-collapsed to compare. The
        ledger shows what the scanner actually said."""
        raw = "Possible   hardcoded PASSWORD: 'hunter2'"
        [rec] = reconcile([], keyed([bandit_finding("pkg/auth.py", 3, raw)]))

        assert rec.entry["message"] == raw

    def test_introduced_side_still_matches_the_narrow_function(self):
        baseline = keyed([bandit_finding("pkg/auth.py", 12, HARDCODED)])
        post = keyed(
            [
                bandit_finding("pkg/auth.py", 14, HARDCODED),
                bandit_finding("pkg/auth.py", 20, ASSERT_USED),
            ]
        )

        assert introduced_from_reconciliation(reconcile(baseline, post)) == (
            new_findings_by_multiplicity(baseline, post)
        )

    def test_no_severity_leaks_into_the_kept_record(self):
        """`_run_bandit` stamps a constant 0.7 on every finding. It is not a
        measurement, so it must not be persisted as one."""
        [rec] = reconcile([], keyed([bandit_finding("pkg/auth.py", 3, HARDCODED)]))

        assert rec.entry["post"] == [{"line": 3}]


class TestReconciliationLanes:
    def test_a_scanner_that_did_not_run_gets_a_lane_marked_not_compared(self):
        recs = reconcile([], keyed([bandit_finding("pkg/auth.py", 3, HARDCODED)]))

        lanes = reconciliation_lanes(recs, ["bandit"])

        assert [lane["tool"] for lane in lanes] == ["bandit", "semgrep"]
        assert lanes[0]["compared"] is True
        assert len(lanes[0]["keys"]) == 1
        # Not an empty lane that reads as "found nothing" — an absent one.
        assert lanes[1]["compared"] is False
        assert lanes[1]["keys"] == []

    def test_ruff_never_gets_a_lane(self):
        """`_keyed` excludes ruff as style, not security. The ledger agrees."""
        lanes = reconciliation_lanes([], ["bandit", "semgrep", "ruff"])
        assert [lane["tool"] for lane in lanes] == ["bandit", "semgrep"]


class TestAgentKeepsTheReconciliation:
    @pytest.mark.asyncio
    async def test_a_shifted_finding_is_visible_as_a_carried_pair(self, monkeypatch, tmp_path):
        agent = make_agent(monkeypatch, bandit=[bandit_json("pkg/auth.py", 14, HARDCODED)])
        state = make_state(
            tmp_path, [{"file": "pkg/auth.py", "line": 12, "message": HARDCODED, "severity": 0.9}]
        )

        result = (await agent.run(state)).security_result

        bandit_lane = result["reconciliation"][0]
        assert bandit_lane["compared"] is True
        assert bandit_lane["keys"][0]["matched"][0]["line_delta"] == 2
        assert result["new_findings"] == []

    @pytest.mark.asyncio
    async def test_an_absent_scanner_excludes_its_baseline_from_the_ledger(
        self, monkeypatch, tmp_path
    ):
        """A semgrep baseline with no semgrep re-scan is not "resolved" — it is
        not compared, and nothing about it may appear in the ledger."""
        agent = make_agent(
            monkeypatch, bandit=[bandit_json("pkg/auth.py", 3, HARDCODED)], semgrep_ran=False
        )
        state = make_state(
            tmp_path, [{"file": "pkg/auth.py", "line": 3, "message": HARDCODED, "severity": 0.9}]
        )
        state.static_report["baseline_json"]["semgrep"] = [
            {"file": "pkg/other.py", "line": 9, "message": "sql injection", "severity": 0.9}
        ]

        result = (await agent.run(state)).security_result

        bandit_lane, semgrep_lane = result["reconciliation"]
        assert semgrep_lane == {"tool": "semgrep", "compared": False, "keys": []}
        assert [k["file"] for k in bandit_lane["keys"]] == ["pkg/auth.py"]


class TestReconciliationBackwardCompatibility:
    def test_state_persisted_before_the_field_existed_still_deserializes(self):
        legacy = {
            "new_findings": [],
            "rejected": False,
            "security_score": 100.0,
            "scanners_run": ["bandit"],
            "reexecution_command": "bandit -f json -q -r .",
            "reexecution_timeout_seconds": 150,
        }
        result = SecurityRescanResult.model_validate(legacy)
        assert result.reconciliation == []


async def _noop(*args, **kwargs):
    return None


def make_agent(monkeypatch, *, bandit=None, semgrep=None, bandit_ran=True, semgrep_ran=True):
    """A9 with both scanners stubbed. `*_ran=False` simulates an absent tool."""

    async def fake_run_command(cmd, cwd=None, timeout=120, env=None):
        tool = cmd[0]
        if tool == "bandit":
            if not bandit_ran:
                return -1, "", "command not found: bandit"
            return 0, json.dumps({"results": bandit or []}), ""
        if not semgrep_ran:
            return -1, "", "command not found: semgrep"
        return 0, json.dumps({"results": semgrep or []}), ""

    monkeypatch.setattr("backend.agents.a9_security_rescan.run_command", fake_run_command)
    monkeypatch.setattr(
        "backend.agents.a9_security_rescan.get_scan_targets", lambda state, repo, sig: [repo]
    )
    monkeypatch.setattr(
        "backend.agents.a9_security_rescan.resolve_source_roots", lambda repo, roots, sig: ["."]
    )

    store = MagicMock()
    store.set_json = _noop
    store.append_event = _noop
    store.get_json = _noop
    agent = A9SecurityRescanAgent(store, Settings(stub_mode=True))
    agent.emit_status = _noop
    return agent


def bandit_json(file: str, line: int, message: str) -> dict:
    """Raw bandit output, as A9 parses it — not the normalized dict."""
    return {"filename": file, "line_number": line, "issue_text": message}


def make_state(tmp_path, baseline_bandit: list[dict]) -> RunStateModel:
    return RunStateModel(
        run_id="r1",
        repo_path=str(tmp_path),
        repo_clone_path=str(tmp_path),
        sig={"files": {}},
        static_report={
            "baseline_json": {"bandit": baseline_bandit, "semgrep": [], "ruff": []},
        },
    )


class TestAgentRescan:
    @pytest.mark.asyncio
    async def test_shifted_finding_does_not_reject_the_patch(self, monkeypatch, tmp_path):
        """The end-to-end shape of the bug: a patch inserted two lines."""
        agent = make_agent(monkeypatch, bandit=[bandit_json("pkg/auth.py", 14, HARDCODED)])
        state = make_state(
            tmp_path, [{"file": "pkg/auth.py", "line": 12, "message": HARDCODED, "severity": 0.9}]
        )

        result = (await agent.run(state)).security_result

        assert result["new_findings"] == []
        assert result["rejected"] is False
        assert result["security_score"] == 100.0

    @pytest.mark.asyncio
    async def test_introduced_finding_rejects_and_scores(self, monkeypatch, tmp_path):
        agent = make_agent(
            monkeypatch,
            bandit=[
                bandit_json("pkg/auth.py", 14, HARDCODED),
                bandit_json("pkg/auth.py", 20, ASSERT_USED),
            ],
        )
        state = make_state(
            tmp_path, [{"file": "pkg/auth.py", "line": 12, "message": HARDCODED, "severity": 0.9}]
        )

        result = (await agent.run(state)).security_result

        assert result["rejected"] is True
        assert result["security_score"] == 75.0
        assert result["new_findings"][0]["message"] == ASSERT_USED
        assert result["new_findings"][0]["tools"] == ["bandit"]
        assert result["scanners_run"] == ["bandit", "semgrep"]

    @pytest.mark.asyncio
    async def test_absent_scanners_do_not_score_a_hundred(self, monkeypatch, tmp_path):
        """`bandit is not installed` must not read as `this patch is safe`."""
        agent = make_agent(monkeypatch, bandit_ran=False, semgrep_ran=False)
        state = make_state(tmp_path, [])

        result = (await agent.run(state)).security_result

        assert result["security_score"] is None
        assert result["rejected"] is False
        assert result["scanners_run"] == []
        # And the unmeasured score cannot clear the auto-merge gate.
        assert not meets_threshold(result["security_score"], SECURITY_TECHNICAL_THRESHOLD)

    @pytest.mark.asyncio
    async def test_one_working_scanner_still_measures(self, monkeypatch, tmp_path):
        agent = make_agent(
            monkeypatch, bandit=[bandit_json("pkg/auth.py", 3, HARDCODED)], semgrep_ran=False
        )
        state = make_state(tmp_path, [])

        result = (await agent.run(state)).security_result

        assert result["security_score"] == 75.0
        assert result["rejected"] is True
        assert result["scanners_run"] == ["bandit"]

    @pytest.mark.asyncio
    async def test_scanners_run_persists_both_scanners_when_both_execute(self, monkeypatch, tmp_path):
        agent = make_agent(monkeypatch, bandit=[], semgrep=[])
        state = make_state(tmp_path, [])

        result = (await agent.run(state)).security_result

        assert result["scanners_run"] == ["bandit", "semgrep"]
        assert result["security_score"] == 100.0

    @pytest.mark.asyncio
    async def test_baseline_from_a_tool_that_did_not_rerun_is_ignored(self, monkeypatch, tmp_path):
        """A semgrep baseline with no semgrep re-scan must not look resolved.

        It also must not make bandit's findings look new: the comparison is
        per-tool, so an absent tool contributes nothing on either side.
        """
        agent = make_agent(
            monkeypatch, bandit=[bandit_json("pkg/auth.py", 3, HARDCODED)], semgrep_ran=False
        )
        state = make_state(
            tmp_path, [{"file": "pkg/auth.py", "line": 3, "message": HARDCODED, "severity": 0.9}]
        )
        state.static_report["baseline_json"]["semgrep"] = [
            {"file": "pkg/other.py", "line": 9, "message": "sql injection", "severity": 0.9}
        ]

        result = (await agent.run(state)).security_result

        assert result["rejected"] is False
        assert result["security_score"] == 100.0


class TestScannersRunBackwardCompatibility:
    def test_state_persisted_before_the_field_existed_still_deserializes(self):
        """A `SecurityRescanResult` dict from before `scanners_run` existed —
        as sits in Redis for any run completed before this change — must still
        validate, defaulting to an empty list rather than raising."""
        legacy = {
            "new_findings": [],
            "rejected": False,
            "security_score": 100.0,
            "reexecution_command": "bandit -f json -q -r .",
            "reexecution_timeout_seconds": 150,
        }
        result = SecurityRescanResult.model_validate(legacy)
        assert result.scanners_run == []
        assert result.security_score == 100.0


class TestScannerSeverityMapping:
    def test_bandit_severity_mapping(self):
        from backend.agents.a9_security_rescan import _bandit_severity

        assert _bandit_severity({"issue_severity": "HIGH"}) == 0.9
        assert _bandit_severity({"issue_severity": "MEDIUM"}) == 0.6
        assert _bandit_severity({"issue_severity": "LOW"}) == 0.3
        assert _bandit_severity({"issue_severity": "high"}) == 0.9
        assert _bandit_severity({"issue_severity": "unknown"}) is None
        assert _bandit_severity({}) is None

    def test_semgrep_severity_mapping(self):
        from backend.agents.a9_security_rescan import _semgrep_severity

        assert _semgrep_severity({"extra": {"severity": "ERROR"}}) == 0.9
        assert _semgrep_severity({"extra": {"severity": "WARNING"}}) == 0.6
        assert _semgrep_severity({"extra": {"severity": "INFO"}}) == 0.3
        assert _semgrep_severity({"extra": {"severity": "error"}}) == 0.9
        assert _semgrep_severity({"extra": {"severity": "NOTE"}}) is None
        assert _semgrep_severity({}) is None

