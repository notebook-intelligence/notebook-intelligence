# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import asyncio
from typing import Any, Callable, Dict, Union
from dataclasses import asdict, dataclass
from enum import Enum
import uuid
from fuzzy_json import loads as fuzzy_json_loads
import logging
from mcp.server.fastmcp.tools import Tool as MCPToolClass

from notebook_intelligence.config import NBIConfig

log = logging.getLogger(__name__)

class RequestDataType(str, Enum):
    ChatRequest = 'chat-request'
    ChatUserInput = 'chat-user-input'
    ClearChatHistory = 'clear-chat-history'
    RunUICommandResponse = 'run-ui-command-response'
    GenerateCode = 'generate-code'
    CancelChatRequest = 'cancel-chat-request'
    InlineCompletionRequest = 'inline-completion-request'
    CancelInlineCompletionRequest = 'cancel-inline-completion-request'

class BackendMessageType(str, Enum):
    StreamMessage = 'stream-message'
    StreamEnd = 'stream-end'
    RunUICommand = 'run-ui-command'
    GitHubCopilotLoginStatusChange = 'github-copilot-login-status-change'
    MCPServerStatusChange = 'mcp-server-status-change'

class ResponseStreamDataType(str, Enum):
    LLMRaw = 'llm-raw'
    Markdown = 'markdown'
    MarkdownPart = 'markdown-part'
    Image = 'image'
    HTMLFrame = 'html-frame'
    Button = 'button'
    Anchor = 'anchor'
    Progress = 'progress'
    Confirmation = 'confirmation'

    def __str__(self) -> str:
        return self.value

class BuiltinToolset(str, Enum):
    NotebookEdit = 'nbi-notebook-edit'
    NotebookExecute = 'nbi-notebook-execute'
    PythonFileEdit = 'nbi-python-file-edit'

class MCPServerStatus(str, Enum):
    NotConnected = 'not-connected'
    Connecting = 'connecting'
    Disconnecting = 'disconnecting'
    FailedToConnect = 'failed-to-connect'
    Connected = 'connected'
    UpdatingToolList = 'updating-tool-list'
    UpdatedToolList = 'updated-tool-list'

class Signal:
    def __init__(self):
        self._listeners = []

    def connect(self, listener: Callable) -> None:
        self._listeners.append(listener)

    def disconnect(self, listener: Callable) -> None:
        self._listeners.remove(listener)

class SignalImpl(Signal):
    def __init__(self):
        super().__init__()

    def emit(self, *args, **kwargs) -> None:
        for listener in self._listeners:
            listener(*args, **kwargs)

class CancelToken:
    def __init__(self):
        self._cancellation_signal = Signal()
        self._cancellation_requested = False

    @property
    def is_cancel_requested(self) -> bool:
        return self._cancellation_requested

    @property
    def cancellation_signal(self) -> Signal:
        return self._cancellation_signal

@dataclass
class RequestToolSelection:
    built_in_toolsets: list[str] = None
    mcp_server_tools: dict[str, list[str]] = None
    extension_tools: dict[str, dict[str, list[str]]] = None

@dataclass
class ChatRequest:
    host: 'Host' = None
    chat_mode: 'ChatMode' = None
    tool_selection: RequestToolSelection = None
    command: str = ''
    prompt: str = ''
    chat_history: list[dict] = None
    cancel_token: CancelToken = None

@dataclass
class ResponseStreamData:
    @property
    def data_type(self) -> ResponseStreamDataType:
        raise NotImplemented

@dataclass
class MarkdownData(ResponseStreamData):
    content: str = ''
    detail: dict = None

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Markdown

@dataclass
class MarkdownPartData(ResponseStreamData):
    content: str = ''

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.MarkdownPart

@dataclass
class ImageData(ResponseStreamData):
    content: str = ''

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Image

@dataclass
class HTMLFrameData(ResponseStreamData):
    source: str = ''
    height: int = 30

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.HTMLFrame

@dataclass
class AnchorData(ResponseStreamData):
    uri: str = ''
    title: str = ''

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Anchor

@dataclass
class ButtonData(ResponseStreamData):
    title: str = ''
    commandId: str = ''
    args: Dict[str, str] = None

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Button

@dataclass
class ProgressData(ResponseStreamData):
    title: str = ''

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Progress

