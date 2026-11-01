from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.config import Settings
from backend.services.python_ast_parser import ParsedModule
from backend.services.role_classifier import RolePrediction
from backend.services.role_llm_classifier import BatchRoleFileResult, classify_ambiguous_batch


@pytest.mark.asyncio
async def test_single_batch_llm_call():
    settings = Settings(stub_mode=False, llm_provider="anthropic", anthropic_api_key="key")
    queue = [
        ("engine.py", ParsedModule()),
        ("core.py", ParsedModule(functions=["run"])),
    ]
    ast_preds = {
        "engine.py": RolePrediction(role="internal-util", confidence=0.4, role_source="ast"),
        "core.py": RolePrediction(role="internal-util", confidence=0.4, role_source="ast"),
    }

    mock_output = MagicMock()
    mock_output.files = [
        BatchRoleFileResult(path="engine.py", role="internal-util", confidence=0.94),
        BatchRoleFileResult(path="core.py", role="internal-util", confidence=0.91),
    ]

    with patch("backend.services.role_llm_classifier.LLMService") as mock_cls:
        instance = mock_cls.return_value
        instance.structured = AsyncMock(return_value=mock_output)
        result = await classify_ambiguous_batch(queue, settings=settings, ast_predictions=ast_preds)

    assert instance.structured.await_count == 1
    assert len(result) == 2
    assert result["engine.py"].role_source == "llm"


@pytest.mark.asyncio
async def test_graceful_llm_failure_fallback():
    settings = Settings(stub_mode=False, anthropic_api_key="key")
    queue = [("engine.py", ParsedModule())]
    ast_preds = {
        "engine.py": RolePrediction(role="data-access", confidence=0.7, role_source="ast"),
    }

    with patch("backend.services.role_llm_classifier.LLMService") as mock_cls:
        instance = mock_cls.return_value
        instance.structured = AsyncMock(side_effect=RuntimeError("api down"))
        result = await classify_ambiguous_batch(queue, settings=settings, ast_predictions=ast_preds)

    assert result["engine.py"].role == "data-access"
    assert result["engine.py"].confidence == 0.50
    assert result["engine.py"].role_source == "ast"
