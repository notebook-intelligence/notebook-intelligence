# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import asyncio
from dataclasses import dataclass
import json
import os
from queue import Queue
import threading
import time
from typing import Any, Union
import uuid
from fastmcp.client import StdioTransport, StreamableHttpTransport
from mcp import StdioServerParameters
from mcp.client.stdio import get_default_environment as mcp_get_default_environment
from mcp.types import CallToolResult, TextContent, ImageContent
from tornado import ioloop
from notebook_intelligence.api import BackendMessageType, ChatCommand, ChatRequest, ChatResponse, HTMLFrameData, ImageData, MCPServer, MCPServerStatus, MarkdownData, ProgressData, SignalImpl, Tool, ToolPreInvokeResponse
from notebook_intelligence.base_chat_participant import BaseChatParticipant
import logging
from enum import Enum
from fastmcp import Client

from notebook_intelligence.util import ThreadSafeWebSocketConnector

log = logging.getLogger(__name__)

MCP_ICON_SRC = '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'
MCP_ICON_URL = f"data:image/png;base64,{MCP_ICON_SRC}"
MCP_SERVER_RESPONSE_TIMEOUT = float(os.getenv("NBI_MCP_SERVER_RESPONSE_TIMEOUT", "30"))

class MCPServerEventType(str, Enum):
    ListTools = 'list-tools'
    CallTool = 'call-tool'
    StopServer = 'stop-server'

class MCPTool(Tool):
    def __init__(self, server: 'MCPServer', name, description, schema, auto_approve=False):
        super().__init__()
        self._server = server
        self._name = name
        self._description = description
        self._schema = schema
        self._auto_approve = auto_approve

    @property
    def name(self) -> str:
        return self._name

    @property
    def title(self) -> str:
        return self._name
    
    @property
    def tags(self) -> list[str]:
        return ["mcp-tool"]
    
    @property
    def description(self) -> str:
        return self._description

    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self._name,
                "description": self._description,
                "strict": False,
                "parameters": self._schema
            },
        }
    
    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        confirmationTitle = None
        confirmationMessage = None
        if not self._auto_approve:
            confirmationTitle = "Approve"
            confirmationMessage = "Are you sure you want to call this MCP tool?"
        return ToolPreInvokeResponse(f"Calling MCP tool '{self.name}'", detail={"title": "Parameters", "content": json.dumps(tool_args)}, confirmationTitle=confirmationTitle, confirmationMessage=confirmationMessage)

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        call_args = {}

        for key in self._schema['properties']:
            if key in tool_args:
                call_args[key] = tool_args.get(key)

        try:
            result = self._server.call_tool(self.name, call_args)
            if hasattr(result, "content") and isinstance(result.content, list):
                if len(result.content) > 0:
                    text_contents = []
                    for content in result.content:
                        if type(content) is ImageContent:
                            response.stream(ImageData(f"data:{content.mimeType};base64,{content.data}"))
                        elif type(content) is TextContent:
                            text_contents.append(content.text)

                    if len(text_contents) > 0:
                        return "\n".join(text_contents)
                    else:
                        return "success"
            elif type(result) is dict:
                return result
            else:
                return f"Error! Invalid tool result: {result}"
        except Exception as e:
            return f"Error occurred while calling MCP tool: {str(e)}"

@dataclass
class StreamableHttpServerParameters:
    url: str
    headers: dict[str, Any] | None = None

