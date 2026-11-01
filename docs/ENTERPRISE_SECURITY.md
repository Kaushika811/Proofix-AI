# Enterprise Security Platform (Phase 5)

Every LLM interaction is governed by deterministic controls. The model never
receives a repository — only a verified, sanitized, policy-approved context
package. Additive throughout: no LangGraph change, no `RunState` change, no
existing API change, no agent logic changed.

---

## 1. Architecture

```
Repository ──► Repository Intelligence ──► Context Engineering (A5.5)
                                                   │
                                                   ▼
                    ┌──────── Enterprise Security Layer ────────┐
                    │                                            │
                    │  1  repository_isolation   host/env strip  │
                    │  2  prompt_firewall        STRUCTURAL scan │ ◄─ pre-sanitize
                    │  3  privacy_guard ─┬─ secret_scanner       │
                    │                    ├─ pii_detector         │
                    │                    └─ sanitizer            │
                    │  4  policy_engine          5 classifications│
                    │  5  llm_router             8 providers      │
                    │  6  prompt_firewall        EGRESS scan     │ ◄─ post-sanitize
                    │  7  audit_logger           hash-chained    │
                    │                                            │
                    │  encryption · compliance_engine (7 frameworks)
                    └────────────────────┬───────────────────────┘
                                         ▼
                                   LLM Gateway
                                         ▼
              Claude · Mistral · Gemini · OpenAI · Ollama · vLLM · LM Studio · TGI
```

**No bypass.** `LLMGateway.complete()` calls `SecurityPipeline.approve()` before
`_dispatch()`. There is no parameter that skips it and no other entry point to a
provider. The prompt that reaches the provider is the *approved* prompt, which
may differ from the caller's — sanitization happens before egress, not as advice.

---

## 2. Pipeline

| # | Stage | Can reject on |
|---|---|---|
| 1 | Isolation | host paths, environment dumps |
| 2 | **Structural firewall** | private keys, binary blobs, repository dumps, `.git`/`.env` metadata |
| 3 | Privacy guard | (redacts; does not reject) |
| 4 | Policy | provider, model, size, files, types, languages, users, repos |
| 5 | Routing | no permitted provider available |
| 6 | **Egress firewall** | residual secrets, residual PII |
| 7 | Audit | (records; never rejects) |

**Why the firewall runs twice.** Sanitization can legitimately fix a hardcoded
password. It cannot fix a prompt containing a private key file — that means
context assembly went wrong upstream, and redacting it to
`<REDACTED_PRIVATE_KEY>` would let it pass every later check while the fault
went unreported. Structural rules therefore run on the caller's original text;
residual rules run on the exact bytes going on the wire.

Rejections are audited as thoroughly as approvals. A security layer that only
logs what it permitted cannot answer the question an incident review asks first.

---

## 3. Policy engine

| | PUBLIC | PRIVATE | CONFIDENTIAL | RESTRICTED | AIR_GAPPED |
|---|---|---|---|---|---|
| Providers | all 8 | anthropic, mistral, local | local only | ollama, vllm | local only |
| Egress | ✓ | ✓ | ✗ | ✗ | ✗ |
| Max tokens | 8192 | 8192 | 4096 | 2048 | 2048 |
| Max context | 400 K | 200 K | 100 K | 40 K | 20 K |
| Max files | 100 | 50 | 25 | 10 | 5 |
| File types | any | any | source + config | `.py` | `.py` |
| Sanitization | optional | required | required | required | required |
| Secrets / PII | never | never | never | never | never |

Three principles the defaults encode:

- **Fail closed.** An unrecognised classification resolves to `AIR_GAPPED`, not
  `PUBLIC`. A configuration typo must not silently downgrade a repository.
- **Egress is a property of classification**, expressed as `egress_permitted`
  rather than relying on the provider list being correct.
- **All violations are reported**, not just the first — a caller that fixes one
  rejection only to hit the next has learned nothing.

---

## 4. Privacy Guard workflow

