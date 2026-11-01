"""Segment-aware audit chain verification.

The hash chain is global and its head lives in process memory. Two consequences
made verification report intact ledgers as tampered:

* concurrent runs interleave, so a run-scoped view is not contiguous;
* every process starts from GENESIS, so a ledger read back from Redis contains
  one segment per process that wrote it.

These tests pin both halves of the contract: legitimate boundaries verify, and
every form of tampering still fails.
"""

import json

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.models.security import AuditEvent
from backend.security.audit_logger import (
    AUDIT_NAMESPACE,
    AUDIT_VERSION,
    GENESIS_HASH,
    AuditLogger,
    compute_entry_hash,
)
from backend.state.redis_store import RedisStore


def _logger(store=None) -> AuditLogger:
    return AuditLogger(store=store, settings=Settings(security_enabled=True))


async def _record(log: AuditLogger, run_id: str, **kwargs) -> AuditEvent:
    return await log.record(
        log.build_event(run_id=run_id, provider="anthropic", model="m", **kwargs)
    )


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisStore(client, Settings(redis_url="redis://localhost:6379/0"))
    await client.aclose()


# -- legitimate boundaries -------------------------------------------------


async def test_single_process_single_run_is_one_segment():
    log = _logger()
    for _ in range(3):
        await _record(log, "runA")

    intact, detail = log.verify_chain()
    assert intact
    # The common case keeps its original wording.
    assert detail == "3 event(s) verified"


async def test_concurrent_runs_do_not_break_run_scoped_verification():
    log = _logger()
    for run in ("runA", "runB", "runA", "runB"):
        await _record(log, run)

    assert log.verify_chain()[0] is True
    for run in ("runA", "runB"):
        summary = log.summary(run)
        assert summary["chain_intact"] is True, summary["chain_detail"]
        assert "2 segment(s)" in summary["chain_detail"]


async def test_interleaved_three_runs_all_verify():
    log = _logger()
    for run in ["a", "b", "c", "a", "c", "b", "a"]:
        await _record(log, run)

    assert log.verify_chain()[0] is True
    for run in ("a", "b", "c"):
        assert log.summary(run)["chain_intact"] is True


async def test_sequential_runs_remain_one_segment_each():
    # No interleaving: each run's events are already adjacent.
    log = _logger()
    for run in ("runA", "runA", "runB", "runB"):
        await _record(log, run)

    assert log.summary("runA")["chain_detail"] == "2 event(s) verified"
    assert log.summary("runB")["chain_detail"] == "2 event(s) verified"


async def test_process_restart_is_a_legitimate_segment():
    first = _logger()
    await _record(first, "runA")
    await _record(first, "runA")

    # A new process: fresh sequence, fresh chain head.
    second = _logger()
    await _record(second, "runA")
    await _record(second, "runA")

    merged = list(first._events) + list(second._events)
    intact, detail = first.verify_chain(merged)
    assert intact, detail
    assert "2 segment(s)" in detail


async def test_many_restarts_verify():
    merged: list[AuditEvent] = []
    for _ in range(5):
        process = _logger()
        await _record(process, "runA")
        await _record(process, "runA")
        merged.extend(process._events)

    intact, detail = _logger().verify_chain(merged)
    assert intact, detail
    assert "5 segment(s)" in detail


async def test_restart_and_concurrency_combined():
    merged: list[AuditEvent] = []
    for _ in range(3):
        process = _logger()
        for run in ("runA", "runB", "runA"):
            await _record(process, run)
        merged.extend(process._events)

    assert _logger().verify_chain(merged)[0] is True

    run_a = [e for e in merged if e.run_id == "runA"]
    intact, detail = _logger().verify_chain(run_a, scope="subset")
    assert intact, detail


async def test_empty_and_single_event_chains():
    log = _logger()
    assert log.verify_chain() == (True, "no events to verify")
    await _record(log, "runA")
    assert log.verify_chain()[0] is True


# -- Redis persistence -----------------------------------------------------


async def _persisted_events(store: RedisStore, run_id: str) -> list[AuditEvent]:
    """Read back what `_persist` wrote, in the order the events occurred.

    Ordered by timestamp, not by the storage key: the key embeds `sequence`,
    which restarts at 1 in every process, so key order scrambles a ledger that
    spans restarts. R1's durable read path needs a real ordering for the same
    reason.
    """
    prefix = store.namespaced_key(AUDIT_NAMESPACE, AUDIT_VERSION, f"{run_id}:")
    keys = [k.decode() if isinstance(k, bytes) else k
            for k in await store.client.keys(f"{prefix}*")]
    events = []
    for key in keys:
        raw = await store.client.get(key)
        events.append(AuditEvent.model_validate(json.loads(raw)))
    return sorted(events, key=lambda e: (e.timestamp, e.sequence))


