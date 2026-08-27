# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Chatbook Jupyter kernel: natural-language cell source, Python execution."""

from typing import Any

__all__ = ["ChatbookKernel"]


def __getattr__(name: str) -> Any:
    if name == "ChatbookKernel":
        from .kernel import ChatbookKernel

        return ChatbookKernel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