class MCPServerImpl(MCPServer):
    def __init__(self, manager: "MCPManager", name: str, stdio_params: StdioServerParameters = None, streamable_http_params: StreamableHttpServerParameters = None, auto_approve_tools: list[str] = []):
        self._manager = manager
        self._name: str = name
        self._stdio_params: StdioServerParameters = stdio_params
        self._streamable_http_params: StreamableHttpServerParameters = streamable_http_params
        self._auto_approve_tools: set[str] = set(auto_approve_tools)
        self._tried_to_get_tool_list = False
        self._mcp_tools = []
        self._session = None
        self._client = None
        self._client_queue = None
        self._client_thread_signal = None
        self._client_thread = None
        self._status = MCPServerStatus.NotConnected
        self.connect()

    @property
    def name(self) -> str:
        return self._name
    
    @property
    def status(self) -> MCPServerStatus:
        return self._status

    def is_connected(self):
        return self._client_thread is not None

    def connect(self):
        if self.is_connected():
            return

        self._set_status(MCPServerStatus.Connecting)
        
        self._client_queue = Queue()
        self._client_thread_signal: SignalImpl = SignalImpl()
        try:
            self._client_thread = threading.Thread(
                name="MCP Server Thread",
                target=asyncio.run,
                daemon=True,
                args=(self._client_thread_func(),)
            )
            self._client_thread.start()
        except Exception as e:
            self._client_thread = None
            log.error(f"Error occurred while connecting to MCP server: {str(e)}")
            self._set_status(MCPServerStatus.FailedToConnect)

    def disconnect(self):
        if not self.is_connected():
            return

        self._set_status(MCPServerStatus.Disconnecting)

        response = self._send_mcp_request(MCPServerEventType.StopServer)
        if not response["success"]:
            log.error(f"MCP server '{self.name}' failed to stop: {response['error']}")

        self._set_status(MCPServerStatus.NotConnected)

        self._client_queue = None
        self._client_thread_signal = None
        self._client_thread = None

    
    def _set_status(self, status: MCPServerStatus):
        self._status = status
        if self._manager.websocket_connector is not None:
            self._manager.websocket_connector.write_message({
                "type": BackendMessageType.MCPServerStatusChange,
                "data": {}
            })

    async def _client_thread_func(self):
        try:
            async with await self._get_client() as client:
                self._set_status(MCPServerStatus.Connected)
                while True:
                    event = self._client_queue.get(block=True)
                    event_id = event["id"]
                    event_type = event["type"]
                    if event_type == MCPServerEventType.ListTools:
                        try:
                            tool_list = await client.list_tools()
                        except Exception as e:
                            log.error(f"Error occurred while listing MCP tools: {str(e)}")
                            tool_list = []
                        finally:
                            self._client_thread_signal.emit({
                                "id": event_id,
                                "data": tool_list
                            })
                    elif event_type == MCPServerEventType.CallTool:
                        try:
                            result = await client.call_tool(event["args"]["tool_name"], event["args"]["tool_args"])
                        except Exception as e:
                            result = f"Error occurred while calling MCP tool {event['args']['tool_name']}: {str(e)}"
                            log.error(result)
                        finally:
                            self._client_thread_signal.emit({
                                "id": event_id,
                                "data": result
                            })
                    elif event_type == MCPServerEventType.StopServer:
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
            self._set_status(MCPServerStatus.FailedToConnect)

    def _create_client(self) -> Client:
        if self._stdio_params is not None:
            return Client(transport=StdioTransport(
                command=self._stdio_params.command,
                args=self._stdio_params.args,
                env=self._stdio_params.env
            ))
        elif self._streamable_http_params is not None:
            return Client(transport=StreamableHttpTransport(
                url=self._streamable_http_params.url,
                headers=self._streamable_http_params.headers
            ))

    async def _get_client(self) -> Client:
        if self._stdio_params is None and self._streamable_http_params is None:
            raise ValueError("Failed to create MCP client. Either stdio_params or sse_params must be provided")
        if self._client is None:
            self._client = self._create_client()
        else:
            try:
                async with self._client:
                    await self._client.ping()
            except Exception as e:
                self._client = self._create_client()
        return self._client

    def _send_mcp_request(self, event_type: MCPServerEventType, event_args: dict = None):
        event_id = uuid.uuid4().hex
        event = {
            "id": event_id,
            "type": event_type,
            "args": event_args,
        }
        self._client_queue.put(event)

        resp = {"data": None}
        def _on_client_response(data: dict):
            if data['id'] == event_id:
                resp["data"] = data['data']

        self._client_thread_signal.connect(_on_client_response)

        start_time = time.time()

        while True:
            if resp["data"] is not None:
                self._client_thread_signal.disconnect(_on_client_response)
                return {
                    "data": resp["data"],
                    "success": True,
                    "error": None
                }
            if time.time() - start_time > MCP_SERVER_RESPONSE_TIMEOUT:
                self._client_thread_signal.disconnect(_on_client_response)
                return {
                    "data": None,
                    "success": False,
                    "error": f"MCP server '{self.name}' response timeout"
                }
            time.sleep(0.1)

    def update_tool_list(self):
        if not self.is_connected():
            return
        self._set_status(MCPServerStatus.UpdatingToolList)
        response = self._send_mcp_request(MCPServerEventType.ListTools)
        if response["success"]:
            self._mcp_tools = response["data"]
        else:
            log.error(f"MCP server '{self.name}' failed to update tool list: {response['error']}")
        self._set_status(MCPServerStatus.UpdatedToolList)

    def call_tool(self, tool_name: str, tool_args: dict):
        if not self.is_connected():
            return f"MCP server '{self.name}' is not connected"

        response = self._send_mcp_request(MCPServerEventType.CallTool, {
            "tool_name": tool_name,
            "tool_args": tool_args
        })

        if response["success"]:
            return response["data"]
        else:
            log.error(f"MCP server '{self.name}' failed to call tool: {response['error']}")
            return response["error"]

    # TODO: optimize this
    def get_tools(self) -> list[Tool]:
        return [MCPTool(self, tool.name, tool.description, tool.inputSchema, auto_approve=(tool.name in self._auto_approve_tools)) for tool in self._mcp_tools]

    def get_tool(self, tool_name: str) -> Tool:
        for tool in self.get_tools():
            if tool.name == tool_name:
                return tool
        return None

