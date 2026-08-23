# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Chatbook NL execution-mode ranking and kernel run policy."""

from __future__ import annotations

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


def should_execute_generated(mode: str, scan_level: str) -> bool:
    """Whether the kernel should run generated Python in the same NL turn."""
    policy = parse_execution_mode(mode)
    if policy == "auto-run":
        return True
    if policy == "confirm-if-risky":
        return scan_level == "clean"
    return False
