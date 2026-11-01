"""Analytics over the learning system.

Every figure is a count or a ratio of counts. `learning_growth` is the one that
matters operationally: it reports whether the platform is still learning or has
plateaued, by comparing recent observations against the accumulated total.

A plateau is not a failure — a repository whose style is fully characterised has
nothing left to teach about style. The metric exists so that a plateau is
visible and can be interpreted, rather than mistaken for the system working.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from backend.models.learning import (
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    BugPattern,
    LearningMetrics,
    OrganizationProfile,
    RepairKnowledge,
    RepairTemplate,
    RepositoryProfile,
)

# Window for "recent" in growth calculations.
GROWTH_WINDOW_DAYS = 30


def build_metrics(
    records: list[RepairKnowledge],
    templates: list[RepairTemplate],
    patterns: list[BugPattern],
    repository_profiles: dict[str, RepositoryProfile],
    organization_profile: OrganizationProfile,
    reviews_recorded: int = 0,
    template_reuses: int = 0,
    learning_updates: int = 0,
    total_update_ms: int = 0,
) -> LearningMetrics:
    """Assemble the analytics surface from already-computed learning state."""
    succeeded = sum(1 for r in records if r.outcome in POSITIVE_OUTCOMES or (r.outcome == "suggested" and r.succeeded))
    rejected = sum(1 for r in records if r.outcome in NEGATIVE_OUTCOMES)

    return LearningMetrics(
        repairs_recorded=len(records),
        repairs_succeeded=succeeded,
        repairs_rejected=rejected,
        reviews_recorded=reviews_recorded,
        templates_mined=len(templates),
        template_reuses=template_reuses,
        patterns_identified=len(patterns),
        repositories_known=len(repository_profiles),
        frameworks_covered=dict(
            sorted(Counter(
                p.framework.primary_framework for p in repository_profiles.values()
                if p.framework.primary_framework != "unknown"
            ).items())
        ),
        bug_category_frequency=dict(
            sorted(Counter(r.bug_category for r in records).items(), key=lambda kv: (-kv[1], kv[0]))
        ),
        repository_maturity={rid: p.maturity for rid, p in sorted(repository_profiles.items())},
        organization_maturity=organization_profile.maturity,
        style_confidence={rid: p.style.confidence for rid, p in sorted(repository_profiles.items())},
        learning_updates=learning_updates,
        total_update_ms=total_update_ms,
    )


def learning_growth(records: list[RepairKnowledge], now: datetime | None = None) -> dict:
    """Is the platform still learning, or has it plateaued?

    Compares observations inside the recent window against the lifetime total.
    A high rate early is expected; a rate near zero later means either that
    nothing is being repaired, or that everything worth learning has been.
    """
    reference = now or datetime.utcnow()
    cutoff = reference - timedelta(days=GROWTH_WINDOW_DAYS)

    recent = [r for r in records if r.recorded_at >= cutoff]
    categories_all = {r.bug_category for r in records}
    categories_recent = {r.bug_category for r in recent}
    new_categories = categories_recent - {r.bug_category for r in records if r.recorded_at < cutoff}

    return {
        "window_days": GROWTH_WINDOW_DAYS,
        "total_repairs": len(records),
        "recent_repairs": len(recent),
        "growth_rate": round(len(recent) / len(records), 4) if records else 0.0,
        "categories_known": len(categories_all),
        "categories_seen_recently": len(categories_recent),
        "new_categories": sorted(new_categories),
        "plateaued": bool(records) and not new_categories and len(recent) < max(1, len(records) // 10),
    }


def repair_evolution(records: list[RepairKnowledge], buckets: int = 5) -> list[dict]:
    """How repair quality has changed over time, in equal-sized buckets.

    Equal *count* buckets rather than equal time spans: repairs arrive
    irregularly, and time buckets would produce empty periods that read as
    quality collapses.
    """
    if not records:
        return []

    ordered = sorted(records, key=lambda r: r.recorded_at)
    size = max(1, len(ordered) // buckets)
    windows = [ordered[i : i + size] for i in range(0, len(ordered), size)][:buckets]

    evolution = []
    for position, window in enumerate(windows):
        decided = [r for r in window if r.outcome in POSITIVE_OUTCOMES | NEGATIVE_OUTCOMES]
        positive = sum(1 for r in decided if r.outcome in POSITIVE_OUTCOMES)
        evolution.append({
            "bucket": position,
            "repairs": len(window),
            "from": window[0].recorded_at.isoformat(),
            "to": window[-1].recorded_at.isoformat(),
            "validation_pass_rate": round(
                sum(1 for r in window if r.validation_passed) / len(window), 4
            ),
            "success_rate": round(positive / len(decided), 4) if decided else None,
            "average_retries": round(sum(r.retry_count for r in window) / len(window), 4),
        })
    return evolution


def pattern_frequency(patterns: list[BugPattern], limit: int = 10) -> list[dict]:
    """Which defect shapes recur most, and how often a repair failed to hold."""
    ranked = sorted(patterns, key=lambda p: (-p.occurrences, p.pattern_id))[:limit]
    return [
        {
            "pattern_id": p.pattern_id,
            "category": p.category,
            "occurrences": p.occurrences,
            "repositories": len(p.repositories),
            "repaired": p.repaired,
            "recurred": p.recurred,
            "recurrence_rate": p.recurrence_rate,
        }
        for p in ranked
    ]


def template_effectiveness(templates: list[RepairTemplate], limit: int = 10) -> list[dict]:
    """Templates ranked by confidence, with their honest track record."""
    ranked = sorted(templates, key=lambda t: (-t.confidence, -t.support, t.template_id))[:limit]
    return [
        {
            "template_id": t.template_id,
            "bug_category": t.bug_category,
            "title": t.title,
            "support": t.support,
            "successes": t.successes,
            "failures": t.failures,
            "success_rate": t.success_rate,
            "confidence": t.confidence,
            "repositories": len(t.repositories),
            "frameworks": t.frameworks,
        }
        for t in ranked
    ]


def framework_coverage(repository_profiles: dict[str, RepositoryProfile]) -> dict:
    """Which frameworks the platform has learned, and how confidently."""
    coverage: dict[str, dict] = {}
    for repository_id, profile in sorted(repository_profiles.items()):
        name = profile.framework.primary_framework
        if name == "unknown":
            continue
        entry = coverage.setdefault(name, {"repositories": [], "confidence": 0.0, "conventions": 0})
        entry["repositories"].append(repository_id)
        entry["confidence"] = max(entry["confidence"], profile.framework.confidence)
        entry["conventions"] = max(entry["conventions"], len(profile.framework.conventions))

    unknown = sum(
        1 for p in repository_profiles.values() if p.framework.primary_framework == "unknown"
    )
    return {
        "frameworks": coverage,
        "covered_repositories": len(repository_profiles) - unknown,
        "unknown_repositories": unknown,
        "coverage_rate": round(
            (len(repository_profiles) - unknown) / len(repository_profiles), 4
        ) if repository_profiles else 0.0,
    }


def dashboard(
    metrics: LearningMetrics,
    records: list[RepairKnowledge],
    templates: list[RepairTemplate],
    patterns: list[BugPattern],
    repository_profiles: dict[str, RepositoryProfile],
    review_summary: dict | None = None,
    outcome_summary: dict | None = None,
) -> dict:
    """Everything section 14 asks for, in one payload."""
    return {
        "learning_growth": learning_growth(records),
        "successful_repairs": metrics.repairs_succeeded,
        "rejected_repairs": metrics.repairs_rejected,
        "success_rate": metrics.success_rate,
        "template_reuse": {
            "reuses": metrics.template_reuses,
            "rate": metrics.template_reuse_rate,
            "templates_mined": metrics.templates_mined,
        },
        "template_effectiveness": template_effectiveness(templates),
        "framework_coverage": framework_coverage(repository_profiles),
        "repository_maturity": metrics.repository_maturity,
        "organization_maturity": metrics.organization_maturity,
        "style_confidence": metrics.style_confidence,
        "pattern_frequency": pattern_frequency(patterns),
        "repair_evolution": repair_evolution(records),
        "bug_category_frequency": metrics.bug_category_frequency,
        "reviews": review_summary or {},
        "outcomes": outcome_summary or {},
        "performance": {
            "learning_updates": metrics.learning_updates,
            "average_update_ms": metrics.average_update_ms,
            "total_update_ms": metrics.total_update_ms,
        },
    }
