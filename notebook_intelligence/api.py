# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import asyncio
import contextlib
import json
import time
from typing import Any, Callable, Dict, Union, Optional
from dataclasses import asdict, dataclass
from enum import Enum
import uuid
from fuzzy_json import loads as fuzzy_json_loads
import logging
import threading
from mcp.server.fastmcp.tools import Tool as MCPToolClass

from notebook_intelligence import perf
from notebook_intelligence.config import NBIConfig
from notebook_intelligence.ruleset import RuleContext
from notebook_intelligence.util import ThreadSafeWebSocketConnector

log = logging.getLogger(__name__)


class RegistrationError(Exception):
    """Raised when an extension tries to register a participant, provider, or
    other named resource whose id is reserved or already in use.

    Previously the registrar logged the collision via ``log.error`` and
    silently dropped the registration; that surface failed only in
    production logs that authors rarely read. Raising lets the loader
    surface the failure at install/import time instead.
    """


class UICommandCancelledError(RuntimeError):
    """Raised when a pending frontend command cannot produce a result."""


class UserInputCancelledError(RuntimeError):
    """Raised when pending interactive input cannot produce a result."""


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
    ClaudeCodeStatusChange = 'claude-code-status-change'
    ClaudeCodeHeartbeat = 'claude-code-heartbeat'
    ClaudePermissionModeChange = 'claude-permission-mode-change'
    SkillsReloaded = 'skills-reloaded'

class ResponseStreamDataType(str, Enum):
    LLMRaw = 'llm-raw'
    Markdown = 'markdown'
    MarkdownPart = 'markdown-part'
    Image = 'image'
    HTMLFrame = 'html-frame'
    Button = 'button'
    Anchor = 'anchor'
    Progress = 'progress'
    ToolCall = 'tool-call'
    Confirmation = 'confirmation'
    AskUserQuestion = 'ask-user-question'

    def __str__(self) -> str:
        return self.value

class BuiltinToolset(str, Enum):
    NotebookEdit = 'nbi-notebook-edit'
    NotebookExecute = 'nbi-notebook-execute'
    PythonFileEdit = 'nbi-python-file-edit'
    FileEdit = 'nbi-file-edit'
    FileRead = 'nbi-file-read'
    CommandExecute = 'nbi-command-execute'

class MCPServerStatus(str, Enum):
    NotConnected = 'not-connected'
    Connecting = 'connecting'
    Disconnecting = 'disconnecting'
    FailedToConnect = 'failed-to-connect'
    Connected = 'connected'
    UpdatingToolList = 'updating-tool-list'
    UpdatedToolList = 'updated-tool-list'
    FailedToUpdateToolList = 'failed-to-update-tool-list'
    UpdatingPromptList = 'updating-prompt-list'
    UpdatedPromptList = 'updated-prompt-list'
    FailedToUpdatePromptList = 'failed-to-update-prompt-list'

class ClaudeToolType(str, Enum):
  ClaudeCodeTools = 'claude-code:built-in-tools'
  JupyterUITools = 'nbi:built-in-jupyter-ui-tools'

class Signal:
    def __init__(self):
        self._listeners = []
        self._listeners_lock = threading.Lock()

    def connect(self, listener: Callable) -> None:
        with self._listeners_lock:
            self._listeners.append(listener)

    def disconnect(self, listener: Callable) -> None:
        # `list.remove` raises ``ValueError`` on a missing entry; tolerating
        # double-disconnect avoids crashing the chat loop when a misbehaving
        # plugin disconnects twice or after a crash recovery. Surfaced at
        # DEBUG so a real plugin bug can still be diagnosed if the noise
        # ever matters.
        with self._listeners_lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                log.debug("Signal.disconnect: listener %r was not connected", listener)

    def __deepcopy__(self, memo):
        """Copy the listener registry without copying its thread lock.

        Subscribers are intentionally not carried into an independent copy;
        retaining bound listeners would couple cancellation across requests.
        """
        copied = object.__new__(type(self))
        memo[id(self)] = copied
        copied.__setstate__({"_listeners": []})
        return copied

    def __getstate__(self):
        """Serialize listeners while excluding the non-picklable lock."""
        with self._listeners_lock:
            return {"_listeners": list(self._listeners)}

    def __setstate__(self, state):
        self._listeners = state.get("_listeners", [])
        self._listeners_lock = threading.Lock()

