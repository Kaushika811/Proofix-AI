"""Repair Memory v2 — every repair as structured, privacy-safe knowledge.

The Phase 3 `services/repair_memory` remains: it answers "have we fixed this
exact function before?" during a run, keyed on content hashes, and A7 still
consults it. This module answers a different question — "what has this
organisation learned about repairing this *kind* of defect?" — and is written
for durability rather than for retrieval inside one run.

**Metadata only, structurally.** `RepairKnowledge` has no field that can hold
source, a diff, or a prompt. `summarize_patch` converts a patch bundle into a
shape description ("2 files, +9/-3 lines, 3 functions") and discards the content
before it ever reaches a record. That is the privacy guarantee: not "we choose
not to store it", but "there is nowhere to put it".

Recording is append-and-replace by `repair_id`, so re-running the same repair
corrects the record rather than duplicating it, and the memory stays bounded.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

from backend.models.learning import (
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    OutcomeStatus,
    RepairKnowledge,
    ReviewDecision,
)

# Bound on retained records. Oldest are dropped first.
MAX_RECORDS = 5000

# Fields a caller might try to pass that would carry content rather than
# metadata. Rejected loudly rather than silently dropped, so a mistake in an
# integration surfaces during development instead of leaking in production.
FORBIDDEN_FIELDS = frozenset({
    "source", "patch", "diff", "prompt", "content", "body", "original", "patched",
    "raw_prompt", "file_contents", "code",
})


class PrivacyViolation(ValueError):
    """Raised when a caller tries to put content into the learning store."""


def signature(*parts: str) -> str:
    """Stable short hash used for issue and pattern identity."""
    joined = "|".join(p.strip().lower() for p in parts if p)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def issue_signature(
    bug_category: str,
    exception_type: str = "",
    root_cause_category: str = "",
) -> str:
    """Identity of a *kind* of failure, not of one occurrence.

    Deliberately excludes file and function names: the point is to recognise the
    same defect shape recurring somewhere else, which a location-bearing
    signature could never do.
    """
    return signature(bug_category, exception_type.rsplit(".", 1)[-1], root_cause_category)


# -- categorisation --------------------------------------------------------
# Generic defect vocabulary. Nothing here may encode one repository's domain.

_ROOT_CAUSE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("missing-validation", ("validat", "sanitiz", "check", "verify", "assert")),
    ("boundary-condition", ("off-by-one", "boundary", "index", "range", "bound", "overflow")),
    ("null-handling", ("none", "null", "missing", "absent", "empty", "optional")),
    ("expiry-comparison", ("expir", "timeout", "stale", "ttl", "deadline")),
    ("state-management", ("state", "race", "concurren", "lock", "thread", "atomic")),
    ("type-mismatch", ("type", "cast", "convert", "coerce", "parse")),
    ("configuration", ("config", "setting", "environment", "default", "flag")),
    ("resource-handling", ("leak", "close", "release", "cleanup", "handle", "descriptor")),
    ("logic-error", ("logic", "condition", "branch", "order", "sequence")),
)


def classify_root_cause(summary: str) -> str:
    """Map a root-cause sentence into a fixed category.

    Keyword matching over a generic vocabulary, first match wins. Returns
    "unknown" rather than guessing when nothing matches — an unknown category is
    honest, and a wrong one poisons every template mined from it.
    """
    text = (summary or "").lower()
    if not text.strip():
        return "unknown"
    for category, markers in _ROOT_CAUSE_RULES:
        if any(marker in text for marker in markers):
            return category
    return "unknown"


_SECURITY_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sql-injection", ("sql injection", "sqli", "b608")),
    ("xss", ("xss", "cross-site scripting")),
    ("csrf", ("csrf", "cross-site request forgery")),
    ("command-injection", ("command injection", "shell injection", "b602", "b605")),
    ("path-traversal", ("path traversal", "directory traversal")),
    ("weak-crypto", ("weak crypto", "insecure hash", "md5", "sha1", "b303", "b324")),
    ("hardcoded-secret", ("hardcoded", "b105", "b106", "b107")),
    ("deserialization", ("pickle", "deserializ", "b301")),
    ("authentication", ("authentication", "auth bypass", "login")),
    ("authorization", ("authorization", "permission", "access control", "privilege")),
)


def classify_bug_category(
    exception_type: str = "",
    root_cause_summary: str = "",
    static_rule: str = "",
) -> str:
    """Assign a bug category from the strongest available evidence.

    Security findings are checked first: a SQL-injection defect that happens to
    raise a `TypeError` is a SQL-injection defect, and categorising it by the
    exception would put it in the wrong template family.
    """
    haystack = f"{root_cause_summary} {static_rule}".lower()
    for category, markers in _SECURITY_CATEGORIES:
        if any(marker in haystack for marker in markers):
            return category

    bare = (exception_type or "").rsplit(".", 1)[-1]
    if bare:
        return {
            "AttributeError": "null-dereference",
            "TypeError": "type-error",
            "KeyError": "missing-key",
            "IndexError": "index-error",
            "ValueError": "value-validation",
            "AssertionError": "assertion-failure",
            "ZeroDivisionError": "arithmetic",
            "TimeoutError": "timeout",
            "RuntimeError": "runtime-state",
        }.get(bare, f"exception:{bare}")

    root = classify_root_cause(root_cause_summary)
    return root if root != "unknown" else "unknown"


# -- patch shape -----------------------------------------------------------


def summarize_patch(patches: list[dict]) -> tuple[str, int, int, int]:
    """Reduce a patch bundle to a shape description. Content is discarded here.

    Returns (summary, lines_added, lines_removed, functions_touched). The caller
    never receives the diff, and no field of the returned tuple can hold it.
    """
    if not patches:
        return "no changes", 0, 0, 0

    added = removed = functions = 0
    for patch in patches:
        original = (patch.get("original") or "").splitlines()
        patched = (patch.get("patched") or "").splitlines()
        # Line-count delta rather than a real diff: we only need magnitude, and
        # computing a diff here would mean holding both versions in memory
        # alongside a third representation of the same content.
        delta = len(patched) - len(original)
        added += max(0, delta)
        removed += max(0, -delta)
        changed = sum(1 for a, b in zip(original, patched) if a != b)
        added += changed
        functions += len(_DEF_RE.findall(patch.get("patched") or ""))

    files = len(patches)
    summary = (
        f"{files} file(s), +{added}/-{removed} line(s), {functions} function(s) in scope"
    )
    return summary, added, removed, functions


_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+\w+", re.MULTILINE)


# -- store -----------------------------------------------------------------


@dataclass
class RepairMemoryV2:
    """Append-only structured memory of completed repairs."""

    records: list[RepairKnowledge] = field(default_factory=list)

    # -- writing

    def record(self, knowledge: RepairKnowledge) -> RepairKnowledge:
        """Store a record, replacing any earlier one with the same id."""
        _assert_metadata_only(knowledge)
        self.records = [r for r in self.records if r.repair_id != knowledge.repair_id]
        self.records.append(knowledge)
        if len(self.records) > MAX_RECORDS:
            self.records = self.records[-MAX_RECORDS:]
        return knowledge

    def update_outcome(self, repair_id: str, outcome: OutcomeStatus) -> RepairKnowledge | None:
        record = self.get(repair_id)
        if record is None:
            return None
        record.outcome = outcome
        if outcome in ("merged", "production_success"):
            record.merge_status = "merged"
        if outcome in ("reverted", "rolled_back"):
            record.rolled_back = True
        return record

    def update_review(
        self,
        repair_id: str,
        decision: ReviewDecision,
        categories: list[str] | None = None,
    ) -> RepairKnowledge | None:
        record = self.get(repair_id)
        if record is None:
            return None
        record.reviewer_decision = decision
        if categories:
            record.review_categories = list(categories)  # type: ignore[assignment]
        return record

    # -- reading

    def get(self, repair_id: str) -> RepairKnowledge | None:
        for record in self.records:
            if record.repair_id == repair_id:
                return record
        return None

    def for_repository(self, repository_id: str) -> list[RepairKnowledge]:
        return [r for r in self.records if r.repository_id == repository_id]

    def for_category(self, bug_category: str) -> list[RepairKnowledge]:
        return [r for r in self.records if r.bug_category == bug_category]

    def for_signature(self, issue_sig: str) -> list[RepairKnowledge]:
        return [r for r in self.records if r.issue_signature == issue_sig]

    def successful(self) -> list[RepairKnowledge]:
        return [r for r in self.records if r.succeeded]

    def recent(self, limit: int = 10, repository_id: str | None = None) -> list[RepairKnowledge]:
        pool = self.for_repository(repository_id) if repository_id else self.records
        return sorted(pool, key=lambda r: r.recorded_at, reverse=True)[:limit]

    # -- aggregation

    def category_counts(self, repository_id: str | None = None) -> dict[str, int]:
        pool = self.for_repository(repository_id) if repository_id else self.records
        counts: dict[str, int] = {}
        for record in pool:
            counts[record.bug_category] = counts.get(record.bug_category, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def success_rate(self, bug_category: str | None = None) -> float:
        pool = self.for_category(bug_category) if bug_category else self.records
        decided = [r for r in pool if r.outcome in POSITIVE_OUTCOMES | NEGATIVE_OUTCOMES]
        if not decided:
            return 0.0
        positive = sum(1 for r in decided if r.outcome in POSITIVE_OUTCOMES)
        return round(positive / len(decided), 4)

    @property
    def repositories(self) -> list[str]:
        return sorted({r.repository_id for r in self.records if r.repository_id})


def _assert_metadata_only(knowledge: RepairKnowledge) -> None:
    """Guard against content reaching the learning store.

    The model has no field for source, so this catches the other route in: a
    caller stuffing a diff into a free-text field like `patch_summary`. A summary
    is one line; anything multi-line or long is content that escaped.
    """
    summary = knowledge.patch_summary or ""
    if "\n" in summary or len(summary) > 200:
        raise PrivacyViolation(
            "patch_summary must be a one-line shape description, not patch content"
        )
    for name in FORBIDDEN_FIELDS:
        if hasattr(knowledge, name):  # pragma: no cover — schema guarantees this
            raise PrivacyViolation(f"RepairKnowledge must not carry a '{name}' field")


def build_repair_knowledge(
    *,
    repair_id: str,
    run_id: str = "",
    repository_id: str = "",
    repository_hash: str = "",
    reproduction: dict | None = None,
    root_cause: dict | None = None,
    static_findings: list[dict] | None = None,
    patches: list[dict] | None = None,
    mutation_result: dict | None = None,
    security_result: dict | None = None,
    pr_decision: dict | None = None,
    context_metrics: dict | None = None,
    framework: str = "unknown",
    language: str = "python",
    retry_count: int = 0,
) -> RepairKnowledge:
    """Derive a record from a completed run's state. Content never survives."""
    reproduction = reproduction or {}
    root_cause = root_cause or {}
    mutation_result = mutation_result or {}
    security_result = security_result or {}
    pr_decision = pr_decision or {}
    context_metrics = context_metrics or {}
    patches = patches or []

    summary_text = str(root_cause.get("root_cause") or root_cause.get("summary") or "")
    exception_type = str(reproduction.get("exception_type") or "")
    static_rule = str((static_findings or [{}])[0].get("rule_id", "")) if static_findings else ""

    bug_category = classify_bug_category(exception_type, summary_text, static_rule)
    root_category = classify_root_cause(summary_text)

    patch_summary, added, removed, functions = summarize_patch(patches)
    files = [p.get("file", "") for p in patches if p.get("file")]

    return RepairKnowledge(
        repair_id=repair_id,
        run_id=run_id,
        repository_id=repository_id,
        repository_hash=repository_hash,
        issue_signature=issue_signature(bug_category, exception_type, root_category),
        bug_category=bug_category,
        root_cause_category=root_category,
        exception_type=exception_type.rsplit(".", 1)[-1],
        target_files=sorted(set(files)),
        target_functions=sorted({str(c.get("symbol", "")) for c in (root_cause.get("citations") or []) if c.get("symbol")}),
        file_count=len(set(files)),
        context_files=int(context_metrics.get("context_files") or 0),
        context_functions=int(context_metrics.get("context_functions") or 0),
        context_chars=int(context_metrics.get("context_lines") or 0),
        patch_summary=patch_summary,
        lines_added=added,
        lines_removed=removed,
        functions_touched=functions,
        validation_passed=bool(mutation_result.get("pytest_passed")),
        mutation_score=mutation_result.get("mutation_score"),
        mutation_status=str(mutation_result.get("mutation_status") or "not_run"),
        security_score=security_result.get("security_score"),
        security_rejected=bool(security_result.get("rejected")),
        retry_count=retry_count,
        framework=framework,
        language=language,
        pr_type=str(pr_decision.get("pr_type") or ""),
        recorded_at=datetime.utcnow(),
    )
