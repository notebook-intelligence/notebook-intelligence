# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""claude_agent_sdk.create_sdk_mcp_server is incompatible with mcp 2.0.

The SDK still registers tools via Server.list_tools(), which mcp 2.0
removed. NBI must still produce a server the SDK query router can call,
or JupyterLab startup fails and the sidebar never appears.
"""

import asyncio

from claude_agent_sdk import tool
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

from notebook_intelligence.claude import (
    create_compatible_sdk_mcp_server,
    tool_text_response,
)


@tool("echo", "Echo text back.", {"text": str})
async def _echo(args):
    return tool_text_response(f"You said: {args['text']}")


def test_compatible_sdk_mcp_server_lists_and_calls_tools():
    config = create_compatible_sdk_mcp_server(
        name="nbi", version="1.0.0", tools=[_echo]
    )
    assert config["type"] == "sdk"
    assert config["name"] == "nbi"
    server = config["instance"]
    assert server.name == "nbi"
    assert server.version == "1.0.0"

    list_handler = server.request_handlers[ListToolsRequest]
    listed = asyncio.run(list_handler(None))
    assert len(listed.root.tools) == 1
    echo_tool = listed.root.tools[0]
    assert echo_tool.name == "echo"
    assert echo_tool.inputSchema["properties"]["text"]["type"] == "string"

    call_handler = server.request_handlers[CallToolRequest]
    request = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(name="echo", arguments={"text": "hi"}),
    )
    result = asyncio.run(call_handler(request))
    assert result.root.content[0].text == "You said: hi"
    assert not result.root.is_error
