# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from typing import Any

import requests
from notebook_intelligence.api import ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, CancelToken, ChatResponse, CompletionContext
from notebook_intelligence import github_copilot as gh_copilot
from notebook_intelligence.github_copilot import chat as copilot_chat, generate_copilot_headers, inline_completions
import logging

log = logging.getLogger(__name__)

GH_COPILOT_EXCLUDED_MODELS = set(["o1"])

class GitHubCopilotChatModel(ChatModel):
    def __init__(self, provider: LLMProvider, model_id: str, model_name: str, context_window: int, supports_tools: bool, context_window_is_configured: bool = True):
        super().__init__(provider)
        self._model_id = model_id
        self._model_name = model_name
        self._context_window = context_window
        self._context_window_is_configured = context_window_is_configured
        self._supports_tools = supports_tools

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return self._context_window

    @property
    def context_window_is_configured(self) -> bool:
        return self._context_window_is_configured

    @property
    def supports_tools(self) -> bool:
        return self._supports_tools

    def completions(self, messages: list[dict], tools: list[dict] = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
        return copilot_chat(self._model_id, messages, tools, response, cancel_token, options)

class GitHubCopilotInlineCompletionModel(InlineCompletionModel):
    def __init__(self, provider: LLMProvider, model_id: str, model_name: str):
        super().__init__(provider)
        self._model_id = model_id
        self._model_name = model_name

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return 4096

    def inline_completions(self, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        return inline_completions(self._model_id, prefix, suffix, language, filename, context, cancel_token)

class GitHubCopilotLLMProvider(LLMProvider):
    # Used until the dynamic catalogue from https://api.githubcopilot.com/models
    # is populated. Lets pre-login users see a non-empty dropdown and
    # protects against API outages.
    _FALLBACK_CHAT_MODELS: list[tuple[str, str, int]] = [
        ("gpt-5-mini", "GPT-5 mini", 128000),
        ("gpt-4.1", "GPT-4.1", 128000),
        ("gpt-4o", "GPT-4o", 128000),
        ("gpt-5", "GPT-5", 128000),
        ("gpt-5.3-codex", "GPT-5.3-Codex", 128000),
        ("claude-haiku-4.5", "Claude Haiku 4.5", 144000),
        ("claude-sonnet-4.6", "Claude Sonnet 4.6", 144000),
        ("claude-sonnet-4.5", "Claude Sonnet 4.5", 144000),
        ("claude-sonnet-4", "Claude Sonnet 4", 80000),
        ("claude-opus-4.6", "Claude Opus 4.6", 144000),
        ("gemini-3.1-pro", "Gemini 3.1 Pro", 128000),
        ("gemini-2.5-pro", "Gemini 2.5 Pro", 128000),
    ]

    def __init__(self):
        self._inline_completion_model_gpt41 = GitHubCopilotInlineCompletionModel(self, "gpt-41-copilot", "GPT-4.1 Copilot")
        self._inline_completion_model_gpt4o = GitHubCopilotInlineCompletionModel(self, "gpt-4o-copilot", "GPT-4o Copilot")
        self._inline_completion_model_codex = GitHubCopilotInlineCompletionModel(self, "copilot-codex", "Copilot Codex")

    def _build_chat_models(self) -> list[ChatModel]:
        cached = gh_copilot.copilot_models_cache
        if cached:
            return [
                GitHubCopilotChatModel(
                    self,
                    entry["id"],
                    entry["name"],
                    entry["context_window"],
                    True,
                    entry.get("context_window_is_configured", True),
                )
                for entry in cached
            ]
        return [
            GitHubCopilotChatModel(
                self,
                model_id,
                model_name,
                ctx,
                True,
                False,
            )
            for (model_id, model_name, ctx) in self._FALLBACK_CHAT_MODELS
        ]

    @property
    def id(self) -> str:
        return "github-copilot"
    
    @property
    def name(self) -> str:
        return "GitHub Copilot"

    @property
    def chat_models(self) -> list[ChatModel]:
        # Rebuilt per call so the dropdown reflects the current
        # copilot_models_cache without explicit invalidation paths.
        return self._build_chat_models()
    
    @property
    def inline_completion_models(self) -> list[InlineCompletionModel]:
        return [self._inline_completion_model_gpt41, self._inline_completion_model_gpt4o, self._inline_completion_model_codex]
    
    @property
    def embedding_models(self) -> list[EmbeddingModel]:
        return []
