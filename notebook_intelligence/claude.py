# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
import difflib
import os
import sys
import secrets
import asyncio
from enum import Enum
from pathlib import Path
from queue import Queue
import threading
import time
from typing import Any, Optional, TYPE_CHECKING
import uuid
import re
from anyio.abc import Process
from notebook_intelligence.api import AskUserQuestionData, BackendMessageType, CancelToken, ChatCommand, ChatModel, ChatRequest, ChatResponse, ClaudeToolType, CompletionContext, ConfirmationData, Host, InlineCompletionModel, MarkdownData, ProgressData, SignalImpl, ToolCallData, UICommandCancelledError, UserInputCancelledError
from notebook_intelligence.base_chat_participant import BaseChatParticipant
from notebook_intelligence.claude_sessions import CONTROL_SLASH_COMMANDS
from notebook_intelligence.feature_flags import is_external_ui_tools_active
from notebook_intelligence._version import __version__ as NBI_VERSION
import base64
import logging
from claude_agent_sdk import AssistantMessage, PermissionResultAllow, PermissionResultDeny, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage, create_sdk_mcp_server, ClaudeAgentOptions, ClaudeSDKClient, tool

from notebook_intelligence.util import ThreadSafeWebSocketConnector, _emit, get_jupyter_root_dir, import_litellm, resolve_claude_cli_path, safe_jupyter_path, terminate_process_tree

if TYPE_CHECKING:
    from anthropic import Anthropic

log = logging.getLogger(__name__)


def _extract_text_from_content(content) -> str:
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content if isinstance(block, dict) and block.get("type") == "text")
    return content


def assemble_client_query(query_lines: list[str]) -> str:
    """Join this turn's user-role lines into the query sent to the CLI.

    The CLI only recognizes a slash command when the query *starts* with
    it, but NBI appends this turn's context lines (attachment @-mentions,
    cell pointers, output context) *before* the user's prompt. The old
    behavior kept only the command line, silently discarding whatever the
    user had just attached (the footgun documented in extension.py's
    on_message). Instead:

    - control-only commands (``/clear``, ``/cost``, ...) still drop the
      context — it is meaningless to them and could break their parsing;
    - every other command is moved to the front so the CLI still
      recognizes it, with the context lines preserved after it (a custom
      skill/plugin command receives them as part of its arguments).
    """
    if query_lines and query_lines[-1].startswith('/'):
        command_line = query_lines[-1]
        command_token = command_line.split(None, 1)[0].lower()
        if command_token in CONTROL_SLASH_COMMANDS or len(query_lines) == 1:
            query_lines = [command_line]
        else:
            query_lines = [command_line, *query_lines[:-1]]
    return "\n".join(line.strip() for line in query_lines)

