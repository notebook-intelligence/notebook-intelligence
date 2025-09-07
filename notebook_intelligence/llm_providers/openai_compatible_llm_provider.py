# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
from typing import Any
from notebook_intelligence.api import ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, CancelToken, ChatResponse, CompletionContext, LLMProviderProperty
from openai import NOT_GIVEN, OpenAI

DEFAULT_CONTEXT_WINDOW = 4096

class OpenAICompatibleChatModel(ChatModel):
    def __init__(self, provider: "OpenAICompatibleLLMProvider"):
        super().__init__(provider)
        self._provider = provider
        self._properties = [
            LLMProviderProperty("api_key", "API key", "API key", "", False),
            LLMProviderProperty("model_id", "Model", "Model (must support streaming)", "", False),
            LLMProviderProperty("base_url", "Base URL", "Base URL", "", True),
            LLMProviderProperty("context_window", "Context window", "Context window length", "", True),
        ]

    @property
    def id(self) -> str:
        return "openai-compatible-chat-model"
    
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
        base_url_prop = self.get_property("base_url")
        base_url = base_url_prop.value if base_url_prop is not None else None
        base_url = base_url if base_url.strip() != "" else None
        api_key = self.get_property("api_key").value
        has_tools = tools is not None and len(tools) > 0

        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model_id,
            messages=messages.copy(),
            tools=tools if has_tools else NOT_GIVEN,
            tool_choice=options.get("tool_choice", None) if has_tools else NOT_GIVEN,
            stream=stream,
        )

        if stream:
            for chunk in resp:
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
            json_resp = json.loads(resp.model_dump_json())
            return json_resp
    
class OpenAICompatibleInlineCompletionModel(InlineCompletionModel):
    def __init__(self, provider: "OpenAICompatibleLLMProvider"):
        super().__init__(provider)
        self._provider = provider
        self._properties = [
            LLMProviderProperty("api_key", "API key", "API key", "", False),
            LLMProviderProperty("model_id", "Model", "Model", "", False),
            LLMProviderProperty("base_url", "Base URL", "Base URL", "", True),
            LLMProviderProperty("context_window", "Context window", "Context window length", "", True),
        ]

    @property
    def id(self) -> str:
        return "openai-compatible-inline-completion-model"
    
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
        base_url_prop = self.get_property("base_url")
        base_url = base_url_prop.value if base_url_prop is not None else None
        base_url = base_url if base_url.strip() != "" else None
        api_key = self.get_property("api_key").value

        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.completions.create(
            model=model_id,
            prompt=prefix,
            suffix=suffix,
            stream=False,
        )

        return resp.choices[0].text

class OpenAICompatibleLLMProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self._chat_model = OpenAICompatibleChatModel(self)
        self._inline_completion_model = OpenAICompatibleInlineCompletionModel(self)

    @property
    def id(self) -> str:
        return "openai-compatible"
    
    @property
    def name(self) -> str:
        return "OpenAI Compatible"

    @property
    def chat_models(self) -> list[ChatModel]:
        return [self._chat_model]
    
    @property
    def inline_completion_models(self) -> list[InlineCompletionModel]:
        return [self._inline_completion_model]
    
    @property
    def embedding_models(self) -> list[EmbeddingModel]:
        return []
