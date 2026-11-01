"""A4 must degrade when the LLM call fails, never fail the run.

Found in production validation: Django's investigation prompt tripped the
prompt firewall (`SecurityRejection: prompt exposes repository or host
internals: host_path`), the exception propagated out of the LangGraph node, and
a repository that had been analysed successfully through A0.5/A1/A2/A3/A3.5
ended as a failed run with no report.

A6 and A7 already guarded their LLM calls. A4 was the only one that did not.
"""

import pytest

from backend.agents.a4_evidence_investigator import A4EvidenceInvestigatorAgent
from backend.config import Settings
from backend.services.llm_gateway import SecurityRejection
from backend.state.schema import RunStateModel


class _Store:
    """Minimal store: records events, swallows persistence."""

    def __init__(self):
        self.events = []

    async def append_event(self, event):
        self.events.append(event)

    async def save_state(self, state):
        return None

    async def set_json(self, *a, **k):
        return None


def _agent(monkeypatch, raises):
    settings = Settings(stub_mode=False, mistral_api_key="x", llm_provider="mistral")
    agent = A4EvidenceInvestigatorAgent(_Store(), settings)

    async def boom(*_a, **_k):
        raise raises

    monkeypatch.setattr(agent, "_llm_brief", boom)

    # The broadcaster reaches out to websockets; not under test here.
    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(agent, "emit_status", noop)
    return agent


def _state(tmp_path):
    return RunStateModel(
        run_id="r",
        repo_path=str(tmp_path),
        repo_clone_path=str(tmp_path),
        reproduction={"status": "CONFIRMED", "failing_test": "tests/test_x.py::test_y"},
        static_report={"prioritized": []},
    )


@pytest.mark.asyncio
async def test_security_rejection_falls_back_instead_of_failing(monkeypatch, tmp_path):
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    agent = _agent(monkeypatch, SecurityRejection("host_path", []))

    result = await agent.run(_state(tmp_path))

    # The run continues and a root cause exists.
    assert result.root_cause is not None
    assert result.root_cause.get("summary")


@pytest.mark.asyncio
async def test_any_llm_failure_degrades(monkeypatch, tmp_path):
    """Timeouts and provider errors take the same path."""
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    agent = _agent(monkeypatch, TimeoutError("provider timed out"))

    result = await agent.run(_state(tmp_path))

    assert result.root_cause is not None


@pytest.mark.asyncio
async def test_the_failure_is_recorded_not_hidden(monkeypatch, tmp_path):
    """Degrading silently would hide a firewall misconfiguration indefinitely."""
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    agent = _agent(monkeypatch, SecurityRejection("host_path", []))

    result = await agent.run(_state(tmp_path))

    assert any(e.get("agent") == "A4" for e in result.errors)
    assert any("SecurityRejection" in str(e.get("error")) for e in result.errors)


@pytest.mark.asyncio
async def test_the_degradation_reaches_the_investigation_report(monkeypatch, tmp_path):
    """The report a client reads must say it was produced by the fallback."""
    (tmp_path / "app.py").write_text("def go():\n    return 1\n")
    agent = _agent(monkeypatch, SecurityRejection("host_path", []))

    result = await agent.run(_state(tmp_path))

    assert result.investigation is not None
    assert result.investigation["status"] == "error"
    assert result.investigation["root_cause_source"] == "deterministic"
    assert any("SecurityRejection" in e for e in result.investigation["errors"])


@pytest.mark.asyncio
async def test_the_prompt_carries_no_host_paths(monkeypatch, tmp_path):
    """The firewall rejection above was A4's own prompt leaking the clone path.

    Every field of the prompt is interpolated raw, and both the traceback and
    the reproduction dict carry the clone's absolute path — so without
    scrubbing this branch could never reach a provider on any repository.
    """
    from backend.agents import a4_evidence_investigator as module

    settings = Settings(stub_mode=False, mistral_api_key="x", llm_provider="mistral")
    agent = A4EvidenceInvestigatorAgent(_Store(), settings)
    captured: dict[str, str] = {}

    class _LLM:
        def __init__(self, *a, **k):
            pass

        async def structured(self, prompt, _model):
            captured["prompt"] = prompt
            return module.RootCauseLLMOutput(
                summary="s", root_cause="r", citations=[], affected_modules=[]
            )

    monkeypatch.setattr(module, "LLMService", _LLM)

    async def noop(*_a, **_k):
        return None

    monkeypatch.setattr(agent, "emit_status", noop)

    state = RunStateModel(
        run_id="r",
        repo_path="/Users/someone/clones/repo",
        repo_clone_path="/Users/someone/clones/repo",
        reproduction={
            "status": "CONFIRMED",
            "failing_test": "tests/test_x.py::test_y",
            "traceback": 'File "/Users/someone/clones/repo/app.py", line 3\nAssertionError',
        },
        static_report={"prioritized": []},
    )

    await agent.run(state)

    assert "/Users/someone" not in captured["prompt"]
    assert "<PATH>" in captured["prompt"]
    # The file name the model has to cite survives the scrub.
    assert "app.py" in captured["prompt"]
