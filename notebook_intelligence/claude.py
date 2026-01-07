# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
import os
import asyncio
from enum import Enum
from queue import Queue
import threading
import time
from typing import Any
import uuid
from anyio.abc import Process
from anthropic import Anthropic
from notebook_intelligence.api import AskUserQuestionData, BackendMessageType, CancelToken, ChatCommand, ChatModel, ChatRequest, ChatResponse, ClaudeToolType, CompletionContext, ConfirmationData, Host, InlineCompletionModel, MarkdownData, ProgressData, SignalImpl
from notebook_intelligence.base_chat_participant import BaseChatParticipant
import base64
import logging
from claude_agent_sdk import AssistantMessage, PermissionResultAllow, PermissionResultDeny, TextBlock, UserMessage, create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient, tool
from anthropic.types.text_block import TextBlock as AnthropicTextBlock

from notebook_intelligence.util import ThreadSafeWebSocketConnector, extract_llm_generated_code, get_jupyter_root_dir

log = logging.getLogger(__name__)

CLAUDE_CODE_ICON_SVG = '<svg width="1200" height="1200" viewBox="0 0 1200 1200" xmlns="http://www.w3.org/2000/svg"><g id="g314"><path id="path147" fill="#d97757" stroke="#d97757" d="M 233.959793 800.214905 L 468.644287 668.536987 L 472.590637 657.100647 L 468.644287 650.738403 L 457.208069 650.738403 L 417.986633 648.322144 L 283.892639 644.69812 L 167.597321 639.865845 L 54.926208 633.825623 L 26.577238 627.785339 L 3.3e-05 592.751709 L 2.73832 575.27533 L 26.577238 559.248352 L 60.724873 562.228149 L 136.187973 567.382629 L 249.422867 575.194763 L 331.570496 580.026978 L 453.261841 592.671082 L 472.590637 592.671082 L 475.328857 584.859009 L 468.724915 580.026978 L 463.570557 575.194763 L 346.389313 495.785217 L 219.543671 411.865906 L 153.100723 363.543762 L 117.181267 339.060425 L 99.060455 316.107361 L 91.248367 266.01355 L 123.865784 230.093994 L 167.677887 233.073853 L 178.872513 236.053772 L 223.248367 270.201477 L 318.040283 343.570496 L 441.825592 434.738342 L 459.946411 449.798706 L 467.194672 444.64447 L 468.080597 441.020203 L 459.946411 427.409485 L 392.617493 305.718323 L 320.778564 181.932983 L 288.80542 130.630859 L 280.348999 99.865845 C 277.369171 87.221436 275.194641 76.590698 275.194641 63.624268 L 312.322174 13.20813 L 332.8591 6.604126 L 382.389313 13.20813 L 403.248352 31.328979 L 434.013519 101.71814 L 483.865753 212.537048 L 561.181274 363.221497 L 583.812134 407.919434 L 595.892639 449.315491 L 600.40271 461.959839 L 608.214783 461.959839 L 608.214783 454.711609 L 614.577271 369.825623 L 626.335632 265.61084 L 637.771851 131.516846 L 641.718201 93.745117 L 660.402832 48.483276 L 697.530334 24.000122 L 726.52356 37.852417 L 750.362549 72 L 747.060486 94.067139 L 732.886047 186.201416 L 705.100708 330.52356 L 686.979919 427.167847 L 697.530334 427.167847 L 709.61084 415.087341 L 758.496704 350.174561 L 840.644348 247.490051 L 876.885925 206.738342 L 919.167847 161.71814 L 946.308838 140.29541 L 997.61084 140.29541 L 1035.38269 196.429626 L 1018.469849 254.416199 L 965.637634 321.422852 L 921.825562 378.201538 L 859.006714 462.765259 L 819.785278 530.41626 L 823.409424 535.812073 L 832.75177 534.92627 L 974.657776 504.724915 L 1051.328979 490.872559 L 1142.818848 475.167786 L 1184.214844 494.496582 L 1188.724854 514.147644 L 1172.456421 554.335693 L 1074.604126 578.496765 L 959.838989 601.449829 L 788.939636 641.879272 L 786.845764 643.409485 L 789.261841 646.389343 L 866.255127 653.637634 L 899.194702 655.409424 L 979.812134 655.409424 L 1129.932861 666.604187 L 1169.154419 692.537109 L 1192.671265 724.268677 L 1188.724854 748.429688 L 1128.322144 779.194641 L 1046.818848 759.865845 L 856.590759 714.604126 L 791.355774 698.335754 L 782.335693 698.335754 L 782.335693 703.731567 L 836.69812 756.885986 L 936.322205 846.845581 L 1061.073975 962.81897 L 1067.436279 991.490112 L 1051.409424 1014.120911 L 1034.496704 1011.704712 L 924.885986 929.234924 L 882.604126 892.107544 L 786.845764 811.48999 L 780.483276 811.48999 L 780.483276 819.946289 L 802.550415 852.241699 L 919.087341 1027.409424 L 925.127625 1081.127686 L 916.671204 1098.604126 L 886.469849 1109.154419 L 853.288696 1103.114136 L 785.073914 1007.355835 L 714.684631 899.516785 L 657.906067 802.872498 L 650.979858 806.81897 L 617.476624 1167.704834 L 601.771851 1186.147705 L 565.530212 1200 L 535.328857 1177.046997 L 519.302124 1139.919556 L 535.328857 1066.550537 L 554.657776 970.792053 L 570.362488 894.68457 L 584.536926 800.134277 L 592.993347 768.724976 L 592.429626 766.630859 L 585.503479 767.516968 L 514.22821 865.369263 L 405.825531 1011.865906 L 320.053711 1103.677979 L 299.516815 1111.812256 L 263.919525 1093.369263 L 267.221497 1060.429688 L 287.114136 1031.114136 L 405.825531 880.107361 L 477.422913 786.52356 L 523.651062 732.483276 L 523.328918 724.671265 L 520.590698 724.671265 L 205.288605 929.395935 L 149.154434 936.644409 L 124.993355 914.01355 L 127.973183 876.885986 L 139.409409 864.80542 L 234.201385 799.570435 L 233.879227 799.8927 Z"/></g></svg>'
CLAUDE_CODE_ICON_URL = f"data:image/svg+xml;base64,{base64.b64encode(CLAUDE_CODE_ICON_SVG.encode('utf-8')).decode('utf-8')}"
CLAUDE_DEFAULT_CHAT_MODEL = "claude-sonnet-4-5"
CLAUDE_DEFAULT_INLINE_COMPLETION_MODEL = "claude-sonnet-4-5"
CLAUDE_CODE_CHAT_PARTICIPANT_ID = "claude-code"
CLAUDE_CODE_MAX_BUFFER_SIZE = 20 * 1024 * 1024 # 20MB

