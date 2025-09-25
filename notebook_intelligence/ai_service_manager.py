# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
from os import path
import os
import sys
from typing import Dict
import logging
from notebook_intelligence import github_copilot
from notebook_intelligence.api import ButtonData, ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, ChatParticipant, ChatRequest, ChatResponse, CompletionContext, ContextRequest, Host, CompletionContextProvider, MCPServer, MarkdownData, NotebookIntelligenceExtension, TelemetryEvent, TelemetryListener, Tool, Toolset
from notebook_intelligence.base_chat_participant import BaseChatParticipant
from notebook_intelligence.config import NBIConfig
from notebook_intelligence.github_copilot_chat_participant import GithubCopilotChatParticipant
from notebook_intelligence.llm_providers.github_copilot_llm_provider import GitHubCopilotLLMProvider
from notebook_intelligence.llm_providers.litellm_compatible_llm_provider import LiteLLMCompatibleLLMProvider
from notebook_intelligence.llm_providers.ollama_llm_provider import OllamaLLMProvider
from notebook_intelligence.llm_providers.openai_compatible_llm_provider import OpenAICompatibleLLMProvider
from notebook_intelligence.mcp_manager import MCPManager

log = logging.getLogger(__name__)

DEFAULT_CHAT_PARTICIPANT_ID = 'default'
RESERVED_LLM_PROVIDER_IDS = set([
    'openai', 'anthropic', 'chat', 'copilot', 'jupyter', 'jupyterlab', 'jlab', 'notebook', 'intelligence', 'nb', 'nbi', 'ai', 'config', 'settings', 'ui', 'cell', 'code', 'file', 'data', 'new'
])
RESERVED_PARTICIPANT_IDS = set([
    'chat', 'copilot', 'jupyter', 'jupyterlab', 'jlab', 'notebook', 'intelligence', 'nb', 'nbi', 'terminal', 'vscode', 'workspace', 'help', 'ai', 'config', 'settings', 'ui', 'cell', 'code', 'file', 'data', 'new', 'run', 'search'
])

