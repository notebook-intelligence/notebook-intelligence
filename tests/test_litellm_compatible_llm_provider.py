from notebook_intelligence.base_chat_participant import (
    _chat_history_context_window,
)
from notebook_intelligence.llm_providers.litellm_compatible_llm_provider import (
    DEFAULT_CONTEXT_WINDOW,
    LiteLLMCompatibleLLMProvider,
)


def test_blank_context_window_does_not_trigger_history_pruning():
    model = LiteLLMCompatibleLLMProvider().chat_models[0]

    assert model.context_window == DEFAULT_CONTEXT_WINDOW
    assert model.context_window_is_configured is False
    assert _chat_history_context_window(model) == 0

    model.set_property_value("context_window", "32768")

    assert model.context_window_is_configured is True
    assert _chat_history_context_window(model) == 32768
