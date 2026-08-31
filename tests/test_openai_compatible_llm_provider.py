from unittest.mock import MagicMock, patch

from notebook_intelligence.llm_providers.openai_compatible_llm_provider import (
    OpenAICompatibleLLMProvider,
    sanitize_tools_for_openai_compatible,
    strip_reasoning_fields,
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


@patch("openai.OpenAI")
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


def test_strip_reasoning_fields_removes_reasoning_keys_without_mutating_input():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "hi", "reasoning_content": "thinking...", "reasoning": "more"},
        "not-a-dict",
    ]

    stripped = strip_reasoning_fields(messages)

    assert "reasoning_content" not in stripped[1]
    assert "reasoning" not in stripped[1]
    assert stripped[1]["content"] == "hi"
    assert stripped[1]["role"] == "assistant"
    assert stripped[2] == "not-a-dict"
    # original messages must not be mutated
    assert messages[1]["reasoning_content"] == "thinking..."
    assert messages[1]["reasoning"] == "more"


@patch("notebook_intelligence.llm_providers.openai_compatible_llm_provider.OpenAI")
def test_openai_compatible_chat_model_strips_reasoning_before_request(mock_openai_cls):
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

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "prev", "reasoning_content": "", "reasoning": "x"},
    ]

    model.completions(messages=messages)

    outbound = mock_client.chat.completions.create.call_args.kwargs["messages"]
    for m in outbound:
        assert "reasoning_content" not in m
        assert "reasoning" not in m
    # NBI's stored history must be left intact for the next turn
    assert messages[1]["reasoning_content"] == ""
    assert messages[1]["reasoning"] == "x"
