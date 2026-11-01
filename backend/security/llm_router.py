"""Route each request to a provider its classification permits.

Eight providers across two trust zones:

    external (network egress)   anthropic · mistral · gemini · openai
    local (customer hardware)   ollama · lmstudio · vllm · tgi

The router never widens what the policy allows. It picks the highest-preference
provider from the policy's own allowlist that is also configured and available,
and records why every rejected candidate was rejected — a routing decision a
reviewer cannot reconstruct is not auditable.

**A routing violation is impossible by construction, not by care.** The final
`permitted` flag is recomputed from the policy after selection, so even a bug in
preference ordering cannot produce an approved decision for a forbidden
provider. `route()` returns a decision with `permitted=False` rather than
raising, and the pipeline treats that as a rejection.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.models.security import (
    LOCAL_PROVIDERS,
    DataClassification,
    RoutingDecision,
    SecurityPolicy,
)

# Preference order within a trust zone. Earlier is preferred when the policy
# permits several. Ordering is by capability for the repair task, not by cost.
PROVIDER_PREFERENCE: tuple[str, ...] = (
    "anthropic",
    "mistral",
    "openai",
    "gemini",
    "ollama",
    "vllm",
    "lmstudio",
    "tgi",
)

# Default model per provider when configuration does not name one.
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-sonnet-4-20250514",
    "mistral": "codestral-latest",
    "openai": "gpt-4o",
    "gemini": "gemini-1.5-pro",
    "ollama": "codellama",
    "vllm": "codellama",
    "lmstudio": "qwen2.5-coder",
    "tgi": "codellama",
}

# Which settings attribute holds each provider's credential or endpoint. Local
# providers need a base URL rather than a key; both are "is it configured?".
CREDENTIAL_FIELDS: dict[str, str] = {
    "anthropic": "anthropic_api_key",
    "mistral": "mistral_api_key",
    "openai": "openai_api_key",
    "gemini": "gemini_api_key",
    "ollama": "ollama_base_url",
    "vllm": "vllm_base_url",
    "lmstudio": "lmstudio_base_url",
    "tgi": "tgi_base_url",
}

MODEL_FIELDS: dict[str, str] = {
    "anthropic": "anthropic_model",
    "mistral": "mistral_model",
    "openai": "openai_model",
    "gemini": "gemini_model",
    "ollama": "ollama_model",
    "vllm": "vllm_model",
    "lmstudio": "lmstudio_model",
    "tgi": "tgi_model",
}


def is_local(provider: str) -> bool:
    return provider in LOCAL_PROVIDERS


@dataclass
class ProviderAvailability:
    """Whether a provider can actually be used right now."""

    provider: str
    configured: bool
    model: str
    reason: str = ""


class LLMRouter:
    """Chooses a permitted, configured provider for a classification."""

    def __init__(self, settings, preference: tuple[str, ...] = PROVIDER_PREFERENCE):
        self.settings = settings
        self.preference = preference

    # -- availability ----------------------------------------------------

    def model_for(self, provider: str) -> str:
        field = MODEL_FIELDS.get(provider, "")
        configured = getattr(self.settings, field, "") if field else ""
        return configured or DEFAULT_MODELS.get(provider, "")

    def availability(self, provider: str) -> ProviderAvailability:
        field = CREDENTIAL_FIELDS.get(provider, "")
        credential = getattr(self.settings, field, "") if field else ""
        model = self.model_for(provider)

        if not credential:
            label = "endpoint" if is_local(provider) else "API key"
            return ProviderAvailability(provider, False, model, f"no {label} configured")
        if not model:
            return ProviderAvailability(provider, False, model, "no model configured")
        return ProviderAvailability(provider, True, model)

    def available_providers(self) -> list[str]:
        return [p for p in self.preference if self.availability(p).configured]

    # -- routing ---------------------------------------------------------

    def route(
        self,
        policy: SecurityPolicy,
        *,
        preferred_provider: str = "",
        preferred_model: str = "",
    ) -> RoutingDecision:
        """Select a provider. Returns `permitted=False` rather than raising.

        A caller's preference is honoured only if the policy permits it; it can
        never widen the allowlist, only choose within it.
        """
        rejected: dict[str, str] = {}
        candidates: list[str] = []

        if preferred_provider:
            candidates.append(preferred_provider)
        candidates.extend(p for p in self.preference if p != preferred_provider)

        for provider in candidates:
            if not policy.permits_provider(provider):
                rejected[provider] = f"not permitted by {policy.name}"
                continue
            if not policy.egress_permitted and not is_local(provider):
                rejected[provider] = f"{policy.name} forbids network egress"
                continue

            state = self.availability(provider)
            if not state.configured:
                rejected[provider] = state.reason
                continue

            model = preferred_model if provider == preferred_provider and preferred_model else state.model
            if not policy.permits_model(model):
                rejected[provider] = f"model '{model}' not permitted by {policy.name}"
                continue

            return self._finalize(policy, provider, model, rejected)

        return RoutingDecision(
            classification=policy.classification,
            permitted=False,
            reason=(
                f"no provider satisfies {policy.name}: "
                + ("; ".join(f"{p} ({r})" for p, r in sorted(rejected.items())) or "none configured")
            ),
            rejected_providers=rejected,
        )

    def _finalize(
        self,
        policy: SecurityPolicy,
        provider: str,
        model: str,
        rejected: dict[str, str],
    ) -> RoutingDecision:
        """Recompute permission from the policy after selection.

        This is a deliberate second check of something already checked above. A
        routing violation is the single worst failure this layer can have — it
        sends confidential source to an external provider — so it is verified
        twice, on independent reads of the policy.
        """
        permitted = (
            policy.permits_provider(provider)
            and policy.permits_model(model)
            and (policy.egress_permitted or is_local(provider))
        )
        zone = "local" if is_local(provider) else "external"

        return RoutingDecision(
            provider=provider if permitted else "",
            model=model if permitted else "",
            is_local=is_local(provider),
            classification=policy.classification,
            permitted=permitted,
            reason=(
                f"{policy.name} permits {zone} provider '{provider}' with model '{model}'"
                if permitted
                else f"post-selection verification failed for '{provider}'"
            ),
            rejected_providers=rejected,
        )

    # -- introspection ---------------------------------------------------

    def routing_matrix(self, policies: dict[str, SecurityPolicy]) -> dict[str, dict]:
        """What each classification would route to right now. For the dashboard."""
        matrix: dict[str, dict] = {}
        for name, policy in policies.items():
            decision = self.route(policy)
            matrix[name] = {
                "provider": decision.provider,
                "model": decision.model,
                "is_local": decision.is_local,
                "permitted": decision.permitted,
                "reason": decision.reason,
                "allowed_providers": list(policy.allowed_providers),
                "egress_permitted": policy.egress_permitted,
            }
        return matrix


def classification_zone(classification: DataClassification) -> str:
    """Which trust zone a classification is confined to."""
    return "external" if classification in ("PUBLIC", "PRIVATE") else "local"