async def test_persisted_chain_from_multiple_processes_verifies(store):
    """A ledger read back from Redis spans processes and must still verify.

    This is the shape R1's durable read path will produce.
    """
    for _ in range(3):
        process = _logger(store)
        await _record(process, "runA")
        await _record(process, "runA")

    persisted = await _persisted_events(store, "runA")
    assert len(persisted) == 6

    intact, detail = _logger().verify_chain(persisted)
    assert intact, detail
    assert "3 segment(s)" in detail


async def test_persisted_chain_detects_tampering_after_reload(store):
    process = _logger(store)
    await _record(process, "runA")
    await _record(process, "runA")

    persisted = await _persisted_events(store, "runA")
    persisted[1].prompt_chars = 4242

    intact, detail = _logger().verify_chain(persisted)
    assert intact is False
    assert "modified after writing" in detail


# -- tamper detection: unchanged strictness --------------------------------


async def test_modified_payload_fails():
    log = _logger()
    await _record(log, "runA")
    await _record(log, "runA")
    log._events[1].secret_count = 99

    intact, detail = log.verify_chain()
    assert intact is False
    assert "modified after writing" in detail


async def test_modified_entry_hash_fails():
    log = _logger()
    await _record(log, "runA")
    log._events[0].entry_hash = "f" * 64

    assert log.verify_chain()[0] is False


async def test_modified_previous_hash_fails():
    log = _logger()
    await _record(log, "runA")
    await _record(log, "runA")
    log._events[1].previous_hash = "a" * 64

    intact, detail = log.verify_chain()
    assert intact is False
    # `previous_hash` is inside the hash payload, so this reads as modification.
    assert "modified after writing" in detail


async def test_modified_sequence_fails():
    log = _logger()
    await _record(log, "runA")
    await _record(log, "runA")
    log._events[1].sequence = 77

    assert log.verify_chain()[0] is False


async def test_forged_genesis_boundary_cannot_hide_a_break():
    """The escape hatch must not be forgeable.

    Rewriting `previous_hash` to GENESIS would look like a legitimate process
    start — but `previous_hash` is covered by the entry hash, so the forgery is
    caught before linkage is consulted.
    """
    log = _logger()
    await _record(log, "runA")
    await _record(log, "runA")
    log._events[1].previous_hash = GENESIS_HASH

    intact, detail = log.verify_chain()
    assert intact is False
    assert "modified after writing" in detail


async def test_forged_genesis_with_recomputed_hash_still_fails_in_complete_scope():
    """Even a fully re-hashed forgery cannot splice a chain in complete scope.

    An attacker who deletes an event and re-hashes the successor as a GENESIS
    start defeats per-event integrity. Complete scope still rejects it, because
    a mid-ledger event whose sequence did not restart is not a process start.
    """
    log = _logger()
    await _record(log, "runA")
    await _record(log, "runA")
    await _record(log, "runA")

    forged = log._events[2]
    forged.previous_hash = GENESIS_HASH
    forged.entry_hash = compute_entry_hash(forged)

    # Drop the middle event, as a deletion attack would.
    spliced = [log._events[0], forged]
    intact, detail = log.verify_chain(spliced)
    assert intact is False, detail


async def test_deletion_is_detected_in_complete_scope():
    log = _logger()
    for _ in range(4):
        await _record(log, "runA")

    without_middle = [log._events[0], log._events[2], log._events[3]]
    intact, detail = log.verify_chain(without_middle)
    assert intact is False
    assert "chain broken" in detail


async def test_adjacent_sequences_with_broken_linkage_fail_in_subset_scope():
    """Subset scope tolerates gaps, not breaks.

    Two events with consecutive sequence numbers were adjacent in the ledger,
    so nothing was filtered between them — a linkage mismatch there is real.
    """
    log = _logger()
    await _record(log, "runA")
    second = await _record(log, "runA")

    second.previous_hash = "b" * 64
    second.entry_hash = compute_entry_hash(second)

    intact, detail = log.verify_chain(list(log._events), scope="subset")
    assert intact is False
    assert "chain broken" in detail


# -- scope semantics -------------------------------------------------------


async def test_complete_scope_is_the_default():
    log = _logger()
    for run in ("runA", "runB", "runA"):
        await _record(log, run)

    filtered = [e for e in log._events if e.run_id == "runA"]
    # Without declaring the subset, a filtered view is still reported as broken.
    assert log.verify_chain(filtered)[0] is False
    assert log.verify_chain(filtered, scope="subset")[0] is True


async def test_global_summary_uses_complete_scope():
    log = _logger()
    for run in ("runA", "runB"):
        await _record(log, run)

    summary = log.summary()
    assert summary["chain_intact"] is True
    assert summary["events"] == 2


@pytest.mark.parametrize("scope", ["complete", "subset"])
async def test_integrity_is_enforced_in_every_scope(scope):
    log = _logger()
    await _record(log, "runA")
    await _record(log, "runA")
    log._events[1].pii_count = 5

    assert log.verify_chain(list(log._events), scope=scope)[0] is False