JUPYTER_UI_TOOLS_SYSTEM_PROMPT = """You can interact with the JupyterLab UI (notebook / file editor, terminal, etc.) using the tools provided in 'nbi' MCP server. Tools in 'nbi' MCP server, directly interact with the JupyterLab UI, accessing notebooks and files in the UI. When interacting with JupyterLab UI, use relative file paths for file paths. If you create a notebook or run it, save it after creating or running it.
"""

class ClaudeAgentEventType(str, Enum):
    GetServerInfo = 'get-server-info'
    Query = 'query'
    ClearChatHistory = 'clear-chat-history'
    StopClient = 'stop-server'

class ClaudeAgentClientStatus(str, Enum):
    NotConnected = 'not-connected'
    Connecting = 'connecting'
    Disconnecting = 'disconnecting'
    FailedToConnect = 'failed-to-connect'
    Connected = 'connected'
    UpdatingServerInfo = 'updating-server-info'
    UpdatedServerInfo = 'updated-server-info'

CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME = float(os.getenv("NBI_CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME", "0.5"))
CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT = float(os.getenv("NBI_CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT", "1800"))
CLAUDE_AGENT_CLIENT_UPDATE_WAIT_TIME = float(os.getenv("NBI_CLAUDE_AGENT_CLIENT_UPDATE_WAIT_TIME", "3"))

_current_request = None
_current_response = None
_current_claude_client = None

_approved_tools_response_id: str = None
_approved_tools_for_response: set[str] = set()

def set_current_request(request: ChatRequest):
    global _current_request
    _current_request = request

def get_current_request() -> ChatRequest:
    global _current_request
    return _current_request

def set_current_response(response: ChatResponse):
    global _current_response
    _current_response = response

def get_current_response() -> ChatResponse:
    global _current_response
    return _current_response

def set_current_claude_client(client: ClaudeSDKClient):
    global _current_claude_client
    _current_claude_client = client

def get_current_claude_client() -> ClaudeSDKClient:
    global _current_claude_client
    return _current_claude_client

def tool_text_response(text: Any) -> dict[str, Any]:
    return {
        "content": [{
            "type": "text",
            "text": str(text)
        }]
    }

def model_info_from_id(model_id: str) -> dict:
    if model_id == "claude-sonnet-4-5":
        return {
            "name": "Claude Sonnet 4.5",
            "context_window": 200000,
        }
    elif model_id == "claude-haiku-4-5":
        return {
            "name": "Claude Haiku 4.5",
            "context_window": 200000,
        }
    elif model_id == "claude-opus-4-5":
        return {
            "name": "Claude Opus 4.5",
            "context_window": 200000,
        }
    return {
        "name": model_id,
        "context_window": 200000,
    }

