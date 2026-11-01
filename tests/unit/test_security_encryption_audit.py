"""Encryption at rest and the tamper-evident audit trail."""

import base64

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.config import Settings
from backend.models.security import AuditEvent, EncryptedBlob, SanitizationReport, SecretFinding
from backend.security.audit_logger import (
    GENESIS_HASH,
    AuditLogger,
    compute_entry_hash,
    content_hash,
)
from backend.security.encryption import (
    ALGORITHM,
    KEY_BYTES,
    EncryptionError,
    EncryptionService,
    Keyring,
    derive_key,
    generate_key,
    key_fingerprint,
)
from backend.state.redis_store import RedisStore


@pytest.fixture
def service() -> EncryptionService:
    keyring = Keyring()
    keyring.add(derive_key("test-material"), key_id="v1")
    return EncryptionService(keyring)


@pytest_asyncio.fixture
async def store():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield RedisStore(client, Settings(stub_mode=True))
    await client.aclose()


# ===================================================== encryption


def test_derive_key_length():
    assert len(derive_key("material")) == KEY_BYTES


def test_derive_key_is_deterministic():
    assert derive_key("material") == derive_key("material")


def test_different_material_yields_different_keys():
    assert derive_key("a") != derive_key("b")


def test_different_salt_yields_different_keys():
    assert derive_key("m", "salt-a") != derive_key("m", "salt-b")


def test_empty_material_is_refused():
    with pytest.raises(EncryptionError):
        derive_key("")


def test_generate_key_is_random_and_correctly_sized():
    first, second = generate_key(), generate_key()
    assert first != second
    assert len(base64.b64decode(first)) == KEY_BYTES


def test_fingerprint_is_stable_and_not_the_key():
    key = derive_key("material")
    assert key_fingerprint(key) == key_fingerprint(key)
    assert key_fingerprint(key) not in key.hex()


def test_round_trip(service):
    blob = service.encrypt("secret payload")
    assert service.decrypt(blob) == "secret payload"


def test_ciphertext_does_not_contain_plaintext(service):
    blob = service.encrypt("hunter2-very-secret")
    assert "hunter2" not in blob.ciphertext
    assert "hunter2" not in blob.model_dump_json()


def test_nonce_differs_per_encryption(service):
    assert service.encrypt("same").nonce != service.encrypt("same").nonce


def test_same_plaintext_yields_different_ciphertext(service):
    assert service.encrypt("same").ciphertext != service.encrypt("same").ciphertext


def test_blob_records_algorithm_and_key_id(service):
    blob = service.encrypt("x")
    assert blob.algorithm == ALGORITHM
    assert blob.key_id == "v1"


def test_tampered_ciphertext_is_detected(service):
    """GCM is authenticated: modification fails rather than yielding garbage."""
    blob = service.encrypt("payload")
    raw = bytearray(base64.b64decode(blob.ciphertext))
    raw[0] ^= 0xFF
    blob.ciphertext = base64.b64encode(bytes(raw)).decode()

    with pytest.raises(EncryptionError, match="authentication failed"):
        service.decrypt(blob)


def test_wrong_key_fails(service):
    blob = service.encrypt("payload")
    other = EncryptionService(Keyring())
    other.keyring.add(derive_key("different"), key_id="v1")
    with pytest.raises(EncryptionError):
        other.decrypt(blob)


def test_associated_data_is_authenticated(service):
    blob = service.encrypt("payload", associated_data="run-1")
    assert service.decrypt(blob, "run-1") == "payload"
    with pytest.raises(EncryptionError):
        service.decrypt(blob, "run-2")


def test_malformed_envelope_is_refused(service):
    with pytest.raises(EncryptionError, match="malformed"):
        service.decrypt(EncryptedBlob(key_id="v1", nonce="!!!", ciphertext="!!!"))


def test_unknown_key_id_is_refused(service):
    with pytest.raises(EncryptionError, match="no key available"):
        service.decrypt(EncryptedBlob(key_id="absent", nonce="AA==", ciphertext="AA=="))


def test_json_round_trip(service):
    payload = {"a": 1, "b": ["x", "y"]}
    assert service.decrypt_json(service.encrypt_json(payload)) == payload


# -- key rotation ----------------------------------------------------------


