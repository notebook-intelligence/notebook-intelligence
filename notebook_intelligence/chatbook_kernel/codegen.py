# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Prompt hashing, cache checks, and extraction of a Python cell from nui output."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Optional

from notebook_intelligence.util import extract_llm_generated_code

CHATBOOK_MSG_TYPE = "nbi_chatbook_code"
CHATBOOK_KERNEL_NAME = "chatbook"
STUB_ENV = "NBI_CHATBOOK_STUB"

CELL_CODEGEN_INSTRUCTIONS = """You are generating code for a Chatbook Jupyter notebook cell.

The user's message is a natural-language prompt for THIS cell only.
Return ONLY executable Python that IPython can run in this kernel.
Wrap the code in a single ```python fenced block.
Do not explain, do not apologize, do not write files unless the prompt asks you to.
Do not wrap the snippet in a function unless the prompt asks for one.
Later cells may refer to names you bind here, so use clear variable names.
"""


class ChatbookCodegenError(Exception):
    """Raised when nui did not produce an executable Python cell."""


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def stub_enabled() -> bool:
    return os.environ.get(STUB_ENV, "").strip() in {"1", "true", "TRUE", "yes"}


def stub_python(prompt: str) -> str:
    return f"print({prompt!r})"


def build_run_message(prompt: str) -> str:
    return f"{CELL_CODEGEN_INSTRUCTIONS}\nPrompt:\n{prompt.strip()}\n"


def extract_python_cell(text: str) -> str:
    if not text or not str(text).strip():
        raise ChatbookCodegenError("Code generation produced no executable cell")
    extracted = extract_llm_generated_code(str(text)).strip()
    if not extracted:
        raise ChatbookCodegenError("Code generation produced no executable cell")
    return extracted


def cached_code_if_valid(prompt: str, chatbook_meta: Optional[dict]) -> Optional[str]:
    """Return cached Python when the client sent a matching hash and snippet."""
    meta = chatbook_meta or {}
    cached = meta.get("cachedCode")
    client_hash = meta.get("promptHash")
    if not cached or not isinstance(cached, str):
        return None
    expected = prompt_hash(prompt)
    if client_hash and client_hash != expected:
        return None
    if not client_hash:
        return None
    return cached


def resolve_executable_source(
    prompt: str,
    chatbook_meta: Optional[dict],
    generate,
) -> tuple[str, dict[str, Any]]:
    """Return (python_source, info) using cache or ``generate(prompt, meta)``.

    ``generate`` must return a dict with at least ``generatedCode`` and may
    include ``nuiSessionId`` / ``nuiRunId``.
    """
    cached = cached_code_if_valid(prompt, chatbook_meta)
    if cached is not None:
        meta = chatbook_meta or {}
        return cached, {
            "generatedCode": cached,
            "promptHash": prompt_hash(prompt),
            "nuiSessionId": meta.get("nuiSessionId"),
            "nuiRunId": meta.get("nuiRunId"),
            "cacheHit": True,
        }

    if stub_enabled():
        code = stub_python(prompt)
        return code, {
            "generatedCode": code,
            "promptHash": prompt_hash(prompt),
            "cacheHit": False,
            "stub": True,
        }

    info = generate(prompt, chatbook_meta or {})
    code = extract_python_cell(info.get("generatedCode") or info.get("output") or "")
    info = dict(info)
    info["generatedCode"] = code
    info["promptHash"] = prompt_hash(prompt)
    info["cacheHit"] = False
    return code, info