class SignalImpl(Signal):
    def __init__(self):
        super().__init__()

    def emit(self, *args, **kwargs) -> None:
        # Listener callbacks may disconnect themselves from another thread
        # while an event is being delivered. Iterate a synchronized snapshot
        # so list compaction cannot skip a pending request's callback.
        with self._listeners_lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
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
    language: str = ''
    kernel_name: str = ''
    chat_history: list[dict] = None
    cancel_token: CancelToken = None
    # NEW: Add context for rule evaluation
    rule_context: Optional[RuleContext] = None
    # Claude-mode permission mode, already clamped server-side against the
    # bypass policy and managed settings at the websocket boundary.
    permission_mode: str = 'default'
    # These budgeting hints are appended to preserve the positional indexes
    # of the existing public ChatRequest fields.
    mcp_prompt_message_count: int = 0
    required_context_message_count: int = 0

@dataclass
class ResponseStreamData:
    @property
    def data_type(self) -> ResponseStreamDataType:
        raise NotImplementedError

@dataclass
class MarkdownData(ResponseStreamData):
    content: str = ''
    detail: dict = None
    reasoning_content: str = ''

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Markdown

@dataclass
class MarkdownPartData(ResponseStreamData):
    content: str = ''
    reasoning_content: str = ''

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
class ToolCallData(ResponseStreamData):
    """A single agent tool call, surfaced as a persistent chat card.

    Unlike ``ProgressData`` (a transient single line that vanishes when the
    turn ends), a tool call is emitted twice under the same ``id`` -- once
    when it starts and once when it finishes -- and the frontend merges the
    two by ``id`` into one card that carries its final ``status``.

    ``kind`` is a coarse category (``read`` / ``edit`` / ``execute`` /
    ``other``) used only to pick an icon. ``status`` is one of
    ``in_progress`` / ``completed`` / ``failed``.
    """
    id: str = ''
    title: str = ''
    kind: str = 'other'
    status: str = 'in_progress'
    # Inline before/after edits for file-edit tools; empty/None otherwise.
    diffs: list = None

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.ToolCall

@dataclass
class ConfirmationData(ResponseStreamData):
    title: str = ''
    message: str = ''
    confirmArgs: dict = None
    confirmSessionArgs: dict = None
    cancelArgs: dict = None
    confirmLabel: str = None
    confirmSessionLabel: str = None
    cancelLabel: str = None

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.Confirmation