def test_rotation_promotes_the_new_key(service):
    service.rotate("new-material", "v2")
    assert service.keyring.active_key_id == "v2"
    assert service.encrypt("x").key_id == "v2"


def test_data_encrypted_before_rotation_stays_readable(service):
    old = service.encrypt("written under v1")
    service.rotate("new-material", "v2")
    assert service.decrypt(old) == "written under v1"


def test_keyring_lists_every_version(service):
    service.rotate("new-material", "v2")
    assert service.keyring.key_ids == ["v1", "v2"]


def test_keyring_rejects_a_wrong_length_key():
    with pytest.raises(EncryptionError):
        Keyring().add(b"too-short")


def test_first_key_added_becomes_active_even_when_not_requested():
    keyring = Keyring()
    keyring.add(derive_key("m"), key_id="k1", make_active=False)
    assert keyring.active_key_id == "k1"


# -- configuration ---------------------------------------------------------


def test_disabled_without_a_key():
    service = EncryptionService.from_settings(Settings(encryption_key=""))
    assert not service.enabled
    assert not service.status()["enabled"]


def test_encrypt_raises_when_disabled():
    with pytest.raises(EncryptionError, match="not configured"):
        EncryptionService(Keyring()).encrypt("x")


def test_encrypt_if_enabled_is_honest_when_disabled():
    """A silent no-op reporting success would be worse than no encryption."""
    service = EncryptionService(Keyring())
    value, encrypted = service.encrypt_if_enabled("payload")
    assert value == "payload"
    assert encrypted is False


def test_encrypt_if_enabled_when_configured(service):
    value, encrypted = service.encrypt_if_enabled("payload")
    assert encrypted is True
    assert "payload" not in value


def test_decrypt_if_encrypted_round_trip(service):
    value, _ = service.encrypt_if_enabled("payload", "ctx")
    assert service.decrypt_if_encrypted(value, "ctx") == "payload"


def test_decrypt_if_encrypted_passes_plaintext_through(service):
    assert service.decrypt_if_encrypted("plain text") == "plain text"


def test_from_settings_loads_previous_keys():
    settings = Settings(
        encryption_key="current", encryption_key_version="v2",
        encryption_previous_keys="v1:old-material",
    )
    service = EncryptionService.from_settings(settings)
    assert service.keyring.active_key_id == "v2"
    assert "v1" in service.keyring.key_ids


def test_status_never_exposes_key_material(service):
    assert "test-material" not in str(service.status())


# ===================================================== audit


def report(secrets: int = 0, pii: int = 0) -> SanitizationReport:
    return SanitizationReport(
        secrets=[SecretFinding(category="password", detector="d") for _ in range(secrets)],
        pii=[],
    )


def test_content_hash_is_stable():
    assert content_hash("abc") == content_hash("abc")
    assert content_hash("abc") != content_hash("abd")


def test_first_event_links_to_genesis():
    logger = AuditLogger()
    assert logger.build_event(prompt="p").previous_hash == GENESIS_HASH


def test_events_are_chained():
    logger = AuditLogger()
    first = logger.build_event(prompt="a")
    second = logger.build_event(prompt="b")
    assert second.previous_hash == first.entry_hash


def test_sequence_increments():
    logger = AuditLogger()
    assert [logger.build_event(prompt=str(i)).sequence for i in range(3)] == [1, 2, 3]


def test_prompt_is_hashed_not_stored():
    """An audit log is read by more people than the repository is."""
    event = AuditLogger().build_event(prompt="secret prompt content")
    assert event.prompt_hash == content_hash("secret prompt content")
    assert event.raw_prompt is None
    assert "secret prompt content" not in event.model_dump_json()


def test_raw_prompt_stored_only_when_explicitly_enabled():
    logger = AuditLogger(settings=Settings(audit_store_raw_prompts=True))
    assert logger.build_event(prompt="content").raw_prompt == "content"


def test_response_is_hashed():
    event = AuditLogger().build_event(prompt="p", response="r")
    assert event.response_hash == content_hash("r")


def test_no_response_yields_no_hash():
    assert AuditLogger().build_event(prompt="p").response_hash == ""


def test_sanitization_counts_are_recorded():
    event = AuditLogger().build_event(prompt="p", sanitization=report(secrets=3))
    assert event.secret_count == 3


