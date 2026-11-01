"""Project learned knowledge into the Repository Knowledge Graph.

An adapter, exactly like the seven in `services/knowledge_graph`: it translates
learning state into typed nodes and edges and attaches them to the existing
graph. No learning data is duplicated — the nodes carry identity and a few
traversal attributes, and the substance stays in the learning state.

Attaching is a separate, opt-in step rather than part of `build_knowledge_graph`.
The graph must remain buildable with no learning state at all, which is what
keeps every Phase 4 query working unchanged when Phase 6 is disabled.

New edges connect things that could not previously see each other:

    Repository ──USES_FRAMEWORK──▶ Framework
    Repository ──FOLLOWS_STYLE───▶ Style
    Repair     ──REVIEWED_BY─────▶ Review
    Repair     ──RESULTED_IN─────▶ Outcome
    Repair     ──INSTANCE_OF─────▶ Pattern
    Repair     ──APPLIES_TEMPLATE▶ Template
    Repository ──BELONGS_TO──────▶ Organization
"""

from __future__ import annotations

from backend.models.knowledge_graph import KGNode
from backend.models.learning import (
    BugPattern,
    OrganizationProfile,
    RepairKnowledge,
    RepairTemplate,
    RepositoryProfile,
    ReviewRecord,
)
from backend.services.knowledge_graph import RepositoryKnowledgeGraph, repair_id
from backend.services.repository_graph import node_id


def framework_node_id(name: str) -> str:
    return f"framework:{name}"


def style_node_id(repository_id: str) -> str:
    return f"style:{repository_id}"


def review_node_id(repair: str, position: int) -> str:
    return f"review:{repair}#{position}"


def outcome_node_id(repair: str) -> str:
    return f"outcome:{repair}"


def pattern_node_id(pattern: str) -> str:
    return f"pattern:{pattern}"


def template_node_id(template: str) -> str:
    return f"template:{template}"


def organization_node_id(organization: str) -> str:
    return f"organization:{organization}"


def attach_learning(
    graph: RepositoryKnowledgeGraph,
    *,
    repository_profile: RepositoryProfile | None = None,
    organization_profile: OrganizationProfile | None = None,
    repairs: list[RepairKnowledge] | None = None,
    reviews: list[ReviewRecord] | None = None,
    templates: list[RepairTemplate] | None = None,
    patterns: list[BugPattern] | None = None,
) -> None:
    """Attach learning nodes and edges to an already-built graph.

    Idempotent: `add_node` and `add_edge` deduplicate, so attaching twice
    produces the same graph.
    """
    repository_node = _repository_node(graph)

    if repository_profile is not None:
        _attach_profile(graph, repository_node, repository_profile)
    if organization_profile is not None:
        _attach_organization(graph, repository_node, organization_profile)
    if templates:
        _attach_templates(graph, templates)
    if patterns:
        _attach_patterns(graph, patterns)
    if repairs:
        _attach_repairs(graph, repairs, templates or [], patterns or [])
    if reviews:
        _attach_reviews(graph, reviews)


def _repository_node(graph: RepositoryKnowledgeGraph) -> str | None:
    nodes = graph.nodes_of_type("repository")
    return nodes[0].id if nodes else None


def _attach_profile(
    graph: RepositoryKnowledgeGraph,
    repository_node: str | None,
    profile: RepositoryProfile,
) -> None:
    framework = profile.framework
    if framework.primary_framework != "unknown":
        node = framework_node_id(framework.primary_framework)
        graph.add_node(
            KGNode(
                id=node,
                type="framework",
                name=framework.primary_framework,
                attributes={
                    "confidence": framework.confidence,
                    "conventions": len(framework.conventions),
                    "detected_from": framework.detected_from[:5],
                },
            )
        )
        if repository_node:
            graph.add_edge(
                repository_node, node, "USES_FRAMEWORK",
                weight=framework.confidence,
                provenance="capability",
                evidence=f"detected from {', '.join(framework.detected_from[:3]) or 'imports'}",
            )

    style = profile.style
    if style.files_analyzed:
        node = style_node_id(profile.repository_id or "repository")
        graph.add_node(
            KGNode(
                id=node,
                type="style",
                name=f"{style.function_naming} / {style.quote_style}",
                attributes={
                    "function_naming": style.function_naming,
                    "class_naming": style.class_naming,
                    "quote_style": style.quote_style,
                    "docstring_style": style.docstring_style,
                    "type_hint_coverage": style.type_hint_coverage,
                    "confidence": style.confidence,
                    "files_analyzed": style.files_analyzed,
                },
            )
        )
        if repository_node:
            graph.add_edge(
                repository_node, node, "FOLLOWS_STYLE",
                weight=style.confidence,
                provenance="capability",
                evidence=f"observed across {style.files_analyzed} file(s)",
            )


