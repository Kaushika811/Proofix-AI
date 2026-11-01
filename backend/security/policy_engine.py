"""Deterministic policy evaluation.

Five classifications, each a fixed set of hard limits. Evaluation is a pure
function of (policy, request): the same inputs always produce the same decision,
the same violations, in the same order. Nothing is scored, weighted or inferred.

Three principles the defaults encode:

**Fail closed.** An unrecognised classification resolves to the most restrictive
policy, not the least. A typo in configuration must not silently downgrade a
repository to PUBLIC.

**Egress is a property of classification, not of convenience.** CONFIDENTIAL and
above may only route to a provider running inside the customer's infrastructure.
AIR_GAPPED forbids network egress outright — there is no provider list that
satisfies it except local ones, and `egress_permitted=False` says so explicitly
rather than relying on the list being correct.

**Sanitization is mandatory everywhere except PUBLIC.** A policy that permitted
unsanitized CONFIDENTIAL context would make every other control decorative.
"""

from __future__ import annotations

from backend.models.security import (
    CLASSIFICATION_ORDER,
    DataClassification,
    Decision,
    PolicyDecision,
    PolicyViolation,
    SecurityPolicy,
    classification_rank,
)

# Providers reachable over the public internet, and those that are not.
EXTERNAL_PROVIDERS = ("anthropic", "mistral", "gemini", "openai")
LOCAL_PROVIDERS = ("ollama", "lmstudio", "vllm", "tgi")


BUILTIN_POLICIES: dict[str, SecurityPolicy] = {
    "PUBLIC": SecurityPolicy(
        name="PUBLIC",
        classification="PUBLIC",
        allowed_providers=[*EXTERNAL_PROVIDERS, *LOCAL_PROVIDERS],
        max_tokens=8192,
        max_context_chars=400_000,
        max_files=100,
        require_sanitization=False,
        allow_pii=False,   # never permitted, at any classification
        allow_secrets=False,
        egress_permitted=True,
    ),
    "PRIVATE": SecurityPolicy(
        name="PRIVATE",
        classification="PRIVATE",
        allowed_providers=["anthropic", "mistral", *LOCAL_PROVIDERS],
        max_tokens=8192,
        max_context_chars=200_000,
        max_files=50,
        require_sanitization=True,
        allow_pii=False,
        allow_secrets=False,
        egress_permitted=True,
    ),
    "CONFIDENTIAL": SecurityPolicy(
        name="CONFIDENTIAL",
        classification="CONFIDENTIAL",
        allowed_providers=list(LOCAL_PROVIDERS),
        max_tokens=4096,
        max_context_chars=100_000,
        max_files=25,
        allowed_file_types=[".py", ".pyi", ".toml", ".cfg", ".ini", ".txt", ".md"],
        require_sanitization=True,
        allow_pii=False,
        allow_secrets=False,
        egress_permitted=False,
    ),
    "RESTRICTED": SecurityPolicy(
        name="RESTRICTED",
        classification="RESTRICTED",
        allowed_providers=["ollama", "vllm"],
        allowed_models=["llama", "qwen", "codellama", "deepseek", "mistral"],
        max_tokens=2048,
        max_context_chars=40_000,
        max_files=10,
        allowed_file_types=[".py", ".pyi"],
        allowed_languages=["python"],
        require_sanitization=True,
        allow_pii=False,
        allow_secrets=False,
        egress_permitted=False,
    ),
    "AIR_GAPPED": SecurityPolicy(
        name="AIR_GAPPED",
        classification="AIR_GAPPED",
        allowed_providers=["ollama", "vllm", "lmstudio", "tgi"],
        allowed_models=["llama", "codellama", "qwen", "deepseek"],
        max_tokens=2048,
        max_context_chars=20_000,
        max_files=5,
        allowed_file_types=[".py", ".pyi"],
        allowed_languages=["python"],
        require_sanitization=True,
        allow_pii=False,
        allow_secrets=False,
        egress_permitted=False,
    ),
}

