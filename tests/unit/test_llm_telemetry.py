"""Tests for LLM call attribution (G9).

Every number needed to answer "what did this run cost, and which agent spent
it?" already existed — but `LLMService` never passed `run_id` to the gateway, so
every audit event carried an empty one and run-scoped queries returned nothing.
These tests pin the provenance now threaded through the whole path.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from backend.config import Settings
from backend.security.audit_logger import AuditLogger, compute_entry_hash
from backend.services.llm import LLMService
from backend.services.llm_gateway import LLMGateway

RUN_ID = "7c1e5a90-2b3c-4d5e-8f90-1a2b3c4d5e6f"


class SampleOutput(BaseModel):
    answer: str


def anthropic_settings(**overrides) -> Settings:
    base = dict(
        stub_mode=False,
        llm_provider="anthropic",
        anthropic_api_key="test-key",
        anthropic_model="claude-sonnet-4-20250514",
        llm_retry_base_delay=0.0,
        security_enabled=False,
    )
    base.update(overrides)
    return Settings(**base)


def anthropic_client(text: str = '{"answer": "ok"}'):
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=120, output_tokens=40)
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)
    return client


# -- service → gateway ----------------------------------------------------


async def test_structured_call_carries_run_and_agent_identity():
    settings = anthropic_settings()
    service = LLMService(settings, run_id=RUN_ID, agent_id="A4", retry_count=2)
    service._anthropic = anthropic_client()

    await service.structured("prompt", SampleOutput)

    metrics = service.last_metrics
    assert metrics.run_id == RUN_ID
    assert metrics.agent_id == "A4"
    assert metrics.retry_count == 2


async def test_text_call_carries_run_and_agent_identity():
    settings = anthropic_settings()
    service = LLMService(settings, run_id=RUN_ID, agent_id="chat")
    service._anthropic = anthropic_client("plain answer")

    await service.text("prompt")

    assert service.last_metrics.run_id == RUN_ID
    assert service.last_metrics.agent_id == "chat"


async def test_metrics_report_the_full_telemetry_set():
    settings = anthropic_settings()
    service = LLMService(settings, run_id=RUN_ID, agent_id="A7", retry_count=1)
    service._anthropic = anthropic_client()

    await service.structured("prompt", SampleOutput)
    metrics = service.last_metrics

    assert metrics.provider == "anthropic"
    assert metrics.model == "claude-sonnet-4-20250514"
    assert metrics.operation == "structured"
    assert metrics.prompt_tokens == 120
    assert metrics.completion_tokens == 40
    assert metrics.total_tokens == 160
    assert metrics.estimated_cost_usd is not None
    assert metrics.latency_ms >= 0
    assert metrics.token_source == "provider"


async def test_identity_defaults_to_empty_rather_than_a_placeholder():
    # An un-attributed call must be visibly un-attributed, not labelled with a
    # stand-in that would look like a real agent in the audit trail.
    settings = anthropic_settings()
    service = LLMService(settings)
    service._anthropic = anthropic_client()

    await service.structured("prompt", SampleOutput)

    assert service.last_metrics.run_id == ""
    assert service.last_metrics.agent_id == ""


async def test_gateway_accepts_identity_directly():
    gateway = LLMGateway(anthropic_settings())
    gateway._anthropic = anthropic_client("hello")

    response = await gateway.complete(
        "p", "s", operation="custom", run_id=RUN_ID, agent_id="A6", retry_count=3
    )

    assert response.metrics.run_id == RUN_ID
    assert response.metrics.agent_id == "A6"
    assert response.metrics.retry_count == 3
    assert response.metrics.operation == "custom"


# -- gateway → audit ------------------------------------------------------


async def test_completion_is_audited_against_the_run_and_agent():
    settings = anthropic_settings(security_enabled=True)
    gateway = LLMGateway(settings)
    gateway._anthropic = anthropic_client()

    await gateway.complete("prompt", "system", run_id=RUN_ID, agent_id="A7", retry_count=1)

    events = gateway._pipeline.audit.events(RUN_ID)
    assert events, "the call produced no run-scoped audit event"
    event = events[-1]
    assert event.run_id == RUN_ID
    assert event.agent_id == "A7"
    assert event.retry_count == 1
    assert event.attempts >= 1
    assert event.provider and event.model


async def test_run_scoped_audit_queries_now_return_the_call():
    settings = anthropic_settings(security_enabled=True)
    gateway = LLMGateway(settings)
    gateway._anthropic = anthropic_client()

    await gateway.complete("prompt", "system", run_id=RUN_ID, agent_id="A4")

    timeline = gateway._pipeline.audit.timeline(RUN_ID)
    assert timeline, "timeline?run_id= returned nothing for a call made under that run"
    assert timeline[-1]["agent_id"] == "A4"
    assert timeline[-1]["run_id"] == RUN_ID

    summary = gateway._pipeline.audit.summary(RUN_ID)
    assert summary["events"] >= 1


# -- per-agent aggregation -------------------------------------------------


def _logger_with(events: list[tuple[str, float, int]]) -> AuditLogger:
    logger = AuditLogger(settings=Settings(security_enabled=True))
    for agent_id, cost, latency in events:
        logger._events.append(
            logger.build_event(
                run_id=RUN_ID,
                agent_id=agent_id,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                estimated_cost_usd=cost,
                latency_ms=latency,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
            )
        )
    return logger


def test_summary_aggregates_cost_and_latency_per_agent():
    logger = _logger_with([("A4", 0.01, 100), ("A7", 0.02, 250), ("A7", 0.03, 150)])

    by_agent = {b["agent_id"]: b for b in logger.summary(RUN_ID)["by_agent"]}

    assert by_agent["A4"]["calls"] == 1
    assert by_agent["A7"]["calls"] == 2
    assert by_agent["A7"]["estimated_cost_usd"] == pytest.approx(0.05)
    assert by_agent["A7"]["latency_ms"] == 400
    assert by_agent["A7"]["total_tokens"] == 30
    assert by_agent["A7"]["providers"] == {"anthropic": 2}


def test_unattributed_events_are_counted_not_dropped():
    # Events written before attribution existed still carry real cost.
    logger = _logger_with([("", 0.01, 50)])
    by_agent = {b["agent_id"]: b for b in logger.summary(RUN_ID)["by_agent"]}
    assert by_agent[""]["calls"] == 1
    assert by_agent[""]["estimated_cost_usd"] == pytest.approx(0.01)


# -- backwards compatibility ----------------------------------------------


def test_attribution_fields_are_outside_the_audit_hash():
    """Adding attribution must not invalidate an intact historical chain.

    `compute_entry_hash` uses an explicit field list precisely so new fields
    cannot change historical hashes. This asserts that property directly.
    """
    logger = AuditLogger(settings=Settings(security_enabled=True))
    event = logger.build_event(run_id=RUN_ID, agent_id="A7", retry_count=4, attempts=2)

    before = compute_entry_hash(event)
    event.agent_id = "A1"
    event.retry_count = 99
    event.attempts = 7

    assert compute_entry_hash(event) == before


def test_audit_event_deserializes_without_the_new_fields():
    from backend.models.security import AuditEvent

    event = AuditEvent.model_validate({"event_id": "e1"})
    assert event.agent_id == ""
    assert event.retry_count == 0
    assert event.attempts == 0