def _attach_organization(
    graph: RepositoryKnowledgeGraph,
    repository_node: str | None,
    profile: OrganizationProfile,
) -> None:
    node = organization_node_id(profile.organization_id)
    graph.add_node(
        KGNode(
            id=node,
            type="organization",
            name=profile.organization_id,
            attributes={
                "repositories": len(profile.repositories),
                "maturity": profile.maturity,
                "architecture_style": profile.architecture_style,
                "total_repairs": profile.total_repairs,
            },
        )
    )
    if repository_node:
        graph.add_edge(
            repository_node, node, "BELONGS_TO",
            provenance="capability",
            evidence=f"one of {len(profile.repositories)} repository(ies)",
        )


def _attach_templates(graph: RepositoryKnowledgeGraph, templates: list[RepairTemplate]) -> None:
    for template in templates:
        graph.add_node(
            KGNode(
                id=template_node_id(template.template_id),
                type="template",
                name=template.title or template.template_id,
                attributes={
                    "bug_category": template.bug_category,
                    "support": template.support,
                    "success_rate": template.success_rate,
                    "confidence": template.confidence,
                },
            )
        )


def _attach_patterns(graph: RepositoryKnowledgeGraph, patterns: list[BugPattern]) -> None:
    for pattern in patterns:
        graph.add_node(
            KGNode(
                id=pattern_node_id(pattern.pattern_id),
                type="pattern",
                name=pattern.category,
                attributes={
                    "occurrences": pattern.occurrences,
                    "repositories": len(pattern.repositories),
                    "recurrence_rate": pattern.recurrence_rate,
                },
            )
        )


def _attach_repairs(
    graph: RepositoryKnowledgeGraph,
    repairs: list[RepairKnowledge],
    templates: list[RepairTemplate],
    patterns: list[BugPattern],
) -> None:
    """Link repairs to their outcome, pattern and template.

    Repair nodes may already exist from the Phase 3 adapter under the same id
    scheme, so they are added rather than replaced — deduplication keeps the
    earlier node and its FIXED edges intact.
    """
    by_signature = {p.signature: p for p in patterns}
    by_category: dict[str, RepairTemplate] = {}
    for template in templates:
        by_category.setdefault(template.bug_category, template)

    for repair in repairs:
        node = repair_id(repair.repair_id)
        graph.add_node(
            KGNode(
                id=node,
                type="repair",
                name=repair.repair_id,
                file=repair.target_files[0] if repair.target_files else "",
                attributes={
                    "bug_category": repair.bug_category,
                    "validation_passed": repair.validation_passed,
                    "outcome": repair.outcome,
                    "retry_count": repair.retry_count,
                    "framework": repair.framework,
                },
            )
        )

        outcome = outcome_node_id(repair.repair_id)
        graph.add_node(
            KGNode(
                id=outcome,
                type="outcome",
                name=repair.outcome,
                attributes={
                    "merge_status": repair.merge_status,
                    "rolled_back": repair.rolled_back,
                },
            )
        )
        graph.add_edge(
            node, outcome, "RESULTED_IN",
            provenance="repair_memory",
            evidence=f"outcome recorded as {repair.outcome}",
        )

        pattern = by_signature.get(repair.issue_signature)
        if pattern is not None:
            graph.add_edge(
                node, pattern_node_id(pattern.pattern_id), "INSTANCE_OF",
                provenance="repair_memory",
                evidence=f"one of {pattern.occurrences} occurrence(s) of this defect shape",
            )

        template = by_category.get(repair.bug_category)
        if template is not None:
            graph.add_edge(
                node, template_node_id(template.template_id), "APPLIES_TEMPLATE",
                weight=template.confidence,
                provenance="repair_memory",
                evidence=f"matches template for {template.bug_category}",
            )

        for path in repair.target_files:
            graph.add_edge(
                node, node_id("file", path), "FIXED",
                provenance="repair_memory",
                evidence=f"{repair.bug_category} repair",
            )


def _attach_reviews(graph: RepositoryKnowledgeGraph, reviews: list[ReviewRecord]) -> None:
    counters: dict[str, int] = {}
    for review in reviews:
        position = counters.get(review.repair_id, 0)
        counters[review.repair_id] = position + 1

        node = review_node_id(review.repair_id, position)
        graph.add_node(
            KGNode(
                id=node,
                type="review",
                name=review.decision,
                attributes={
                    "categories": list(review.categories),
                    "accepted": review.accepted,
                    "reviewer": review.reviewer,
                },
            )
        )
        graph.add_edge(
            repair_id(review.repair_id), node, "REVIEWED_BY",
            provenance="repair_memory",
            evidence=f"reviewer decision: {review.decision}",
        )
