# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Filesystem mentions for natural-language Chatbook cells."""

from __future__ import annotations

import logging
import re
from collections import deque
from pathlib import Path
from typing import Any, Iterable

from notebook_intelligence.api import (
    ChatbookMentionBreadcrumb,
    ChatbookMentionItem,
    ChatbookMentionList,
    ChatbookMentionListRequest,
    ChatbookMentionResolveRequest,
)
from notebook_intelligence.util import (
    get_jupyter_root_dir,
    has_dangerous_text_codepoints,
    safe_jupyter_path,
)

log = logging.getLogger(__name__)

FILES_ROOT = "builtin:files"
EXTENSION_ROOT_PREFIX = "ext:"
DEFAULT_LIMIT = 100
MAX_FILE_CHARS = 16_000
MAX_PROVIDER_CONTEXT_CHARS = 16_000
MAX_DIRECTORY_ITEMS = 100
BUILTIN_SKIPPED_DIRECTORIES = frozenset({"__pycache__", "node_modules"})

MENTION_TOKEN_RE = re.compile(r"(?<![\w@])@([^\s@]+)")


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


def list_chatbook_mentions(
    parent: str = "",
    query: str = "",
    limit: int = DEFAULT_LIMIT,
    skipped_directories: Iterable[str] = (),
    providers: Iterable[Any] = (),
    notebook_path: str = "",
) -> dict:
    """List built-in and extension-provided Chatbook mentions."""
    provider_list = list(providers)
    if not parent:
        response = list_filesystem_mentions()
        items = list(response["items"])
        for provider in provider_list:
            items.append(
                {
                    "label": str(provider.name or provider.id),
                    "value": _provider_root(provider.id),
                    "kind": "root",
                    "hasChildren": True,
                    "description": str(provider.description or ""),
                }
            )
        items.sort(key=lambda item: str(item.get("label") or "").lower())
        return {"items": items[:_normalize_limit(limit)], "breadcrumbs": []}
    if parent == FILES_ROOT:
        return list_filesystem_mentions(
            parent=parent,
            query=query,
            limit=limit,
            skipped_directories=skipped_directories,
        )

    provider_id, provider_parent = _parse_provider_value(parent)
    provider = next(
        (item for item in provider_list if item.id == provider_id), None
    )
    if provider is None:
        return {"items": [], "breadcrumbs": []}
    request = ChatbookMentionListRequest(
        parent=provider_parent,
        query=query,
        limit=_normalize_limit(limit),
        notebook_path=notebook_path,
        working_directory=get_jupyter_root_dir() or "",
    )
    try:
        result = provider.list_mentions(request)
    except Exception as exc:
        log.warning(
            "Chatbook mention provider '%s' failed to list mentions: %s",
            provider_id,
            exc,
        )
        return {
            "items": [],
            "breadcrumbs": [
                {
                    "label": str(provider.name or provider.id),
                    "value": _provider_root(provider.id),
                }
            ],
        }
    return _format_provider_list(
        provider_id, provider.name, result, limit=request.limit
    )


