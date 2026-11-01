"""Privacy guard: mask credential values, change nothing else.

The guard runs on code that is about to leave the process. Two properties matter
equally — secrets must not survive, and the masked source must remain the same
program structurally, or the repair it feeds will be wrong.
"""

import ast

import pytest

from backend.services.privacy_guard import (
    scan_source,
    scan_text,
    shannon_entropy,
)


def masked(source: str) -> str:
    return scan_source(source, "m.py").source


# -- structural detection --------------------------------------------------


def test_masks_named_secret_assignment():
    out = masked('SECRET_KEY = "super-secret-value"\n')
    assert "super-secret-value" not in out
    assert "<REDACTED_SECRET>" in out
    assert out.startswith("SECRET_KEY = ")


def test_masks_password_and_token_and_key_by_name():
    out = masked(
        'password = "hunter2"\n'
        'api_key = "abc123"\n'
        'auth_token = "xyz789"\n'
    )
    assert "hunter2" not in out and "abc123" not in out and "xyz789" not in out
    assert "<REDACTED_PASSWORD>" in out
    assert "<REDACTED_KEY>" in out
    assert "<REDACTED_TOKEN>" in out


def test_masks_keyword_argument_values():
    out = masked('conn = connect(host="localhost", password="s3cret")\n')
    assert "s3cret" not in out
    assert "localhost" in out  # not a credential


def test_masks_dict_values_by_key_name():
    out = masked('CONFIG = {"api_key": "live-key-value", "region": "eu-west-1"}\n')
    assert "live-key-value" not in out
    assert "eu-west-1" in out


def test_masks_annotated_assignment():
    out = masked('SECRET: str = "value-here"\n')
    assert "value-here" not in out


# -- format detection ------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abc", "<REDACTED_TOKEN>"),
        ("AKIAIOSFODNN7EXAMPLE", "<REDACTED_KEY>"),
        ("sk-abcdefghijklmnopqrstuvwxyz", "<REDACTED_KEY>"),
        ("ghp_abcdefghijklmnopqrstuvwxyz1234", "<REDACTED_TOKEN>"),
        ("xoxb-1234567890-abcdefghij", "<REDACTED_TOKEN>"),
        ("postgres://user:pw@host:5432/db", "<REDACTED_SECRET>"),
        ("dev@example.com", "<REDACTED_EMAIL>"),
    ],
)
def test_masks_by_value_format_regardless_of_name(value, expected):
    out = masked(f'harmless_name = "{value}"\n')
    assert value not in out
    assert expected in out


def test_masks_pem_private_key():
    out = masked('CERT = """-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----"""\n')
    assert "MIIabc" not in out
    assert "REDACTED" in out


def test_masks_ssh_public_key():
    out = masked('k = "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDabcdefghijklmnop"\n')
    assert "AAAAB3NzaC1yc2E" not in out


# -- entropy detection -----------------------------------------------------


def test_masks_unlabelled_high_entropy_literal():
    out = masked('value = "aZ3kP9xQ7wL2mN8vB4tR6yU1iO5pA0sD"\n')
    assert "aZ3kP9xQ7wL2mN8vB4tR6yU1iO5pA0sD" not in out


def test_low_entropy_long_string_is_left_alone():
    out = masked('MESSAGE = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n')
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in out


def test_entropy_calculation():
    assert shannon_entropy("aaaa") == 0.0
    assert shannon_entropy("abcd") == 2.0
    assert shannon_entropy("") == 0.0


# -- structure preservation ------------------------------------------------


def test_masked_source_still_parses():
    source = (
        "import os\n\n"
        'SECRET = "abc123xyz"\n\n'
        "def get_secret() -> str:\n"
        '    """Docstring."""\n'
        "    return SECRET\n"
    )
    out = masked(source)
    ast.parse(out)  # must not raise


def test_identifiers_are_never_modified():
    source = (
        'SECRET_KEY = "value"\n'
        "def rotate_secret_key(api_key_name):\n"
        "    return api_key_name\n"
    )
    out = masked(source)
    assert "SECRET_KEY" in out
    assert "def rotate_secret_key(api_key_name):" in out


def test_control_flow_and_comments_survive():
    source = (
        "# comment about the secret\n"
        'TOKEN = "abc"\n'
        "if TOKEN:\n"
        "    for key in items:\n"
        "        print(key)\n"
    )
    out = masked(source)
    assert "# comment about the secret" in out
    assert "for key in items:" in out
    assert "print(key)" in out


