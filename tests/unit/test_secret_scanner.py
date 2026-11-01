"""Credential detection: every category, and the false positives that matter.

The negative tests carry as much weight as the positive ones. A scanner that
redacts `max_tokens = 4096` corrupts the code under repair, and one that redacts
every constant teaches reviewers to ignore redactions entirely.
"""

import pytest

from backend.models.security import SecretFinding
from backend.security.secret_scanner import (
    ENTROPY_THRESHOLD,
    PLACEHOLDERS,
    RULES,
    category_counts,
    contains_secret,
    scan_lines,
    scan_text,
)


def categories(text: str) -> list[str]:
    return [f.category for f in scan_text(text).findings]


# -- cloud credentials -----------------------------------------------------


def test_aws_secret_access_key():
    result = scan_text('AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"')
    assert "aws_secret_access_key" in [f.category for f in result.findings]
    assert "<REDACTED_AWS_SECRET>" in result.text
    assert "wJalrXUtnFEMI" not in result.text


def test_aws_access_key_id():
    assert "aws_access_key_id" in categories('key = "AKIAIOSFODNN7EXAMPLE"')


def test_aws_temporary_key_id():
    assert "aws_access_key_id" in categories('key = "ASIAIOSFODNN7EXAMPLE"')


def test_aws_session_token():
    assert "aws_session_token" in categories('AWS_SESSION_TOKEN = "FwoGZXIvYXdzEBYaDH"')


def test_gcp_service_account_marker():
    assert "gcp_service_account" in categories('{"type": "service_account", "project_id": "x"}')


def test_gcp_api_key():
    assert "api_key" in categories("AIzaSyD-1234567890abcdefghijklmnopqrstu")


def test_azure_connection_string():
    text = "DefaultEndpointsProtocol=https;AccountName=store;AccountKey=abc123=="
    assert "azure_credential" in categories(text)


# -- keys and certificates -------------------------------------------------


def test_ssh_private_key_block():
    key = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaA==\n-----END OPENSSH PRIVATE KEY-----"
    result = scan_text(key)
    assert "ssh_private_key" in [f.category for f in result.findings]
    assert "b3BlbnNzaA" not in result.text


def test_rsa_private_key_block():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert "ssh_private_key" in [f.category for f in scan_text(key).findings]


def test_certificate_block():
    cert = "-----BEGIN CERTIFICATE-----\nMIIBcert\n-----END CERTIFICATE-----"
    assert "certificate" in categories(cert)


def test_private_key_severity_is_critical():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"
    assert scan_text(key).findings[0].severity == "critical"


# -- tokens ----------------------------------------------------------------


def test_jwt():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    assert "jwt" in categories(jwt)


def test_jwt_secret_assignment():
    assert "jwt_secret" in categories('JWT_SECRET = "super-secret-signing-value"')


def test_github_token():
    assert "github_token" in categories("ghp_" + "a" * 36)


@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghu_", "ghs_", "ghr_"])
def test_all_github_token_prefixes(prefix):
    assert "github_token" in categories(prefix + "a" * 36)


def test_slack_token():
    assert "slack_token" in categories("xoxb-123456789012-abcdefghijkl")


def test_stripe_live_key():
    assert "stripe_key" in categories("sk_live_" + "a" * 24)


def test_openai_key():
    assert "api_key" in categories("sk-" + "a" * 32)


def test_npm_token():
    assert "npm_token" in categories("npm_" + "a" * 36)


def test_bearer_token_header():
    assert "bearer_token" in categories("Authorization: Bearer abcdef1234567890ABCDEF")


# -- oauth, connection strings, database ----------------------------------


def test_oauth_client_secret():
    assert "oauth_secret" in categories('client_secret = "abc123XYZ-secret-value"')


def test_connection_string_with_inline_credentials():
    result = scan_text('DB = "postgres://user:hunter2@db.example.com:5432/app"')
    assert "connection_string" in [f.category for f in result.findings]
    assert "hunter2" not in result.text


def test_odbc_connection_string():
    assert "connection_string" in categories("Server=db1;Database=app;Password=secret123;")


def test_database_password_assignment():
    assert "database_credential" in categories('DB_PASSWORD = "prodpassword123"')


def test_generic_password_assignment():
    assert "password" in categories('password = "P@ssw0rd!2024"')


def test_generic_api_key_assignment():
    assert "api_key" in categories('api_key = "abcdefghijklmnop"')


def test_generic_secret_assignment():
    assert "generic_secret" in categories('secret = "some-long-secret-value"')


# -- entropy ---------------------------------------------------------------


