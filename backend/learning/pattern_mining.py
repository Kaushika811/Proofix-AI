"""Generalise recurring repairs into reusable templates.

The algorithm is exact-key grouping, deliberately. Repairs are grouped by
`(bug_category, root_cause_category)` — both of which are already deterministic
categories from a fixed vocabulary — and a group becomes a template once enough
repairs support it. There is no similarity metric, no clustering, no threshold
to tune, and running it twice on the same records yields byte-identical output.

Fuzzy grouping was the alternative and is worse here. A near-miss cluster would
merge "expiry comparison in auth" with "boundary condition in pagination"
because both mention comparison, and the resulting template would advise the
wrong approach with the authority of aggregate evidence behind it.

**A template is not a patch.** It carries the approach, the guardrails that
repairs in this family needed, and the historical success rate. The model still
writes the code. Storing a patch body would both break the privacy guarantee and
encourage the model to replay a fix that suited a different repository.

Templates whose historical success rate is poor are *kept*, not deleted: "this
approach has failed 4 of 5 times" is more useful to a ranking decision than the
absence of a template.
"""

from __future__ import annotations

from collections import Counter

from backend.models.learning import (
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    BugPattern,
    RepairKnowledge,
    RepairTemplate,
)
from backend.learning.repair_memory import signature

# A group becomes a template at this many supporting repairs. Below it, one
# repository's single fix would be presented to every other repository as
# organisational knowledge.
MIN_SUPPORT = 2

# A pattern is reported once seen this many times.
MIN_PATTERN_OCCURRENCES = 2

# Approach text per bug category. Fixed, generic, and framework-neutral: what
# varies between organisations is *which* categories recur and how often their
# repairs succeed, not what a SQL-injection fix fundamentally is.
CATEGORY_APPROACHES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "sql-injection": (
        "Replace string-built SQL with parameterised queries or the ORM's query API.",
        ("Never interpolate user input into SQL text.",
         "Keep the existing query semantics — parameterisation must not change results."),
        ("A test must exercise the previously injectable input.",),
    ),
    "xss": (
        "Escape or sanitise untrusted values at the point of rendering.",
        ("Escape on output, not on input — input escaping breaks stored data.",
         "Do not disable the template engine's autoescaping."),
        ("A test must assert the payload is rendered inert.",),
    ),
    "csrf": (
        "Require and verify an anti-forgery token on state-changing requests.",
        ("Exempt only endpoints that are genuinely safe to replay.",),
        ("A test must reject a request without a valid token.",),
    ),
    "command-injection": (
        "Pass arguments as a list to the subprocess API; never build a shell string.",
        ("Avoid shell=True.", "Validate any path or identifier taken from input."),
        ("A test must cover the previously injectable argument.",),
    ),
    "path-traversal": (
        "Resolve the path and verify it stays inside the permitted root.",
        ("Normalise before checking — a literal '..' scan misses encoded traversal.",),
        ("A test must attempt an escape and expect refusal.",),
    ),
    "hardcoded-secret": (
        "Read the value from configuration or the environment instead of source.",
        ("Do not leave the literal as a default.",),
        ("A test must confirm the value is not required at import time.",),
    ),
    "weak-crypto": (
        "Replace the weak primitive with a current one from the standard library.",
        ("Preserve any stored-format compatibility, or migrate explicitly.",),
        ("A test must cover the new primitive's output shape.",),
    ),
    "deserialization": (
        "Replace unsafe deserialisation with a format that cannot execute code.",
        ("Do not accept pickled input from an untrusted source.",),
        ("A test must reject a hostile payload.",),
    ),
    "null-dereference": (
        "Guard the access, or ensure the value cannot be absent at that point.",
        ("Prefer establishing the invariant over adding a defensive check everywhere.",),
        ("A test must cover the absent-value path.",),
    ),
    "missing-key": (
        "Use a lookup that tolerates absence, or validate presence before access.",
        ("Do not silently default when absence indicates a real error.",),
        ("A test must cover the missing-key path.",),
    ),
    "value-validation": (
        "Validate the value at the boundary and reject it with a clear error.",
        ("Validate at the edge, not deep in the call stack.",),
        ("A test must cover both the accepted and the rejected value.",),
    ),
    "expiry-comparison": (
        "Compare against the current time with correct ordering and units.",
        ("Use a single clock source.", "Be explicit about timezone and units."),
        ("A test must cover an expired value and a valid one.",),
    ),
    "boundary-condition": (
        "Correct the comparison or index bound and cover the edge explicitly.",
        ("Check both ends of the range.",),
        ("A test must cover the first and last valid element.",),
    ),
    "authentication": (
        "Verify the credential completely before granting access.",
        ("Fail closed on any verification error.",),
        ("A test must cover an invalid credential.",),
    ),
    "authorization": (
        "Check the caller's permission for the specific resource, not just identity.",
        ("Authorise per resource, not per route.",),
        ("A test must cover an authenticated but unauthorised caller.",),
    ),
    "type-error": (
        "Correct the type at the boundary where it enters, and annotate it.",
        ("Convert once at the edge rather than defensively at every use.",),
        ("A test must cover the previously mistyped input.",),
    ),
    "runtime-state": (
        "Make the invalid state unrepresentable, or guard the transition.",
        ("Prefer restructuring over a flag check.",),
        ("A test must cover the previously invalid ordering.",),
    ),
    "arithmetic": (
        "Guard the operand or use an arithmetic form that cannot fault.",
        ("Handle the degenerate case explicitly.",),
        ("A test must cover the degenerate operand.",),
    ),
    "timeout": (
        "Set an explicit bound and handle expiry deterministically.",
        ("Choose a bound from the operation's real budget.",),
        ("A test must cover the timeout path.",),
    ),
}