def parse_chatbook_mentions(prompt: str) -> list[tuple[str, str]]:
    """Return unique (kind, relative path) mentions, preserving prompt order."""
    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in MENTION_TOKEN_RE.finditer(prompt or ""):
        value = match.group(1)
        if value.startswith("file:"):
            item = ("file", value.removeprefix("file:"))
        elif value.startswith("dir:"):
            item = ("dir", value.removeprefix("dir:"))
        elif value.startswith(EXTENSION_ROOT_PREFIX):
            item = ("ext", value.removeprefix(EXTENSION_ROOT_PREFIX))
        else:
            continue
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def resolve_chatbook_mentions(
    prompt: str,
    skipped_directories: Iterable[str] = (),
    providers: Iterable[Any] = (),
    notebook_path: str = "",
    notebook_context: dict | None = None,
    cell_id: str = "",
    prompt_hash: str = "",
    context_hash: str = "",
) -> list[dict[str, str]]:
    """Resolve mention tokens into bounded, soft-failing reference context."""
    mentions = parse_chatbook_mentions(prompt)
    if not mentions:
        return []
    root = _root_path()
    provider_by_id = {provider.id: provider for provider in providers}
    current = (notebook_context or {}).get("current") or {}
    cell_index = current.get("index") if isinstance(current, dict) else None
    resolved: list[dict[str, str]] = []
    for kind, relative in mentions:
        token = f"@{kind}:{relative}"
        if kind == "ext":
            provider_id, value = _split_extension_mention(relative)
            provider = provider_by_id.get(provider_id)
            try:
                if provider is None or not value:
                    raise ValueError("unknown mention provider")
                content = provider.resolve_mention(
                    ChatbookMentionResolveRequest(
                        value=value,
                        prompt=prompt,
                        notebook_path=notebook_path,
                        notebook_context=notebook_context,
                        cell_id=cell_id,
                        cell_index=cell_index,
                        prompt_hash=prompt_hash,
                        context_hash=context_hash,
                        working_directory=str(root),
                    )
                )
                content = _truncate_reference(
                    str(content or ""), MAX_PROVIDER_CONTEXT_CHARS
                )
                resolved.append(
                    {
                        "token": token,
                        "kind": "extension",
                        "provider": provider_id,
                        "path": value,
                        "content": content,
                        "available": "true",
                    }
                )
            except Exception:
                resolved.append(
                    {
                        "token": token,
                        "kind": "extension",
                        "provider": provider_id,
                        "path": value,
                        "content": "[unavailable]",
                        "available": "false",
                    }
                )
            continue
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


def _provider_root(provider_id: str) -> str:
    return f"{EXTENSION_ROOT_PREFIX}{provider_id}"


def _parse_provider_value(value: str) -> tuple[str, str]:
    if not value.startswith(EXTENSION_ROOT_PREFIX):
        return "", ""
    return _split_extension_mention(value.removeprefix(EXTENSION_ROOT_PREFIX))


def _split_extension_mention(value: str) -> tuple[str, str]:
    provider_id, separator, provider_value = value.partition(":")
    return provider_id, provider_value if separator else ""


def _normalize_limit(limit: int) -> int:
    return max(1, min(int(limit or DEFAULT_LIMIT), DEFAULT_LIMIT))


def _format_provider_list(
    provider_id: str,
    provider_name: str,
    result: ChatbookMentionList | dict,
    limit: int = DEFAULT_LIMIT,
) -> dict:
    if isinstance(result, dict):
        raw_items = result.get("items") or []
        raw_breadcrumbs = result.get("breadcrumbs") or result.get("breadcrumb") or []
    else:
        raw_items = result.items or []
        raw_breadcrumbs = result.breadcrumbs or []

    items = []
    for raw in raw_items:
        item = raw if isinstance(raw, ChatbookMentionItem) else ChatbookMentionItem(
            label=str(raw.get("label") or ""),
            value=str(raw.get("value") or ""),
            has_children=bool(raw.get("hasChildren", raw.get("has_children", False))),
            kind=str(raw.get("kind") or "reference"),
            description=str(raw.get("description") or ""),
        )
        value = str(item.value or "")
        prefix = _provider_root(provider_id)
        if value and not value.startswith(prefix + ":"):
            value = f"{prefix}:{value}"
        items.append(
            {
                "label": item.label,
                "value": value,
                "kind": item.kind,
                "hasChildren": item.has_children,
                "description": item.description,
            }
        )

    breadcrumbs = [
        {"label": str(provider_name or provider_id), "value": _provider_root(provider_id)}
    ]
    for raw in raw_breadcrumbs:
        crumb = (
            raw
            if isinstance(raw, ChatbookMentionBreadcrumb)
            else ChatbookMentionBreadcrumb(
                label=str(raw.get("label") or ""),
                value=str(raw.get("value") or raw.get("parent") or ""),
            )
        )
        value = crumb.value
        prefix = _provider_root(provider_id)
        if value and not value.startswith(prefix):
            value = f"{prefix}:{value}"
        breadcrumbs.append({"label": crumb.label, "value": value})
    return {
        "items": items[:_normalize_limit(limit)],
        "breadcrumbs": breadcrumbs,
    }


def _truncate_reference(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n...[truncated]"


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
