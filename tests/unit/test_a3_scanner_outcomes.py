"""A3 must distinguish "scanner found nothing" from "scanner did not run".

The regression these lock down: a successful bandit/semgrep run reporting zero
findings previously fell through to a hand-rolled heuristic scan, so a clean
repository produced fabricated vulnerabilities that A4 then explained and A7
then patched.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.agents.a3_static_analysis import A3StaticAnalysisAgent, ScanOutcome
from backend.config import Settings


def make_agent(**overrides) -> A3StaticAnalysisAgent:
    settings = Settings(**{"stub_mode": False, **overrides})
    return A3StaticAnalysisAgent(MagicMock(), settings)


def patch_run_command(monkeypatch, result):
    """Stub the shared subprocess runner with a fixed (code, stdout, stderr)."""

    async def fake_run_command(cmd, cwd=None, timeout=120, env=None):
        return result

    monkeypatch.setattr(
        "backend.agents.a3_static_analysis.run_command", fake_run_command
    )


@pytest.fixture
def repo(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    # Content the legacy heuristics would have flagged, so a fabricated finding
    # would be visible if one were produced.
    (pkg / "app.py").write_text("import pickle\nSECRET_KEY = 'hunter2'\n")
    return tmp_path


# -- bandit ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_bandit_clean_scan_yields_no_findings(monkeypatch, repo):
    patch_run_command(monkeypatch, (0, json.dumps({"results": []}), ""))
    agent = make_agent()

    outcome = await agent._run_bandit(repo, [repo / "pkg"])

    assert outcome.executed is True
    assert outcome.findings == []


@pytest.mark.asyncio
async def test_bandit_clean_scan_is_not_stubbed_even_in_stub_mode(monkeypatch, repo):
    """Stub mode covers a missing tool, not a tool that reported nothing."""
    patch_run_command(monkeypatch, (0, json.dumps({"results": []}), ""))
    agent = make_agent(stub_mode=True)

    resolved = agent._resolve_outcome(
        await agent._run_bandit(repo, [repo / "pkg"]),
        lambda: agent._stub_bandit(repo, [repo / "pkg"]),
    )

    assert resolved.findings == []


@pytest.mark.asyncio
async def test_bandit_real_findings_are_returned(monkeypatch, repo):
    payload = {
        "results": [
            {
                "filename": str(repo / "pkg/app.py"),
                "line_number": 1,
                "issue_text": "pickle usage",
                "issue_severity": "HIGH",
            }
        ]
    }
    patch_run_command(monkeypatch, (1, json.dumps(payload), ""))
    agent = make_agent()

    outcome = await agent._run_bandit(repo, [repo / "pkg"])

    assert outcome.executed is True
    assert len(outcome.findings) == 1
    assert outcome.findings[0]["severity"] == 0.9


@pytest.mark.asyncio
async def test_bandit_missing_binary_is_a_failure(monkeypatch, repo):
    patch_run_command(monkeypatch, (-1, "", "command not found: bandit"))
    agent = make_agent()

    outcome = await agent._run_bandit(repo, [repo / "pkg"])

    assert outcome.executed is False
    assert "command not found" in outcome.detail


@pytest.mark.asyncio
async def test_bandit_crash_is_a_failure(monkeypatch, repo):
    patch_run_command(monkeypatch, (2, "", "internal error"))
    agent = make_agent()

    outcome = await agent._run_bandit(repo, [repo / "pkg"])
    assert outcome.executed is False


@pytest.mark.asyncio
async def test_bandit_unparseable_output_is_a_failure(monkeypatch, repo):
    patch_run_command(monkeypatch, (0, "not json at all", ""))
    agent = make_agent()

    outcome = await agent._run_bandit(repo, [repo / "pkg"])
    assert outcome.executed is False


# -- semgrep ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_semgrep_clean_scan_yields_no_findings(monkeypatch, repo):
    patch_run_command(monkeypatch, (0, json.dumps({"results": []}), ""))
    agent = make_agent()

    outcome = await agent._run_semgrep(repo, [repo / "pkg"])

    assert outcome.executed is True
    assert outcome.findings == []


@pytest.mark.asyncio
async def test_semgrep_missing_binary_is_a_failure(monkeypatch, repo):
    patch_run_command(monkeypatch, (-1, "", "command not found: semgrep"))
    agent = make_agent()

    assert (await agent._run_semgrep(repo, [repo / "pkg"])).executed is False


@pytest.mark.asyncio
async def test_semgrep_silent_output_is_a_failure(monkeypatch, repo):
    patch_run_command(monkeypatch, (1, "", "config error"))
    agent = make_agent()

    assert (await agent._run_semgrep(repo, [repo / "pkg"])).executed is False


# -- ruff ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ruff_clean_scan_yields_no_findings(monkeypatch, repo):
    patch_run_command(monkeypatch, (0, "[]", ""))
    agent = make_agent()

    outcome = await agent._run_ruff(repo, [repo / "pkg"])
    assert outcome.executed is True
    assert outcome.findings == []


@pytest.mark.asyncio
async def test_ruff_missing_binary_is_a_failure(monkeypatch, repo):
    patch_run_command(monkeypatch, (-1, "", "command not found: ruff"))
    agent = make_agent()

    assert (await agent._run_ruff(repo, [repo / "pkg"])).executed is False


# -- stub fallback policy --------------------------------------------------


def test_failed_scanner_stubs_only_in_stub_mode(repo):
    failed = ScanOutcome.failed("bandit missing")

    production = make_agent(stub_mode=False)
    assert production._resolve_outcome(failed, lambda: [{"file": "x"}]).findings == []

    stubbed = make_agent(stub_mode=True)
    assert stubbed._resolve_outcome(failed, lambda: [{"file": "x"}]).findings == [{"file": "x"}]


def test_successful_outcome_is_never_replaced():
    real = ScanOutcome.clean([{"file": "real.py"}])
    agent = make_agent(stub_mode=True)

    assert agent._resolve_outcome(real, lambda: [{"file": "fake.py"}]) is real


def test_status_labels_distinguish_all_four_cases():
    agent = make_agent()
    assert agent._status_label(ScanOutcome.clean([{"f": 1}])) == "ok"
    assert agent._status_label(ScanOutcome.clean([])) == "ok_no_findings"
    assert agent._status_label(ScanOutcome.failed("x")) == "unavailable"
    assert agent._status_label(ScanOutcome(False, [{"f": 1}], "x")) == "stubbed"


@pytest.mark.asyncio
async def test_no_scan_targets_is_a_failure_not_a_clean_scan(repo):
    agent = make_agent()
    assert (await agent._run_bandit(repo, [])).executed is False
    assert (await agent._run_semgrep(repo, [])).executed is False
    assert (await agent._run_ruff(repo, [])).executed is False


# -- end-to-end agent behaviour -------------------------------------------


@pytest.mark.asyncio
async def test_clean_repository_produces_empty_report(monkeypatch, repo):
    """The headline regression: all scanners clean means zero findings."""
    patch_run_command(monkeypatch, (0, json.dumps({"results": []}), ""))

    store = MagicMock()
    store.get_json = _async_none
    store.set_json = _async_noop
    store.append_event = _async_noop

    agent = A3StaticAnalysisAgent(store, Settings(stub_mode=True))
    agent.emit_status = _async_noop

    from backend.state.schema import RunStateModel

    state = RunStateModel(run_id="r1", repo_path=str(repo), repo_clone_path=str(repo))
    result = await agent.run(state)

    assert result.static_report["prioritized"] == []
    assert result.static_report["raw_count"] == 0


async def _async_none(*args, **kwargs):
    return None


async def _async_noop(*args, **kwargs):
    return None


# -- scanner_status / criticality / churn_weight / severity_measured -------


def patch_run_command_per_tool(monkeypatch, results: dict):
    """`results` keyed by the binary name (`cmd[0]`) so bandit/semgrep/ruff
    can be given independent outcomes in one run."""

    async def fake_run_command(cmd, cwd=None, timeout=120, env=None):
        return results[cmd[0]]

    monkeypatch.setattr(
        "backend.agents.a3_static_analysis.run_command", fake_run_command
    )


async def _run_agent(monkeypatch, repo, results, *, stub_mode=False, sig=None):
    patch_run_command_per_tool(monkeypatch, results)

    async def _get_json(*a, **k):
        return sig

    store = MagicMock()
    store.get_json = _get_json
    store.set_json = _async_noop
    store.append_event = _async_noop

    agent = A3StaticAnalysisAgent(store, Settings(stub_mode=stub_mode))
    agent.emit_status = _async_noop

    from backend.state.schema import RunStateModel

    state = RunStateModel(run_id="r1", repo_path=str(repo), repo_clone_path=str(repo))
    return await agent.run(state)


@pytest.mark.asyncio
async def test_scanner_status_is_persisted_on_the_durable_report(monkeypatch, repo):
    """`scanner_status` previously lived only on the transient event; it must
    also reach `state.static_report`, which survives past the event window."""
    result = await _run_agent(
        monkeypatch,
        repo,
        {
            "bandit": (-1, "", "command not found: bandit"),
            "semgrep": (-1, "", "command not found: semgrep"),
            "ruff": (0, json.dumps([]), ""),
        },
    )

    assert result.static_report["scanner_status"] == {
        "bandit": "unavailable",
        "semgrep": "unavailable",
        "ruff": "ok_no_findings",
    }


@pytest.mark.asyncio
async def test_scanner_status_reports_ok_when_a_scanner_finds_something(monkeypatch, repo):
    payload = {
        "results": [
            {
                "filename": str(repo / "pkg/app.py"),
                "line_number": 1,
                "issue_text": "pickle usage",
                "issue_severity": "HIGH",
            }
        ]
    }
    result = await _run_agent(
        monkeypatch,
        repo,
        {
            "bandit": (1, json.dumps(payload), ""),
            "semgrep": (-1, "", "not found"),
            "ruff": (0, json.dumps([]), ""),
        },
    )

    assert result.static_report["scanner_status"]["bandit"] == "ok"


@pytest.mark.asyncio
async def test_finding_carries_criticality_and_churn_weight_from_sig(monkeypatch, repo):
    payload = {
        "results": [
            {
                "filename": str(repo / "pkg/app.py"),
                "line_number": 1,
                "issue_text": "pickle usage",
                "issue_severity": "HIGH",
            }
        ]
    }
    sig = {
        "repo_path": str(repo),
        "source_roots": [""],
        "files": {
            "pkg/app.py": {
                "path": "pkg/app.py",
                "role": "public-api",
                "imports": [],
                "imported_by": [],
                "churn_weight": 0.37,
                "criticality": 0.81,
            }
        },
        "edges": [],
    }
    result = await _run_agent(
        monkeypatch,
        repo,
        {
            "bandit": (1, json.dumps(payload), ""),
            "semgrep": (-1, "", "not found"),
            "ruff": (0, json.dumps([]), ""),
        },
        sig=sig,
    )

    finding = result.static_report["prioritized"][0]
    assert finding["criticality"] == 0.81
    assert finding["churn_weight"] == 0.37


@pytest.mark.asyncio
async def test_bandit_only_finding_reports_severity_measured(monkeypatch, repo):
    payload = {
        "results": [
            {
                "filename": str(repo / "pkg/app.py"),
                "line_number": 1,
                "issue_text": "pickle usage",
                "issue_severity": "HIGH",
            }
        ]
    }
    result = await _run_agent(
        monkeypatch,
        repo,
        {
            "bandit": (1, json.dumps(payload), ""),
            "semgrep": (-1, "", "not found"),
            "ruff": (0, json.dumps([]), ""),
        },
    )

    finding = result.static_report["prioritized"][0]
    assert finding["tools"] == ["bandit"]
    assert finding["severity_measured"] is True
    assert finding["severity"] == 0.9


@pytest.mark.asyncio
async def test_ruff_only_finding_reports_severity_not_measured(monkeypatch, repo):
    """ruff assigns every finding the same constant (0.4) — never a real
    per-finding measurement, so the UI must not display it as one."""
    ruff_payload = [
        {
            "filename": str(repo / "pkg/app.py"),
            "location": {"row": 1},
            "message": "unused import",
        }
    ]
    result = await _run_agent(
        monkeypatch,
        repo,
        {
            "bandit": (-1, "", "not found"),
            "semgrep": (-1, "", "not found"),
            "ruff": (0, json.dumps(ruff_payload), ""),
        },
    )

    finding = result.static_report["prioritized"][0]
    assert finding["tools"] == ["ruff"]
    assert finding["severity_measured"] is False
    assert finding["severity"] == 0.4


@pytest.mark.asyncio
async def test_stub_mode_bandit_fallback_is_never_reported_as_measured(monkeypatch, repo):
    """A stub finding carries the tool name "bandit" but is not a real
    measurement — `outcome.executed` must gate this, not the tool name."""
    result = await _run_agent(
        monkeypatch,
        repo,
        {
            "bandit": (-1, "", "command not found: bandit"),
            "semgrep": (-1, "", "command not found: semgrep"),
            "ruff": (0, json.dumps([]), ""),
        },
        stub_mode=True,
    )

    assert result.static_report["scanner_status"]["bandit"] == "stubbed"
    bandit_findings = [f for f in result.static_report["prioritized"] if "bandit" in f["tools"]]
    assert bandit_findings
    assert all(f["severity_measured"] is False for f in bandit_findings)


@pytest.mark.asyncio
async def test_two_tool_consensus_keeps_the_measured_severitys_own_flag(monkeypatch, repo):
    """When bandit and ruff both flag the same 5-line cluster, the winning
    (higher) severity is bandit's real measurement — `severity_measured` must
    track that, not default false just because multiple tools contributed."""
    bandit_payload = {
        "results": [
            {
                "filename": str(repo / "pkg/app.py"),
                "line_number": 1,
                "issue_text": "pickle usage",
                "issue_severity": "HIGH",
            }
        ]
    }
    ruff_payload = [
        {
            "filename": str(repo / "pkg/app.py"),
            "location": {"row": 2},
            "message": "unused import",
        }
    ]
    result = await _run_agent(
        monkeypatch,
        repo,
        {
            "bandit": (1, json.dumps(bandit_payload), ""),
            "semgrep": (-1, "", "not found"),
            "ruff": (0, json.dumps(ruff_payload), ""),
        },
    )

    finding = result.static_report["prioritized"][0]
    assert set(finding["tools"]) == {"bandit", "ruff"}
    assert finding["consensus"] is True
    assert finding["severity"] == 0.9  # bandit's HIGH, not ruff's 0.4
    assert finding["severity_measured"] is True
