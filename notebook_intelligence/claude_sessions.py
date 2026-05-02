# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Discovery of Claude Code session transcripts.

Claude Code persists each conversation as a line-delimited JSON file at::

    ~/.claude/projects/<cwd-encoded>/<session-id>.jsonl

where ``<cwd-encoded>`` is the session cwd with path separators replaced by
dashes (e.g. ``/Users/me/proj`` -> ``-Users-me-proj``).

This module reads those files for the current Jupyter working directory and
returns lightweight metadata (id, timestamps, first user message preview) so
the UI can offer a "resume previous session" picker.

Each line in a transcript is a JSON object. User messages look like::

    {"type": "user", "message": {"role": "user", "content": "..."}, ...}

``content`` can be a string (the common case) or a list of content blocks in
the Anthropic format. Other line types (assistant replies, tool events,
snapshots) are ignored for preview purposes.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PREVIEW_MAX_CHARS = 160

# Hard cap on lines scanned per file while looking for the first user
# message. Transcripts can grow very large, and in practice the first user
# prompt is on the first few lines.
_MAX_LINES_SCANNED = 200


@dataclass
class ClaudeSessionInfo:
    """Lightweight metadata for a Claude Code session transcript."""

    session_id: str
    path: str
    modified_at: float
    created_at: float
    preview: str
    cwd: str = ""


def encode_cwd(cwd: str) -> str:
    """Encode a filesystem path the way Claude Code names its project dirs.

    Claude Code replaces every path separator with a dash, so
    ``/Users/me/proj`` becomes ``-Users-me-proj``. We normalize the path
    first so trailing slashes or ``..`` segments don't produce surprising
    directory names.
    """
    normalized = os.path.normpath(os.path.abspath(cwd))
    return normalized.replace(os.sep, "-")


def get_sessions_dir(cwd: str, claude_home: Optional[str] = None) -> Path:
    """Return the directory containing session transcripts for ``cwd``.

    ``claude_home`` defaults to ``~/.claude`` but can be overridden (useful
    for tests and for the ``CLAUDE_CONFIG_DIR`` convention).
    """
    home = Path(claude_home) if claude_home else Path.home() / ".claude"
    return home / "projects" / encode_cwd(cwd)


def list_sessions(
    cwd: str,
    claude_home: Optional[str] = None,
) -> list[ClaudeSessionInfo]:
    """List Claude sessions for ``cwd``, newest first.

    Returns an empty list if the project directory doesn't exist or contains
    no transcripts. Corrupt or unreadable files are skipped with a log
    warning rather than raising.
    """
    sessions_dir = get_sessions_dir(cwd, claude_home=claude_home)
    if not sessions_dir.is_dir():
        return []

    sessions: list[ClaudeSessionInfo] = []
    for entry in sessions_dir.iterdir():
        # Only consider top-level .jsonl files; skip nested subagent dirs.
        if not entry.is_file() or entry.suffix != ".jsonl":
            continue
        info = _read_session_info(entry)
        if info is not None:
            sessions.append(info)

    sessions.sort(key=lambda s: s.modified_at, reverse=True)
    return sessions


def _read_session_info(path: Path) -> Optional[ClaudeSessionInfo]:
    """Read metadata from a single transcript file.

    Returns ``None`` if the file is empty or has no user messages.
    """
    try:
        stat = path.stat()
    except OSError as exc:
        log.warning("Could not stat Claude session file %s: %s", path, exc)
        return None

    preview = ""

    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in itertools.islice(fh, _MAX_LINES_SCANNED):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # Tolerate the occasional partial write at the tail
                    # of an in-progress session.
                    continue
                if not _is_user_message(obj):
                    continue
                preview = _extract_preview(obj)
                break
    except OSError as exc:
        log.warning("Could not read Claude session file %s: %s", path, exc)
        return None

    if not preview:
        # Empty or non-conversation file (e.g. only snapshots). Skip it so
        # the picker doesn't show a meaningless row.
        return None

    return ClaudeSessionInfo(
        session_id=path.stem,
        path=str(path),
        modified_at=stat.st_mtime,
        created_at=stat.st_ctime,
        preview=preview,
    )


