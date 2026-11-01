"""Schemas for the Enterprise Security Layer.

Every type here describes a *decision* or the *evidence* behind one. Nothing in
this layer may emit a verdict that cannot name the rule that produced it — a
security control a reviewer cannot audit is not a control.

Two conventions run throughout:

* **Findings carry location and detector, never the secret.** A `SecretFinding`
  records that an AWS key was found at line 12 by the `aws_access_key` detector.
  It does not record the key. Audit logs and dashboards are themselves a
  disclosure surface, and a security layer that stores what it redacts has
  moved the leak rather than closed it.

* **Decisions are `allow` / `sanitize` / `reject`, never a score.** Policy is a
  deterministic function of classification and request shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"

# ------------------------------------------------------------ classification

DataClassification = Literal[
    "PUBLIC",
    "PRIVATE",
    "CONFIDENTIAL",
    "RESTRICTED",
    "AIR_GAPPED",
]

# Ordered least to most restrictive. Index position is the comparison key, so
# "at least as restrictive as" is a single lookup.
CLASSIFICATION_ORDER: tuple[DataClassification, ...] = (
    "PUBLIC",
    "PRIVATE",
    "CONFIDENTIAL",
    "RESTRICTED",
    "AIR_GAPPED",
)


def classification_rank(value: str) -> int:
    """Position in the restrictiveness order. Unknown values rank most strict.

    Failing closed on an unrecognised classification is deliberate: a typo in
    configuration must not silently downgrade a repository to PUBLIC.
    """
    try:
        return CLASSIFICATION_ORDER.index(value)  # type: ignore[arg-type]
    except ValueError:
        return len(CLASSIFICATION_ORDER) - 1


LLMProviderName = Literal[
    "anthropic",
    "mistral",
    "gemini",
    "openai",
    "ollama",
    "lmstudio",
    "vllm",
    "tgi",
]

# Providers that run inside the customer's own infrastructure. The distinction
# is the whole point of the routing matrix: CONFIDENTIAL and above may only
# reach a provider on this list.
LOCAL_PROVIDERS: frozenset[str] = frozenset({"ollama", "lmstudio", "vllm", "tgi"})

Decision = Literal["allow", "sanitize", "reject"]

Severity = Literal["low", "medium", "high", "critical"]


# ---------------------------------------------------------------- findings


class SecretFinding(BaseModel):
    """One detected credential. Never carries the credential itself."""

    category: str
    detector: str
    file: str = ""
    line: int = 0
    identifier: str = ""
    severity: Severity = "high"
    redacted_as: str = ""

    def describe(self) -> str:
        location = f"{self.file}:{self.line}" if self.file else "context"
        return f"{self.category} detected at {location} by {self.detector}"


class PIIFinding(BaseModel):
    """One detected personal identifier. Never carries the value."""

    category: str
    detector: str
    file: str = ""
    line: int = 0
    severity: Severity = "medium"
    redacted_as: str = ""

    def describe(self) -> str:
        location = f"{self.file}:{self.line}" if self.file else "context"
        return f"{self.category} detected at {location} by {self.detector}"


class SanitizationFinding(BaseModel):
    """One removed organisational identifier — a hostname, domain, or marker."""

    category: str
    detector: str
    file: str = ""
    line: int = 0
    replaced_with: str = ""


class SanitizationReport(BaseModel):
    """What the privacy layer changed, in aggregate and in detail."""

    secrets: list[SecretFinding] = Field(default_factory=list)
    pii: list[PIIFinding] = Field(default_factory=list)
    sanitized: list[SanitizationFinding] = Field(default_factory=list)

    status: Literal["clean", "sanitized", "failed"] = "clean"
    failure_reason: str | None = None

    original_chars: int = 0
    sanitized_chars: int = 0
    duration_ms: int = 0

    @property
    def secret_count(self) -> int:
        return len(self.secrets)

    @property
    def pii_count(self) -> int:
        return len(self.pii)

    @property
    def total_findings(self) -> int:
        return len(self.secrets) + len(self.pii) + len(self.sanitized)

    def categories(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.secrets:
            counts[finding.category] = counts.get(finding.category, 0) + 1
        for finding in self.pii:
            counts[finding.category] = counts.get(finding.category, 0) + 1
        for finding in self.sanitized:
            counts[finding.category] = counts.get(finding.category, 0) + 1
        return dict(sorted(counts.items()))


# ------------------------------------------------------------------- policy


class SecurityPolicy(BaseModel):
    """What a classification permits. Every field is a hard limit."""

    name: str
    classification: DataClassification

    allowed_providers: list[str] = Field(default_factory=list)
    allowed_models: list[str] = Field(default_factory=list)  # prefixes; empty = any
    max_tokens: int = 4096
    max_context_chars: int = 200_000
    max_files: int = 50

    allowed_repositories: list[str] = Field(default_factory=list)  # empty = any
    allowed_file_types: list[str] = Field(default_factory=list)  # empty = any
    allowed_languages: list[str] = Field(default_factory=list)  # empty = any
    allowed_users: list[str] = Field(default_factory=list)  # empty = any

    require_sanitization: bool = True
    allow_pii: bool = False
    allow_secrets: bool = False
    egress_permitted: bool = True

    def permits_provider(self, provider: str) -> bool:
        return provider in self.allowed_providers

    def permits_model(self, model: str) -> bool:
        if not self.allowed_models:
            return True
        return any(model.startswith(prefix) for prefix in self.allowed_models)


class PolicyViolation(BaseModel):
    """One rule that failed, with the values that failed it."""

    rule: str
    detail: str
    severity: Severity = "high"
    observed: str = ""
    permitted: str = ""


class PolicyDecision(BaseModel):
    """The deterministic outcome of evaluating one request against one policy."""

    decision: Decision
    policy: str
    classification: DataClassification
    violations: list[PolicyViolation] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision != "reject"


class RoutingDecision(BaseModel):
    """Which provider and model a request was routed to, and why."""

    provider: str = ""
    model: str = ""
    is_local: bool = False
    classification: DataClassification = "PRIVATE"
    permitted: bool = False
    reason: str = ""
    rejected_providers: dict[str, str] = Field(default_factory=dict)


# ----------------------------------------------------------------- firewall


class FirewallVerdict(BaseModel):
    """Outcome of inspecting one outgoing prompt."""

    decision: Decision
    rule_results: dict[str, bool] = Field(default_factory=dict)
    violations: list[PolicyViolation] = Field(default_factory=list)
    prompt_chars: int = 0
    estimated_tokens: int = 0
    file_count: int = 0
    inspected_ms: int = 0

    @property
    def allowed(self) -> bool:
        return self.decision != "reject"

    @property
    def rules_failed(self) -> list[str]:
        return sorted(name for name, passed in self.rule_results.items() if not passed)


# -------------------------------------------------------------------- audit


class AuditEvent(BaseModel):
    """One immutable record of an LLM interaction.

    Raw prompts are never stored by default — only hashes. A prompt that had to
    be sanitized before egress must not be retained verbatim in a log that is
    itself readable by more people than the repository is.
    """

    event_id: str
    sequence: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    run_id: str = ""
    repository_hash: str = ""
    context_hash: str = ""
    prompt_hash: str = ""
    response_hash: str = ""

    provider: str = ""
    model: str = ""
    policy: str = ""
    classification: DataClassification = "PRIVATE"
    operation: str = "generic"
    actor: str = "pipeline"

    # Call attribution. Defaulted, so events written before these existed still
    # deserialize, and deliberately absent from `compute_entry_hash` so adding
    # them cannot invalidate an intact chain.
    agent_id: str = ""
    retry_count: int = 0
    attempts: int = 0

    files_included: list[str] = Field(default_factory=list)
    secret_count: int = 0
    pii_count: int = 0
    sanitization_count: int = 0
    sanitization_categories: dict[str, int] = Field(default_factory=dict)

    prompt_chars: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: int = 0

    decision: Decision = "allow"
    result: Literal["success", "rejected", "error"] = "success"
    failure_reason: str | None = None
    violations: list[str] = Field(default_factory=list)

    # Chain integrity. `previous_hash` links to the prior event; `entry_hash`
    # covers this event's content plus that link, so any retroactive edit breaks
    # verification from that point onward.
    previous_hash: str = ""
    entry_hash: str = ""

    # Only populated when `audit_store_raw_prompts` is explicitly enabled.
    raw_prompt: str | None = None


# --------------------------------------------------------------- compliance

ComplianceFramework = Literal[
    "SOC2",
    "ISO27001",
    "GDPR",
    "HIPAA",
    "PCI-DSS",
    "EU_AI_ACT",
    "NIST_AI_RMF",
]

ControlStatus = Literal["pass", "fail", "not_applicable"]


class ComplianceControl(BaseModel):
    """One control, its status, the evidence, and what to do if it failed."""

    control_id: str
    title: str
    status: ControlStatus = "pass"
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""


class ComplianceReport(BaseModel):
    framework: ComplianceFramework
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    controls: list[ComplianceControl] = Field(default_factory=list)
    events_examined: int = 0

    @property
    def passed(self) -> list[ComplianceControl]:
        return [c for c in self.controls if c.status == "pass"]

    @property
    def failed(self) -> list[ComplianceControl]:
        return [c for c in self.controls if c.status == "fail"]

    @property
    def compliant(self) -> bool:
        return not self.failed

    @property
    def score(self) -> float:
        applicable = [c for c in self.controls if c.status != "not_applicable"]
        if not applicable:
            return 1.0
        return round(len(self.passed) / len(applicable), 4)


# --------------------------------------------------------------- encryption


class EncryptedBlob(BaseModel):
    """AES-GCM ciphertext with everything needed to decrypt except the key."""

    version: str = "v1"
    key_id: str = ""
    nonce: str = ""       # base64
    ciphertext: str = ""  # base64
    algorithm: str = "AES-256-GCM"
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ------------------------------------------------------------- approved ctx


class ApprovedContext(BaseModel):
    """The only object the gateway will accept as a basis for an LLM call.

    Produced solely by `security_pipeline`. Carrying the policy decision, the
    routing decision and the sanitization report together means the gateway
    cannot be handed a sanitized package without its approval, or an approval
    without its evidence.
    """

    approved: bool = False
    run_id: str = ""
    context_hash: str = ""
    repository_hash: str = ""

    classification: DataClassification = "PRIVATE"
    policy_decision: PolicyDecision | None = None
    routing: RoutingDecision | None = None
    firewall: FirewallVerdict | None = None
    sanitization: SanitizationReport = Field(default_factory=SanitizationReport)

    prompt: str = ""
    system: str = ""
    files_included: list[str] = Field(default_factory=list)
    rejection_reason: str | None = None

    @property
    def rejected(self) -> bool:
        return not self.approved


# ------------------------------------------------------------------ metrics


class SecurityMetrics(BaseModel):
    """Dashboard counters for one process."""

    secrets_detected: int = 0
    pii_detected: int = 0
    contexts_sanitized: int = 0
    contexts_clean: int = 0
    policies_applied: dict[str, int] = Field(default_factory=dict)
    policy_violations: int = 0
    llm_calls: int = 0
    provider_usage: dict[str, int] = Field(default_factory=dict)
    estimated_cost_usd: float = 0.0
    total_prompt_chars: int = 0
    rejected_requests: int = 0
    firewall_rejections: dict[str, int] = Field(default_factory=dict)
    secret_categories: dict[str, int] = Field(default_factory=dict)
    pii_categories: dict[str, int] = Field(default_factory=dict)
    sanitization_ms: int = 0

    @property
    def average_prompt_chars(self) -> int:
        return int(self.total_prompt_chars / self.llm_calls) if self.llm_calls else 0

    @property
    def rejection_rate(self) -> float:
        total = self.llm_calls + self.rejected_requests
        return round(self.rejected_requests / total, 4) if total else 0.0
