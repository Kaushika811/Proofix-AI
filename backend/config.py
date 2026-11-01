from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal[
    "anthropic", "mistral", "openai", "gemini",
    "ollama", "lmstudio", "vllm", "tgi",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_provider: LLMProvider = "anthropic"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"
    mistral_api_key: str = ""
    mistral_model: str = "codestral-latest"
    redis_url: str = "redis://localhost:6379/0"
    github_token: str = ""
    github_repo_owner: str = ""
    # No default. It used to be "vulnapi" — the fixture repository — so an
    # operator who set a token but forgot the repo name got a PR aimed at, and a
    # dry-run URL naming, a repository their run had nothing to do with.
    github_repo_name: str = ""
    github_dry_run: bool = True
    stub_mode: bool = True
    max_retries: int = 3
    state_ttl_seconds: int = 604800  # 7 days
    mutmut_timeout_seconds: int = 60
    default_criticality: float = 0.4

    # LLM gateway transport policy. Defaults preserve the previous single-shot
    # behaviour for callers that never hit a transient failure.
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 120.0
    llm_max_attempts: int = 3
    llm_retry_base_delay: float = 0.5

    # A5.5 Context Engineering. Disabling it returns A6/A7 to their pre-A5.5
    # behaviour exactly — the layer is advisory, never load-bearing.
    context_engineering_enabled: bool = True
    context_budget_chars: int = 4000
    context_supporting_files: int = 2

    # Phase 3 Repository Intelligence. Disabling it returns A1, A5.5 and A7 to
    # their pre-Phase-3 behaviour exactly — the layer is additive throughout.
    repository_intelligence_enabled: bool = True

    #: Run the environment precheck (A0.7) and let it gate reproduction.
    #: On by default: continuing past an unrunnable environment produces
    #: fabricated-looking results, which is worse than stopping.
    environment_precheck_enabled: bool = True
    #: Seconds allowed for the probe's interpreter checks.
    environment_probe_timeout: int = 30
    # v2: FunctionSpan gained `complexity`, which the risk engine reads.
    repository_cache_version: str = "v2"
    repository_index_max_files: int = 3000
    repository_history_window_days: int = 180
    # A1 reuses the index's AST parse instead of repeating it. Off returns A1 to
    # parsing every file itself; output is identical either way.
    repository_reuse_in_a1: bool = True
    # Historical repairs attached to A7's prompt as metadata. Never evidence.
    repair_memory_enabled: bool = True
    repair_memory_max_matches: int = 2

    # Repository Knowledge Graph. Disabling it returns A5.5 to its Phase 3
    # ranking exactly and stops A0.5 publishing graph summaries.
    knowledge_graph_enabled: bool = True
    knowledge_graph_top_risks: int = 10
    knowledge_graph_hotspots_per_kind: int = 5
    # A5.5 graph enrichment: how many neighbours each query may contribute.
    knowledge_graph_max_related: int = 5

    # ---- Phase 5 Enterprise Security -----------------------------------
    # The pipeline is mandatory; these tune it, never bypass it. Defaults are
    # chosen so an existing deployment behaves exactly as before: PRIVATE
    # permits the providers already in use, and sanitization is transparent
    # when the context is clean.
    security_enabled: bool = True
    security_default_classification: str = "PRIVATE"
    security_always_sanitize: bool = True
    security_detect_secrets: bool = True
    security_detect_pii: bool = True
    security_sanitize_identity: bool = True
    # Fail closed: a security fault rejects the call rather than letting it
    # through unchecked. Set False only to diagnose a false positive.
    security_fail_closed: bool = True

    # Organisational identity for the sanitizer. Comma-separated.
    security_company_identifiers: str = ""
    security_internal_domains: str = ""
    security_private_registries: str = ""
    security_private_package_prefixes: str = ""
    security_repository_names: str = ""
    security_redact_repository_names: bool = False
    security_strip_confidential_comments: bool = True
    pii_known_names: str = ""

    # Audit. Raw prompts are NOT stored by default — an audit log is read by
    # more people than the repository is.
    audit_store_raw_prompts: bool = False
    audit_retention_seconds: int = 31_536_000  # 1 year

    # Encryption at rest. Empty key ⇒ disabled and reported as disabled,
    # never silently no-op.
    encryption_key: str = ""
    encryption_key_version: str = "v1"
    encryption_previous_keys: str = ""  # "key_id:material,key_id:material"

    # Additional providers for the router. Local providers take a base URL.
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    gemini_api_key: str = ""

    # ---- Sarvam AI Speech-to-Text -----------------------------------
    # Used only by backend/api/routes/speech.py. Never exposed to the frontend.
    sarvam_api_key: str = ""
    gemini_model: str = "gemini-1.5-pro"
    ollama_base_url: str = ""
    ollama_model: str = "codellama"
    lmstudio_base_url: str = ""
    lmstudio_model: str = "qwen2.5-coder"
    vllm_base_url: str = ""
    vllm_model: str = "codellama"
    tgi_base_url: str = ""
    tgi_model: str = "codellama"

    # ---- Phase 6 Organizational Learning -------------------------------
    # Learning is advisory: it contributes prompt context, never ranking
    # weights or gates. Off returns A5.5 and A7 to their Phase 5 behaviour.
    learning_enabled: bool = True
    learning_organization_id: str = "default"
    learning_max_directives: int = 18
    # Attach learned conventions to A7's prompt. Separate from the flag above
    # so learning can accumulate while its prompt influence stays off.
    learning_influence_prompts: bool = True

    role_confidence_threshold: float = 0.85
    role_high_confidence_threshold: float = 0.95
    sig_cache_enabled: bool = True
    sig_cache_ttl_seconds: int = 604800
    # v3: FunctionSpan gained `complexity`; a v2 payload would deserialize with
    # zeros and silently understate risk.
    sig_cache_key_version: str = "v3"
    always_llm_filenames: str = ""

    # Comma-separated browser origins allowed to call the API.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def llm_configured(self) -> bool:
        """Whether the active LLM provider has a usable credential or endpoint.

        Cloud providers require an API key. Local providers (Ollama, LM Studio,
        vLLM, TGI) require a base URL — the key is optional and defaults to the
        empty string, which their SDKs accept.
        """
        provider = self.llm_provider
        if provider == "mistral":
            return bool(self.mistral_api_key)
        if provider == "openai":
            return bool(self.openai_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        if provider == "ollama":
            return bool(self.ollama_base_url)
        if provider == "lmstudio":
            return bool(self.lmstudio_base_url)
        if provider == "vllm":
            return bool(self.vllm_base_url)
        if provider == "tgi":
            return bool(self.tgi_base_url)
        # Default: anthropic
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
