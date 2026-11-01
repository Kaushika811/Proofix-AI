"""Prompt firewall: every rejection rule, and what must still get through."""

import base64

import pytest

from backend.security.policy_engine import BUILTIN_POLICIES
from backend.security.prompt_firewall import (
    BINARY_ENTROPY_THRESHOLD,
    WHOLE_REPOSITORY_FILE_MARKERS,
    PromptFirewall,
    contains_binary,
    count_referenced_files,
    estimate_tokens,
)

CLEAN = "def validate(token):\n    if not token:\n        return False\n    return True\n"


@pytest.fixture
def firewall():
    return PromptFirewall(BUILTIN_POLICIES["PRIVATE"])


@pytest.fixture
def strict():
    return PromptFirewall(BUILTIN_POLICIES["AIR_GAPPED"])


# -- clean traffic ---------------------------------------------------------


def test_clean_prompt_is_allowed(firewall):
    verdict = firewall.inspect(CLEAN)
    assert verdict.allowed
    assert verdict.decision == "allow"
    assert not verdict.violations


def test_all_rules_pass_on_clean_input(firewall):
    assert all(firewall.inspect(CLEAN).rule_results.values())


def test_clean_verdict_reports_measurements(firewall):
    verdict = firewall.inspect(CLEAN, "system prompt")
    assert verdict.prompt_chars > 0
    assert verdict.estimated_tokens > 0
    assert verdict.inspected_ms >= 0


def test_system_prompt_is_inspected_too(firewall):
    verdict = firewall.inspect(CLEAN, 'system key = "ghp_' + "a" * 36 + '"')
    assert not verdict.allowed
    assert "residual_secrets" in verdict.rules_failed


# -- residual credentials --------------------------------------------------


def test_residual_secret_is_rejected(firewall):
    verdict = firewall.inspect('token = "ghp_' + "a" * 36 + '"')
    assert not verdict.allowed
    assert "residual_secrets" in verdict.rules_failed


def test_rejection_names_the_category_not_the_secret(firewall):
    """The violation must not carry the credential it found."""
    verdict = firewall.inspect('token = "ghp_' + "b" * 36 + '"')
    violation = next(v for v in verdict.violations if v.rule == "residual_secrets")
    assert "github_token" in violation.observed
    assert "b" * 36 not in violation.model_dump_json()


def test_sanitized_prompt_passes(firewall):
    assert firewall.inspect('token = "<REDACTED_GITHUB_TOKEN>"').allowed


def test_connection_string_is_rejected(firewall):
    assert not firewall.inspect('DB = "postgres://u:p@host/db"').allowed


# -- residual PII ----------------------------------------------------------


def test_residual_pii_is_rejected(firewall):
    verdict = firewall.inspect("contact ada@corp.example")
    assert "residual_pii" in verdict.rules_failed


def test_ssn_is_rejected(firewall):
    assert "residual_pii" in firewall.inspect("SSN 123-45-6789").rules_failed


def test_redacted_pii_passes(firewall):
    assert firewall.inspect("contact <REDACTED_EMAIL>").allowed


def test_pii_rule_is_skipped_when_policy_allows_it():
    policy = BUILTIN_POLICIES["PUBLIC"].model_copy(update={"allow_pii": True})
    verdict = PromptFirewall(policy).inspect("contact ada@corp.example")
    assert verdict.rule_results["residual_pii"] is True


# -- key material ----------------------------------------------------------


def test_private_key_block_is_rejected(firewall):
    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
    assert "private_key_material" in firewall.inspect(pem).rules_failed


def test_certificate_block_is_rejected(firewall):
    pem = "-----BEGIN CERTIFICATE-----\nMIIabc\n-----END CERTIFICATE-----"
    assert "private_key_material" in firewall.inspect(pem).rules_failed


def test_mention_of_certificates_is_not_rejected(firewall):
    assert firewall.inspect("def load_certificate(path):\n    return path").allowed


# -- binary content --------------------------------------------------------