@pytest.mark.asyncio
async def test_recording_appends(store):
    logger = AuditLogger(store=store, settings=Settings())
    await logger.record(logger.build_event(prompt="a", run_id="r1"))
    await logger.record(logger.build_event(prompt="b", run_id="r1"))
    assert len(logger.events()) == 2


@pytest.mark.asyncio
async def test_events_filter_by_run(store):
    logger = AuditLogger(store=store, settings=Settings())
    await logger.record(logger.build_event(prompt="a", run_id="r1"))
    await logger.record(logger.build_event(prompt="b", run_id="r2"))
    assert len(logger.events("r1")) == 1


@pytest.mark.asyncio
async def test_persisted_event_is_encrypted_when_configured(store):
    encryption = EncryptionService(Keyring())
    encryption.keyring.add(derive_key("m"), key_id="v1")
    logger = AuditLogger(store=store, settings=Settings(), encryption=encryption)

    event = logger.build_event(prompt="p", run_id="r1")
    await logger.record(event)

    raw = await store.get_cached("security_audit", "v1", f"r1:{event.sequence:08d}:{event.event_id}")
    assert "r1" not in raw or "ciphertext" in raw


@pytest.mark.asyncio
async def test_storage_failure_does_not_fail_the_run():
    class Broken:
        async def set_cached(self, *a, **k):
            raise RuntimeError("redis down")

    logger = AuditLogger(store=Broken(), settings=Settings())
    await logger.record(logger.build_event(prompt="p"))  # must not raise


# -- chain integrity -------------------------------------------------------


def test_intact_chain_verifies():
    logger = AuditLogger()
    for i in range(5):
        logger._events.append(logger.build_event(prompt=str(i)))
    intact, detail = logger.verify_chain()
    assert intact
    assert "5 event(s)" in detail


def test_modified_event_breaks_verification():
    """Recomputation, not trust — the check an auditor actually runs."""
    logger = AuditLogger()
    for i in range(3):
        logger._events.append(logger.build_event(prompt=str(i)))

    logger._events[1].secret_count = 99
    intact, detail = logger.verify_chain()
    assert not intact
    assert "modified after writing" in detail


def test_deleted_event_breaks_the_chain():
    logger = AuditLogger()
    for i in range(3):
        logger._events.append(logger.build_event(prompt=str(i)))

    del logger._events[1]
    intact, detail = logger.verify_chain()
    assert not intact
    assert "chain broken" in detail


def test_empty_chain_verifies():
    assert AuditLogger().verify_chain() == (True, "no events to verify")


def test_entry_hash_covers_the_previous_hash():
    event = AuditEvent(event_id="e", previous_hash="abc")
    first = compute_entry_hash(event)
    event.previous_hash = "def"
    assert compute_entry_hash(event) != first


# -- reporting -------------------------------------------------------------


def test_timeline_contains_no_prompt_content():
    logger = AuditLogger()
    logger._events.append(logger.build_event(prompt="highly secret prompt", provider="anthropic"))
    assert "highly secret prompt" not in str(logger.timeline())


def test_timeline_reports_decisions():
    logger = AuditLogger()
    logger._events.append(logger.build_event(prompt="p", provider="anthropic", decision="reject"))
    assert logger.timeline()[0]["decision"] == "reject"


def test_summary_aggregates():
    logger = AuditLogger()
    for provider in ("anthropic", "anthropic", "ollama"):
        logger._events.append(
            logger.build_event(prompt="p", provider=provider, policy="PRIVATE", estimated_cost_usd=0.001)
        )
    summary = logger.summary()
    assert summary["events"] == 3
    assert summary["providers"] == {"anthropic": 2, "ollama": 1}
    assert summary["estimated_cost_usd"] == pytest.approx(0.003)
    assert summary["chain_intact"]


def test_summary_reports_raw_prompt_setting():
    assert AuditLogger(settings=Settings(audit_store_raw_prompts=True)).summary()["raw_prompts_stored"]


def test_violations_are_recorded_from_decisions():
    from backend.models.security import PolicyDecision, PolicyViolation

    decision = PolicyDecision(
        decision="reject", policy="PRIVATE", classification="PRIVATE",
        violations=[PolicyViolation(rule="egress_forbidden", detail="d")],
    )
    event = AuditLogger().build_event(prompt="p", policy_decision=decision)
    assert "policy:egress_forbidden" in event.violations
