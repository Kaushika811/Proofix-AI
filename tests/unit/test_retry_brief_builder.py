"""Unit tests for RetryBrief construction from validation failures."""

from backend.models.validation import ValidationFailure
from backend.services.retry_brief_builder import build_retry_brief, retry_reason_from_brief

AUTH_PATCH = {
    "file": "vulnapi/auth.py",
    "original": "def validate_token():\n    return True\n",
    "patched": "def validate_token():\n    return False\n",
}


def test_build_retry_brief_includes_validation_context():
    failure = ValidationFailure(
        failing_test="tests/test_auth.py::test_expired_token_rejected",
        assertion_message="AssertionError: assert True is False",
        expected_value="False",
        actual_value="True",
        validation_stage="mutation",
    )
    reproduction = {"failing_test": "tests/test_auth.py::test_expired_token_rejected"}

    brief = build_retry_brief(
        failure,
        attempt=1,
        patch_bundle={"patches": [AUTH_PATCH]},
        reproduction=reproduction,
    )

    assert brief.attempt == 1
    assert brief.previous_patch_summary is not None
    # pytest's own expected value, unedited. This used to read
    # `validate_token(expired_token) == False` — the builder prepended a
    # function and parameter name from the `vulnapi` fixture whenever a test
    # name contained "token", producing an assertion the repository under
    # repair never wrote (B-B03).
    assert brief.expected_behaviour == "False"
    assert brief.actual_behaviour == "True"
    # The instruction names the test that failed, not a conclusion about *why*.
    # "Previous attempt still accepted expired JWT tokens" used to appear here
    # for any repository whose test name contained "expired" — a claim about
    # behaviour nobody measured, on a cache or a session or a lock (B-B03).
    instruction = brief.retry_instruction or ""
    assert "JWT" not in instruction
    assert "tests/test_auth.py::test_expired_token_rejected" in instruction
    assert "DIFFERENT implementation" in instruction
    assert brief.validation_failure is not None


def test_retry_reason_from_brief():
    brief = build_retry_brief(
        ValidationFailure(
            failing_test="tests/test_auth.py::test_expired_token_rejected",
            assertion_message="AssertionError: assert True is False",
            validation_stage="mutation",
        ),
        attempt=1,
    )

    reason = retry_reason_from_brief(brief)
    assert reason.startswith("pytest:")
    assert "assert True is False" in reason
