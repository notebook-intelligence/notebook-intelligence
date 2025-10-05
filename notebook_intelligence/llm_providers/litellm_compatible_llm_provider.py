# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
from typing import Any
from notebook_intelligence.api import ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, CancelToken, ChatResponse, CompletionContext, LLMProviderProperty
import litellm

DEFAULT_CONTEXT_WINDOW = 4096

class LiteLLMCompatibleChatModel(ChatModel):
    def __init__(self, provider: "LiteLLMCompatibleLLMProvider"):
        super().__init__(provider)
        self._provider = provider
        self._properties = [
            LLMProviderProperty("model_id", "Model", "Model (must support streaming)", "", False),
            LLMProviderProperty("base_url", "Base URL", "Base URL", "", False),
            LLMProviderProperty("api_key", "API key", "API key", "", True),
            LLMProviderProperty("context_window", "Context window", "Context window length", "", True),
        ]

    @property
    def id(self) -> str:
        return "litellm-compatible-chat-model"
    
    @property
    def name(self) -> str:
        return self.get_property("model_id").value
    
    @property
    def context_window(self) -> int:
        try:
            context_window_prop = self.get_property("context_window")
            if context_window_prop is not None:
                context_window = int(context_window_prop.value)
            return context_window
        except:
            return DEFAULT_CONTEXT_WINDOW

    def completions(self, messages: list[dict], tools: list[dict] = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
        stream = response is not None
        model_id = self.get_property("model_id").value
        base_url = self.get_property("base_url").value
        api_key_prop = self.get_property("api_key")
        api_key = api_key_prop.value if api_key_prop is not None else None        

        litellm_resp = litellm.completion(
            model=model_id,
            messages=messages.copy(),
            tools=tools,
            tool_choice=options.get("tool_choice", None),
            api_base=base_url,
            api_key=api_key,
            stream=stream,
        )

        if stream:
            for chunk in litellm_resp:
                response.stream({
                        "choices": [{
                            "delta": {
                                "role": chunk.choices[0].delta.role,
                                "content": chunk.choices[0].delta.content
                            }
                        }]
                    })
            response.finish()
            return
        else:
            json_resp = json.loads(litellm_resp.model_dump_json())
            return json_resp
    
class LiteLLMCompatibleInlineCompletionModel(InlineCompletionModel):
    def __init__(self, provider: "LiteLLMCompatibleLLMProvider"):
        super().__init__(provider)
        self._provider = provider
        self._properties = [
            LLMProviderProperty("model_id", "Model", "Model", "", False),
            LLMProviderProperty("base_url", "Base URL", "Base URL", "", False),
            LLMProviderProperty("api_key", "API key", "API key", "", True),
            LLMProviderProperty("context_window", "Context window", "Context window length", "", True),
        ]

    @property
    def id(self) -> str:
        return "litellm-compatible-inline-completion-model"
    
    @property
    def name(self) -> str:
        return "Inline Completion Model"
    
    @property
    def context_window(self) -> int:
        try:
            context_window_prop = self.get_property("context_window")
            if context_window_prop is not None:
                context_window = int(context_window_prop.value)
            return context_window
        except:
            return DEFAULT_CONTEXT_WINDOW

    def inline_completions(self, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        model_id = self.get_property("model_id").value
        base_url = self.get_property("base_url").value
        api_key_prop = self.get_property("api_key")
        api_key = api_key_prop.value if api_key_prop is not None else None

        messages = [
            {"role": "system", "content": "You are a code completion engine. Predict only the next code tokens."},
            {"role": "user", "content": prefix},
            {"role": "assistant", "content": suffix}
        ]

        litellm_resp = litellm.completion(
            model=model_id,
            messages=messages,
            stream=False,
            max_completion_tokens = 2000,
            stop = ['<END>', '```'],
            top_p = 1,                
            api_base=base_url,
            api_key=api_key,
        )

        return litellm_resp.choices[0].message.content

class LiteLLMCompatibleLLMProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self._chat_model = LiteLLMCompatibleChatModel(self)
        self._inline_completion_model = LiteLLMCompatibleInlineCompletionModel(self)

    @property
    def id(self) -> str:
        return "litellm-compatible"
    
    @property
    def name(self) -> str:
        return "LiteLLM Compatible"

    @property
    def chat_models(self) -> list[ChatModel]:
        return [self._chat_model]
    
    @property
    def inline_completion_models(self) -> list[InlineCompletionModel]:
        return [self._inline_completion_model]
    
    @property
    def embedding_models(self) -> list[EmbeddingModel]:
        return []
