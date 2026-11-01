"""Single egress point for every LLM call in the pipeline.

Before this module each call site constructed a provider client inline and got
no retry, no timeout, and no accounting — so "what did this run cost?" had no
answer, and a transient provider error failed a whole agent.

The gateway owns provider routing, retry policy, timeouts, latency measurement,
token accounting and cost estimation, and emits one structured log record per
call. `LLMService` is a thin facade over it, so agents keep their existing API.

Nothing here interprets prompts or responses: the gateway moves text and
measures the transfer. Schema handling stays in `LLMService`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from anthropic import AsyncAnthropic
from pydantic import BaseModel, Field

try:
    from mistralai import Mistral
except ImportError:  # installed version may not export this name
    Mistral = None  # type: ignore[assignment,misc]
from backend.config import Settings, get_settings

logger = logging.getLogger(__name__)

TokenSource = Literal["provider", "estimated"]

# Rough characters-per-token for the estimation fallback. Only used when the
# provider does not report usage; `token_source` records which path was taken so
# estimates are never mistaken for measurements.
_CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class ModelPricing:
    """USD per million tokens. Indicative — update alongside provider changes."""

    input_per_mtok: float
    output_per_mtok: float


# Keyed by model-id prefix, longest match wins. An unpriced model yields
# `estimated_cost_usd=None` rather than a misleading zero.
MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-opus-4": ModelPricing(15.0, 75.0),
    "claude-sonnet-4": ModelPricing(3.0, 15.0),
    "claude-3-5-haiku": ModelPricing(0.80, 4.0),
    "claude-3-haiku": ModelPricing(0.25, 1.25),
    "codestral": ModelPricing(0.30, 0.90),
    "mistral-large": ModelPricing(2.0, 6.0),
    "mistral-small": ModelPricing(0.20, 0.60),
}

# Exception class names treated as transient. Matched by name so the gateway
# does not depend on provider SDK exception hierarchies staying stable.
_RETRYABLE_NAMES = frozenset({
    "APIConnectionError",
    "APITimeoutError",
    "APIStatusError",
    "InternalServerError",
    "RateLimitError",
    "ServiceUnavailableError",
    "OverloadedError",
    "ConnectionError",
    "ConnectError",
    "ReadError",
    "WriteError",
    "RemoteProtocolError",
    "TimeoutError",
    "ReadTimeout",
    "ConnectTimeout",
    "SDKError",
})

_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})


class LLMTimeoutError(RuntimeError):
    """Raised when a call exceeds the configured timeout on every attempt."""


class LLMConfigurationError(RuntimeError):
    """Raised when STUB_MODE is false but no LLM provider is configured.

    Distinguished from a transient provider error so the caller can tell a
    deployment problem from a service outage.
    """


class SecurityRejection(RuntimeError):
    """Raised when the security pipeline refuses to approve a call.

    Carries the rejection reason and the failing rules so an agent's fallback
    path can record *why* it fell back, rather than treating a policy refusal as
    an indistinguishable provider error.
    """

    def __init__(self, reason: str, rules: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.rules = rules or []


class LLMCallMetrics(BaseModel):
    """Per-call observability record. Emitted to logs and returned to callers."""

    # Call provenance. Without these a call could be measured but not
    # attributed, so "what did this run cost?" and "which agent spent it?" had
    # no answer even though every number needed to compute them existed.
    run_id: str = ""
    agent_id: str = ""
    retry_count: int = 0

    provider: str = ""
    model: str = ""
    operation: str = "generic"
    latency_ms: int = 0
    attempts: int = 0
    retried: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    token_source: TokenSource = "estimated"
    estimated_cost_usd: float | None = None
    success: bool = False
    error: str | None = None

    def to_log_extra(self) -> dict[str, Any]:
        return {"llm_metrics": self.model_dump(mode="json")}


class LLMResponse(BaseModel):
    text: str = ""
    metrics: LLMCallMetrics = Field(default_factory=LLMCallMetrics)


def _as_int(value: Any) -> int | None:
    """Coerce a provider usage field to int, tolerating absent or mocked values."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // _CHARS_PER_TOKEN)


def pricing_for_model(model: str) -> ModelPricing | None:
    """Longest-prefix pricing lookup. None when the model is not priced."""
    if not model:
        return None
    matches = [prefix for prefix in MODEL_PRICING if model.startswith(prefix)]
    if not matches:
        return None
    return MODEL_PRICING[max(matches, key=len)]


