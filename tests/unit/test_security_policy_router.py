"""Policy evaluation and provider routing.

The tests that matter most are the ones proving a routing violation cannot
happen: CONFIDENTIAL data must never reach an external provider, whatever the
caller asked for and whatever is configured.
"""

import pytest

from backend.config import Settings
from backend.models.security import CLASSIFICATION_ORDER, classification_rank
from backend.security.llm_router import (
    DEFAULT_MODELS,
    PROVIDER_PREFERENCE,
    LLMRouter,
    classification_zone,
    is_local,
)
from backend.security.policy_engine import (
    BUILTIN_POLICIES,
    EXTERNAL_PROVIDERS,
    FAIL_CLOSED_POLICY,
    LOCAL_PROVIDERS,
    PolicyEngine,
    PolicyRequest,
)


@pytest.fixture
def engine():
    return PolicyEngine()


def external_settings(**overrides) -> Settings:
    return Settings(anthropic_api_key="k", mistral_api_key="k", **overrides)


def local_settings(**overrides) -> Settings:
    return Settings(ollama_base_url="http://localhost:11434", vllm_base_url="http://localhost:8000", **overrides)


def no_provider_settings() -> Settings:
    """Every credential explicitly cleared.

    `Settings()` reads the developer's `.env`, so a bare constructor may arrive
    with real keys and make an "unconfigured" test pass for the wrong reason.
    """
    from backend.security.llm_router import CREDENTIAL_FIELDS

    return Settings(**{field: "" for field in CREDENTIAL_FIELDS.values()})


# -- classification order --------------------------------------------------


def test_classifications_are_ordered_least_to_most_restrictive():
    assert CLASSIFICATION_ORDER == ("PUBLIC", "PRIVATE", "CONFIDENTIAL", "RESTRICTED", "AIR_GAPPED")


def test_rank_increases_with_restrictiveness():
    ranks = [classification_rank(c) for c in CLASSIFICATION_ORDER]
    assert ranks == sorted(ranks)


def test_unknown_classification_ranks_most_restrictive():
    assert classification_rank("NONSENSE") == len(CLASSIFICATION_ORDER) - 1


def test_is_at_least(engine):
    assert engine.is_at_least("RESTRICTED", "CONFIDENTIAL")
    assert not engine.is_at_least("PUBLIC", "CONFIDENTIAL")


# -- resolution ------------------------------------------------------------


@pytest.mark.parametrize("name", list(BUILTIN_POLICIES))
def test_every_builtin_policy_resolves(engine, name):
    assert engine.resolve(name).name == name


def test_resolution_is_case_insensitive(engine):
    assert engine.resolve("confidential").name == "CONFIDENTIAL"


def test_unknown_classification_fails_closed(engine):
    """A typo must not silently downgrade a repository to PUBLIC."""
    assert engine.resolve("TYPO").name == FAIL_CLOSED_POLICY
    assert engine.resolve("").name == FAIL_CLOSED_POLICY


def test_fail_closed_policy_is_the_most_restrictive():
    assert FAIL_CLOSED_POLICY == CLASSIFICATION_ORDER[-1]


# -- provider rules --------------------------------------------------------


def test_public_permits_external_providers(engine):
    assert engine.evaluate(PolicyRequest(classification="PUBLIC", provider="anthropic")).allowed


def test_private_permits_anthropic(engine):
    assert engine.evaluate(PolicyRequest(classification="PRIVATE", provider="anthropic")).allowed


def test_private_forbids_openai(engine):
    decision = engine.evaluate(PolicyRequest(classification="PRIVATE", provider="openai"))
    assert not decision.allowed
    assert "provider_not_allowed" in [v.rule for v in decision.violations]


@pytest.mark.parametrize("provider", EXTERNAL_PROVIDERS)
def test_confidential_forbids_every_external_provider(engine, provider):
    decision = engine.evaluate(PolicyRequest(classification="CONFIDENTIAL", provider=provider))
    assert not decision.allowed
    assert "egress_forbidden" in [v.rule for v in decision.violations]


@pytest.mark.parametrize("provider", LOCAL_PROVIDERS)
def test_confidential_permits_local_providers(engine, provider):
    decision = engine.evaluate(
        PolicyRequest(classification="CONFIDENTIAL", provider=provider, sanitized=True)
    )
    assert decision.allowed


@pytest.mark.parametrize("classification", ["CONFIDENTIAL", "RESTRICTED", "AIR_GAPPED"])
def test_strict_classifications_forbid_egress(classification):
    assert not BUILTIN_POLICIES[classification].egress_permitted


def test_air_gapped_forbids_anthropic(engine):
    assert not engine.evaluate(
        PolicyRequest(classification="AIR_GAPPED", provider="anthropic")
    ).allowed


# -- model rules -----------------------------------------------------------


def test_unrestricted_policy_permits_any_model(engine):
    assert engine.evaluate(
        PolicyRequest(classification="PRIVATE", provider="anthropic", model="anything")
    ).allowed