_DEFAULT_APPROACH = (
    "Address the identified root cause with the minimum change that resolves it.",
    ("Do not restructure code unrelated to the defect.",),
    ("A test must reproduce the original failure and then pass.",),
)


def template_id_for(bug_category: str, root_cause_category: str) -> str:
    return f"tpl-{signature(bug_category, root_cause_category)}"


def mine_templates(
    records: list[RepairKnowledge],
    min_support: int = MIN_SUPPORT,
) -> list[RepairTemplate]:
    """Group repairs into templates. Deterministic and order-independent."""
    groups: dict[tuple[str, str], list[RepairKnowledge]] = {}
    for record in records:
        groups.setdefault((record.bug_category, record.root_cause_category), []).append(record)

    templates: list[RepairTemplate] = []
    for (bug_category, root_cause), members in sorted(groups.items()):
        if len(members) < min_support or bug_category == "unknown":
            continue
        templates.append(_build_template(bug_category, root_cause, members))

    templates.sort(key=lambda t: (-t.confidence, -t.support, t.template_id))
    return templates


def _build_template(
    bug_category: str,
    root_cause: str,
    members: list[RepairKnowledge],
) -> RepairTemplate:
    approach, guardrails, validation = CATEGORY_APPROACHES.get(bug_category, _DEFAULT_APPROACH)

    successes = sum(
        1 for r in members
        if r.outcome in POSITIVE_OUTCOMES or (r.outcome == "suggested" and r.succeeded)
    )
    failures = sum(1 for r in members if r.outcome in NEGATIVE_OUTCOMES)

    # Guardrails learned from what reviewers actually objected to in this family.
    review_concerns = Counter(
        category for r in members for category in r.review_categories if category != "unknown"
    )
    learned = [
        _CONCERN_GUARDRAILS[concern]
        for concern, _count in review_concerns.most_common(2)
        if concern in _CONCERN_GUARDRAILS
    ]

    timestamps = [r.recorded_at for r in members]
    return RepairTemplate(
        template_id=template_id_for(bug_category, root_cause),
        bug_category=bug_category,
        title=f"{bug_category.replace('-', ' ')} via {root_cause.replace('-', ' ')}",
        approach=approach,
        guardrails=list(guardrails) + learned,
        validation_hints=list(validation),
        support=len(members),
        successes=successes,
        failures=failures,
        frameworks=dict(Counter(r.framework for r in members if r.framework != "unknown")),
        languages=dict(Counter(r.language for r in members)),
        repositories=sorted({r.repository_id for r in members if r.repository_id}),
        first_seen=min(timestamps),
        last_seen=max(timestamps),
    )