def estimate_cost_usd(model: str, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    pricing = pricing_for_model(model)
    if pricing is None or prompt_tokens is None or completion_tokens is None:
        return None
    cost = (
        prompt_tokens / 1_000_000 * pricing.input_per_mtok
        + completion_tokens / 1_000_000 * pricing.output_per_mtok
    )
    return round(cost, 6)


def is_retryable(exc: BaseException) -> bool:
    """Transient-failure classification by exception name and HTTP status."""
    if isinstance(exc, asyncio.TimeoutError):
        return True

    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in _RETRYABLE_STATUS

    for klass in type(exc).__mro__:
        if klass.__name__ in _RETRYABLE_NAMES:
            return True
    return False


class LLMGateway:
    """Provider-routing, retrying, measured LLM transport."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._anthropic: AsyncAnthropic | None = None
        self._mistral: Mistral | None = None
        self.last_metrics: LLMCallMetrics | None = None

    # -- clients ---------------------------------------------------------

    @property
    def anthropic_client(self) -> AsyncAnthropic:
        if self._anthropic is None:
            self._anthropic = AsyncAnthropic(api_key=self.settings.anthropic_api_key)
        return self._anthropic

    @property
    def mistral_client(self) -> Mistral:
        if self._mistral is None:
            self._mistral = Mistral(api_key=self.settings.mistral_api_key)
        return self._mistral

    @property
    def provider(self) -> str:
        return self.settings.llm_provider

    @property
    def model(self) -> str:
        if self.provider == "mistral":
            return self.settings.mistral_model
        return self.settings.anthropic_model

    def ensure_available(self) -> None:
        """Guard: the provider must be configured for real work.

        Raises `LLMConfigurationError` with an actionable message when
        `STUB_MODE=false` but the selected provider has no key/endpoint,
        so a misconfigured production deployment fails loudly at the first
        LLM call rather than silently returning stub patches.
        """
        if self.settings.stub_mode:
            raise RuntimeError("LLM unavailable in stub mode — use agent stub paths")
        if not self.settings.llm_configured():
            provider = self.settings.llm_provider
            env_hint = {
                "anthropic": "ANTHROPIC_API_KEY",
                "mistral": "MISTRAL_API_KEY",
                "openai": "OPENAI_API_KEY",
                "gemini": "GEMINI_API_KEY",
                "ollama": "OLLAMA_BASE_URL",
                "lmstudio": "LMSTUDIO_BASE_URL",
                "vllm": "VLLM_BASE_URL",
                "tgi": "TGI_BASE_URL",
            }.get(provider, f"{provider.upper()}_API_KEY")
            raise LLMConfigurationError(
                f"STUB_MODE is false but LLM provider '{provider}' is not configured. "
                f"Set {env_hint} in your environment or .env file, "
                f"or set STUB_MODE=true for development."
            )

    # -- transport -------------------------------------------------------

    async def complete(
        self,
        prompt: str,
        system: str,
        *,
        operation: str = "generic",
        json_mode: bool = False,
        run_id: str = "",
        agent_id: str = "",
        retry_count: int = 0,
        repository: str = "",
        repository_hash: str = "",
        classification: str = "",
        files: tuple[str, ...] = (),
        workspace_root: str = "",
    ) -> LLMResponse:
        """Send one completion, retrying transient failures, and record metrics.

        Every call is gated by the security pipeline first — there is no
        parameter that skips it and no alternate entry point to `_dispatch`.
        The prompt that reaches the provider is the *approved* prompt, which may
        differ from the one passed in: sanitization happens before egress, not
        as advice to the caller.
        """
        self.ensure_available()

        approved = await self._approve(
            prompt,
            system,
            operation=operation,
            run_id=run_id,
            agent_id=agent_id,
            retry_count=retry_count,
            repository=repository,
            repository_hash=repository_hash,
            classification=classification,
            files=files,
            workspace_root=workspace_root,
        )
        if approved is not None:
            # Use the sanitized text, not the caller's original.
            prompt, system = approved.prompt, approved.system

        metrics = LLMCallMetrics(
            run_id=run_id,
            agent_id=agent_id,
            retry_count=retry_count,
            provider=self.provider,
            model=self.model,
            operation=operation,
        )
        started = time.monotonic()
        max_attempts = max(1, self.settings.llm_max_attempts)
        last_exc: BaseException | None = None

        for attempt in range(1, max_attempts + 1):
            metrics.attempts = attempt
            try:
                raw = await asyncio.wait_for(
                    self._dispatch(prompt, system, json_mode=json_mode),
                    timeout=self.settings.llm_timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001 — classified below
                last_exc = exc
                if attempt >= max_attempts or not is_retryable(exc):
                    break
                metrics.retried = True
                await asyncio.sleep(self.settings.llm_retry_base_delay * (2 ** (attempt - 1)))
                continue

            text, usage = raw
            metrics.latency_ms = int((time.monotonic() - started) * 1000)
            self._record_tokens(metrics, prompt, system, text, usage)
            metrics.success = True
            self.last_metrics = metrics
            logger.info("llm_call", extra=metrics.to_log_extra())
            await self._audit_completion(approved, text, operation, metrics)
            return LLMResponse(text=text, metrics=metrics)

        metrics.latency_ms = int((time.monotonic() - started) * 1000)
        metrics.success = False
        metrics.error = f"{type(last_exc).__name__}: {last_exc}" if last_exc else "unknown error"
        self.last_metrics = metrics
        logger.warning("llm_call_failed", extra=metrics.to_log_extra())
        await self._audit_completion(approved, "", operation, metrics)

        if isinstance(last_exc, asyncio.TimeoutError):
            raise LLMTimeoutError(
                f"LLM call timed out after {metrics.attempts} attempt(s) "
                f"({self.settings.llm_timeout_seconds}s each)"
            ) from last_exc
        raise last_exc if last_exc else RuntimeError("LLM call failed without an exception")

    # -- security --------------------------------------------------------

    async def _approve(self, prompt: str, system: str, **context) -> Any:
        """Gate the call through the security pipeline.

        Returns the `ApprovedContext` whose sanitized text must be sent, or
        None when security is disabled by configuration. Raises
        `SecurityRejection` when the call is refused.

        Import is local so `backend.security` is not a hard import cycle for the
        gateway, and so the security layer can itself use the gateway's models.
        """
        if not getattr(self.settings, "security_enabled", True):
            return None

        from backend.services.security_pipeline import (
            SecurityRequest,
            get_security_pipeline,
        )

        pipeline = get_security_pipeline(self.settings)
        self._pipeline = pipeline

        try:
            approved = await pipeline.approve(
                SecurityRequest(
                    prompt=prompt,
                    system=system,
                    preferred_provider=self.provider,
                    preferred_model=self.model,
                    max_tokens=self.settings.llm_max_tokens,
                    **context,
                )
            )
        except Exception as exc:  # noqa: BLE001 — a control fault is not a pass
            if getattr(self.settings, "security_fail_closed", True):
                raise SecurityRejection(
                    f"security pipeline failed closed: {type(exc).__name__}: {exc}"
                ) from exc
            logger.warning("security_pipeline_error", extra={"security_error": str(exc)})
            return None

        if not approved.approved:
            rules: list[str] = []
            if approved.firewall:
                rules.extend(approved.firewall.rules_failed)
            if approved.policy_decision:
                rules.extend(v.rule for v in approved.policy_decision.violations)
            raise SecurityRejection(
                approved.rejection_reason or "rejected by security policy", rules
            )

        return approved

    async def _audit_completion(
        self,
        approved: Any,
        text: str,
        operation: str,
        metrics: LLMCallMetrics,
    ) -> None:
        """Record the outcome. An audit failure must never fail the call."""
        if approved is None or getattr(self, "_pipeline", None) is None:
            return
        try:
            await self._pipeline.record_completion(
                approved,
                response=text,
                operation=operation,
                agent_id=metrics.agent_id,
                retry_count=metrics.retry_count,
                attempts=metrics.attempts,
                prompt_tokens=metrics.prompt_tokens,
                completion_tokens=metrics.completion_tokens,
                total_tokens=metrics.total_tokens,
                estimated_cost_usd=metrics.estimated_cost_usd,
                latency_ms=metrics.latency_ms,
                success=metrics.success,
                error=metrics.error,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit_completion_failed", extra={"audit_error": str(exc)})

    async def _dispatch(
        self,
        prompt: str,
        system: str,
        *,
        json_mode: bool,
    ) -> tuple[str, Any]:
        if self.provider == "mistral":
            return await self._call_mistral(prompt, system, json_mode=json_mode)
        return await self._call_anthropic(prompt, system)

    async def _call_anthropic(self, prompt: str, system: str) -> tuple[str, Any]:
        response = await self.anthropic_client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=self.settings.llm_max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text, getattr(response, "usage", None)

    async def _call_mistral(self, prompt: str, system: str, *, json_mode: bool) -> tuple[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.settings.mistral_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": self.settings.llm_max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        response = await self.mistral_client.chat.complete_async(**kwargs)
        return _mistral_content(response), getattr(response, "usage", None)

    # -- accounting ------------------------------------------------------

    def _record_tokens(
        self,
        metrics: LLMCallMetrics,
        prompt: str,
        system: str,
        text: str,
        usage: Any,
    ) -> None:
        prompt_tokens = _as_int(getattr(usage, "input_tokens", None))
        if prompt_tokens is None:
            prompt_tokens = _as_int(getattr(usage, "prompt_tokens", None))

        completion_tokens = _as_int(getattr(usage, "output_tokens", None))
        if completion_tokens is None:
            completion_tokens = _as_int(getattr(usage, "completion_tokens", None))

        if prompt_tokens is not None and completion_tokens is not None:
            metrics.token_source = "provider"
        else:
            metrics.token_source = "estimated"
            prompt_tokens = _estimate_tokens(f"{system}\n{prompt}")
            completion_tokens = _estimate_tokens(text)

        metrics.prompt_tokens = prompt_tokens
        metrics.completion_tokens = completion_tokens
        metrics.total_tokens = prompt_tokens + completion_tokens
        metrics.estimated_cost_usd = estimate_cost_usd(
            metrics.model, prompt_tokens, completion_tokens
        )


def _mistral_content(response: Any) -> str:
    choice = response.choices[0]
    message = choice.message
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return content or ""
