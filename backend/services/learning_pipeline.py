"""Integration facade for the Organizational Learning System.

Sits between Enterprise Security and the LLM Gateway, exactly as specified. That
position has a concrete consequence worth stating: everything learning
contributes to a prompt is *still* sanitized by the security layer before egress.
Learned directives are conventions, not content, so in practice nothing is
redacted — but the ordering means a learning bug could never become a disclosure.

Two responsibilities:

* **Before a repair** — supply the knowledge index as prompt context. Advisory
  only: no ranking weight, no gate, no threshold.
* **After a repair** — extract the run into durable metadata and refresh the
  profiles.

Everything is failure-isolated. `learning_disabled` returns empty context and
records nothing, and every method catches its own exceptions: a learning fault
degrades the platform to its pre-Phase-6 behaviour rather than failing a run.
"""

from __future__ import annotations

import logging
from pathlib import Path

from backend.config import Settings, get_settings
from backend.learning.knowledge_index import prompt_context, render_directives
from backend.learning.learning_engine import LearningEngine, LearningState
from backend.models.learning import LearningScore, RepairKnowledge

logger = logging.getLogger(__name__)

LEARNING_STORE_KEY = "learning"


class LearningPipeline:
    """Process-wide learning, exposed as two safe entry points."""

    def __init__(self, settings: Settings | None = None, store=None):
        self.settings = settings or get_settings()
        self.store = store
        self.engine = LearningEngine(LearningState())

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "learning_enabled", True))

    # -- before a repair ---------------------------------------------------

    def observe_repository(
        self,
        repository_id: str,
        repo_path: Path | None,
        parsed_modules: dict,
        repository_name: str = "",
    ):
        """Refresh style and framework profiles from an existing parse."""
        if not self.enabled or not parsed_modules:
            return None
        try:
            return self.engine.observe_repository(
                repository_id, repo_path, parsed_modules, repository_name
            )
        except Exception as exc:  # noqa: BLE001 — learning never fails a run
            logger.warning("learning_observe_failed", extra={"learning_error": str(exc)})
            return None

    def context_for(self, repository_id: str, bug_category: str = "") -> dict:
        """Prompt context for an imminent repair. Empty when learning is off."""
        if not self.enabled:
            return {"directives": [], "sources": [], "template_id": None}
        try:
            index = self.engine.knowledge_index(repository_id)
            context = prompt_context(
                index,
                bug_category=bug_category,
                reviewer_guardrails=self.engine.state.reviews.guardrails(),
                max_directives=self.settings.learning_max_directives,
            )
            self.engine.note_template_use(context.get("template_id"))
            return context
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning_context_failed", extra={"learning_error": str(exc)})
            return {"directives": [], "sources": [], "template_id": None}

    def directive_block(self, repository_id: str, bug_category: str = "") -> str:
        """The context rendered as a prompt block, or "" when there is nothing."""
        return render_directives(self.context_for(repository_id, bug_category))

    # -- after a repair ----------------------------------------------------

    def learn_from_run(self, state, repository_id: str = "") -> RepairKnowledge | None:
        """Extract a completed run. Reads `RunStateModel` without modifying it."""
        if not self.enabled:
            return None
        try:
            patches = (state.patch_bundle or {}).get("patches") or []
            if not patches:
                return None

            knowledge = self.engine.learn_from_run(
                repair_id=f"{state.run_id}:{patches[0].get('file', '-')}",
                run_id=state.run_id,
                repository_id=repository_id or _repository_id_for(state),
                repository_hash="",
                reproduction=state.reproduction,
                root_cause=state.root_cause,
                static_findings=(state.static_report or {}).get("prioritized"),
                patches=patches,
                mutation_result=state.mutation_result,
                security_result=state.security_result,
                pr_decision=state.pr_decision,
                context_metrics=(state.__dict__.get("context_metrics") or {}),
                retry_count=state.retry_count,
            )

            decision = (state.pr_decision or {}).get("pr_type", "")
            if decision == "auto_mergeable":
                self.engine.record_outcome(knowledge.repair_id, "accepted", "auto-mergeable")
            return knowledge
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning_extract_failed", extra={"learning_error": str(exc)})
            return None

    def record_outcome(self, repair_id: str, status: str, detail: str = "", actor: str = "pipeline"):
        if not self.enabled:
            return None
        try:
            return self.engine.record_outcome(repair_id, status, detail, actor)
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning_outcome_failed", extra={"learning_error": str(exc)})
            return None

    def record_review(self, repair_id: str, decision: str, reason: str = "", reviewer: str = ""):
        if not self.enabled:
            return None
        try:
            return self.engine.record_review(repair_id, decision, reason, reviewer)
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning_review_failed", extra={"learning_error": str(exc)})
            return None

    def score(self, knowledge: RepairKnowledge, **kwargs) -> LearningScore:
        try:
            return self.engine.score_repair(knowledge, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("learning_score_failed", extra={"learning_error": str(exc)})
            return LearningScore()

    # -- reporting ---------------------------------------------------------

    def dashboard(self) -> dict:
        if not self.enabled:
            return {"enabled": False}
        return {"enabled": True, **self.engine.dashboard()}


def _repository_id_for(state) -> str:
    """Reuse the Phase 3 repository identity so learning keys match the index."""
    from backend.services.repair_memory import repository_id

    return repository_id(state.repo_path or state.repo_clone_path or "")


# -- process-wide instance -------------------------------------------------
# One pipeline per process, so profiles and statistics accumulate across runs.
# That accumulation is the entire point of the layer.

_pipeline: LearningPipeline | None = None


def get_learning_pipeline(settings: Settings | None = None, store=None) -> LearningPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = LearningPipeline(settings, store)
    if store is not None and _pipeline.store is None:
        _pipeline.store = store
    return _pipeline


def reset_learning_pipeline() -> None:
    """Drop the process instance. For tests and deliberate reconfiguration."""
    global _pipeline
    _pipeline = None
