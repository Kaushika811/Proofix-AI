"""Per-run cost and token attribution (B-B05).

Extracting `llm_gateway` was justified by one thing: being able to answer "what
did this run cost?". That needs `run_id` on every call, and the register
recorded it as never arriving — every `AuditEvent.run_id` was `""`.

It arrives now: `LLMService` binds the provenance at construction and forwards
it on every call, and all six construction sites pass it. What was missing is
any test saying so, which is why the defect could be fixed and still read as
open. These pin the chain end to end — call site → `LLMService` → gateway →
audit — so a future refactor that drops `run_id` at any hop fails here rather
than silently emptying the cost report.
"""

import ast
from pathlib import Path

import pytest

from backend.config import Settings
from backend.services.llm import LLMService

BACKEND = Path(__file__).parent.parent.parent / "backend"


class TestEveryCallSitePassesProvenance:
    """The hop most likely to be forgotten is the one furthest from the gateway."""

    def test_no_llmservice_is_constructed_without_a_run_id(self):
        offenders: list[str] = []

        for path in BACKEND.rglob("*.py"):
            if path.name == "llm.py":
                continue
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name != "LLMService":
                    continue
                if not any(kw.arg == "run_id" for kw in node.keywords):
                    offenders.append(f"{path.name}:{node.lineno}")

        assert offenders == [], f"LLMService built without run_id: {offenders}"


class TestProvenanceReachesTheGateway:
    @pytest.mark.asyncio
    async def test_run_id_and_agent_id_are_forwarded_on_every_call(self):
        seen: list[dict] = []

        class RecordingGateway:
            async def complete(self, prompt, system, **kwargs):
                seen.append(kwargs)

                class _Response:
                    text = '{"ok": true}'

                return _Response()

        service = LLMService(
            Settings(stub_mode=False, anthropic_api_key="k"),
            run_id="run-abc",
            agent_id="A7",
            retry_count=2,
        )
        service.gateway = RecordingGateway()  # type: ignore[assignment]
        service._ensure_available = lambda: None  # type: ignore[method-assign]

        await service.text("prompt", "system")

        assert seen[0]["run_id"] == "run-abc"
        assert seen[0]["agent_id"] == "A7"
        # The attempt number is provenance too: a retry's cost belongs to the
        # retry, not to the first attempt.
        assert seen[0]["retry_count"] == 2

    @pytest.mark.asyncio
    async def test_a_structured_call_carries_the_same_provenance(self):
        from pydantic import BaseModel

        class Shape(BaseModel):
            ok: bool

        seen: list[dict] = []

        class RecordingGateway:
            async def complete(self, prompt, system, **kwargs):
                seen.append(kwargs)

                class _Response:
                    text = '{"ok": true}'

                return _Response()

        service = LLMService(
            Settings(stub_mode=False, anthropic_api_key="k"), run_id="run-xyz", agent_id="A4"
        )
        service.gateway = RecordingGateway()  # type: ignore[assignment]
        service._ensure_available = lambda: None  # type: ignore[method-assign]

        await service.structured("prompt", Shape)

        assert seen[0]["run_id"] == "run-xyz"
        assert seen[0]["agent_id"] == "A4"

    @pytest.mark.asyncio
    async def test_the_gateway_stamps_run_id_onto_its_call_metrics(self):
        """The metrics object is what the cost report is built from."""
        from backend.services.llm_gateway import LLMGateway

        gateway = LLMGateway(Settings(stub_mode=False, anthropic_api_key="k"))

        captured: list = []

        async def fake_dispatch(*args, **kwargs):
            # `_dispatch` returns (text, usage); the gateway unpacks two.
            return "text", {"prompt_tokens": 1, "completion_tokens": 2}

        async def fake_audit(approved, text, operation, metrics):
            captured.append(metrics)

        gateway._dispatch = fake_dispatch  # type: ignore[method-assign]
        gateway._audit_completion = fake_audit  # type: ignore[method-assign]
        gateway._approve = lambda *a, **k: _none()  # type: ignore[method-assign]

        await gateway.complete("p", "s", run_id="run-1", agent_id="A6", operation="plan")

        assert captured and captured[0].run_id == "run-1"
        assert captured[0].agent_id == "A6"


async def _none():
    return None
