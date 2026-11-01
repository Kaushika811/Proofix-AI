"""Learn what human reviewers keep asking for.

A reviewer's free-text reason is mapped into a fixed category vocabulary by
keyword matching — the same deterministic approach used for bug categories, and
for the same reason: a model asked to classify the reason would introduce
non-determinism into a signal that feeds repair guardrails.

The mapping is deliberately conservative. A reason that matches nothing becomes
`unknown` rather than being forced into the nearest category, because a wrong
category becomes a wrong guardrail on every future repair in that family. An
`unknown` rate that climbs is itself the signal that the vocabulary needs a term
added.

The output that matters is `top_concerns()`: the two or three things reviewers
correct most often. Those become guardrails in the mined templates, which is how
a human correction made once turns into a constraint applied thereafter.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from backend.models.learning import (
    ReviewCategory,
    ReviewDecision,
    ReviewRecord,
    ReviewStatistics,
)

# Reason vocabulary. Ordered most-specific first: "security test" should map to
# security rather than testing, because the security concern is the substantive
# one and the test is how it would be demonstrated.
CATEGORY_MARKERS: tuple[tuple[ReviewCategory, tuple[str, ...]], ...] = (
    ("security", (
        "security", "vulnerab", "injection", "xss", "csrf", "auth", "permission",
        "credential", "secret", "sanitiz", "escape", "exploit", "unsafe",
    )),
    ("testing", (
        "test", "coverage", "assertion", "fixture", "mock", "regression test",
        "no tests", "untested",
    )),
    ("architecture", (
        "architect", "design", "structure", "coupling", "abstraction", "layer",
        "separation", "wrong place", "belongs in", "refactor",
    )),
    ("performance", (
        "performance", "slow", "n+1", "latency", "memory", "inefficient",
        "optimi", "hot path", "blocking",
    )),
    ("logic", (
        "logic", "incorrect", "wrong", "bug", "edge case", "off-by-one",
        "does not handle", "breaks when", "race",
    )),
    ("naming", (
        "naming", "name", "rename", "misleading", "unclear variable", "typo",
    )),
    ("documentation", (
        "document", "docstring", "comment", "explain", "unclear why",
    )),
    ("dependencies", (
        "dependency", "dependencies", "import", "library", "package", "vendor",
        "new dep",
    )),
    ("formatting", (
        "format", "style", "lint", "whitespace", "indent", "line length",
        "quotes", "black", "ruff", "pep8",
    )),
)

# Decision vocabulary, for callers that receive a free-text verdict.
DECISION_MARKERS: tuple[tuple[ReviewDecision, tuple[str, ...]], ...] = (
    ("rejected", ("reject", "close", "wontfix", "not needed", "abandon")),
    ("changes_requested", (
        "changes requested", "request changes", "requesting changes",
        "needs work", "please fix", "blocked on",
    )),
    ("major_edits", ("major", "significant", "rewrote", "rewritten", "substantial")),
    ("minor_edits", ("minor", "nit", "small", "tweak", "touch up")),
    ("accepted_immediately", ("lgtm", "approved", "ship it", "looks good", "accept", "merge")),
)

_WORD = re.compile(r"[a-z0-9+]+")


def categorize_reason(reason: str) -> list[ReviewCategory]:
    """Map a reviewer's reason onto categories. Empty means unrecognised.

    Multiple categories are returned when several genuinely apply — "no tests and
    the naming is confusing" is two distinct pieces of feedback, and collapsing
    them to one would lose half the signal.
    """
    text = (reason or "").lower()
    if not text.strip():
        return []

    matched: list[ReviewCategory] = []
    for category, markers in CATEGORY_MARKERS:
        if any(marker in text for marker in markers):
            matched.append(category)
    return matched


def classify_decision(text: str, default: ReviewDecision = "pending") -> ReviewDecision:
    """Map a free-text verdict onto a decision. Most-specific marker wins."""
    lowered = (text or "").lower()
    if not lowered.strip():
        return default
    for decision, markers in DECISION_MARKERS:
        if any(marker in lowered for marker in markers):
            return decision
    return default


@dataclass
class ReviewLearner:
    """Accumulates reviewer verdicts and what they were about."""

    reviews: list[ReviewRecord] = field(default_factory=list)

    def record(
        self,
        repair_id: str,
        decision: ReviewDecision,
        reason: str = "",
        reviewer: str = "",
    ) -> ReviewRecord:
        """Record one verdict, categorising its reason deterministically.

        `reviewer` is stored as given; callers pass a role or an anonymised
        handle. The learning layer never needs an identity to work, and the
        privacy guard would redact a real name anyway.
        """
        categories = categorize_reason(reason) or (["unknown"] if reason else [])
        record = ReviewRecord(
            repair_id=repair_id,
            decision=decision,
            categories=categories,  # type: ignore[arg-type]
            reason_summary=_summarize(reason),
            reviewer=reviewer,
        )
        self.reviews.append(record)
        return record

    def record_from_text(
        self,
        repair_id: str,
        verdict_text: str,
        reviewer: str = "",
    ) -> ReviewRecord:
        """Record from a single free-text verdict, deriving both fields from it."""
        return self.record(
            repair_id,
            classify_decision(verdict_text),
            reason=verdict_text,
            reviewer=reviewer,
        )

    # -- reading ---------------------------------------------------------

    def for_repair(self, repair_id: str) -> list[ReviewRecord]:
        return [r for r in self.reviews if r.repair_id == repair_id]

    def latest_for(self, repair_id: str) -> ReviewRecord | None:
        matching = self.for_repair(repair_id)
        return matching[-1] if matching else None

    def statistics(self, repair_ids: list[str] | None = None) -> ReviewStatistics:
        pool = (
            [r for r in self.reviews if r.repair_id in set(repair_ids)]
            if repair_ids is not None
            else self.reviews
        )
        stats = ReviewStatistics(total_reviews=len(pool))
        stats.by_decision = dict(sorted(Counter(r.decision for r in pool).items()))
        stats.by_category = dict(
            sorted(Counter(c for r in pool for c in r.categories).items(), key=lambda kv: (-kv[1], kv[0]))
        )
        return stats

    def review_confidence(self, repair_ids: list[str] | None = None) -> tuple[float, str]:
        """0..1 — how readily reviewers accept this platform's repairs.

        Damped by sample size: two approvals is not a track record.
        """
        stats = self.statistics(repair_ids)
        if not stats.total_reviews:
            return 0.0, "no reviews recorded yet"
        evidence = min(1.0, stats.total_reviews / 10.0)
        confidence = round(stats.acceptance_rate * evidence, 4)
        return confidence, (
            f"{stats.acceptance_rate:.0%} acceptance across {stats.total_reviews} review(s)"
        )

    def guardrails(self, limit: int = 3) -> list[str]:
        """The concerns reviewers raise most, as instructions for future repairs."""
        directives: list[str] = []
        for category, count in self.statistics().top_concerns(limit):
            if category == "unknown":
                continue
            directives.append(
                f"Reviewers have raised '{category}' {count} time(s) — address it preemptively."
            )
        return directives

    def unknown_rate(self) -> float:
        """Share of reasons the vocabulary failed to classify.

        A rising value means the vocabulary needs a term, not that reviewers
        stopped explaining themselves.
        """
        with_reason = [r for r in self.reviews if r.reason_summary]
        if not with_reason:
            return 0.0
        unknown = sum(1 for r in with_reason if r.categories == ["unknown"])
        return round(unknown / len(with_reason), 4)

    def summary(self) -> dict:
        stats = self.statistics()
        confidence, explanation = self.review_confidence()
        return {
            "total_reviews": stats.total_reviews,
            "by_decision": stats.by_decision,
            "by_category": stats.by_category,
            "acceptance_rate": stats.acceptance_rate,
            "review_confidence": confidence,
            "explanation": explanation,
            "top_concerns": stats.top_concerns(),
            "unclassified_rate": self.unknown_rate(),
        }


def _summarize(reason: str, limit: int = 200) -> str:
    """One-line, length-bounded reason.

    Bounded because a reviewer's comment can contain a pasted diff, and this
    layer must not become a place where source accumulates.
    """
    collapsed = " ".join((reason or "").split())
    return collapsed[:limit]
