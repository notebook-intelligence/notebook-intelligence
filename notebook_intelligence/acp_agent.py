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
import contextlib
import difflib
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
import uuid
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
from notebook_intelligence import perf
from notebook_intelligence.acp_registry import (
    AcpAgentSpec,
    acp_adapter_path,
    codex_approval_args,
    codex_auth_args,
    codex_model_args,
    resolve_acp_agent,
    resolve_acp_agent_command,
)
from notebook_intelligence.acp_runtime import validate_acp_adapter_runtime
from notebook_intelligence.base_chat_participant import BaseChatParticipant
from notebook_intelligence.claude_sessions import CONTROL_SLASH_COMMANDS, NBI_CONTEXT_PREFIX
from notebook_intelligence.util import BIDI_CONTROL_CODEPOINTS, ThreadSafeWebSocketConnector, get_jupyter_root_dir

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
        # Open perf "tool:" spans keyed by tool_call_id, entered/exited
        # manually since a single tool call arrives as several separate
        # session/update notifications (create, then updates, then a
        # terminal status) rather than one call we can wrap in a `with`.
        self._tool_perf_spans: dict[str, Any] = {}

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
        is_new_call = tid not in self._tool_state
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
        # ACP has no stable tool-name field on a call (see schema), so the
        # NBI-mapped kind (read/edit/execute/...) is the closest thing to
        # an identity for the span.
        turn = perf.get_turn(resp.message_id)
        if turn is not None:
            if is_new_call:
                span_cm = turn.span(f"tool:{state['kind']}", tool=state["kind"], builtin=True)
                self._tool_perf_spans[tid] = span_cm
                span_cm.__enter__()
            if state["status"] in ("completed", "failed"):
                span_cm = self._tool_perf_spans.pop(tid, None)
                if span_cm is not None:
                    span_cm.__exit__(None, None, None)
        resp.stream(ToolCallData(
            id=tid,
            title=state["title"] or "Tool call",
            kind=state["kind"],
            status=state["status"],
            diffs=state["diffs"] or None,
        ))

    async def request_permission(self, options, session_id, tool_call, **kw):
        resp = self._response
        allow = _pick_option(options, "allow")
        if (
            resp is None
            or allow is None
            or not getattr(self._owner, "current_permissions_enabled", True)
        ):
            # No UI to ask, or no allow option offered: fail closed (reject).
            return acp.RequestPermissionResponse(
                outcome=schema.DeniedOutcome(outcome="cancelled")
            )
        agent_label = self._owner.agent_spec.label
        raw = getattr(tool_call, "raw_input", None)
        command, script, runner = _parse_command(raw)
        bidi = [c for c in command if ord(c) in BIDI_CONTROL_CODEPOINTS]
        if bidi:
            # Same policy as Claude mode's Bash approvals: a command that
            # displays in a different order from how it runs is refused and
            # the turn is interrupted, rather than offered for approval.
            names = ", ".join(dict.fromkeys(
                f"U+{ord(c):04X} {unicodedata.name(c)}" for c in bidi
            ))
            resp.stream(MarkdownData(
                f"&#x26A0; **{agent_label} tool call rejected: its command contains "
                f"hidden Unicode bidirectional controls ({names}).**"
            ))
            return _reject(options, cancel=True)
        codex_event = _is_codex_event(self._owner.agent_spec.id, raw)
        title = getattr(tool_call, "title", None) or ""
        script_is_title = codex_event and bool(script) and script.strip() == title.strip()
        try:
            details = _permission_details(
                tool_call, raw, codex_event, script, runner, script_is_title, agent_label
            )
            reject = _pick_option(options, "reject")
            lasting = []
            # Keep the command (or the whole input) last, next to the buttons.
            last = details.items.pop() if details.items and details.items[-1]["label"].split(" (")[0] in ("Command", "Input") else None
            if allow.kind != "allow_once":
                lasting.append("Approving grants a lasting permission, not only this request.")
                details.field("Permission granted", allow.name or "")
                if codex_event and raw.get("proposed_execpolicy_amendment"):
                    details.block("Lasting rule", _json_text(raw["proposed_execpolicy_amendment"]))
            if reject is not None and reject.kind != "reject_once":
                lasting.append("Rejecting records a lasting denial, not only for this request.")
                details.field("Denial recorded", reject.name or "")
            if last is not None:
                details.items.append(last)
        except _ValueTooLarge:
            resp.stream(MarkdownData(
                f"&#x26A0; **{agent_label} tool call rejected: it is too large to show "
                "in full for approval.**"
            ))
            return _reject(options)
        diff_card = bool(_diffs_from_content(getattr(tool_call, "content", None)))
        if diff_card:
            # Show a proposed edit's diff on its tool card even if the agent
            # did not send the tool call before asking.
            self._emit_tool_call(resp, tool_call)
        # Unique per request: codex-acp can ask more than once for one call.
        callback_id = f"acp-perm-{tool_call.tool_call_id}-{uuid.uuid4().hex}"
        sentences = [
            "Approve running this command?" if script_is_title
            else f"Approve this {agent_label} tool call?",
            *lasting,
        ]
        if command and not command.isascii():
            sentences.append(
                "The command contains non-ASCII characters, which can look like "
                "quotes or letters they are not."
            )
        if details.escaped:
            sentences.append("Characters that would not display are shown as \\u{XXXX}.")
        sentences.append(
            f"{agent_label} decides which tools to ask about, so some actions may run without a prompt."
        )
        pending_user_input = resp.stream_user_input_request(
            callback_id,
            ConfirmationData(
            title=f"{agent_label} tool call",
            message=" ".join(sentences),
            details=details.items or None,
            confirmArgs={"id": resp.message_id, "data": {
                "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": resp.message_id, "data": {
                "callback_id": callback_id, "data": {"confirmed": False}}},
            confirmLabel="Approve",
            cancelLabel="Reject",
            ),
        )
        user_input = await ChatResponse.wait_for_chat_user_input(
            resp, callback_id, pending_user_input
        )
        if user_input.get("confirmed"):
            return acp.RequestPermissionResponse(
                outcome=schema.AllowedOutcome(outcome="selected", option_id=allow.option_id)
            )
        if diff_card:
            # A rejected edit never starts, so the agent may send no final
            # status; close the card rather than leave it in progress.
            self._emit_tool_call(resp, schema.ToolCallUpdate.model_validate(
                {"toolCallId": tool_call.tool_call_id, "status": "failed"}
            ))
        return _reject(options)

    # fs/*: not offered. These would run in the Jupyter server process, outside
    # any agent sandbox, so serving them would let an agent read or write any
    # path the server can reach. The spec requires an agent to do its own file
    # I/O when the capability is not advertised. The acp router registers these
    # handlers whatever NBI advertises, so they must refuse rather than go unused.
    async def read_text_file(self, path, session_id, limit=None, line=None, **kw):
        raise acp.RequestError.method_not_found("fs/read_text_file")

    async def write_text_file(self, content, path, session_id, **kw):
        raise acp.RequestError.method_not_found("fs/write_text_file")

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

    # The inherited acp.Client stub answers any extension request with a null
    # success, which an agent would read as "handled".
    async def ext_method(self, method, params):
        raise acp.RequestError.method_not_found(method)


def _is_link(path: str) -> bool:
    # Windows directory junctions are not symlinks to os.path.islink.
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(isjunction and isjunction(path))


def _remove_persisted_codex_credentials(codex_home: str) -> None:
    """Delete key copies earlier NBI versions let Codex write to its CODEX_HOME.

    NBI points Codex at this directory only when it supplies the API key, so
    its ``auth.json`` and shell snapshots (which captured the environment,
    key included) came from those launches. Codex no longer writes either
    (``codex_auth_args``), but the old files would otherwise stay on disk.
    Nothing is followed through a symlink or junction, and a failure is
    logged rather than raised, so it never stops the agent from starting.
    """
    if _is_link(codex_home):
        log.warning("Not cleaning %s: it is a link, not NBI's own directory", codex_home)
        return
    if not os.path.isdir(codex_home):
        return
    paths = [os.path.join(codex_home, "auth.json")]
    snapshots = os.path.join(codex_home, "shell_snapshots")
    if os.path.isdir(snapshots) and not _is_link(snapshots):
        try:
            with os.scandir(snapshots) as entries:
                paths += [
                    entry.path for entry in entries
                    if entry.is_symlink() or entry.is_file(follow_symlinks=False)
                ]
        except OSError as e:
            log.warning("Could not list old Codex shell snapshots in %s: %s", snapshots, e)
    for path in paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            continue
        except OSError as e:
            log.warning("Could not remove an old Codex key copy at %s: %s", path, e)
            continue
        log.info("Removed an old Codex key copy at %s", path)


def _block_text(block) -> str:
    if block is None:
        return ""
    return getattr(block, "text", "") or ""


# Characters that render as nothing or as blank space: Unicode's
# Default_Ignorable_Code_Point ranges plus blank-looking letters and symbols.
# Characters str.isprintable() rejects (categories C*, Zl, Zp, and spaces
# other than U+0020) are caught separately.
_INVISIBLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x2800, 0x2800), (0x3164, 0x3164), (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFFC), (0x13441, 0x13442),
    (0x16FE4, 0x16FE4), (0x1BCA0, 0x1BCA3), (0x1D159, 0x1D159),
    (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF),
)
# Combining marks after this many in a row are escaped: stacked marks can
# draw over neighbouring text.
_MAX_COMBINING_MARKS = 3
# Shells whose ``<shell> -c <script>`` argv the approval card shows as a script.
_SCRIPT_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_BLANK_LINE_RUN = 2
_SPACE_RUN = 40
# Values are shown whole: the frontend bounds each block's height and lets it
# scroll, so nothing needs truncating to keep the buttons in reach. A request
# with a value longer than this is refused instead of being shown in part.
_MAX_VALUE_CHARS = 200_000


