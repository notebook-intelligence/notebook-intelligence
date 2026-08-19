# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Thin client adapter backed by the official ``mcp`` Python SDK.

Exposes the small surface NBI uses (``Client``, ``StdioTransport``,
``StreamableHttpTransport``) so the rest of ``mcp_manager.py`` reads
identically to its prior ``fastmcp``-backed implementation. The
underlying protocol types (``Tool``, ``Prompt``, ``Implementation``,
``TextContent``, ``ImageContent``, etc.) are shared with the SDK, so
no shape translation is needed at the consumer.

Why this exists rather than just using ``fastmcp``: every ``fastmcp``
release that NBI's pinned floor (``>=2.11.2``) admits requires
``python-dotenv>=1.1.0``, while every ``litellm`` release that picks
up the recent CVE fixes (1.83.1+) hard-pins ``python-dotenv==1.0.1``.
The two are mutually unsatisfiable; ``pip install -e .`` on a fresh
Python 3.14 against ``main`` reports ``ResolutionImpossible``. The
official ``mcp`` SDK is already in NBI's dep tree (via
``claude-agent-sdk``) and lists ``python-dotenv`` only as an optional
``cli`` extra, so swapping ``fastmcp`` → ``mcp`` here unblocks the
install without losing any CVE fix.

Tracked upstream at BerriAI/litellm#25231 (relax the python-dotenv
pin). Once that lands and propagates through ``fastmcp``-shaped
clients, this shim can be reverted to a direct ``fastmcp`` import.
"""

import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

try:
    # mcp 2.0 renamed this helper and dropped the session-id third yield.
    from mcp.client.streamable_http import streamable_http_client as _streamable_http_client

    _MCP_STREAMABLE_HTTP_V2 = True
except ImportError:  # mcp < 2.0
    from mcp.client.streamable_http import streamablehttp_client as _streamable_http_client

    _MCP_STREAMABLE_HTTP_V2 = False


@dataclass
class StdioTransport:
    """Constructor-shape parity with ``fastmcp.client.StdioTransport``."""

    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)


@dataclass
class StreamableHttpTransport:
    """Constructor-shape parity with ``fastmcp.client.StreamableHttpTransport``."""

    url: str
    headers: Dict[str, str] = field(default_factory=dict)


async def _open_streamable_http(stack: AsyncExitStack, url: str, headers: Optional[Dict[str, str]]):
    """Open the streamable-http transport across mcp 1.x and 2.0 APIs.

    mcp 1.x: ``streamablehttp_client(url, headers=...)`` yields
    ``(read, write, get_session_id)``. mcp 2.0: ``streamable_http_client``
    yields ``(read, write)`` and takes headers via a caller-owned httpx
    client instead of a ``headers=`` kwarg.
    """
    if _MCP_STREAMABLE_HTTP_V2:
        kwargs: Dict[str, Any] = {}
        if headers:
            from mcp.shared._httpx_utils import create_mcp_http_client

            http_client = create_mcp_http_client(headers=headers)
            await stack.enter_async_context(http_client)
            kwargs["http_client"] = http_client
        streams = await stack.enter_async_context(_streamable_http_client(url, **kwargs))
    else:
        streams = await stack.enter_async_context(
            _streamable_http_client(url, headers=headers)
        )
    return streams[0], streams[1]


class Client:
    """Async context manager that opens an MCP session over the given transport.

    Mirrors the subset of ``fastmcp.Client`` that NBI's MCP manager uses:
    ``__aenter__``/``__aexit__``, ``ping``, ``list_tools``, ``call_tool``,
    ``list_prompts``, ``get_prompt``. Return values are the underlying
    SDK objects (Pydantic models from ``mcp.types``) — the same shapes
    ``fastmcp`` returned, since fastmcp itself is a thin layer over mcp.
    """

    def __init__(
        self,
        transport,
        client_info: Optional[Implementation] = None,
    ):
        self._transport = transport
        self._client_info = client_info
        self._stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None
        self._server_capabilities = None

    async def __aenter__(self) -> "Client":
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            if isinstance(self._transport, StdioTransport):
                params = StdioServerParameters(
                    command=self._transport.command,
                    args=list(self._transport.args),
                    env=dict(self._transport.env) if self._transport.env else None,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            elif isinstance(self._transport, StreamableHttpTransport):
                read, write = await _open_streamable_http(
                    stack,
                    self._transport.url,
                    dict(self._transport.headers) if self._transport.headers else None,
                )
            else:
                raise TypeError(
                    f"Unsupported MCP transport: {type(self._transport).__name__}"
                )
            session = await stack.enter_async_context(
                ClientSession(read, write, client_info=self._client_info)
            )
            initialize_result = await session.initialize()
        except BaseException:
            # The exit stack owns every context we entered above; let it
            # tear them down in reverse order before re-raising so we
            # don't leak the stdio subprocess or the HTTP connection on
            # a partially-constructed session. ``sys.exc_info()`` inside
            # an except block returns the in-flight exception triple,
            # which AsyncExitStack.__aexit__ uses to invoke each entered
            # context's __aexit__ as if the unwind were native.
            await stack.__aexit__(*sys.exc_info())
            raise
        self._stack = stack
        self._session = session
        self._server_capabilities = initialize_result.capabilities
        return self

    async def __aexit__(self, exc_type, exc, tb):
        stack = self._stack
        self._stack = None
        self._session = None
        self._server_capabilities = None
        if stack is None:
            return False
        return await stack.__aexit__(exc_type, exc, tb)

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(
                "MCP client is not connected. Use `async with client:` first."
            )
        return self._session

    async def ping(self) -> Any:
        return await self._require_session().send_ping()

    @property
    def supports_tools(self) -> bool:
        """Whether the server advertised the MCP tools capability."""
        return (
            self._server_capabilities is not None
            and self._server_capabilities.tools is not None
        )

    @property
    def supports_prompts(self) -> bool:
        """Whether the server advertised the MCP prompts capability."""
        return (
            self._server_capabilities is not None
            and self._server_capabilities.prompts is not None
        )

    async def list_tools(self):
        """Return a list of ``mcp.types.Tool``, matching fastmcp's shape."""
        result = await self._require_session().list_tools()
        return result.tools

    async def call_tool(self, name: str, arguments: dict):
        """Return ``mcp.types.CallToolResult`` (has ``.content``)."""
        return await self._require_session().call_tool(name, arguments)

    async def list_prompts(self):
        """Return a list of ``mcp.types.Prompt``."""
        result = await self._require_session().list_prompts()
        return result.prompts

    async def get_prompt(self, name: str, arguments: dict):
        """Return ``mcp.types.GetPromptResult`` (has ``.messages``)."""
        return await self._require_session().get_prompt(name, arguments)


def tool_input_schema(tool: Any) -> dict:
    """JSON Schema for a tool's arguments.

    mcp 1.x exposed this as ``tool.inputSchema`` (wire-name attribute).
    mcp 2.0's pydantic model uses the Python field ``input_schema`` with
    alias ``inputSchema``. NBI's MCP manager historically read the
    camelCase attribute.
    """
    schema = getattr(tool, "inputSchema", None)
    if schema is None:
        schema = getattr(tool, "input_schema", None)
    return schema or {}
