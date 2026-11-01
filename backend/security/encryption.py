"""AES-256-GCM encryption for data this platform stores at rest.

Covers repair memory, audit logs, cached context packages and knowledge-graph
snapshots — everything that outlives a run and therefore outlives the run's
access controls.

GCM rather than CBC because it is authenticated: a tampered ciphertext fails to
decrypt rather than yielding plausible garbage. For an audit log that property is
the point, not a bonus.

**Key material is never persisted by this module.** Keys arrive from settings or
the environment; the keyring holds them in memory and writes only `key_id` into
the ciphertext envelope. Rotation adds a new key and keeps old ones readable, so
re-encrypting historical data is a background task rather than a precondition for
rotating.

When no key is configured, `EncryptionService.enabled` is False and the service
passes data through unchanged rather than pretending to protect it. A silent
no-op that reports success would be worse than no encryption at all.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.models.security import EncryptedBlob

# AES-256. GCM's standard nonce length; 96 bits is what the mode is specified for.
KEY_BYTES = 32
NONCE_BYTES = 12

ALGORITHM = "AES-256-GCM"


class EncryptionError(RuntimeError):
    """Raised when decryption fails — wrong key, or tampered ciphertext."""


def derive_key(material: str, salt: str = "proofix-v1") -> bytes:
    """Derive a 32-byte key from configured material.

    Scrypt rather than a bare hash: configured key material is often a passphrase
    with far less entropy than 256 bits, and a fast hash would leave it open to
    offline brute force if a ciphertext leaked.
    """
    if not material:
        raise EncryptionError("no key material provided")
    return hashlib.scrypt(
        material.encode("utf-8"),
        salt=salt.encode("utf-8"),
        n=2**14,
        r=8,
        p=1,
        dklen=KEY_BYTES,
    )


def generate_key() -> str:
    """A fresh random key, base64-encoded for configuration."""
    return base64.b64encode(os.urandom(KEY_BYTES)).decode("ascii")


def key_fingerprint(key: bytes) -> str:
    """Short, non-reversible identifier for a key. Safe to store and log."""
    return hashlib.sha256(key).hexdigest()[:12]


@dataclass
class Keyring:
    """Active key plus retired keys that must remain readable."""

    keys: dict[str, bytes] = field(default_factory=dict)
    active_key_id: str = ""

    def add(self, key: bytes, key_id: str = "", make_active: bool = True) -> str:
        if len(key) != KEY_BYTES:
            raise EncryptionError(f"key must be {KEY_BYTES} bytes, got {len(key)}")
        identifier = key_id or key_fingerprint(key)
        self.keys[identifier] = key
        if make_active or not self.active_key_id:
            self.active_key_id = identifier
        return identifier

    def get(self, key_id: str) -> bytes | None:
        return self.keys.get(key_id)

    @property
    def active(self) -> bytes | None:
        return self.keys.get(self.active_key_id)

    def rotate(self, new_key: bytes, key_id: str = "") -> str:
        """Promote a new key. Previous keys stay readable for decryption."""
        return self.add(new_key, key_id, make_active=True)

    @property
    def key_ids(self) -> list[str]:
        return sorted(self.keys)


class EncryptionService:
    """Envelope encryption over a keyring. Degrades honestly when unconfigured."""

    def __init__(self, keyring: Keyring | None = None, version: str = "v1"):
        self.keyring = keyring or Keyring()
        self.version = version

    # -- construction ----------------------------------------------------

    @classmethod
    def from_settings(cls, settings) -> EncryptionService:
        """Build from configuration. No key configured ⇒ disabled, not fake."""
        service = cls(version=getattr(settings, "encryption_key_version", "v1"))
        material = getattr(settings, "encryption_key", "") or ""
        if material:
            service.keyring.add(derive_key(material), key_id=service.version)

        # Retired keys, so data written before a rotation stays readable.
        previous = getattr(settings, "encryption_previous_keys", "") or ""
        for entry in (e.strip() for e in previous.split(",") if e.strip()):
            key_id, _, value = entry.partition(":")
            if value:
                service.keyring.add(derive_key(value), key_id=key_id, make_active=False)

        return service

    @property
    def enabled(self) -> bool:
        return self.keyring.active is not None

    # -- operations ------------------------------------------------------

    def encrypt(self, plaintext: str, associated_data: str = "") -> EncryptedBlob:
        """Encrypt a string. Raises when no key is configured.

        `associated_data` is authenticated but not encrypted — use it to bind a
        ciphertext to its context (a run id, a record type), so a blob cannot be
        moved from one record to another undetected.
        """
        key = self.keyring.active
        if key is None:
            raise EncryptionError("encryption is not configured")

        nonce = os.urandom(NONCE_BYTES)
        aad = associated_data.encode("utf-8") if associated_data else None
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), aad)

        return EncryptedBlob(
            version=self.version,
            key_id=self.keyring.active_key_id,
            nonce=base64.b64encode(nonce).decode("ascii"),
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
            algorithm=ALGORITHM,
        )

    def decrypt(self, blob: EncryptedBlob, associated_data: str = "") -> str:
        """Decrypt a blob using the key it names."""
        key = self.keyring.get(blob.key_id)
        if key is None:
            raise EncryptionError(f"no key available for key_id '{blob.key_id}'")

        try:
            # `validate=True`: without it b64decode silently discards invalid
            # characters, so a malformed envelope produces a wrong-length nonce
            # that AES-GCM rejects with a bare ValueError instead of ours.
            nonce = base64.b64decode(blob.nonce, validate=True)
            ciphertext = base64.b64decode(blob.ciphertext, validate=True)
        except Exception as exc:  # noqa: BLE001 — malformed envelope
            raise EncryptionError("malformed ciphertext envelope") from exc

        if len(nonce) != NONCE_BYTES:
            raise EncryptionError(
                f"malformed ciphertext envelope: nonce is {len(nonce)} bytes, expected {NONCE_BYTES}"
            )

        aad = associated_data.encode("utf-8") if associated_data else None
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, aad).decode("utf-8")
        except InvalidTag as exc:
            raise EncryptionError(
                "authentication failed — wrong key or the ciphertext was modified"
            ) from exc
        except ValueError as exc:
            raise EncryptionError("malformed ciphertext envelope") from exc

    # -- convenience -----------------------------------------------------

    def encrypt_json(self, payload: dict | list, associated_data: str = "") -> EncryptedBlob:
        return self.encrypt(json.dumps(payload, sort_keys=True, default=str), associated_data)

    def decrypt_json(self, blob: EncryptedBlob, associated_data: str = "") -> dict | list:
        return json.loads(self.decrypt(blob, associated_data))

    def encrypt_if_enabled(self, plaintext: str, associated_data: str = "") -> tuple[str, bool]:
        """Encrypt when configured, otherwise return the plaintext and say so.

        The boolean is the honesty: a caller can record whether the value it
        stored was actually protected, instead of assuming it was.
        """
        if not self.enabled:
            return plaintext, False
        return self.encrypt(plaintext, associated_data).model_dump_json(), True

    def decrypt_if_encrypted(self, value: str, associated_data: str = "") -> str:
        """Inverse of `encrypt_if_enabled`. Plaintext passes through."""
        if not value or not value.lstrip().startswith("{"):
            return value
        try:
            blob = EncryptedBlob.model_validate_json(value)
        except Exception:  # noqa: BLE001 — not an envelope; it is plaintext JSON
            return value
        if not blob.ciphertext:
            return value
        return self.decrypt(blob, associated_data)

    def rotate(self, new_material: str, new_key_id: str) -> str:
        """Rotate to new key material, keeping the old key for decryption."""
        return self.keyring.rotate(derive_key(new_material), new_key_id)

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "algorithm": ALGORITHM,
            "active_key_id": self.keyring.active_key_id,
            "key_ids": self.keyring.key_ids,
            "version": self.version,
        }