class ClaudeChatModel(ChatModel):
    def __init__(self, model_id: str, api_key: str = None, base_url: str = None):
        super().__init__(provider=None)
        if model_id == "":
            model_id = CLAUDE_DEFAULT_CHAT_MODEL

        model_info = model_info_from_id(model_id)
        self._model_id = model_id
        self._model_name = model_info["name"]
        self._context_window = model_info["context_window"]
        self._supports_tools = True
        self._client = Anthropic(base_url=base_url, api_key=api_key)

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
    def supports_tools(self) -> bool:
        return self._supports_tools

    def completions(self, messages: list[dict], tools: list[dict] = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
        resp = self._client.messages.create(
            model=self._model_id,
            max_tokens=10000,
            messages=messages
        )
 
        for block in resp.content:
            if isinstance(block, AnthropicTextBlock):
                response.stream({
                    "choices": [{
                        "delta": {
                            "role": "assistant",
                            "content": block.text
                        }
                    }]
                })

        response.finish()

class ClaudeCodeInlineCompletionModel(InlineCompletionModel):
    def __init__(self, model_id: str, api_key: str = None, base_url: str = None):
        super().__init__(provider=None)
        if model_id == "":
            model_id = CLAUDE_DEFAULT_INLINE_COMPLETION_MODEL

        model_info = model_info_from_id(model_id)
        self._model_id = model_id
        self._model_name = model_info["name"]
        self._context_window = model_info["context_window"]
        self._client = Anthropic(base_url=base_url, api_key=api_key)

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return self._context_window

    def inline_completions(self, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        message = self._client.messages.create(
            model=self._model_id,
            max_tokens=10000,
            messages=[{"role": "user", "content": f"""Generate autocomplete suggestion for this code in {language}. Fill in the middle where <CURSOR> is. Keep it concise and return only the code that fits best in place of <CURSOR>. provide multiline code if needed. Don't return markdown formatting, just return the code in {language}. Don't return any other text, just the code. Don't return the prefix or suffix. The code is below in between <CODE> tags.

<CODE>
{prefix}
<CURSOR>
{suffix}
</CODE>
"""}]
        )
        code = ''
        for block in message.content:
            if isinstance(block, AnthropicTextBlock):
                code += block.text
        return extract_llm_generated_code(code)


class ClaudeCodeClient():
    def __init__(self, websocket_connector: ThreadSafeWebSocketConnector, client_options: ClaudeAgentOptions):
        self._client_options = client_options
        self._websocket_connector = websocket_connector
        self._client = None
        self._client_queue = None
        self._client_thread_signal = None
        self._client_thread = None
        self._status = ClaudeAgentClientStatus.NotConnected
        self._server_info: dict[str, Any] | None = None
        self._server_info_lock = threading.Lock()
        self._reconnect_required = False
        self.connect()

    @property
    def client_options(self) -> ClaudeAgentOptions:
        return self._client_options

    @client_options.setter
    def client_options(self, value: ClaudeAgentOptions):
        self._client_options = value

    @property
    def websocket_connector(self) -> ThreadSafeWebSocketConnector:
        return self._websocket_connector

    @websocket_connector.setter
    def websocket_connector(self, websocket_connector: ThreadSafeWebSocketConnector):
        self._websocket_connector = websocket_connector
    
    @property
    def status(self) -> ClaudeAgentClientStatus:
        return self._status

    def is_connected(self):
        return self._client_thread is not None

    def connect(self):
        if self.is_connected():
            return

        self._set_status(ClaudeAgentClientStatus.Connecting)

        self._reconnect_required = False
        self._client_queue = Queue()
        self._client_thread_signal: SignalImpl = SignalImpl()
        try:
            self._client_thread = threading.Thread(
                name="Claude Agent Client Thread",
                target=asyncio.run,
                daemon=True,
                args=(self._client_thread_func(),)
            )
            self._client_thread.start()
            self._update_server_info_async()
        except Exception as e:
            self._client_thread = None
            log.error(f"Error occurred while connecting to Claude agent client: {str(e)}")
            self._set_status(ClaudeAgentClientStatus.FailedToConnect)

    def disconnect(self):
        if not self.is_connected():
            return

        self._set_status(ClaudeAgentClientStatus.Disconnecting)

        response = self._send_claude_agent_request(ClaudeAgentEventType.StopClient)
        if not response["success"]:
            log.error(f"Claude agent client failed to stop: {response['error']}")

        self._mark_as_disconnected()
        self._server_info = None

    def _mark_as_disconnected(self):
        self._set_status(ClaudeAgentClientStatus.NotConnected)

        self._client_queue = None
        self._client_thread_signal = None
        self._client_thread = None
        self._client = None
        self._server_info = None

    def _update_server_info_async(self):
        thread = threading.Thread(target=self._update_server_info, args=())
        thread.start()
    
    def _update_server_info(self):
        with self._server_info_lock:
            self.update_server_info()
    
    def _set_status(self, status: ClaudeAgentClientStatus):
        self._status = status
        if self._websocket_connector is not None:
            try:
                self._websocket_connector.write_message({
                        "type": BackendMessageType.ClaudeCodeStatusChange,
                        "data": {}
                    })
            except Exception as e:
                log.error(f"Error occurred while sending status message to websocket: {str(e)}")

    async def _client_thread_func(self):
        try:
            async with await self._get_client() as client:
                self._set_status(ClaudeAgentClientStatus.Connected)
                set_current_claude_client(client)

                while True:
                    event = self._client_queue.get(block=True)
                    event_id = event["id"]
                    event_type = event["type"]
                    if event_type == ClaudeAgentEventType.Query:
                        try:
                            request: ChatRequest = event["args"]["request"]
                            response: ChatResponse = event["args"]["response"]

                            set_current_request(request)
                            set_current_response(response)

                            messages = request.chat_history
                            query_lines = []
                            for msg in messages:
                                if msg["role"] == "user":
                                    query_lines.append(msg["content"])
                            # if a command is present, remove other lines
                            if len(query_lines) > 0 and query_lines[-1].startswith('/'):
                                query_lines = query_lines[-1:]
                            client_query = "\n".join([line.strip() for line in query_lines])

                            already_handled = False

                            if client_query.startswith('/enter-plan-mode'):
                                await client.set_permission_mode("plan")
                                response.stream(MarkdownData("&#x2713; Entered plan mode"))
                                already_handled = True
                            elif client_query.startswith('/exit-plan-mode'):
                                await client.set_permission_mode("default")
                                response.stream(MarkdownData("&#x2713; Exit plan mode"))
                                already_handled = True

                            if not already_handled and not request.cancel_token.is_cancel_requested:
                                await client.query(client_query)
                                async for message in client.receive_response():
                                    if request.cancel_token.is_cancel_requested:
                                        continue
                                    if isinstance(message, AssistantMessage):
                                        for block in message.content:
                                            if isinstance(block, TextBlock):
                                                response.stream(MarkdownData(block.text))
                                    elif isinstance(message, UserMessage):
                                        if isinstance(message.content, str):
                                            content = message.content
                                            content = content.replace('<local-command-stdout>', '').replace('</local-command-stdout>', '')
                                            response.stream(MarkdownData(content))
                                        elif isinstance(message.content, TextBlock):
                                            content = message.content.text
                                            content = content.replace('<local-command-stdout>', '').replace('</local-command-stdout>', '')
                                            response.stream(MarkdownData(content))
                                    else:
                                        pass
                        except Exception as e:
                            log.error(f"Error communicating with Claude agent: {str(e)}")
                            if not self._reconnect_required:
                                response.stream(MarkdownData(f"Error communicating with Claude agent: {str(e)}"))
                        finally:
                            if not self._reconnect_required:
                                self._client_thread_signal.emit({
                                    "id": event_id,
                                    "data": "query completed"
                                })
                            set_current_request(None)
                            set_current_response(None)
                    elif event_type == ClaudeAgentEventType.GetServerInfo:
                        try:
                            server_info = await client.get_server_info()
                        except Exception as e:
                            log.error(f"Error occurred while getting server info: {str(e)}")
                            server_info = None
                        finally:
                            self._client_thread_signal.emit({
                                "id": event_id,
                                "data": server_info
                            })
                    elif event_type == ClaudeAgentEventType.ClearChatHistory:
                        try:
                           await client.query('/clear')
                           async for message in client.receive_response():
                                # clear response messages
                                pass
                        except Exception as e:
                            log.error(f"Error occurred while clearing chat history: {str(e)}")
                        finally:
                            self._client_thread_signal.emit({
                                "id": event_id,
                                "data": "chat history cleared"
                            })
                    elif event_type == ClaudeAgentEventType.StopClient:
                        self._client_thread_signal.emit({
                            "id": event_id,
                            "data": "stopped"
                        })
                        return
                    else:
                        log.error(f"Unknown event type {event}")
        except Exception as e:
            self._client_thread = None
            log.error(f"Error occurred while running MCP server thread: {str(e)}")
            self._set_status(ClaudeAgentClientStatus.FailedToConnect)

    def _create_client(self) -> ClaudeSDKClient:
        return ClaudeSDKClient(options=self._client_options)

    async def _get_client(self) -> ClaudeSDKClient:
        if self._client is None:
            self._client = self._create_client()
        # else:
        #     try:
        #         async with self._client:
        #             await self._client.ping()
        #     except Exception as e:
        #         self._client = self._create_client()
        return self._client

    def _send_claude_agent_request(self, event_type: ClaudeAgentEventType, event_args: dict = None):
        event_id = uuid.uuid4().hex
        event = {
            "id": event_id,
            "type": event_type,
            "args": event_args,
        }
        set_current_request(None)
        self._client_queue.put(event)

        resp = {"data": None}
        def _on_client_response(data: dict):
            if data['id'] == event_id:
                resp["data"] = data['data']

        self._client_thread_signal.connect(_on_client_response)

        start_time = time.time()

        while True:
            self._reconnect_required = False
            nbi_request_obj = get_current_request()
            if nbi_request_obj is not None and nbi_request_obj.cancel_token.is_cancel_requested:
                try:
                    process: Process = self._client._transport._process
                    process.kill()

                    self._reconnect_required = True
                except Exception as e:
                    log.error(f"Error occurred while setting current request and response to None: {str(e)}")
                self._client_thread_signal.disconnect(_on_client_response)
                if self._reconnect_required:
                    self._mark_as_disconnected()
                return {
                    "data": None,
                    "success": False,
                    "error": "Cancel requested by user"
                }
            if resp["data"] is not None:
                self._client_thread_signal.disconnect(_on_client_response)
                return {
                    "data": resp["data"],
                    "success": True,
                    "error": None
                }
            if time.time() - start_time > CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT:
                self._client_thread_signal.disconnect(_on_client_response)
                return {
                    "data": None,
                    "success": False,
                    "error": f"Claude agent client response timeout"
                }
            time.sleep(CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME)

    def update_server_info(self):
        if self._reconnect_required:
            self.connect()
        if not self.is_connected():
            return
        self._set_status(ClaudeAgentClientStatus.UpdatingServerInfo)
        response = self._send_claude_agent_request(ClaudeAgentEventType.GetServerInfo)
        if response["success"]:
            self._server_info = response["data"]
        else:
            log.error(f"Claude agent client failed to update server info: {response['error']}")
        self._set_status(ClaudeAgentClientStatus.UpdatedServerInfo)

    @property
    def server_info(self) -> dict[str, Any] | None:
        return self._server_info

    def query(self, request: ChatRequest, response: ChatResponse):
        if self._reconnect_required:
            self.connect()
        if not self.is_connected():
            return f"Claude agent is not connected"

        response = self._send_claude_agent_request(ClaudeAgentEventType.Query, {
            "request": request,
            "response": response
        })

        if response["success"]:
            return response["data"]
        else:
            log.error(f"Claude agent query failed: {response['error']}")
            return response["error"]

    def clear_chat_history(self):
        if self._reconnect_required:
            self.connect()
        if not self.is_connected():
            return
        response = self._send_claude_agent_request(ClaudeAgentEventType.ClearChatHistory)
        if response["success"]:
            return response["data"]
        else:
            log.error(f"Claude agent client failed to clear chat history: {response['error']}")
            return response["error"]
    
    def reconnect(self):
        self.disconnect()
        self.connect()


@tool("create-new-notebook", "Creates a new empty notebook.", {})
async def create_new_notebook(args) -> str:
    """Creates a new empty notebook.
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:create-new-notebook-from-py', {'code': ''})
    file_path = ui_cmd_response['path']

    return tool_text_response(f"Created new notebook at {file_path}")

@tool("rename-notebook", "Renames the notebook.", {"new_name": str})
async def rename_notebook(args) -> str: 
    """Renames the notebook.
    Args:
        new_name: New name for the notebook
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:rename-notebook', {'newName': args['new_name']})
    return tool_text_response(ui_cmd_response)

@tool("add-markdown-cell", "Adds a markdown cell to the notebook.", {"source": str})
async def add_markdown_cell(args) -> str:
    """Adds a markdown cell to notebook.
    Args:
        source: Markdown source
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-markdown-cell-to-active-notebook', {'source': args['source']})

    return tool_text_response(f"Added markdown cell to notebook")

@tool("add-code-cell", "Adds a code cell to the notebook.", {"source": str})
async def add_code_cell(args) -> str:
    """Adds a code cell to notebook.
    Args:
        source: Python code source
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-active-notebook', {'source': args['source']})

    return tool_text_response(f"Added code cell to notebook")

@tool("get-number-of-cells", "Gets the number of cells in the notebook.", {})
async def get_number_of_cells(args) -> str:
    """Get number of cells for the active notebook.
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-number-of-cells', {})

    return tool_text_response(ui_cmd_response)

@tool("get-cell-type-and-source", "Gets the type and source of the cell at index.", {"cell_index": int})
async def get_cell_type_and_source(args) -> str:
    """Get cell type and source for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-type-and-source', {"cellIndex": args['cell_index'] })

    return tool_text_response(ui_cmd_response)


@tool("get-cell-output", "Gets the output of the cell at index.", {"cell_index": int})
async def get_cell_output(args) -> str:
    """Get cell output for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-output', {"cellIndex": args['cell_index']})

    return tool_text_response(ui_cmd_response)

@tool("set-cell-type-and-source", "Sets the type and source of the cell at index.", {"cell_index": int, "cell_type": str, "source": str})
async def set_cell_type_and_source(args) -> str:
    """Set cell type and source for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
        cell_type: Cell type (code or markdown)
        source: Markdown or Python code source
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:set-cell-type-and-source', {"cellIndex": args['cell_index'], "cellType": args['cell_type'], "source": args['source']})

    return tool_text_response(ui_cmd_response)

@tool("delete-cell", "Deletes the cell at index.", {"cell_index": int})
async def delete_cell(args) -> str:
    """Delete the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = get_current_response()

    ui_cmd_response = await response.run_ui_command('notebook-intelligence:delete-cell-at-index', {"cellIndex": args['cell_index']})

    return tool_text_response(f"Deleted the cell at index: {args['cell_index']}")

@tool("insert-cell", "Inserts a cell with type and source at index.", {"cell_index": int, "cell_type": str, "source": str})
async def insert_cell(args) -> str:
    """Insert cell with type and source at index for the active notebook.

    Args:
        cell_index: Zero based cell index
        cell_type: Cell type (code or markdown)
        source: Markdown or Python code source
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:insert-cell-at-index', {"cellIndex": args['cell_index'], "cellType": args['cell_type'], "source": args['source']})

    return tool_text_response(ui_cmd_response)

@tool("run-cell", "Runs the cell at index.", {"cell_index": int})
async def run_cell(args) -> str:
    """Run the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = get_current_response()

    ui_cmd_response = await response.run_ui_command('notebook-intelligence:run-cell-at-index', {"cellIndex": args['cell_index'] if args['cell_index'] is not None else 0})

    return tool_text_response(f"Ran the cell at index: {args['cell_index'] if args['cell_index'] is not None else 0}")

@tool("save-notebook", "Saves the changes in active notebook to disk.", {})
async def save_notebook(args) -> str:
    """Save the changes in active notebook to disk.
    """
    response: ChatResponse = get_current_response()
    ui_cmd_response = await response.run_ui_command('docmanager:save')

    return tool_text_response(f"Saved the notebook")

@tool("run-command-in-jupyter-terminal", "Runs a shell command in a Jupyter terminal within working_directory.", {"command": str, "working_directory": str})
async def run_command_in_jupyter_terminal(args) -> str:
    """Run a shell command in a Jupyter terminal within working_directory. This can be used to run long running processes like web applications. Returns the output of the command.
    
    Args:
        command: Shell command to execute in the terminal
        working_directory: Directory to execute command in (relative to Jupyter working directory, default is '' which translates to the Jupyter working directory root)
    """
    try:
        response = get_current_response()
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:run-command-in-terminal', {
            'command': args['command'],
            'cwd': args['working_directory']
        })
        return tool_text_response(ui_cmd_response)
    except Exception as e:
        return tool_text_response(f"Error running command in Jupyter terminal: {str(e)}")


@tool("open-file-in-jupyter-ui", "Opens a file in the Jupyter UI.", {"file_path": str})
async def open_file_in_jupyter_ui(args) -> str:
    """Open a file in the Jupyter UI.
    
    Args:
        file_path: Path to the file to open
    """
    try:
        response = get_current_response()
        ui_cmd_response = await response.run_ui_command('docmanager:open', {
            'path': args['file_path']
        })
        return tool_text_response(ui_cmd_response)
    except Exception as e:
        return tool_text_response(f"Error opening file in Jupyter UI: {str(e)}")

async def custom_permission_handler(
    tool_name: str,
    input_data: dict,
    context: dict
):
    """Custom logic for tool permissions."""
    global _approved_tools_response_id
    global _approved_tools_for_response

    log.debug(f"Custom permission handler called for tool {tool_name} with input {input_data} and context {context}")

    response = get_current_response()
    callback_id = str(uuid.uuid4())

    if tool_name == "EnterPlanMode":
        response.stream(ConfirmationData(
            title="Enter Plan Mode",
            message="Claude wants to enter plan mode to explore and design an implementation approach. In plan mode, Claude will explore the codebase thoroughly, identify existing patterns, design an implementation strategy, and present a plan for your approval. No code changes will be made until you approve the plan.",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
            confirmLabel="Yes, enter plan mode",
            cancelLabel="No, start implementing now",
        ))
        user_input = await ChatResponse.wait_for_chat_user_input(response, callback_id)
        if user_input['confirmed'] == True:
            response.stream(MarkdownData(f"&#x2713; Entered plan mode"))
            return PermissionResultAllow()
        else:
            return PermissionResultDeny(message="Skipping plan mode...")
    elif tool_name == "ExitPlanMode":
        plan = input_data.get('plan')
        if plan is not None:
            response.stream(MarkdownData(plan))
        else:
            log.error(f"No plan provided in ExitPlanMode tool call")
        response.stream(ConfirmationData(
            message="Do you want to confirm the plan above?",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
            confirmLabel="Yes, approve plan",
            cancelLabel="No, continue planning",
        ))
        user_input = await ChatResponse.wait_for_chat_user_input(response, callback_id)
        if user_input['confirmed'] == True:
            await get_current_claude_client().set_permission_mode("default")
            return PermissionResultAllow(updated_input={"message": "Plan approved", "approved": True})
        else:
            return PermissionResultDeny(message="User did not confirm the plan", interrupt=True)
    elif tool_name == "AskUserQuestion":
        response.stream(AskUserQuestionData(
            identifier={"id": response.message_id, "callback_id": callback_id},
            questions=input_data['questions']
        ))
        user_input = await ChatResponse.wait_for_chat_user_input(response, callback_id)
        if user_input['confirmed'] == False or len(user_input['selectedAnswers']) == 0:
            return PermissionResultDeny(message="User did not choose any options", interrupt=True)
        else:
            selected_answers = user_input['selectedAnswers']
            answers = {}
            for question in selected_answers.keys():
                answers[question] = ", ".join(selected_answers[question])
            return PermissionResultAllow(updated_input={
                "questions": input_data['questions'],
                "answers": answers
            })
    elif tool_name == "Bash":
        response.stream(MarkdownData(f"&#x2713; **{input_data.get('description', '')}**\n```shell\n{input_data.get('command', '')}\n```"))
        response.stream(ConfirmationData(
            message=f"Approve Bash tool to execute the command above?",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
        ))
        user_input = await ChatResponse.wait_for_chat_user_input(response, callback_id)
        if user_input['confirmed'] == False:
            response.finish()
            return PermissionResultDeny(message="User did not confirm the tool call", interrupt=True)

        log.debug(f"Allowing tool {tool_name} with input {input_data}")
        return PermissionResultAllow()
    else:
        if _approved_tools_response_id != response.message_id:
            _approved_tools_for_response.clear()

        if tool_name in _approved_tools_for_response:
            return PermissionResultAllow()
        response.stream(MarkdownData(f"&#x2713; Calling tool '{tool_name}'...", detail={"title": "Parameters", "content": json.dumps(input_data)}))
        response.stream(ConfirmationData(
            message=f"Are you sure you want to call this tool?",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            confirmSessionArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed_for_session": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
        ))
        user_input = await ChatResponse.wait_for_chat_user_input(response, callback_id)
        if user_input.get('confirmed', None) == False:
            response.finish()
            return PermissionResultDeny(message="User did not confirm the tool call", interrupt=True)

        if user_input.get('confirmed_for_session', None) == True:
            _approved_tools_for_response.add(tool_name)
            _approved_tools_response_id = response.message_id

        log.debug(f"Allowing tool {tool_name} with input {input_data}")
        return PermissionResultAllow()

class ClaudeCodeChatParticipant(BaseChatParticipant):
    def __init__(self, host: Host):
        super().__init__()
        self._update_client_debounced_timer = None
        self._host = host
        self._client_options: ClaudeAgentOptions = self._create_client_options()
        self._client = ClaudeCodeClient(host.websocket_connector, self._client_options)

    @property
    def id(self) -> str:
        return CLAUDE_CODE_CHAT_PARTICIPANT_ID
    
    @property
    def name(self) -> str:
        return "Claude Code"

    @property
    def description(self) -> str:
        return "Claude Code"
    
    @property
    def icon_path(self) -> str:
        return CLAUDE_CODE_ICON_URL
    
    @property
    def commands(self) -> list[ChatCommand]:
        participant_commands = [
            ChatCommand(name='clear', description='Clear chat history')
        ]
        server_info = self._client.server_info
        if server_info is not None:
            commands = server_info.get('commands', [])
            for command in commands:
                participant_commands.append(ChatCommand(name=command['name'], description=command['description']))
            return participant_commands
        else:
            return [
                ChatCommand(name='compact', description='Compact chat history'),
                ChatCommand(name='context', description='Show context of the chat'),
                ChatCommand(name='cost', description='Show cost of the chat'),
                ChatCommand(name='clear', description='Clear chat history'),
            ]

    @property
    def websocket_connector(self) -> ThreadSafeWebSocketConnector:
        return self._client.websocket_connector
    
    @websocket_connector.setter
    def websocket_connector(self, websocket_connector: ThreadSafeWebSocketConnector):
        self._client.websocket_connector = websocket_connector
    
    def chat_prompt(self, model_provider: str, model_name: str) -> str:
        return ""

    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        if request.chat_mode.id == "inline-chat":
            return await self.handle_inline_chat_request(request, response, options)
        self._current_chat_request = request

        response.stream(ProgressData("Thinking..."))
        self._client.query(request, response)
        response.finish()

    async def handle_inline_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        try:
            claude_settings = request.host.nbi_config.claude_settings
            chat_model_id = claude_settings.get('chat_model', '').strip()
            chat_model = ClaudeChatModel(
                chat_model_id,
                claude_settings.get('api_key', None),
                claude_settings.get('base_url', None)
            )
            messages = request.chat_history.copy()
            chat_model.completions(messages, response=response, cancel_token=request.cancel_token)
        except Exception as e:
            log.error(f"Error while handling chat request!\n{e}")
            response.stream(MarkdownData(f"Oops! There was a problem handling chat request. Please try again with a different prompt."))
            response.finish()
    
    def _create_client_options(self) -> ClaudeAgentOptions:
        claude_settings = self._host.nbi_config.claude_settings
        self._jupyter_ui_tools_mcp_server = create_sdk_mcp_server(
            name="nbi",
            version="1.0.0",
            tools=[create_new_notebook, add_markdown_cell, add_code_cell, get_number_of_cells, get_cell_type_and_source, get_cell_output, set_cell_type_and_source, delete_cell, insert_cell, run_cell, save_notebook, rename_notebook, run_command_in_jupyter_terminal, open_file_in_jupyter_ui]
        )
        mcp_servers = {}
        jupyter_ui_tools_enabled = ClaudeToolType.JupyterUITools in claude_settings.get('tools', [])
        if jupyter_ui_tools_enabled:
            mcp_servers["nbi"] = self._jupyter_ui_tools_mcp_server
        allowed_tools = []
        if jupyter_ui_tools_enabled:
            allowed_tools.extend(["mcp__nbi__create-new-notebook", "mcp__nbi__add-markdown-cell", "mcp__nbi__add-code-cell", "mcp__nbi__get-number-of-cells", "mcp__nbi__get-cell-type-and-source", "mcp__nbi__get-cell-output", "mcp__nbi__set-cell-type-and-source", "mcp__nbi__insert-cell", "mcp__nbi__save-notebook", "mcp__nbi__rename-notebook", "mcp__nbi__open-file-in-jupyter-ui"])
        setting_sources = claude_settings.get('setting_sources')
        chat_model_id = claude_settings.get('chat_model', '').strip()
        if chat_model_id == "":
            chat_model_id = None

        env = {}
        base_url = claude_settings.get('base_url', '')
        if base_url != '':
            env['ANTHROPIC_BASE_URL'] = base_url

        client_options = ClaudeAgentOptions(
            system_prompt=self._create_system_prompt(jupyter_ui_tools_enabled),
            cwd=get_jupyter_root_dir(),
            model=chat_model_id,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            setting_sources=setting_sources,
            can_use_tool=custom_permission_handler,
            env=env,
            max_buffer_size=CLAUDE_CODE_MAX_BUFFER_SIZE,
        )
        return client_options

    def _create_system_prompt(self, jupyter_ui_tools_enabled: bool) -> str:
        return f"""You are an AI programming assistant integrated into JupyterLab which is an IDE for Jupyter notebooks.
Assume Python if the language is not specified.
JupyterLab is launched from a working directory and it can only access files in this directory and its subdirectories. Follow the same rule for file system access. Working directory for current session is '{get_jupyter_root_dir()}'.
If messages contain relative file paths, assume they are relative to the working directory.
If you need to install a Python package within a notebook cell code, use %pip install <package_name> instead of !pip install <package_name>.
{JUPYTER_UI_TOOLS_SYSTEM_PROMPT if jupyter_ui_tools_enabled else ""}
"""

    def clear_chat_history(self):
        self._client.clear_chat_history()
        self._client.reconnect()

    def update_client(self):
        self._client_options = self._create_client_options()
        self._client.client_options = self._client_options
        self._client.disconnect()
        claude_enabled = self._host.nbi_config.claude_settings.get('enabled', False)
        if claude_enabled:
            self._client.connect()

    def update_client_debounced(self):
        if self._update_client_debounced_timer is not None:
            self._update_client_debounced_timer.cancel()
        self._update_client_debounced_timer = asyncio.get_event_loop().create_task(self._update_client_debounced())

    async def _update_client_debounced(self):
        await asyncio.sleep(CLAUDE_AGENT_CLIENT_UPDATE_WAIT_TIME)
        self.update_client()
