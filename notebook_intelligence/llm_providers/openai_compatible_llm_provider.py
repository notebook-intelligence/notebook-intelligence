# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
import re
from typing import Any
from notebook_intelligence.api import ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, CancelToken, ChatResponse, CompletionContext, LLMProviderProperty
from openai import OpenAI, omit

INLINE_COMPLETION_SYSTEM_PROMPT = """You are a code completion assistant. Your task is to generate intelligent autocomplete suggestions for the code at the cursor position for given language and active file type. This is not an interactive session, don't ask for clarifying questions, always generate a suggestion. Don't include any explanations for your response, just generate the code. Don't return any thinking or reasoning, just generate the code. You are given a code snippet with a prefix and a suffix. You need to generate a suggestion for the code that fits best in place of <CURSOR/>. You should return only the code that fits best in place of <CURSOR/>. You should provide multiline code if needed. Enclose the code in triple backticks, just return the code in language. You should not return any other text, just the code. DO NOT INCLUDE THE PREFIX OR SUFFIX IN THE RESPONSE. .ipynb files are Jupyter notebook files and for notebook files, you generate suggestions for a cell within the notebook. A cell can be a code cell with code or a markdown cell with markdown text. If the language is markdown, only return markdown text. If you need to install a Python package within a notebook cell code (for .ipynb files), use %pip install <package_name> instead of !pip install <package_name>. Follow the tags very carefully for proper spacing and indentations."""

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

        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model_id,
            messages=messages.copy(),
            tools=tools or omit,
            tool_choice=options.get("tool_choice", omit),
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

        model_id = self.get_property("model_id").value
        base_url_prop = self.get_property("base_url")
        base_url = base_url_prop.value if base_url_prop is not None else None
        base_url = base_url if base_url and base_url.strip() != "" else None
        api_key = self.get_property("api_key").value

        client = OpenAI(base_url=base_url, api_key=api_key)
        resp = client.chat.completions.create(
            model=model_id,
            messages=[
                {"role": "system", "content": INLINE_COMPLETION_SYSTEM_PROMPT},
                {"role": "user", "content": f"""Generate a single suggestion that fits best in place of cursor. The code is below in between <CODE> tags and <CURSOR/> is the placeholder for the code to be filled in. Current language is {language} and the active file is {filename}.

<CODE><PREFIX>{prefix}</PREFIX><CURSOR/><SUFFIX>{suffix}</SUFFIX></CODE>
"""}
            ],
            max_tokens=1000,
            stream=False,
        )

        if cancel_token.is_cancel_requested:
            return ''

        content = resp.choices[0].message.content or ''
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