def _is_invisible(character: str) -> bool:
    codepoint = ord(character)
    return not character.isprintable() or any(
        low <= codepoint <= high for low, high in _INVISIBLE_RANGES
    )


def _escape_invisible(text: str, keep_line_breaks: bool) -> str:
    """Write characters that do not display as themselves as ``\\u{XXXX}``.

    A browser can draw these as nothing, as blank space, or as a line break
    while a shell reads them as part of a word, so text containing them could
    read differently from how it runs. ``keep_line_breaks`` keeps newlines and
    tabs for multi-line values; single-line values escape them too.
    """
    if text.isascii() and (
        text.isprintable() or (keep_line_breaks and text.replace("\n", "").replace("\t", "").isprintable())
    ):
        return text
    out = []
    marks = 0
    for character in text:
        if keep_line_breaks and character in "\n\t":
            out.append(character)
            marks = 0
            continue
        combining = unicodedata.category(character) in ("Mn", "Me")
        marks = marks + 1 if combining else 0
        if _is_invisible(character) or marks > _MAX_COMBINING_MARKS:
            out.append(f"\\u{{{ord(character):04X}}}")
        else:
            out.append(character)
    return "".join(out)


def _mark_padding(text: str) -> str:
    """Mark long runs of blank lines or spaces in an escaped multi-line value."""
    shown = []
    blank_run = 0

    def flush():
        if blank_run >= _BLANK_LINE_RUN:
            shown.append(f"[{blank_run} blank lines]")
        else:
            shown.extend([""] * blank_run)

    for line in text.split("\n"):
        if not line.strip():
            blank_run += 1
            continue
        flush()
        blank_run = 0
        shown.append(re.sub(r"[ \t]{2,}", _mark_space_run, line))
    flush()
    return "\n".join(shown)


