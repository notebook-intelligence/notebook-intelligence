from unittest.mock import Mock, patch

from openai import omit

from notebook_intelligence.llm_providers.openai_compatible_llm_provider import (
    OpenAICompatibleLLMProvider,
    _parse_temperature,
)
from notebook_intelligence.api import LLMProviderProperty


def test_parse_temperature_omits_missing_values():
    assert _parse_temperature(None) is omit
    assert _parse_temperature(LLMProviderProperty("temperature", "Temperature", "", "   ", True)) is omit


def test_parse_temperature_returns_float_value():
    prop = LLMProviderProperty("temperature", "Temperature", "", "0.25", True)
    assert _parse_temperature(prop) == 0.25


@patch("notebook_intelligence.llm_providers.openai_compatible_llm_provider.OpenAI")
def test_chat_completions_passes_temperature(mock_openai_cls):
    provider = OpenAICompatibleLLMProvider()
    model = provider.chat_models[0]
    model.set_property_value("api_key", "test-key")
    model.set_property_value("model_id", "gpt-4.1")
    model.set_property_value("temperature", "0.2")

    mock_client = Mock()
    mock_response = Mock()
    mock_response.model_dump_json.return_value = '{"choices": []}'
    mock_response.choices = []
    mock_client.chat.completions.create.return_value = mock_response
    mock_openai_cls.return_value = mock_client

    model.completions(messages=[{"role": "user", "content": "hi"}])

    mock_client.chat.completions.create.assert_called_once()
    assert mock_client.chat.completions.create.call_args.kwargs["temperature"] == 0.2


@patch("notebook_intelligence.llm_providers.openai_compatible_llm_provider.OpenAI")
def test_inline_completions_omits_blank_temperature(mock_openai_cls):
    provider = OpenAICompatibleLLMProvider()
    model = provider.inline_completion_models[0]
    model.set_property_value("api_key", "test-key")
    model.set_property_value("model_id", "gpt-4.1")
    model.set_property_value("temperature", "")

    mock_client = Mock()
    mock_response = Mock()
    mock_response.choices = [Mock(message=Mock(content="```python\npass\n```"))]
    mock_client.chat.completions.create.return_value = mock_response
    mock_openai_cls.return_value = mock_client

    cancel_token = Mock(is_cancel_requested=False)
    result = model.inline_completions("", "", "python", "test.py", Mock(), cancel_token)

    assert result.strip() == "pass"
    mock_client.chat.completions.create.assert_called_once()
    assert mock_client.chat.completions.create.call_args.kwargs["temperature"] is omit
