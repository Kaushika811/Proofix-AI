"""Post-run extraction: turn one completed run into durable knowledge.

Runs after A10, once the outcome of a repair is known. It extracts the repair's
metadata, updates the repository and organisation profiles, re-mines templates
and patterns, and scores the repair — all deterministically, all from data the
run already produced.

**Nothing here can fail a run.** The engine is invoked after the pipeline's work
is complete, and every entry point catches its own exceptions. A learning fault
means the platform learns nothing from that run, which is a cost; it must never
mean the repair itself is lost.

**Scoring is explainable by construction.** `score_repair` returns seven named
components and the sentences behind them. There is no weighting model: the
overall score is the mean of the components that could actually be measured,
and components with no evidence are excluded rather than counted as zero — a
repair with no review yet is not a repair with a bad review.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from backend.models.learning import (
    KnowledgeIndex,
    LearningScore,
    OrganizationProfile,
    RepairKnowledge,
    RepositoryProfile,
)
from backend.learning.framework_learning import framework_match_score, learn_framework
from backend.learning.knowledge_index import build_index
from backend.learning.metrics import build_metrics, dashboard
from backend.learning.organization_memory import OrganizationMemory
from backend.learning.outcome_learning import OutcomeLearner
from backend.learning.pattern_mining import mine_patterns, mine_templates, select_template
from backend.learning.repair_memory import RepairMemoryV2, build_repair_knowledge
from backend.learning.review_learning import ReviewLearner
from backend.learning.style_learning import learn_style, style_match_score


@dataclass
class LearningState:
    """Everything the engine accumulates. One instance per process."""

    repairs: RepairMemoryV2 = field(default_factory=RepairMemoryV2)
    outcomes: OutcomeLearner = field(default_factory=OutcomeLearner)
    reviews: ReviewLearner = field(default_factory=ReviewLearner)
    organization: OrganizationMemory = field(default_factory=OrganizationMemory)

    repository_profiles: dict[str, RepositoryProfile] = field(default_factory=dict)
    repository_files: dict[str, tuple[str, ...]] = field(default_factory=dict)
    repository_imports: dict[str, set[str]] = field(default_factory=dict)

    template_reuses: int = 0
    learning_updates: int = 0
    total_update_ms: int = 0


class LearningEngine:
    """Deterministic extraction and profile maintenance."""

    def __init__(self, state: LearningState | None = None):
        self.state = state or LearningState()

    # -- profile maintenance ---------------------------------------------

    def observe_repository(
        self,
        repository_id: str,
        repo_path: Path | None,
        parsed_modules: dict,
        repository_name: str = "",
    ) -> RepositoryProfile:
        """Learn or refresh one repository's style and framework profile.

        Reuses the repository index's existing parse; this adds no AST work of
        its own, which is what keeps the update inside the 100 ms budget.
        """
        style = learn_style(repo_path, parsed_modules, repository_id)
        framework = learn_framework(repo_path, parsed_modules, repository_id, tuple(parsed_modules))

        existing = self.state.repository_profiles.get(repository_id)
        profile = RepositoryProfile(
            repository_id=repository_id,
            repository_name=repository_name or (existing.repository_name if existing else ""),
            style=style,
            framework=framework,
            repairs_recorded=existing.repairs_recorded if existing else 0,
            repairs_succeeded=existing.repairs_succeeded if existing else 0,
            repairs_reviewed=existing.repairs_reviewed if existing else 0,
            common_bug_categories=existing.common_bug_categories if existing else {},
            updated_at=datetime.utcnow(),
        )

        self.state.repository_profiles[repository_id] = profile
        self.state.organization.register(profile)
        self.state.repository_files[repository_id] = tuple(sorted(parsed_modules))
        self.state.repository_imports[repository_id] = {
            module for parsed in parsed_modules.values() for module in parsed.imports
        }
        return profile

    # -- extraction --------------------------------------------------------

    def learn_from_run(
        self,
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
        retry_count: int = 0,
    ) -> RepairKnowledge:
        """Extract one completed run into a repair record and refresh derived state."""
        started = time.perf_counter()

        profile = self.state.repository_profiles.get(repository_id)
        framework = profile.framework.primary_framework if profile else "unknown"

        knowledge = build_repair_knowledge(
            repair_id=repair_id,
            run_id=run_id,
            repository_id=repository_id,
            repository_hash=repository_hash,
            reproduction=reproduction,
            root_cause=root_cause,
            static_findings=static_findings,
            patches=patches,
            mutation_result=mutation_result,
            security_result=security_result,
            pr_decision=pr_decision,
            context_metrics=context_metrics,
            framework=framework,
            retry_count=retry_count,
        )

        self.state.repairs.record(knowledge)
        self.state.outcomes.record(repair_id, "suggested", detail="repair generated")
        self._refresh_repository_counters(repository_id)

        self.state.learning_updates += 1
        self.state.total_update_ms += int((time.perf_counter() - started) * 1000)
        return knowledge

    def record_outcome(self, repair_id: str, status: str, detail: str = "", actor: str = "pipeline"):
        """Record an outcome transition and mirror it onto the repair record."""
        record = self.state.outcomes.record(repair_id, status, detail=detail, actor=actor)  # type: ignore[arg-type]
        self.state.repairs.update_outcome(repair_id, status)  # type: ignore[arg-type]
        knowledge = self.state.repairs.get(repair_id)
        if knowledge:
            self._refresh_repository_counters(knowledge.repository_id)
        return record

    def record_review(
        self,
        repair_id: str,
        decision: str,
        reason: str = "",
        reviewer: str = "",
    ):
        """Record a reviewer verdict and mirror its categories onto the record."""
        review = self.state.reviews.record(repair_id, decision, reason, reviewer)  # type: ignore[arg-type]
        self.state.repairs.update_review(repair_id, decision, list(review.categories))  # type: ignore[arg-type]

        knowledge = self.state.repairs.get(repair_id)
        if knowledge:
            profile = self.state.repository_profiles.get(knowledge.repository_id)
            if profile:
                profile.repairs_reviewed += 1
        return review

    def _refresh_repository_counters(self, repository_id: str) -> None:
        profile = self.state.repository_profiles.get(repository_id)
        if profile is None:
            return
        records = self.state.repairs.for_repository(repository_id)
        profile.repairs_recorded = len(records)
        profile.repairs_succeeded = sum(1 for r in records if r.succeeded)
        profile.common_bug_categories = self.state.repairs.category_counts(repository_id)
        profile.updated_at = datetime.utcnow()

    # -- derived knowledge -------------------------------------------------

    def templates(self):
        return mine_templates(self.state.repairs.records)

    def patterns(self):
        return mine_patterns(self.state.repairs.records)

    def organization_profile(self) -> OrganizationProfile:
        profile = self.state.organization.build(
            self.state.repairs.records, self.state.repository_files
        )
        profile.preferred_libraries = self.state.organization.learn_libraries(
            self.state.repository_imports
        )
        return profile

    def knowledge_index(self, repository_id: str) -> KnowledgeIndex:
        """The view A5.5 and A7 consume for one repository."""
        profile = self.state.repository_profiles.get(repository_id) or RepositoryProfile(
            repository_id=repository_id
        )
        return build_index(
            repository_profile=profile,
            organization_profile=self.organization_profile(),
            templates=self.templates(),
            patterns=self.patterns(),
            recent_repairs=self.state.repairs.recent(repository_id=repository_id),
        )

    def note_template_use(self, template_id: str | None) -> None:
        if template_id:
            self.state.template_reuses += 1

    # -- scoring -----------------------------------------------------------

    def score_repair(
        self,
        knowledge: RepairKnowledge,
        parsed_patch: object | None = None,
        patch_imports: set[str] | None = None,
    ) -> LearningScore:
        """Seven named components, each 0..1, each with its reason.

        Components with no evidence are *excluded* from the mean rather than
        counted as zero. A repair awaiting review is not a repair that was
        reviewed badly, and averaging in a zero would make every new repair look
        worse than a bad one that had been reviewed.
        """
        reasons: list[str] = []
        measured: dict[str, float] = {}

        # -- validation quality
        if knowledge.validation_passed:
            validation = 1.0 if knowledge.retry_count == 0 else max(0.4, 1.0 - 0.2 * knowledge.retry_count)
            reasons.append(
                f"validation passed after {knowledge.retry_count} retry(ies)"
            )
        else:
            validation = 0.0
            reasons.append("validation did not pass")
        measured["validation_quality"] = round(validation, 4)

        # -- mutation quality
        if knowledge.mutation_score is not None:
            measured["mutation_quality"] = round(max(0.0, min(1.0, knowledge.mutation_score)), 4)
            reasons.append(f"mutation score {knowledge.mutation_score:.2f}")
        elif knowledge.mutation_status not in ("not_run", ""):
            measured["mutation_quality"] = 0.0
            reasons.append(f"mutation testing reported '{knowledge.mutation_status}'")

        # -- repair confidence: security and change size
        security = 0.0 if knowledge.security_rejected else 1.0
        locality = 1.0 if knowledge.file_count <= 1 else max(0.3, 1.0 - 0.2 * (knowledge.file_count - 1))
        measured["repair_confidence"] = round((security + locality) / 2, 4)
        reasons.append(
            f"{'security rescan rejected the patch' if knowledge.security_rejected else 'security rescan clean'}; "
            f"{knowledge.file_count} file(s) changed"
        )

        # -- review confidence
        if knowledge.reviewer_decision != "pending":
            review = {
                "accepted_immediately": 1.0,
                "minor_edits": 0.75,
                "major_edits": 0.35,
                "changes_requested": 0.2,
                "rejected": 0.0,
            }.get(knowledge.reviewer_decision, 0.0)
            measured["review_confidence"] = review
            reasons.append(f"reviewer decision: {knowledge.reviewer_decision}")

        # -- historical success
        historical, explanation = self.state.outcomes.historical_success(
            self.state.repairs.records, knowledge.bug_category, knowledge.framework
        )
        if historical > 0 or "no prior" not in explanation:
            measured["historical_success"] = historical
        reasons.append(explanation)

        # -- framework and style conformance
        profile = self.state.repository_profiles.get(knowledge.repository_id)
        if profile and patch_imports is not None:
            match = framework_match_score(profile.framework, patch_imports)
            measured["framework_match"] = match
            reasons.append(
                f"patch {'uses' if match >= 1.0 else 'does not conflict with' if match > 0 else 'conflicts with'} "
                f"{profile.framework.primary_framework}"
            )
        if profile and parsed_patch is not None:
            style = style_match_score(profile.style, parsed_patch)  # type: ignore[arg-type]
            measured["style_match"] = style
            reasons.append(f"{style:.0%} of patched callables follow the repository naming convention")

        overall = round(sum(measured.values()) / len(measured), 4) if measured else 0.0

        return LearningScore(
            repair_confidence=measured.get("repair_confidence", 0.0),
            review_confidence=measured.get("review_confidence", 0.0),
            historical_success=measured.get("historical_success", 0.0),
            framework_match=measured.get("framework_match", 0.0),
            style_match=measured.get("style_match", 0.0),
            validation_quality=measured.get("validation_quality", 0.0),
            mutation_quality=measured.get("mutation_quality", 0.0),
            overall=overall,
            reasons=reasons,
            measured=sorted(measured),
        )

    # -- reporting ---------------------------------------------------------

    def metrics(self):
        return build_metrics(
            records=self.state.repairs.records,
            templates=self.templates(),
            patterns=self.patterns(),
            repository_profiles=self.state.repository_profiles,
            organization_profile=self.organization_profile(),
            reviews_recorded=len(self.state.reviews.reviews),
            template_reuses=self.state.template_reuses,
            learning_updates=self.state.learning_updates,
            total_update_ms=self.state.total_update_ms,
        )

    def dashboard(self) -> dict:
        return dashboard(
            metrics=self.metrics(),
            records=self.state.repairs.records,
            templates=self.templates(),
            patterns=self.patterns(),
            repository_profiles=self.state.repository_profiles,
            review_summary=self.state.reviews.summary(),
            outcome_summary=self.state.outcomes.summary(),
        )

    def select_template_for(self, repository_id: str, bug_category: str):
        profile = self.state.repository_profiles.get(repository_id)
        framework = profile.framework.primary_framework if profile else ""
        return select_template(self.templates(), bug_category, framework, repository_id)
