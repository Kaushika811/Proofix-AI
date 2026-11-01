"""The mandatory gate between Context Engineering and the LLM Gateway.

Every LLM call in the pipeline passes through `approve()`. There is no second
path: `LLMGateway.complete()` requires an `ApprovedContext`, and the only thing
that can produce one is this module.

Six stages, in order, each able to reject:

    1. isolation     host paths and environment stripped from the prompt
    2. sanitize      secrets, PII and organisational identity removed
    3. policy        classification limits evaluated against the request
    4. route         a permitted provider selected, or refused
    5. firewall      the final bytes inspected
    6. audit         the decision recorded, allowed or not

Rejections are audited exactly as thoroughly as approvals. A security layer that
only logs what it permitted cannot answer the question an incident review asks
first, which is what it stopped.

**Backward compatibility.** The default classification is configurable and
defaults to PRIVATE, whose policy permits the providers the pipeline already
used. Existing agents call `LLMService` unchanged; the gate is transparent when
the content is clean, and only becomes visible when it has something to say.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from backend.config import Settings, get_settings
from backend.models.context import ContextPackage
from backend.models.security import (
    ApprovedContext,
    AuditEvent,
    SanitizationReport,
    SecurityMetrics,
)
from backend.security.audit_logger import AuditLogger, content_hash
from backend.security.compliance_engine import ComplianceContext, ComplianceEngine
from backend.security.encryption import EncryptionService
from backend.security.llm_router import LLMRouter
from backend.security.policy_engine import BUILTIN_POLICIES, PolicyEngine, PolicyRequest
from backend.security.privacy_guard import ContextPrivacyGuard, GuardOptions
from backend.security.prompt_firewall import PromptFirewall
from backend.security.repository_isolation import scrub_environment

logger = logging.getLogger(__name__)


@dataclass
class SecurityRequest:
    """One LLM call awaiting approval. Plain data."""

    prompt: str
    system: str = ""
    run_id: str = ""
    operation: str = "generic"
    actor: str = "pipeline"
    # Which agent asked, and which repair attempt it was on. Carried so a
    # rejection is attributable to a stage, not just to a run.
    agent_id: str = ""
    retry_count: int = 0
    repository: str = ""
    repository_hash: str = ""
    classification: str = ""
    files: tuple[str, ...] = ()
    languages: tuple[str, ...] = ("python",)
    context_package: ContextPackage | None = None
    preferred_provider: str = ""
    preferred_model: str = ""
    max_tokens: int = 0
    workspace_root: str = ""


class SecurityPipeline:
    """Composes every control into one decision."""

    def __init__(self, settings: Settings | None = None, store=None):
        self.settings = settings or get_settings()
        self.store = store
        self.policy_engine = PolicyEngine()
        self.router = LLMRouter(self.settings)
        self.encryption = EncryptionService.from_settings(self.settings)
        self.audit = AuditLogger(store=store, settings=self.settings, encryption=self.encryption)
        self.metrics = SecurityMetrics()
        self.guard_options = GuardOptions.from_settings(self.settings)

    # -- classification --------------------------------------------------

    def classification_for(self, request: SecurityRequest) -> str:
        """Explicit request value, else configured default, else PRIVATE."""
        return (
            request.classification
            or getattr(self.settings, "security_default_classification", "")
            or "PRIVATE"
        )

    # -- approval --------------------------------------------------------

    async def approve(self, request: SecurityRequest) -> ApprovedContext:
        """Run every control. Returns an approval or a reasoned rejection."""
        started = time.perf_counter()
        classification = self.classification_for(request)
        policy = self.policy_engine.resolve(classification)

        prompt = request.prompt
        system = request.system

        # -- 1. isolation: strip host and environment disclosure
        prompt = scrub_environment(prompt)
        system = scrub_environment(system)
        if request.workspace_root:
            from backend.security.repository_isolation import guard_for

            guard = guard_for(request.workspace_root)
            prompt = guard.scrub_paths(prompt)
            system = guard.scrub_paths(system)

        # -- 1b. structural inspection, BEFORE sanitization
        # A private key, a binary blob or a repository dump means context
        # assembly went wrong. Sanitizing first would erase the evidence and
        # approve a prompt that should never have been built.
        firewall = PromptFirewall(policy, self.guard_options.known_names)
        structural = firewall.pre_inspect(prompt, system, files=request.files, run_id=request.run_id)
        if not structural.allowed:
            for rule in structural.rules_failed:
                self.metrics.firewall_rejections[rule] = (
                    self.metrics.firewall_rejections.get(rule, 0) + 1
                )
            return await self._reject(
                request, classification,
                SanitizationReport(original_chars=len(prompt) + len(system)),
                content_hash(prompt),
                firewall=structural,
                reason="; ".join(v.detail for v in structural.violations),
            )

        # -- 2. sanitize
        report = SanitizationReport(original_chars=len(prompt) + len(system))
        if policy.require_sanitization or self.settings.security_always_sanitize:
            guard = ContextPrivacyGuard(self.guard_options)
            prompt = guard.sanitize_text(prompt, request.repository, report)
            system = guard.sanitize_text(system, request.repository, report)
            report.status = "sanitized" if report.total_findings else "clean"
        report.sanitized_chars = len(prompt) + len(system)

        context_hash = content_hash(prompt)

        # -- 3. policy
        policy_decision = self.policy_engine.evaluate(
            PolicyRequest(
                classification=classification,
                repository=request.repository,
                user=request.actor,
                files=request.files,
                languages=request.languages,
                context_chars=len(prompt) + len(system),
                max_tokens=request.max_tokens or self.settings.llm_max_tokens,
                secret_count=0,  # measured post-sanitization by the firewall
                pii_count=0,
                sanitized=True,
            )
        )
        self.metrics.policies_applied[policy.name] = (
            self.metrics.policies_applied.get(policy.name, 0) + 1
        )

        if not policy_decision.allowed:
            return await self._reject(
                request, classification, report, context_hash,
                policy_decision=policy_decision,
                reason="; ".join(v.detail for v in policy_decision.violations),
            )

        # -- 4. route
        routing = self.router.route(
            policy,
            preferred_provider=request.preferred_provider,
            preferred_model=request.preferred_model,
        )
        if not routing.permitted:
            return await self._reject(
                request, classification, report, context_hash,
                policy_decision=policy_decision,
                routing=routing,
                reason=routing.reason,
            )

        # -- 5. firewall: full inspection of the exact bytes going on the wire
        verdict = firewall.inspect(prompt, system, files=request.files, run_id=request.run_id)

        if not verdict.allowed:
            for rule in verdict.rules_failed:
                self.metrics.firewall_rejections[rule] = (
                    self.metrics.firewall_rejections.get(rule, 0) + 1
                )
            return await self._reject(
                request, classification, report, context_hash,
                policy_decision=policy_decision,
                routing=routing,
                firewall=verdict,
                reason="; ".join(v.detail for v in verdict.violations),
            )

        # -- 6. record the approval
        self._record_metrics(report, verdict, routing)
        approved = ApprovedContext(
            approved=True,
            run_id=request.run_id,
            context_hash=context_hash,
            repository_hash=request.repository_hash,
            classification=policy.classification,
            policy_decision=policy_decision,
            routing=routing,
            firewall=verdict,
            sanitization=report,
            prompt=prompt,
            system=system,
            files_included=list(request.files),
        )

        logger.info(
            "security_approved",
            extra={
                "security": {
                    "run_id": request.run_id,
                    "operation": request.operation,
                    "policy": policy.name,
                    "provider": routing.provider,
                    "secrets": report.secret_count,
                    "pii": report.pii_count,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                }
            },
        )
        return approved

    async def _reject(
        self,
        request: SecurityRequest,
        classification: str,
        report: SanitizationReport,
        context_hash: str,
        *,
        policy_decision=None,
        routing=None,
        firewall=None,
        reason: str = "",
    ) -> ApprovedContext:
        """Record a rejection with the same rigour as an approval."""
        self.metrics.rejected_requests += 1
        if policy_decision:
            self.metrics.policy_violations += len(policy_decision.violations)

        event = self.audit.build_event(
            run_id=request.run_id,
            repository_hash=request.repository_hash,
            context_hash=context_hash,
            prompt=request.prompt,
            provider=routing.provider if routing else "",
            model=routing.model if routing else "",
            policy=self.policy_engine.resolve(classification).name,
            classification=self.policy_engine.classification_for(classification),
            operation=request.operation,
            actor=request.actor,
            agent_id=request.agent_id,
            retry_count=request.retry_count,
            files=request.files,
            sanitization=report,
            policy_decision=policy_decision,
            firewall=firewall,
            routing=routing,
            decision="reject",
            result="rejected",
            failure_reason=reason,
        )
        await self.audit.record(event)

        return ApprovedContext(
            approved=False,
            run_id=request.run_id,
            context_hash=context_hash,
            repository_hash=request.repository_hash,
            classification=self.policy_engine.classification_for(classification),
            policy_decision=policy_decision,
            routing=routing,
            firewall=firewall,
            sanitization=report,
            rejection_reason=reason or "rejected by security policy",
        )

    def _record_metrics(self, report, verdict, routing) -> None:
        self.metrics.secrets_detected += report.secret_count
        self.metrics.pii_detected += report.pii_count
        self.metrics.sanitization_ms += report.duration_ms
        if report.total_findings:
            self.metrics.contexts_sanitized += 1
        else:
            self.metrics.contexts_clean += 1

        for category, count in report.categories().items():
            self.metrics.secret_categories[category] = (
                self.metrics.secret_categories.get(category, 0) + count
            )
        for finding in report.pii:
            self.metrics.pii_categories[finding.category] = (
                self.metrics.pii_categories.get(finding.category, 0) + 1
            )

        self.metrics.total_prompt_chars += verdict.prompt_chars
        if routing.provider:
            self.metrics.provider_usage[routing.provider] = (
                self.metrics.provider_usage.get(routing.provider, 0) + 1
            )

    # -- post-call --------------------------------------------------------

    async def record_completion(
        self,
        approved: ApprovedContext,
        *,
        response: str = "",
        operation: str = "generic",
        agent_id: str = "",
        retry_count: int = 0,
        attempts: int = 0,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        estimated_cost_usd: float | None = None,
        latency_ms: int = 0,
        success: bool = True,
        error: str | None = None,
    ) -> AuditEvent:
        """Audit the outcome of an approved call."""
        self.metrics.llm_calls += 1
        self.metrics.estimated_cost_usd += estimated_cost_usd or 0.0

        event = self.audit.build_event(
            run_id=approved.run_id,
            repository_hash=approved.repository_hash,
            context_hash=approved.context_hash,
            prompt=approved.prompt,
            response=response,
            provider=approved.routing.provider if approved.routing else "",
            model=approved.routing.model if approved.routing else "",
            policy=approved.policy_decision.policy if approved.policy_decision else "",
            classification=approved.classification,
            operation=operation,
            agent_id=agent_id,
            retry_count=retry_count,
            attempts=attempts,
            files=tuple(approved.files_included),
            sanitization=approved.sanitization,
            policy_decision=approved.policy_decision,
            firewall=approved.firewall,
            routing=approved.routing,
            decision="allow",
            result="success" if success else "error",
            failure_reason=error,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            latency_ms=latency_ms,
        )
        return await self.audit.record(event)

    # -- reporting --------------------------------------------------------

    def compliance_context(self, isolation_violations: int = 0) -> ComplianceContext:
        intact, detail = self.audit.verify_chain()
        return ComplianceContext(
            events=self.audit.events(limit=1000),
            policies=BUILTIN_POLICIES,
            encryption_enabled=self.encryption.enabled,
            audit_chain_intact=intact,
            audit_chain_detail=detail,
            raw_prompts_stored=bool(getattr(self.settings, "audit_store_raw_prompts", False)),
            sanitization_enforced=True,
            firewall_enabled=True,
            isolation_violations=isolation_violations,
            pii_detection_enabled=self.guard_options.detect_pii,
        )

    def compliance(self) -> ComplianceEngine:
        return ComplianceEngine(self.compliance_context())

    def dashboard(self) -> dict:
        """Every metric section 11 asks for, in one payload."""
        return {
            "secrets_detected": self.metrics.secrets_detected,
            "pii_detected": self.metrics.pii_detected,
            "contexts_sanitized": self.metrics.contexts_sanitized,
            "contexts_clean": self.metrics.contexts_clean,
            "policies_applied": dict(sorted(self.metrics.policies_applied.items())),
            "policy_violations": self.metrics.policy_violations,
            "llm_calls": self.metrics.llm_calls,
            "provider_usage": dict(sorted(self.metrics.provider_usage.items())),
            "estimated_cost_usd": round(self.metrics.estimated_cost_usd, 6),
            "average_prompt_chars": self.metrics.average_prompt_chars,
            "rejected_requests": self.metrics.rejected_requests,
            "rejection_rate": self.metrics.rejection_rate,
            "firewall_rejections": dict(sorted(self.metrics.firewall_rejections.items())),
            "secret_categories": dict(sorted(self.metrics.secret_categories.items())),
            "pii_categories": dict(sorted(self.metrics.pii_categories.items())),
            "sanitization_ms": self.metrics.sanitization_ms,
            "encryption": self.encryption.status(),
            "audit": self.audit.summary(),
            "compliance": self.compliance().summary(),
            "routing_matrix": self.router.routing_matrix(BUILTIN_POLICIES),
        }


# -- process-wide instance -------------------------------------------------
# One pipeline per process so metrics and the audit chain accumulate across
# calls. A per-call instance would reset the chain and make it unverifiable.

_pipeline: SecurityPipeline | None = None


def get_security_pipeline(settings: Settings | None = None, store=None) -> SecurityPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = SecurityPipeline(settings, store)
    if store is not None and _pipeline.store is None:
        _pipeline.store = store
        _pipeline.audit.store = store
    return _pipeline


def reset_security_pipeline() -> None:
    """Drop the process instance. For tests and deliberate reconfiguration."""
    global _pipeline
    _pipeline = None
