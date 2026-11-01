"""A6's repair plan — ordering provenance (`ordering_source`/`ordering_rationale`).

Nothing here fabricates a confidence or a rationale that wasn't actually
produced. The point of `ordering_source` is that a client must never have to
guess whether the deterministic topological sort or an LLM call produced
`execution_order` — these tests pin both paths, plus the fallback when the LLM
returns nothing usable.
"""

import pytest

from backend.agents.a6_fix_dag_planner import A6FixDAGPlannerAgent, FixOrderLLM
from backend.config import Settings
from backend.state.schema import RunStateModel


class _Store:
    def __init__(self):
        self.events = []

    async def append_event(self, event):
        self.events.append(event)

    async def save_state(self, state):
        return None

    async def get_json(self, *_a, **_k):
        return None


async def _noop_emit(*_a, **_k):
    return None


def _state() -> RunStateModel:
    return RunStateModel(
        run_id="r",
        repo_path="/repo",
        static_report={
            "prioritized": [{"id": "finding-0", "file": "app/auth.py", "message": "hardcoded secret"}]
        },
        blast_graph={"auto_patch_scope": ["app/auth.py"]},
    )


@pytest.mark.asyncio
async def test_stub_mode_records_deterministic_ordering(monkeypatch):
    agent = A6FixDAGPlannerAgent(_Store(), Settings(stub_mode=True))
    monkeypatch.setattr(agent, "emit_status", _noop_emit)

    result = await agent.run(_state())

    assert result.fix_dag["ordering_source"] == "deterministic"
    assert result.fix_dag["ordering_rationale"] == ""
    assert result.fix_dag["deterministic_order"] == result.fix_dag["execution_order"]


@pytest.mark.asyncio
async def test_llm_ordering_is_recorded_with_its_rationale(monkeypatch):
    settings = Settings(stub_mode=False, mistral_api_key="x", llm_provider="mistral")
    agent = A6FixDAGPlannerAgent(_Store(), settings)
    monkeypatch.setattr(agent, "emit_status", _noop_emit)

    async def fake_order(nodes, state):
        return ["finding-0"], "Fix the auth boundary first since it is the reachable CVE surface."

    monkeypatch.setattr(agent, "_llm_order", fake_order)

    result = await agent.run(_state())

    assert result.fix_dag["ordering_source"] == "llm"
    assert result.fix_dag["ordering_rationale"] == (
        "Fix the auth boundary first since it is the reachable CVE surface."
    )
    assert result.fix_dag["execution_order"] == ["finding-0"]
    # The independent graph-computed order is still recorded even though the
    # LLM path produced `execution_order` — a client comparing the two must
    # never be left to guess what the graph itself would have said.
    assert result.fix_dag["deterministic_order"] == ["finding-0"]


@pytest.mark.asyncio
async def test_llm_returning_nothing_falls_back_to_deterministic_and_says_so(monkeypatch):
    settings = Settings(stub_mode=False, mistral_api_key="x", llm_provider="mistral")
    agent = A6FixDAGPlannerAgent(_Store(), settings)
    monkeypatch.setattr(agent, "emit_status", _noop_emit)

    async def empty_order(nodes, state):
        return [], ""

    monkeypatch.setattr(agent, "_llm_order", empty_order)

    result = await agent.run(_state())

    assert result.fix_dag["ordering_source"] == "deterministic"
    assert result.fix_dag["ordering_rationale"] == ""
    assert result.fix_dag["execution_order"]  # the topological fallback still ran


@pytest.mark.asyncio
async def test_llm_order_helper_returns_the_rationale_when_the_model_gives_one(monkeypatch):
    settings = Settings(stub_mode=False, mistral_api_key="x", llm_provider="mistral")
    agent = A6FixDAGPlannerAgent(_Store(), settings)

    class _LLM:
        def __init__(self, *_a, **_k):
            pass

        async def structured(self, _prompt, _model):
            return FixOrderLLM(
                nodes=[], execution_order=["a", "b"], rationale="dependency order respected"
            )

    import backend.agents.a6_fix_dag_planner as module

    monkeypatch.setattr(module, "LLMService", _LLM)

    order, rationale = await agent._llm_order([], _state())

    assert order == ["a", "b"]
    assert rationale == "dependency order respected"


@pytest.mark.asyncio
async def test_llm_order_helper_degrades_to_empty_on_exception(monkeypatch):
    settings = Settings(stub_mode=False, mistral_api_key="x", llm_provider="mistral")
    agent = A6FixDAGPlannerAgent(_Store(), settings)

    class _LLM:
        def __init__(self, *_a, **_k):
            pass

        async def structured(self, _prompt, _model):
            raise TimeoutError("provider timed out")

    import backend.agents.a6_fix_dag_planner as module

    monkeypatch.setattr(module, "LLMService", _LLM)

    order, rationale = await agent._llm_order([], _state())

    assert order == []
    assert rationale == ""


@pytest.mark.asyncio
async def test_rationale_is_never_fabricated_when_the_model_omits_it(monkeypatch):
    """The model returned an order but no rationale field — none is invented."""
    settings = Settings(stub_mode=False, mistral_api_key="x", llm_provider="mistral")
    agent = A6FixDAGPlannerAgent(_Store(), settings)

    class _LLM:
        def __init__(self, *_a, **_k):
            pass

        async def structured(self, _prompt, _model):
            return FixOrderLLM(nodes=[], execution_order=["a"])

    import backend.agents.a6_fix_dag_planner as module

    monkeypatch.setattr(module, "LLMService", _LLM)

    order, rationale = await agent._llm_order([], _state())

    assert order == ["a"]
    assert rationale == ""