CLAUDE_CODE_ICON_SVG = '<svg width="1200" height="1200" viewBox="0 0 1200 1200" xmlns="http://www.w3.org/2000/svg"><g id="g314"><path id="path147" fill="#d97757" stroke="#d97757" d="M 233.959793 800.214905 L 468.644287 668.536987 L 472.590637 657.100647 L 468.644287 650.738403 L 457.208069 650.738403 L 417.986633 648.322144 L 283.892639 644.69812 L 167.597321 639.865845 L 54.926208 633.825623 L 26.577238 627.785339 L 3.3e-05 592.751709 L 2.73832 575.27533 L 26.577238 559.248352 L 60.724873 562.228149 L 136.187973 567.382629 L 249.422867 575.194763 L 331.570496 580.026978 L 453.261841 592.671082 L 472.590637 592.671082 L 475.328857 584.859009 L 468.724915 580.026978 L 463.570557 575.194763 L 346.389313 495.785217 L 219.543671 411.865906 L 153.100723 363.543762 L 117.181267 339.060425 L 99.060455 316.107361 L 91.248367 266.01355 L 123.865784 230.093994 L 167.677887 233.073853 L 178.872513 236.053772 L 223.248367 270.201477 L 318.040283 343.570496 L 441.825592 434.738342 L 459.946411 449.798706 L 467.194672 444.64447 L 468.080597 441.020203 L 459.946411 427.409485 L 392.617493 305.718323 L 320.778564 181.932983 L 288.80542 130.630859 L 280.348999 99.865845 C 277.369171 87.221436 275.194641 76.590698 275.194641 63.624268 L 312.322174 13.20813 L 332.8591 6.604126 L 382.389313 13.20813 L 403.248352 31.328979 L 434.013519 101.71814 L 483.865753 212.537048 L 561.181274 363.221497 L 583.812134 407.919434 L 595.892639 449.315491 L 600.40271 461.959839 L 608.214783 461.959839 L 608.214783 454.711609 L 614.577271 369.825623 L 626.335632 265.61084 L 637.771851 131.516846 L 641.718201 93.745117 L 660.402832 48.483276 L 697.530334 24.000122 L 726.52356 37.852417 L 750.362549 72 L 747.060486 94.067139 L 732.886047 186.201416 L 705.100708 330.52356 L 686.979919 427.167847 L 697.530334 427.167847 L 709.61084 415.087341 L 758.496704 350.174561 L 840.644348 247.490051 L 876.885925 206.738342 L 919.167847 161.71814 L 946.308838 140.29541 L 997.61084 140.29541 L 1035.38269 196.429626 L 1018.469849 254.416199 L 965.637634 321.422852 L 921.825562 378.201538 L 859.006714 462.765259 L 819.785278 530.41626 L 823.409424 535.812073 L 832.75177 534.92627 L 974.657776 504.724915 L 1051.328979 490.872559 L 1142.818848 475.167786 L 1184.214844 494.496582 L 1188.724854 514.147644 L 1172.456421 554.335693 L 1074.604126 578.496765 L 959.838989 601.449829 L 788.939636 641.879272 L 786.845764 643.409485 L 789.261841 646.389343 L 866.255127 653.637634 L 899.194702 655.409424 L 979.812134 655.409424 L 1129.932861 666.604187 L 1169.154419 692.537109 L 1192.671265 724.268677 L 1188.724854 748.429688 L 1128.322144 779.194641 L 1046.818848 759.865845 L 856.590759 714.604126 L 791.355774 698.335754 L 782.335693 698.335754 L 782.335693 703.731567 L 836.69812 756.885986 L 936.322205 846.845581 L 1061.073975 962.81897 L 1067.436279 991.490112 L 1051.409424 1014.120911 L 1034.496704 1011.704712 L 924.885986 929.234924 L 882.604126 892.107544 L 786.845764 811.48999 L 780.483276 811.48999 L 780.483276 819.946289 L 802.550415 852.241699 L 919.087341 1027.409424 L 925.127625 1081.127686 L 916.671204 1098.604126 L 886.469849 1109.154419 L 853.288696 1103.114136 L 785.073914 1007.355835 L 714.684631 899.516785 L 657.906067 802.872498 L 650.979858 806.81897 L 617.476624 1167.704834 L 601.771851 1186.147705 L 565.530212 1200 L 535.328857 1177.046997 L 519.302124 1139.919556 L 535.328857 1066.550537 L 554.657776 970.792053 L 570.362488 894.68457 L 584.536926 800.134277 L 592.993347 768.724976 L 592.429626 766.630859 L 585.503479 767.516968 L 514.22821 865.369263 L 405.825531 1011.865906 L 320.053711 1103.677979 L 299.516815 1111.812256 L 263.919525 1093.369263 L 267.221497 1060.429688 L 287.114136 1031.114136 L 405.825531 880.107361 L 477.422913 786.52356 L 523.651062 732.483276 L 523.328918 724.671265 L 520.590698 724.671265 L 205.288605 929.395935 L 149.154434 936.644409 L 124.993355 914.01355 L 127.973183 876.885986 L 139.409409 864.80542 L 234.201385 799.570435 L 233.879227 799.8927 Z"/></g></svg>'
CLAUDE_CODE_ICON_URL = f"data:image/svg+xml;base64,{base64.b64encode(CLAUDE_CODE_ICON_SVG.encode('utf-8')).decode('utf-8')}"
# Fallbacks when the user hasn't picked a model. Chat wants the current
# Sonnet-tier balance of quality and speed; autocomplete wants the fastest,
# cheapest tier — a suggestion has to beat the user's next keystroke, and a
# Sonnet-class model is both slower and several times the cost per token
# for a surface that fires on every pause in typing.
CLAUDE_DEFAULT_CHAT_MODEL = "claude-sonnet-5"
CLAUDE_DEFAULT_INLINE_COMPLETION_MODEL = "claude-haiku-4-5"
def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    """Parse an int env var defensively and clamp it to a sane range.

    This runs at import time: a malformed value must degrade to the
    default with a warning, not raise ``ValueError`` and block the
    backend from starting. Out-of-range values are clamped so an
    operator typo (``0``, ``-5``, ``10000``) can't produce failing API
    requests or restore the unbounded behavior the cap exists to
    prevent.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning(
            "Invalid %s=%r (expected an integer); using default %d",
            name, raw, default,
        )
        return default
    clamped = max(minimum, min(maximum, value))
    if clamped != value:
        log.warning(
            "%s=%d is outside the supported range [%d, %d]; clamping to %d",
            name, value, minimum, maximum, clamped,
        )
    return clamped


# Autocomplete suggestions are at most a few dozen lines; the previous
# 10K-token ceiling let a rambling response generate for many seconds
# before the extraction regex threw most of it away. The override is
# clamped to [1, 4096] so it can't reintroduce that behavior or produce
# a max_tokens value the API rejects.
CLAUDE_INLINE_COMPLETION_MAX_TOKENS = _bounded_int_env(
    "NBI_CLAUDE_INLINE_COMPLETION_MAX_TOKENS", 1024, 1, 4096
)
CLAUDE_CODE_CHAT_PARTICIPANT_ID = "claude-code"
CLAUDE_CODE_MAX_BUFFER_SIZE = 20 * 1024 * 1024 # 20MB

JUPYTER_UI_TOOLS_SYSTEM_PROMPT = """You can interact with the JupyterLab UI (notebook / file editor, terminal, etc.) using the tools provided in 'nbi' MCP server. Tools in 'nbi' MCP server, directly interact with the JupyterLab UI, accessing notebooks and files open in the UI. When interacting with JupyterLab UI, use relative file paths for file paths. If the user has asked you to create a notebook, save it afterward.
If you need to create a notebook in a language or kernel that is not already established by the current notebook context, first call the list-available-notebook-kernels tool and choose only from the kernels it returns. Do not guess kernel names.
"""


def build_claude_system_prompt(
    jupyter_ui_tools_enabled: bool,
    jupyter_root_dir: str,
) -> str:
    """Build the system prompt for the Claude Code chat participant.

    Module-level so the test suite can pin the answer-don't-create
    bias guard (issue #335) without instantiating the chat
    participant or touching JupyterLab runtime state.

    Order is load-bearing: the chat-default guard paragraph must
    appear BEFORE the UI-tools block. The UI-tools block ends with
    a conditional save instruction whose recency would otherwise
    dilute the guard's "default to chat reply" framing.
    """
    return f"""You are an AI programming assistant hosted in JupyterLab. JupyterLab supports Jupyter notebooks, plain file editors, and terminals; treat all three as first-class surfaces.
Assume Python if the language is not specified.
JupyterLab is launched from a working directory and it can only access files in this directory and its subdirectories. Follow the same rule for file system access. Working directory for current session is '{jupyter_root_dir}'.
If messages contain relative file paths, assume they are relative to the working directory.
If you need to install a Python package within a notebook cell code, use %pip install <package_name> instead of !pip install <package_name>.

Default to answering questions directly in your chat reply. Many user prompts are questions about code, data, or files ("summarize this", "explain this", "what does this output mean", "how would I do X", "debug this", "show me what this does") and the right response is prose in your reply, not a new artifact. Do not create a notebook, file, or other workspace artifact unless the user explicitly asks for one (for example: "create a notebook that...", "write me a script to...", "save this as a file", "show me a notebook that..."). When the user attaches a file and asks a question about it, read the file if you need to and answer in your reply; do not produce a new notebook to hold the answer.
{JUPYTER_UI_TOOLS_SYSTEM_PROMPT if jupyter_ui_tools_enabled else ""}
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
CLAUDE_AGENT_CLIENT_UPDATE_WAIT_TIME = float(os.getenv("NBI_CLAUDE_AGENT_CLIENT_UPDATE_WAIT_TIME", "0.5"))
CLAUDE_AGENT_CONNECT_TIMEOUT = float(os.getenv("NBI_CLAUDE_AGENT_CONNECT_TIMEOUT", "15"))
CLAUDE_AGENT_HEARTBEAT_INTERVAL = float(os.getenv("NBI_CLAUDE_AGENT_HEARTBEAT_INTERVAL", "20"))

# Single source of truth for tool-call presentation: SDK tool name ->
# (label, kind). `label` is the short human-readable progress label; `kind`
# is the coarse card-icon category (read / edit / execute / other). NBI's MCP
# tools use the kebab-case @tool decorator names; Claude's built-ins keep
# their CamelCase identifiers. Unknown names fall through to a sentence-case
# label and a keyword-heuristic kind rather than masking the raw name.
_CLAUDE_TOOLS: dict[str, tuple[str, str]] = {
    # NBI's MCP toolset (defined in this file via @tool(...))
    "list-available-notebook-kernels": ("Listing notebook kernels", "read"),
    "create-new-notebook": ("Creating notebook", "edit"),
    "rename-notebook": ("Renaming notebook", "edit"),
    "add-markdown-cell": ("Adding markdown cell", "edit"),
    "add-code-cell": ("Adding code cell", "edit"),
    "get-number-of-cells": ("Reading notebook", "read"),
    "get-cell-type-and-source": ("Reading cell", "read"),
    "get-cell-output": ("Reading cell output", "read"),
    "set-cell-type-and-source": ("Editing cell", "edit"),
    "delete-cell": ("Deleting cell", "edit"),
    "insert-cell": ("Inserting cell", "edit"),
    "run-cell": ("Running cell", "execute"),
    "save-notebook": ("Saving notebook", "edit"),
    "run-command-in-jupyter-terminal": ("Running shell command", "execute"),
    "open-file-in-jupyter-ui": ("Opening file", "read"),
    # Claude's built-in toolset
    "Bash": ("Running shell command", "execute"),
    "Read": ("Reading file", "read"),
    "Write": ("Writing file", "edit"),
    "Edit": ("Editing file", "edit"),
    "MultiEdit": ("Editing file", "edit"),
    "NotebookEdit": ("Editing notebook cell", "edit"),
    "Glob": ("Searching files", "read"),
    "Grep": ("Searching contents", "read"),
    "WebFetch": ("Fetching URL", "read"),
    "WebSearch": ("Searching web", "read"),
    "Task": ("Spawning subagent", "other"),
    "TodoWrite": ("Updating task list", "other"),
}


def _inner_tool_name(name: str) -> str:
    """Unwrap an MCP tool name (``mcp__<server>__<tool>``) to its inner tool."""
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            return parts[-1]
    return name


def _lookup_tool(name: str) -> Optional[tuple[str, str]]:
    """Return (label, kind) for a known tool, unwrapping MCP names; else None."""
    if name in _CLAUDE_TOOLS:
        return _CLAUDE_TOOLS[name]
    inner = _inner_tool_name(name)
    if inner != name and inner in _CLAUDE_TOOLS:
        return _CLAUDE_TOOLS[inner]
    return None


def humanize_claude_tool_name(name: str) -> str:
    """Map a Claude SDK tool name to a short progress-indicator label.

    Falls back to a sentence-cased version of the raw name so unknown
    tools still surface something the user can read rather than a bare
    kebab-case identifier.
    """
    entry = _lookup_tool(name)
    if entry is not None:
        return entry[0]
    pretty = _inner_tool_name(name).replace("-", " ").replace("_", " ").strip()
    if not pretty:
        return name
    return pretty[:1].upper() + pretty[1:]


def claude_tool_kind(name: str) -> str:
    """Coarse category for a tool call (``read`` / ``edit`` / ``execute`` /
    ``other``), used only to choose a card icon.

    Known names map directly (unwrapping ``mcp__server__tool``); anything left
    over falls back to a whole-token keyword heuristic so unfamiliar MCP tools
    still get a sensible icon rather than always landing on ``other``.
    """
    entry = _lookup_tool(name)
    if entry is not None:
        return entry[1]
    # Tokenize so a verb is matched as a whole word, not a substring: "widget"
    # must not read as "get", and "command" must not read as "and".
    tokens = {t for t in re.split(r"[^a-z0-9]+", _inner_tool_name(name).lower()) if t}
    if tokens & {"bash", "run", "exec", "execute", "shell", "terminal", "command"}:
        return "execute"
    if tokens & {"write", "edit", "create", "add", "insert", "delete", "remove",
                 "rename", "save", "set", "update", "patch", "make"}:
        return "edit"
    if tokens & {"read", "get", "list", "search", "grep", "glob", "fetch",
                 "view", "open", "show", "find"}:
        return "read"
    return "other"


def _format_token_count(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def format_result_usage(result: ResultMessage, show_cost: bool = True) -> Optional[str]:
    """One-line usage footer for a completed Claude Code turn.

    The SDK ends every turn with a ``ResultMessage`` carrying duration,
    token usage, and cost; NBI used to drop it on the floor, leaving
    users no signal of what a turn cost short of typing ``/cost``.
    Returns a small italic markdown line prefixed with a paragraph
    break — the footer is streamed right after the turn's final text
    block, and without the separator it would concatenate onto prose
    that doesn't end in a blank line (``Done.*12.3s · ...*``), breaking
    the emphasis markup. Returns ``None`` when there is nothing
    meaningful to show (error results, missing usage) so the caller can
    skip the footer entirely.

    ``show_cost`` gates the ``$`` segment. The SDK's ``total_cost_usd``
    is priced from the CLI's built-in public list rates, so it is only
    trustworthy for a direct first-party API key; on a subscription
    login the marginal cost is really $0, and enterprise/cloud-endpoint
    pricing differs. The caller passes ``False`` unless NBI is running
    against a direct API key on Anthropic's own endpoint (see
    ``_should_show_turn_cost``) so we omit a figure we can't stand
    behind, while still showing the always-accurate duration and token
    counts.
    """
    if getattr(result, "is_error", False):
        return None
    usage = getattr(result, "usage", None) or {}
    parts: list[str] = []

    duration_ms = getattr(result, "duration_ms", None)
    if isinstance(duration_ms, (int, float)) and duration_ms > 0:
        parts.append(f"{duration_ms / 1000:.1f}s")

    def _count(key: str) -> int:
        value = usage.get(key, 0)
        return value if isinstance(value, int) and value > 0 else 0

    total_in = (
        _count("input_tokens")
        + _count("cache_read_input_tokens")
        + _count("cache_creation_input_tokens")
    )
    output_tokens = _count("output_tokens")
    if total_in > 0 or output_tokens > 0:
        token_part = f"{_format_token_count(total_in)} in"
        cache_read = _count("cache_read_input_tokens")
        if cache_read > 0:
            token_part += f" ({_format_token_count(cache_read)} cached)"
        token_part += f" / {_format_token_count(output_tokens)} out"
        parts.append(token_part)

    if show_cost:
        cost = getattr(result, "total_cost_usd", None)
        if isinstance(cost, (int, float)) and cost > 0:
            parts.append(f"${cost:.4f}")

    if not parts:
        return None
    return f"\n\n*{' · '.join(parts)}*"


# Cap the diff lines surfaced per tool-call card so a large edit doesn't bloat
# the transcript or the wire payload.
_MAX_DIFF_LINES = 60


def _diff_lines(old: str, new: str, max_lines: int = _MAX_DIFF_LINES) -> tuple[list[dict], bool]:
    """Line-level diff of ``old`` -> ``new`` as typed lines for a card.

    Returns ``(lines, truncated)``. Each line is
    ``{"type": "add"|"remove"|"context", "content": str}``; the list is capped
    at ``max_lines`` so a huge edit stays compact.
    """
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    lines: list[dict] = []
    for raw in difflib.unified_diff(old_lines, new_lines, lineterm="", n=1):
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


def extract_tool_diffs(name: str, tool_input: Any) -> list[dict]:
    """Extract inline diffs from a file-edit tool's input.

    Handles Claude's Edit / Write / MultiEdit (including MCP-wrapped names).
    Returns a list of ``{"path", "lines", "truncated"}`` entries; empty for
    tools that aren't file edits or whose input lacks the expected fields.
    """
    if not isinstance(tool_input, dict):
        return []
    inner = _inner_tool_name(name)
    raw_diffs: list[tuple[str, str, str]] = []  # (path, old, new)
    if inner == "Edit":
        raw_diffs.append((
            tool_input.get("file_path", ""),
            tool_input.get("old_string", ""),
            tool_input.get("new_string", ""),
        ))
    elif inner == "Write":
        raw_diffs.append((
            tool_input.get("file_path", ""),
            "",
            tool_input.get("content", ""),
        ))
    elif inner == "MultiEdit":
        path = tool_input.get("file_path", "")
        for edit in tool_input.get("edits", []) or []:
            if isinstance(edit, dict):
                raw_diffs.append((
                    path,
                    edit.get("old_string", ""),
                    edit.get("new_string", ""),
                ))

    # Budget the line cap across ALL diffs in the card, not per diff, so a
    # MultiEdit with many edits can't blow up the payload.
    out: list[dict] = []
    remaining = _MAX_DIFF_LINES
    for path, old, new in raw_diffs:
        if remaining <= 0:
            if out:
                out[-1]["truncated"] = True
            break
        lines, truncated = _diff_lines(old, new, max_lines=remaining)
        if lines:
            out.append({"path": path, "lines": lines, "truncated": truncated})
            remaining -= len(lines)
    return out


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

# The subset of the SDK's PermissionMode literals NBI exposes (issue #359).
# "dontAsk" and "auto" are deliberately excluded: both skip the
# human-in-the-loop gate the way bypass does, without the arming UX.
CLAUDE_PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")
CLAUDE_BYPASS_PERMISSION_MODE = "bypassPermissions"
DEFAULT_PERMISSION_MODE = "default"

# Claude Code's enterprise managed-settings locations, one per platform.
_MANAGED_SETTINGS_PATHS = (
    "/Library/Application Support/ClaudeCode/managed-settings.json",
    "/etc/claude-code/managed-settings.json",
    "C:\\ProgramData\\ClaudeCode\\managed-settings.json",
)


def _claude_managed_permissions() -> Optional[dict]:
    """Best-effort read of the ``permissions`` block of Claude Code's
    enterprise managed settings.

    Returns ``None`` when no managed-settings file exists. A file that
    exists but cannot be parsed returns a block that disables bypass:
    an admin clearly tried to manage this machine, so the safe reading
    of a corrupt file is the most restrictive one.
    """
    for path in _MANAGED_SETTINGS_PATHS:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            permissions = data.get("permissions") if isinstance(data, dict) else None
            return permissions if isinstance(permissions, dict) else {}
        except Exception as exc:
            log.warning(
                f"Could not read Claude managed settings at {path}; treating bypass as disabled: {exc}"
            )
            return {"disableBypassPermissionsMode": True}
    return None


def claude_bypass_disabled_by_managed_settings() -> bool:
    permissions = _claude_managed_permissions()
    return bool(permissions and permissions.get("disableBypassPermissionsMode"))


def claude_managed_default_permission_mode() -> Optional[str]:
    """The ``permissions.defaultMode`` from managed settings, when valid."""
    permissions = _claude_managed_permissions() or {}
    mode = permissions.get("defaultMode")
    return mode if mode in CLAUDE_PERMISSION_MODES else None


def resolve_permission_mode(requested: str, allow_bypass: bool) -> str:
    """Clamp a client-requested permission mode at the request boundary.

    Fails closed: unknown modes collapse to default, and bypass is only
    honored when both the NBI policy allows it and managed settings don't
    forbid it, so a hand-rolled websocket request can't escalate past
    what the selector UI offers.
    """
    if requested not in CLAUDE_PERMISSION_MODES:
        return DEFAULT_PERMISSION_MODE
    if requested == CLAUDE_BYPASS_PERMISSION_MODE and (
        not allow_bypass or claude_bypass_disabled_by_managed_settings()
    ):
        return DEFAULT_PERMISSION_MODE
    return requested


# These module globals are written only from the single Claude client
# thread (connect, the query loop, and the SDK-invoked permission handler
# all run there), mirroring the existing _current_claude_client /
# set_current_request singletons. The only cross-thread hop is
# write_message, which marshals onto the Tornado loop. A future
# multi-client or per-session refactor would need to revisit this.
_current_permission_mode = DEFAULT_PERMISSION_MODE
_permission_mode_notifier: Optional[ThreadSafeWebSocketConnector] = None


def set_permission_mode_notifier(connector: Optional[ThreadSafeWebSocketConnector]):
    global _permission_mode_notifier
    _permission_mode_notifier = connector


def get_current_permission_mode() -> str:
    return _current_permission_mode


def _notify_permission_mode(mode: str, reset: bool = False):
    if _permission_mode_notifier is None:
        return
    try:
        _permission_mode_notifier.write_message({
            "type": BackendMessageType.ClaudePermissionModeChange,
            "data": {"mode": mode, "reset": reset}
        })
    except Exception as e:
        log.error(f"Error occurred while sending permission mode change to websocket: {str(e)}")


def reset_permission_mode_tracking():
    """A fresh SDK client starts in default mode; realign tracking and the UI.

    The notification is flagged ``reset`` and carries the selector's
    *starting* value, which honors managed settings' defaultMode. The UI
    applies it only to retire bypass (which never survives a reset, even as
    a managed default: re-arming is always manual) and otherwise keeps the
    user's explicit selection, so a mid-session reconnect (skills reload, a
    config change) doesn't clobber the chosen mode (issue #377). The next
    request applies whatever the selector then shows.
    """
    global _current_permission_mode
    _current_permission_mode = DEFAULT_PERMISSION_MODE
    starting = claude_managed_default_permission_mode() or DEFAULT_PERMISSION_MODE
    if starting == CLAUDE_BYPASS_PERMISSION_MODE:
        starting = DEFAULT_PERMISSION_MODE
    _notify_permission_mode(starting, reset=True)


async def apply_permission_mode(sdk_client: ClaudeSDKClient, mode: str) -> bool:
    """Switch the SDK's permission mode when it differs from the applied one.

    Every switch goes through here (selector requests, the hidden plan-mode
    slash aliases, and the plan-approval reset) so the tracked mode and the
    UI notification can't drift from what the SDK was actually told.

    Bypass is realized in NBI's own ``custom_permission_handler`` rather than
    the CLI's ``bypassPermissions`` mode: the CLI refuses that mode unless the
    session was launched with ``--dangerously-skip-permissions``, and that flag
    skips ALL permission prompts for the whole session (it can't be scoped to
    the bypass turns only), which would silently un-gate default / acceptEdits
    / plan too (issue #377). Instead bypass keeps the CLI in ``default`` and the
    handler auto-allows every tool while ``_current_permission_mode`` is bypass,
    so the gate stays fully under NBI's control.

    This does NOT re-check the bypass policy: callers must pass a mode that was
    already clamped by ``resolve_permission_mode`` at the request boundary, or a
    hardcoded non-bypass literal.
    """
    global _current_permission_mode
    if mode not in CLAUDE_PERMISSION_MODES or mode == _current_permission_mode:
        return False
    cli_mode = (
        DEFAULT_PERMISSION_MODE
        if mode == CLAUDE_BYPASS_PERMISSION_MODE
        else mode
    )
    await sdk_client.set_permission_mode(cli_mode)
    _current_permission_mode = mode
    _notify_permission_mode(mode)
    return True

def tool_text_response(text: Any, *, is_error: bool = False) -> dict[str, Any]:
    """Shape an MCP tool result. Set ``is_error=True`` for rejection paths.

    The Claude Agent SDK reads ``result.get("is_error", False)`` and maps
    it to ``CallToolResult.isError``. Without the flag, model-side retry
    heuristics treat rejection text as authoritative output instead of a
    fault to recover from, so a tool call rejected for security reasons
    can leak into chat as a confusing "successful" result.
    """
    result: dict[str, Any] = {
        "content": [{
            "type": "text",
            "text": str(text)
        }]
    }
    if is_error:
        result["is_error"] = True
    return result

def model_info_from_id(model_id: str) -> dict:
    """Get model info, checking cached models first then falling back to defaults."""
    for model in _claude_models_cache:
        if model["id"] == model_id:
            return model
    return {
        "id": model_id,
        "name": model_id,
        "context_window": 200000,
    }

# Cache of available Claude models fetched from API.
# `_claude_models_fetch_lock` short-circuits concurrent ``fetch_claude_models``
# calls: ``AIServiceManager.update_models_from_config`` fires a background
# fetch each time the cache is empty, and that method runs on every
# ``/capabilities`` GET and every ``/config`` POST. Without the lock,
# an in-flight (or recently-failed) fetch lets each subsequent call spawn
# another doomed thread, hammering api.anthropic.com in parallel when the
# api_key is missing or wrong.
_claude_models_cache: list[dict] = []
# Identity of the endpoint the cache was fetched from — (api_key,
# base_url) after credential normalization. The cache is only
# authoritative for that endpoint: a user can switch to a custom
# base_url whose catalog differs, and resolving a default against
# another endpoint's model list would send it an id it doesn't serve.
_claude_models_cache_endpoint: Optional[tuple] = None
_claude_models_fetch_lock = threading.Lock()

def get_claude_models() -> list[dict]:
    """Return the cached list of available Claude models."""
    return _claude_models_cache


def _endpoint_identity(api_key: str = None, base_url: str = None) -> tuple:
    return (
        _normalize_anthropic_credential(api_key),
        _normalize_anthropic_credential(base_url),
    )


def _model_version_key(model_id: str) -> tuple:
    """Sort key ordering model ids by their numeric version components.

    Plain lexicographic order gets version-like ids wrong —
    ``claude-sonnet-4-6`` sorts *after* ``claude-sonnet-4-10``. Compare
    the extracted number sequences instead ([4, 6] < [4, 10] < [5]),
    with the id itself as a deterministic tiebreak.
    """
    return ([int(part) for part in re.findall(r"\d+", model_id)], model_id)


def resolve_default_model(preferred: str, tier_prefix: str, api_key: str = None, base_url: str = None) -> str:
    """Pick a default model id, validated against the fetched model list.

    The ``CLAUDE_DEFAULT_*`` constants name current first-party aliases,
    but a custom ``base_url`` can front an endpoint that serves a
    different catalog. When the model cache holds this endpoint's
    catalog, prefer the constant only if the endpoint actually lists it;
    otherwise fall back to the newest id in the same tier
    (``claude-sonnet``/``claude-haiku`` prefix), then to the first
    listed model. When the cache is empty — or was fetched from a
    *different* endpoint (credentials changed, refresh still in flight)
    — trust the constant rather than another endpoint's catalog, and
    trigger a background refresh so later resolutions converge on this
    endpoint's catalog.
    """
    models = get_claude_models()
    endpoint_matches = _claude_models_cache_endpoint == _endpoint_identity(api_key, base_url)
    if not models or not endpoint_matches:
        if not endpoint_matches and _claude_models_cache_endpoint is not None:
            # The cache demonstrably belongs to another endpoint (the
            # credentials changed): kick off a background refresh so
            # subsequent resolutions see this endpoint's catalog. A
            # never-stamped cache is left to the startup fetch that
            # update_models_from_config already fires. The single-flight
            # lock inside fetch_claude_models dedupes concurrent
            # attempts; failures leave the cache as-is. Deliberately
            # never block on the network here — this runs in model
            # constructors on the request path, and a slow or dead
            # custom endpoint must degrade to one possibly-failing
            # request, not a hung chat turn.
            threading.Thread(
                target=fetch_claude_models,
                kwargs={"api_key": api_key, "base_url": base_url},
                name="nbi-claude-models-refresh",
                daemon=True,
            ).start()
        return preferred
    ids = [m["id"] for m in models if isinstance(m.get("id"), str)]
    if preferred in ids:
        return preferred
    tier_matches = sorted(
        (i for i in ids if i.startswith(tier_prefix)), key=_model_version_key
    )
    if tier_matches:
        fallback = tier_matches[-1]
    elif ids:
        fallback = ids[0]
    else:
        return preferred
    log.info(
        "Default model %r is not served by the configured endpoint; using %r",
        preferred, fallback,
    )
    return fallback


def _get_context_window(model_id: str) -> int:
    """Get context window size for a model using litellm's model database."""
    try:
        litellm = import_litellm()
        info = litellm.get_model_info(model_id)
        return info.get("max_input_tokens", 200000)
    except Exception:
        return 200000

def _normalize_anthropic_credential(value: Any) -> str | None:
    """Settings-panel inputs save unset fields as ``""`` rather than ``None``.
    The Anthropic SDK forwards an empty ``base_url`` straight to httpx, which
    rejects it with ``UnsupportedProtocol``; an empty ``api_key`` blocks the
    SDK from falling back to ``ANTHROPIC_API_KEY``. Normalize both to ``None``
    so the SDK's defaults kick in. Non-string values (``False``, ``123``,
    ``None``, dicts) coming from a malformed config also collapse to
    ``None`` so the SDK gets a clean default rather than crashing."""
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _is_first_party_anthropic_endpoint(base_url: Any) -> bool:
    """True when requests go to Anthropic's own API — the only place the
    SDK's public-list-price ``total_cost_usd`` is meaningful.

    An unset ``base_url`` means the SDK uses its default
    (``api.anthropic.com``). A custom ``base_url`` (proxy, gateway,
    Bedrock/Vertex front, OpenAI-compatible endpoint) bills on its own
    terms, so cost figures priced off Anthropic's list rates can't be
    trusted there.
    """
    from urllib.parse import urlparse

    normalized = _normalize_anthropic_credential(base_url)
    if normalized is None:
        return True
    return (urlparse(normalized).hostname or "").lower() == "api.anthropic.com"


def _resolve_effective_credential(settings_value: Any, env_var: str) -> str | None:
    """Resolve a credential the way the Claude Code subprocess does.

    NBI only overlays ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_BASE_URL`` onto
    the CLI's environment when the corresponding ``claude_settings`` field
    is non-empty; that env is merged over the server process's own
    ``os.environ``, so a blank setting falls through to whatever the
    process already had. Mirror that precedence — setting wins, else the
    ambient variable — so the cost gate reflects the endpoint/key the CLI
    actually uses, not just what the settings panel shows.
    """
    resolved = _normalize_anthropic_credential(settings_value)
    if resolved is not None:
        return resolved
    return _normalize_anthropic_credential(os.environ.get(env_var))


def _should_show_turn_cost(claude_settings: dict) -> bool:
    """Whether the per-turn footer's ``$`` cost can be trusted.

    Requires both a direct API key (subscription logins report a
    meaningless notional cost) and Anthropic's own endpoint (custom
    ``base_url`` targets bill differently). Credentials are resolved
    against the environment the CLI inherits, not just the settings
    panel, so an env-provided key or a custom ``ANTHROPIC_BASE_URL`` is
    honored. Either condition failing suppresses the dollar figure while
    duration/tokens still render.
    """
    api_key = _resolve_effective_credential(
        claude_settings.get('api_key'), 'ANTHROPIC_API_KEY'
    )
    if api_key is None:
        return False
    base_url = _resolve_effective_credential(
        claude_settings.get('base_url'), 'ANTHROPIC_BASE_URL'
    )
    return _is_first_party_anthropic_endpoint(base_url)


def _create_anthropic_client(api_key: str = None, base_url: str = None) -> "Anthropic":
    """Create an Anthropic client with normalized credentials and default headers."""
    from anthropic import Anthropic
    api_key = _normalize_anthropic_credential(api_key)
    base_url = _normalize_anthropic_credential(base_url)
    return Anthropic(
        api_key=api_key,
        base_url=base_url,
        default_headers={"User-Agent": f"NotebookIntelligence/{NBI_VERSION}"}
    )


def fetch_claude_models(api_key: str = None, base_url: str = None) -> list[dict]:
    """Fetch available models from the Anthropic API and update cache.

    Single-flight: if another caller is already inside this function (e.g.
    the daemon thread launched at startup), additional callers return the
    current cache snapshot without firing a duplicate request. The
    non-blocking acquire avoids piling up requests when the api_key is
    missing or wrong and every call would otherwise hit the SDK's
    timeout in parallel.
    """
    if not _claude_models_fetch_lock.acquire(blocking=False):
        return _claude_models_cache
    try:
        try:
            client = _create_anthropic_client(api_key, base_url)
            page = client.models.list(limit=100)
            models = []
            for model in page.data:
                # The Models API reports the context window directly on
                # newer SDK/service versions (max_input_tokens). Prefer it
                # over litellm's static database, which lags new model
                # releases and mislabels unknown ids with the 200K default.
                context_window = getattr(model, "max_input_tokens", None)
                if not isinstance(context_window, int) or context_window <= 0:
                    context_window = _get_context_window(model.id)
                models.append({
                    "id": model.id,
                    "name": model.display_name,
                    "context_window": context_window,
                })
            global _claude_models_cache_endpoint
            _claude_models_cache.clear()
            _claude_models_cache.extend(models)
            _claude_models_cache_endpoint = _endpoint_identity(api_key, base_url)
            log.info(f"Fetched {len(models)} Claude models: {[m['id'] + ' (' + m['name'] + ')' for m in models]}")
            return models
        except Exception as e:
            log.warning(f"Failed to fetch Claude models: {e}")
            return _claude_models_cache
    finally:
        _claude_models_fetch_lock.release()

class ClaudeChatModel(ChatModel):
    def __init__(self, model_id: str, api_key: str = None, base_url: str = None):
        super().__init__(provider=None)
        if model_id == "":
            model_id = resolve_default_model(CLAUDE_DEFAULT_CHAT_MODEL, "claude-sonnet", api_key, base_url)

        model_info = model_info_from_id(model_id)
        self._model_id = model_id
        self._model_name = model_info["name"]
        self._context_window = model_info["context_window"]
        self._supports_tools = True
        self._client = _create_anthropic_client(api_key, base_url)

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
        # Use the streaming endpoint so the inline-chat diff pane fills in
        # progressively instead of staying empty until the whole response
        # lands. Each text delta is forwarded as its own LLMRaw payload so the
        # front-end accumulator (chat-sidebar.tsx InlinePopoverComponent)
        # appends it directly to the modified-code state.
        if cancel_token is not None and cancel_token.is_cancel_requested:
            # A fast CancelChatRequest can flip the token before the worker
            # thread reaches us. Bail out before opening the stream so we
            # don't burn an Anthropic request whose output has nowhere to go.
            response.finish()
            return
        try:
            stream_options: dict[str, Any] = {
                "model": self._model_id,
                "max_tokens": 10000,
                "messages": messages,
            }
            system_prompt = options.get("system_prompt")
            if system_prompt:
                stream_options["system"] = system_prompt
            with self._client.messages.stream(**stream_options) as stream:
                for chunk in stream.text_stream:
                    if cancel_token is not None and cancel_token.is_cancel_requested:
                        break
                    if not chunk:
                        continue
                    response.stream({
                        "choices": [{
                            "delta": {
                                "role": "assistant",
                                "content": chunk
                            }
                        }]
                    })
        except Exception as e:
            # The outer inline-chat handler reports failures as MarkdownData,
            # but the diff pane only consumes payloads with delta.content, so
            # that error never reaches the user — they'd see partial code
            # silently fence-stripped on StreamEnd and assume it was final.
            # Push a plain-text marker into the same channel so the partial
            # output is visibly annotated, plus a structured nbi_stream_error
            # field so the fresh-generation auto-insert path in index.ts can
            # detect the failure and skip writing the partial buffer to the
            # user's cell. Re-raise so the caller logs and runs finish().
            if response is not None:
                response.stream({
                    "choices": [{
                        "delta": {
                            "role": "assistant",
                            "content": f"\n\n[Stream interrupted: {e}]"
                        }
                    }],
                    "nbi_stream_error": str(e)
                })
            raise

        response.finish()

class ClaudeCodeInlineCompletionModel(InlineCompletionModel):
    def __init__(self, model_id: str, api_key: str = None, base_url: str = None):
        super().__init__(provider=None)
        if model_id == "":
            model_id = resolve_default_model(CLAUDE_DEFAULT_INLINE_COMPLETION_MODEL, "claude-haiku", api_key, base_url)

        model_info = model_info_from_id(model_id)
        self._model_id = model_id
        self._model_name = model_info["name"]
        self._context_window = model_info["context_window"]
        self._client = _create_anthropic_client(api_key, base_url)

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return self._context_window

    def _extract_llm_generated_code(self, text: str) -> str:
        tags = ["<CODE>", "</CODE>", "<PREFIX>", "</PREFIX>", "<SUFFIX>", "</SUFFIX>", "<CURSOR>", "</CURSOR>"]
        for tag in tags:
            text = text.replace(tag, "")
        
        # Find all code blocks (```...```)
        # Pattern matches ```optional_language\n...content...```
        pattern = r'```(?:\w+)?\n?(.*?)```'
        matches = re.findall(pattern, text, re.DOTALL)
        
        if matches:
            # Return the last code block
            code = matches[-1]
            return code
        
        # Fallback: try inline code with single backticks
        inline_pattern = r'`([^`]+)`'
        inline_matches = re.findall(inline_pattern, text)
        if inline_matches:
            return inline_matches[-1]
        
        # No code blocks found, return original with basic cleanup
        return text

    def inline_completions(self, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        if cancel_token.is_cancel_requested:
            return ''

        from anthropic.types.text_block import TextBlock as AnthropicTextBlock

        message = self._client.messages.create(
            model=self._model_id,
            max_tokens=CLAUDE_INLINE_COMPLETION_MAX_TOKENS,
            system=f"""You are a code completion assistant. Your task is to generate intelligent autocomplete suggestions for the code at the cursor position for given language and active file type. This is not an interactive session, don't ask for clarifying questions, always generate a suggestion. Don't include any explanations for your response, just generate the code. Don't return any thinking or reasoning, just generate the code. You are given a code snippet with a prefix and a suffix. You need to generate a suggestion for the code that fits best in place of <CURSOR/>. You should return only the code that fits best in place of <CURSOR/>. You should provide multiline code if needed. Enclose the code in triple backticks, just return the code in language. You should not return any other text, just the code. DO NOT INCLUDE THE PREFIX OR SUFFIX IN THE RESPONSE. .ipynb files are Jupyter notebook files and for notebook files, you generate suggestions for a cell within the notebook. A cell can be a code cell with code or a markdown cell with markdown text. If the language is markdown, only return markdown text. If you need to install a Python package within a notebook cell code (for .ipynb files), use %pip install <package_name> instead of !pip install <package_name>. Follow the tags very carefully for proper spacing and indentations.""",
            messages=[
                {"role": "user", "content": f"""Generate a single suggestion that fits best in place of cursor. The code is below in between <CODE> tags and <CURSOR/> is the placeholder for the code to be filled in. Current language is {language} and the active file is {filename}.

<CODE><PREFIX>{prefix}</PREFIX><CURSOR/><SUFFIX>{suffix}</SUFFIX></CODE>
"""}]
        )
        code = ''
        for block in message.content:
            if cancel_token.is_cancel_requested:
                return ''
            if isinstance(block, AnthropicTextBlock):
                code += block.text

        if cancel_token.is_cancel_requested:
            return ''
        return self._extract_llm_generated_code(code)


class ClaudeCodeClient():
    def __init__(self, host: Host, client_options: ClaudeAgentOptions):
        self._host = host
        self._client_options = client_options
        self._websocket_connector = host.websocket_connector
        self._client = None
        self._client_queue = None
        self._client_thread_signal = None
        self._client_thread = None
        self._connect_resolved = threading.Event()
        self._status = ClaudeAgentClientStatus.NotConnected
        self._server_info: dict[str, Any] | None = None
        self._server_info_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self._reconnect_required = False
        self._continue_conversation: bool | None = None
        # One-shot, cleared after _create_client applies it.
        self._resume_session_id: str | None = None
        # Connect on a background thread so JupyterLab's startup path isn't
        # blocked by the synchronous SDK handshake (#163). Callers that need
        # to wait for readiness (e.g. _ensure_connected from a chat request)
        # still call connect() directly and get the blocking behavior.
        self.connect_in_background()

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

    @property
    def continue_conversation(self) -> bool | None:
        return self._continue_conversation

    @continue_conversation.setter
    def continue_conversation(self, value: bool | None):
        self._continue_conversation = value

    def is_connected(self):
        # `_client_thread_func` sets FailedToConnect *before* signalling
        # `_connect_resolved`, so this short-circuit is reliable for any
        # `is_connected()` call ordered after a `connect()` wait. Without
        # it, the publish-after-start race in `_start_worker_thread` can
        # resurrect a dead Thread reference whose `is_alive()` reads True
        # for Python's brief cleanup window, sending `query()` down the
        # dead-thread path with a misleading error.
        if self._status == ClaudeAgentClientStatus.FailedToConnect:
            return False
        return self._client_thread is not None and self._client_thread.is_alive()

    def _create_client_thread_event_loop(self) -> asyncio.AbstractEventLoop:
        if sys.platform == 'win32' and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            return asyncio.WindowsProactorEventLoopPolicy().new_event_loop()
        return asyncio.new_event_loop()

    def _run_client_thread(self, coro):
        loop = self._create_client_thread_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            asyncio.set_event_loop(None)
            loop.close()

    def _start_worker_thread(self) -> bool:
        """Spawn the worker thread without waiting for the handshake to resolve.

        Returns True if a thread was started (or was already running), False if
        spawning raised. Must be called with ``_connect_lock`` held to prevent
        two callers from racing and double-spawning the worker.
        """
        if self.is_connected():
            return True

        self._set_status(ClaudeAgentClientStatus.Connecting)

        self._reconnect_required = False
        self._client_queue = Queue()
        self._client_thread_signal = SignalImpl()
        self._connect_resolved.clear()
        try:
            thread = threading.Thread(
                name="Claude Agent Client Thread",
                target=self._run_client_thread,
                daemon=True,
                args=(self._client_thread_func(),),
            )
            thread.start()
            # Only publish the thread after start() succeeds so observers
            # (including test cleanup) never see an un-started Thread object.
            self._client_thread = thread
            return True
        except Exception as e:
            self._client_thread = None
            log.error(f"Error occurred while connecting to Claude agent client: {str(e)}")
            self._set_status(ClaudeAgentClientStatus.FailedToConnect)
            self._connect_resolved.set()
            return False

    def connect(self):
        # Serialise concurrent connects so a chat handler hitting
        # _ensure_connected() while the background __init__ connect is still
        # in flight doesn't double-spawn the worker thread.
        with self._connect_lock:
            if self.is_connected() and self._connect_resolved.is_set():
                return
            started = self._start_worker_thread()
        if not started:
            return
        # Block until the worker has either finished the SDK handshake or
        # failed — otherwise callers that check is_connected() afterwards
        # see a momentarily-alive thread that's about to die on spawn
        # failure, and never reach the "not connected" error branch (#147).
        if not self._connect_resolved.wait(timeout=CLAUDE_AGENT_CONNECT_TIMEOUT):
            log.warning(
                f"Claude agent did not reach a terminal connect state within "
                f"{CLAUDE_AGENT_CONNECT_TIMEOUT}s"
            )
        if self.is_connected():
            self._update_server_info_async()

    def connect_in_background(self):
        """Kick off ``connect()`` on a daemon thread and return immediately.

        Used from ``__init__`` so JupyterLab's server startup isn't blocked by
        the SDK handshake (#163). The status reflects ``Connecting`` until the
        background thread resolves, and any caller that needs the synchronous
        guarantee can call ``connect()`` directly — the lock makes them wait
        for the in-flight handshake instead of double-spawning.
        """
        thread = threading.Thread(
            name="Claude Agent Connect Thread",
            target=self.connect,
            daemon=True,
        )
        thread.start()

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
                self._connect_resolved.set()
                set_current_claude_client(client)
                set_permission_mode_notifier(self._websocket_connector)
                reset_permission_mode_tracking()

                while True:
                    queue = self._client_queue
                    signal = self._client_thread_signal
                    if queue is None:
                        return
                    event = queue.get(block=True)
                    event_id = event["id"]
                    event_type = event["type"]
                    if event_type == ClaudeAgentEventType.Query:
                        try:
                            request: ChatRequest = event["args"]["request"]
                            response: ChatResponse = event["args"]["response"]

                            set_current_request(request)
                            set_current_response(response)
                            # Refresh per query: the websocket reconnects on
                            # every page reload while this client thread (and
                            # its agent subprocess) persists, so the connector
                            # captured at connect time goes stale. The
                            # participant pushes the live connector onto this
                            # client, so re-read it here.
                            set_permission_mode_notifier(self._websocket_connector)

                            messages = request.chat_history
                            query_lines = []
                            for msg in messages:
                                if msg["role"] == "user":
                                    query_lines.append(_extract_text_from_content(msg["content"]))
                            client_query = assemble_client_query(query_lines)

                            already_handled = False

                            # Hidden aliases kept for one release after the
                            # selector replaced them in the UI (issue #359);
                            # they're filtered out of autocomplete on the
                            # frontend but still work when typed.
                            if client_query.startswith('/enter-plan-mode'):
                                await apply_permission_mode(client, "plan")
                                response.stream(MarkdownData("&#x2713; Entered plan mode"))
                                already_handled = True
                            elif client_query.startswith('/exit-plan-mode'):
                                await apply_permission_mode(client, DEFAULT_PERMISSION_MODE)
                                response.stream(MarkdownData("&#x2713; Exit plan mode"))
                                already_handled = True

                            if not already_handled and not request.cancel_token.is_cancel_requested:
                                # The mode was clamped against the bypass
                                # policy and managed settings at the websocket
                                # boundary; here it only needs applying.
                                await apply_permission_mode(
                                    client,
                                    request.permission_mode or DEFAULT_PERMISSION_MODE,
                                )
                                await client.query(client_query)
                                # Per-query map from tool_use_id to its
                                # (title, kind) so the ToolResultBlock echo
                                # back can re-emit the same card with a final
                                # status. Lifetime is one query — pops entries
                                # on completion so the dict stays bounded.
                                in_flight_tools: dict[str, tuple[str, str, list]] = {}
                                async for message in client.receive_response():
                                    if request.cancel_token.is_cancel_requested:
                                        # Stop iterating once the user cancels — we'd
                                        # otherwise keep recording ToolUseBlocks into
                                        # `in_flight_tools` for results that will
                                        # never be paired with progress callbacks.
                                        break
                                    if isinstance(message, AssistantMessage):
                                        for block in message.content:
                                            if isinstance(block, TextBlock):
                                                response.stream(MarkdownData(block.text))
                                            elif isinstance(block, ToolUseBlock):
                                                title = humanize_claude_tool_name(block.name)
                                                kind = claude_tool_kind(block.name)
                                                diffs = extract_tool_diffs(block.name, block.input)
                                                in_flight_tools[block.id] = (title, kind, diffs)
                                                response.stream(ToolCallData(
                                                    id=block.id,
                                                    title=title,
                                                    kind=kind,
                                                    status='in_progress',
                                                    diffs=diffs,
                                                ))
                                    elif isinstance(message, UserMessage):
                                        if isinstance(message.content, str):
                                            content = message.content
                                            content = content.replace('<local-command-stdout>', '').replace('</local-command-stdout>', '')
                                            response.stream(MarkdownData(content))
                                        elif isinstance(message.content, TextBlock):
                                            content = message.content.text
                                            content = content.replace('<local-command-stdout>', '').replace('</local-command-stdout>', '')
                                            response.stream(MarkdownData(content))
                                        elif isinstance(message.content, list):
                                            for block in message.content:
                                                if isinstance(block, ToolResultBlock):
                                                    # A result without a matching
                                                    # ToolUseBlock is anomalous (cross-
                                                    # query stragglers, sub-agent
                                                    # results routed through a parent
                                                    # tool_use_id). A status card with
                                                    # no opening card to merge into
                                                    # would be more confusing than
                                                    # silence, so we only surface
                                                    # results we can name.
                                                    entry = in_flight_tools.pop(
                                                        block.tool_use_id, None
                                                    )
                                                    if entry is None:
                                                        continue
                                                    title, kind, diffs = entry
                                                    status = 'failed' if block.is_error else 'completed'
                                                    response.stream(ToolCallData(
                                                        id=block.tool_use_id,
                                                        title=title,
                                                        kind=kind,
                                                        status=status,
                                                        diffs=diffs,
                                                    ))
                                    elif isinstance(message, ResultMessage):
                                        # End-of-turn accounting from the SDK.
                                        # Opt-in (off by default): the footer
                                        # is persistent per-turn noise, and its
                                        # cost figure is only trustworthy for a
                                        # direct API key against Anthropic's own
                                        # endpoint, so gate the dollar amount on
                                        # both (see _should_show_turn_cost).
                                        claude_settings = self._host.nbi_config.claude_settings
                                        if claude_settings.get('show_turn_usage', False):
                                            show_cost = _should_show_turn_cost(claude_settings)
                                            usage_line = format_result_usage(message, show_cost=show_cost)
                                            if usage_line is not None:
                                                response.stream(MarkdownData(usage_line))
                                    else:
                                        pass

                                # On cancel the receive loop breaks before
                                # these tools' results arrive. Their cards are
                                # persistent now (unlike the old transient
                                # progress line), so flip them out of the
                                # spinning in_progress state to a terminal
                                # 'cancelled' rather than leaving a perpetual
                                # spinner in the transcript.
                                if request.cancel_token.is_cancel_requested and in_flight_tools:
                                    for tool_id, (title, kind, diffs) in in_flight_tools.items():
                                        response.stream(ToolCallData(
                                            id=tool_id,
                                            title=title,
                                            kind=kind,
                                            status='cancelled',
                                            diffs=diffs,
                                        ))
                                    in_flight_tools.clear()
                        except Exception as e:
                            err_msg = f"Error communicating with Claude agent: {str(e)}"
                            log.error(err_msg)
                            if not self._reconnect_required:
                                response.stream(MarkdownData(err_msg))
                        finally:
                            _emit(signal, {"id": event_id, "data": "query completed"})
                            set_current_request(None)
                            set_current_response(None)
                    elif event_type == ClaudeAgentEventType.GetServerInfo:
                        try:
                            server_info = await client.get_server_info()
                        except Exception as e:
                            log.error(f"Error occurred while getting server info: {str(e)}")
                            server_info = None
                        finally:
                            _emit(signal, {"id": event_id, "data": server_info})
                    elif event_type == ClaudeAgentEventType.ClearChatHistory:
                        try:
                           await client.query('/clear')
                           async for message in client.receive_response():
                                # clear response messages
                                pass
                        except Exception as e:
                            log.error(f"Error occurred while clearing chat history: {str(e)}")
                        finally:
                            # A new session must not inherit an armed bypass:
                            # realign permission tracking to default and tell the
                            # UI, mirroring a fresh client connect. Without this,
                            # /clear leaves _current_permission_mode at bypass and
                            # the handler keeps auto-allowing every tool in the
                            # supposedly fresh session (issue #377). Refresh the
                            # notifier first since the connector goes stale across
                            # a page reload while this thread persists.
                            set_permission_mode_notifier(self._websocket_connector)
                            reset_permission_mode_tracking()
                            _emit(signal, {"id": event_id, "data": "chat history cleared"})
                    elif event_type == ClaudeAgentEventType.StopClient:
                        _emit(signal, {"id": event_id, "data": "stopped"})
                        return
                    else:
                        log.error(f"Unknown event type {event}")
        except Exception as e:
            self._client_thread = None
            log.error(f"Error occurred while running MCP server thread: {str(e)}")
            self._set_status(ClaudeAgentClientStatus.FailedToConnect)
            self._connect_resolved.set()

    def _create_client(self) -> ClaudeSDKClient:
        continue_conversation_cfg = self._host.nbi_config.claude_settings.get('continue_conversation', False)
        self._client_options.continue_conversation = self._continue_conversation if self._continue_conversation is not None else continue_conversation_cfg
        self._continue_conversation = None

        # resume overrides continue_conversation: always start from the
        # chosen transcript, never the most recent one.
        if self._resume_session_id is not None:
            self._client_options.resume = self._resume_session_id
            self._client_options.continue_conversation = False
            self._resume_session_id = None
        else:
            self._client_options.resume = None

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

        # _mark_as_disconnected() nulls both fields, so snapshot them once
        # rather than dereferencing through `self` repeatedly and racing with
        # a concurrent disconnect.
        queue = self._client_queue
        signal = self._client_thread_signal
        if queue is None or signal is None:
            return {
                "data": None,
                "success": False,
                "error": "Claude agent is not connected",
            }

        resp = {"data": None}
        def _on_client_response(data: dict):
            if data['id'] == event_id:
                resp["data"] = data['data']

        signal.connect(_on_client_response)

        start_time = time.time()
        last_heartbeat = start_time
        # Time the agent spends blocked on a tool approval (or any user input)
        # must not count toward the response timeout: a slow human reply isn't
        # an unresponsive agent. Without this, a long approval wait timed out
        # the turn and left the worker mid-request, wedging later prompts
        # (issue #381).
        response_obj = event_args.get("response") if event_args else None

        try:
            # Subscribe before publishing the request. A fast worker can emit
            # its response synchronously from queue.put(), so publishing first
            # would lose the response and leave this call polling until timeout.
            queue.put(event)
            while True:
                self._reconnect_required = False
                nbi_request_obj = get_current_request()
                if nbi_request_obj is not None and nbi_request_obj.cancel_token.is_cancel_requested:
                    try:
                        process: Process = self._client._transport._process
                        # Tear down the whole process tree, not just the Claude CLI
                        # child: the agent may have spawned shells, MCP servers, and
                        # dev servers that a bare child-only kill would orphan. The
                        # teardown (SIGTERM, grace, then SIGKILL on survivors) runs on
                        # a background thread so this cancel returns immediately; the
                        # reconnect on the next request spawns a fresh client and
                        # subprocess that share no state with the dying tree.
                        pid = process.pid if process is not None else None
                        if pid:
                            threading.Thread(
                                target=terminate_process_tree,
                                args=(pid,),
                                name="nbi-claude-cancel-teardown",
                                daemon=True,
                            ).start()

                        self._reconnect_required = True
                        self._continue_conversation = True
                    except Exception as e:
                        log.error(f"Error tearing down Claude subprocess on cancel: {str(e)}")
                    if self._reconnect_required:
                        self._mark_as_disconnected()
                    return {
                        "data": None,
                        "success": False,
                        "error": "Cancel requested by user"
                    }
                if resp["data"] is not None:
                    return {
                        "data": resp["data"],
                        "success": True,
                        "error": None
                    }
                # Bail out immediately if the worker thread has died (e.g. Claude
                # Code failed to start on a previous event). Without this we'd
                # poll for the full CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT window
                # (30 min default) while the UI sits on "Thinking…".
                if self._client_thread is None or not self._client_thread.is_alive():
                    self._mark_as_disconnected()
                    return {
                        "data": None,
                        "success": False,
                        "error": "Claude agent is not running",
                    }
                elapsed = time.time() - start_time
                if response_obj is not None:
                    elapsed -= response_obj.user_input_wait_seconds
                if elapsed > CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT:
                    return {
                        "data": None,
                        "success": False,
                        "error": "Claude agent response timeout",
                    }
                current_time = time.time()
                if current_time - last_heartbeat >= CLAUDE_AGENT_HEARTBEAT_INTERVAL:
                    if self._websocket_connector is not None:
                        try:
                            self._websocket_connector.write_message({
                                "type": BackendMessageType.ClaudeCodeHeartbeat,
                                "data": {}
                            })
                            last_heartbeat = current_time
                            log.debug(f"Heartbeat sent after {int(current_time - start_time)}s")
                        except Exception as e:
                            last_heartbeat = current_time
                            log.warning(f"Failed to send heartbeat: {e}")
                time.sleep(CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME)
        finally:
            signal.disconnect(_on_client_response)

    def _ensure_connected(self) -> bool:
        """Reconnect if the worker thread is missing or the SDK flagged a retry."""
        if self._reconnect_required or not self.is_connected():
            self.connect()
        return self.is_connected()

    def update_server_info(self):
        if not self._ensure_connected():
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
        if not self._ensure_connected():
            return "Claude agent is not connected. Check the server log for the underlying startup error."

        response = self._send_claude_agent_request(ClaudeAgentEventType.Query, {
            "request": request,
            "response": response
        })

        if response["success"]:
            # Query results are streamed to `response` as they arrive; the
            # success payload is just a sentinel, not something to surface.
            return None
        else:
            log.error(f"Claude agent query failed: {response['error']}")
            return response["error"]

    def clear_chat_history(self):
        if not self._ensure_connected():
            return
        response = self._send_claude_agent_request(ClaudeAgentEventType.ClearChatHistory)

        self._continue_conversation = False

        if response["success"]:
            return response["data"]
        else:
            log.error(f"Claude agent client failed to clear chat history: {response['error']}")
            return response["error"]
    
    def reconnect(self):
        self.disconnect()
        self.connect()

    def resume_session(self, session_id: str) -> None:
        """Reconnect the Claude client so the next query resumes ``session_id``.

        Raises ``ValueError`` if ``session_id`` is empty.
        """
        if not session_id:
            raise ValueError("session_id must be a non-empty string")
        self._resume_session_id = session_id
        self.reconnect()


@tool(
    "list-available-notebook-kernels",
    "Lists Jupyter kernels available in the current frontend environment.",
    {},
)
async def list_available_notebook_kernels(args) -> str:
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command(
        'notebook-intelligence:list-available-notebook-kernels',
        {}
    )
    return tool_text_response(json.dumps(ui_cmd_response))


@tool(
    "create-new-notebook",
    "Creates a new empty notebook.",
    {"language": str, "kernel_name": str},
)
async def create_new_notebook(args) -> str:
    """Creates a new empty notebook.
    """
    response = get_current_response()
    request = get_current_request()
    language = args.get("language") or getattr(request, "language", "") or "python"
    kernel_name = args.get("kernel_name") or getattr(request, "kernel_name", "") or ""
    ui_cmd_response = await response.run_ui_command(
        'notebook-intelligence:create-new-notebook',
        {'code': '', 'language': language, 'kernelName': kernel_name}
    )
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
    cell_index = ui_cmd_response.get("cellIndex") if isinstance(ui_cmd_response, dict) else None
    if isinstance(cell_index, int):
        return tool_text_response(f"Added markdown cell at index {cell_index}")
    return tool_text_response(f"Added markdown cell to notebook")

@tool("add-code-cell", "Adds a code cell to the notebook.", {"source": str})
async def add_code_cell(args) -> str:
    """Adds a code cell to notebook.
    Args:
        source: Code source for the notebook's current language
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-active-notebook', {'source': args['source']})
    cell_index = ui_cmd_response.get("cellIndex") if isinstance(ui_cmd_response, dict) else None
    if isinstance(cell_index, int):
        return tool_text_response(f"Added code cell at index {cell_index}")
    return tool_text_response(f"Added code cell to notebook")

@tool("get-number-of-cells", "Gets the number of cells in the notebook.", {})
async def get_number_of_cells(args) -> str:
    """Get number of cells for the active notebook.
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-number-of-cells', {})

    return tool_text_response(ui_cmd_response)

@tool("get-cell-type-and-source", "Gets the type and source of the cell at zero-based index.", {"cell_index": int})
async def get_cell_type_and_source(args) -> str:
    """Get cell type and source for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-type-and-source', {"cellIndex": args['cell_index'] })

    return tool_text_response(ui_cmd_response)


@tool("get-cell-output", "Gets the output of the cell at zero-based index.", {"cell_index": int})
async def get_cell_output(args) -> str:
    """Get cell output for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = get_current_response()
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-output', {"cellIndex": args['cell_index']})

    return tool_text_response(ui_cmd_response)

@tool("set-cell-type-and-source", "Sets the type and source of the cell at zero-based index.", {"cell_index": int, "cell_type": str, "source": str})
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

@tool("delete-cell", "Deletes the cell at zero-based index.", {"cell_index": int})
async def delete_cell(args) -> str:
    """Delete the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = get_current_response()

    ui_cmd_response = await response.run_ui_command('notebook-intelligence:delete-cell-at-index', {"cellIndex": args['cell_index']})

    return tool_text_response(f"Deleted the cell at index: {args['cell_index']}")

@tool("insert-cell", "Inserts a cell with type and source at zero-based index.", {"cell_index": int, "cell_type": str, "source": str})
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

@tool("run-cell", "Runs the cell at zero-based index.", {"cell_index": int})
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
        # Mirror the sandbox the sibling built-in tool applies (see
        # built_in_toolsets.run_command_in_jupyter_terminal): the cwd is
        # forwarded to a JupyterLab UI command that opens a real terminal
        # at any path the user can read, so an LLM-supplied '/etc',
        # '../../..', or a workspace symlink would otherwise land a shell
        # outside jupyter_root_dir. Apply the same gate server-side
        # before the UI bridge is invoked.
        working_directory = args.get('working_directory', '') or ''
        try:
            work_dir = safe_jupyter_path(working_directory)
        except ValueError as e:
            return tool_text_response(f"Error: {e}", is_error=True)
        if not work_dir.exists():
            return tool_text_response(
                f"Directory '{working_directory}' does not exist",
                is_error=True,
            )
        if not work_dir.is_dir():
            return tool_text_response(
                f"'{working_directory}' is not a directory",
                is_error=True,
            )
        response = get_current_response()
        # The frontend command forwards `cwd` directly to JupyterLab's
        # terminal service (`terminal:create-new`), which hands the
        # value to a real PTY spawn that honors absolute paths. Send
        # the sandboxed absolute path so any intermediate that does
        # cwd-relative resolution can't double-resolve into a different
        # target.
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:run-command-in-terminal', {
            'command': args['command'],
            'cwd': str(work_dir),
        })
        return tool_text_response(ui_cmd_response)
    except Exception as e:
        return tool_text_response(
            f"Error running command in Jupyter terminal: {str(e)}",
            is_error=True,
        )


@tool("open-file-in-jupyter-ui", "Opens a file in the Jupyter UI.", {"file_path": str})
async def open_file_in_jupyter_ui(args) -> str:
    """Open a file in the Jupyter UI.

    Args:
        file_path: Path to the file to open
    """
    try:
        # ``docmanager:open`` routes the path through JupyterLab's
        # contents service, which is rooted at ``jupyter_root_dir``.
        # The contents service strips a leading slash and rejoins under
        # the root (jupyter_server/utils.py: to_os_path), so forwarding
        # an absolute path would turn '/x/y/foo.ipynb' into
        # '{root}/x/y/foo.ipynb' and 404. Forward the path *relative to
        # the workspace root* instead, after safe_jupyter_path has
        # resolved symlinks / ``..`` and confirmed containment.
        file_path = args.get('file_path', '') or ''
        try:
            target = safe_jupyter_path(file_path)
        except ValueError as e:
            return tool_text_response(f"Error: {e}", is_error=True)
        root_dir = Path(get_jupyter_root_dir()).expanduser().resolve()
        relative_path = target.relative_to(root_dir).as_posix()
        response = get_current_response()
        ui_cmd_response = await response.run_ui_command('docmanager:open', {
            'path': relative_path,
        })
        return tool_text_response(ui_cmd_response)
    except Exception as e:
        return tool_text_response(
            f"Error opening file in Jupyter UI: {str(e)}",
            is_error=True,
        )


# The Jupyter-UI tools above are exposed to Claude Code as an EXTERNAL stdio MCP
# server (notebook_intelligence.mcp_ui_proxy) instead of an in-process sdk server,
# so they survive a managed/enterprise MCP config that forbids dynamically
# configured servers. The proxy fetches this manifest over HTTP and forwards each
# call to invoke_ui_tool, which runs the same handlers against the live chat turn.
JUPYTER_UI_TOOLS = [
    list_available_notebook_kernels, create_new_notebook, add_markdown_cell,
    add_code_cell, get_number_of_cells, get_cell_type_and_source, get_cell_output,
    set_cell_type_and_source, delete_cell, insert_cell, run_cell, save_notebook,
    rename_notebook, run_command_in_jupyter_terminal, open_file_in_jupyter_ui,
]

_PY_TYPE_TO_JSON = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _tool_json_schema(input_schema) -> dict:
    """Mirror claude_agent_sdk.create_sdk_mcp_server's schema build so the manifest
    is identical to the former in-process server (dict params, all required)."""
    if isinstance(input_schema, dict):
        if "type" in input_schema and "properties" in input_schema and isinstance(input_schema["type"], str):
            return input_schema
        props = {name: {"type": _PY_TYPE_TO_JSON.get(t, "string")} for name, t in input_schema.items()}
        return {"type": "object", "properties": props, "required": list(props)}
    return {"type": "object", "properties": {}}


# Per-process shared secret proving a request came from the mcp_ui_proxy NBI spawned.
# The proxy sends it to UIToolsHandler in a dedicated header (X-NBI-UI-Tools-Token),
# separate from the Jupyter identity in Authorization. A bearer secret is not
# cookie-based and is therefore XSRF-immune, so the relay uses it only to exempt the
# request from the browser XSRF check (mirroring jupyter_server's own exemption for
# token-authenticated requests) -- it is not a Jupyter identity. Generated once per
# process and handed only to the proxy (via the NBI_UI_TOOLS_SECRET env var).
_UI_TOOLS_SECRET = secrets.token_urlsafe(32)


def get_ui_tools_secret() -> str:
    return _UI_TOOLS_SECRET


def get_ui_tools_manifest() -> list[dict]:
    """MCP tool descriptors (name/description/inputSchema) for the Jupyter-UI tools."""
    return [
        {"name": t.name, "description": t.description, "inputSchema": _tool_json_schema(t.input_schema)}
        for t in JUPYTER_UI_TOOLS
    ]


async def invoke_ui_tool(name: str, arguments: dict, timeout: float = CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT) -> dict:
    """Run a Jupyter-UI tool by name against the active chat turn's UI bridge.

    Returns an MCP tool-result dict: {"content": [...], "is_error"?: bool}. The
    timeout matches the agent response window so long-running cells/commands aren't
    cut off, while still bounding the call if the frontend never replies (closed tab)."""
    tool_def = next((t for t in JUPYTER_UI_TOOLS if t.name == name), None)
    if tool_def is None:
        return tool_text_response(f"Unknown tool: {name}", is_error=True)
    if get_current_response() is None:
        return tool_text_response(
            "No active Notebook Intelligence chat turn; UI tools are only callable while a chat request is being handled.",
            is_error=True,
        )
    try:
        return await asyncio.wait_for(tool_def.handler(arguments or {}), timeout)
    except asyncio.TimeoutError:
        return tool_text_response(f"Jupyter UI command timed out after {timeout:.0f}s.", is_error=True)
    except Exception as exc:
        return tool_text_response(f"Jupyter UI command failed: {exc}", is_error=True)


async def custom_permission_handler(
    tool_name: str,
    input_data: dict,
    context: dict,
):
    try:
        return await _custom_permission_handler(tool_name, input_data, context)
    except (UserInputCancelledError, UICommandCancelledError) as error:
        log.debug("Permission request for %s cancelled: %s", tool_name, error)
        return PermissionResultDeny(message="Request cancelled", interrupt=True)


async def _custom_permission_handler(
    tool_name: str,
    input_data: dict,
    context: dict
):
    """Custom logic for tool permissions."""
    global _approved_tools_response_id
    global _approved_tools_for_response

    log.debug(f"Custom permission handler called for tool {tool_name} with input {input_data} and context {context}")

    # Bypass Permissions: auto-allow every tool without prompting. NBI realizes
    # bypass here rather than via the CLI's bypassPermissions mode (which needs
    # an un-scopable launch flag); the mode only reaches here clamped against
    # the policy at the request boundary (issue #377).
    if get_current_permission_mode() == CLAUDE_BYPASS_PERMISSION_MODE:
        return PermissionResultAllow()

    response = get_current_response()
    callback_id = str(uuid.uuid4())

    if tool_name == "EnterPlanMode":
        pending_user_input = response.stream_user_input_request(
            callback_id,
            ConfirmationData(
            title="Enter Plan Mode",
            message="Claude wants to enter plan mode to explore and design an implementation approach. In plan mode, Claude will explore the codebase thoroughly, identify existing patterns, design an implementation strategy, and present a plan for your approval. No code changes will be made until you approve the plan.",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
            confirmLabel="Yes, enter plan mode",
            cancelLabel="No, start implementing now",
            ),
        )
        user_input = await ChatResponse.wait_for_chat_user_input(
            response, callback_id, pending_user_input
        )
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
        pending_user_input = response.stream_user_input_request(
            callback_id,
            ConfirmationData(
            message="Do you want to confirm the plan above?",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
            confirmLabel="Yes, approve plan",
            cancelLabel="No, continue planning",
            ),
        )
        user_input = await ChatResponse.wait_for_chat_user_input(
            response, callback_id, pending_user_input
        )
        if user_input['confirmed'] == True:
            await apply_permission_mode(get_current_claude_client(), DEFAULT_PERMISSION_MODE)
            return PermissionResultAllow(updated_input={"message": "Plan approved", "approved": True})
        else:
            return PermissionResultDeny(message="User did not confirm the plan", interrupt=True)
    elif tool_name == "AskUserQuestion":
        pending_user_input = response.stream_user_input_request(
            callback_id,
            AskUserQuestionData(
            identifier={"id": response.message_id, "callback_id": callback_id},
            questions=input_data['questions']
            ),
        )
        user_input = await ChatResponse.wait_for_chat_user_input(
            response, callback_id, pending_user_input
        )
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
        pending_user_input = response.stream_user_input_request(
            callback_id,
            ConfirmationData(
            message=f"Approve Bash tool to execute the command above?",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
            ),
        )
        user_input = await ChatResponse.wait_for_chat_user_input(
            response, callback_id, pending_user_input
        )
        if user_input['confirmed'] == False:
            return PermissionResultDeny(message="User did not confirm the tool call", interrupt=True)

        log.debug(f"Allowing tool {tool_name} with input {input_data}")
        return PermissionResultAllow()
    else:
        if _approved_tools_response_id != response.message_id:
            _approved_tools_for_response.clear()

        if tool_name in _approved_tools_for_response:
            return PermissionResultAllow()
        response.stream(MarkdownData(f"&#x2713; Calling tool '{tool_name}'...", detail={"title": "Parameters", "content": json.dumps(input_data)}))
        pending_user_input = response.stream_user_input_request(
            callback_id,
            ConfirmationData(
            message=f"Are you sure you want to call this tool?",
            confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
            confirmSessionArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed_for_session": True}}},
            cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
            ),
        )
        user_input = await ChatResponse.wait_for_chat_user_input(
            response, callback_id, pending_user_input
        )
        if user_input.get('confirmed', None) == False:
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
        self._client = ClaudeCodeClient(host, self._client_options)
        skill_manager = host.get_skill_manager()
        if skill_manager is not None and skill_manager is not NotImplemented:
            skill_manager.on_skills_changed(self._on_skills_changed)

    def _on_skills_changed(self):
        # Called from the skill watcher thread. Marshal onto the Tornado event loop before
        # touching asyncio state — update_client_debounced uses asyncio.get_event_loop().
        connector = self._host.websocket_connector
        if connector is None:
            return
        try:
            self._client.continue_conversation = True
            connector.schedule(self.update_client_debounced)
            connector.write_message({
                "type": BackendMessageType.SkillsReloaded,
                "data": {}
            })
        except Exception as e:
            log.error(f"Error while handling skills changed event: {e}")

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
        # Dedupe by name so a command the Claude SDK now ships as a
        # built-in (e.g. `/clear`) doesn't appear twice in the @-mention
        # autocomplete. SDK-provided commands win the description when
        # both sources list the same name.
        nbi_built_ins = [
            ChatCommand(name='clear', description='Clear chat history'),
        ]
        server_info = self._client.server_info
        if server_info is None:
            return [
                ChatCommand(name='compact', description='Compact chat history'),
                ChatCommand(name='context', description='Show context of the chat'),
                ChatCommand(name='cost', description='Show cost of the chat'),
                ChatCommand(name='clear', description='Clear chat history'),
            ]
        commands_by_name: dict[str, ChatCommand] = {
            cmd.name: cmd for cmd in nbi_built_ins
        }
        for command in server_info.get('commands', []):
            commands_by_name[command['name']] = ChatCommand(
                name=command['name'], description=command['description']
            )
        return list(commands_by_name.values())

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

        try:
            response.stream(ProgressData("Thinking…"))
            result = self._client.query(request, response)
            # query() returns a string when it bails early without dispatching —
            # e.g. the agent isn't connected, a response timeout elapsed, or the
            # worker thread died. Surface it so the user sees why instead of a
            # silent spinner stop.
            if isinstance(result, str) and result:
                response.stream(MarkdownData(f"**Claude agent error:** {result}"))
        except Exception as e:
            log.error(f"Error while handling Claude chat request: {e}", exc_info=True)
            try:
                response.stream(MarkdownData(f"**Error:** {e}"))
            except Exception as stream_err:
                log.debug(f"Could not stream error to client (likely closed websocket): {stream_err}")
        finally:
            try:
                response.finish()
            except Exception as e:
                # Most common cause: the user's websocket closed mid-request.
                # Nothing useful to send back; just keep the task from dying.
                log.warning(f"Could not finalize Claude chat response: {e}")

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
            completion_options = options.copy()
            base_system_prompt = completion_options.get("system_prompt", "")
            system_prompt = self._inject_rules_into_system_prompt(
                base_system_prompt,
                request,
            )
            if system_prompt:
                completion_options["system_prompt"] = system_prompt
            else:
                completion_options.pop("system_prompt", None)
            chat_model.completions(
                messages,
                response=response,
                cancel_token=request.cancel_token,
                options=completion_options,
            )
        except Exception as e:
            log.error(f"Error while handling chat request!\n{e}")
            response.stream(MarkdownData(f"Oops! There was a problem handling chat request. Please try again with a different prompt."))
            response.finish()
    
    def _create_client_options(self) -> ClaudeAgentOptions:
        claude_settings = self._host.nbi_config.claude_settings
        mcp_servers = {}
        jupyter_ui_tools_enabled = ClaudeToolType.JupyterUITools in claude_settings.get('tools', [])
        # By default the Jupyter-UI tools run as an IN-PROCESS sdk MCP server. When
        # 'jupyter_ui_tools_external' is set, they are instead served by an EXTERNAL
        # MCP server (notebook_intelligence.mcp_ui_proxy) named "nbi" in the client's
        # MCP config — required under a managed/enterprise MCP config that rejects
        # dynamically-configured (in-process) servers. Tool names (mcp__nbi__*) and
        # the system prompt are identical either way; only the transport differs.
        # external_ui_tools is the single source of truth shared with the UI-tools
        # relay (extension.UIToolsHandler); see feature_flags.is_external_ui_tools_active.
        external_ui_tools = is_external_ui_tools_active(claude_settings)
        if jupyter_ui_tools_enabled and not external_ui_tools:
            mcp_servers["nbi"] = create_sdk_mcp_server(
                name="nbi", version="1.0.0", tools=JUPYTER_UI_TOOLS
            )
        if jupyter_ui_tools_enabled:
            transport = "external (via managed MCP config)" if external_ui_tools else "in-process"
            log.info(f"Jupyter UI tools: {transport} MCP server 'nbi' ({len(JUPYTER_UI_TOOLS)} tools)")
        else:
            log.debug("Jupyter UI tools: disabled")
        allowed_tools = []
        if jupyter_ui_tools_enabled:
            allowed_tools.extend(["mcp__nbi__list-available-notebook-kernels", "mcp__nbi__create-new-notebook", "mcp__nbi__add-markdown-cell", "mcp__nbi__add-code-cell", "mcp__nbi__get-number-of-cells", "mcp__nbi__get-cell-type-and-source", "mcp__nbi__get-cell-output", "mcp__nbi__set-cell-type-and-source", "mcp__nbi__insert-cell", "mcp__nbi__save-notebook", "mcp__nbi__rename-notebook", "mcp__nbi__open-file-in-jupyter-ui"])
        setting_sources = claude_settings.get('setting_sources')
        chat_model_id = claude_settings.get('chat_model', '').strip()
        if chat_model_id == "":
            chat_model_id = None

        env = {}
        api_key = claude_settings.get('api_key', '')
        if api_key != '':
            env['ANTHROPIC_API_KEY'] = api_key
        base_url = claude_settings.get('base_url', '')
        if base_url != '':
            env['ANTHROPIC_BASE_URL'] = base_url

        env["CLAUDE_CODE_ENTRYPOINT"] = "notebook-intelligence"
        if external_ui_tools:
            # Scope the bridge credential to the only mode that consumes it. Claude
            # Code propagates its env to Bash, hooks, and MCP subprocesses, so do not
            # expose the secret when tools are disabled or use the in-process transport.
            # The proxy sends it in a dedicated header; see get_ui_tools_secret.
            env["NBI_UI_TOOLS_SECRET"] = get_ui_tools_secret()

        continue_conversation = claude_settings.get('continue_conversation', False)

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
            continue_conversation=continue_conversation,
            cli_path=resolve_claude_cli_path()
        )
        return client_options

    def _create_system_prompt(self, jupyter_ui_tools_enabled: bool) -> str:
        return build_claude_system_prompt(
            jupyter_ui_tools_enabled,
            get_jupyter_root_dir(),
        )

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

    def resume_session(self, session_id: str) -> None:
        self._client.resume_session(session_id)

    def update_client_debounced(self):
        if self._update_client_debounced_timer is not None:
            self._update_client_debounced_timer.cancel()
        self._update_client_debounced_timer = asyncio.get_event_loop().create_task(self._update_client_debounced())

    async def _update_client_debounced(self):
        await asyncio.sleep(CLAUDE_AGENT_CLIENT_UPDATE_WAIT_TIME)
        # update_client() does synchronous disconnect/connect with blocking time.sleep
        # inside _send_claude_agent_request. Run it off the event loop thread.
        await asyncio.get_event_loop().run_in_executor(None, self.update_client)
