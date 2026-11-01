"""Analytics endpoints for the Organizational Learning System.

All additive, all under `/api/learning`. The write endpoints record *outcomes*
and *reviews* — facts the platform cannot observe for itself, because only a
human knows whether a merged patch was later reverted. They do not change
policy, weights or thresholds.

Nothing returned here contains source, a prompt, a diff or an identity: the
learning models have no field that could hold them.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.api.deps import get_settings_dep, get_store
from backend.config import Settings
from backend.learning.knowledge_index import explain
from backend.models.learning import OutcomeStatus, ReviewDecision
from backend.services.learning_pipeline import get_learning_pipeline
from backend.state.redis_store import RedisStore

router = APIRouter(prefix="/api/learning", tags=["learning"])


def _pipeline(settings: Settings, store: RedisStore):
    return get_learning_pipeline(settings, store)


@router.get("/dashboard")
async def dashboard(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Growth, success rates, template reuse, maturity, evolution."""
    return _pipeline(settings, store).dashboard()


@router.get("/metrics")
async def metrics(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Counters only — the subset a monitoring system scrapes."""
    engine = _pipeline(settings, store).engine
    m = engine.metrics()
    return {
        "repairs_recorded": m.repairs_recorded,
        "repairs_succeeded": m.repairs_succeeded,
        "repairs_rejected": m.repairs_rejected,
        "success_rate": m.success_rate,
        "reviews_recorded": m.reviews_recorded,
        "templates_mined": m.templates_mined,
        "template_reuse_rate": m.template_reuse_rate,
        "patterns_identified": m.patterns_identified,
        "repositories_known": m.repositories_known,
        "frameworks_covered": m.frameworks_covered,
        "organization_maturity": m.organization_maturity,
        "average_update_ms": m.average_update_ms,
    }


@router.get("/repositories/{repository_id}")
async def repository_profile(
    repository_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """What the platform has learned about one repository."""
    engine = _pipeline(settings, store).engine
    profile = engine.state.repository_profiles.get(repository_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"no profile learned for '{repository_id}' yet")

    return {
        "repository_id": profile.repository_id,
        "repository_name": profile.repository_name,
        "maturity": profile.maturity,
        "repairs_recorded": profile.repairs_recorded,
        "repairs_succeeded": profile.repairs_succeeded,
        "repair_success_rate": profile.repair_success_rate,
        "common_bug_categories": profile.common_bug_categories,
        "style": {
            **profile.style.model_dump(exclude={"observations"}),
            "directives": profile.style.prompt_directives(),
            "observations": [o.describe() for o in profile.style.observations],
        },
        "framework": {
            "primary": profile.framework.primary_framework,
            "confidence": profile.framework.confidence,
            "detected": profile.framework.frameworks,
            "detected_from": profile.framework.detected_from,
            "conventions": [
                {"aspect": c.aspect, "convention": c.convention, "evidence": c.evidence}
                for c in profile.framework.conventions
            ],
        },
    }


@router.get("/organization")
async def organization_profile(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Preferences aggregated across every known repository."""
    engine = _pipeline(settings, store).engine
    return engine.state.organization.summary(engine.organization_profile())


@router.get("/templates")
async def templates(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    bug_category: str | None = None,
) -> list[dict]:
    """Mined repair templates with their honest track record."""
    mined = _pipeline(settings, store).engine.templates()
    if bug_category:
        mined = [t for t in mined if t.bug_category == bug_category]
    return [
        {
            "template_id": t.template_id,
            "bug_category": t.bug_category,
            "title": t.title,
            "approach": t.approach,
            "guardrails": t.guardrails,
            "validation_hints": t.validation_hints,
            "support": t.support,
            "successes": t.successes,
            "failures": t.failures,
            "success_rate": t.success_rate,
            "confidence": t.confidence,
            "repositories": len(t.repositories),
            "frameworks": t.frameworks,
        }
        for t in mined
    ]


@router.get("/patterns")
async def patterns(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> list[dict]:
    """Recurring defect shapes, and how often a repair failed to hold."""
    return [
        {
            "pattern_id": p.pattern_id,
            "category": p.category,
            "occurrences": p.occurrences,
            "repositories": p.repositories,
            "repaired": p.repaired,
            "recurred": p.recurred,
            "recurrence_rate": p.recurrence_rate,
        }
        for p in _pipeline(settings, store).engine.patterns()
    ]


@router.get("/context/{repository_id}")
async def learned_context(
    repository_id: str,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    bug_category: str = "",
) -> dict:
    """Exactly what would be added to a patch prompt, and why each line."""
    pipeline = _pipeline(settings, store)
    context = pipeline.context_for(repository_id, bug_category)
    return {**context, "explanations": explain(context), "block": pipeline.directive_block(repository_id, bug_category)}


class OutcomeBody(BaseModel):
    status: OutcomeStatus
    detail: str = ""
    actor: str = Field("api", description="Who reported this outcome")


@router.post("/repairs/{repair_id}/outcome")
async def record_outcome(
    repair_id: str,
    body: OutcomeBody,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Record what happened to a repair after it was suggested."""
    record = _pipeline(settings, store).record_outcome(
        repair_id, body.status, body.detail, body.actor
    )
    if record is None:
        raise HTTPException(status_code=503, detail="learning is disabled")
    return {"repair_id": repair_id, "status": record.status, "recorded_at": record.recorded_at.isoformat()}


class ReviewBody(BaseModel):
    decision: ReviewDecision
    reason: str = ""
    reviewer: str = ""


@router.post("/repairs/{repair_id}/review")
async def record_review(
    repair_id: str,
    body: ReviewBody,
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Record a human verdict, categorising its reason deterministically."""
    review = _pipeline(settings, store).record_review(
        repair_id, body.decision, body.reason, body.reviewer
    )
    if review is None:
        raise HTTPException(status_code=503, detail="learning is disabled")
    return {
        "repair_id": repair_id,
        "decision": review.decision,
        "categories": review.categories,
        "recorded_at": review.recorded_at.isoformat(),
    }


@router.get("/repairs")
async def repairs(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
    repository_id: str | None = None,
    limit: int = Query(20, ge=1, le=200),
) -> list[dict]:
    """Recent repair records. Metadata only — no source, no diffs, no prompts."""
    memory = _pipeline(settings, store).engine.state.repairs
    return [
        r.model_dump(mode="json")
        for r in memory.recent(limit=limit, repository_id=repository_id)
    ]


@router.get("/reviews")
async def reviews(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """What reviewers keep asking for — the platform's improvement backlog."""
    return _pipeline(settings, store).engine.state.reviews.summary()


@router.get("/outcomes")
async def outcomes(
    store: Annotated[RedisStore, Depends(get_store)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> dict:
    """Outcome distribution, success rate and rollback rate."""
    return _pipeline(settings, store).engine.state.outcomes.summary()