@dataclass
class AskUserQuestionData(ResponseStreamData):
    identifier: dict = None
    title: str = ''
    message: str = ''
    questions: list[dict]= None
    submitLabel: str = 'Submit'
    cancelLabel: str = 'Cancel'

    @property
    def data_type(self) -> ResponseStreamDataType:
        return ResponseStreamDataType.AskUserQuestion


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
        self._user_input_state_lock = threading.RLock()
        self._pending_user_inputs: dict[str, asyncio.Future] = {}
        self._user_input_cancel_reason: Optional[str] = None
        self._run_ui_command_response_signal: SignalImpl = SignalImpl()
        self._run_ui_command_state_lock = threading.RLock()
        self._pending_ui_commands: dict[str, asyncio.Future] = {}
        self._run_ui_command_cancel_reason: Optional[str] = None
        self.participant_id = ''
        # Cumulative wall-clock seconds spent blocked on a user reply (tool
        # approval, plan confirm, AskUserQuestion), plus the start of any wait
        # currently in progress. Consumers subtract this from a response
        # timeout so a slow human approval isn't mistaken for an unresponsive
        # agent, which previously timed out the turn and wedged the next one
        # (issue #381).
        self._user_input_wait_total = 0.0
        self._user_input_wait_started = None
        self._user_input_wait_count = 0

    @property
    def user_input_wait_seconds(self) -> float:
        """Total time this response has spent awaiting user input, including
        any wait still in progress."""
        with self._user_input_state_lock:
            total = self._user_input_wait_total
            started = self._user_input_wait_started
            if self._user_input_wait_count > 0 and started is not None:
                total += time.monotonic() - started
            return total

    @property
    def message_id(self) -> str:
        raise NotImplementedError

    def stream(self, data: ResponseStreamData, finish: bool = False) -> None:
        raise NotImplementedError
    
    def finish(self) -> None:
        raise NotImplementedError
    
    @property
    def user_input_signal(self) -> Signal:
        return self._user_input_signal

    def on_user_input(self, data: dict) -> None:
        if not isinstance(data, dict):
            log.warning("Ignoring malformed user-input response: %r", data)
            return
        callback_id = data.get('callback_id')
        if not isinstance(callback_id, str) or not callback_id or 'data' not in data:
            log.warning("Ignoring malformed user-input response: %r", data)
            return
        with self._user_input_state_lock:
            cancelled = self._user_input_cancel_reason is not None
            pending = None if cancelled else self._pending_user_inputs.pop(
                callback_id,
                None,
            )
        if pending is not None:
            try:
                pending.get_loop().call_soon_threadsafe(
                    self._resolve_user_input,
                    pending,
                    data['data'],
                )
            except RuntimeError:
                pass
        # Preserve the public signal contract for extension listeners. Core
        # approval handling uses the guarded future above, so a late signal
        # cannot override terminal cancellation.
        self._user_input_signal.emit(data)

    def cancel_pending_user_inputs(self, reason: str) -> None:
        with self._user_input_state_lock:
            self._user_input_cancel_reason = reason
            pending_inputs = list(self._pending_user_inputs.values())
            self._pending_user_inputs.clear()
        for pending in pending_inputs:
            try:
                pending.get_loop().call_soon_threadsafe(
                    self._reject_user_input,
                    pending,
                    reason,
                )
            except RuntimeError:
                pass

    @staticmethod
    def _resolve_user_input(pending: asyncio.Future, data: Any) -> None:
        if not pending.done():
            pending.set_result(data)

    @staticmethod
    def _reject_user_input(pending: asyncio.Future, reason: str) -> None:
        if not pending.done():
            pending.set_exception(UserInputCancelledError(reason))

    def prepare_chat_user_input(self, callback_id: str) -> asyncio.Future:
        """Register an expected callback before its prompt is streamed."""
        pending = asyncio.get_running_loop().create_future()
        pending.add_done_callback(self._consume_future_exception)
        with self._user_input_state_lock:
            reason = self._user_input_cancel_reason
            if reason is None:
                self._pending_user_inputs[callback_id] = pending
        if reason is not None:
            self._reject_user_input(pending, reason)
        return pending

    def stream_user_input_request(
        self,
        callback_id: str,
        data: ResponseStreamData,
    ) -> asyncio.Future:
        """Register expected input and stream its prompt as one operation."""
        pending = self.prepare_chat_user_input(callback_id)
        try:
            self.stream(data)
        except BaseException:
            with self._user_input_state_lock:
                if self._pending_user_inputs.get(callback_id) is pending:
                    self._pending_user_inputs.pop(callback_id, None)
            pending.cancel()
            raise
        return pending

    @staticmethod
    async def wait_for_chat_user_input(
        response: 'ChatResponse',
        callback_id: str,
        pending: Optional[asyncio.Future] = None,
    ):
        if pending is None:
            pending = response.prepare_chat_user_input(callback_id)
        with response._user_input_state_lock:
            if response._user_input_wait_count == 0:
                response._user_input_wait_started = time.monotonic()
            response._user_input_wait_count += 1

        try:
            return await pending
        finally:
            with response._user_input_state_lock:
                if response._pending_user_inputs.get(callback_id) is pending:
                    response._pending_user_inputs.pop(callback_id, None)
            with response._user_input_state_lock:
                response._user_input_wait_count -= 1
                if response._user_input_wait_count == 0:
                    started = response._user_input_wait_started
                    if started is not None:
                        response._user_input_wait_total += (
                            time.monotonic() - started
                        )
                    response._user_input_wait_started = None

    async def run_ui_command(self, command: str, args: dict = {}) -> dict:
        raise NotImplementedError
    
    @property
    def run_ui_command_response_signal(self) -> Signal:
        return self._run_ui_command_response_signal
    
    def on_run_ui_command_response(self, data: dict) -> None:
        if not isinstance(data, dict):
            log.warning("Ignoring malformed UI-command response: %r", data)
            return
        callback_id = data.get('callback_id')
        if not isinstance(callback_id, str) or not callback_id or 'result' not in data:
            log.warning("Ignoring malformed UI-command response: %r", data)
            return
        with self._run_ui_command_state_lock:
            pending = self._pending_ui_commands.pop(callback_id, None)
        if pending is not None:
            try:
                pending.get_loop().call_soon_threadsafe(
                    self._resolve_ui_command,
                    pending,
                    data['result'],
                )
            except RuntimeError:
                pass
        self._run_ui_command_response_signal.emit(data)

    def cancel_pending_ui_commands(self, reason: str) -> None:
        with self._run_ui_command_state_lock:
            self._run_ui_command_cancel_reason = reason
            pending_commands = list(self._pending_ui_commands.values())
            self._pending_ui_commands.clear()
        for pending in pending_commands:
            try:
                pending.get_loop().call_soon_threadsafe(
                    self._reject_ui_command,
                    pending,
                    reason,
                )
            except RuntimeError:
                # The owning event loop has already shut down, so there is
                # no worker left to wake.
                pass

    @staticmethod
    def _resolve_ui_command(pending: asyncio.Future, result: Any) -> None:
        if not pending.done():
            pending.set_result(result)

    @staticmethod
    def _reject_ui_command(pending: asyncio.Future, reason: str) -> None:
        if not pending.done():
            pending.set_exception(UICommandCancelledError(reason))

    def prepare_run_ui_command_response(self, callback_id: str) -> asyncio.Future:
        """Register a command waiter synchronously, before frontend dispatch."""
        pending = asyncio.get_running_loop().create_future()
        pending.add_done_callback(self._consume_future_exception)
        with self._run_ui_command_state_lock:
            reason = self._run_ui_command_cancel_reason
            if reason is None:
                self._pending_ui_commands[callback_id] = pending
        if reason is not None:
            self._reject_ui_command(pending, reason)
        return pending

    @staticmethod
    def _consume_future_exception(pending: asyncio.Future) -> None:
        """Prevent abandoned terminal futures from producing noisy warnings."""
        if not pending.cancelled():
            pending.exception()

    @staticmethod
    async def wait_for_run_ui_command_response(
        response: 'ChatResponse',
        callback_id: str,
        pending: Optional[asyncio.Future] = None,
    ):
        if pending is None:
            pending = response.prepare_run_ui_command_response(callback_id)
        try:
            return await pending
        finally:
            with response._run_ui_command_state_lock:
                if response._pending_ui_commands.get(callback_id) is pending:
                    response._pending_ui_commands.pop(callback_id, None)

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
        raise NotImplementedError

    @property
    def title(self) -> str:
        raise NotImplementedError
    
    @property
    def tags(self) -> list[str]:
        raise NotImplementedError
    
    @property
    def description(self) -> str:
        raise NotImplementedError
    
    @property
    def schema(self) -> dict:
        raise NotImplementedError

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        return None

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        raise NotImplementedError