@dataclass
class ConfirmationData(ResponseStreamData):
    title: str = ''
    message: str = ''
    confirmArgs: dict = None
    cancelArgs: dict = None
    confirmLabel: str = None
    cancelLabel: str = None

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Confirmation

class ContextRequestType(Enum):
    InlineCompletion = 'inline-completion'
    NewPythonFile = 'new-python-file'
    NewNotebook = 'new-notebook'

class ContextType(Enum):
    Custom = 'custom'
    Provider = 'provider'
    CurrentFile = 'current-file'

@dataclass
class ContextRequest:
    type: ContextRequestType
    prefix: str = ''
    suffix: str = ''
    language: str = ''
    filename: str = ''
    participant: 'ChatParticipant' = None
    cancel_token: CancelToken = None

@dataclass
class ContextItem:
    type: ContextType
    content: str
    currentCellContents: dict = None
    filePath: str = None
    cellIndex: int = None
    startLine: int = None
    endLine: int = None

@dataclass
class CompletionContext:
    items: list[ContextItem]

class ChatResponse:
    def __init__(self):
        self._user_input_signal: SignalImpl = SignalImpl()
        self._run_ui_command_response_signal: SignalImpl = SignalImpl()
        self.participant_id = ''

    @property
    def message_id(self) -> str:
        raise NotImplemented

    def stream(self, data: ResponseStreamData, finish: bool = False) -> None:
        raise NotImplemented
    
    def finish(self) -> None:
        raise NotImplemented
    
    @property
    def user_input_signal(self) -> Signal:
        return self._user_input_signal

    def on_user_input(self, data: dict) -> None:
        self._user_input_signal.emit(data)

    @staticmethod
    async def wait_for_chat_user_input(response: 'ChatResponse', callback_id: str):
        resp = {"data": None}
        def _on_user_input(data: dict):
            if data['callback_id'] == callback_id:
                resp["data"] = data['data']

        response.user_input_signal.connect(_on_user_input)

        while True:
            if resp["data"] is not None:
                response.user_input_signal.disconnect(_on_user_input)
                return resp["data"]
            await asyncio.sleep(0.1)

    async def run_ui_command(self, command: str, args: dict = {}) -> None:
        raise NotImplemented
    
    @property
    def run_ui_command_response_signal(self) -> Signal:
        return self._run_ui_command_response_signal
    
    def on_run_ui_command_response(self, data: dict) -> None:
        self._run_ui_command_response_signal.emit(data)

    @staticmethod
    async def wait_for_run_ui_command_response(response: 'ChatResponse', callback_id: str):
        resp = {"result": None}
        def _on_ui_command_response(data: dict):
            if data['callback_id'] == callback_id:
                resp["result"] = data['result']

        response.run_ui_command_response_signal.connect(_on_ui_command_response)

        while True:
            if resp["result"] is not None:
                response.run_ui_command_response_signal.disconnect(_on_ui_command_response)
                return resp["result"]
            await asyncio.sleep(0.1)

@dataclass
class ToolPreInvokeResponse:
    message: str = None
    detail: dict = None
    confirmationTitle: str = None
    confirmationMessage: str = None

@dataclass
class ChatCommand:
    name: str = ''
    description: str = ''

class Tool:
    @property
    def name(self) -> str:
        raise NotImplemented

    @property
    def title(self) -> str:
        raise NotImplemented
    
    @property
    def tags(self) -> list[str]:
        raise NotImplemented
    
    @property
    def description(self) -> str:
        raise NotImplemented
    
    @property
    def schema(self) -> dict:
        raise NotImplemented

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        return None

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        raise NotImplemented

class Toolset:
    def __init__(self, id: str, name: str, description: str, provider: Union['NotebookIntelligenceExtension', None], tools: list[Tool] = [], instructions: str = None):
        self.id = id
        self.name = name
        self.description = description
        self.provider = provider
        self.tools: list[Tool] = tools
        self.instructions: Union[str, None] = instructions

    def add_tool(self, tool: Tool) -> None:
        self.tools.append(tool)

    def remove_tool(self, tool: Tool) -> None:
        self.tools.remove(tool)

