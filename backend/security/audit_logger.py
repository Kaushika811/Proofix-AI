"""Immutable audit trail for every LLM interaction.

Each event is hash-chained: `entry_hash` covers the event's content *and* the
previous event's hash, so altering or deleting any historical record breaks
verification from that point forward. That is what makes the log evidence rather
than a diary — a tamper-evident chain can be checked by someone who does not
trust the process that wrote it.

**Raw prompts are not stored by default.** The log records `prompt_hash`, which
proves what was sent without disclosing it. An audit log is read by more people
than the repository is, and storing sanitized-but-sensitive prompts there would
move the disclosure rather than prevent it. `audit_store_raw_prompts` exists for
incident investigation and is off by default.

Storage is append-only through `RedisStore`, with an in-memory ring for the
current process so the dashboard can render without a round trip.

**Verification segments.** The chain is global and its head lives in process
memory, so two ordinary situations produce a ledger that is not one unbroken
sequence, and both were previously reported as tampering:

* *Concurrent runs.* Runs interleave in one process, so asking for a single
  run's events yields a view whose members are not adjacent in the chain.
* *Process restarts.* `_last_hash` resets to `GENESIS_HASH`, so a ledger read
  back from durable storage contains one segment per process that wrote it.

`verify_chain` therefore verifies *segments*: maximal runs of genuinely
adjacent events. A new segment is legitimate only at a process start
(`previous_hash == GENESIS_HASH` **and** `sequence <= 1`) or, when the caller
declares `scope="subset"`, at a proven gap in `sequence` showing that
intervening events exist and were filtered out.

This preserves tamper detection in full. `compute_entry_hash` covers the
payload, `sequence` and `previous_hash`, and every event's hash is recomputed
unconditionally before linkage is consulted — so modifying any field fails, and
forging a `GENESIS_HASH` to fabricate a boundary fails with it. The sequence
guard is what stops a deletion being disguised as a restart. The one property
`subset` scope cannot establish is completeness: an event removed from a
filtered view is indistinguishable from one filtered out of it. That is
inherent to filtering — the previous implementation did not detect it either,
it simply reported every concurrent run as broken.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections import deque
from datetime import datetime
from typing import Literal

from backend.models.security import (
    AuditEvent,
    DataClassification,
    Decision,
    FirewallVerdict,
    PolicyDecision,
    RoutingDecision,
    SanitizationReport,
)

logger = logging.getLogger(__name__)

AUDIT_NAMESPACE = "security_audit"
AUDIT_VERSION = "v1"

# Events retained in-process for the dashboard. The durable copy is in Redis.
MEMORY_RING_SIZE = 1000

GENESIS_HASH = "0" * 64

# What the caller is handing to `verify_chain`.
#   complete — the whole ledger, or one process's whole ledger. Every adjacency
#              must hold, so a deleted event is detected.
#   subset   — a filtered view (today: one run out of a ledger shared by
#              concurrent runs). Gaps are expected and must not read as
#              tampering; per-event integrity is still enforced in full.
VerificationScope = Literal["complete", "subset"]


def _segment_boundary(
    event: AuditEvent,
    previous_event: AuditEvent | None,
    scope: VerificationScope,
) -> str | None:
    """Name the legitimate reason this event starts a new segment, or None.

    Called only when linkage did not continue, and only after the event's own
    hash has already been verified — so the fields consulted here (`sequence`,
    `previous_hash`) are known to be authentic.
    """
    if event.previous_hash == GENESIS_HASH and event.sequence <= 1:
        # A process wrote this event as its first. The chain head is in-process
        # state, so this is how every restart looks.
        #
        # The sequence guard matters: without it, deleting an event and
        # relabelling its successor as a process start would splice the ledger
        # undetected. `sequence` restarts at 1 with the chain head, so a
        # mid-ledger event claiming GENESIS is not a restart — it is a forgery,
        # and this is where the old implementation's strictness is preserved.
        return "process_start"

    if (
        scope == "subset"
        and previous_event is not None
        and event.sequence > previous_event.sequence + 1
    ):
        # Sequence skipped: events exist between these two and were filtered
        # out. Adjacent sequence numbers with broken linkage is still a break.
        return "filtered_gap"

    return None


def content_hash(value: str) -> str:
    """SHA-256 of a value. Used for prompts, contexts and responses."""
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def compute_entry_hash(event: AuditEvent) -> str:
    """Hash covering the event's content plus its link to the previous entry.

    Explicit field list rather than a model dump: adding a field later must not
    silently change historical hashes and invalidate a chain that is intact.
    """
    payload = json.dumps(
        {
            "event_id": event.event_id,
            "sequence": event.sequence,
            "timestamp": event.timestamp.isoformat(),
            "run_id": event.run_id,
            "repository_hash": event.repository_hash,
            "context_hash": event.context_hash,
            "prompt_hash": event.prompt_hash,
            "response_hash": event.response_hash,
            "provider": event.provider,
            "model": event.model,
            "policy": event.policy,
            "classification": event.classification,
            "decision": event.decision,
            "result": event.result,
            "secret_count": event.secret_count,
            "pii_count": event.pii_count,
            "prompt_chars": event.prompt_chars,
            "total_tokens": event.total_tokens,
            "previous_hash": event.previous_hash,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLogger:
    """Append-only, hash-chained event log."""

    def __init__(self, store=None, settings=None, encryption=None):
        self.store = store
        self.settings = settings
        self.encryption = encryption
        self._events: deque[AuditEvent] = deque(maxlen=MEMORY_RING_SIZE)
        self._last_hash: str = GENESIS_HASH
        self._sequence: int = 0

    # -- writing ---------------------------------------------------------

    def build_event(
        self,
        *,
        run_id: str = "",
        repository_hash: str = "",
        context_hash: str = "",
        prompt: str = "",
        response: str = "",
        provider: str = "",
        model: str = "",
        policy: str = "",
        classification: DataClassification = "PRIVATE",
        operation: str = "generic",
        actor: str = "pipeline",
        agent_id: str = "",
        retry_count: int = 0,
        attempts: int = 0,
        files: tuple[str, ...] = (),
        sanitization: SanitizationReport | None = None,
        policy_decision: PolicyDecision | None = None,
        firewall: FirewallVerdict | None = None,
        routing: RoutingDecision | None = None,
        decision: Decision = "allow",
        result: str = "success",
        failure_reason: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        latency_ms: int = 0,
    ) -> AuditEvent:
        """Assemble an event and link it into the chain."""
        report = sanitization or SanitizationReport()

        violations: list[str] = []
        if policy_decision:
            violations.extend(f"policy:{v.rule}" for v in policy_decision.violations)
        if firewall:
            violations.extend(f"firewall:{v.rule}" for v in firewall.violations)
        if routing and not routing.permitted:
            violations.append("routing:not_permitted")

        self._sequence += 1
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            sequence=self._sequence,
            timestamp=datetime.utcnow(),
            run_id=run_id,
            repository_hash=repository_hash,
            context_hash=context_hash,
            prompt_hash=content_hash(prompt),
            response_hash=content_hash(response) if response else "",
            provider=provider,
            model=model,
            policy=policy,
            classification=classification,
            operation=operation,
            actor=actor,
            agent_id=agent_id,
            retry_count=retry_count,
            attempts=attempts,
            files_included=sorted(files),
            secret_count=report.secret_count,
            pii_count=report.pii_count,
            sanitization_count=len(report.sanitized),
            sanitization_categories=report.categories(),
            prompt_chars=len(prompt or ""),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            latency_ms=latency_ms,
            decision=decision,
            result=result,  # type: ignore[arg-type]
            failure_reason=failure_reason,
            violations=violations,
            previous_hash=self._last_hash,
        )

        if self._store_raw_prompts():
            event.raw_prompt = prompt

        event.entry_hash = compute_entry_hash(event)
        self._last_hash = event.entry_hash
        return event

    def _store_raw_prompts(self) -> bool:
        return bool(getattr(self.settings, "audit_store_raw_prompts", False))

    async def record(self, event: AuditEvent) -> AuditEvent:
        """Append an event to memory and durable storage."""
        self._events.append(event)

        logger.info(
            "security_audit",
            extra={
                "audit": {
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "run_id": event.run_id,
                    "provider": event.provider,
                    "model": event.model,
                    "policy": event.policy,
                    "decision": event.decision,
                    "result": event.result,
                    "secret_count": event.secret_count,
                    "pii_count": event.pii_count,
                    "prompt_hash": event.prompt_hash,
                    "entry_hash": event.entry_hash,
                }
            },
        )

        if self.store is not None:
            await self._persist(event)
        return event

    async def _persist(self, event: AuditEvent) -> None:
        """Write to durable storage, encrypting when a key is configured."""
        try:
            payload = event.model_dump_json()
            if self.encryption is not None and self.encryption.enabled:
                payload, _encrypted = self.encryption.encrypt_if_enabled(
                    payload, associated_data=f"audit:{event.event_id}"
                )
            await self.store.set_cached(
                AUDIT_NAMESPACE,
                AUDIT_VERSION,
                f"{event.run_id or 'global'}:{event.sequence:08d}:{event.event_id}",
                payload,
                getattr(self.settings, "audit_retention_seconds", None),
            )
        except Exception as exc:  # noqa: BLE001 — a storage fault must not fail the run
            logger.warning("audit_persist_failed", extra={"audit_error": str(exc)})

    # -- reading ---------------------------------------------------------

    def events(self, run_id: str | None = None, limit: int = 100) -> list[AuditEvent]:
        """In-process events, newest last, optionally scoped to one run."""
        selected = [e for e in self._events if run_id is None or e.run_id == run_id]
        return selected[-limit:]

    def timeline(self, run_id: str | None = None, limit: int = 100) -> list[dict]:
        """Decision timeline for the dashboard. Hashes only, never content."""
        return [
            {
                "sequence": e.sequence,
                "timestamp": e.timestamp.isoformat(),
                "run_id": e.run_id,
                "agent_id": e.agent_id,
                "retry_count": e.retry_count,
                "attempts": e.attempts,
                "operation": e.operation,
                "policy": e.policy,
                "classification": e.classification,
                "provider": e.provider,
                "model": e.model,
                "decision": e.decision,
                "result": e.result,
                "secret_count": e.secret_count,
                "pii_count": e.pii_count,
                "sanitization_count": e.sanitization_count,
                "prompt_chars": e.prompt_chars,
                "total_tokens": e.total_tokens,
                "estimated_cost_usd": e.estimated_cost_usd,
                "latency_ms": e.latency_ms,
                "violations": e.violations,
                "prompt_hash": e.prompt_hash[:16],
                "entry_hash": e.entry_hash[:16],
            }
            for e in self.events(run_id, limit)
        ]

    # -- integrity -------------------------------------------------------

    def verify_chain(
        self,
        events: list[AuditEvent] | None = None,
        *,
        scope: VerificationScope = "complete",
    ) -> tuple[bool, str]:
        """Recompute the chain. Returns (intact, explanation).

        The check a compliance auditor actually runs: it does not trust the
        stored `entry_hash`, it recomputes it.

        **Two independent guarantees.** Every event's `entry_hash` is recomputed
        unconditionally — that is what detects tampering, and it is never
        relaxed. Separately, adjacent events are checked to be *linked*, which
        is what detects deletion. Only the second check knows about segments.

        **What a segment is.** A segment is a maximal run of events that are
        genuinely adjacent in the underlying ledger. A new segment legitimately
        begins in exactly two situations:

        1. **A process start** — `previous_hash == GENESIS_HASH`. The chain
           head lives in memory, so every process starts a fresh segment. A
           ledger read back from Redis therefore contains one segment per
           process that wrote to it, and all of them are legitimate.
        2. **A filtered view** (`scope="subset"` only) — the caller asked for
           one run's events out of a ledger shared by concurrent runs, so the
           events in between belong to other runs and are absent by request.
           Recognised by a gap in `sequence`, which proves intervening events
           exist rather than assuming it.

        Anything else is a break and fails.

        **Why this does not weaken tamper detection.** `compute_entry_hash`
        covers `sequence`, `previous_hash` and the event payload, so altering
        any of them — including forging a `GENESIS_HASH` to fake a boundary —
        changes the recomputed hash and fails before linkage is even consulted.
        In `complete` scope, deleting an event still breaks linkage exactly as
        before. The one thing `subset` scope cannot prove is *completeness*:
        an event deleted from a filtered view is indistinguishable from an
        event filtered out of it. That is inherent to filtering — the previous
        implementation did not detect it either, it merely reported every
        concurrent run as tampered.
        """
        chain = events if events is not None else list(self._events)
        if not chain:
            return True, "no events to verify"

        expected_previous = chain[0].previous_hash
        previous_event: AuditEvent | None = None
        segments = 1

        for event in chain:
            # Integrity first: a modified event is reported as modified, not as
            # a broken link, whichever field was touched.
            recomputed = compute_entry_hash(event)
            if recomputed != event.entry_hash:
                return False, (
                    f"event {event.sequence} was modified after writing: "
                    f"stored {event.entry_hash[:16]}, recomputed {recomputed[:16]}"
                )

            if event.previous_hash != expected_previous:
                boundary = _segment_boundary(event, previous_event, scope)
                if boundary is None:
                    return False, (
                        f"chain broken at sequence {event.sequence}: "
                        f"expected previous {expected_previous[:16]}, "
                        f"found {event.previous_hash[:16]}"
                    )
                segments += 1

            expected_previous = event.entry_hash
            previous_event = event

        if segments == 1:
            return True, f"{len(chain)} event(s) verified"
        return True, f"{len(chain)} event(s) verified across {segments} segment(s)"

    # -- aggregation -----------------------------------------------------

    def summary(self, run_id: str | None = None) -> dict:
        events = self.events(run_id, limit=MEMORY_RING_SIZE)
        providers: dict[str, int] = {}
        policies: dict[str, int] = {}
        cost = 0.0

        for event in events:
            if event.provider:
                providers[event.provider] = providers.get(event.provider, 0) + 1
            if event.policy:
                policies[event.policy] = policies.get(event.policy, 0) + 1
            cost += event.estimated_cost_usd or 0.0

        # A run-scoped summary hands `verify_chain` a filtered view: the ledger
        # is global and concurrent runs interleave in it, so this run's events
        # are not adjacent. Declaring the scope is what stops that being
        # reported as tampering.
        intact, detail = self.verify_chain(
            events, scope="subset" if run_id else "complete"
        )
        return {
            "events": len(events),
            "providers": dict(sorted(providers.items())),
            "policies": dict(sorted(policies.items())),
            "by_agent": self._by_agent(events),
            "secrets_detected": sum(e.secret_count for e in events),
            "pii_detected": sum(e.pii_count for e in events),
            "rejected": sum(1 for e in events if e.result == "rejected"),
            "estimated_cost_usd": round(cost, 6),
            "chain_intact": intact,
            "chain_detail": detail,
            "raw_prompts_stored": self._store_raw_prompts(),
        }

    @staticmethod
    def _by_agent(events: list[AuditEvent]) -> list[dict]:
        """Per-agent LLM usage, aggregated here rather than in the client.

        Events written before call attribution existed carry no `agent_id`;
        they are grouped under the empty key so their cost is still counted
        rather than silently dropped from the run total.
        """
        buckets: dict[str, dict] = {}
        for event in events:
            bucket = buckets.setdefault(
                event.agent_id,
                {
                    "agent_id": event.agent_id,
                    "calls": 0,
                    "rejected": 0,
                    "providers": {},
                    "models": {},
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "latency_ms": 0,
                    "max_retry_count": 0,
                },
            )
            bucket["calls"] += 1
            if event.result == "rejected":
                bucket["rejected"] += 1
            if event.provider:
                bucket["providers"][event.provider] = bucket["providers"].get(event.provider, 0) + 1
            if event.model:
                bucket["models"][event.model] = bucket["models"].get(event.model, 0) + 1
            bucket["prompt_tokens"] += event.prompt_tokens or 0
            bucket["completion_tokens"] += event.completion_tokens or 0
            bucket["total_tokens"] += event.total_tokens or 0
            bucket["estimated_cost_usd"] += event.estimated_cost_usd or 0.0
            bucket["latency_ms"] += event.latency_ms
            bucket["max_retry_count"] = max(bucket["max_retry_count"], event.retry_count)

        for bucket in buckets.values():
            bucket["estimated_cost_usd"] = round(bucket["estimated_cost_usd"], 6)
            bucket["providers"] = dict(sorted(bucket["providers"].items()))
            bucket["models"] = dict(sorted(bucket["models"].items()))

        return [buckets[key] for key in sorted(buckets)]
