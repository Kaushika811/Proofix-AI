"""LLM gateway — retries, timeouts, and per-call accounting.

Before the gateway, a transient provider error failed an entire agent and no run
could report what it cost. These tests pin the retry classification (transient
retried, permanent not) and the metrics contract.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from backend.config import Settings
from backend.services.llm import LLMService
from backend.services.llm_gateway import (
    LLMGateway,
    LLMTimeoutError,
    estimate_cost_usd,
    is_retryable,
    pricing_for_model,
)


class SampleOutput(BaseModel):
    answer: str


def anthropic_settings(**overrides) -> Settings:
    base = dict(
        stub_mode=False,
        llm_provider="anthropic",
        anthropic_api_key="test-key",
        anthropic_model="claude-sonnet-4-20250514",
        llm_retry_base_delay=0.0,  # keep tests fast
    )
    base.update(overrides)
    return Settings(**base)


def anthropic_response(text: str = '{"answer": "ok"}', *, usage=None):
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = usage
    return response


def client_returning(*side_effects):
    client = MagicMock()
    client.messages.create = AsyncMock(side_effect=side_effects)
    return client


class TransientError(Exception):
    """Stands in for a provider connection failure."""

    def __init__(self, message="boom", status_code=None):
        super().__init__(message)
        if status_code is not None:
            self.status_code = status_code


class APIConnectionError(Exception):
    """Name-matched as retryable, mirroring the provider SDK class name."""


# -- retry classification --------------------------------------------------


def test_retryable_by_exception_name():
    assert is_retryable(APIConnectionError("network down")) is True


def test_retryable_by_http_status():
    assert is_retryable(TransientError(status_code=503)) is True
    assert is_retryable(TransientError(status_code=429)) is True


def test_not_retryable_for_client_errors():
    assert is_retryable(TransientError(status_code=400)) is False
    assert is_retryable(TransientError(status_code=401)) is False
    assert is_retryable(ValueError("bad schema")) is False


def test_asyncio_timeout_is_retryable():
    assert is_retryable(asyncio.TimeoutError()) is True


# -- retries ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_transient_failure_then_succeeds():
    gateway = LLMGateway(anthropic_settings())
    gateway._anthropic = client_returning(
        APIConnectionError("reset"),
        anthropic_response("recovered"),
    )

    response = await gateway.complete("prompt", "system")

    assert response.text == "recovered"
    assert response.metrics.attempts == 2
    assert response.metrics.retried is True
    assert response.metrics.success is True


@pytest.mark.asyncio
async def test_httpx_connection_failure_retries_then_succeeds():
    import httpx
    gateway = LLMGateway(anthropic_settings())
    gateway._anthropic = client_returning(
        httpx.ConnectError("temporary DNS failure"), anthropic_response("recovered"),
    )
    response = await gateway.complete("prompt", "system")
    assert response.text == "recovered"
    assert response.metrics.attempts == 2
    assert response.metrics.retried is True


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts():
    gateway = LLMGateway(anthropic_settings(llm_max_attempts=3))
    gateway._anthropic = client_returning(*[APIConnectionError("reset")] * 3)

    with pytest.raises(APIConnectionError):
        await gateway.complete("prompt", "system")

    assert gateway.last_metrics.attempts == 3
    assert gateway.last_metrics.success is False
    assert "APIConnectionError" in gateway.last_metrics.error


@pytest.mark.asyncio
async def test_permanent_error_is_not_retried():
    gateway = LLMGateway(anthropic_settings())
    client = client_returning(TransientError("bad request", status_code=400))
    gateway._anthropic = client

    with pytest.raises(TransientError):
        await gateway.complete("prompt", "system")

    assert client.messages.create.await_count == 1
    assert gateway.last_metrics.retried is False


@pytest.mark.asyncio
async def test_single_attempt_configuration_disables_retry():
    gateway = LLMGateway(anthropic_settings(llm_max_attempts=1))
    client = client_returning(APIConnectionError("reset"))
    gateway._anthropic = client

    with pytest.raises(APIConnectionError):
        await gateway.complete("prompt", "system")

    assert client.messages.create.await_count == 1


# -- timeouts --------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_raises_llm_timeout_error():
    async def never_returns(**_kwargs):
        await asyncio.sleep(10)

    gateway = LLMGateway(anthropic_settings(llm_timeout_seconds=0.01, llm_max_attempts=1))
    client = MagicMock()
    client.messages.create = never_returns
    gateway._anthropic = client

    with pytest.raises(LLMTimeoutError):
        await gateway.complete("prompt", "system")

    assert gateway.last_metrics.success is False


@pytest.mark.asyncio
async def test_timeout_is_retried_before_failing():
    calls = {"n": 0}

    async def slow_then_fast(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            await asyncio.sleep(10)
        return anthropic_response("second try")

    gateway = LLMGateway(anthropic_settings(llm_timeout_seconds=0.01, llm_max_attempts=2))
    client = MagicMock()
    client.messages.create = slow_then_fast
    gateway._anthropic = client

    response = await gateway.complete("prompt", "system")
    assert response.text == "second try"
    assert calls["n"] == 2


# -- metrics ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_use_provider_usage_when_available():
    usage = MagicMock()
    usage.input_tokens = 1200
    usage.output_tokens = 300

    gateway = LLMGateway(anthropic_settings())
    gateway._anthropic = client_returning(anthropic_response(usage=usage))

    metrics = (await gateway.complete("prompt", "system", operation="patch")).metrics

    assert metrics.token_source == "provider"
    assert metrics.prompt_tokens == 1200
    assert metrics.completion_tokens == 300
    assert metrics.total_tokens == 1500
    assert metrics.operation == "patch"
    assert metrics.provider == "anthropic"
    assert metrics.model == "claude-sonnet-4-20250514"
    assert metrics.latency_ms >= 0


@pytest.mark.asyncio
async def test_metrics_fall_back_to_estimates_without_usage():
    gateway = LLMGateway(anthropic_settings())
    gateway._anthropic = client_returning(anthropic_response("x" * 400, usage=None))

    metrics = (await gateway.complete("p" * 800, "system")).metrics

    assert metrics.token_source == "estimated"
    assert metrics.prompt_tokens > 0
    assert metrics.completion_tokens > 0


@pytest.mark.asyncio
async def test_mocked_usage_fields_do_not_corrupt_accounting():
    """A bare MagicMock usage object must not be read as token counts."""
    gateway = LLMGateway(anthropic_settings())
    gateway._anthropic = client_returning(anthropic_response(usage=MagicMock()))

    metrics = (await gateway.complete("prompt", "system")).metrics

    assert metrics.token_source == "estimated"
    assert isinstance(metrics.prompt_tokens, int)


@pytest.mark.asyncio
async def test_cost_is_estimated_for_known_models():
    usage = MagicMock()
    usage.input_tokens = 1_000_000
    usage.output_tokens = 0

    gateway = LLMGateway(anthropic_settings())
    gateway._anthropic = client_returning(anthropic_response(usage=usage))

    metrics = (await gateway.complete("prompt", "system")).metrics
    assert metrics.estimated_cost_usd == pytest.approx(3.0)


def test_cost_is_none_for_unpriced_model():
    assert pricing_for_model("some-unreleased-model") is None
    assert estimate_cost_usd("some-unreleased-model", 100, 100) is None


def test_pricing_prefers_longest_matching_prefix():
    assert pricing_for_model("claude-3-5-haiku-20241022").input_per_mtok == 0.80
    assert pricing_for_model("claude-3-haiku-20240307").input_per_mtok == 0.25


def test_cost_none_without_token_counts():
    assert estimate_cost_usd("claude-sonnet-4", None, 10) is None


# -- stub mode and facade --------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_refuses_to_run_in_stub_mode():
    gateway = LLMGateway(Settings(stub_mode=True, anthropic_api_key="x"))
    with pytest.raises(RuntimeError, match="stub mode"):
        await gateway.complete("prompt", "system")


@pytest.mark.asyncio
async def test_gateway_refuses_when_provider_unconfigured():
    gateway = LLMGateway(Settings(stub_mode=False, llm_provider="anthropic", anthropic_api_key=""))
    with pytest.raises(RuntimeError, match="not configured|stub mode"):
        await gateway.complete("prompt", "system")


@pytest.mark.asyncio
async def test_service_routes_through_gateway_and_exposes_metrics():
    service = LLMService(anthropic_settings())
    service._anthropic = client_returning(anthropic_response('{"answer": "routed"}'))

    result = await service.structured("prompt", SampleOutput)

    assert result.answer == "routed"
    assert service.last_metrics is not None
    assert service.last_metrics.operation == "structured"
    assert service.last_metrics.success is True


@pytest.mark.asyncio
async def test_service_retry_is_transparent_to_callers():
    """Agents keep their simple API; the retry happens beneath them."""
    service = LLMService(anthropic_settings())
    service._anthropic = client_returning(
        APIConnectionError("reset"),
        anthropic_response('{"answer": "second"}'),
    )

    result = await service.structured("prompt", SampleOutput)

    assert result.answer == "second"
    assert service.last_metrics.attempts == 2


@pytest.mark.asyncio
async def test_service_text_call_records_operation():
    service = LLMService(anthropic_settings())
    service._anthropic = client_returning(anthropic_response("plain text"))

    assert await service.text("prompt") == "plain text"
    assert service.last_metrics.operation == "text"


@pytest.mark.asyncio
async def test_injected_client_is_shared_with_gateway():
    """The historical `service._anthropic = mock` seam must still work."""
    service = LLMService(anthropic_settings())
    client = client_returning(anthropic_response())
    service._anthropic = client

    assert service.gateway._anthropic is client
    assert service._anthropic is client
