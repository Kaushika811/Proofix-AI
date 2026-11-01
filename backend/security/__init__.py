"""Enterprise Security Layer.

Sits between Context Engineering and the LLM Gateway, and is mandatory: there is
no code path from an agent to a provider that does not pass through
`services/security_pipeline.py`.

Modules are ordered by when they run:

    repository_isolation  before anything reads the repository
    secret_scanner        \\
    pii_detector           }  detection, composed by privacy_guard
    sanitizer             /
    privacy_guard         sanitizes one Context Package
    policy_engine         decides what this classification permits
    llm_router            picks a permitted provider, or refuses
    prompt_firewall       last inspection of the outgoing prompt
    audit_logger          immutable record of the decision
    compliance_engine     reports over the audit trail
    encryption            at-rest protection for what is stored

Everything is deterministic. No module here calls an LLM, and none may.
"""
