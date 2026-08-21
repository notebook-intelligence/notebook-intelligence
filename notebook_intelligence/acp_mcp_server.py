# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""A small stdio MCP server that exposes NBI tools to an ACP agent.

ACP's ``session/new`` takes stdio/socket MCP servers, not the in-process SDK
MCP server Claude mode uses (``create_sdk_mcp_server``), so NBI's tools must run
as a real subprocess for the agent-mode framework (issue #378, Phase 1). This
module is that subprocess: newline-delimited JSON-RPC over stdio, launched as
``python -m notebook_intelligence.acp_mcp_server`` with its cwd set to the
JupyterLab working directory by the participant.

Phase 1 ships one safe, dependency-free tool (``nbi_workspace_root``) so the
end-to-end MCP path is exercised; richer Jupyter UI tools that need the live
websocket are a follow-up.
"""

import json
import os
import sys

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "nbi_workspace_root",
        "description": (
            "Return the absolute path of the JupyterLab workspace root that "
            "Notebook Intelligence is serving. Use it to resolve relative paths."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(mid, result):
    _send({"jsonrpc": "2.0", "id": mid, "result": result})


def _error(mid, code, message):
    _send({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def _call_tool(name, arguments):
    if name == "nbi_workspace_root":
        return {"content": [{"type": "text", "text": os.getcwd()}]}
    raise KeyError(name)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            params = msg.get("params") or {}
            _result(mid, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "nbi", "version": "1.0.0"},
            })
        elif method == "notifications/initialized":
            continue  # notification: no response
        elif method == "tools/list":
            _result(mid, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params") or {}
            try:
                _result(mid, _call_tool(params.get("name"), params.get("arguments") or {}))
            except KeyError as e:
                _error(mid, -32602, f"unknown tool: {e}")
            except Exception as e:  # surface tool failures as MCP errors
                _error(mid, -32603, f"tool error: {e}")
        elif mid is not None:
            _error(mid, -32601, f"method not found: {method}")


if __name__ == "__main__":
    main()