class SimpleTool(Tool):
    def __init__(self, tool_function: Callable, name: str, description: str, schema: dict, title: str = None, auto_approve: bool = False, has_var_args: bool = False):
        super().__init__()
        self._tool_function = tool_function
        self._name = name
        self._description = description
        self._schema = schema
        self._title = title
        self._auto_approve = auto_approve
        self._has_var_args = has_var_args

    @property
    def name(self) -> str:
        return self._name

    @property
    def title(self) -> str:
        return self._title if self._title is not None else self._name
    
    @property
    def tags(self) -> list[str]:
        return []
    
    @property
    def description(self) -> str:
        return self._description
    
    @property
    def schema(self) -> dict:
        return self._schema

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        confirmationTitle = None
        confirmationMessage = None
        if not self._auto_approve:
            confirmationTitle = "Approve"
            confirmationMessage = "Are you sure you want to call this tool?"
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", confirmationTitle, confirmationMessage)

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        fn_args = tool_args.copy()
        if self._has_var_args:
            fn_args.update({"request": request, "response": response})
        return await self._tool_function(**fn_args)

class MCPServer:
    @property
    def name(self) -> str:
        return NotImplemented

    @property
    def status(self) -> MCPServerStatus:
        return NotImplemented
    
    def connect(self):
        return NotImplemented

    def disconnect(self):
        return NotImplemented

    def update_tool_list(self):
        return NotImplemented
    
    def get_tools(self) -> list[Tool]:
        return NotImplemented

    def get_tool(self, tool_name: str) -> Tool:
        return NotImplemented

    def call_tool(self, tool_name: str, tool_args: dict):
        return NotImplemented

def auto_approve(tool: SimpleTool):
    """
    Decorator to set auto_approve to True for a tool.
    """
    tool._auto_approve = True
    return tool

def tool(tool_function: Callable) -> SimpleTool:
    mcp_tool = MCPToolClass.from_function(tool_function)
    has_var_args = False
    if "args" in mcp_tool.parameters["properties"]:
        has_var_args = True
        del mcp_tool.parameters["properties"]["args"]
    if "args" in mcp_tool.parameters["required"]:
        mcp_tool.parameters["required"].remove("args")

    schema = {
        "type": "function",
        "function": {
            "name": mcp_tool.name,
            "description": mcp_tool.description,
            "strict": False,
            "parameters": mcp_tool.parameters
        },
    }

    return SimpleTool(tool_function, mcp_tool.name, mcp_tool.description, schema, mcp_tool.name, auto_approve, has_var_args)

class ChatMode:
    def __init__(self, id: str, name: str, instructions: str = None):
        self.id = id
        self.name = name
        self.instructions = instructions

