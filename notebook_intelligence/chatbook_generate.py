# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Generate Chatbook cell Python using the NBI chat model (not nui)."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from notebook_intelligence.api import ChatResponse, MarkdownData, MarkdownPartData
from notebook_intelligence.chatbook_kernel.codegen import (
    CELL_CODEGEN_INSTRUCTIONS,
    ChatbookCodegenError,
    extract_python_cell,
)


class CollectingChatResponse(ChatResponse):
    """ChatResponse that concatenates streamed text for a single completion."""

    def __init__(self):
        super().__init__()
        self._chunks: list[str] = []
        self._id = uuid.uuid4().hex

    @property
    def message_id(self) -> str:
        return self._id

    def stream(self, data: Any, finish: bool = False) -> None:
        if isinstance(data, MarkdownData) or isinstance(data, MarkdownPartData):
            if data.content:
                self._chunks.append(data.content)
        elif isinstance(data, dict):
            choices = data.get("choices") or []
            delta = (choices[0].get("delta") if choices else {}) or {}
            nbi = delta.get("nbiContent") or {}
            content = nbi.get("content") or delta.get("content") or ""
            if content:
                self._chunks.append(content)
        if finish:
            self.finish()

    def finish(self) -> None:
        return

    async def run_ui_command(self, command: str, args: dict = None) -> dict:
        return {}

    @property
    def text(self) -> str:
        return "".join(self._chunks)


def resolve_chatbook_chat_model(manager: Any):
    """Return the configured NBI chat model, including Claude-mode fallback."""
    model = getattr(manager, "chat_model", None)
    if model is not None:
        return model
    if not getattr(manager, "is_claude_code_mode", False):
        return None
    nbi_config = getattr(manager, "nbi_config", None)
    if nbi_config is None:
        return None
    settings = nbi_config.claude_settings or {}
    from notebook_intelligence.claude import ClaudeChatModel

    model_id = (settings.get("chat_model") or "").strip()
    return ClaudeChatModel(
        model_id,
        settings.get("api_key", None),
        settings.get("base_url", None),
    )


def _truncate(text: str, max_chars: int) -> str:
    value = text or ""
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...[truncated]"


def _format_context_cell(cell: dict, *, is_cursor: bool) -> str:
    index = cell.get("index", "?")
    cell_type = cell.get("cellType") or "code"
    header = f"### Cell {index} ({cell_type})"
    if is_cursor:
        header += " — generate this cell"
    lines = [header]
    prompt = _truncate(str(cell.get("prompt") or "").strip(), 8000)
    generated = _truncate(str(cell.get("generatedCode") or "").strip(), 8000)
    source = _truncate(str(cell.get("source") or "").strip(), 8000)
    output = _truncate(str(cell.get("output") or "").strip(), 4000)
    if prompt:
        lines.extend(["Prompt:", prompt])
    if generated:
        lines.extend(["Generated code:", "```python", generated, "```"])
    if source and source not in {prompt, generated}:
        lines.extend(["Cell source:", source])
    elif source and not prompt and not generated:
        lines.extend(["Cell source:", source])
    if output:
        lines.extend(["Output:", output])
    if len(lines) == 1:
        lines.append("(empty)")
    return "\n".join(lines)


def _format_context_section(cells: Any, empty_label: str) -> str:
    if not isinstance(cells, list) or not cells:
        return empty_label
    parts = []
    for cell in cells:
        if isinstance(cell, dict):
            parts.append(_format_context_cell(cell, is_cursor=False))
    return "\n\n".join(parts) if parts else empty_label


def format_chatbook_user_message(
    prompt: str,
    notebook_context: Optional[dict] = None,
) -> str:
    """Build the user message with PREFIX / CURSOR / SUFFIX notebook context."""
    prompt_text = (prompt or "").strip()
    if not notebook_context or not isinstance(notebook_context, dict):
        return prompt_text
    current = notebook_context.get("current")
    if not isinstance(current, dict):
        current = {"prompt": prompt_text, "cellType": "code"}
    elif prompt_text and not str(current.get("prompt") or "").strip():
        current = {**current, "prompt": prompt_text}
    return "\n".join(
        [
            "Notebook context is split relative to the cursor.",
            "PREFIX = cells above the cursor (already executed in this kernel). "
            "CURSOR = the cell to generate. SUFFIX = cells below the cursor.",
            "Generate Python only for the CURSOR cell prompt.",
            "Reuse PREFIX functions, variables, imports, and results. Do not copy PREFIX code into the CURSOR cell.",
            "",
            "<PREFIX>",
            _format_context_section(
                notebook_context.get("prefix"),
                "(no cells above the cursor)",
            ),
            "</PREFIX>",
            "",
            "<CURSOR>",
            _format_context_cell(current, is_cursor=True),
            "</CURSOR>",
            "",
            "<SUFFIX>",
            _format_context_section(
                notebook_context.get("suffix"),
                "(no cells below the cursor)",
            ),
            "</SUFFIX>",
            "",
            "CURSOR cell prompt:",
            prompt_text,
        ]
    )


def generate_python_with_chat_model(
    chat_model: Any,
    prompt: str,
    cancel_token: Optional[Any] = None,
    notebook_context: Optional[dict] = None,
) -> str:
    collector = CollectingChatResponse()
    messages = [
        {"role": "system", "content": CELL_CODEGEN_INSTRUCTIONS},
        {
            "role": "user",
            "content": format_chatbook_user_message(prompt, notebook_context),
        },
    ]
    result = chat_model.completions(
        messages, response=collector, cancel_token=cancel_token
    )
    text = collector.text
    if not text and isinstance(result, dict):
        choices = result.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}
        text = message.get("content") or ""
    try:
        return extract_python_cell(text)
    except ChatbookCodegenError:
        raise ChatbookCodegenError(
            "Notebook Intelligence produced no executable Python cell"
        ) from None


def generate_chatbook_python(
    manager: Any,
    prompt: str,
    notebook_context: Optional[dict] = None,
) -> str:
    model = resolve_chatbook_chat_model(manager)
    if model is None:
        raise ChatbookCodegenError(
            "No chat model configured in Notebook Intelligence"
        )
    return generate_python_with_chat_model(
        model, prompt, notebook_context=notebook_context
    )