class Toolset:
    def __init__(self, id: str, name: str, description: str, provider: Union['NotebookIntelligenceExtension', None], tools: Optional[list[Tool]] = None, instructions: str = None):
        self.id = id
        self.name = name
        self.description = description
        self.provider = provider
        # `tools=[]` as the default would share a single list across every
        # Toolset instance — `add_tool` on one would leak into all others.
        self.tools: list[Tool] = list(tools) if tools is not None else []
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
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", detail={"title": "Parameters", "content": json.dumps(tool_args)}, confirmationTitle=confirmationTitle, confirmationMessage=confirmationMessage)

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        fn_args = tool_args.copy()
        if self._has_var_args:
            fn_args.update({"request": request, "response": response})
        return await self._tool_function(**fn_args)

class PromptArgument:
    @property
    def name(self) -> str:
        raise NotImplementedError
    
    @property
    def description(self) -> str:
        raise NotImplementedError
    
    @property
    def required(self) -> bool:
        raise NotImplementedError

class MCPPrompt:
    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def title(self) -> str:
        raise NotImplementedError
    
    @property
    def description(self) -> str:
        raise NotImplementedError

    @property
    def arguments(self) -> list[PromptArgument]:
        raise NotImplementedError

    def get_value(self, prompt_args: dict = {}) -> str:
        raise NotImplementedError

