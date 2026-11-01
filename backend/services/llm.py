"""Schema-aware facade over the LLM gateway.

Agents (A1, A4, A6, A7) and `run_chat` keep calling `structured()` / `text()`
exactly as before. Provider routing, retries, timeouts, and token/cost
accounting now happen inside `LLMGateway`; this module only handles prompt
framing and response parsing.
"""

import json
from typing import TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

try:
    from mistralai import Mistral
except ImportError:  # installed version may not export this name
    Mistral = None  # type: ignore[assignment,misc]

from backend.config import Settings, get_settings
from backend.services.llm_gateway import LLMCallMetrics, LLMGateway

T = TypeVar("T", bound=BaseModel)

DEFAULT_SYSTEM = "You are a security-focused code analysis assistant. Respond with valid JSON only."


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        return text[start:end]
    return text


class LLMService:
    """Schema handling plus the call context the gateway records.

    `run_id`, `agent_id` and `retry_count` are bound once at construction
    because the agent constructing this already knows all three, and passing
    them per call is what got forgotten before: every audit event carried an
    empty `run_id`, so run-scoped cost and provider queries returned nothing
    despite the plumbing existing end to end.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        gateway: LLMGateway | None = None,
        *,
        run_id: str = "",
        agent_id: str = "",
        retry_count: int = 0,
    ):
        self.settings = settings or get_settings()
        self.gateway = gateway or LLMGateway(self.settings)
        self.run_id = run_id
        self.agent_id = agent_id
        self.retry_count = retry_count

    # Client handles live on the gateway. These proxies keep the historical
    # `service._anthropic = mock` injection point working for callers and tests.
    @property
    def _anthropic(self) -> AsyncAnthropic | None:
        return self.gateway._anthropic

    @_anthropic.setter
    def _anthropic(self, client: AsyncAnthropic | None) -> None:
        self.gateway._anthropic = client

    @property
    def _mistral(self) -> Mistral | None:
        return self.gateway._mistral

    @_mistral.setter
    def _mistral(self, client: Mistral | None) -> None:
        self.gateway._mistral = client

    @property
    def last_metrics(self) -> LLMCallMetrics | None:
        """Observability for the most recent call made through this service."""
        return self.gateway.last_metrics

    def _ensure_available(self) -> None:
        self.gateway.ensure_available()

    async def structured(
        self,
        prompt: str,
        schema: type[T],
        system: str = DEFAULT_SYSTEM,
        *,
        operation: str = "structured",
    ) -> T:
        self._ensure_available()
        schema_json = json.dumps(schema.model_json_schema(), indent=2)
        full_system = f"{system}\n\nRespond with JSON matching this schema:\n{schema_json}"

        response = await self.gateway.complete(
            prompt,
            full_system,
            operation=operation,
            json_mode=True,
            **self._call_context(),
        )
        return schema.model_validate_json(_extract_json(response.text))

    async def text(
        self,
        prompt: str,
        system: str = "You are a helpful assistant.",
        *,
        operation: str = "text",
    ) -> str:
        self._ensure_available()
        response = await self.gateway.complete(
            prompt, system, operation=operation, **self._call_context()
        )
        return response.text

    def _call_context(self) -> dict[str, object]:
        """Provenance attached to every call this service makes."""
        return {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "retry_count": self.retry_count,
        }
