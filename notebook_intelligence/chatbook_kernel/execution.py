# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Chatbook NL execution-mode ranking and kernel run policy."""

from __future__ import annotations

import os
from typing import Optional

CHATBOOK_EXECUTION_MODES = (
    "always-confirm",
    "confirm-if-risky",
    "auto-run",
)

DEFAULT_CHATBOOK_EXECUTION_MODE = "always-confirm"
DEFAULT_CHATBOOK_MAX_EXECUTION_MODE = "auto-run"

_RANK = {name: index for index, name in enumerate(CHATBOOK_EXECUTION_MODES)}


def parse_execution_mode(
    value: Optional[str],
    default: str = DEFAULT_CHATBOOK_EXECUTION_MODE,
) -> str:
    text = (value or "").strip()
    if text == "generate-only":
        text = "always-confirm"
    if text in _RANK:
        return text
    return default if default in _RANK else DEFAULT_CHATBOOK_EXECUTION_MODE


def clamp_execution_mode(mode: str, max_mode: str) -> str:
    chosen = parse_execution_mode(mode)
    cap = parse_execution_mode(max_mode, DEFAULT_CHATBOOK_MAX_EXECUTION_MODE)
    if _RANK[chosen] > _RANK[cap]:
        return cap
    return chosen


def admin_max_execution_mode() -> str:
    """Ceiling from ``NBI_CHATBOOK_MAX_EXECUTION_MODE`` (kernel process env)."""
    env = os.environ.get("NBI_CHATBOOK_MAX_EXECUTION_MODE", "").strip()
    return parse_execution_mode(env, DEFAULT_CHATBOOK_MAX_EXECUTION_MODE)


def effective_execution_mode(
    client_mode: Optional[str],
    stored_mode: Optional[str] = None,
    max_mode: Optional[str] = None,
) -> str:
    """Less-permissive of client and stored, then clamped to the admin cap."""
    cap = parse_execution_mode(
        max_mode if max_mode is not None else admin_max_execution_mode(),
        DEFAULT_CHATBOOK_MAX_EXECUTION_MODE,
    )
    combined = clamp_execution_mode(client_mode or "", stored_mode or cap)
    return clamp_execution_mode(combined, cap)


def should_execute_generated(mode: str, scan_level: str) -> bool:
    """Whether the kernel should run generated Python in the same NL turn."""
    policy = parse_execution_mode(mode)
    if policy == "auto-run":
        return True
    if policy == "confirm-if-risky":
        return scan_level == "clean"
    return False
