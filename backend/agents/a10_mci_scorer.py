from pathlib import Path

from backend.agents.a10_routing import (
    compute_fidelity_score,
    compute_scope_risk,
    route_pr_decision,
)
from backend.agents.base import AgentBase
from backend.models.pr import AxisScores, PRRoutingDecision, PublicationResult
from backend.services.github_pr import GitHubPRService
from backend.services.draft_publication import retain_checkout
from backend.services.mci_verifier import generate_description_from_diff, verify_mci
from backend.services.proof_bundle import build_verification_bundle
from backend.services.issue_test_generation import generated_test_integrity_error
from backend.state.events import A10CompletedPayload
from backend.state.schema import RunStateModel


class A10MCIScorerAgent(AgentBase):
    agent_id = "A10"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "MCI verification and reviewability scoring")

        root_cause = state.root_cause or {}
        patch_bundle = state.patch_bundle or {}
        mutation = state.mutation_result or {}
        security = state.security_result or {}
        blast = state.blast_graph or {}

        description_why = root_cause.get("summary", "Security fix based on root cause analysis")
        diff_text = patch_bundle.get("diff_text", "")
        description_what = generate_description_from_diff(diff_text)

        # The "why" section describes the pre-existing defect and can cite a
        # failing test or caller that the patch need not change. Phantom-change
        # detection applies to the "what changed" section only. A4's separate
        # claim-support gate evaluates whether the diagnosis itself is proven.
        fidelity_ok, phantoms = verify_mci(description_what, diff_text)

        # No `0.0` defaults. An agent that never ran leaves its result empty,
        # and `.get(key, 0.0)` turned that absence into the worst possible
        # measured score — which was then averaged into the trust score and
        # printed as "security=0" for a scan that never happened.
        correctness = mutation.get("correctness_score")
        security_score = security.get("security_score")

        # Fidelity and scope risk are derived from state A10 always holds, so
        # they are measured whenever A10 runs. They take no absent branch.
        fidelity = compute_fidelity_score(fidelity_ok, state)
        scope_risk = compute_scope_risk(blast, state)

        axis = AxisScores(
            correctness=correctness,
            security=security_score,
            fidelity=fidelity,
            scope_risk=scope_risk,
        )

        pr_type, review_note = route_pr_decision(state, axis, phantoms)

        github = GitHubPRService(self.settings)
        title = f"[ProoFix] {root_cause.get('root_cause', 'Security fix')[:60]}"
        review_section = review_note or ""

        branch = f"proofix-fix-{state.run_id[:8]}"
        patch_files = {
            p["file"]: p["patched"]
            for p in patch_bundle.get("patches", [])
            if p.get("file") and p.get("patched") is not None
        }

        preliminary_bundle = build_verification_bundle(state)

        test_integrity_error = generated_test_integrity_error(
            Path(state.repo_clone_path or state.repo_path), state.generated_test,
        )
        if test_integrity_error:
            state.generated_test.update(after_passed=None, validation_detail=test_integrity_error)
        no_targeted_patch = bool(state.issue_contract and not patch_files)
        if no_targeted_patch or test_integrity_error:
            # A selected issue without a candidate is an analysis result, not
            # a pull request. Keep its proof available in state without
            # publishing an empty change.
            pr_type = "draft"
            review_note = (f"{test_integrity_error}. No PR was created." if test_integrity_error else
                           "No patch generated for the selected issue. No PR was created.")
            publication = PublicationResult(
                status="disabled", reason=review_note,
            )
            final_bundle = preliminary_bundle
        else:
            if state.generated_test and state.generated_test.get("status") == "generated":
                patch_files[state.generated_test["path"]] = state.generated_test["content"]
            try:
                raw_result, final_bundle = github.publish_fix_with_proof(
                    repo_path=state.repo_clone_path or state.repo_path,
                    branch=branch,
                    patch_files=patch_files,
                    bundle=preliminary_bundle,
                    commit_message=title,
                    title=title,
                    draft=True,
                    prepare_only=True,
                    why_section=description_why,
                    what_section=description_what,
                    review_section=review_section,
                    expected_source_hashes=(mutation.get("patch_causality") or {}).get("source_hashes"),
                )
                publication = raw_result if isinstance(raw_result, PublicationResult) else PublicationResult(
                    status="failed", reason="Publisher returned no structured publication result",
                )
            except Exception as exc:
                publication = PublicationResult(
                    status="failed", reason=f"GitHub publication failed ({type(exc).__name__})",
                )
                final_bundle = preliminary_bundle

        if publication.status == "published" and not all((
            publication.pr_url, publication.tested_patch_sha,
            publication.published_head_sha, publication.proof_artifact_hash,
        )):
            publication = PublicationResult(
                status="failed", tested_patch_sha=publication.tested_patch_sha,
                published_head_sha=publication.published_head_sha,
                proof_artifact_hash=publication.proof_artifact_hash,
                reason="Publisher did not return a complete verified publication identity",
            )

        if publication.status == "failed":
            review_note = " ".join(filter(None, [review_note, publication.reason,
                                                  "Repair evidence is retained but unpublished."]))
        elif publication.status in {"disabled", "dry_run"} and not (no_targeted_patch or test_integrity_error):
            review_note = " ".join(filter(None, [review_note, publication.reason,
                                                  "No GitHub PR was published."]))

        pr_url = publication.pr_url if publication.status == "published" else None
        state.publication_result = publication.model_dump(mode="json")
        if publication.status == "ready":
            retain_checkout(state.repo_clone_path or state.repo_path, self.settings.state_ttl_seconds)

        bundle_dict = final_bundle.model_dump(mode="json")
        state.proof_bundle = bundle_dict
        await self.store.set_json(state.run_id, f"proof:{final_bundle.issue_id}", bundle_dict)

        decision = PRRoutingDecision(
            pr_type=pr_type,  # type: ignore[arg-type]
            axis_scores=axis,
            pr_url=pr_url,
            description_why=description_why,
            description_what=description_what,
            review_note=review_note,
            phantom_changes_detected=bool(phantoms),
            publication=state.publication_result,
        )
        decision_dict = decision.model_dump(mode="json")
        state.pr_decision = decision_dict
        state.status = "completed"

        completed_payload = A10CompletedPayload(
            pr_type=pr_type,  # type: ignore[arg-type]
            axis_scores=axis,
            pr_url=pr_url,
            proof_bundle_hash=final_bundle.bundle_hash,
            reproduction_confidence=final_bundle.reproduction_confidence,
        )
        await self.emit_status(
            state,
            "completed",
            review_note or f"PR routed as {pr_type}",
            completed_payload.model_dump(mode="json"),
        )
        return state