def test_restricted_policy_rejects_unlisted_model(engine):
    decision = engine.evaluate(
        PolicyRequest(classification="RESTRICTED", provider="ollama", model="gpt-4o")
    )
    assert "model_not_allowed" in [v.rule for v in decision.violations]


def test_restricted_policy_accepts_listed_model(engine):
    decision = engine.evaluate(
        PolicyRequest(
            classification="RESTRICTED", provider="ollama", model="codellama:13b", sanitized=True
        )
    )
    assert decision.allowed


# -- limits ----------------------------------------------------------------


def test_context_size_limit(engine):
    decision = engine.evaluate(PolicyRequest(classification="AIR_GAPPED", context_chars=999_999))
    assert "context_too_large" in [v.rule for v in decision.violations]


def test_max_tokens_limit(engine):
    decision = engine.evaluate(PolicyRequest(classification="RESTRICTED", max_tokens=999_999))
    assert "max_tokens_exceeded" in [v.rule for v in decision.violations]


def test_file_count_limit(engine):
    files = tuple(f"f{i}.py" for i in range(50))
    decision = engine.evaluate(PolicyRequest(classification="AIR_GAPPED", files=files))
    assert "too_many_files" in [v.rule for v in decision.violations]


def test_within_limits_is_allowed(engine):
    decision = engine.evaluate(
        PolicyRequest(classification="AIR_GAPPED", provider="ollama", files=("a.py",),
                      context_chars=100, max_tokens=1024, sanitized=True)
    )
    assert decision.allowed


# -- scope rules -----------------------------------------------------------


def test_file_type_restriction(engine):
    decision = engine.evaluate(
        PolicyRequest(classification="RESTRICTED", files=("a.py", "b.exe"))
    )
    assert "file_type_not_allowed" in [v.rule for v in decision.violations]


def test_language_restriction(engine):
    decision = engine.evaluate(
        PolicyRequest(classification="RESTRICTED", languages=("go",))
    )
    assert "language_not_allowed" in [v.rule for v in decision.violations]


def test_repository_allowlist():
    policy = BUILTIN_POLICIES["PRIVATE"].model_copy(update={"allowed_repositories": ["approved"]})
    engine = PolicyEngine({**BUILTIN_POLICIES, "PRIVATE": policy})
    decision = engine.evaluate(PolicyRequest(classification="PRIVATE", repository="other"))
    assert "repository_not_allowed" in [v.rule for v in decision.violations]


def test_user_allowlist():
    policy = BUILTIN_POLICIES["PRIVATE"].model_copy(update={"allowed_users": ["alice"]})
    engine = PolicyEngine({**BUILTIN_POLICIES, "PRIVATE": policy})
    decision = engine.evaluate(PolicyRequest(classification="PRIVATE", user="mallory"))
    assert "user_not_allowed" in [v.rule for v in decision.violations]


# -- content rules ---------------------------------------------------------


def test_unsanitized_request_is_told_to_sanitize(engine):
    decision = engine.evaluate(PolicyRequest(classification="PRIVATE", provider="anthropic"))
    assert decision.decision == "sanitize"


def test_public_does_not_require_sanitization(engine):
    assert engine.evaluate(
        PolicyRequest(classification="PUBLIC", provider="anthropic")
    ).decision == "allow"


def test_residual_secrets_after_sanitization_are_fatal(engine):
    decision = engine.evaluate(
        PolicyRequest(classification="PRIVATE", provider="anthropic", secret_count=1, sanitized=True)
    )
    assert not decision.allowed
    assert "residual_secrets" in [v.rule for v in decision.violations]


def test_residual_pii_after_sanitization_is_fatal(engine):
    decision = engine.evaluate(
        PolicyRequest(classification="PRIVATE", provider="anthropic", pii_count=1, sanitized=True)
    )
    assert "residual_pii" in [v.rule for v in decision.violations]


def test_no_builtin_policy_permits_secrets_or_pii():
    for policy in BUILTIN_POLICIES.values():
        assert not policy.allow_secrets, policy.name
        assert not policy.allow_pii, policy.name


def test_every_policy_outside_public_requires_sanitization():
    for name, policy in BUILTIN_POLICIES.items():
        if name != "PUBLIC":
            assert policy.require_sanitization, name


# -- decision shape --------------------------------------------------------


def test_all_violations_are_reported_not_just_the_first(engine):
    decision = engine.evaluate(
        PolicyRequest(classification="AIR_GAPPED", provider="anthropic", context_chars=999_999)
    )
    rules = {v.rule for v in decision.violations}
    assert {"provider_not_allowed", "egress_forbidden", "context_too_large"} <= rules


def test_violations_name_observed_and_permitted(engine):
    decision = engine.evaluate(PolicyRequest(classification="CONFIDENTIAL", provider="anthropic"))
    for violation in decision.violations:
        assert violation.observed
        assert violation.permitted
        assert violation.detail