class ChatParticipant:
    @property
    def id(self) -> str:
        raise NotImplemented

    @property
    def name(self) -> str:
        raise NotImplemented

    @property
    def description(self) -> str:
        raise NotImplemented
    
    @property
    def icon_path(self) -> str:
        return None
    
    @property
    def commands(self) -> list[ChatCommand]:
        return []
    
    @property
    def tools(self) -> list[Tool]:
        return []

    @property
    def allowed_context_providers(self) -> set[str]:
        # any context provider can be used
        return set(["*"])

    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        raise NotImplemented
    
    async def handle_chat_request_with_tools(self, request: ChatRequest, response: ChatResponse, options: dict = {}, tool_context: dict = {}, tool_choice = 'auto') -> None:
        tools = self.tools

        messages = request.chat_history.copy()

        system_prompt = options.get("system_prompt")
        if system_prompt is not None:
            messages = [
                {"role": "system", "content": system_prompt}
            ] + messages

        if len(tools) == 0:
            request.host.chat_model.completions(messages, tools=None, cancel_token=request.cancel_token, response=response)
            return

        openai_tools = [tool.schema for tool in tools]


        tool_call_rounds = []
        # TODO overrides options arg
        options = {'tool_choice': tool_choice}

        async def _tool_call_loop(tool_call_rounds: list):
            try:
                if request.cancel_token.is_cancel_requested:
                    return

                tool_response = request.host.chat_model.completions(messages, openai_tools, cancel_token=request.cancel_token, options=options)
                # after first call, set tool_choice to auto
                options['tool_choice'] = 'auto'

                for choice in tool_response['choices']:
                    if choice['message'].get('tool_calls', None) is not None:
                        for tool_call in choice['message']['tool_calls']:
                            tool_call_rounds.append(tool_call)
                    elif choice['message'].get('content', None) is not None:
                        response.stream(MarkdownData(tool_response['choices'][0]['message']['content']))

                    messages.append(choice['message'])

                had_tool_call = len(tool_call_rounds) > 0

                # handle first tool calls
                while len(tool_call_rounds) > 0:
                    if request.cancel_token.is_cancel_requested:
                        return

                    tool_call = tool_call_rounds[0]
                    if "id" not in tool_call:
                        tool_call['id'] = uuid.uuid4().hex
                    tool_call_rounds = tool_call_rounds[1:]

                    tool_name = tool_call['function']['name']
                    tool_to_call = self._get_tool_by_name(tool_name)
                    if tool_to_call is None:
                        log.error(f"Tool not found: {tool_name}, args: {tool_call['function']['arguments']}")
                        response.stream(MarkdownData("Oops! Failed to find requested tool. Please try again with a different prompt."))
                        response.finish()
                        return

                    if type(tool_call['function']['arguments']) is dict:
                        args = tool_call['function']['arguments']
                    elif not tool_call['function']['arguments'].startswith('{'):
                        args = tool_call['function']['arguments']
                    else:
                        args = fuzzy_json_loads(tool_call['function']['arguments'])

                    tool_properties = tool_to_call.schema["function"]["parameters"]["properties"]
                    if type(args) is str:
                        if len(tool_properties) == 1 and tool_call['function']['arguments'] is not None:
                            tool_property = list(tool_properties.keys())[0]
                            args = {tool_property: args}
                        else:
                            args = {}

                    tool_pre_invoke_response = tool_to_call.pre_invoke(request, args)
                    if tool_pre_invoke_response is not None:
                        if tool_pre_invoke_response.message is not None:
                            response.stream(MarkdownData(f"&#x2713; {tool_pre_invoke_response.message}...", tool_pre_invoke_response.detail))
                        if tool_pre_invoke_response.confirmationMessage is not None:
                            response.stream(ConfirmationData(
                                title=tool_pre_invoke_response.confirmationTitle,
                                message=tool_pre_invoke_response.confirmationMessage,
                                confirmArgs={"id": response.message_id, "data": { "callback_id": tool_call['id'], "data": {"confirmed": True}}},
                                cancelArgs={"id": response.message_id, "data": { "callback_id": tool_call['id'], "data": {"confirmed": False}}},
                            ))
                            user_input = await ChatResponse.wait_for_chat_user_input(response, tool_call['id'])
                            if user_input['confirmed'] == False:
                                response.finish()
                                return

                    tool_call_response = await tool_to_call.handle_tool_call(request, response, tool_context, args)

                    function_call_result_message = {
                        "role": "tool",
                        "content": str(tool_call_response),
                        "tool_call_id": tool_call['id']
                    }

                    messages.append(function_call_result_message)

                if had_tool_call:
                    await _tool_call_loop(tool_call_rounds)
                    return

                if len(tool_call_rounds) > 0:
                    await _tool_call_loop(tool_call_rounds)
                    return
                else:
                    response.finish()
                    return
            except Exception as e:
                log.error(f"Error in tool call loop: {str(e)}")
                response.stream(MarkdownData(f"Oops! I am sorry, there was a problem generating response with tools. Please try again. You can check server logs for more details."))
                response.finish()
                return

        await _tool_call_loop(tool_call_rounds)
    
    def _get_tool_by_name(self, name: str) -> Tool:
        for tool in self.tools:
            if tool.name == name:
                return tool
        return None

class CompletionContextProvider:
    @property
    def id(self) -> str:
        raise NotImplemented

    def handle_completion_context_request(self, request: ContextRequest) -> CompletionContext:
        raise NotImplemented

@dataclass
class LLMProviderProperty:
    id: str
    name: str
    description: str
    value: str
    optional: bool = False

    def to_dict(self):
        return asdict(self)

class LLMPropertyProvider:
    def __init__(self):
        self._properties = []

    @property
    def properties(self) -> list[LLMProviderProperty]:
        return self._properties

    def get_property(self, property_id: str) -> LLMProviderProperty:
        for prop in self.properties:
            if prop.id == property_id:
                return prop
        return None

    def set_property_value(self, property_id: str, value: str):
        for prop in self.properties:
            if prop.id == property_id:
                prop.value = value

class AIModel(LLMPropertyProvider):
    def __init__(self, provider: 'LLMProvider'):
        super().__init__()
        self._provider = provider

    @property
    def id(self) -> str:
        raise NotImplemented
    
    @property
    def name(self) -> str:
        raise NotImplemented

    @property
    def provider(self) -> 'LLMProvider':
        return self._provider
    
    @property
    def context_window(self) -> int:
        raise NotImplemented

    @property
    def supports_tools(self) -> bool:
        return False