def _is_user_message(obj: dict) -> bool:
    if obj.get("type") != "user":
        return False
    message = obj.get("message")
    if not isinstance(message, dict):
        return False
    # Guard against tool-result "user" envelopes; we only want real prompts.
    content = message.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return any(
            isinstance(block, dict) and block.get("type") == "text"
            for block in content
        )
    return False


def _extract_preview(obj: dict) -> str:
    """Extract a short preview string from a user message line."""
    content = obj.get("message", {}).get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
        text = "\n".join(parts)

    # Collapse whitespace so multi-line prompts render as a single row.
    text = " ".join(text.split())
    if len(text) > _PREVIEW_MAX_CHARS:
        text = text[: _PREVIEW_MAX_CHARS - 1].rstrip() + "\u2026"
    return text


def list_all_sessions(
    claude_home: Optional[str] = None,
) -> list[ClaudeSessionInfo]:
    """List all resumable Claude sessions across all projects, newest first.

    Reads ``~/.claude/history.jsonl`` \u2014 Claude Code writes one entry per
    prompt, so every session that appears there can actually be resumed.
    Each session is enriched with its ``cwd`` (project path) so callers
    can run ``claude --resume <id>`` from the correct directory.

    Sessions are de-duplicated by session ID and sorted by most recent
    activity. Only sessions whose ``.jsonl`` transcript file still exists
    in ``~/.claude/projects/`` are returned.
    """
    home = Path(claude_home) if claude_home else Path.home() / ".claude"
    history_path = home / "history.jsonl"

    if not history_path.exists():
        return []

    # Build index: session_id -> .jsonl path for existence check.
    projects_dir = home / "projects"
    session_to_jsonl: dict[str, Path] = {}
    if projects_dir.is_dir():
        for project_dir in projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl_file in project_dir.glob("*.jsonl"):
                session_to_jsonl[jsonl_file.stem] = jsonl_file

    # Read history.jsonl: group entries by session_id, track first/last timestamps.
    # Structure: {session_id: {"project": str, "first_ts": int, "last_ts": int, "preview": str}}
    seen: dict[str, dict] = {}
    try:
        with history_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                session_id = obj.get("sessionId")
                if not session_id:
                    continue
                project = obj.get("project", "")
                ts = int(obj.get("timestamp", 0))
                display = obj.get("display", "")
                if session_id not in seen:
                    seen[session_id] = {
                        "project": project,
                        "first_ts": ts,
                        "last_ts": ts,
                        "preview": display,
                    }
                else:
                    if ts < seen[session_id]["first_ts"]:
                        seen[session_id]["first_ts"] = ts
                        seen[session_id]["preview"] = display
                    if ts > seen[session_id]["last_ts"]:
                        seen[session_id]["last_ts"] = ts
    except OSError as exc:
        log.warning("Could not read Claude history file %s: %s", history_path, exc)
        return []

    sessions: list[ClaudeSessionInfo] = []
    for session_id, data in seen.items():
        # Only include sessions whose transcript file still exists.
        if session_id not in session_to_jsonl:
            continue
        jsonl_path = session_to_jsonl[session_id]
        try:
            stat = jsonl_path.stat()
        except OSError:
            continue
        preview = data["preview"]
        if len(preview) > _PREVIEW_MAX_CHARS:
            preview = preview[: _PREVIEW_MAX_CHARS - 1].rstrip() + "\u2026"
        sessions.append(ClaudeSessionInfo(
            session_id=session_id,
            path=str(jsonl_path),
            modified_at=data["last_ts"] / 1000.0,
            created_at=stat.st_ctime,
            preview=preview,
            cwd=data["project"],
        ))

    sessions.sort(key=lambda s: s.modified_at, reverse=True)
    return sessions
