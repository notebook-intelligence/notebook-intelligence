# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""End-to-end smoke test for the fastmcp -> mcp client adapter.

Spins up a minimal MCP server over stdio in a subprocess, drives the
shim's full surface against it (ping, list_tools, call_tool,
list_prompts, get_prompt), and verifies the return shapes match what
NBI's ``mcp_manager.py`` expects. Without this, a future refactor that
silently drops the ``await session.initialize()`` call or returns a
Pydantic envelope where a list was expected would still pass NBI's
existing test suite — none of which exercise the MCP code path.

Why end-to-end rather than mocked: every claim this shim makes is
about wire compatibility with the underlying ``mcp`` SDK. Mocking the
SDK would test the shim's call shape but not the contract it relies
on. The minimal stdio server is fast (sub-second), uses no network,
and runs in CI without extra setup beyond the existing ``mcp`` dep.
"""

import asyncio
import os
import sys
import tempfile
import textwrap
import time

import psutil
import pytest

from mcp.types import Implementation

from notebook_intelligence.mcp_client import (
    Client,
    StdioTransport,
    StreamableHttpTransport,
)


_SERVER_SCRIPT = textwrap.dedent(
    """
    import asyncio
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool,
        TextContent,
        Prompt,
        PromptArgument,
        PromptMessage,
        GetPromptResult,
    )

    server = Server("nbi-shim-test")

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="echo",
                description="Echo input back as text.",
                inputSchema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
        ]

    @server.call_tool()
    async def call_tool(name, args):
        return [TextContent(type="text", text=f"You said: {args['text']}")]

    @server.list_prompts()
    async def list_prompts():
        return [
            Prompt(
                name="greet",
                title="Greet",
                description="Greet someone by name.",
                arguments=[
                    PromptArgument(
                        name="who",
                        description="Person to greet.",
                        required=True,
                    )
                ],
            )
        ]

    @server.get_prompt()
    async def get_prompt(name, args):
        return GetPromptResult(
            description="Greeting.",
            messages=[
                PromptMessage(
                    role="user",
                    content=TextContent(type="text", text=f"Hello, {args['who']}!"),
                )
            ],
        )

    async def main():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(main())
    """
)


@pytest.fixture(scope="module")
def server_script_path():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False
    ) as f:
        f.write(_SERVER_SCRIPT)
        path = f.name
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _stdio_transport(server_script_path: str) -> StdioTransport:
    return StdioTransport(
        command=sys.executable,
        args=[server_script_path],
        # The MCP server inherits the current process env so it can find
        # site-packages on PATH; ``stdio_client`` defaults env=None to
        # ``inherit`` but we pass it explicitly so the adapter's dict
        # forwarding is also exercised.
        env=dict(os.environ),
    )


def _client_info() -> Implementation:
    return Implementation(
        name="nbi-shim-test", title="NBI shim test", version="0.0.0"
    )


def test_transport_dataclasses_match_fastmcp_shape():
    # The shim's transports are dataclasses with the same constructor
    # signature fastmcp.client.StdioTransport / StreamableHttpTransport
    # accepted, so NBI's existing call sites (e.g.
    # mcp_manager._create_client) work without translation. Pin the
    # field names so a future rename can't silently break wire format.
    stdio = StdioTransport(command="cmd", args=["a"], env={"K": "V"})
    assert stdio.command == "cmd"
    assert stdio.args == ["a"]
    assert stdio.env == {"K": "V"}

    http = StreamableHttpTransport(url="https://x", headers={"Auth": "Bearer x"})
    assert http.url == "https://x"
    assert http.headers == {"Auth": "Bearer x"}


def test_session_required_before_calls():
    # Using any Client method before ``async with client:`` is a
    # programmer error, not user input. Raise loudly so a missing
    # await on ``__aenter__`` surfaces at the call site rather than
    # silently producing AttributeError on a None session.
    client = Client(transport=StdioTransport(command="x"))

    async def call_without_context():
        await client.list_tools()

    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(call_without_context())


def test_unsupported_transport_type_raises():
    client = Client(transport="not a transport")

    async def go():
        async with client:
            pass

    with pytest.raises(TypeError, match="Unsupported MCP transport"):
        asyncio.run(go())


def test_end_to_end_against_real_stdio_server(server_script_path):
    """The load-bearing test. Drives every shim method against a real
    MCP server over stdio and asserts the return shapes are exactly
    what NBI's mcp_manager.py expects to read off them.
    """

    async def go():
        transport = _stdio_transport(server_script_path)
        async with Client(transport, client_info=_client_info()) as client:
            await client.ping()
            assert client.supports_tools is True
            assert client.supports_prompts is True

            tools = await client.list_tools()
            # mcp_manager.get_tools() reads tool.name / tool.description
            # / tool.inputSchema. Pin them.
            assert len(tools) == 1
            assert tools[0].name == "echo"
            assert tools[0].description == "Echo input back as text."
            assert tools[0].inputSchema["properties"]["text"]["type"] == "string"

            # mcp_manager.handle_tool_call inspects result.content and
            # iterates TextContent / ImageContent entries.
            tool_result = await client.call_tool("echo", {"text": "hi"})
            assert hasattr(tool_result, "content")
            assert len(tool_result.content) == 1
            assert tool_result.content[0].type == "text"
            assert tool_result.content[0].text == "You said: hi"

            # mcp_manager.get_prompts() reads prompt.name / prompt.title /
            # prompt.description / prompt.arguments[i].{name,description,required}.
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            assert prompts[0].name == "greet"
            assert prompts[0].title == "Greet"
            assert prompts[0].description == "Greet someone by name."
            assert len(prompts[0].arguments) == 1
            assert prompts[0].arguments[0].name == "who"
            assert prompts[0].arguments[0].required is True

            # mcp_manager.handle_get_prompt reads message.content.text /
            # .type. Pin the GetPromptResult.messages shape.
            prompt_result = await client.get_prompt("greet", {"who": "world"})
            assert hasattr(prompt_result, "messages")
            assert len(prompt_result.messages) == 1
            message = prompt_result.messages[0]
            assert message.role == "user"
            assert message.content.type == "text"
            assert message.content.text == "Hello, world!"

    asyncio.run(go())


def test_context_cleanup_on_initialize_failure(tmp_path):
    """If the server fails to initialize, ``__aenter__`` must unwind
    every context it entered (the stdio subprocess) before re-raising,
    so a partial connect doesn't leak processes. Drives an
    intentionally-broken server script and verifies via psutil that no
    Python child process survives the failure. Without the psutil
    check, the shim could silently regress to leaking subprocesses and
    the test would still pass on the ``pytest.raises`` alone.
    """
    bad_script = tmp_path / "bad_server.py"
    bad_script.write_text(
        textwrap.dedent(
            """
            import sys
            # Exit immediately so the MCP handshake fails. The shim's
            # __aenter__ must still tear down the stdio subprocess
            # cleanly rather than leaving zombies.
            sys.exit(1)
            """
        )
    )

    parent = psutil.Process(os.getpid())

    def _shim_children() -> list[psutil.Process]:
        """Return live children whose command line invoked our bad
        server. Filters out unrelated subprocesses pytest itself may
        spawn (xdist workers, coverage helpers) so the assertion
        isolates the shim's own footprint.
        """
        out = []
        for child in parent.children(recursive=True):
            try:
                if str(bad_script) in child.cmdline():
                    out.append(child)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return out

    async def go():
        transport = StdioTransport(
            command=sys.executable,
            args=[str(bad_script)],
            env=dict(os.environ),
        )
        client = Client(transport, client_info=_client_info())
        with pytest.raises(Exception):
            async with client:
                pass  # pragma: no cover

    assert _shim_children() == []
    asyncio.run(go())

    # Briefly poll for the subprocess to be reaped. The shim's
    # AsyncExitStack unwind happens synchronously inside __aenter__'s
    # except branch, but the OS-level wait for SIGCHLD may lag a tick.
    # A 2-second budget is generous; in practice this resolves under
    # 50ms locally.
    deadline = time.monotonic() + 2.0
    while _shim_children() and time.monotonic() < deadline:
        time.sleep(0.05)
    leaked = _shim_children()
    assert not leaked, (
        f"shim leaked {len(leaked)} subprocess(es) on init failure: "
        f"{[p.pid for p in leaked]}"
    )
