# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""ACP agent mode over the Agent Client Protocol (issue #378, Phase 1).

A second, off-by-default agent mode that drives the chat panel like Claude mode
for the core loop (streaming, tool-call cards with diffs, per-tool approval),
backed by an ACP agent adapter instead of the Claude SDK. Agent types are
described by ``ACP_AGENTS``; Codex (via ``codex-acp``) is the first entry and
further agents (e.g. Pi, OpenCode) slot in as registry additions once their
adapters are validated.

Design notes (validated by the Phase 0 spike under ``spikes/acp-codex/``):

- The ACP client runs on its own worker thread with an asyncio loop, mirroring
  ``ClaudeCodeClient``. Per-request ``query`` blocks on that loop via
  ``run_coroutine_threadsafe`` while tool-call cards stream and ``request_permission``
  is answered cross-thread through ``wait_for_chat_user_input`` (the same poll a
  signal pattern Claude uses).
- ``agent-client-protocol`` is imported here, and this module is only imported
  when ACP mode is enabled, so the dependency loads lazily (matching #370).
- Phase 1 is deliberately allowed to duplicate a little of Claude's card/diff
  wiring; Phase 2 extracts the shared layer.
"""

import asyncio
import concurrent.futures
import difflib
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Any, Optional

import acp
from acp import schema

from notebook_intelligence.api import (
    ChatCommand,
    ChatRequest,
    ChatResponse,
    Host,
    MarkdownData,
    MarkdownPartData,
    ProgressData,
    ToolCallData,
    ConfirmationData,
)
from notebook_intelligence.acp_registry import (
    AcpAgentSpec,
    codex_approval_args,
    codex_model_args,
    resolve_acp_agent,
    resolve_acp_agent_command,
)
from notebook_intelligence.base_chat_participant import BaseChatParticipant
from notebook_intelligence.claude_sessions import CONTROL_SLASH_COMMANDS
from notebook_intelligence.util import ThreadSafeWebSocketConnector, get_jupyter_root_dir

log = logging.getLogger(__name__)

ACP_AGENT_CHAT_PARTICIPANT_ID = "acp-agent"
_START_TIMEOUT = 60          # seconds to bring up codex-acp + a session
_RESPONSE_TIMEOUT = 30 * 60  # a turn can be long; cap so the UI doesn't hang forever
_POLL = 0.2

_MAX_DIFF_LINES = 60


def _diff_lines(old: str, new: str, max_lines: int = _MAX_DIFF_LINES) -> tuple[list[dict], bool]:
    """Line-level diff as typed lines for a tool-call card.

    Duplicated (minimally) from claude.py to keep this module independent of
    the Claude SDK import; the shared version is a Phase 2 extraction.
    """
    lines: list[dict] = []
    for raw in difflib.unified_diff(
        (old or "").splitlines(), (new or "").splitlines(), lineterm="", n=1
    ):
        if raw.startswith(("---", "+++", "@@")):
            continue
        if raw.startswith("+"):
            lines.append({"type": "add", "content": raw[1:]})
        elif raw.startswith("-"):
            lines.append({"type": "remove", "content": raw[1:]})
        else:
            lines.append({"type": "context", "content": raw[1:] if raw[:1] == " " else raw})
    truncated = len(lines) > max(0, max_lines)
    return lines[: max(0, max_lines)], truncated


# ACP tool kinds are finer than NBI's card-icon categories; collapse them.
_ACP_KIND_TO_NBI = {
    "read": "read", "search": "read", "fetch": "read",
    "edit": "edit", "delete": "edit", "move": "edit",
    "execute": "execute",
}


def _nbi_kind(acp_kind: Optional[str]) -> str:
    return _ACP_KIND_TO_NBI.get(acp_kind or "", "other")


def _nbi_status(acp_status: Optional[str]) -> str:
    if acp_status in ("completed", "failed"):
        return acp_status
    return "in_progress"  # pending / in_progress / None


class _NbiAcpClient(acp.Client):
    """The editor side of ACP. Maps agent events onto NBI's chat surfaces."""

    def __init__(self, owner: "AcpAgentClient"):
        self._owner = owner
        self._tool_state: dict[str, dict] = {}

    @property
    def _response(self) -> Optional[ChatResponse]:
        return self._owner.current_response

    async def session_update(self, session_id, update, **kw):
        resp = self._response
        if resp is None:
            return
        su = getattr(update, "session_update", None)
        if su in ("tool_call", "tool_call_update"):
            self._emit_tool_call(resp, update)
        elif su == "agent_message_chunk":
            text = _block_text(getattr(update, "content", None))
            if text:
                # ACP streams token-sized deltas. MarkdownPartData because the
                # frontend concatenates consecutive parts into one block;
                # MarkdownData would render every delta as its own paragraph.
                resp.stream(MarkdownPartData(content=text))
        elif su == "agent_thought_chunk":
            text = _block_text(getattr(update, "content", None))
            if text:
                resp.stream(MarkdownPartData(reasoning_content=text))
        elif su == "available_commands_update":
            self._owner.available_commands = [
                getattr(c, "name", "") for c in (update.available_commands or [])
            ]

    def _emit_tool_call(self, resp: ChatResponse, update):
        tid = update.tool_call_id
        state = self._tool_state.setdefault(
            tid, {"kind": "other", "title": "", "status": "in_progress", "diffs": []}
        )
        if getattr(update, "kind", None) is not None:
            state["kind"] = _nbi_kind(update.kind)
        if getattr(update, "title", None):
            state["title"] = update.title
        if getattr(update, "status", None) is not None:
            state["status"] = _nbi_status(update.status)
        diffs = _diffs_from_content(getattr(update, "content", None))
        if diffs:
            state["diffs"] = diffs
        resp.stream(ToolCallData(
            id=tid,
            title=state["title"] or "Tool call",
            kind=state["kind"],
            status=state["status"],
            diffs=state["diffs"] or None,
        ))

    async def request_permission(self, options, session_id, tool_call, **kw):
        resp = self._response
        allow = next((o for o in options if o.kind == "allow_once"), None) \
            or next((o for o in options if str(o.kind).startswith("allow")), None)
        if resp is None or allow is None:
            # No UI to ask, or no allow option offered: fail closed (reject).
            return acp.RequestPermissionResponse(
                outcome=schema.DeniedOutcome(outcome="cancelled")
            )
        callback_id = f"acp-perm-{tool_call.tool_call_id}"
        title = getattr(tool_call, "title", None) or "Run this tool?"
        agent_label = self._owner.agent_spec.label
        resp.stream(ConfirmationData(
            title=f"{agent_label} tool call",
            message=(
                f"Approve: {title}?\n\n"
                f"{agent_label} decides which tools to ask about, so some actions may run "
                "without a prompt."
            ),
            confirmArgs={"id": resp.message_id, "data": {
                "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": resp.message_id, "data": {
                "callback_id": callback_id, "data": {"confirmed": False}}},
            confirmLabel="Approve",
            cancelLabel="Reject",
        ))
        user_input = await ChatResponse.wait_for_chat_user_input(resp, callback_id)
        if user_input.get("confirmed"):
            return acp.RequestPermissionResponse(
                outcome=schema.AllowedOutcome(outcome="selected", option_id=allow.option_id)
            )
        reject = next((o for o in options if o.kind == "reject_once"), None) \
            or next((o for o in options if str(o.kind).startswith("reject")), None)
        if reject is not None:
            return acp.RequestPermissionResponse(
                outcome=schema.AllowedOutcome(outcome="selected", option_id=reject.option_id)
            )
        return acp.RequestPermissionResponse(outcome=schema.DeniedOutcome(outcome="cancelled"))

    # fs/*: implemented so an agent that delegates file ops (e.g. claude-acp)
    # routes through NBI. codex-acp self-applies, so these may not fire for it.
    async def read_text_file(self, path, session_id, limit=None, line=None, **kw):
        try:
            with open(path, encoding="utf-8") as f:
                return acp.ReadTextFileResponse(content=f.read())
        except Exception as e:
            raise acp.RequestError.internal_error(str(e))

    async def write_text_file(self, content, path, session_id, **kw):
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return acp.WriteTextFileResponse()
        except Exception as e:
            raise acp.RequestError.internal_error(str(e))

    # terminal/*: not emulated in Phase 1 (Codex runs shell internally).
    async def create_terminal(self, command, session_id, **kw):
        raise acp.RequestError.method_not_found("terminal not supported")

    async def terminal_output(self, session_id, terminal_id, **kw):
        raise acp.RequestError.method_not_found("terminal not supported")

    async def wait_for_terminal_exit(self, session_id, terminal_id, **kw):
        raise acp.RequestError.method_not_found("terminal not supported")

    async def kill_terminal(self, session_id, terminal_id, **kw):
        return None

    async def release_terminal(self, session_id, terminal_id, **kw):
        return None


def _block_text(block) -> str:
    if block is None:
        return ""
    return getattr(block, "text", "") or ""


def _epoch_from_iso(value) -> float:
    """ISO-8601 timestamp (as ACP's ``updatedAt``) to epoch seconds, else 0."""
    if not value:
        return 0
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def _strip_context_preamble(title: str) -> str:
    """Drop NBI's leading context lines from an agent-stored session title.

    The agent titles a session with its first prompt, which NBI prefixes with
    context lines (current-directory pointer, attachments). The preview should
    show the user's actual first question, like the Claude picker does.

    Handles both shapes: newline-separated lines, and the joined form codex
    stores (newlines collapsed to spaces, title truncated), where the
    directory pointer is matched structurally by its quoted segments.
    """
    from notebook_intelligence.claude_sessions import NBI_CONTEXT_PREFIX
    lines = [line for line in title.splitlines() if line.strip()]
    while lines and (
        lines[0].startswith(NBI_CONTEXT_PREFIX)
        or lines[0].startswith("The user attached ")
    ):
        lines.pop(0)
    stripped = " ".join(lines)
    if stripped and stripped != title.strip():
        return stripped
    # Joined form: peel the directory pointer off the front by shape.
    joined_preamble = re.compile(
        re.escape(NBI_CONTEXT_PREFIX)
        + r" '[^']*'( and current file is: '[^']*')?\s*"
    )
    remainder = joined_preamble.sub("", title, count=1)
    return remainder.strip() or title


def _diffs_from_content(content) -> list[dict]:
    out: list[dict] = []
    remaining = _MAX_DIFF_LINES
    for c in content or []:
        if getattr(c, "type", None) != "diff":
            continue
        if remaining <= 0:
            break
        lines, truncated = _diff_lines(
            getattr(c, "old_text", "") or "", getattr(c, "new_text", "") or "",
            max_lines=remaining,
        )
        if lines:
            out.append({"path": getattr(c, "path", ""), "lines": lines, "truncated": truncated})
            remaining -= len(lines)
    return out


class AcpAgentClient:
    """Persistent ACP client: one agent-adapter subprocess + session on a
    worker thread, prompted once per chat request."""

    def __init__(self, host: Host):
        self._host = host
        self.websocket_connector: Optional[ThreadSafeWebSocketConnector] = (
            host.websocket_connector
        )
        self.current_response: Optional[ChatResponse] = None
        self.available_commands: list[str] = []
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._conn = None
        self._session_id: Optional[str] = None
        self._proc = None
        self._stderr_task = None
        self._started = threading.Event()
        self._start_error: Optional[str] = None
        self._shutdown = None  # asyncio.Event, created on the loop
        self._shutting_down = False
        self._client: Optional[_NbiAcpClient] = None
        self._agent_capabilities = None
        self._lock = threading.Lock()
        # Serializes turns: the ACP session runs one prompt at a time, and
        # current_response is shared, so a second concurrent turn must not
        # interleave with the first.
        self._turn_lock = threading.Lock()

    def _mcp_servers(self) -> list:
        """The NBI MCP server config passed to every session create/load."""
        return [
            schema.McpServerStdio(
                name="nbi", command=sys.executable,
                args=["-m", "notebook_intelligence.acp_mcp_server"], env=[],
            )
        ]

    @property
    def acp_settings(self) -> dict:
        return self._host.nbi_config.acp_settings

    @property
    def agent_spec(self) -> AcpAgentSpec:
        return resolve_acp_agent(self.acp_settings.get("agent"))

    def _ensure_started(self) -> bool:
        with self._lock:
            if (
                self._thread is not None
                and self._thread.is_alive()
                and not self._shutting_down
            ):
                return self._start_error is None
            # A thread that is shutting down (or already dead) must not be
            # reused: its loop is closing, so scheduling a prompt on it would
            # hang. Wait for it to exit, then start a fresh one.
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=_START_TIMEOUT)
            self._started.clear()
            self._start_error = None
            self._shutting_down = False
            self._thread = threading.Thread(
                target=self._thread_main, name="nbi-acp-agent", daemon=True
            )
            self._thread.start()
        if not self._started.wait(timeout=_START_TIMEOUT):
            self._start_error = self._start_error or "ACP agent did not start in time"
            return False
        return self._start_error is None

    def _thread_main(self):
        try:
            asyncio.run(self._serve())
        except Exception as e:
            self._start_error = f"ACP agent failed to start: {e}"
            log.error(self._start_error, exc_info=True)
            self._started.set()

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._shutdown = asyncio.Event()
        workdir = get_jupyter_root_dir()
        spec = self.agent_spec
        env = self._child_env(spec)
        cmd = list(resolve_acp_agent_command(spec))
        if spec.id == "codex":
            cmd += codex_approval_args(
                bool(self.acp_settings.get("full_access", False))
            )
            # acp_settings already folds in the OPENAI_BASE_URL /
            # NBI_ACP_CHAT_MODEL env overrides (ACP_SETTINGS_OVERRIDES),
            # so these -c flags are the single delivery path to codex.
            cmd += codex_model_args(self.acp_settings)
        log.info("Starting ACP agent (%s): %s", spec.id, " ".join(cmd))
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=workdir, env=env,
            )
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            self._client = _NbiAcpClient(self)
            self._conn = acp.connect_to_agent(
                self._client, self._proc.stdin, self._proc.stdout
            )
            init = await self._conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=schema.ClientCapabilities(
                    fs=schema.FileSystemCapabilities(read_text_file=True, write_text_file=True),
                ),
                client_info=schema.Implementation(name="notebook-intelligence", version="1.0.0"),
            )
            self._agent_capabilities = getattr(init, "agent_capabilities", None)
            await self._authenticate(init)
            sess = await self._conn.new_session(
                cwd=workdir, mcp_servers=self._mcp_servers()
            )
            self._session_id = sess.session_id
            log.info("ACP agent session ready: %s", self._session_id)
        except Exception as e:
            self._start_error = f"ACP agent session failed: {e}"
            log.error(self._start_error, exc_info=True)
            self._started.set()
            await self._teardown()
            return
        self._started.set()
        try:
            await self._shutdown.wait()
        finally:
            await self._teardown()

    def _api_key(self, spec: AcpAgentSpec) -> str:
        return (self.acp_settings.get("api_key") or "").strip() \
            or os.environ.get(spec.api_key_env, "").strip()

    def _child_env(self, spec: AcpAgentSpec) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}
        api_key = self._api_key(spec)
        if api_key:
            env[spec.api_key_env] = api_key
            if spec.id == "codex":
                # Use a clean config dir so a stale ChatGPT OAuth token doesn't
                # shadow API-key auth (the spike hit refresh_token_reused).
                env["CODEX_HOME"] = os.path.join(
                    self._host.nbi_config.nbi_user_dir, "codex-home"
                )
                os.makedirs(env["CODEX_HOME"], exist_ok=True)
        return env

    async def _authenticate(self, init):
        methods = [m.id for m in (init.auth_methods or [])]
        if not methods:
            return
        spec = self.agent_spec
        method = (
            spec.auth_method
            if (self._api_key(spec) and spec.auth_method in methods)
            else methods[0]
        )
        try:
            await self._conn.authenticate(method_id=method)
        except Exception as e:
            log.warning("ACP agent authenticate(%s) failed: %s", method, e)

    async def _drain_stderr(self):
        while self._proc and self._proc.stderr:
            line = await self._proc.stderr.readline()
            if not line:
                break
            s = line.decode(errors="replace").rstrip()
            if s:
                log.debug("acp-agent: %s", s[:200])

    async def _teardown(self):
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                    await self._proc.wait()
                except Exception:
                    pass
        task = getattr(self, "_stderr_task", None)
        if task is not None:
            task.cancel()
        # Drop references to the dying loop/session so _ensure_started can't
        # hand a closing loop to the next turn (see the restart race).
        self._conn = None
        self._session_id = None
        self._client = None
        self._agent_capabilities = None
        self._proc = None
        self._loop = None

    async def _run_prompt(self, text: str):
        await self._conn.prompt(
            prompt=[schema.TextContentBlock(type="text", text=text)],
            session_id=self._session_id,
        )

    async def _cancel(self):
        try:
            if self._conn and self._session_id:
                await self._conn.cancel(session_id=self._session_id)
        except Exception as e:
            log.debug("ACP cancel failed: %s", e)

    def _run_on_loop(self, coro, timeout: float):
        """Run ``coro`` on the ACP loop from a foreign thread and wait."""
        loop = self._loop
        if loop is None:
            coro.close()
            raise RuntimeError("agent is not running")
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    def new_session(self) -> Optional[str]:
        """Start a fresh session on the running agent (the header's new-chat).

        Returns an error string on failure, else None. When the agent is not
        running there is nothing to clear — the next turn starts fresh — so
        that case is a silent no-op rather than a spurious subprocess launch.
        """
        with self._lock:
            running = (
                self._thread is not None
                and self._thread.is_alive()
                and not self._shutting_down
            )
        if not running or self._loop is None:
            return None
        # Serialize against turns: swapping the session id mid-prompt would
        # interleave two conversations. The frontend cancels any in-flight
        # request before asking for a new session, so this acquire is brief.
        with self._turn_lock:
            try:
                self._run_on_loop(self._new_session_coro(), _START_TIMEOUT)
                return None
            except Exception as e:
                log.warning("ACP new session failed, restarting client: %s", e)
                # The session state is unknown at this point; force a restart
                # so the next turn gets a clean one.
                self.shutdown()
                return f"Failed to start a new session: {e}"

    async def _new_session_coro(self):
        sess = await self._conn.new_session(
            cwd=get_jupyter_root_dir(), mcp_servers=self._mcp_servers()
        )
        self._session_id = sess.session_id
        if self._client is not None:
            self._client._tool_state.clear()
        log.info("ACP agent session ready: %s", self._session_id)

    def list_sessions(self) -> tuple[list[dict], Optional[str]]:
        """List the agent's stored sessions for the current workspace.

        Returns ``(sessions, error)``; sessions are newest-first dicts shaped
        for the session picker. ``session/list`` is an optional ACP extension
        (codex-acp implements it); an agent without it reports a friendly
        error instead of raising.
        """
        if not self._ensure_started():
            return [], self._start_error or "Agent is not available"
        try:
            result = self._run_on_loop(self._conn.list_sessions(), 30)
        except acp.RequestError as e:
            # Only JSON-RPC method-not-found means the capability is missing;
            # anything else is a real failure and must not masquerade as one.
            if getattr(e, "code", None) == -32601:
                return [], "This agent does not support listing sessions"
            log.warning("ACP session/list failed: %s", e)
            return [], f"Failed to list sessions: {e}"
        except Exception as e:
            log.warning("ACP session/list failed: %s", e)
            return [], f"Failed to list sessions: {e}"
        cwd = os.path.realpath(get_jupyter_root_dir())
        sessions = []
        for s in getattr(result, "sessions", None) or []:
            s_cwd = getattr(s, "cwd", "") or ""
            if os.path.realpath(s_cwd) != cwd:
                continue
            sessions.append({
                "session_id": getattr(s, "session_id", ""),
                "preview": _strip_context_preamble(getattr(s, "title", "") or ""),
                "modified_at": _epoch_from_iso(getattr(s, "updated_at", None)),
            })
        sessions.sort(key=lambda s: s["modified_at"], reverse=True)
        return sessions, None

    def load_session(self, session_id: str) -> Optional[str]:
        """Resume a stored session. Returns an error string on failure."""
        if not self._ensure_started():
            return self._start_error or "Agent is not available"
        caps = self._agent_capabilities
        if not getattr(caps, "load_session", False):
            return "This agent does not support resuming sessions"
        with self._turn_lock:
            try:
                self._run_on_loop(self._load_session_coro(session_id), _START_TIMEOUT)
                return None
            except Exception as e:
                log.warning("ACP session/load failed: %s", e)
                # A timed-out load may still complete later on the loop: it
                # would swap _session_id mid-turn and its replayed
                # session_update notifications would stream a resumed
                # conversation into whatever turn is then active. The session
                # state is unknown either way, so force a restart (mirrors
                # new_session's failure path).
                self.shutdown()
                return f"Failed to resume session: {e}"

    async def _load_session_coro(self, session_id: str):
        # The agent replays the resumed conversation through session_update
        # notifications during load. current_response is None outside a turn,
        # so the replay is deliberately not re-rendered — the sidebar shows a
        # "resumed" notice instead, exactly like Claude-mode resume.
        await self._conn.load_session(
            cwd=get_jupyter_root_dir(), session_id=session_id,
            mcp_servers=self._mcp_servers(),
        )
        self._session_id = session_id
        if self._client is not None:
            self._client._tool_state.clear()
        log.info("ACP agent session resumed: %s", session_id)

    @staticmethod
    def assemble_query(request: ChatRequest) -> str:
        """Join this turn's user-role lines into the prompt sent to the agent.

        The websocket handler appends the turn's context lines (attachment
        @-mentions, current-file pointer, output context) to the chat history
        before the prompt, exactly like Claude mode. Sending only
        ``request.prompt`` would silently drop whatever the user just
        attached. Slash commands mirror Claude mode's join (#388):

        - control-only commands (``/clear``, ``/compact``, ...) drop the
          context; it is meaningless to them and could break their parsing;
        - every other command is moved to the front so the agent still
          recognizes it, with the context lines preserved after it (a custom
          command receives them as part of its arguments).
        """
        query_lines = []
        for msg in request.chat_history:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str) and content:
                    query_lines.append(content)
        if not query_lines:
            return request.prompt
        if query_lines[-1].startswith("/"):
            command_line = query_lines[-1]
            command_token = command_line.split(None, 1)[0].lower()
            if command_token in CONTROL_SLASH_COMMANDS or len(query_lines) == 1:
                query_lines = [command_line]
            else:
                query_lines = [command_line, *query_lines[:-1]]
        return "\n".join(line.strip() for line in query_lines)

    def query(self, request: ChatRequest, response: ChatResponse) -> Optional[str]:
        """Run one turn. Returns an error string on failure, else None.

        Mirrors ClaudeCodeClient.query: blocks the per-request thread while the
        turn streams on the ACP loop thread.
        """
        if not self._turn_lock.acquire(blocking=False):
            return f"{self.agent_spec.label} is busy with another request"
        try:
            if not self._ensure_started():
                return self._start_error or f"{self.agent_spec.label} agent is not available"
            loop = self._loop
            if loop is None:
                return f"{self.agent_spec.label} agent is not available"
            # Fresh turn: drop the prior turn's accumulated tool-call cards so
            # the per-id merge cache does not grow without bound.
            if self._client is not None:
                self._client._tool_state.clear()
            self.current_response = response
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._run_prompt(self.assemble_query(request)), loop
                )
            except RuntimeError as e:
                return f"{self.agent_spec.label} agent is not available: {e}"
            start = time.time()
            try:
                while True:
                    if request.cancel_token is not None and request.cancel_token.is_cancel_requested:
                        self._schedule(self._cancel(), loop)
                        return None
                    try:
                        fut.result(timeout=_POLL)
                        return None
                    except concurrent.futures.TimeoutError:
                        pass
                    except Exception as e:
                        log.error("ACP agent turn failed: %s", e, exc_info=True)
                        return f"{self.agent_spec.label} agent error: {e}"
                    if time.time() - start > _RESPONSE_TIMEOUT:
                        self._schedule(self._cancel(), loop)
                        return f"{self.agent_spec.label} agent response timeout"
            finally:
                self.current_response = None
        finally:
            self._turn_lock.release()

    def _schedule(self, coro, loop):
        try:
            asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()

    def shutdown(self):
        # Mark shutting-down first so a concurrent _ensure_started won't reuse
        # the loop that is about to close.
        self._shutting_down = True
        if self._loop is not None and self._shutdown is not None:
            try:
                self._loop.call_soon_threadsafe(self._shutdown.set)
            except Exception:
                pass