def test_base64_payload_is_rejected(firewall):
    blob = base64.b64encode(bytes(range(256)) * 4).decode()
    assert "binary_content" in firewall.inspect(blob).rules_failed


def test_data_uri_is_rejected(firewall):
    assert "binary_content" in firewall.inspect("img = 'data:image/png;base64,iVBOR'").rules_failed


def test_null_byte_is_rejected(firewall):
    assert "binary_content" in firewall.inspect("data = 'a\x00b'").rules_failed


def test_archive_magic_is_rejected(firewall):
    assert "binary_content" in firewall.inspect("PK\x03\x04 archive").rules_failed


def test_repeated_characters_are_not_binary():
    """A long run of one character is not a payload."""
    assert not contains_binary("p" * 2000)


def test_separator_rule_is_not_binary():
    """`====` separator lines are common in prompts."""
    assert not contains_binary("=" * 800)


def test_real_base64_is_binary():
    assert contains_binary(base64.b64encode(bytes(range(256)) * 4).decode())


def test_normal_source_is_not_binary():
    assert not contains_binary(CLEAN)


def test_binary_entropy_threshold_is_declared():
    assert 0 < BINARY_ENTROPY_THRESHOLD < 8


# -- size ------------------------------------------------------------------


def test_oversized_prompt_is_rejected(strict):
    oversized = "def f():\n    return 1\n" * 2000
    assert "prompt_size" in strict.inspect(oversized).rules_failed


def test_size_violation_reports_limit_and_observed(strict):
    verdict = strict.inspect("x = 1\n" * 10_000)
    violation = next(v for v in verdict.violations if v.rule == "prompt_size")
    assert violation.observed.isdigit()
    assert violation.permitted == str(strict.policy.max_context_chars)


def test_prompt_within_the_limit_passes(firewall):
    assert firewall.inspect(CLEAN * 10).allowed


# -- file count ------------------------------------------------------------


def test_declared_file_count_over_limit_is_rejected(strict):
    files = tuple(f"module_{i}.py" for i in range(20))
    assert "file_count" in strict.inspect(CLEAN, files=files).rules_failed


def test_referenced_file_count_is_counted_even_when_undeclared(strict):
    text = " ".join(f"module_{i}.py" for i in range(20))
    assert "file_count" in strict.inspect(text).rules_failed


def test_count_referenced_files_deduplicates():
    assert count_referenced_files("a.py a.py b.py") == 2


def test_count_referenced_files_ignores_prose():
    assert count_referenced_files("the module handles requests") == 0


# -- whole repository ------------------------------------------------------


def test_repository_dump_is_rejected(firewall):
    text = " ".join(f"pkg/module_{i}.py" for i in range(WHOLE_REPOSITORY_FILE_MARKERS + 5))
    verdict = firewall.inspect(text)
    assert "whole_repository" in verdict.rules_failed


def test_a_few_files_is_not_a_dump(firewall):
    assert firewall.inspect("see a.py and b.py").allowed


def test_dump_violation_is_critical(firewall):
    text = " ".join(f"pkg/m{i}.py" for i in range(WHOLE_REPOSITORY_FILE_MARKERS + 5))
    violation = next(v for v in firewall.inspect(text).violations if v.rule == "whole_repository")
    assert violation.severity == "critical"


# -- repository and host metadata -----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "see .git/config for remotes",
        "check .git/HEAD",
        "load .env for settings",
        'File "/Users/alice/project/a.py", line 3',
        "read ~/.ssh/id_rsa",
        "using .aws/credentials",
        "cluster at .kube/config",
    ],
)
def test_metadata_disclosure_is_rejected(firewall, text):
    assert "repository_metadata" in firewall.inspect(text).rules_failed


def test_environment_dump_is_rejected(firewall):
    assert "repository_metadata" in firewall.inspect("PATH=/usr/bin:/bin").rules_failed


def test_relative_paths_are_not_metadata(firewall):
    assert firewall.inspect('File "pkg/auth.py", line 3').allowed


def test_gitignore_is_not_git_internals(firewall):
    assert firewall.inspect("see .gitignore").allowed


