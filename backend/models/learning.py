"""Schemas for the Organizational Learning System.

Everything here is **metadata about repairs**, never the repairs themselves. No
model in this module has a field that can hold source code, a prompt, a secret,
or a personal identifier — that is a structural guarantee, not a convention, and
`test_no_learning_model_can_hold_source` asserts it.

The learning system makes the *platform* smarter, not the model. Every value is
derived by counting, matching or aggregating observations. There is no
inference, no embedding and no training: a style profile says "84% of functions
in this repository use snake_case, observed across 213 functions", and that
sentence is the whole of its reasoning.

Two conventions run throughout:

* **Confidence is sample-driven.** Every learned property carries the number of
  observations behind it. A convention seen twice is reported at low confidence
  rather than suppressed, so a caller can decide its own threshold.

* **Nothing learned may outrank runtime evidence.** These profiles are inputs to
  prompt *context*, never to ranking weights or validation gates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

# ---------------------------------------------------------------- outcomes

OutcomeStatus = Literal[
    "suggested",
    "accepted",
    "rejected",
    "modified",
    "merged",
    "reverted",
    "rolled_back",
    "production_success",
    "production_failure",
]

# Outcomes that indicate the repair ultimately worked.
POSITIVE_OUTCOMES: frozenset[str] = frozenset({
    "accepted", "merged", "production_success",
})

# Outcomes that indicate it did not.
NEGATIVE_OUTCOMES: frozenset[str] = frozenset({
    "rejected", "reverted", "rolled_back", "production_failure",
})

ReviewDecision = Literal[
    "accepted_immediately",
    "minor_edits",
    "major_edits",
    "changes_requested",
    "rejected",
    "pending",
]

# Deterministic categories a reviewer's stated reason maps into.
ReviewCategory = Literal[
    "formatting",
    "architecture",
    "security",
    "testing",
    "logic",
    "performance",
    "naming",
    "documentation",
    "dependencies",
    "unknown",
]


# ---------------------------------------------------------- repair memory v2


class RepairKnowledge(BaseModel):
    """One completed repair, as structured metadata.

    Deliberately absent: prompts, patch bodies, source, file contents. `file` and
    `function` are names, not code. `patch_summary` is a generated shape
    description ("1 file, +4/-2 lines, 1 function touched"), never a diff.
    """

    repair_id: str
    schema_version: str = SCHEMA_VERSION
    recorded_at: datetime = Field(default_factory=datetime.utcnow)

    # -- identity
    repository_id: str = ""
    repository_hash: str = ""
    run_id: str = ""

    # -- what was wrong
    issue_signature: str = ""       # deterministic hash of the failure shape
    bug_category: str = "unknown"
    root_cause_category: str = "unknown"
    exception_type: str = ""

    # -- where
    target_files: list[str] = Field(default_factory=list)
    target_functions: list[str] = Field(default_factory=list)
    file_count: int = 0

    # -- context shape (sizes only, never content)
    context_files: int = 0
    context_functions: int = 0
    context_chars: int = 0

    # -- what changed (shape only, never the diff)
    patch_summary: str = ""
    lines_added: int = 0
    lines_removed: int = 0
    functions_touched: int = 0

    # -- how it went
    validation_passed: bool = False
    mutation_score: float | None = None
    mutation_status: str = "not_run"
    security_score: float | None = None
    security_rejected: bool = False
    retry_count: int = 0

    # -- human and production verdict
    reviewer_decision: ReviewDecision = "pending"
    review_categories: list[ReviewCategory] = Field(default_factory=list)
    outcome: OutcomeStatus = "suggested"
    merge_status: str = "unmerged"
    rolled_back: bool = False

    # -- environment
    framework: str = "unknown"
    language: str = "python"
    pr_type: str = ""

    @property
    def succeeded(self) -> bool:
        return self.validation_passed and not self.security_rejected and not self.rolled_back


# ------------------------------------------------------------------- style


StyleValue = Literal["snake_case", "camelCase", "PascalCase", "SCREAMING_SNAKE", "mixed", "unknown"]


class StyleObservation(BaseModel):
    """One learned convention, with the evidence count behind it."""

    property: str
    value: str
    confidence: float = 0.0
    observations: int = 0
    distribution: dict[str, int] = Field(default_factory=dict)

    def describe(self) -> str:
        return (
            f"{self.property}={self.value} "
            f"({self.confidence:.0%} of {self.observations} observation(s))"
        )


class StyleProfile(BaseModel):
    """Deterministic coding conventions for one repository."""

    repository_id: str = ""
    function_naming: StyleValue = "unknown"
    class_naming: StyleValue = "unknown"
    constant_naming: StyleValue = "unknown"
    variable_naming: StyleValue = "unknown"

    quote_style: Literal["single", "double", "mixed", "unknown"] = "unknown"
    docstring_style: Literal["google", "numpy", "sphinx", "plain", "none", "unknown"] = "unknown"
    docstring_coverage: float = 0.0
    type_hint_coverage: float = 0.0
    async_ratio: float = 0.0

    logging_style: Literal["logging_module", "print", "structlog", "loguru", "none", "unknown"] = "unknown"
    exception_style: Literal["custom_hierarchy", "builtin", "mixed", "unknown"] = "unknown"
    import_style: Literal["grouped_sorted", "grouped", "flat", "unknown"] = "unknown"
    indent: int = 4
    max_line_length: int = 0

    observations: list[StyleObservation] = Field(default_factory=list)
    files_analyzed: int = 0
    confidence: float = 0.0

    def prompt_directives(self) -> list[str]:
        """Style instructions for a patch prompt, strongest evidence first."""
        directives: list[str] = []
        if self.function_naming not in ("unknown", "mixed"):
            directives.append(f"Name functions in {self.function_naming}.")
        if self.class_naming not in ("unknown", "mixed"):
            directives.append(f"Name classes in {self.class_naming}.")
        if self.quote_style in ("single", "double"):
            directives.append(f"Use {self.quote_style} quotes for strings.")
        if self.type_hint_coverage >= 0.5:
            directives.append("Annotate parameters and return types.")
        if self.docstring_coverage >= 0.5 and self.docstring_style != "none":
            directives.append(f"Write {self.docstring_style}-style docstrings on public callables.")
        if self.logging_style == "logging_module":
            directives.append("Use the `logging` module rather than `print`.")
        elif self.logging_style in ("structlog", "loguru"):
            directives.append(f"Use `{self.logging_style}` for logging.")
        if self.exception_style == "custom_hierarchy":
            directives.append("Raise the module's own exception types rather than builtins.")
        if self.indent and self.indent != 4:
            directives.append(f"Indent with {self.indent} spaces.")
        return directives


# --------------------------------------------------------------- framework


class FrameworkConvention(BaseModel):
    """One convention a framework implies, with the evidence that detected it."""

    aspect: str          # routing | validation | orm | testing | auth | di | config
    convention: str
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class FrameworkProfile(BaseModel):
    """Detected frameworks and the conventions they imply."""

    repository_id: str = ""
    primary_framework: str = "unknown"
    frameworks: dict[str, float] = Field(default_factory=dict)   # name -> confidence
    conventions: list[FrameworkConvention] = Field(default_factory=list)
    detected_from: list[str] = Field(default_factory=list)
    confidence: float = 0.0

    def convention_for(self, aspect: str) -> FrameworkConvention | None:
        matches = [c for c in self.conventions if c.aspect == aspect]
        return max(matches, key=lambda c: c.confidence) if matches else None

    def prompt_directives(self) -> list[str]:
        if self.primary_framework == "unknown":
            return []
        directives = [f"This repository uses {self.primary_framework}; follow its conventions."]
        directives.extend(
            f"{c.aspect.replace('_', ' ').title()}: {c.convention}"
            for c in sorted(self.conventions, key=lambda c: (-c.confidence, c.aspect))
        )
        return directives


# ---------------------------------------------------------------- patterns


class RepairTemplate(BaseModel):
    """A recurring repair, generalised from repeated observations.

    A template is evidence that a class of defect has been fixed a particular way
    before. It is never a patch: it carries the *approach*, the guardrails and
    the historical success rate, and the model still has to write the code.
    """

    template_id: str
    bug_category: str
    title: str = ""
    approach: str = ""
    guardrails: list[str] = Field(default_factory=list)
    validation_hints: list[str] = Field(default_factory=list)

    support: int = 0                 # repairs this was mined from
    successes: int = 0
    failures: int = 0
    frameworks: dict[str, int] = Field(default_factory=dict)
    languages: dict[str, int] = Field(default_factory=dict)
    repositories: list[str] = Field(default_factory=list)

    first_seen: datetime = Field(default_factory=datetime.utcnow)
    last_seen: datetime = Field(default_factory=datetime.utcnow)

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return round(self.successes / total, 4) if total else 0.0

    @property
    def confidence(self) -> float:
        """Success rate damped by how much evidence supports it.

        A single successful repair is 100% successful and proves almost nothing,
        so the rate is scaled by sample size up to a saturation point.
        """
        if not self.support:
            return 0.0
        evidence = min(1.0, self.support / 5.0)
        return round(self.success_rate * evidence, 4)


class BugPattern(BaseModel):
    """A recurring defect shape, independent of how it was repaired."""

    pattern_id: str
    category: str
    signature: str = ""
    occurrences: int = 0
    repositories: list[str] = Field(default_factory=list)
    example_functions: list[str] = Field(default_factory=list)
    repaired: int = 0
    recurred: int = 0

    @property
    def recurrence_rate(self) -> float:
        return round(self.recurred / self.occurrences, 4) if self.occurrences else 0.0


# ---------------------------------------------------------------- outcomes


class OutcomeRecord(BaseModel):
    """One transition in a repair's life, appended never rewritten."""

    repair_id: str
    status: OutcomeStatus
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
    detail: str = ""
    actor: str = "pipeline"