class AIServiceManager(Host):
    def __init__(self, options: dict = {}):
        self.llm_providers: Dict[str, LLMProvider] = {}
        self.chat_participants: Dict[str, ChatParticipant] = {}
        self.completion_context_providers: Dict[str, CompletionContextProvider] = {}
        self.telemetry_listeners: Dict[str, TelemetryListener] = {}
        self._extension_toolsets: Dict[str, list[Toolset]] = {}
        self._options = options.copy()
        self._nbi_config = NBIConfig({"server_root_dir": self._options.get('server_root_dir', '')})
        self._openai_compatible_llm_provider = OpenAICompatibleLLMProvider()
        self._litellm_compatible_llm_provider = LiteLLMCompatibleLLMProvider()
        self._ollama_llm_provider = OllamaLLMProvider()
        self._extensions = []
        self.initialize()

    @property
    def nbi_config(self) -> NBIConfig:
        return self._nbi_config
    
    @property
    def ollama_llm_provider(self) -> OllamaLLMProvider:
        return self._ollama_llm_provider

    def initialize(self):
        self.chat_participants = {}
        self.register_llm_provider(GitHubCopilotLLMProvider())
        self.register_llm_provider(self._openai_compatible_llm_provider)
        self.register_llm_provider(self._litellm_compatible_llm_provider)
        self.register_llm_provider(self._ollama_llm_provider)
        self._mcp_manager = MCPManager(self.nbi_config.mcp)
        for participant in self._mcp_manager.get_mcp_participants():
            self.register_chat_participant(participant)

        self.update_models_from_config()
        self.initialize_extensions()

    def update_models_from_config(self):
        using_github_copilot_service = self.nbi_config.using_github_copilot_service
        if using_github_copilot_service:
            github_copilot.login_with_existing_credentials(self._nbi_config.store_github_access_token)
        github_copilot.enable_github_login_status_change_updater(using_github_copilot_service)

        chat_model_cfg = self.nbi_config.chat_model
        chat_model_provider_id = chat_model_cfg.get('provider', 'none')
        chat_model_id = chat_model_cfg.get('model', 'none')
        chat_model_provider = self.get_llm_provider(chat_model_provider_id)
        self._chat_model = chat_model_provider.get_chat_model(chat_model_id) if chat_model_provider is not None else None

        inline_completion_model_cfg = self.nbi_config.inline_completion_model
        inline_completion_model_provider_id = inline_completion_model_cfg.get('provider', 'none')
        inline_completion_model_id = inline_completion_model_cfg.get('model', 'none')
        inline_completion_model_provider = self.get_llm_provider(inline_completion_model_provider_id)
        self._inline_completion_model = inline_completion_model_provider.get_inline_completion_model(inline_completion_model_id) if inline_completion_model_provider is not None else None
        self._embedding_model = None

        if self._chat_model is not None:
            properties = chat_model_cfg.get('properties', [])
            for property in properties:
                self._chat_model.set_property_value(property['id'], property['value'])

        if self._inline_completion_model is not None:
            properties = inline_completion_model_cfg.get('properties', [])
            for property in properties:
                self._inline_completion_model.set_property_value(property['id'], property['value'])

        is_github_copilot_chat_model = isinstance(chat_model_provider, GitHubCopilotLLMProvider)
        default_chat_participant = GithubCopilotChatParticipant() if is_github_copilot_chat_model else BaseChatParticipant()
        self._default_chat_participant = default_chat_participant

        self.chat_participants[DEFAULT_CHAT_PARTICIPANT_ID] = self._default_chat_participant

    def update_mcp_servers(self):
        self._mcp_manager.update_mcp_servers(self.nbi_config.mcp)

    def initialize_extensions(self):
        extensions_dir = path.join(sys.prefix, "share", "jupyter", "nbi_extensions")
        if not path.exists(extensions_dir):
            return
        subfolders = [f.path for f in os.scandir(extensions_dir) if f.is_dir()]
        for extension_dir in list(subfolders):
            try:
                log.info(f"Loading NBI extension from '{extension_dir}'...")
                metadata_path = path.join(extension_dir, "extension.json")
                if path.exists(metadata_path) and path.isfile(metadata_path):
                    with open(metadata_path, 'r') as file:
                        data = json.load(file)
                        class_name = data['class']
                        extension = self.load_extension(class_name)
                        if extension:
                            extension.activate(self)
                            log.info(f"Activated NBI extension '{class_name}'.")
                            self._extensions.append(extension)
            except Exception as e:
                log.error(f"Failed to load NBI extension from '{extension_dir}'!\n{e}")
    
    def load_extension(self, extension_class: str) -> NotebookIntelligenceExtension:
        import importlib
        try:
            parts = extension_class.split(".")
            module_name = ".".join(parts[0:-1])
            class_name = parts[-1]
            ExtensionClass = getattr(importlib.import_module(module_name), class_name)
            if ExtensionClass is not None and issubclass(ExtensionClass, NotebookIntelligenceExtension):
                instance = ExtensionClass()
                return instance
        except Exception as e:
            log.error(f"Failed to load NBI extension: '{extension_class}'!\n{e}")

        return None

    def register_chat_participant(self, participant: ChatParticipant):
        if participant.id in RESERVED_PARTICIPANT_IDS:
            log.error(f"Participant ID '{participant.id}' is reserved!")
            return
        if participant.id in self.chat_participants:
            log.error(f"Participant ID '{participant.id}' is already in use!")
            return
        self.chat_participants[participant.id] = participant

    def register_llm_provider(self, provider: LLMProvider) -> None:
        if provider.id in RESERVED_LLM_PROVIDER_IDS:
            log.error(f"LLM Provider ID '{provider.id}' is reserved!")
            return
        if provider.id in self.chat_participants:
            log.error(f"LLM Provider ID '{provider.id}' is already in use!")
            return
        self.llm_providers[provider.id] = provider

    def register_completion_context_provider(self, provider: CompletionContextProvider) -> None:
        if provider.id in self.completion_context_providers:
            log.error(f"Completion Context Provider ID '{provider.id}' is already in use!")
            return
        self.completion_context_providers[provider.id] = provider

    def register_telemetry_listener(self, listener: TelemetryListener) -> None:
        if listener.name in self.telemetry_listeners:
            log.error(f"Notebook Intelligence telemetry listener '{listener.name}' already exists!")
            return
        log.warning(f"Notebook Intelligence telemetry listener '{listener.name}' registered. Make sure it is from a trusted source.")
        self.telemetry_listeners[listener.name] = listener

    def register_toolset(self, toolset: Toolset) -> None:
        if toolset.provider is None:
            log.error(f"Toolset '{toolset.id}' has no provider! It cannot be registered.")
            return
        provider_id = toolset.provider.id
        if provider_id not in self._extension_toolsets:
            self._extension_toolsets[provider_id] = []
        self._extension_toolsets[provider_id].append(toolset)
        log.debug(f"Registered toolset '{toolset.id}' from provider '{provider_id}'.")

    @property
    def default_chat_participant(self) -> ChatParticipant:
        return self._default_chat_participant

    @property
    def chat_model(self) -> ChatModel:
        return self._chat_model
    
    @property
    def inline_completion_model(self) -> InlineCompletionModel:
        return self._inline_completion_model
    
    @property
    def embedding_model(self) -> EmbeddingModel:
        return self._embedding_model

    @staticmethod
    def parse_prompt(prompt: str) -> tuple[str, str, str]:
        participant = DEFAULT_CHAT_PARTICIPANT_ID
        command = ''
        input = ''

        prompt = prompt.lstrip()
        parts = prompt.split(' ')
        parts = [part for part in parts if part.strip() != '']

        if len(parts) > 0:
            if parts[0].startswith('@'):
                participant = parts[0][1:]
                parts = parts[1:]

        if len(parts) > 0:
            if parts[0].startswith('/'):
                command = parts[0][1:]
                parts = parts[1:]

        if len(parts) > 0:
            input = " ".join(parts)

        return [participant, command, input]
    
    def get_llm_provider(self, provider_id: str) -> LLMProvider:
        return self.llm_providers.get(provider_id)
    
    def get_llm_provider_for_model_ref(self, model_ref: str) -> LLMProvider:
        parts = model_ref.split('::')
        if len(parts) < 2:
            return None

        provider_id = parts[0]

        return self.get_llm_provider(provider_id)

    def get_chat_model(self, model_ref: str) -> ChatModel:
        return self._get_provider_model(model_ref, 'chat')
    
    def get_inline_completion_model(self, model_ref: str) -> ChatModel:
        return self._get_provider_model(model_ref, 'inline-completion')
    
    def get_embedding_model(self, model_ref: str) -> ChatModel:
        return self._get_provider_model(model_ref, 'embedding')
    
    def _get_provider_model(self, model_ref: str, model_type: str) -> ChatModel:
        parts = model_ref.split('::')
        if len(parts) < 2:
            return None

        provider_id = parts[0]
        model_id = parts[1]
        llm_provider = self.get_llm_provider(provider_id)

        if llm_provider is None:
            return None

        model_list = llm_provider.chat_models if model_type == 'chat' else llm_provider.inline_completion_models if model_type == 'inline-completion' else llm_provider.embedding_models

        for model in model_list:
            if model.id == model_id:
                return model

        return None
    
    @property
    def chat_model_ids(self) -> list[ChatModel]:
        model_ids = []
        for provider in self.llm_providers.values():
            model_ids += [{"provider": provider.id, "id": model.id, "name": model.name, "context_window": model.context_window, "properties": [property.to_dict() for property in model.properties]} for model in provider.chat_models]
        return model_ids

    @property
    def inline_completion_model_ids(self) -> list[InlineCompletionModel]:
        model_ids = []
        for provider in self.llm_providers.values():
            model_ids += [{"provider": provider.id, "id": model.id, "name": model.name, "context_window": model.context_window, "properties": [property.to_dict() for property in model.properties]} for model in provider.inline_completion_models]
        return model_ids
    
    @property
    def embedding_model_ids(self) -> list[EmbeddingModel]:
        model_ids = []
        for provider in self.llm_providers.values():
            model_ids += [{"id": f"{provider.id}::{model.id}", "name": f"{provider.name} / {model.name}", "context_window": model.context_window} for model in provider.embedding_models]
        return model_ids

    def get_chat_participant(self, prompt: str) -> ChatParticipant:
        (participant_id, command, input) = AIServiceManager.parse_prompt(prompt)
        return self.chat_participants.get(participant_id, DEFAULT_CHAT_PARTICIPANT_ID)

    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        if self.chat_model is None:
            response.stream(MarkdownData("Chat model is not set!"))
            response.stream(ButtonData("Configure", "notebook-intelligence:open-configuration-dialog"))
            response.finish()
            return
        request.host = self
        (participant_id, command, prompt) = AIServiceManager.parse_prompt(request.prompt)
        participant = self.chat_participants.get(participant_id, DEFAULT_CHAT_PARTICIPANT_ID)
        request.command = command
        request.prompt = prompt
        response.participant_id  = participant_id
        return await participant.handle_chat_request(request, response, options)

    async def get_completion_context(self, request: ContextRequest) -> CompletionContext:
        cancel_token = request.cancel_token
        context = CompletionContext([])

        allowed_context_providers = request.participant.allowed_context_providers

        if cancel_token.is_cancel_requested:
            return context

        for provider in self.completion_context_providers:
            if cancel_token.is_cancel_requested:
                return context
            provider = self.completion_context_providers.get(provider)
            if provider.id not in allowed_context_providers and '*' not in allowed_context_providers:
                continue
            try:
                provider_context = provider.handle_completion_context_request(request)
                if provider_context.items:
                    context.items += provider_context.items
            except Exception as e:
                log.error(f"Error while getting completion context from provider '{provider.id}'!\n{e}")

        return context
    
    async def emit_telemetry_event(self, event: TelemetryEvent):
        for listener in self.telemetry_listeners.values():
            listener.on_telemetry_event(event)

    def get_mcp_servers(self):
        return self._mcp_manager.get_mcp_servers()
    
    def get_mcp_server(self, server_name: str) -> MCPServer:
        return self._mcp_manager.get_mcp_server(server_name)
    
    def get_mcp_server_tool(self, server_name: str, tool_name: str) -> Tool:
        mcp_server = self._mcp_manager.get_mcp_server(server_name)
        if mcp_server is not None:
            return mcp_server.get_tool(tool_name)

        return None

    def get_extension_toolsets(self) -> Dict[str, list[Toolset]]:
        return self._extension_toolsets
    
    def get_extension_toolset(self, extension_id: str, toolset_id: str) -> Toolset:
        if extension_id not in self._extension_toolsets:
            return None

        extension_toolsets = self._extension_toolsets[extension_id]
        for toolset in extension_toolsets:
            if toolset_id == toolset.id:
                return toolset
        
        return None

    def get_extension_tool(self, extension_id: str, toolset_id: str, tool_name: str) -> Tool:
        if extension_id not in self._extension_toolsets:
            return None
        extension_toolsets = self._extension_toolsets[extension_id]
        for toolset in extension_toolsets:
            if toolset_id == toolset.id:
                for tool in toolset.tools:
                    if tool.name == tool_name:
                        return tool
        return None
    
    def get_extension(self, extension_id: str) -> NotebookIntelligenceExtension:
        for extension in self._extensions:
            if extension.id == extension_id:
                return extension
        return None

    def handle_stop_request(self):
        self._mcp_manager.handle_stop_request()