# What a recurring reviewer concern implies for future repairs in this family.
_CONCERN_GUARDRAILS = {
    "testing": "Reviewers repeatedly asked for more test coverage in this family — add a test.",
    "security": "Reviewers repeatedly raised security concerns here — state the security impact.",
    "architecture": "Reviewers repeatedly objected to the structure — keep the change local.",
    "formatting": "Reviewers repeatedly corrected formatting — follow the repository's style exactly.",
    "performance": "Reviewers repeatedly raised performance — avoid work inside hot paths.",
    "naming": "Reviewers repeatedly renamed things — follow the repository's naming convention.",
    "documentation": "Reviewers repeatedly asked for documentation — document the changed behaviour.",
    "logic": "Reviewers repeatedly found logic errors here — cover the edge cases explicitly.",
    "dependencies": "Reviewers repeatedly objected to new dependencies — use what is already present.",
}


def mine_patterns(
    records: list[RepairKnowledge],
    min_occurrences: int = MIN_PATTERN_OCCURRENCES,
) -> list[BugPattern]:
    """Identify recurring defect shapes, independent of how they were repaired.

    Grouped on `issue_signature`, which excludes file and function names — the
    question is whether the same *kind* of defect keeps appearing, which a
    location-bearing key could never answer.
    """
    groups: dict[str, list[RepairKnowledge]] = {}
    for record in records:
        if record.bug_category == "unknown":
            continue
        groups.setdefault(record.issue_signature, []).append(record)

    patterns: list[BugPattern] = []
    for sig, members in sorted(groups.items()):
        if len(members) < min_occurrences:
            continue

        repositories = sorted({r.repository_id for r in members if r.repository_id})
        # Recurrence: the same defect shape appearing more than once inside a
        # single repository means the earlier repair did not hold.
        per_repo = Counter(r.repository_id for r in members if r.repository_id)
        recurred = sum(count - 1 for count in per_repo.values() if count > 1)

        patterns.append(
            BugPattern(
                pattern_id=f"pat-{sig}",
                category=members[0].bug_category,
                signature=sig,
                occurrences=len(members),
                repositories=repositories,
                example_functions=sorted({f for r in members for f in r.target_functions})[:5],
                repaired=sum(1 for r in members if r.succeeded),
                recurred=recurred,
            )
        )

    patterns.sort(key=lambda p: (-p.occurrences, p.pattern_id))
    return patterns


def select_template(
    templates: list[RepairTemplate],
    bug_category: str,
    framework: str = "",
    repository_id: str = "",
) -> RepairTemplate | None:
    """Best template for a repair about to be attempted.

    Preference order: same category, then a framework match, then a repository
    match, then confidence. Ties break on id so selection is reproducible.
    """
    candidates = [t for t in templates if t.bug_category == bug_category]
    if not candidates:
        return None

    def rank(template: RepairTemplate) -> tuple:
        return (
            -(1 if framework and framework in template.frameworks else 0),
            -(1 if repository_id and repository_id in template.repositories else 0),
            -template.confidence,
            -template.support,
            template.template_id,
        )

    return sorted(candidates, key=rank)[0]


def template_directives(template: RepairTemplate) -> list[str]:
    """Prompt lines for a template, including its honest track record."""
    lines = [f"Known approach for {template.bug_category}: {template.approach}"]
    lines.extend(f"Guardrail: {g}" for g in template.guardrails)
    lines.extend(f"Validation: {v}" for v in template.validation_hints)
    lines.append(
        f"This approach has succeeded in {template.successes} of "
        f"{template.successes + template.failures} previously decided repair(s)."
        if (template.successes + template.failures)
        else f"This approach is derived from {template.support} prior repair(s), none yet decided."
    )
    return lines