```
ContextPackage (from A5.5, never modified)
   │  deep copy — the original stays exactly as A5.5 produced it
   ▼
for every text field: focused_context · symbol sources · runtime_evidence
                      acceptance_criteria · contracts · constraints · imports
   │
   ├─ 1  host paths + environment   /Users/alice/… → <PATH>
   ├─ 2  secrets (22 categories)    AWS_SECRET_ACCESS_KEY → <REDACTED_AWS_SECRET>
   ├─ 3  PII (14 categories)        ada@corp.com → <REDACTED_EMAIL>
   └─ 4  organisational identity    acme.net → host0.example.internal
   ▼
sanitized copy + SanitizationReport      ── or ──   status="failed", package withheld
```

Stage order is forced: a connection string contains a password *and* a hostname,
so the credential must go before the sanitizer sees the host. Emails inside
credential URIs are gone before PII detection runs, so what remains is genuine
contact data rather than the tail of a secret.

**Structure is preserved, values are not.** Sanitized code still parses,
so the repair model can still work on it. Package aliases are consistent across
files — `acme.net` maps to the same placeholder everywhere, or the model would
read two references to one system as two different systems.

**A5.5 is untouched.** Ranking, selection and metrics are asserted identical
before and after (`test_ranking_data_is_untouched`).

---

## 5. Detection coverage

**Secrets (22 categories):** AWS secret/key-id/session-token, GCP service
account + API key, Azure connection string, SSH private keys, PEM private keys,
certificates, JWTs, JWT signing secrets, GitHub/Slack/Stripe/OpenAI/npm tokens,
bearer tokens, OAuth client secrets, URI credentials, ODBC connection strings,
database passwords, generic password/api-key/secret assignments, high-entropy
tokens.

**PII (14 categories):** email, SSN (with issuance-range validation), credit
card (Luhn-validated), IBAN, phone (international + US), street address,
employee/customer/medical/financial IDs, internal usernames, internal hostnames,
private IP ranges, person names.

**Name detection is scoped, not general — stated plainly.** There is no
deterministic way to distinguish a person's name from an identifier in arbitrary
text. A general detector would redact `Session`, `Parser` and every capitalised
word, destroying the repair context. Names are detected only where a repository
actually states one: git author strings, `@author`/`Copyright` annotations, and
a configured roster. This substantially reduces PII exposure; it does not
eliminate the possibility of a name in free prose. For that case, AIR_GAPPED
forbids external egress entirely — that is the control which actually closes it.

---

## 6. Audit schema

Hash-chained: `entry_hash` covers the event's content **and** the previous
event's hash, so any retroactive edit breaks verification from that point on.
`verify_chain()` recomputes rather than trusting what was stored.

```
event_id · sequence · timestamp · run_id
repository_hash · context_hash · prompt_hash · response_hash
provider · model · policy · classification · operation · actor
files_included · secret_count · pii_count · sanitization_categories
prompt_chars · prompt/completion/total_tokens · estimated_cost_usd · latency_ms
decision (allow|sanitize|reject) · result (success|rejected|error) · violations
previous_hash · entry_hash
```

**Raw prompts are not stored by default.** An audit log is read by more people
than the repository is; storing sanitized-but-sensitive prompts there moves the
disclosure rather than preventing it. `audit_store_raw_prompts` exists for
incident investigation and is off.

---

## 7. Compliance engine

Seven frameworks: SOC2, ISO27001, GDPR, HIPAA, PCI-DSS, EU AI Act, NIST AI RMF.
Each control reports `pass` / `fail` / `not_applicable` with evidence, and every
failure carries a recommendation naming the setting that fixes it.

**It does not certify compliance — no software does.** It reports which
technical controls this platform enforces and what evidence exists. Organisational
controls (personnel screening, vendor review, business associate agreements) are
marked `not_applicable` with the reason, and excluded from the score, rather
than quietly counted as passes.

---

## 8. LLM routing matrix