def _mark_space_run(match) -> str:
    run = match.group(0)
    tabs = run.count("\t")
    # A tab can render as wide as eight spaces.
    if len(run) - tabs + 8 * tabs < _SPACE_RUN:
        return run
    spaces = len(run) - tabs
    parts = [f"{spaces} spaces"] if spaces else []
    if tabs:
        parts.append(f"{tabs} tabs")
    return f" [{' and '.join(parts)}] "


def _without_final_newline(text: str) -> str:
    return text[:-1] if text.endswith("\n") else text


def _parse_command(raw) -> tuple[str, str, str]:
    """Read ``raw_input["command"]`` once for every use the card makes of it.

    Returns ``(whole, script, runner)``: the whole command as text (what the
    bidi check scans), the script the card shows, and ``"<shell> <flag>"``
    when the command is ``<shell> -c <script>`` for a known shell, else "".
    """
    command = raw.get("command") if isinstance(raw, dict) else None
    if command is None:
        return "", "", ""
    if isinstance(command, str):
        return command, command, ""
    whole = json.dumps(command, ensure_ascii=False)
    if (
        isinstance(command, list) and len(command) == 3
        and all(isinstance(part, str) for part in command)
        and command[1] in ("-c", "-lc")
        and os.path.basename(command[0]) in _SCRIPT_SHELLS
    ):
        return whole, command[2], f"{os.path.basename(command[0])} {command[1]}"
    return whole, whole, ""