class OutcomeStatistics(BaseModel):
    """Aggregate outcomes for one grouping key (category, framework, template)."""

    key: str
    total: int = 0
    positive: int = 0
    negative: int = 0
    pending: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        decided = self.positive + self.negative
        return round(self.positive / decided, 4) if decided else 0.0

    @property
    def confidence(self) -> float:
        decided = self.positive + self.negative
        if not decided:
            return 0.0
        return round(self.success_rate * min(1.0, decided / 5.0), 4)


# ----------------------------------------------------------------- reviews


class ReviewRecord(BaseModel):
    """One human verdict, with its reason mapped to a fixed category."""

    repair_id: str
    decision: ReviewDecision
    categories: list[ReviewCategory] = Field(default_factory=list)
    reason_summary: str = ""
    reviewer: str = ""
    recorded_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def accepted(self) -> bool:
        return self.decision in ("accepted_immediately", "minor_edits")


class ReviewStatistics(BaseModel):
    """What reviewers keep asking for — the platform's improvement backlog."""

    total_reviews: int = 0
    by_decision: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)

    @property
    def acceptance_rate(self) -> float:
        accepted = self.by_decision.get("accepted_immediately", 0) + self.by_decision.get("minor_edits", 0)
        return round(accepted / self.total_reviews, 4) if self.total_reviews else 0.0

    def top_concerns(self, limit: int = 3) -> list[tuple[str, int]]:
        return sorted(self.by_category.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


# ----------------------------------------------------------------- profiles


class RepositoryProfile(BaseModel):
    """Everything learned about one repository."""

    repository_id: str
    repository_name: str = ""
    style: StyleProfile = Field(default_factory=StyleProfile)
    framework: FrameworkProfile = Field(default_factory=FrameworkProfile)

    repairs_recorded: int = 0
    repairs_succeeded: int = 0
    repairs_reviewed: int = 0
    common_bug_categories: dict[str, int] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def repair_success_rate(self) -> float:
        return round(self.repairs_succeeded / self.repairs_recorded, 4) if self.repairs_recorded else 0.0

    @property
    def maturity(self) -> float:
        """0..1 — how much this repository has taught the platform."""
        signals = [
            min(1.0, self.repairs_recorded / 20.0),
            min(1.0, self.repairs_reviewed / 10.0),
            self.style.confidence,
            self.framework.confidence,
        ]
        return round(sum(signals) / len(signals), 4)


class OrganizationProfile(BaseModel):
    """Preferences aggregated across every repository in the organization."""

    organization_id: str = "default"
    repositories: list[str] = Field(default_factory=list)

    preferred_libraries: dict[str, int] = Field(default_factory=dict)
    naming_conventions: dict[str, str] = Field(default_factory=dict)
    testing_conventions: dict[str, str] = Field(default_factory=dict)
    architecture_style: str = "unknown"
    error_handling_style: str = "unknown"
    dependency_injection_style: str = "unknown"
    logging_style: str = "unknown"
    authentication_style: str = "unknown"
    validation_style: str = "unknown"
    folder_conventions: dict[str, int] = Field(default_factory=dict)

    frameworks: dict[str, int] = Field(default_factory=dict)
    total_repairs: int = 0
    total_reviews: int = 0
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def maturity(self) -> float:
        signals = [
            min(1.0, len(self.repositories) / 5.0),
            min(1.0, self.total_repairs / 50.0),
            min(1.0, self.total_reviews / 25.0),
            min(1.0, len(self.preferred_libraries) / 10.0),
        ]
        return round(sum(signals) / len(signals), 4)

    def prompt_directives(self) -> list[str]:
        directives: list[str] = []
        if self.error_handling_style != "unknown":
            directives.append(f"Organisation error-handling convention: {self.error_handling_style}.")
        if self.logging_style != "unknown":
            directives.append(f"Organisation logging convention: {self.logging_style}.")
        if self.validation_style != "unknown":
            directives.append(f"Organisation validation convention: {self.validation_style}.")
        top = sorted(self.preferred_libraries.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        if top:
            directives.append("Prefer established libraries: " + ", ".join(name for name, _ in top) + ".")
        return directives


# ------------------------------------------------------------------ scoring


class LearningScore(BaseModel):
    """Explainable per-repair score. Every component is named and bounded 0..1."""

    repair_confidence: float = 0.0
    review_confidence: float = 0.0
    historical_success: float = 0.0
    framework_match: float = 0.0
    style_match: float = 0.0
    validation_quality: float = 0.0
    mutation_quality: float = 0.0

    overall: float = 0.0
    reasons: list[str] = Field(default_factory=list)

    # Which components had evidence. A component absent from this list scored
    # 0.0 because it could not be evaluated, not because it evaluated badly —
    # and only the measured ones contribute to `overall`. Without this a repair
    # awaiting review is indistinguishable from one that was reviewed badly.
    measured: list[str] = Field(default_factory=list)

    def components(self) -> dict[str, float]:
        return {
            "repair_confidence": self.repair_confidence,
            "review_confidence": self.review_confidence,
            "historical_success": self.historical_success,
            "framework_match": self.framework_match,
            "style_match": self.style_match,
            "validation_quality": self.validation_quality,
            "mutation_quality": self.mutation_quality,
        }

    def measured_components(self) -> dict[str, float]:
        """Only the components that were actually evaluated."""
        return {k: v for k, v in self.components().items() if k in set(self.measured)}

    def unmeasured(self) -> list[str]:
        return [k for k in self.components() if k not in set(self.measured)]


# ----------------------------------------------------------- knowledge index


class KnowledgeIndex(BaseModel):
    """The per-repository view Context Engineering consumes."""

    repository_id: str
    repository_profile: RepositoryProfile
    organization_profile: OrganizationProfile = Field(default_factory=OrganizationProfile)
    templates: list[RepairTemplate] = Field(default_factory=list)
    patterns: list[BugPattern] = Field(default_factory=list)
    recent_repairs: list[RepairKnowledge] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)

    def templates_for(self, bug_category: str, limit: int = 3) -> list[RepairTemplate]:
        matching = [t for t in self.templates if t.bug_category == bug_category]
        matching.sort(key=lambda t: (-t.confidence, -t.support, t.template_id))
        return matching[:limit]


# ---------------------------------------------------------------- analytics


class LearningMetrics(BaseModel):
    """Analytics surface for the dashboard."""

    repairs_recorded: int = 0
    repairs_succeeded: int = 0
    repairs_rejected: int = 0
    reviews_recorded: int = 0
    templates_mined: int = 0
    template_reuses: int = 0
    patterns_identified: int = 0

    repositories_known: int = 0
    frameworks_covered: dict[str, int] = Field(default_factory=dict)
    bug_category_frequency: dict[str, int] = Field(default_factory=dict)

    repository_maturity: dict[str, float] = Field(default_factory=dict)
    organization_maturity: float = 0.0
    style_confidence: dict[str, float] = Field(default_factory=dict)

    learning_updates: int = 0
    total_update_ms: int = 0

    @property
    def success_rate(self) -> float:
        decided = self.repairs_succeeded + self.repairs_rejected
        return round(self.repairs_succeeded / decided, 4) if decided else 0.0

    @property
    def average_update_ms(self) -> float:
        return round(self.total_update_ms / self.learning_updates, 4) if self.learning_updates else 0.0

    @property
    def template_reuse_rate(self) -> float:
        return round(self.template_reuses / self.repairs_recorded, 4) if self.repairs_recorded else 0.0