# -- verdict shape ---------------------------------------------------------


def test_multiple_failures_are_all_reported(firewall):
    text = 'token = "ghp_' + "a" * 36 + '"\ncontact a@b.com\nPATH=/usr/bin'
    verdict = firewall.inspect(text)
    assert {"residual_secrets", "residual_pii", "repository_metadata"} <= set(verdict.rules_failed)


def test_rules_failed_is_sorted(firewall):
    text = 'token = "ghp_' + "a" * 36 + '"\ncontact a@b.com'
    assert firewall.inspect(text).rules_failed == sorted(firewall.inspect(text).rules_failed)


def test_every_rule_is_reported_pass_or_fail(firewall):
    expected = {
        "residual_secrets", "residual_pii", "private_key_material", "binary_content",
        "prompt_size", "file_count", "whole_repository", "repository_metadata",
    }
    assert set(firewall.inspect(CLEAN).rule_results) == expected


def test_inspection_is_deterministic(firewall):
    text = 'token = "ghp_' + "a" * 36 + '"'
    first, second = firewall.inspect(text), firewall.inspect(text)
    assert first.decision == second.decision
    assert first.rules_failed == second.rules_failed


def test_empty_prompt_is_allowed(firewall):
    assert firewall.inspect("").allowed


def test_estimate_tokens_is_positive():
    assert estimate_tokens("abcd" * 100) > 0
    assert estimate_tokens("") >= 1


def test_stricter_policy_rejects_what_a_looser_one_allows():
    text = "x = 1\n" * 5000
    assert PromptFirewall(BUILTIN_POLICIES["PUBLIC"]).inspect(text).allowed
    assert not PromptFirewall(BUILTIN_POLICIES["AIR_GAPPED"]).inspect(text).allowed


def test_rejection_is_logged(firewall, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        firewall.inspect('token = "ghp_' + "a" * 36 + '"', run_id="run-1")
    assert "prompt_firewall_rejected" in caplog.text


# -- pre-inspection --------------------------------------------------------


def test_pre_inspect_rejects_structural_problems(firewall):
    """A private key means context assembly went wrong, not that it is dirty."""
    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
    assert not firewall.pre_inspect(pem).allowed


def test_pre_inspect_ignores_sanitizable_findings(firewall):
    """A hardcoded password is about to be redacted; it is not a rejection."""
    assert firewall.pre_inspect('password = "P@ssw0rd12345"').allowed


def test_pre_inspect_ignores_pii(firewall):
    assert firewall.pre_inspect("contact ada@corp.example").allowed


def test_pre_inspect_reports_only_structural_rules(firewall):
    from backend.security.prompt_firewall import STRUCTURAL_RULES

    assert set(firewall.pre_inspect(CLEAN).rule_results) == set(STRUCTURAL_RULES)


def test_pre_inspect_catches_repository_dumps(firewall):
    text = " ".join(f"pkg/m{i}.py" for i in range(WHOLE_REPOSITORY_FILE_MARKERS + 5))
    assert not firewall.pre_inspect(text).allowed


def test_pre_inspect_does_not_log_sanitizable_findings(firewall, caplog):
    """A rejection log entry for something that was then approved is a lie."""
    import logging

    with caplog.at_level(logging.WARNING):
        firewall.pre_inspect('password = "P@ssw0rd12345"')
    assert "prompt_firewall_rejected" not in caplog.text


def test_pre_inspect_logs_a_real_structural_rejection(firewall, caplog):
    import logging

    pem = "-----BEGIN PRIVATE KEY-----\nMIIabc\n-----END PRIVATE KEY-----"
    with caplog.at_level(logging.WARNING):
        firewall.pre_inspect(pem, run_id="r1")
    assert "prompt_firewall_rejected" in caplog.text


def test_inspect_logging_can_be_suppressed(firewall, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        firewall.inspect('token = "ghp_' + "a" * 36 + '"', log=False)
    assert "prompt_firewall_rejected" not in caplog.text