class _ValueTooLarge(Exception):
    pass


def _sized_label(label: str, text: str, notes=(), count_lines: bool = True) -> str:
    notes = list(notes)
    lines = text.count("\n") + 1
    if count_lines and lines > 1:
        notes.append(f"{lines} lines")
    if len(text) > 300:
        notes.append(f"{len(text)} characters")
    return f"{label} ({', '.join(notes)})" if notes else label


class _Details:
    """Builds the card's label and value pairs and notes any escaping.

    Labels are NBI's own text. Every value comes from the agent, so each is
    escaped here and rendered by the frontend in its own bounded block.
    """

    def __init__(self):
        self.items: list[dict] = []
        self.escaped = False

    def _escape(self, text: str, keep_line_breaks: bool) -> str:
        if len(text) > _MAX_VALUE_CHARS:
            raise _ValueTooLarge()
        shown = _escape_invisible(text, keep_line_breaks)
        self.escaped = self.escaped or shown != text
        return shown

    def escape_line(self, text: str) -> str:
        """One line of a multi-line block, with its own line breaks escaped."""
        return self._escape(text, keep_line_breaks=False)

    def field(self, label: str, value: str) -> None:
        shown = self._escape(value, keep_line_breaks=False)
        self.items.append({"label": _sized_label(label, value, count_lines=False), "value": shown})

    def block(self, label: str, value: str, notes=()) -> None:
        value = _without_final_newline(value)
        shown = _mark_padding(self._escape(value, keep_line_breaks=True))
        self.items.append({"label": _sized_label(label, value, notes), "value": shown})


def _json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _is_codex_event(agent_id: str, raw) -> bool:
    """codex-acp serializes Codex's own event as ``raw_input``, and every such
    event carries a call id or turn id. Checking the shape as well as the
    agent id keeps a different adapter launched through NBI_ACP_AGENT_COMMAND
    from having its input read as Codex fields."""
    return agent_id == "codex" and isinstance(raw, dict) and ("call_id" in raw or "turn_id" in raw)


def _permission_details(tool_call, raw, codex_event: bool, script: str, runner: str,
                        script_is_title: bool, agent_label: str) -> _Details:
    """What an ACP permission request would do, as label and value pairs.

    The request's title is the agent's own summary. codex-acp also sends the
    exact command, its working directory, the reason, the files an edit
    changes, and any network access or extra permissions in ``raw_input``,
    and approving can let that command run outside Codex's sandbox. For
    anything else, the card shows the agent's text content and its whole
    input. The command, or else the whole input, comes last, next to the
    buttons.
    """
    details = _Details()
    title = getattr(tool_call, "title", None) or ""
    if title and not script_is_title:
        details.field("Request", title)
    known = False
    if codex_event:
        command = raw.get("command")
        for key, label in (("cwd", "Working directory"), ("grant_root", "Write access under")):
            if isinstance(raw.get(key), str) and raw[key]:
                known = True
                details.field(label, raw[key])
        reason = raw.get("reason")
        if isinstance(reason, str) and reason.strip() and reason.strip() != title.strip():
            known = True
            details.field("Reason", reason)
        changes = raw.get("changes")
        if isinstance(changes, dict) and changes:
            known = True
            entries = []
            for path, change in changes.items():
                change = change if isinstance(change, dict) else {}
                kind = change.get("type") if isinstance(change.get("type"), str) else "change"
                move = change.get("move_path")
                target = f" -> {move}" if isinstance(move, str) and move else ""
                entries.append(details.escape_line(f"{kind}: {path}{target}"))
            details.block("Files", "\n".join(entries))
        network = raw.get("network_approval_context")
        if isinstance(network, dict):
            parts = [p for p in (network.get("protocol"), network.get("host")) if isinstance(p, str) and p]
            if parts:
                known = True
                details.field("Network access", " ".join(parts))
        for key, label in (("additional_permissions", "Additional permissions"), ("permissions", "Requested permissions")):
            if raw.get(key):
                known = True
                details.block(label, _json_text(raw[key]))
        if command is not None:
            known = True
            if runner and command[0] != runner.split(" ")[0]:
                details.field("Shell", command[0])
            details.block("Command", script, [f"run by {runner}"] if runner else [])
    if not known:
        texts = [
            _block_text(getattr(item, "content", None))
            for item in getattr(tool_call, "content", None) or []
            if getattr(item, "type", None) == "content"
        ]
        text = "\n\n".join(t for t in texts if t)
        if text:
            details.block(f"Details from {agent_label}", text)
        if raw not in (None, {}, []):
            details.block("Input", _json_text(raw))
    return details