# Resolved when configuration names something unknown. Most restrictive wins.
FAIL_CLOSED_POLICY = "AIR_GAPPED"


class PolicyRequest:
    """One request to evaluate. Plain data, no pipeline dependency."""

    def __init__(
        self,
        *,
        classification: str = "PRIVATE",
        provider: str = "",
        model: str = "",
        repository: str = "",
        user: str = "",
        files: tuple[str, ...] = (),
        languages: tuple[str, ...] = (),
        context_chars: int = 0,
        max_tokens: int = 0,
        secret_count: int = 0,
        pii_count: int = 0,
        sanitized: bool = False,
    ):
        self.classification = classification
        self.provider = provider
        self.model = model
        self.repository = repository
        self.user = user
        self.files = files
        self.languages = languages
        self.context_chars = context_chars
        self.max_tokens = max_tokens
        self.secret_count = secret_count
        self.pii_count = pii_count
        self.sanitized = sanitized


class PolicyEngine:
    """Resolves policies and evaluates requests against them."""

    def __init__(self, policies: dict[str, SecurityPolicy] | None = None):
        self.policies = dict(policies or BUILTIN_POLICIES)

    # -- resolution ------------------------------------------------------

    def resolve(self, classification: str) -> SecurityPolicy:
        """Policy for a classification, failing closed on anything unknown."""
        name = (classification or "").strip().upper()
        return self.policies.get(name) or self.policies[FAIL_CLOSED_POLICY]

    def classification_for(self, classification: str) -> DataClassification:
        return self.resolve(classification).classification

    def is_at_least(self, classification: str, floor: str) -> bool:
        return classification_rank(classification) >= classification_rank(floor)

    # -- evaluation ------------------------------------------------------

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        """Evaluate one request. Every failing rule is reported, not just the first.

        Reporting all violations matters operationally: a caller that fixes one
        rejection reason only to hit the next has learned nothing about whether
        the request is fundamentally permitted.
        """
        policy = self.resolve(request.classification)
        violations: list[PolicyViolation] = []
        reasons: list[str] = []

        self._check_provider(policy, request, violations)
        self._check_model(policy, request, violations)
        self._check_limits(policy, request, violations)
        self._check_scope(policy, request, violations)
        self._check_content(policy, request, violations, reasons)

        decision: Decision = "reject" if violations else "allow"
        if decision == "allow" and policy.require_sanitization and not request.sanitized:
            decision = "sanitize"
            reasons.append(f"{policy.name} requires sanitization before egress")

        if not violations and not reasons:
            reasons.append(f"{policy.name} permits this request")

        return PolicyDecision(
            decision=decision,
            policy=policy.name,
            classification=policy.classification,
            violations=violations,
            reasons=reasons,
        )

    # -- individual rules ------------------------------------------------

    def _check_provider(self, policy, request, violations) -> None:
        if not request.provider:
            return
        if not policy.permits_provider(request.provider):
            violations.append(
                PolicyViolation(
                    rule="provider_not_allowed",
                    detail=f"{policy.name} does not permit provider '{request.provider}'",
                    severity="critical",
                    observed=request.provider,
                    permitted=", ".join(policy.allowed_providers),
                )
            )
        if not policy.egress_permitted and request.provider in EXTERNAL_PROVIDERS:
            violations.append(
                PolicyViolation(
                    rule="egress_forbidden",
                    detail=f"{policy.name} forbids network egress; '{request.provider}' is external",
                    severity="critical",
                    observed=request.provider,
                    permitted="local providers only",
                )
            )

    def _check_model(self, policy, request, violations) -> None:
        if request.model and not policy.permits_model(request.model):
            violations.append(
                PolicyViolation(
                    rule="model_not_allowed",
                    detail=f"{policy.name} does not permit model '{request.model}'",
                    severity="high",
                    observed=request.model,
                    permitted=", ".join(policy.allowed_models) or "any",
                )
            )

    def _check_limits(self, policy, request, violations) -> None:
        if request.context_chars > policy.max_context_chars:
            violations.append(
                PolicyViolation(
                    rule="context_too_large",
                    detail=f"context is {request.context_chars} chars, limit {policy.max_context_chars}",
                    severity="high",
                    observed=str(request.context_chars),
                    permitted=str(policy.max_context_chars),
                )
            )
        if request.max_tokens > policy.max_tokens:
            violations.append(
                PolicyViolation(
                    rule="max_tokens_exceeded",
                    detail=f"requested {request.max_tokens} tokens, limit {policy.max_tokens}",
                    severity="medium",
                    observed=str(request.max_tokens),
                    permitted=str(policy.max_tokens),
                )
            )
        if len(request.files) > policy.max_files:
            violations.append(
                PolicyViolation(
                    rule="too_many_files",
                    detail=f"{len(request.files)} files included, limit {policy.max_files}",
                    severity="high",
                    observed=str(len(request.files)),
                    permitted=str(policy.max_files),
                )
            )

    def _check_scope(self, policy, request, violations) -> None:
        if policy.allowed_repositories and request.repository:
            if request.repository not in policy.allowed_repositories:
                violations.append(
                    PolicyViolation(
                        rule="repository_not_allowed",
                        detail=f"repository '{request.repository}' is not on the allowlist",
                        severity="critical",
                        observed=request.repository,
                        permitted=", ".join(policy.allowed_repositories),
                    )
                )

        if policy.allowed_users and request.user:
            if request.user not in policy.allowed_users:
                violations.append(
                    PolicyViolation(
                        rule="user_not_allowed",
                        detail=f"user '{request.user}' is not permitted under {policy.name}",
                        severity="critical",
                        observed=request.user,
                        permitted=", ".join(policy.allowed_users),
                    )
                )

        if policy.allowed_file_types:
            permitted = {t.lower() for t in policy.allowed_file_types}
            offending = sorted(
                {f for f in request.files if not any(f.lower().endswith(t) for t in permitted)}
            )
            if offending:
                violations.append(
                    PolicyViolation(
                        rule="file_type_not_allowed",
                        detail=f"{len(offending)} file(s) of a type {policy.name} does not permit",
                        severity="high",
                        observed=", ".join(offending[:5]),
                        permitted=", ".join(sorted(permitted)),
                    )
                )

        if policy.allowed_languages and request.languages:
            permitted = {lang.lower() for lang in policy.allowed_languages}
            offending = sorted({lang for lang in request.languages if lang.lower() not in permitted})
            if offending:
                violations.append(
                    PolicyViolation(
                        rule="language_not_allowed",
                        detail=f"language(s) {', '.join(offending)} not permitted under {policy.name}",
                        severity="medium",
                        observed=", ".join(offending),
                        permitted=", ".join(sorted(permitted)),
                    )
                )

    def _check_content(self, policy, request, violations, reasons) -> None:
        """Residual secrets and PII after sanitization are always fatal.

        `allow_secrets` and `allow_pii` are False in every built-in policy. They
        exist as fields so a custom policy can be inspected for them, not so
        that one can be written to permit them.
        """
        if request.secret_count and not policy.allow_secrets:
            if request.sanitized:
                violations.append(
                    PolicyViolation(
                        rule="residual_secrets",
                        detail=f"{request.secret_count} secret(s) still present after sanitization",
                        severity="critical",
                        observed=str(request.secret_count),
                        permitted="0",
                    )
                )
            else:
                reasons.append(f"{request.secret_count} secret(s) must be redacted before egress")

        if request.pii_count and not policy.allow_pii:
            if request.sanitized:
                violations.append(
                    PolicyViolation(
                        rule="residual_pii",
                        detail=f"{request.pii_count} PII value(s) still present after sanitization",
                        severity="critical",
                        observed=str(request.pii_count),
                        permitted="0",
                    )
                )
            else:
                reasons.append(f"{request.pii_count} PII value(s) must be redacted before egress")


def default_engine() -> PolicyEngine:
    return PolicyEngine()


def policy_names() -> list[str]:
    return list(CLASSIFICATION_ORDER)