class MCPChatParticipant(BaseChatParticipant):
    def __init__(self, id: str, name: str, servers: list[MCPServer], nbi_tools: list[str] = []):
        super().__init__()
        self._id = id
        self._name = name
        self._servers = servers
        self._tools_updated = False
        self._nbi_tools = nbi_tools

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name
    
    @property
    def icon_path(self) -> str:
        return MCP_ICON_URL

    @property
    def commands(self) -> list[ChatCommand]:
        return []

    @property
    def tools(self) -> list[Tool]:
        mcp_tools = []
        for mcp_server in self._servers:
            mcp_tools += mcp_server.get_tools()
        for nbi_tool in self._nbi_tools:
            tool = BaseChatParticipant.get_tool_by_name(nbi_tool)
            if tool is not None:
                mcp_tools.append(tool)
        return mcp_tools
    
    @property
    def servers(self) -> list[MCPServer]:
        return self._servers
    
    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        response.stream(ProgressData("Thinking..."))

        if request.command == "info":
            for server in self._servers:
                info_lines = []
                info_lines.append(f"- **{server.name}** server tools:")
                for tool in server.get_tools():
                    info_lines.append(f"  - **{tool.name}**: {tool.description}\n")
                response.stream(MarkdownData(f"\n".join(info_lines)))

            response.finish()
        else:
            await self.handle_chat_request_with_tools(request, response, options)