def _pick_option(options, prefix: str):
    return next((o for o in options if o.kind == f"{prefix}_once"), None) \
        or next((o for o in options if str(o.kind).startswith(prefix)), None)


def _reject(options, cancel: bool = False) -> "acp.RequestPermissionResponse":
    reject = None if cancel else _pick_option(options, "reject")
    if reject is not None:
        return acp.RequestPermissionResponse(
            outcome=schema.AllowedOutcome(outcome="selected", option_id=reject.option_id)
        )
    return acp.RequestPermissionResponse(outcome=schema.DeniedOutcome(outcome="cancelled"))


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


# The directory pointer extension.py puts before every agent-mode prompt. Each
# segment after the directory is optional, and the kernel display name only
# follows the kernel name. A display name may hold one level of parentheses,
# as in "Python 3 (ipykernel)". A value or display name may also run to the
# end without closing, where an agent truncated the title inside it.
_POINTER_VALUE = r"[^']*(?:'|\Z)"
_CONTEXT_POINTER = re.compile(
    re.escape(NBI_CONTEXT_PREFIX)
    + r" '(?P<dir>[^']*)(?:'|\Z)"
    + r"(?: and current file is: '(?P<file>[^']*)(?:'|\Z))?"
    + r"(?: and active programming language is: '" + _POINTER_VALUE + ")?"
    + r"(?: with active kernel name: '" + _POINTER_VALUE
    + r"(?: \((?:[^()]|\([^()]*(?:\)|\Z))*(?:\)|\Z))?)?"
)
_CONTEXT_POINTER_SEGMENTS = (
    " and current file is: '",
    " and active programming language is: '",
    " with active kernel name: '",
)
# Markers the agents append to a truncated session title: codex-acp cuts at
# 117 characters plus "...", claude-code-acp at 127 plus a single ellipsis.
_TITLE_TRUNCATION_MARKERS = ("...", "\u2026")