def test_high_entropy_token_is_caught():
    token = "aZ3kQ9xL2mNpR7vT4wY8bC1dF6gH0jK5sU"
    result = scan_text(f"value = {token}")
    assert any(f.category == "high_entropy" for f in result.findings)


def test_hex_digest_is_not_a_secret():
    """A checksum is not a credential."""
    assert not any(f.category == "high_entropy" for f in scan_text("h = " + "a1b2c3d4" * 8).findings)


def test_short_random_string_is_ignored():
    assert scan_text("value = aZ3kQ9xL").clean


def test_entropy_can_be_disabled():
    token = "aZ3kQ9xL2mNpR7vT4wY8bC1dF6gH0jK5sU"
    assert scan_text(f"v = {token}", include_entropy=False).clean


def test_entropy_threshold_is_declared():
    assert 0 < ENTROPY_THRESHOLD < 8


# -- false positives -------------------------------------------------------


def test_environment_lookup_is_not_a_secret():
    assert scan_text('api_key = os.environ["API_KEY"]').clean


def test_getenv_is_not_a_secret():
    assert scan_text('token = os.getenv("TOKEN")').clean


@pytest.mark.parametrize("value", ["changeme", "placeholder", "your_api_key", "TODO", "example"])
def test_placeholder_values_are_not_secrets(value):
    assert scan_text(f'password = "{value}"').clean


def test_numeric_configuration_is_untouched():
    assert scan_text("max_tokens = 4096").clean


def test_short_value_is_ignored():
    assert scan_text('secret = "abc"').clean


def test_already_redacted_text_is_not_rescanned():
    assert scan_text('password = "<REDACTED_PASSWORD>"').clean


def test_shell_variable_reference_is_not_a_secret():
    assert scan_text('password = "${DB_PASSWORD}"').clean


# -- mechanics -------------------------------------------------------------


def test_empty_input():
    result = scan_text("")
    assert result.clean
    assert result.text == ""


def test_clean_source_is_returned_unchanged():
    source = "def add(a, b):\n    return a + b\n"
    result = scan_text(source)
    assert result.text == source
    assert result.clean


def test_finding_never_carries_the_secret():
    """The whole point: a finding is metadata, not the credential."""
    result = scan_text('password = "hunter2SuperSecret"')
    for finding in result.findings:
        assert "hunter2" not in finding.model_dump_json()


def test_findings_record_line_numbers():
    text = "x = 1\ny = 2\npassword = 'longsecretvalue'\n"
    assert scan_text(text).findings[0].line == 3


def test_multiple_secrets_in_one_text():
    text = 'password = "longsecretvalue"\ntoken = "ghp_' + "a" * 36 + '"'
    assert len(scan_text(text).findings) >= 2


def test_specific_rule_wins_over_generic():
    """AWS keys report as AWS, not as a generic secret."""
    assert categories('AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPx"') == [
        "aws_secret_access_key"
    ]


def test_scan_lines_preserves_line_count():
    text = "a = 1\npassword = 'longsecretvalue'\nb = 2\n"
    assert len(scan_lines(text).text.splitlines()) == len(text.splitlines())


def test_scan_lines_reports_correct_line_numbers():
    text = "a = 1\nb = 2\npassword = 'longsecretvalue'\n"
    assert scan_lines(text).findings[0].line == 3


def test_contains_secret_helper():
    assert contains_secret('password = "longsecretvalue"')
    assert not contains_secret("def f():\n    return 1")


def test_category_counts():
    findings = [
        SecretFinding(category="password", detector="d"),
        SecretFinding(category="password", detector="d"),
        SecretFinding(category="jwt", detector="d"),
    ]
    assert category_counts(findings) == {"jwt": 1, "password": 2}


def test_result_categories():
    text = 'password = "longsecretvalue"\ntoken = "ghp_' + "a" * 36 + '"'
    counts = scan_text(text).categories()
    assert counts["password"] == 1
    assert counts["github_token"] == 1


def test_scanning_is_deterministic():
    text = 'password = "longsecretvalue"\nkey = "AKIAIOSFODNN7EXAMPLE"'
    first = scan_text(text)
    second = scan_text(text)
    assert first.text == second.text
    assert [f.category for f in first.findings] == [f.category for f in second.findings]


def test_every_rule_has_a_placeholder():
    for rule in RULES:
        assert rule.category in PLACEHOLDERS, rule.category


def test_every_placeholder_is_marked_redacted():
    assert all(v.startswith("<REDACTED") and v.endswith(">") for v in PLACEHOLDERS.values())


def test_rescanning_redacted_output_is_stable():
    """Sanitized text must not be re-sanitized into something else."""
    once = scan_text('password = "longsecretvalue"').text
    assert scan_text(once).text == once
