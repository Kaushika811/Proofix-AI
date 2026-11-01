

from backend.config import Settings
from backend.services.role_classifier import (
    accept_local_prediction,
    classify_file_role,
    is_ambiguous_basename,
)
from backend.services.python_ast_parser import ParsedModule


def test_stage1_auth_filename():
    settings = Settings()
    pred = classify_file_role(
        "vulnapi/auth.py",
        ParsedModule(),
        settings=settings,
    )
    assert pred.role == "auth-boundary"
    assert pred.confidence == 1.0
    assert pred.role_source == "filename"


def test_stage1_config_filename():
    settings = Settings()
    pred = classify_file_role(
        "vulnapi/config.py",
        ParsedModule(),
        settings=settings,
    )
    assert pred.role == "config-surface"
    assert pred.role_source == "filename"


def test_stage2_jwt_ast_signals():
    settings = Settings()
    parsed = ParsedModule(
        functions=["validate_token"],
        imports=["jwt"],
    )
    pred = classify_file_role("src/engine.py", parsed, settings=settings)
    assert pred.role == "auth-boundary"
    assert pred.role_source == "ast"
    assert 0.6 <= pred.confidence <= 0.9


def test_stage3_engine_ambiguous_not_accepted_locally():
    settings = Settings()
    pred = classify_file_role("src/engine.py", ParsedModule(), settings=settings)
    assert is_ambiguous_basename("src/engine.py", settings)
    assert not accept_local_prediction(pred, "src/engine.py", settings)


def test_confidence_thresholds_from_settings():
    settings = Settings(role_confidence_threshold=0.99)
    parsed = ParsedModule(functions=["helper"])
    pred = classify_file_role("src/engine.py", parsed, settings=settings)
    assert pred.role == "internal-util"
    assert not accept_local_prediction(pred, "src/engine.py", settings)

    settings_loose = Settings(
        role_confidence_threshold=0.3,
        role_high_confidence_threshold=0.3,
    )
    assert accept_local_prediction(pred, "src/engine.py", settings_loose)


def test_high_ast_confidence_accepts_ambiguous_basename():
    settings = Settings(role_high_confidence_threshold=0.85)
    parsed = ParsedModule(
        functions=["validate_token", "decode_jwt"],
        imports=["jwt", "oauth"],
        classes=["AdminMiddleware"],
    )
    pred = classify_file_role("utils.py", parsed, settings=settings)
    assert pred.confidence >= 0.85
    assert accept_local_prediction(pred, "utils.py", settings)
