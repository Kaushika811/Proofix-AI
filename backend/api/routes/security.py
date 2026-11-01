"""Security dashboard and compliance endpoints.

All additive, all under `/api/security`, all read-only. Nothing here can change
a policy or approve a request — configuration is the control surface, and an
endpoint that could relax a policy at runtime would undermine every guarantee
the layer offers.

**Nothing returned by these endpoints contains a secret, a personal identifier,
or a prompt.** The audit trail stores hashes; the timeline surfaces counts and
categories. That is deliberate: a security dashboard is usually more widely
readable than the repository it describes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import get_settings_dep, get_store
from backend.config import Settings
from backend.security.compliance_engine import SUPPORTED_FRAMEWORKS
from backend.security.policy_engine import BUILTIN_POLICIES
from backend.services.security_pipeline import get_security_pipeline
from backend.state.redis_store import RedisStore

router = APIRouter(prefix="/api/security", tags=["security"])


def _pipeline(settings: Settings, store: RedisStore):
    return get_security_pipeline(settings, store)


@router.get("/dashboard")
async def dashboard(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Every metric in one payload: detections, policies, routing, cost, compliance."""
    return _pipeline(settings, store).dashboard()


@router.get("/metrics")
async def metrics(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Counters only — the subset a monitoring system scrapes."""
    pipeline = _pipeline(settings, store)
    m = pipeline.metrics
    return {
        "secrets_detected": m.secrets_detected,
        "pii_detected": m.pii_detected,
        "contexts_sanitized": m.contexts_sanitized,
        "contexts_clean": m.contexts_clean,
        "policies_applied": dict(sorted(m.policies_applied.items())),
        "policy_violations": m.policy_violations,
        "llm_calls": m.llm_calls,
        "provider_usage": dict(sorted(m.provider_usage.items())),
        "estimated_cost_usd": round(m.estimated_cost_usd, 6),
        "average_prompt_chars": m.average_prompt_chars,
        "rejected_requests": m.rejected_requests,
        "rejection_rate": m.rejection_rate,
        "secret_categories": dict(sorted(m.secret_categories.items())),
        "pii_categories": dict(sorted(m.pii_categories.items())),
    }


@router.get("/policies")
async def policies() -> dict:
    """The active policy definitions. Read-only by design."""
    return {
        name: {
            "classification": policy.classification,
            "allowed_providers": policy.allowed_providers,
            "allowed_models": policy.allowed_models or ["any"],
            "max_tokens": policy.max_tokens,
            "max_context_chars": policy.max_context_chars,
            "max_files": policy.max_files,
            "allowed_file_types": policy.allowed_file_types or ["any"],
            "allowed_languages": policy.allowed_languages or ["any"],
            "require_sanitization": policy.require_sanitization,
            "egress_permitted": policy.egress_permitted,
            "allow_secrets": policy.allow_secrets,
            "allow_pii": policy.allow_pii,
        }
        for name, policy in BUILTIN_POLICIES.items()
    }


@router.get("/routing")
async def routing(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Where each classification would route right now, and why."""
    return _pipeline(settings, store).router.routing_matrix(BUILTIN_POLICIES)


@router.get("/timeline")
async def timeline(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    run_id: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[dict]:
    """Security decision timeline. Hashes and counts, never content."""
    return _pipeline(settings, store).audit.timeline(run_id, limit)


@router.get("/audit/summary")
async def audit_summary(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    run_id: str | None = None,
) -> dict:
    """Aggregate audit state, including hash-chain integrity."""
    return _pipeline(settings, store).audit.summary(run_id)


@router.get("/audit/verify")
async def verify_chain(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Recompute the audit hash chain rather than trusting what it stored."""
    intact, detail = _pipeline(settings, store).audit.verify_chain()
    return {"intact": intact, "detail": detail}


@router.get("/compliance")
async def compliance_summary(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """One-line status per framework."""
    return _pipeline(settings, store).compliance().summary()


@router.get("/compliance/{framework}")
async def compliance_report(
    framework: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Full report: which controls passed, which failed, evidence, recommendations."""
    normalized = framework.upper().replace("-", "_").replace("PCI_DSS", "PCI-DSS")
    if normalized not in SUPPORTED_FRAMEWORKS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown framework '{framework}'; supported: {', '.join(SUPPORTED_FRAMEWORKS)}",
        )

    report = _pipeline(settings, store).compliance().report(normalized)  # type: ignore[arg-type]
    return {
        "framework": report.framework,
        "generated_at": report.generated_at.isoformat(),
        "compliant": report.compliant,
        "score": report.score,
        "events_examined": report.events_examined,
        "controls": [
            {
                "control_id": c.control_id,
                "title": c.title,
                "status": c.status,
                "evidence": c.evidence,
                "recommendation": c.recommendation,
            }
            for c in report.controls
        ],
    }


@router.get("/encryption")
async def encryption_status(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Whether data at rest is encrypted, and under which key version.

    Key identifiers are fingerprints, never key material.
    """
    return _pipeline(settings, store).encryption.status()


@router.get("/frameworks")
async def frameworks() -> dict:
    """Discoverability: which compliance frameworks are supported."""
    return {"frameworks": list(SUPPORTED_FRAMEWORKS)}
