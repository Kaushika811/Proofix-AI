"""Redis-backed cache for built context packages.

The retry loop re-enters A7 up to `max_retries` times. When only the validation
failure changed, ranking and extraction produce an identical package, so the AST
pass is pure waste. The cache key pins every input that can change the result:

    repo hash | SIG hash | target file | target function | root cause id | attempt

`repo_hash` already folds in HEAD plus the worktree diff hash (see `sig_cache`),
so a patch written by A7 invalidates the entry automatically.

Only the post-mask package is ever stored — a cache hit can never resurrect an
unmasked secret.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from backend.models.context import ContextPackage

CACHE_NAMESPACE = "context_cache"
CACHE_VERSION = "v1"


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def sig_hash(sig: dict | None) -> str:
    """Stable hash of the SIG's file set and source roots."""
    if not sig:
        return "no-sig"
    files = sorted((sig.get("files") or {}).keys())
    roots = sorted(sig.get("source_roots") or [])
    return _digest(json.dumps(files, sort_keys=True), json.dumps(roots, sort_keys=True))[:16]


def root_cause_id(root_cause: dict | None) -> str:
    """Identity of the root cause: its summary plus its citation anchors."""
    if not root_cause:
        return "no-root-cause"
    citations = sorted(
        f"{c.get('file', '')}:{c.get('line', 0)}:{bool(c.get('verified'))}"
        for c in (root_cause.get("citations") or [])
    )
    return _digest(
        str(root_cause.get("root_cause") or root_cause.get("summary") or ""),
        json.dumps(citations),
    )[:16]


def build_cache_key(
    *,
    repo_hash: str,
    sig_digest: str,
    target_file: str,
    target_function: str | None,
    root_cause_digest: str,
    attempt: int = 0,
) -> str:
    return _digest(
        CACHE_VERSION,
        repo_hash or "no-repo-hash",
        sig_digest,
        target_file,
        target_function or "no-target-function",
        root_cause_digest,
        str(attempt),
    )


@dataclass
class CacheMetrics:
    """Counters for one process. Exposed on the agent for observability."""

    hits: int = 0
    misses: int = 0
    build_times_ms: list[int] = field(default_factory=list)

    def record_hit(self) -> None:
        self.hits += 1

    def record_miss(self, build_time_ms: int) -> None:
        self.misses += 1
        self.build_times_ms.append(build_time_ms)

    @property
    def total(self) -> int:
        return self.hits + self.misses

    @property
    def hit_rate(self) -> float:
        return round(self.hits / self.total, 4) if self.total else 0.0

    @property
    def average_build_time_ms(self) -> int:
        if not self.build_times_ms:
            return 0
        return int(sum(self.build_times_ms) / len(self.build_times_ms))

    def to_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": self.hit_rate,
            "average_build_time_ms": self.average_build_time_ms,
        }


class ContextCache:
    """Thin async wrapper over the run store's generic JSON access."""

    def __init__(self, store, ttl_seconds: int | None = None):
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.metrics = CacheMetrics()

    @staticmethod
    def suffix(cache_key: str) -> str:
        return f"{CACHE_NAMESPACE}:{cache_key}"

    async def get(self, run_id: str, cache_key: str) -> ContextPackage | None:
        if not cache_key:
            return None
        try:
            raw = await self.store.get_json(run_id, self.suffix(cache_key))
        except Exception:  # noqa: BLE001 — a cache fault must never fail a run
            return None
        if not raw:
            return None
        try:
            package = ContextPackage.model_validate(raw)
        except Exception:  # noqa: BLE001 — a stale shape is a miss, not an error
            return None
        self.metrics.record_hit()
        package.metrics.cache_hit = True
        return package

    async def set(self, run_id: str, cache_key: str, package: ContextPackage) -> None:
        if not cache_key:
            return
        self.metrics.record_miss(package.metrics.build_time_ms)
        try:
            await self.store.set_json(run_id, self.suffix(cache_key), package.to_storage_dict())
        except Exception:  # noqa: BLE001 — best effort
            return
