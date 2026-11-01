"""The per-repository view Context Engineering and patch generation consume.

One object answering "what does the platform know about repairing *this*
repository?" — its style, its framework, the templates that apply, the defects
that recur, and what happened last time.

**Everything here is context, never a decision.** `prompt_context()` produces
directive lines for a prompt; nothing in this module returns a ranking weight, a
gate, or a threshold. That separation is the reason A5.5's ranking logic did not
have to change: learning contributes to what the model *sees*, not to what the
platform *chooses*.

Directives are ordered by how firmly they are grounded — repository style
observed across hundreds of functions first, organisational preference inferred
across repositories last — and the whole block is capped, because a prompt that
is mostly convention advice has crowded out the runtime evidence that actually
identifies the bug.
"""

from __future__ import annotations

from backend.models.learning import (
    BugPattern,
    KnowledgeIndex,
    OrganizationProfile,
    RepairKnowledge,
    RepairTemplate,
    RepositoryProfile,
)
from backend.learning.pattern_mining import select_template, template_directives

# Cap on directive lines contributed to a prompt. Learned context is advisory;
# beyond this it displaces the evidence that identifies the defect.
MAX_DIRECTIVES = 18

# Recent repairs summarised for context.
MAX_RECENT = 5


def build_index(
    repository_profile: RepositoryProfile,
    organization_profile: OrganizationProfile,
    templates: list[RepairTemplate],
    patterns: list[BugPattern],
    recent_repairs: list[RepairKnowledge],
) -> KnowledgeIndex:
    """Assemble the index. Pure composition — no derivation happens here."""
    return KnowledgeIndex(
        repository_id=repository_profile.repository_id,
        repository_profile=repository_profile,
        organization_profile=organization_profile,
        templates=templates,
        patterns=patterns,
        recent_repairs=recent_repairs[:MAX_RECENT],
    )


def prompt_context(
    index: KnowledgeIndex,
    bug_category: str = "",
    reviewer_guardrails: list[str] | None = None,
    max_directives: int = MAX_DIRECTIVES,
) -> dict:
    """Directive lines and the metadata behind them.

    Returns a structured payload rather than a formatted block so the caller
    decides presentation, and so the audit trail can record which knowledge was
    applied without re-parsing prose.
    """
    style = index.repository_profile.style
    framework = index.repository_profile.framework

    directives: list[str] = []
    sources: list[str] = []

    def extend(lines: list[str], source: str) -> None:
        for line in lines:
            if line and line not in directives:
                directives.append(line)
                sources.append(source)

    # Ordered by how directly the evidence supports the claim.
    extend(style.prompt_directives(), "repository_style")
    extend(framework.prompt_directives(), "framework")

    template = select_template(
        index.templates,
        bug_category,
        framework=framework.primary_framework,
        repository_id=index.repository_id,
    ) if bug_category else None
    if template is not None:
        extend(template_directives(template), "repair_template")

    extend(reviewer_guardrails or [], "reviewer_feedback")
    extend(index.organization_profile.prompt_directives(), "organization")

    pattern = _recurring_pattern(index, bug_category)
    if pattern is not None:
        extend(
            [
                f"This defect shape has recurred {pattern.occurrences} time(s) in this "
                f"organisation; {pattern.recurred} were repeats after an earlier repair."
            ],
            "bug_pattern",
        )

    truncated = len(directives) > max_directives
    return {
        "directives": directives[:max_directives],
        "sources": sources[:max_directives],
        "truncated": truncated,
        "template_id": template.template_id if template else None,
        "template_confidence": template.confidence if template else 0.0,
        "framework": framework.primary_framework,
        "framework_confidence": framework.confidence,
        "style_confidence": style.confidence,
        "repository_maturity": index.repository_profile.maturity,
        "organization_maturity": index.organization_profile.maturity,
        "recent_repairs": [
            {
                "bug_category": r.bug_category,
                "outcome": r.outcome,
                "validation_passed": r.validation_passed,
                "retry_count": r.retry_count,
            }
            for r in index.recent_repairs
        ],
    }


def _recurring_pattern(index: KnowledgeIndex, bug_category: str) -> BugPattern | None:
    if not bug_category:
        return None
    matching = [p for p in index.patterns if p.category == bug_category]
    return max(matching, key=lambda p: (p.occurrences, p.pattern_id)) if matching else None


def render_directives(context: dict) -> str:
    """Format a context payload as a prompt block, or "" when there is nothing.

    Returning an empty string for an empty index matters: a heading with no
    content underneath reads to the model as an instruction it failed to follow.
    """
    directives = context.get("directives") or []
    if not directives:
        return ""

    lines = ["Repository conventions learned from prior work in this codebase:"]
    lines.extend(f"- {d}" for d in directives)
    lines.append(
        "These are conventions, not evidence. Where they conflict with the "
        "reproduced failure, the failure wins."
    )
    return "\n".join(lines)


def explain(context: dict) -> list[str]:
    """Why each directive is present — one line per directive, for the audit trail."""
    return [
        f"{source}: {directive}"
        for directive, source in zip(context.get("directives", []), context.get("sources", []))
    ]