def test_loop_variable_named_key_is_not_masked():
    out = masked("for key in mapping:\n    use(key)\n")
    assert "REDACTED" not in out


def test_allowlisted_names_are_not_masked():
    out = masked('algorithm = "HS256"\ntoken_type = "bearer"\n')
    assert "HS256" in out
    assert "bearer" in out


def test_empty_value_is_not_masked():
    """An unset secret may be the bug; hiding it would hide the defect."""
    out = masked('SECRET = ""\n')
    assert out.strip() == 'SECRET = ""'


def test_non_secret_code_is_returned_unchanged():
    source = "def add(a, b):\n    return a + b\n"
    result = scan_source(source, "m.py")
    assert result.source == source
    assert result.status == "clean"
    assert result.redactions == []


# -- reporting -------------------------------------------------------------


def test_redactions_are_auditable():
    result = scan_source('SECRET_KEY = "abc123"\n', "config.py")
    assert len(result.redactions) == 1
    redaction = result.redactions[0]
    assert redaction.file == "config.py"
    assert redaction.line == 1
    assert redaction.kind == "REDACTED_SECRET"
    assert redaction.identifier == "SECRET_KEY"
    assert result.status == "masked"


def test_redaction_never_records_the_secret_value():
    result = scan_source('SECRET_KEY = "topsecret"\n', "config.py")
    assert "topsecret" not in result.model_dump_json() if hasattr(result, "model_dump_json") else True
    assert all("topsecret" not in str(r.model_dump()) for r in result.redactions)


def test_multiple_secrets_on_multiple_lines():
    result = scan_source('A_SECRET = "one"\nB_TOKEN = "abc123def"\nC = 3\n', "m.py")
    assert len(result.redactions) == 2
    assert [r.line for r in result.redactions] == [1, 2]
    assert "C = 3" in result.source


# -- false positives -------------------------------------------------------
#
# A guard that masks ordinary configuration corrupts the code under repair and
# inflates the context for nothing. These are the cases that were observed
# firing against this repository's own source.


@pytest.mark.parametrize(
    "source",
    [
        "llm_max_tokens: int = 4096\n",
        "CHARS_PER_TOKEN = 4\n",
        "key_length = 32\n",
        "MAX_RETRIES = 3\n",
    ],
)
def test_numeric_config_under_credential_name_is_not_masked(source):
    """A number under a credential-ish name is configuration, never a secret."""
    assert scan_source(source, "config.py").redactions == []


@pytest.mark.parametrize(
    "source",
    [
        'token_source = "estimated"\n',
        'auth_mode = "bearer"\n',
        'key_order = "ascending"\n',
        'cert_format = "pem"\n',
    ],
)
def test_enum_style_value_under_weak_name_is_not_masked(source):
    """`token`/`key`/`auth` appear everywhere; the value has to look generated."""
    assert scan_source(source, "m.py").redactions == []


def test_weak_name_with_generated_looking_value_is_still_masked():
    result = scan_source('auth_token = "a1b2c3d4e5"\n', "m.py")
    assert result.redactions
    assert "a1b2c3d4e5" not in result.source


def test_strong_name_masks_even_a_plain_word():
    """`password`/`secret` need no corroboration — the name is proof enough."""
    assert scan_source('password = "correct"\n', "m.py").redactions


# -- unparseable fallback --------------------------------------------------


def test_unparseable_source_still_masked():
    """Repair runs routinely handle broken files; they must not leak."""
    broken = 'SECRET_KEY = "abc123secret"\ndef oops(\n'
    result = scan_source(broken, "broken.py")
    assert "abc123secret" not in result.source
    assert result.status == "masked"


def test_unparseable_without_secrets_is_unchanged():
    broken = "def oops(\n"
    assert scan_source(broken, "b.py").source == broken


# -- free text -------------------------------------------------------------


def test_scan_text_masks_credentials_in_tracebacks():
    trace = 'Traceback: token=ghp_abcdefghijklmnopqrstuvwxyz1234 failed for dev@example.com'
    result = scan_text(trace)
    assert "ghp_abcdefghijklmnopqrstuvwxyz1234" not in result.source
    assert "dev@example.com" not in result.source
    assert result.status == "masked"


def test_scan_text_leaves_ordinary_prose_alone():
    text = "AssertionError: expected token to be rejected"
    assert scan_text(text).source == text


def test_empty_input_is_safe():
    assert scan_source("", "m.py").source == ""
    assert scan_text("").source == ""