class ChatModel(AIModel):
    def completions(self, messages: list[dict], tools: list[dict] = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
        raise NotImplemented

class InlineCompletionModel(AIModel):
    def inline_completions(prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        raise NotImplemented

class EmbeddingModel(AIModel):
    def embeddings(self, inputs: list[str]) -> Any:
        raise NotImplemented

class LLMProvider(LLMPropertyProvider):
    def __init__(self):
        super().__init__()

    @property
    def id(self) -> str:
        raise NotImplemented
    
    @property
    def name(self) -> str:
        raise NotImplemented

    @property
    def chat_models(self) -> list[ChatModel]:
        raise NotImplemented
    
    @property
    def inline_completion_models(self) -> list[InlineCompletionModel]:
        raise NotImplemented
    
    @property
    def embedding_models(self) -> list[EmbeddingModel]:
        raise NotImplemented

    def get_chat_model(self, model_id: str) -> ChatModel:
        for model in self.chat_models:
            if model.id == model_id:
                return model
        return None
    
    def get_inline_completion_model(self, model_id: str) -> InlineCompletionModel:
        for model in self.inline_completion_models:
            if model.id == model_id:
                return model
        return None
    
    def get_embedding_model(self, model_id: str) -> EmbeddingModel:
        for model in self.embedding_models:
            if model.id == model_id:
                return model
        return None

class TelemetryEventType(str, Enum):
    InlineCompletionRequest = 'inline-completion-request'
    ExplainThisRequest = 'explain-this-request'
    FixThisCodeRequest = 'fix-this-code-request'
    ExplainThisOutputRequest = 'explain-this-output-request'
    TroubleshootThisOutputRequest = 'troubleshoot-this-output-request'
    GenerateCodeRequest = 'generate-code-request'
    ChatRequest = 'chat-request'
    InlineChatRequest = 'inline-chat-request'
    ChatResponse = 'chat-response'
    InlineChatResponse = 'inline-chat-response'
    InlineCompletionResponse = 'inline-completion-response'

class TelemetryEvent:
    @property
    def type(self) -> TelemetryEventType:
        raise NotImplemented
    
    @property
    def data(self) -> dict:
        return None

class TelemetryListener:
    @property
    def name(self) -> str:
        raise NotImplemented

    def on_telemetry_event(self, event: TelemetryEvent):
        raise NotImplemented

class Host:
    def register_llm_provider(self, provider: LLMProvider) -> None:
        raise NotImplemented

    def register_chat_participant(self, participant: ChatParticipant) -> None:
        raise NotImplemented

    def register_completion_context_provider(self, provider: CompletionContextProvider) -> None:
        raise NotImplemented
    
    def register_telemetry_listener(self, listener: TelemetryListener) -> None:
        raise NotImplemented

    def register_toolset(self, toolset: Toolset) -> None:
        raise NotImplemented

    @property
    def nbi_config(self) -> NBIConfig:
        raise NotImplemented

    @property
    def default_chat_participant(self) -> ChatParticipant:
        raise NotImplemented
    
    @property
    def chat_model(self) -> ChatModel:
        raise NotImplemented
    
    @property
    def inline_completion_model(self) -> InlineCompletionModel:
        raise NotImplemented
    
    @property
    def embedding_model(self) -> EmbeddingModel:
        raise NotImplemented

    def get_mcp_server(self, server_name: str) -> MCPServer:
        return NotImplemented

    def get_mcp_server_tool(self, server_name: str, tool_name: str) -> Tool:
        return NotImplemented

    def get_extension_toolset(self, extension_id: str, toolset_id: str) -> Toolset:
        return NotImplemented

    def get_extension_tool(self, extension_id: str, toolset_id: str, tool_name: str) -> Tool:
        return NotImplemented

class NotebookIntelligenceExtension:
    @property
    def id(self) -> str:
        raise NotImplemented

    @property
    def name(self) -> str:
        raise NotImplemented
    
    @property
    def provider(self) -> str:
        raise NotImplemented

    @property
    def url(self) -> str:
        raise NotImplemented

    def activate(self, host: Host) -> None:
        raise NotImplemented
