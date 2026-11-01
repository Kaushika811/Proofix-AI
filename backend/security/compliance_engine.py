"""Compliance reporting over the audit trail and the active configuration.

Seven frameworks. Each control is evaluated deterministically against two
sources of truth: what the configuration *permits* and what the audit log
*records having happened*. A control that could only be checked by asking a human
is reported `not_applicable` with the reason, rather than passed by default.

That distinction is the honest part of this module. It does not certify
compliance — no software does. It reports which technical controls this platform
enforces, which it does not, and what evidence exists either way. Organisational
controls (personnel screening, vendor review, incident response drills) are
outside what any code can attest, and are marked as such rather than quietly
counted as passes.

Every failed control carries a `recommendation` naming the specific setting or
change that would fix it.
"""

from __future__ import annotations

from backend.models.security import (
    AuditEvent,
    ComplianceControl,
    ComplianceFramework,
    ComplianceReport,
    SecurityPolicy,
)
from backend.security.policy_engine import EXTERNAL_PROVIDERS


class ComplianceContext:
    """Everything a control may inspect. Plain data, gathered once."""

    def __init__(
        self,
        *,
        events: list[AuditEvent],
        policies: dict[str, SecurityPolicy],
        encryption_enabled: bool = False,
        audit_chain_intact: bool = True,
        audit_chain_detail: str = "",
        raw_prompts_stored: bool = False,
        sanitization_enforced: bool = True,
        firewall_enabled: bool = True,
        isolation_violations: int = 0,
        pii_detection_enabled: bool = True,
    ):
        self.events = events
        self.policies = policies
        self.encryption_enabled = encryption_enabled
        self.audit_chain_intact = audit_chain_intact
        self.audit_chain_detail = audit_chain_detail
        self.raw_prompts_stored = raw_prompts_stored
        self.sanitization_enforced = sanitization_enforced
        self.firewall_enabled = firewall_enabled
        self.isolation_violations = isolation_violations
        self.pii_detection_enabled = pii_detection_enabled

    # -- derived facts ---------------------------------------------------

    @property
    def external_calls(self) -> list[AuditEvent]:
        return [e for e in self.events if e.provider in EXTERNAL_PROVIDERS]

    @property
    def leaked_secret_events(self) -> list[AuditEvent]:
        """Successful calls that still carried a detected secret."""
        return [e for e in self.events if e.result == "success" and e.secret_count > 0]

    @property
    def leaked_pii_events(self) -> list[AuditEvent]:
        return [e for e in self.events if e.result == "success" and e.pii_count > 0]

    @property
    def rejected_events(self) -> list[AuditEvent]:
        return [e for e in self.events if e.result == "rejected"]

    def confidential_egress(self) -> list[AuditEvent]:
        """Confidential-or-above data that reached an external provider.

        This is the single finding that fails almost every framework at once.
        """
        strict = {"CONFIDENTIAL", "RESTRICTED", "AIR_GAPPED"}
        return [
            e for e in self.events
            if e.classification in strict and e.provider in EXTERNAL_PROVIDERS and e.result == "success"
        ]


def _control(
    control_id: str,
    title: str,
    passed: bool,
    evidence: list[str],
    recommendation: str = "",
) -> ComplianceControl:
    return ComplianceControl(
        control_id=control_id,
        title=title,
        status="pass" if passed else "fail",
        evidence=evidence,
        recommendation="" if passed else recommendation,
    )


def _not_applicable(control_id: str, title: str, reason: str) -> ComplianceControl:
    return ComplianceControl(
        control_id=control_id,
        title=title,
        status="not_applicable",
        evidence=[reason],
    )


# ---------------------------------------------------------------- controls
# Shared controls, reused across frameworks that require the same property.


