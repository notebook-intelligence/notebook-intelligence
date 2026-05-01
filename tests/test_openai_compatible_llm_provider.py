from unittest.mock import MagicMock, patch

from notebook_intelligence.llm_providers.openai_compatible_llm_provider import (
    OpenAICompatibleLLMProvider,
    sanitize_tools_for_openai_compatible,
)


def test_sanitize_tools_for_openai_compatible_removes_function_strict_without_mutating_input():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": "Run python",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    ]

    sanitized = sanitize_tools_for_openai_compatible(tools)

    assert sanitized[0]["function"].get("strict") is None
    assert tools[0]["function"]["strict"] is True


@patch("notebook_intelligence.llm_providers.openai_compatible_llm_provider.OpenAI")
def test_openai_compatible_chat_model_drops_strict_before_request(mock_openai_cls):
    provider = OpenAICompatibleLLMProvider()
    model = provider.chat_models[0]
    model.set_property_value("model_id", "test-model")
    model.set_property_value("api_key", "test-key")
    model.set_property_value("base_url", "https://example.com/v1")

    mock_client = MagicMock()
    mock_openai_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.model_dump_json.return_value = '{"choices": [{"message": {"content": "ok"}}]}'
    mock_response.choices = [MagicMock(message=MagicMock(reasoning_content=None, reasoning=None))]
    mock_client.chat.completions.create.return_value = mock_response

    tools = [
        {
            "type": "function",
            "function": {
                "name": "python",
                "description": "Run python",
                "strict": True,
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        }
    ]

    result = model.completions(messages=[{"role": "user", "content": "hi"}], tools=tools)

    assert result["choices"][0]["message"]["content"] == "ok"
    create_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert "strict" not in create_kwargs["tools"][0]["function"]
    assert tools[0]["function"]["strict"] is True
