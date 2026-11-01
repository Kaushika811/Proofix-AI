from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from backend.config import Settings
from backend.services.llm import LLMService


class SampleOutput(BaseModel):
    answer: str


@pytest.mark.asyncio
async def test_structured_anthropic_extracts_json():
    settings = Settings(
        stub_mode=False,
        llm_provider="anthropic",
        anthropic_api_key="test-key",
    )
    service = LLMService(settings)

    mock_block = MagicMock()
    mock_block.text = 'Here is JSON:\n{"answer": "from-claude"}'
    mock_response = MagicMock()
    mock_response.content = [mock_block]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    service._anthropic = mock_client
    result = await service.structured("prompt", SampleOutput)

    assert result.answer == "from-claude"


def test_llm_configured_respects_provider():
    assert Settings(llm_provider="anthropic", anthropic_api_key="x").llm_configured() is True
    assert Settings(llm_provider="anthropic", anthropic_api_key="").llm_configured() is False
    assert Settings(llm_provider="mistral", mistral_api_key="x").llm_configured() is True
    assert Settings(llm_provider="mistral", mistral_api_key="").llm_configured() is False
    assert Settings(llm_provider="openai", openai_api_key="sk-test").llm_configured() is True
    assert Settings(llm_provider="openai", openai_api_key="").llm_configured() is False
    assert Settings(llm_provider="gemini", gemini_api_key="gem-test").llm_configured() is True
    assert Settings(llm_provider="gemini", gemini_api_key="").llm_configured() is False
    assert Settings(llm_provider="ollama", ollama_base_url="http://localhost:11434").llm_configured() is True
    assert Settings(llm_provider="ollama", ollama_base_url="").llm_configured() is False
    assert Settings(llm_provider="lmstudio", lmstudio_base_url="http://localhost:1234").llm_configured() is True
    assert Settings(llm_provider="lmstudio", lmstudio_base_url="").llm_configured() is False
    assert Settings(llm_provider="vllm", vllm_base_url="http://localhost:8000").llm_configured() is True
    assert Settings(llm_provider="vllm", vllm_base_url="").llm_configured() is False
    assert Settings(llm_provider="tgi", tgi_base_url="http://localhost:8080").llm_configured() is True
    assert Settings(llm_provider="tgi", tgi_base_url="").llm_configured() is False


@pytest.mark.asyncio
async def test_ensure_available_raises_configuration_error_when_unconfigured():
    from backend.services.llm_gateway import LLMConfigurationError

    service = LLMService(Settings(stub_mode=False, llm_provider="anthropic", anthropic_api_key=""))
    with pytest.raises(LLMConfigurationError, match="ANTHROPIC_API_KEY"):
        await service.structured("prompt", SampleOutput)

    service_mistral = LLMService(Settings(stub_mode=False, llm_provider="mistral", mistral_api_key=""))
    with pytest.raises(LLMConfigurationError, match="MISTRAL_API_KEY"):
        await service_mistral.structured("prompt", SampleOutput)



@pytest.mark.asyncio
async def test_structured_mistral_uses_json_mode():
    settings = Settings(
        stub_mode=False,
        llm_provider="mistral",
        mistral_api_key="test-key",
        mistral_model="codestral-latest",
    )
    service = LLMService(settings)

    mock_message = MagicMock()
    mock_message.content = '{"answer": "from-mistral"}'
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.complete_async = AsyncMock(return_value=mock_response)

    service._mistral = mock_client
    result = await service.structured("prompt", SampleOutput)

    assert result.answer == "from-mistral"
    mock_client.chat.complete_async.assert_awaited_once()
    kwargs = mock_client.chat.complete_async.await_args.kwargs
    assert kwargs["model"] == "codestral-latest"
    assert kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_structured_raises_in_stub_mode():
    service = LLMService(Settings(stub_mode=True, llm_provider="mistral", mistral_api_key="x"))
    with pytest.raises(RuntimeError, match="stub mode"):
        await service.structured("prompt", SampleOutput)