def _strip_context_preamble(title: str) -> str:
    """Drop NBI's leading context lines from an agent-stored session title.

    The agent titles a session with its first prompt, which NBI prefixes with
    context lines (current-directory pointer, attachments). The preview should
    show the user's actual first question, like the Claude picker does.

    Handles both shapes: newline-separated lines, and the joined form codex
    stores (newlines collapsed to spaces, title truncated), where the
    directory pointer is matched structurally by its quoted segments. A
    pointer naming a file and a language is already longer than the agents'
    title limit, so a truncated title usually ends inside the pointer. When
    nothing of the question is left, the preview names the current file,
    relative to the open directory and cut short with the agent's marker if
    the title ended inside it. If the cut left none of the file's own name, or
    came before any file, the preview names the directory when the title holds
    all of it, and is empty otherwise. A title with no file and no question
    that was not truncated is kept as before.
    """
    lines = [line for line in title.splitlines() if line.strip()]
    while lines and (
        lines[0].startswith(NBI_CONTEXT_PREFIX)
        or lines[0].startswith("The user attached ")
    ):
        lines.pop(0)
    stripped = " ".join(lines)
    if stripped and stripped != title.strip():
        return stripped
    # Joined form: peel the directory pointer off by shape.
    marker = next((m for m in _TITLE_TRUNCATION_MARKERS if title.endswith(m)), "")
    body = title[: len(title) - len(marker)]
    pointer = _CONTEXT_POINTER.search(body)
    if pointer is None:
        return title
    rest = title[pointer.end():]
    tail = body[pointer.end():]
    if marker and any(segment.startswith(tail) for segment in _CONTEXT_POINTER_SEGMENTS):
        # The title ended inside the pointer: nothing after it is the question.
        rest = ""
    remainder = (title[:pointer.start()] + rest.lstrip()).strip()
    if remainder:
        return remainder
    directory = pointer.group("dir")
    file = pointer.group("file")
    if not file:
        if not marker:
            return title
        return directory if pointer.end("dir") < len(body) else ""
    file_cut = bool(marker) and pointer.end("file") == len(body)
    # The file path starts from the Jupyter root and usually repeats the
    # open directory, which would otherwise use up the whole preview.
    inside = f"{directory}/" if directory else ""
    if inside and file.startswith(inside):
        file = file[len(inside):]
    elif inside and file_cut and inside.startswith(file):
        file = ""
    if not file:
        return directory
    return file + marker if file_cut else file


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

    def __init__(self, host: Host, *, force_safe_mode: bool = False):
        self._host = host
        self._force_safe_mode = force_safe_mode
        self.websocket_connector: Optional[ThreadSafeWebSocketConnector] = (
            host.websocket_connector
        )
        self.current_response: Optional[ChatResponse] = None
        self.available_commands: list[str] = []
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._conn = None
        self._session_id: Optional[str] = None
        self._active_prompt_session_id: Optional[str] = None
        self._proc = None
        self._stderr_task = None
        self._started = threading.Event()
        self._start_error: Optional[str] = None
        self._shutdown = None  # asyncio.Event, created on the loop
        self._shutting_down = False
        self._client: Optional[_NbiAcpClient] = None
        self._agent_capabilities = None
        self._lock = threading.Lock()
        self.current_permissions_enabled = True
        # Serializes turns: the ACP session runs one prompt at a time, and
        # current_response is shared, so a second concurrent turn must not
        # interleave with the first.
        self._turn_lock = threading.Lock()

    def _mcp_servers(self) -> list:
        """The NBI MCP server config passed to every session create/load."""
        if self._force_safe_mode:
            return []
        # Launch by absolute file path: the adapter controls this server's cwd,
        # and ``-m`` could import a workspace package instead of NBI's server.
        from notebook_intelligence import acp_mcp_server
        # Pass the workspace explicitly instead of trusting the adapter's cwd.
        return [
            schema.McpServerStdio(
                name="nbi", command=sys.executable,
                args=[os.path.abspath(acp_mcp_server.__file__)],
                env=[schema.EnvVariable(
                    name=acp_mcp_server.WORKSPACE_ROOT_ENV,
                    value=get_jupyter_root_dir() or "",
                )],
            )
        ]

    @property
    def acp_settings(self) -> dict:
        return self._host.nbi_config.acp_settings

    @property
    def safe_mode(self) -> bool:
        return self._force_safe_mode

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
        try:
            env = self._child_env(spec)
            cmd = resolve_acp_agent_command(spec, workspace_root=workdir)
            launch_cwd = self._adapter_cwd()
            validate_acp_adapter_runtime(
                cmd, env_path=env.get("PATH", os.defpath), cwd=launch_cwd,
                workspace_root=workdir if "NBI_ACP_AGENT_COMMAND" not in os.environ else None,
            )
            if spec.id == "codex":
                # First, so they stay clear of the pins that follow.
                cmd += codex_auth_args(spec.api_key_env if self._api_key(spec) else "")
                cmd += codex_approval_args(
                    False
                    if self._force_safe_mode
                    else bool(self.acp_settings.get("full_access", False))
                )
                # acp_settings folds in the environment overrides too.
                cmd += codex_model_args(self.acp_settings)
            log.info("Starting ACP agent (%s): %s", spec.id, " ".join(cmd))
            self._proc = await asyncio.create_subprocess_exec(
                *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=launch_cwd, env=env,
            )
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            self._client = _NbiAcpClient(self)
            self._conn = acp.connect_to_agent(
                self._client, self._proc.stdin, self._proc.stdout
            )
            init = await self._conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=schema.ClientCapabilities(
                    fs=schema.FileSystemCapabilities(read_text_file=False, write_text_file=False),
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

    def _adapter_cwd(self) -> str:
        """The directory the adapter process is started in.

        Keep the process outside the workspace; session/new and session/load
        receive the actual workspace explicitly. This directory can have a
        shared ancestor, so cwd alone is not a package-resolution boundary.
        resolve_acp_agent_command instead selects a preinstalled executable
        by absolute path, without invoking npm at startup.
        """
        cwd = self._host.nbi_config.nbi_user_dir
        os.makedirs(cwd, exist_ok=True)
        return cwd

    def _child_env(self, spec: AcpAgentSpec) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")}
        if "NBI_ACP_AGENT_COMMAND" not in os.environ:
            # npm-installed adapters use /usr/bin/env node. Resolving the
            # adapter alone must not leave its interpreter workspace-controlled.
            env["PATH"] = acp_adapter_path(get_jupyter_root_dir())
        if spec.id == "codex":
            # Whatever the sign-in, so old copies do not outlive a switch away
            # from an API key.
            _remove_persisted_codex_credentials(
                os.path.join(self._host.nbi_config.nbi_user_dir, "codex-home")
            )
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
        self._active_prompt_session_id = self._session_id
        try:
            await self._conn.prompt(
                prompt=[schema.TextContentBlock(type="text", text=text)],
                session_id=self._session_id,
            )
        finally:
            self._active_prompt_session_id = None

    async def _run_isolated_prompt(self, text: str):
        """Run a one-shot prompt outside the sidebar's conversation."""
        session = await self._conn.new_session(
            cwd=get_jupyter_root_dir(), mcp_servers=[]
        )
        self._active_prompt_session_id = session.session_id
        try:
            await self._conn.prompt(
                prompt=[schema.TextContentBlock(type="text", text=text)],
                session_id=session.session_id,
            )
        finally:
            self._active_prompt_session_id = None

    async def _cancel(self):
        try:
            session_id = self._active_prompt_session_id or self._session_id
            if self._conn and session_id:
                await self._conn.cancel(session_id=session_id)
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
            self._client._tool_perf_spans.clear()
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
            self._client._tool_perf_spans.clear()
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
            turn = perf.get_turn(response.message_id)
            # Mirrors the "already running" branch of _ensure_started below --
            # computed up front since that call's own return value collapses
            # both the warm (already running) and freshly-spawned cases to
            # the same bool.
            cold = not (
                self._thread is not None
                and self._thread.is_alive()
                and not self._shutting_down
            )
            span_cm = turn.span("connect", cold=cold) if turn is not None else contextlib.nullcontext()
            with span_cm:
                started = self._ensure_started()
            if not started:
                return self._start_error or f"{self.agent_spec.label} agent is not available"
            loop = self._loop
            if loop is None:
                return f"{self.agent_spec.label} agent is not available"
            # Fresh turn: drop the prior turn's accumulated tool-call cards so
            # the per-id merge cache does not grow without bound.
            if self._client is not None:
                self._client._tool_state.clear()
                self._client._tool_perf_spans.clear()
            self.current_response = response
            self.current_permissions_enabled = True
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
                self.current_permissions_enabled = True
        finally:
            self._turn_lock.release()

    def query_isolated(
        self, text: str, response: ChatResponse
    ) -> Optional[str]:
        """Run a stateless prompt for a non-chat feature such as Chatbook.

        The prompt gets a fresh ACP session, does not alter the sidebar's
        session, and rejects permission requests because no confirmation UI is
        attached to the originating operation.
        """
        if not self._turn_lock.acquire(blocking=False):
            return f"{self.agent_spec.label} is busy with another request"
        try:
            if not self._ensure_started():
                return self._start_error or (
                    f"{self.agent_spec.label} agent is not available"
                )
            loop = self._loop
            if loop is None:
                return f"{self.agent_spec.label} agent is not available"
            if self._client is not None:
                self._client._tool_state.clear()
            self.current_response = response
            self.current_permissions_enabled = False
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._run_isolated_prompt(text), loop
                )
                try:
                    fut.result(timeout=_RESPONSE_TIMEOUT)
                    return None
                except concurrent.futures.TimeoutError:
                    self._schedule(self._cancel(), loop)
                    return f"{self.agent_spec.label} agent response timeout"
                except Exception as e:
                    log.error(
                        "ACP isolated agent turn failed: %s", e, exc_info=True
                    )
                    return f"{self.agent_spec.label} agent error: {e}"
            finally:
                self.current_response = None
                self.current_permissions_enabled = True
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
