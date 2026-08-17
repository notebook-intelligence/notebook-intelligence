"""Generic stdio MCP proxy for a Jupyter Server tool backend.

Bridges a stdio MCP client (e.g. Claude Code launched under a managed/enterprise
MCP config that forbids dynamically-configured servers) to an authenticated HTTP
tool endpoint served by the running Jupyter Server. It discovers the server URL
and auth token at runtime, fetches the tool manifest, and forwards each tool call.

Nothing here is vendor-specific: configuration comes from standard Jupyter env
vars (or an explicit override), and the backend speaks a minimal JSON protocol:
    GET  <endpoint>                 -> {"tools": [{name, description, inputSchema}]}
    POST <endpoint> {"name","arguments"} -> {"content": [...], "is_error"?: bool}

Launch from an MCP config entry:
    {"command": "python", "args": ["-m", "notebook_intelligence.mcp_ui_proxy"]}

Environment (all optional; discovery falls back to the Jupyter runtime file):
    NBI_UI_TOOLS_URL        full endpoint URL (overrides discovery)
    NBI_UI_TOOLS_TOKEN      auth token (else JUPYTER_TOKEN, else the discovered token)
    JUPYTER_SERVER_URL / JUPYTER_TOKEN   standard Jupyter server coordinates
    NBI_UI_TOOLS_HTTP_TIMEOUT per-request timeout in seconds (default: none)
    NBI_UI_TOOLS_SERVER_NAME  MCP handshake name (default "nbi"; cosmetic only)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

import mcp.types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server


# server_name is only the MCP handshake identity; the CLI-visible tool prefix
# (mcp__<key>__*) comes from the client config-entry KEY, not from this value.
SERVER_NAME = os.environ.get("NBI_UI_TOOLS_SERVER_NAME", "nbi")
# Must match the route registered in extension.py (NotebookIntelligence._setup_handlers).
ENDPOINT_PATH = "notebook-intelligence/ui-tools"
# No client-side timeout by default: the backend bounds the call (NBI caps it at
# the agent response window), so long-running cells/commands aren't cut off.
_http_timeout = os.environ.get("NBI_UI_TOOLS_HTTP_TIMEOUT")
HTTP_TIMEOUT = float(_http_timeout) if _http_timeout else None


# Reach the (loopback) backend directly, never via an ambient HTTP(S)_PROXY: keeps
# the auth token on the local connection and works regardless of NO_PROXY.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

server = Server(SERVER_NAME)
_endpoint = ""
_token = ""


def _log(msg: str) -> None:
    # stdout is the JSON-RPC channel; diagnostics must go to stderr only.
    print(f"[mcp_ui_proxy] {msg}", file=sys.stderr, flush=True)


def _resolve_backend() -> tuple[str, str]:
    """Return (endpoint_url, token) from env, then the Jupyter runtime file."""
    endpoint = os.environ.get("NBI_UI_TOOLS_URL")
    token = os.environ.get("NBI_UI_TOOLS_TOKEN") or os.environ.get("JUPYTER_TOKEN")
    base = os.environ.get("JUPYTER_SERVER_URL")
    # Discover from the Jupyter runtime file when we still lack a base URL (to build
    # the endpoint) or a token (empty string counts as unset).
    if (not endpoint and not base) or not token:
        try:
            from jupyter_server.serverapp import list_running_servers
            servers = list(list_running_servers())
        except Exception as exc:
            servers = []
            _log(f"Could not enumerate running Jupyter servers: {exc}")
        chosen = None
        for srv in servers:
            if base and srv.get("url", "").rstrip("/") == base.rstrip("/"):
                chosen = srv
                break
        if chosen is None and servers:
            if len(servers) > 1:
                _log(
                    "Multiple Jupyter servers found and JUPYTER_SERVER_URL is unset; "
                    f"using {servers[0].get('url')}. Set NBI_UI_TOOLS_URL or "
                    "JUPYTER_SERVER_URL to target a specific server."
                )
            chosen = servers[0]
        if chosen is not None:
            base = base or chosen.get("url")
            if not token:
                token = chosen.get("token")

    if not endpoint:
        if not base:
            raise RuntimeError(
                "Cannot locate the Jupyter Server. Set NBI_UI_TOOLS_URL (and optionally "
                "NBI_UI_TOOLS_TOKEN), or launch inside a running Jupyter Server."
            )
        endpoint = base.rstrip("/") + "/" + ENDPOINT_PATH
    return endpoint, token or ""


def _request(method: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(_endpoint, data=body, method=method)
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if _token:
        req.add_header("Authorization", f"token {_token}")
    with _opener.open(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read().decode() or "{}")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    try:
        data = await asyncio.to_thread(_request, "GET")
    except Exception as exc:
        _log(f"failed to fetch tool manifest from {_endpoint}: {exc}")
        raise
    tools = [
        types.Tool(
            name=t["name"],
            description=t.get("description", ""),
            inputSchema=t.get("inputSchema") or {"type": "object", "properties": {}},
        )
        for t in data.get("tools", [])
    ]
    _log(f"advertising {len(tools)} tools")
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
    _log(f"tool call: {name}")
    try:
        result = await asyncio.to_thread(
            _request, "POST", {"name": name, "arguments": arguments or {}}
        )
    except Exception as exc:
        _log(f"tool call '{name}' failed: {exc}")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Tool bridge error: {exc}")],
            isError=True,
        )
    content = [
        types.TextContent(type="text", text=str(c.get("text", "")))
        for c in (result.get("content") or [])
        if isinstance(c, dict) and c.get("type") == "text"
    ] or [types.TextContent(type="text", text="")]
    return types.CallToolResult(content=content, isError=bool(result.get("is_error")))


async def main() -> None:
    global _endpoint, _token
    _endpoint, _token = _resolve_backend()
    _log(f"bridging to {_endpoint} ({'authenticated' if _token else 'no token'})")
    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name=SERVER_NAME,
                server_version="1.0.0",
                capabilities=server.get_capabilities(NotificationOptions(), {}),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