| Classification | Routes to | Zone |
|---|---|---|
| PUBLIC | anthropic → mistral → openai → gemini → local | external |
| PRIVATE | anthropic → mistral → local | external |
| CONFIDENTIAL | ollama → vllm → lmstudio → tgi | **local only** |
| RESTRICTED | ollama → vllm (llama/qwen/codellama/deepseek) | **local only** |
| AIR_GAPPED | ollama → vllm → lmstudio → tgi | **local only** |

A caller's preference chooses *within* the allowlist; it can never widen it.
When no permitted provider is configured the router refuses — it never falls
back to an external one. Permission is recomputed from the policy *after*
selection, so a bug in preference ordering still cannot approve a forbidden
provider.

---

## 9. Files

**New (14):** `models/security.py`, `security/` (`policy_engine`, `privacy_guard`,
`secret_scanner`, `pii_detector`, `sanitizer`, `prompt_firewall`, `llm_router`,
`audit_logger`, `compliance_engine`, `encryption`, `repository_isolation`),
`services/security_pipeline.py`, `api/routes/security.py`.

**Modified (3):** `config.py` (security settings), `services/llm_gateway.py`
(mandatory gate + `SecurityRejection`), `main.py` (route registration).

**Unchanged:** every agent, LangGraph, `RunState`, every existing endpoint.

---

## 10. Performance

Measured on this machine, per LLM call:

| Prompt size | Approval overhead |
|---|---|
| ~120 B | 0.15 ms |
| ~5 KB | 3.4 ms |
| ~30 KB | 33 ms |
| dirty (redacting) | 0.24 ms |
| AES-256-GCM, 1 KB | 0.010 ms |

Against an LLM call of 1–20 seconds, overhead is well under 1% at realistic
context sizes. Cost scales linearly with prompt length because every detector is
a single regex pass.

---

## 11. Security guarantees

**Guaranteed by construction:**
1. No LLM call reaches a provider without an `ApprovedContext`.
2. CONFIDENTIAL and above never route to an external provider (verified twice).
3. An unknown classification fails closed to the most restrictive policy.
4. A private key, binary blob or repository dump is rejected, never redacted-and-sent.
5. A security-control fault rejects the call (`security_fail_closed`, default on).
6. Audit records are tamper-evident and contain no prompt text.
7. Path traversal, symlink escape and credential-file reads are refused.
8. A privacy-guard failure withholds the package rather than passing it through.

**Explicitly not guaranteed** — stated because a security document that
overclaims is worse than one that admits limits:
- Name detection in free prose is scoped, not exhaustive (§5).
- Read-only workspace is *containment*, not kernel-level immutability: A7 must
  write patches to the clone.
- Dead-code and dynamic-import edge cases are noted in the Phase 4 docs.
- Compliance reports attest technical controls only.

---

## 12. Tests

385 new, across 6 files:

| File | Tests |
|---|---|
| `test_security_pipeline.py` | 95 |
| `test_security_policy_router.py` | 64 |
| `test_secret_scanner.py` | 62 |
| `test_prompt_firewall.py` | 60 |
| `test_pii_detector.py` | 58 |
| `test_security_encryption_audit.py` | 54 |

Full suite: **1359 passed**, 1 pre-existing environmental failure
(`test_reproduction_stability_gate` needs a `vulnapi` git fixture).

---

## 13. Configuration

```python
security_enabled: bool = True                    # off ⇒ pre-Phase-5 behaviour
security_default_classification: str = "PRIVATE"
security_fail_closed: bool = True                # a control fault rejects
security_detect_secrets / detect_pii / sanitize_identity: bool = True

security_company_identifiers: str = ""           # comma-separated
security_internal_domains: str = ""
security_private_package_prefixes: str = ""
security_redact_repository_names: bool = False
pii_known_names: str = ""

audit_store_raw_prompts: bool = False            # keep off outside incidents
encryption_key: str = ""                         # empty ⇒ disabled, reported so
encryption_previous_keys: str = ""               # "v1:material" for rotation

openai_api_key / gemini_api_key / ollama_base_url / vllm_base_url / ...
```

Defaults preserve existing behaviour: PRIVATE permits the providers already in
use, and sanitization is transparent when the context is clean.
