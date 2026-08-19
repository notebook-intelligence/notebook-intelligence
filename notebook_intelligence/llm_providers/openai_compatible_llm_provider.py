# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import copy
import json
import re
from typing import Any
from notebook_intelligence.api import ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, CancelToken, ChatResponse, CompletionContext, LLMProviderProperty
from notebook_intelligence.inline_completion import (
    extract_inline_completion,
    inline_completion_system_prompt,
    inline_completion_user_prompt,
    is_chatbook_inline_language,
)

DEFAULT_CONTEXT_WINDOW = 4096


def sanitize_tools_for_openai_compatible(tools: list[dict] | None) -> list[dict] | None:
    """Drop Structured Outputs-only flags unsupported by many OpenAI-compatible APIs."""
    if tools is None:
        return None

    sanitized_tools = copy.deepcopy(tools)
    for tool in sanitized_tools:
        function_schema = tool.get("function")
        if isinstance(function_schema, dict):
            function_schema.pop("strict", None)
    return sanitized_tools


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
        from openai import OpenAI, omit
        stream = response is not None
        model_id = self.get_property("model_id").value
        base_url_prop = self.get_property("base_url")
        base_url = base_url_prop.value if base_url_prop is not None else None
        base_url = base_url if base_url.strip() != "" else None
        api_key = self.get_property("api_key").value

        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model_id,
            messages=messages.copy(),
            tools=sanitize_tools_for_openai_compatible(tools) or omit,
            tool_choice=options.get("tool_choice", omit),
            stream=stream,
        )

        if stream:
            for chunk in resp:
                if len(chunk.choices) == 0:
                    continue
                delta = chunk.choices[0].delta
                reasoning = getattr(delta, 'reasoning_content', None) or getattr(delta, 'reasoning', None)
                if reasoning is not None:
                    reasoning = str(reasoning)
                response.stream({
                        "choices": [{
                            "delta": {
                                "role": delta.role,
                                "content": delta.content,
                                "reasoning_content": reasoning
                            }
                        }]
                    })
            response.finish()
            return
        else:
            json_resp = json.loads(resp.model_dump_json())
            # Capture reasoning fields if they exist as extra attributes
            for i, choice in enumerate(resp.choices):
                message = choice.message
                reasoning = getattr(message, 'reasoning_content', None) or getattr(message, 'reasoning', None)
                if reasoning:
                    json_resp['choices'][i]['message']['reasoning_content'] = str(reasoning)
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

    def _extract_llm_generated_code(self, text: str) -> str:
        tags = ["<CODE>", "</CODE>", "<PREFIX>", "</PREFIX>", "<SUFFIX>", "</SUFFIX>", "<CURSOR>", "</CURSOR>"]
        for tag in tags:
            text = text.replace(tag, "")
        
        pattern = r'```(?:\w+)?\n?(.*?)```'
        matches = re.findall(pattern, text, re.DOTALL)
        
        if matches:
            code = matches[-1]
            return code
        
        inline_pattern = r'`([^`]+)`'
        inline_matches = re.findall(inline_pattern, text)
        if inline_matches:
            return inline_matches[-1]
        
        return text

    def inline_completions(self, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        if cancel_token.is_cancel_requested:
            return ''

        from openai import OpenAI

        model_id = self.get_property("model_id").value
        base_url_prop = self.get_property("base_url")
        base_url = base_url_prop.value if base_url_prop is not None else None
        base_url = base_url if base_url and base_url.strip() != "" else None
        api_key = self.get_property("api_key").value

        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": inline_completion_system_prompt(language)},
                {"role": "user", "content": inline_completion_user_prompt(prefix, suffix, language, filename)},
            ],
            max_tokens=1000,
            stream=False,
        )

        if cancel_token.is_cancel_requested:
            return ''

        content = resp.choices[0].message.content or ''
        if is_chatbook_inline_language(language):
            return extract_inline_completion(content, language)
        return self._extract_llm_generated_code(content)

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
