"""Track what happened to a repair after it was suggested.

Validation says a patch passed its tests. Outcome says a human merged it and it
did not get reverted three days later. Those are different facts, and only the
second one tells you whether the repair was actually right.

Outcomes are an **append-only history**, not a mutable status field. A repair
that was accepted, merged, and then rolled back has three recorded transitions,
and the rollback does not erase the acceptance — an approach that gets merged and
then reverted is a specific, important failure mode, distinguishable from one
that was rejected outright.

Success rates are damped by sample size everywhere they are used. An approach
with one merged repair is not 100% reliable, and `OutcomeStatistics.confidence`
says so.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from backend.models.learning import (
    NEGATIVE_OUTCOMES,
    POSITIVE_OUTCOMES,
    OutcomeRecord,
    OutcomeStatistics,
    OutcomeStatus,
    RepairKnowledge,
)

# Transitions that make sense. A repair cannot be merged before being accepted,
# and recording an impossible sequence would corrupt every statistic built on it.
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "suggested": frozenset({"accepted", "rejected", "modified"}),
    "modified": frozenset({"accepted", "rejected", "merged"}),
    "accepted": frozenset({"merged", "rejected", "modified"}),
    "merged": frozenset({"production_success", "production_failure", "reverted", "rolled_back"}),
    "rejected": frozenset({"modified", "accepted"}),
    "reverted": frozenset({"modified"}),
    "rolled_back": frozenset({"modified"}),
    "production_success": frozenset({"production_failure", "rolled_back"}),
    "production_failure": frozenset({"rolled_back", "modified"}),
}

MAX_HISTORY = 20_000


class InvalidTransition(ValueError):
    """Raised when a recorded outcome cannot follow the current one."""


def is_valid_transition(current: str, nxt: str) -> bool:
    return nxt in VALID_TRANSITIONS.get(current, frozenset())


@dataclass
class OutcomeLearner:
    """Append-only outcome history with aggregate statistics."""

    history: list[OutcomeRecord] = field(default_factory=list)
    _current: dict[str, str] = field(default_factory=dict)

    # -- writing ---------------------------------------------------------

    def record(
        self,
        repair_id: str,
        status: OutcomeStatus,
        detail: str = "",
        actor: str = "pipeline",
        strict: bool = False,
    ) -> OutcomeRecord:
        """Append a transition.

        `strict` raises on an impossible sequence. Off by default because an
        external signal (a webhook, a manual entry) may legitimately arrive out
        of order, and refusing it would lose the information entirely; the
        transition is still recorded so the anomaly stays visible.
        """
        current = self._current.get(repair_id, "suggested")
        if strict and current != status and not is_valid_transition(current, status):
            raise InvalidTransition(f"{repair_id}: cannot go from '{current}' to '{status}'")

        record = OutcomeRecord(
            repair_id=repair_id,
            status=status,
            detail=detail,
            actor=actor,
            recorded_at=datetime.utcnow(),
        )
        self.history.append(record)
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]
        self._current[repair_id] = status
        return record

    # -- reading ---------------------------------------------------------

    def current_status(self, repair_id: str) -> str:
        return self._current.get(repair_id, "suggested")

    def timeline(self, repair_id: str) -> list[OutcomeRecord]:
        return [r for r in self.history if r.repair_id == repair_id]

    def was_ever(self, repair_id: str, status: str) -> bool:
        """Whether a repair ever reached a status, even if it later left it."""
        return any(r.status == status for r in self.timeline(repair_id))

    # -- statistics ------------------------------------------------------

    def statistics(self, repair_ids: list[str] | None = None, key: str = "all") -> OutcomeStatistics:
        """Aggregate the *current* status of each repair, not every transition.

        Counting transitions would let a repair with a long history dominate the
        rate; what matters is where each one ended up.
        """
        ids = repair_ids if repair_ids is not None else list(self._current)
        statuses = [self._current.get(i, "suggested") for i in ids]

        stats = OutcomeStatistics(key=key, total=len(statuses))
        stats.by_status = dict(sorted(Counter(statuses).items()))
        stats.positive = sum(1 for s in statuses if s in POSITIVE_OUTCOMES)
        stats.negative = sum(1 for s in statuses if s in NEGATIVE_OUTCOMES)
        stats.pending = len(statuses) - stats.positive - stats.negative
        return stats

    def statistics_by(
        self,
        records: list[RepairKnowledge],
        attribute: str,
    ) -> dict[str, OutcomeStatistics]:
        """Statistics grouped by a record attribute — category, framework, repo."""
        groups: dict[str, list[str]] = {}
        for record in records:
            key = str(getattr(record, attribute, "") or "unknown")
            groups.setdefault(key, []).append(record.repair_id)

        return {
            key: self.statistics(ids, key=key)
            for key, ids in sorted(groups.items())
        }

    def success_rate_for(self, records: list[RepairKnowledge], attribute: str, value: str) -> float:
        matching = [r.repair_id for r in records if str(getattr(r, attribute, "")) == value]
        return self.statistics(matching, key=value).success_rate

    # -- ranking signal --------------------------------------------------

    def historical_success(
        self,
        records: list[RepairKnowledge],
        bug_category: str,
        framework: str = "",
    ) -> tuple[float, str]:
        """0..1 confidence that this kind of repair works, plus its explanation.

        Narrows to (category, framework) when there is enough evidence there,
        and falls back to category alone otherwise — a framework-specific rate
        from two samples is worse than a category rate from twenty.
        """
        by_category = [r for r in records if r.bug_category == bug_category]
        if not by_category:
            return 0.0, f"no prior repairs recorded for {bug_category}"

        if framework:
            narrowed = [r for r in by_category if r.framework == framework]
            stats = self.statistics([r.repair_id for r in narrowed], key=f"{bug_category}/{framework}")
            if stats.positive + stats.negative >= 3:
                return stats.confidence, (
                    f"{stats.positive}/{stats.positive + stats.negative} prior "
                    f"{bug_category} repairs succeeded in {framework}"
                )

        stats = self.statistics([r.repair_id for r in by_category], key=bug_category)
        decided = stats.positive + stats.negative
        if not decided:
            return 0.0, f"{len(by_category)} prior {bug_category} repair(s), none decided yet"
        return stats.confidence, (
            f"{stats.positive}/{decided} prior {bug_category} repair(s) succeeded"
        )

    def rollback_rate(self, records: list[RepairKnowledge] | None = None) -> float:
        """Share of merged repairs that were later reverted or rolled back.

        The metric that matters most for trust: a high merge rate with a high
        rollback rate is worse than a low merge rate.
        """
        merged = [i for i in self._current if self.was_ever(i, "merged")]
        if not merged:
            return 0.0
        reverted = sum(
            1 for i in merged
            if self._current.get(i) in ("reverted", "rolled_back", "production_failure")
        )
        return round(reverted / len(merged), 4)

    def summary(self) -> dict:
        stats = self.statistics()
        return {
            "tracked_repairs": len(self._current),
            "transitions": len(self.history),
            "by_status": stats.by_status,
            "success_rate": stats.success_rate,
            "confidence": stats.confidence,
            "rollback_rate": self.rollback_rate(),
            "pending": stats.pending,
        }
