"""PII detection: every category, and the code it must not touch.

The scoping of name detection is asserted explicitly. A detector that redacted
every capitalised word would destroy the repair context, so the tests pin both
what it catches and what it deliberately leaves alone.
"""

import pytest

from backend.models.security import PIIFinding
from backend.security.pii_detector import (
    PLACEHOLDERS,
    RULES,
    category_counts,
    contains_pii,
    detect,
    luhn_valid,
)


def categories(text: str, **kwargs) -> list[str]:
    return [f.category for f in detect(text, **kwargs).findings]


# -- direct identifiers ----------------------------------------------------


def test_email_address():
    result = detect("Contact ada@corp.example for support")
    assert "email" in [f.category for f in result.findings]
    assert "ada@corp.example" not in result.text


def test_multiple_emails():
    assert len(detect("a@x.com and b@y.com").findings) == 2


def test_us_ssn():
    result = detect("SSN: 123-45-6789")
    assert "ssn" in [f.category for f in result.findings]
    assert "123-45-6789" not in result.text


@pytest.mark.parametrize("invalid", ["000-12-3456", "666-12-3456", "900-12-3456", "123-00-4567"])
def test_invalid_ssn_ranges_are_not_matched(invalid):
    """The SSA never issued these prefixes; matching them is noise."""
    assert "ssn" not in categories(f"value {invalid}")


def test_credit_card_with_valid_checksum():
    assert "credit_card" in categories('card = "4111 1111 1111 1111"')


def test_credit_card_without_valid_checksum_is_ignored():
    assert "credit_card" not in categories('id = "1234 5678 9012 3456"')


def test_luhn_validator():
    assert luhn_valid("4111111111111111")
    assert not luhn_valid("4111111111111112")
    assert not luhn_valid("123")


def test_version_string_is_not_a_card():
    assert "credit_card" not in categories("version 1.2.3.4.5678")


def test_iban():
    assert "iban" in categories("IBAN GB82WEST12345698765432")


def test_international_phone():
    assert "phone" in categories("Call +1 415 555 0123 now")


def test_us_phone():
    assert "phone" in categories("Call 415-555-0123 now")


def test_street_address():
    result = detect("Ship to 123 Main Street")
    assert "address" in [f.category for f in result.findings]
    assert "Main Street" not in result.text


# -- organisational identifiers -------------------------------------------


def test_employee_id():
    assert "employee_id" in categories('employee_id = "E-4471"')


def test_staff_number_variant():
    assert "employee_id" in categories('staff_number = "S1234"')


def test_customer_id():
    assert "customer_id" in categories('customer_id = "CUST-9911"')


def test_account_id_variant():
    assert "customer_id" in categories('account_number = "AC-1234"')


def test_medical_id():
    assert "medical_id" in categories('patient_id: "MRN-99821"')


def test_medical_id_is_critical():
    finding = next(f for f in detect('patient_id = "MRN-1"').findings if f.category == "medical_id")
    assert finding.severity == "critical"


def test_financial_id():
    assert "financial_id" in categories('routing_number = "021000021"')


def test_internal_username():
    assert "internal_username" in categories('username = "a.lovelace"')


# -- infrastructure --------------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["db-primary.internal", "api.corp", "cache.local", "svc.intranet", "box.private"],
)
def test_internal_hostnames(host):
    assert "internal_hostname" in categories(f'host = "{host}"')


def test_public_hostname_is_not_pii():
    assert "internal_hostname" not in categories('host = "api.github.com"')


@pytest.mark.parametrize("ip", ["10.1.2.3", "192.168.1.1", "172.16.0.1"])
def test_private_ip_ranges(ip):
    assert "ip_address" in categories(f'ip = "{ip}"')


def test_public_ip_is_not_matched():
    assert "ip_address" not in categories('ip = "8.8.8.8"')


def test_loopback_is_not_pii():
    assert detect('host = "127.0.0.1"').clean


# -- names -----------------------------------------------------------------


def test_git_author_string():
    result = detect("Ada Lovelace <ada@example.org>")
    assert "person_name" in [f.category for f in result.findings]
    assert "Ada Lovelace" not in result.text


def test_author_annotation():
    assert "person_name" in categories("@author Grace Hopper")


def test_copyright_holder():
    assert "person_name" in categories("Copyright (c) 2024 Grace Hopper")


def test_known_name_roster():
    result = detect("Reviewed by Alan Turing", known_names=("Alan Turing",))
    assert "person_name" in [f.category for f in result.findings]
    assert "Alan Turing" not in result.text


def test_roster_matches_longest_name_first():
    """Redacting 'Ada' first would leave 'Lovelace' exposed."""
    result = detect("Ada Lovelace wrote this", known_names=("Ada", "Ada Lovelace"))
    assert "Lovelace" not in result.text


def test_roster_is_case_insensitive():
    assert not detect("ada lovelace", known_names=("Ada Lovelace",)).clean


def test_bare_name_without_annotation_is_not_detected():
    """Scoped by design — see the module docstring."""
    assert detect("Grace Hopper").clean


# -- false positives -------------------------------------------------------


def test_class_names_are_untouched():
    assert detect("class SessionParser:\n    pass").clean


def test_prose_capitalisation_is_untouched():
    assert detect("The Session Parser handles Requests").clean


def test_function_definitions_are_untouched():
    assert detect("def validate_token(token):\n    return True").clean


def test_import_statements_are_untouched():
    assert detect("from backend.services import llm_gateway").clean


def test_placeholder_email_is_ignored():
    assert detect("user@example.com").clean


def test_already_redacted_is_not_rescanned():
    assert detect("email = <REDACTED_EMAIL>").clean


# -- mechanics -------------------------------------------------------------


def test_empty_input():
    assert detect("").clean


def test_finding_never_carries_the_value():
    result = detect("SSN: 123-45-6789")
    for finding in result.findings:
        assert "123-45-6789" not in finding.model_dump_json()


def test_line_numbers_are_recorded():
    assert detect("a = 1\nb = 2\nemail = 'x@y.com'\n").findings[0].line == 3


def test_contains_pii_helper():
    assert contains_pii("email x@y.com")
    assert not contains_pii("def f(): return 1")


def test_category_counts():
    findings = [
        PIIFinding(category="email", detector="d"),
        PIIFinding(category="email", detector="d"),
        PIIFinding(category="ssn", detector="d"),
    ]
    assert category_counts(findings) == {"email": 2, "ssn": 1}


def test_result_categories():
    counts = detect("a@b.com and 123-45-6789").categories()
    assert counts == {"email": 1, "ssn": 1}


def test_detection_is_deterministic():
    text = "a@b.com, 123-45-6789, 10.0.0.1"
    first, second = detect(text), detect(text)
    assert first.text == second.text
    assert [f.category for f in first.findings] == [f.category for f in second.findings]


def test_rescanning_output_is_stable():
    once = detect("Contact a@b.com").text
    assert detect(once).text == once


def test_every_rule_has_a_placeholder():
    for rule in RULES:
        assert rule.category in PLACEHOLDERS, rule.category


def test_every_placeholder_is_marked_redacted():
    assert all(v.startswith("<REDACTED") for v in PLACEHOLDERS.values())


def test_mixed_pii_is_all_redacted():
    text = "Ada Lovelace <ada@x.com> at 10.0.0.5, SSN 123-45-6789"
    result = detect(text)
    for leaked in ("ada@x.com", "123-45-6789", "10.0.0.5", "Ada Lovelace"):
        assert leaked not in result.text