class MCPServer:
    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def status(self) -> MCPServerStatus:
        raise NotImplementedError
    
    def connect(self):
        raise NotImplementedError

    def disconnect(self):
        raise NotImplementedError

    def update_tool_list(self):
        raise NotImplementedError
    
    def get_tools(self) -> list[Tool]:
        raise NotImplementedError

    def get_tool(self, tool_name: str) -> Tool:
        raise NotImplementedError

    def call_tool(self, tool_name: str, tool_args: dict):
        raise NotImplementedError

    def update_prompts_list(self):
        raise NotImplementedError

    def get_prompts(self) -> list[MCPPrompt]:
        raise NotImplementedError

    def get_prompt(self, prompt_name: str) -> MCPPrompt:
        raise NotImplementedError
    
    def get_prompt_value(self, prompt_name: str, prompt_args: dict = {}) -> list[dict]:
        raise NotImplementedError

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

    return SimpleTool(tool_function, mcp_tool.name, mcp_tool.description, schema, mcp_tool.name, auto_approve=False, has_var_args=has_var_args)

class ChatMode:
    def __init__(self, id: str, name: str, instructions: str = None):
        self.id = id
        self.name = name
        self.instructions = instructions

class ChatParticipant:
    @property
    def id(self) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def description(self) -> str:
        raise NotImplementedError
    
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
        raise NotImplementedError
    
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
                    message = choice['message']
                    # Some models use 'reasoning', some use 'reasoning_content'
                    raw_reasoning = message.get('reasoning') or message.get('reasoning_content') or ''
                    
                    reasoning = ''
                    if isinstance(raw_reasoning, dict):
                        reasoning = (raw_reasoning.get('text') or 
                                     raw_reasoning.get('content') or 
                                     raw_reasoning.get('thought') or 
                                     str(raw_reasoning))
                    else:
                        reasoning = str(raw_reasoning)
                    
                    content = message.get('content') or ''
                    if not isinstance(content, str):
                        content = str(content)
                    
                    if reasoning or content:
                        response.stream(MarkdownData(content=content, reasoning_content=reasoning))

                    if message.get('tool_calls', None) is not None:
                        for tool_call in message['tool_calls']:
                            tool_call_rounds.append(tool_call)

                    messages.append(message)

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
                            pending_user_input = response.stream_user_input_request(
                                tool_call['id'],
                                ConfirmationData(
                                title=tool_pre_invoke_response.confirmationTitle,
                                message=tool_pre_invoke_response.confirmationMessage,
                                confirmArgs={"id": response.message_id, "data": { "callback_id": tool_call['id'], "data": {"confirmed": True}}},
                                cancelArgs={"id": response.message_id, "data": { "callback_id": tool_call['id'], "data": {"confirmed": False}}},
                                ),
                            )
                            user_input = await ChatResponse.wait_for_chat_user_input(
                                response,
                                tool_call['id'],
                                pending_user_input,
                            )
                            if user_input['confirmed'] == False:
                                return

                    turn = perf.get_turn(response.message_id)
                    # builtin=True keeps the name readable in redacted mode,
                    # which is what the docs promise for NBI's own tools.
                    # This loop dispatches both NBI toolsets and MCP tools;
                    # MCPTool.call clears the flag for itself, so a
                    # third-party tool name still gets hashed.
                    span_cm = (
                        turn.span(f"tool:{tool_name}", tool=tool_name, builtin=True)
                        if turn is not None
                        else contextlib.nullcontext()
                    )
                    with span_cm:
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
            except (UserInputCancelledError, UICommandCancelledError) as error:
                log.debug("Tool call loop cancelled: %s", error)
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
        raise NotImplementedError

    def handle_completion_context_request(self, request: ContextRequest) -> CompletionContext:
        raise NotImplementedError

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
        raise NotImplementedError
    
    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def provider(self) -> 'LLMProvider':
        return self._provider
    
    @property
    def context_window(self) -> int:
        raise NotImplementedError

    @property
    def context_window_is_configured(self) -> bool:
        """Whether context_window is authoritative enough for pruning."""
        return True

    @property
    def supports_tools(self) -> bool:
        return False

    @property
    def supports_vision(self) -> bool:
        return False