def test_allowed_request_has_a_reason(engine):
    assert engine.evaluate(
        PolicyRequest(classification="PUBLIC", provider="anthropic")
    ).reasons


def test_evaluation_is_deterministic(engine):
    request = PolicyRequest(classification="CONFIDENTIAL", provider="anthropic", context_chars=999_999)
    first, second = engine.evaluate(request), engine.evaluate(request)
    assert first.decision == second.decision
    assert [v.rule for v in first.violations] == [v.rule for v in second.violations]


# -- routing ---------------------------------------------------------------


def test_local_provider_classification():
    assert all(is_local(p) for p in LOCAL_PROVIDERS)
    assert not any(is_local(p) for p in EXTERNAL_PROVIDERS)


def test_classification_zone():
    assert classification_zone("PUBLIC") == "external"
    assert classification_zone("CONFIDENTIAL") == "local"


def test_every_provider_has_a_default_model():
    for provider in PROVIDER_PREFERENCE:
        assert DEFAULT_MODELS.get(provider), provider


def test_public_routes_to_anthropic_when_configured():
    decision = LLMRouter(external_settings()).route(BUILTIN_POLICIES["PUBLIC"])
    assert decision.permitted
    assert decision.provider == "anthropic"
    assert not decision.is_local


def test_confidential_routes_to_a_local_provider():
    decision = LLMRouter(local_settings()).route(BUILTIN_POLICIES["CONFIDENTIAL"])
    assert decision.permitted
    assert decision.is_local
    assert decision.provider in LOCAL_PROVIDERS


def test_confidential_refuses_when_only_external_is_configured():
    """The critical case: no local provider means no call, not a downgrade."""
    decision = LLMRouter(external_settings()).route(BUILTIN_POLICIES["CONFIDENTIAL"])
    assert not decision.permitted
    assert decision.provider == ""
    assert "anthropic" in decision.rejected_providers


def test_air_gapped_never_selects_an_external_provider():
    settings = Settings(
        anthropic_api_key="k", mistral_api_key="k", openai_api_key="k", gemini_api_key="k"
    )
    decision = LLMRouter(settings).route(BUILTIN_POLICIES["AIR_GAPPED"])
    assert decision.provider not in EXTERNAL_PROVIDERS


def test_preference_cannot_widen_the_allowlist():
    """A caller asking for anthropic under CONFIDENTIAL does not get it."""
    settings = Settings(anthropic_api_key="k", ollama_base_url="http://localhost:11434")
    decision = LLMRouter(settings).route(
        BUILTIN_POLICIES["CONFIDENTIAL"], preferred_provider="anthropic"
    )
    assert decision.provider != "anthropic"
    assert decision.is_local


def test_preference_is_honoured_within_the_allowlist():
    settings = Settings(anthropic_api_key="k", mistral_api_key="k")
    decision = LLMRouter(settings).route(
        BUILTIN_POLICIES["PRIVATE"], preferred_provider="mistral"
    )
    assert decision.provider == "mistral"


def test_unconfigured_provider_is_skipped_with_a_reason():
    settings = no_provider_settings().model_copy(update={"mistral_api_key": "k"})
    decision = LLMRouter(settings).route(BUILTIN_POLICIES["PRIVATE"])
    assert decision.provider == "mistral"
    assert "API key" in decision.rejected_providers.get("anthropic", "")


def test_nothing_configured_refuses():
    decision = LLMRouter(no_provider_settings()).route(BUILTIN_POLICIES["PUBLIC"])
    assert not decision.permitted
    assert decision.provider == ""
    assert decision.reason


def test_rejected_providers_explain_themselves():
    decision = LLMRouter(external_settings()).route(BUILTIN_POLICIES["AIR_GAPPED"])
    assert decision.rejected_providers
    assert all(reason for reason in decision.rejected_providers.values())


def test_routing_matrix_covers_every_policy():
    matrix = LLMRouter(external_settings()).routing_matrix(BUILTIN_POLICIES)
    assert set(matrix) == set(BUILTIN_POLICIES)
    assert all("egress_permitted" in row for row in matrix.values())


def test_routing_matrix_never_pairs_strict_policy_with_external_provider():
    settings = Settings(
        anthropic_api_key="k", openai_api_key="k", ollama_base_url="http://localhost:11434"
    )
    matrix = LLMRouter(settings).routing_matrix(BUILTIN_POLICIES)
    for name in ("CONFIDENTIAL", "RESTRICTED", "AIR_GAPPED"):
        assert matrix[name]["provider"] not in EXTERNAL_PROVIDERS, name


def test_routing_is_deterministic():
    router = LLMRouter(external_settings())
    first = router.route(BUILTIN_POLICIES["PRIVATE"])
    second = router.route(BUILTIN_POLICIES["PRIVATE"])
    assert (first.provider, first.model) == (second.provider, second.model)


def test_available_providers_reflects_configuration():
    assert LLMRouter(external_settings()).available_providers() == ["anthropic", "mistral"]