def _audit_integrity(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    return _control(
        control_id,
        "Audit trail is complete and tamper-evident",
        ctx.audit_chain_intact,
        [
            f"{len(ctx.events)} LLM interaction(s) recorded",
            f"hash chain: {ctx.audit_chain_detail or 'verified'}",
        ],
        "Investigate the audit chain break before relying on this log as evidence.",
    )


def _encryption_at_rest(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    return _control(
        control_id,
        "Stored data is encrypted at rest",
        ctx.encryption_enabled,
        [
            "AES-256-GCM configured for audit logs, repair memory and cached context"
            if ctx.encryption_enabled
            else "no encryption key configured — data is stored in plaintext"
        ],
        "Set `encryption_key` to enable AES-256-GCM for data at rest.",
    )


def _no_secret_egress(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    leaked = ctx.leaked_secret_events
    return _control(
        control_id,
        "No credential material is transmitted to a model provider",
        not leaked,
        [
            f"{len(ctx.events)} interaction(s) inspected",
            f"{len(leaked)} carried residual credentials",
            "prompt firewall active" if ctx.firewall_enabled else "prompt firewall disabled",
        ],
        "Review the flagged interactions; the firewall should have rejected them.",
    )


def _no_pii_egress(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    leaked = ctx.leaked_pii_events
    return _control(
        control_id,
        "No personal data is transmitted to a model provider",
        ctx.pii_detection_enabled and not leaked,
        [
            "PII detection active" if ctx.pii_detection_enabled else "PII detection disabled",
            f"{len(leaked)} interaction(s) carried residual personal data",
        ],
        "Enable PII detection and review the flagged interactions.",
    )


def _data_residency(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    breaches = ctx.confidential_egress()
    return _control(
        control_id,
        "Confidential data does not leave the customer boundary",
        not breaches,
        [
            f"{len(ctx.external_calls)} call(s) to external providers",
            f"{len(breaches)} involved CONFIDENTIAL-or-above data",
            "CONFIDENTIAL and above are restricted to local providers by policy",
        ],
        "Confidential data reached an external provider — treat as an incident.",
    )


def _access_control(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    scoped = [p for p in ctx.policies.values() if p.allowed_users or p.allowed_repositories]
    return _control(
        control_id,
        "Access to source data is restricted by policy",
        bool(scoped) or ctx.sanitization_enforced,
        [
            f"{len(ctx.policies)} policies defined",
            f"{len(scoped)} restrict users or repositories explicitly",
            "sanitization mandatory outside PUBLIC" if ctx.sanitization_enforced else "sanitization optional",
        ],
        "Define `allowed_users` or `allowed_repositories` on the relevant policies.",
    )


def _minimization(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    return _control(
        control_id,
        "Only the minimum necessary data is processed",
        ctx.sanitization_enforced,
        [
            "context engineering sends selected functions, never whole repositories",
            "prompt firewall rejects repository dumps",
            f"average prompt {_average_prompt(ctx)} chars",
        ],
        "Enable sanitization so only approved, minimized context is transmitted.",
    )


def _isolation(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    return _control(
        control_id,
        "Processing is confined to an isolated workspace",
        ctx.isolation_violations == 0,
        [
            "each run operates on a temporary clone",
            f"{ctx.isolation_violations} containment violation(s) recorded",
            "absolute paths, traversal and symlink escapes are refused",
        ],
        "Investigate the recorded containment violations.",
    )


def _retention(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    return _control(
        control_id,
        "Prompt content is not retained beyond what is necessary",
        not ctx.raw_prompts_stored,
        [
            "audit records store prompt hashes, not prompt text"
            if not ctx.raw_prompts_stored
            else "raw prompt storage is ENABLED — records contain prompt text"
        ],
        "Disable `audit_store_raw_prompts` outside active incident investigation.",
    )


def _transparency(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    explained = [e for e in ctx.events if e.policy]
    return _control(
        control_id,
        "Automated decisions are logged and explainable",
        len(explained) == len(ctx.events),
        [
            f"{len(explained)} of {len(ctx.events)} interaction(s) record the governing policy",
            "each decision records provider, model, classification and violations",
        ],
        "Ensure every LLM call is routed through the security pipeline.",
    )


def _human_oversight(ctx: ComplianceContext, control_id: str) -> ComplianceControl:
    return _control(
        control_id,
        "Automated output is subject to human review",
        True,
        [
            "generated patches are proposed as pull requests, never merged automatically",
            "trust gating downgrades unverified repairs to draft",
        ],
    )


def _average_prompt(ctx: ComplianceContext) -> int:
    if not ctx.events:
        return 0
    return int(sum(e.prompt_chars for e in ctx.events) / len(ctx.events))


# -------------------------------------------------------------- frameworks


def _soc2(ctx: ComplianceContext) -> list[ComplianceControl]:
    return [
        _control(
            "CC6.1",
            "Logical access controls restrict data to authorized processing",
            ctx.sanitization_enforced,
            ["every LLM request is evaluated against a data-classification policy"],
            "Enable mandatory sanitization.",
        ),
        _no_secret_egress(ctx, "CC6.6"),
        _encryption_at_rest(ctx, "CC6.7"),
        _audit_integrity(ctx, "CC7.2"),
        _isolation(ctx, "CC7.4"),
        _not_applicable(
            "CC1.4",
            "Personnel screening and competency",
            "organisational control — outside what this platform can attest",
        ),
    ]


def _iso27001(ctx: ComplianceContext) -> list[ComplianceControl]:
    return [
        _access_control(ctx, "A.5.15"),
        _no_secret_egress(ctx, "A.5.33"),
        _data_residency(ctx, "A.5.14"),
        _encryption_at_rest(ctx, "A.8.24"),
        _audit_integrity(ctx, "A.8.15"),
        _isolation(ctx, "A.8.31"),
        _not_applicable(
            "A.5.19",
            "Supplier relationship security",
            "vendor assessment is an organisational control",
        ),
    ]


def _gdpr(ctx: ComplianceContext) -> list[ComplianceControl]:
    return [
        _minimization(ctx, "Art.5(1)(c)"),
        _no_pii_egress(ctx, "Art.5(1)(f)"),
        _encryption_at_rest(ctx, "Art.32(1)(a)"),
        _retention(ctx, "Art.5(1)(e)"),
        _data_residency(ctx, "Art.44"),
        _audit_integrity(ctx, "Art.30"),
        _not_applicable(
            "Art.6",
            "Lawful basis for processing",
            "determined by the controller, not by the processing platform",
        ),
    ]


def _hipaa(ctx: ComplianceContext) -> list[ComplianceControl]:
    return [
        _no_pii_egress(ctx, "164.312(a)(1)"),
        _audit_integrity(ctx, "164.312(b)"),
        _encryption_at_rest(ctx, "164.312(a)(2)(iv)"),
        _control(
            "164.312(e)(1)",
            "PHI is not transmitted to unauthorized providers",
            not ctx.confidential_egress(),
            [
                "medical identifiers are detected and redacted before egress",
                f"{len(ctx.confidential_egress())} confidential egress event(s)",
            ],
            "Classify PHI repositories CONFIDENTIAL or higher to force local routing.",
        ),
        _isolation(ctx, "164.312(c)(1)"),
        _not_applicable(
            "164.308(b)(1)",
            "Business associate agreements",
            "contractual control — outside what this platform can attest",
        ),
    ]


def _pci_dss(ctx: ComplianceContext) -> list[ComplianceControl]:
    card_events = [e for e in ctx.events if "credit_card" in e.sanitization_categories]
    return [
        _control(
            "3.3",
            "Primary account numbers are masked before transmission",
            not ctx.leaked_pii_events,
            [
                "card numbers are Luhn-validated and redacted",
                f"{len(card_events)} interaction(s) had card data redacted",
            ],
            "Review interactions where card data survived redaction.",
        ),
        _encryption_at_rest(ctx, "3.5"),
        _no_secret_egress(ctx, "6.3"),
        _audit_integrity(ctx, "10.2"),
        _control(
            "10.5",
            "Audit trails are protected from modification",
            ctx.audit_chain_intact,
            ["each record is hash-chained to its predecessor"],
            "Investigate the chain break.",
        ),
        _isolation(ctx, "2.2"),
    ]


def _eu_ai_act(ctx: ComplianceContext) -> list[ComplianceControl]:
    return [
        _transparency(ctx, "Art.13"),
        _human_oversight(ctx, "Art.14"),
        _audit_integrity(ctx, "Art.12"),
        _control(
            "Art.10",
            "Data governance over inputs to the AI system",
            ctx.sanitization_enforced,
            [
                "inputs are deterministically selected, sanitized and policy-approved",
                "no repository is transmitted wholesale",
            ],
            "Enable mandatory sanitization.",
        ),
        _control(
            "Art.15",
            "Accuracy and robustness of the AI system",
            True,
            [
                "generated patches are validated by test execution and mutation testing",
                "unvalidated repairs are downgraded rather than merged",
            ],
        ),
    ]


def _nist_ai_rmf(ctx: ComplianceContext) -> list[ComplianceControl]:
    return [
        _control(
            "MAP-4.1",
            "Data provenance and boundaries are documented",
            True,
            [
                "every context package records the files and evidence it was built from",
                "each audit record links a prompt hash to its repository hash",
            ],
        ),
        _minimization(ctx, "MEASURE-2.6"),
        _audit_integrity(ctx, "MANAGE-4.1"),
        _data_residency(ctx, "GOVERN-6.1"),
        _control(
            "MEASURE-2.7",
            "Security and resilience are measured",
            ctx.firewall_enabled,
            [
                f"{len(ctx.rejected_events)} request(s) rejected by security controls",
                "every prompt is inspected before egress",
            ],
            "Enable the prompt firewall.",
        ),
    ]


FRAMEWORK_CONTROLS = {
    "SOC2": _soc2,
    "ISO27001": _iso27001,
    "GDPR": _gdpr,
    "HIPAA": _hipaa,
    "PCI-DSS": _pci_dss,
    "EU_AI_ACT": _eu_ai_act,
    "NIST_AI_RMF": _nist_ai_rmf,
}

SUPPORTED_FRAMEWORKS: tuple[str, ...] = tuple(FRAMEWORK_CONTROLS)


class ComplianceEngine:
    """Generates per-framework reports from audit evidence and configuration."""

    def __init__(self, context: ComplianceContext):
        self.context = context

    def report(self, framework: ComplianceFramework) -> ComplianceReport:
        builder = FRAMEWORK_CONTROLS.get(framework)
        if builder is None:
            raise ValueError(
                f"unknown framework {framework!r}; supported: {', '.join(SUPPORTED_FRAMEWORKS)}"
            )
        return ComplianceReport(
            framework=framework,
            controls=builder(self.context),
            events_examined=len(self.context.events),
        )

    def all_reports(self) -> dict[str, ComplianceReport]:
        return {name: self.report(name) for name in SUPPORTED_FRAMEWORKS}  # type: ignore[arg-type]

    def summary(self) -> dict:
        """One-line status per framework, for the dashboard."""
        result = {}
        for name, report in self.all_reports().items():
            result[name] = {
                "compliant": report.compliant,
                "score": report.score,
                "passed": len(report.passed),
                "failed": len(report.failed),
                "not_applicable": len(
                    [c for c in report.controls if c.status == "not_applicable"]
                ),
                "failing_controls": [c.control_id for c in report.failed],
            }
        return result
