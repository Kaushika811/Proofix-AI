"""Repository-agnostic patch planning and generation for A7."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from backend.models.blast import BlastGraphResult
from backend.models.patch import BehavioralContract, PatchPlan
from backend.models.root_cause import RootCauseBrief
from backend.models.validation import RetryBrief
from backend.services.runtime_patch_prompt import (
    build_runtime_patch_prompt,
    enrich_patch_plan_from_runtime,
    uses_runtime_prompt,
)


class PatchLLMOutput(BaseModel):
    patched_content: str
    contract_assertion: str
    contract_location: str


def build_patch_plans(
    scope_files: list[str],
    root_cause: RootCauseBrief,
    blast: BlastGraphResult,
    retry_brief: RetryBrief | None,
    reproduction: dict | None = None,
    repo_path: Path | None = None,
    file_sources: dict[str, str] | None = None,
    issue_contract: dict | None = None,
) -> list[PatchPlan]:
    """Build evidence-driven patch plans from upstream agent outputs."""
    plans: list[PatchPlan] = []
    file_citations = {c.file: c for c in root_cause.citations}
    sources = file_sources or {}

    for file_path in scope_files:
        file_citation = file_citations.get(file_path)
        citation_claim = file_citation.claim if file_citation else ""

        scoped = [s for s in blast.scope if s.path == file_path]
        blast_lines = [
            f"{s.direction} hops={s.hop_count} confidence={s.propagation_confidence:.2f} risk={s.risk_score:.2f}"
            for s in scoped
        ]
        blast_context = "; ".join(blast_lines) if blast_lines else "in blast graph scope"

        security_constraints: list[str] = []
        validation_goals: list[str] = []

        if retry_brief:
            if retry_brief.security_constraint:
                security_constraints.append(retry_brief.security_constraint)
            if retry_brief.violated_contract:
                validation_goals.append(f"Must satisfy: {retry_brief.violated_contract}")
            if retry_brief.assertion_failure:
                validation_goals.append(f"Address test failure: {retry_brief.assertion_failure}")
            if retry_brief.stack_trace:
                validation_goals.append("Fix must align with validation stack trace evidence")

        required_change = root_cause.root_cause or root_cause.summary
        if citation_claim:
            required_change = f"{required_change}. File-specific: {citation_claim}"

        plan = PatchPlan(
            file=file_path,
            root_cause=root_cause.root_cause or root_cause.summary,
            required_behavior_change=required_change,
            security_constraints=security_constraints,
            validation_goals=validation_goals,
            stack_evidence=(root_cause.stack_evidence or "")[:2000],
            blast_context=blast_context,
        )
        source = sources.get(file_path, "")
        enriched = enrich_patch_plan_from_runtime(
                plan,
                root_cause,
                reproduction,
                blast,
                source=source,
                repo_path=repo_path,
            )
        if issue_contract:
            enriched.validation_goals.append(
                f"Developer-selected test must pass: {issue_contract['target_test']}"
            )
            if issue_contract.get("expected_behavior"):
                enriched.expected_behavior = (
                    "Developer-reported expectation (not yet verified): "
                    + issue_contract["expected_behavior"]
                )
        plans.append(enriched)

    return plans


def build_llm_prompt(plan: PatchPlan, original: str, style_exemplar: str, repo_context: str = "") -> str:
    if uses_runtime_prompt(plan):
        return build_runtime_patch_prompt(plan, original, style_exemplar, repo_context)

    constraints = "\n".join(f"- {c}" for c in plan.security_constraints) or "- None"
    goals = "\n".join(f"- {g}" for g in plan.validation_goals) or "- Patch must resolve root cause without regressions"

    return f"""Generate a complete fixed version of this Python file.

## Patch Plan
File: {plan.file}
Root cause: {plan.root_cause}
Required behavior change: {plan.required_behavior_change}
Blast graph context: {plan.blast_context}

## Security constraints
{constraints}

## Validation goals
{goals}

## Stack evidence
{plan.stack_evidence[:1500]}

## Style exemplar (recent git history for this file)
{style_exemplar[:1500]}

## Original file content
{original[:4000]}

Return the complete patched file content and a behavioral contract assertion describing what must hold after the fix."""


def contract_from_plan(plan: PatchPlan) -> BehavioralContract:
    assertion = plan.validation_goals[0] if plan.validation_goals else (
        plan.acceptance_criteria
        if plan.acceptance_criteria
        else f"After fix, {plan.file} must address: {plan.required_behavior_change}"
    )
    return BehavioralContract(assertion=assertion, location=plan.file)


def apply_stub_plan(plan: PatchPlan, original: str) -> PatchLLMOutput:
    """
    Stub-mode generation using plan evidence and file content only (no path heuristics).

    Returns original content unchanged with contract derived from the plan.
    """
    return PatchLLMOutput(
        patched_content=original,
        contract_assertion=(
            plan.validation_goals[0]
            if plan.validation_goals
            else plan.acceptance_criteria
            or f"After fix, behavior must change: {plan.required_behavior_change}"
        ),
        contract_location=plan.file,
    )
