from pathlib import Path

from pydantic import BaseModel

from backend.agents.base import AgentBase
from backend.models.investigation import RootCauseSource
from backend.models.root_cause import Citation, RootCauseBrief
from backend.orchestrator.trust_gating import MAX_REINVESTIGATIONS
from backend.security.repository_isolation import HOST_PATH_RE, scrub_environment
from backend.services.citation_verifier import (
    coerce_llm_citations,
    verify_all_citations_with_metrics,
)
from backend.services.claim_support import (
    assess_claim_support, blocking_evidence_gap, diagnosis_status,
)
from backend.services.evidence_investigation import build_investigation_report
from backend.services.llm import LLMService
from backend.services.root_cause_builder import (
    build_runtime_snapshot,
    collect_evidence_refs,
    compute_confidence_breakdown,
    synthesize_root_cause_summary,
)
from backend.state.schema import RunStateModel


class RootCauseLLMOutput(BaseModel):
    summary: str
    root_cause: str
    citations: list[dict]
    affected_modules: list[str]


class A4EvidenceInvestigatorAgent(AgentBase):
    agent_id = "A4"

    async def run(self, state: RunStateModel) -> RunStateModel:
        await self.emit_status(state, "started", "Investigating root cause with trace evidence")
        repo = Path(state.repo_clone_path or state.repo_path)
        reproduction = state.reproduction or {}
        static = state.static_report or {}
        cve_report = state.cve_report or {}
        stack = reproduction.get("traceback") or reproduction.get("stack_trace", "") or ""
        if state.retry_count and state.validation_failure:
            latest = (state.validation_failure.get("traceback") or
                      state.validation_failure.get("assertion_message"))
            if latest:
                stack = f"Post-patch validation failure:\n{latest[:2500]}\nOriginal reproduction:\n{stack}"
        findings = static.get("prioritized", [])

        evidence_refs, cve_context, draft_citations = collect_evidence_refs(
            stack, findings, cve_report, reproduction, repo
        )
        runtime_snapshot = build_runtime_snapshot(reproduction)

        prior = state.root_cause or {}
        prior_count = int(prior.get("reinvestigation_count", 0))

        # Which path produced the brief is real provenance the investigation
        # report publishes — a deterministic brief and an LLM brief are
        # different evidence, and the UI is entitled to say which one it read.
        source: RootCauseSource = "deterministic"
        investigation_errors: list[str] = []

        if self.settings.stub_mode or not self.settings.llm_configured():
            brief = self._stub_brief(
                stack, findings, repo, reproduction, evidence_refs, cve_context, draft_citations
            )
        else:
            try:
                brief = await self._llm_brief(
                    stack, findings, cve_report, reproduction, evidence_refs, cve_context, repo,
                    run_id=state.run_id, retry_count=state.retry_count,
                    issue_contract=state.issue_contract,
                )
                source = "llm"
            except Exception as exc:  # noqa: BLE001 — degrade, never fail the run
                # A4 was the only LLM agent without this guard, and it cost a
                # whole run: Django's prompt tripped the firewall
                # (`SecurityRejection: host_path`) and the exception propagated
                # out of the graph, failing a repository that had been analysed
                # successfully up to that point. A6 and A7 already fall back;
                # the deterministic brief is right here and produces real
                # evidence refs and citations.
                state.errors.append({"agent": "A4", "error": f"{type(exc).__name__}: {exc}"})
                investigation_errors.append(
                    f"LLM investigation unavailable ({type(exc).__name__}: {exc}); "
                    "the root cause below is the deterministic analysis."
                )
                await self.emit_status(
                    state,
                    "retry",
                    f"LLM investigation unavailable ({type(exc).__name__}); "
                    "falling back to deterministic root-cause analysis",
                )
                brief = self._stub_brief(
                    stack, findings, repo, reproduction, evidence_refs, cve_context,
                    draft_citations,
                )

        brief.reinvestigation_count = prior_count
        brief.evidence_refs = evidence_refs
        brief.runtime_evidence = runtime_snapshot
        brief.cve_context = cve_context

        validated, citation_metrics = verify_all_citations_with_metrics(
            repo,
            [c.model_dump() for c in brief.citations],
            sig=state.sig,
        )
        assessed = []
        assessments = []
        for citation in validated:
            verdict = assess_claim_support(
                repo, citation, issue_contract=state.issue_contract,
                reproduction=state.reproduction, acceptance_test=state.acceptance_test,
            )
            assessments.append(verdict)
            assessed.append({**citation, "claim_support_status": verdict.status,
                             "claim_support_reason": verdict.reason})
        brief.citations = [Citation(**c) for c in assessed]
        brief.diagnosis_status = diagnosis_status(brief.root_cause, assessed)
        if state.issue_contract and blocking_evidence_gap(assessments):
            brief.evidence_incomplete = True
        # Location alone must not raise confidence in the diagnosis.
        verified_count = (sum(1 for c in brief.citations if c.claim_support_status == "supported")
                          if brief.diagnosis_status == "supported" else 0)
        brief.confidence, confidence_components = compute_confidence_breakdown(
            evidence_refs, verified_count, reproduction
        )

        unverified = [c for c in brief.citations
                      if not c.verified or c.claim_support_status == "contradicted"]
        if not brief.citations:
            # No citation is an absence of evidence, not a clean investigation.
            brief.evidence_incomplete = True
            state.reinvestigation_exhausted = True
        if unverified:
            if prior_count < MAX_REINVESTIGATIONS:
                brief.reinvestigation_required = True
                brief.reinvestigation_count = prior_count + 1
            else:
                brief.reinvestigation_required = False
                # `evidence_incomplete` is the observation; the draft decision
                # that follows from it belongs to `trust_gating`, which derives
                # it from this field rather than being told.
                brief.evidence_incomplete = True
                state.reinvestigation_exhausted = True
        else:
            brief.reinvestigation_required = False

        brief_dict = brief.model_dump(mode="json")
        state.root_cause = brief_dict

        # The audit of the brief: which sources answered, what they said, and
        # how the confidence above was arrived at. Deterministic and derived
        # entirely from artifacts A2/A3/A3.5 already persisted — no second
        # analysis, no LLM call, nothing invented to fill a field.
        report = build_investigation_report(
            brief=brief,
            static_report=state.static_report,
            reproduction=state.reproduction,
            cve_report=state.cve_report,
            confidence_components=confidence_components,
            root_cause_source=source,
            errors=investigation_errors,
        )
        state.investigation = report.model_dump(mode="json")

        await self.emit_status(
            state,
            "completed",
            brief.summary[:100],
            {
                "citations": len(brief.citations),
                "reinvestigation": brief.reinvestigation_required,
                "confidence": brief.confidence,
                "evidence_refs": len(brief.evidence_refs),
                "citation_metrics": citation_metrics,
                # Counts only — the report itself is served by
                # `GET /api/runs/{id}/investigation` rather than copied onto
                # every event, where it would roll off with the event stream.
                "investigation_status": report.status,
                "evidence_items": len(report.evidence),
                "supporting_evidence": len(report.supporting),
                "contradicting_evidence": len(report.contradicting),
            },
        )
        return state

    def _stub_brief(
        self,
        stack: str,
        findings: list,
        repo: Path,
        reproduction: dict,
        evidence_refs: list,
        cve_context: list[str],
        draft_citations: list[Citation],
    ) -> RootCauseBrief:
        summary, root_cause = synthesize_root_cause_summary(evidence_refs, cve_context, reproduction)
        citations = draft_citations or []
        if not citations and findings:
            f = findings[0]
            citations = [
                Citation(
                    file=f["file"],
                    line=f.get("line", 1),
                    claim=f.get("message", "issue"),
                    verified=False,
                )
            ]

        return RootCauseBrief(
            summary=summary,
            root_cause=root_cause,
            citations=citations,
            stack_evidence=stack[:2000],
            affected_modules=sorted({c.file for c in citations}),
        )

    async def _llm_brief(
        self,
        stack: str,
        findings: list,
        cve_report: dict,
        reproduction: dict,
        evidence_refs: list,
        cve_context: list[str],
        repo: Path,
        *,
        run_id: str = "",
        retry_count: int = 0,
        issue_contract: dict | None = None,
    ) -> RootCauseBrief:
        llm = LLMService(
            self.settings, run_id=run_id, agent_id=self.agent_id, retry_count=retry_count
        )
        critical_cves = [
            f"{r.get('cve_id')} ({r.get('package')})"
            for r in cve_report.get("findings", [])
            if r.get("classification") == "Critical"
        ]
        prompt = f"""Analyze root cause using ALL evidence sources. Every claim must cite file:line.

Developer-reported issue (request, not verified evidence):
{issue_contract or 'No specific issue supplied; analyze repository findings.'}

Stack trace:
{stack[:3000]}

Static findings:
{findings[:8]}

Runtime reproduction:
{reproduction}

Critical CVEs:
{critical_cves[:10]}

Evidence references:
{[r.model_dump() for r in evidence_refs]}

CVE context IDs: {cve_context}

Return citations as JSON objects with non-null string "file", integer "line" (>=1), and string "claim".
Omit citations you cannot anchor to a concrete file and line.
"""
        # Every field above is interpolated raw, and the traceback and the
        # reproduction dict both carry the clone's absolute path — which is a
        # host path, which the prompt firewall rejects outright. Left unscrubbed
        # this branch could never reach a provider on *any* repository: a real
        # run against the vulnapi fixture failed with
        # `SecurityRejection: prompt exposes repository or host internals:
        # host_path` and fell through to the deterministic brief every time.
        # `<PATH>` keeps the file *names* the model must cite while removing the
        # deployment layout it must not see.
        prompt = HOST_PATH_RE.sub("<PATH>", scrub_environment(prompt))
        output = await llm.structured(prompt, RootCauseLLMOutput)
        raw_citations = coerce_llm_citations(output.citations, evidence_refs)
        return RootCauseBrief(
            summary=output.summary,
            root_cause=output.root_cause,
            citations=[Citation(**c) for c in raw_citations],
            stack_evidence=stack[:2000],
            affected_modules=output.affected_modules,
        )