class ChatModel(AIModel):
    def completions(self, messages: list[dict], tools: list[dict] = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
        raise NotImplementedError

class InlineCompletionModel(AIModel):
    def inline_completions(prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        raise NotImplementedError

class EmbeddingModel(AIModel):
    def embeddings(self, inputs: list[str]) -> Any:
        raise NotImplementedError

class LLMProvider(LLMPropertyProvider):
    def __init__(self):
        super().__init__()

    @property
    def id(self) -> str:
        raise NotImplementedError
    
    @property
    def name(self) -> str:
        raise NotImplementedError

    @property
    def chat_models(self) -> list[ChatModel]:
        raise NotImplementedError
    
    @property
    def inline_completion_models(self) -> list[InlineCompletionModel]:
        raise NotImplementedError
    
    @property
    def embedding_models(self) -> list[EmbeddingModel]:
        raise NotImplementedError

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
    OutputFollowUpRequest = 'output-follow-up-request'
    GenerateCodeRequest = 'generate-code-request'
    ChatRequest = 'chat-request'
    InlineChatRequest = 'inline-chat-request'
    ChatResponse = 'chat-response'
    InlineChatResponse = 'inline-chat-response'
    InlineChatAccepted = 'inline-chat-accepted'
    InlineChatDismissed = 'inline-chat-dismissed'
    InlineCompletionResponse = 'inline-completion-response'
    InlineCompletionAccepted = 'inline-completion-accepted'
    Feedback = 'feedback'

class TelemetryEvent:
    @property
    def type(self) -> TelemetryEventType:
        raise NotImplementedError
    
    @property
    def data(self) -> dict:
        return None

class TelemetryListener:
    @property
    def name(self) -> str:
        raise NotImplementedError

    def on_telemetry_event(self, event: TelemetryEvent):
        raise NotImplementedError

class Host:
    def register_llm_provider(self, provider: LLMProvider) -> None:
        raise NotImplementedError

    def register_chat_participant(self, participant: ChatParticipant) -> None:
        raise NotImplementedError

    def register_completion_context_provider(self, provider: CompletionContextProvider) -> None:
        raise NotImplementedError
    
    def register_telemetry_listener(self, listener: TelemetryListener) -> None:
        raise NotImplementedError

    def register_toolset(self, toolset: Toolset) -> None:
        raise NotImplementedError

    @property
    def nbi_config(self) -> NBIConfig:
        raise NotImplementedError

    @property
    def default_chat_participant(self) -> ChatParticipant:
        raise NotImplementedError
    
    @property
    def chat_model(self) -> ChatModel:
        raise NotImplementedError
    
    @property
    def inline_completion_model(self) -> InlineCompletionModel:
        raise NotImplementedError
    
    @property
    def embedding_model(self) -> EmbeddingModel:
        raise NotImplementedError

    def get_mcp_server(self, server_name: str) -> MCPServer:
        raise NotImplementedError

    def get_mcp_server_tool(self, server_name: str, tool_name: str) -> Tool:
        raise NotImplementedError

    def get_mcp_server_prompt(self, server_name: str, prompt_name: str) -> MCPPrompt:
        mcp_server = self.get_mcp_server(server_name)
        if mcp_server is not None:
            return mcp_server.get_prompt(prompt_name)
        return None

    def get_mcp_server_prompt_value(self, server_name: str, prompt_name: str, prompt_args: dict = {}) -> list[dict]:
        mcp_server = self.get_mcp_server(server_name)
        if mcp_server is not None:
            return mcp_server.get_prompt_value(prompt_name, prompt_args)
        return None

    def get_extension_toolset(self, extension_id: str, toolset_id: str) -> Toolset:
        raise NotImplementedError

    def get_extension_tool(self, extension_id: str, toolset_id: str, tool_name: str) -> Tool:
        raise NotImplementedError
    
    def get_rule_manager(self):
        """Get the rule manager instance if available."""
        raise NotImplementedError

    def get_skill_manager(self):
        """Get the skill manager instance if available."""
        raise NotImplementedError

    @property
    def websocket_connector(self) -> ThreadSafeWebSocketConnector:
        raise NotImplementedError
    

class NotebookIntelligenceExtension:
    @property
    def id(self) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError
    
    @property
    def provider(self) -> str:
        raise NotImplementedError

    @property
    def url(self) -> str:
        raise NotImplementedError

    def activate(self, host: Host) -> None:
        raise NotImplementedError