class AcpAgentChatParticipant(BaseChatParticipant):
    """The chat participant for ACP mode.

    The participant id is stable; the display name, description, and icon
    follow the agent type selected in ``acp_settings`` so the chat header and
    message avatars always show which agent is answering.
    """

    def __init__(self, host: Host):
        super().__init__()
        self._host = host
        self._client = AcpAgentClient(host)

    @property
    def _spec(self) -> AcpAgentSpec:
        return self._client.agent_spec

    @property
    def id(self) -> str:
        return ACP_AGENT_CHAT_PARTICIPANT_ID

    @property
    def name(self) -> str:
        return self._spec.label

    @property
    def description(self) -> str:
        return self._spec.description

    @property
    def icon_path(self) -> str:
        return self._spec.icon_url

    @property
    def commands(self) -> list[ChatCommand]:
        return [ChatCommand(name=c, description="") for c in self._client.available_commands]

    @property
    def websocket_connector(self) -> ThreadSafeWebSocketConnector:
        return self._client.websocket_connector

    @websocket_connector.setter
    def websocket_connector(self, connector: ThreadSafeWebSocketConnector):
        self._client.websocket_connector = connector

    def restart_client(self) -> None:
        """Stop the running ACP client so the next request restarts it.

        The agent reads its credentials and model from acp_settings at launch;
        a settings change only takes effect on a fresh subprocess.
        """
        self._client.shutdown()

    def clear_chat_history(self) -> Optional[str]:
        """Start a fresh agent session (the header's new-chat button).

        Returns an error string on failure, else None.
        """
        return self._client.new_session()

    def list_sessions(self) -> tuple[list[dict], Optional[str]]:
        return self._client.list_sessions()

    def resume_session(self, session_id: str) -> Optional[str]:
        return self._client.load_session(session_id)

    def chat_prompt(self, model_provider: str, model_name: str) -> str:
        return ""

    async def handle_chat_request(
        self, request: ChatRequest, response: ChatResponse, options: dict = {}
    ) -> None:
        self._current_chat_request = request
        try:
            response.stream(ProgressData("Thinking…"))
            result = self._client.query(request, response)
            if isinstance(result, str) and result:
                response.stream(MarkdownData(content=f"**{self._spec.label} agent error:** {result}"))
        except Exception as e:
            log.error("Error handling ACP chat request: %s", e, exc_info=True)
            try:
                response.stream(MarkdownData(content=f"**Error:** {e}"))
            except Exception:
                pass
        finally:
            try:
                response.finish()
            except Exception:
                pass
