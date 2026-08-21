# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Filesystem mentions for natural-language Chatbook cells."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import Iterable

from notebook_intelligence.util import (
    get_jupyter_root_dir,
    has_dangerous_text_codepoints,
    safe_jupyter_path,
)

FILES_ROOT = "builtin:files"
DEFAULT_LIMIT = 100
MAX_FILE_CHARS = 16_000
MAX_DIRECTORY_ITEMS = 100
BUILTIN_SKIPPED_DIRECTORIES = frozenset({"__pycache__", "node_modules"})

MENTION_TOKEN_RE = re.compile(r"(?<![\w@])@(file|dir):([^\s@]+)")


def _root_path() -> Path:
    root = get_jupyter_root_dir()
    if not root:
        raise RuntimeError("Jupyter root directory is not set")
    return Path(root).expanduser().resolve()


def _relative_display(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _safe_relative_path(
    value: str, skipped_directories: Iterable[str] = ()
) -> Path:
    parts = Path(value).parts
    skipped = set(BUILTIN_SKIPPED_DIRECTORIES)
    skipped.update(str(name) for name in skipped_directories if str(name))
    if (
        not value
        or Path(value).is_absolute()
        or has_dangerous_text_codepoints(value)
        or any(part.startswith(".") for part in parts)
        or any(part in skipped for part in parts)
    ):
        raise ValueError("unsafe mention path")
    return safe_jupyter_path(value)


def list_filesystem_mentions(
    parent: str = "",
    query: str = "",
    limit: int = DEFAULT_LIMIT,
    skipped_directories: Iterable[str] = (),
) -> dict:
    """List the Files root or bounded workspace file/directory mention items."""
    if not parent:
        return {
            "items": [
                {
                    "label": "Files & folders",
                    "value": FILES_ROOT,
                    "kind": "root",
                    "hasChildren": True,
                }
            ],
            "breadcrumbs": [],
        }
    if parent != FILES_ROOT:
        return {"items": [], "breadcrumbs": []}

    root = _root_path()
    search = query.strip().lower()
    cap = max(1, min(int(limit or DEFAULT_LIMIT), DEFAULT_LIMIT))
    skipped = set(BUILTIN_SKIPPED_DIRECTORIES)
    skipped.update(str(name) for name in skipped_directories if str(name))
    queue: deque[Path] = deque([root])
    found: list[tuple[bool, str, dict]] = []

    while queue and len(found) < cap:
        directory = queue.popleft()
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda path: (not path.is_dir(), path.name.lower()),
            )
        except OSError:
            continue
        for path in entries:
            name = path.name
            if (
                name.startswith(".")
                or path.is_symlink()
                or has_dangerous_text_codepoints(name)
            ):
                continue
            try:
                is_dir = path.is_dir()
            except OSError:
                continue
            if is_dir and name in skipped:
                continue
            try:
                relative = _relative_display(path, root)
            except ValueError:
                continue
            if is_dir:
                queue.append(path)
            if search and search not in name.lower() and search not in relative.lower():
                continue
            kind = "dir" if is_dir else "file"
            label = f"{relative}/" if is_dir else relative
            found.append(
                (
                    is_dir,
                    relative.lower(),
                    {
                        "label": label,
                        "value": f"{kind}:{relative}",
                        "kind": kind,
                        "hasChildren": False,
                    },
                )
            )
            if len(found) >= cap:
                break

    found.sort(key=lambda item: (not item[0], item[1]))
    return {
        "items": [item[2] for item in found[:cap]],
        "breadcrumbs": [{"label": "Files & folders", "value": FILES_ROOT}],
    }


def parse_chatbook_mentions(prompt: str) -> list[tuple[str, str]]:
    """Return unique (kind, relative path) mentions, preserving prompt order."""
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in MENTION_TOKEN_RE.finditer(prompt or ""):
        item = (match.group(1), match.group(2))
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def resolve_chatbook_mentions(
    prompt: str, skipped_directories: Iterable[str] = ()
) -> list[dict[str, str]]:
    """Resolve mention tokens into bounded, soft-failing reference context."""
    mentions = parse_chatbook_mentions(prompt)
    if not mentions:
        return []
    root = _root_path()
    resolved: list[dict[str, str]] = []
    for kind, relative in mentions:
        token = f"@{kind}:{relative}"
        try:
            path = _safe_relative_path(relative, skipped_directories)
            display = _relative_display(path, root)
            if kind == "file":
                content = _read_text_file(path)
            else:
                content = _list_directory(path)
            resolved.append(
                {
                    "token": token,
                    "kind": kind,
                    "path": display,
                    "content": content,
                    "available": "true",
                }
            )
        except (OSError, RuntimeError, UnicodeError, ValueError):
            resolved.append(
                {
                    "token": token,
                    "kind": kind,
                    "path": relative,
                    "content": "[unavailable]",
                    "available": "false",
                }
            )
    return resolved


def _read_text_file(path: Path) -> str:
    if not path.is_file():
        raise ValueError("not a file")
    raw = path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("binary file")
    text = raw.decode("utf-8")
    if len(text) > MAX_FILE_CHARS:
        return text[:MAX_FILE_CHARS] + "\n...[truncated]"
    return text


def _list_directory(path: Path) -> str:
    if not path.is_dir():
        raise ValueError("not a directory")
    entries = []
    for child in sorted(
        path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
    ):
        if child.name.startswith(".") or has_dangerous_text_codepoints(child.name):
            continue
        suffix = "/" if child.is_dir() else ""
        entries.append(child.name + suffix)
        if len(entries) >= MAX_DIRECTORY_ITEMS:
            entries.append("...[truncated]")
            break
    return "\n".join(entries) if entries else "(empty directory)"