class MCPManager:
    def __init__(self, mcp_config: dict):
        self._websocket_connector: ThreadSafeWebSocketConnector = None
        self._mcp_participants: list[MCPChatParticipant] = []
        self._mcp_servers: list[MCPServer] = []
        self.update_mcp_servers(mcp_config)

    @property
    def websocket_connector(self) -> ThreadSafeWebSocketConnector:
        return self._websocket_connector
    
    @websocket_connector.setter
    def websocket_connector(self, _websocket_connector: ThreadSafeWebSocketConnector):
        self._websocket_connector = _websocket_connector

    def update_mcp_servers(self, mcp_config):
        # TODO: dont reuse servers, recreate with same config
        servers_config = mcp_config.get("mcpServers", {})
        participants_config = mcp_config.get("participants", {})
        self._mcp_participants = []
        for server in self._mcp_servers:
            server.disconnect()
        self._mcp_servers = []

        # parse MCP participants
        for participant_id in participants_config:
            participant_config = participants_config[participant_id]
            participant_name = participant_config.get("name", participant_id)
            if participant_name == "mcp":
                continue
            server_names = participant_config.get("servers", [])
            nbi_tools = participant_config.get("nbiTools", [])
            participant_servers = self.create_servers(server_names, servers_config)

            if len(participant_servers) > 0:
                self._mcp_participants.append(MCPChatParticipant(f"mcp-{participant_id}", participant_name, participant_servers, nbi_tools))
                self._mcp_servers += participant_servers

        enabled_server_names = [server_name for server_name in servers_config.keys() if servers_config.get(server_name, {}).get("disabled", False) == False]
        unused_server_names = set(enabled_server_names)

        for participant in self._mcp_participants:
            for server in participant.servers:
                if server.name in unused_server_names:
                    unused_server_names.remove(server.name)

        if len(unused_server_names) > 0:
            unused_servers = self.create_servers(unused_server_names, servers_config)
            mcp_participant_config = participants_config.get("mcp", {})
            nbi_tools = mcp_participant_config.get("nbiTools", [])
            self._mcp_participants.append(MCPChatParticipant("mcp", "MCP", unused_servers, nbi_tools))
            self._mcp_servers += unused_servers

        thread = threading.Thread(target=self.init_tool_lists, args=())
        thread.start()

    def create_servers(self, server_names: list[str], servers_config: dict):
        servers = []
        for server_name in server_names:
            server_config = servers_config.get(server_name, None)
            if server_config is None:
                log.error(f"Server '{server_name}' not found in MCP servers configuration")
                continue

            if server_config.get("disabled", False) == True:
                log.info(f"MCP Server '{server_name}' is disabled in MCP servers configuration. Skipping it.")
                continue

            mcp_server = self.create_mcp_server(server_name, server_config)
            if mcp_server is None:
                log.error(f"Failed to create MCP server '{server_name}'")
                continue

            servers.append(mcp_server)

        return servers
    
    def create_mcp_server(self, server_name: str, server_config: dict):
        auto_approve_tools = server_config.get("autoApprove", [])

        if "command" in server_config:
            command = server_config["command"]
            args = server_config.get("args", [])
            env = server_config.get("env", None)
            server_env = None
            if env is not None:
                server_env = mcp_get_default_environment()
                server_env.update(env)

            return MCPServerImpl(self, server_name, stdio_params=StdioServerParameters(
                command = command,
                args = args,
                env = server_env
                ), auto_approve_tools = auto_approve_tools)
        elif "url" in server_config:
            server_url = server_config["url"]
            headers = server_config.get("headers", None)

            return MCPServerImpl(
                self,
                server_name,
                streamable_http_params=StreamableHttpServerParameters(url=server_url, headers=headers),
                auto_approve_tools=auto_approve_tools
            )

        log.error(f"Invalid MCP server configuration for: {server_name}")
        return None

    def get_mcp_participants(self):
        return self._mcp_participants

    def init_tool_lists_async(self):
        for server in self._mcp_servers:
            try:
                server.update_tool_list()
            except Exception as e:
                log.error(f"Error initializing tool list for server {server.name}: {e}")
    
    def init_tool_lists(self):
        self.init_tool_lists_async()

    def get_mcp_servers(self):
        return self._mcp_servers
    
    def get_mcp_server(self, server_name: str):
        for server in self._mcp_servers:
            if server.name == server_name:
                return server
        return None

    def handle_stop_request(self):
        for server in self._mcp_servers:
            server.disconnect()

    def update_mcp_server_connections(self, disabled_mcp_servers: list[str]):
        for server in self._mcp_servers:
            if server.name in disabled_mcp_servers:
                server.disconnect()
            else:
                server.connect()

    def connect_mcp_server(self, server_name: str):
        server = self.get_mcp_server(server_name)
        if server is not None:
            server.connect()

    def disconnect_mcp_server(self, server_name: str):
        server = self.get_mcp_server(server_name)
        if server is not None:
            server.disconnect()
