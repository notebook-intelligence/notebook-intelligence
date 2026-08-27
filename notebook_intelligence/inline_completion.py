# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Shared prompts for NBI inline (ghost-text) completions."""

from __future__ import annotations

import re

CHATBOOK_INLINE_LANGUAGES = frozenset({"chatbook", "text/x-chatbook"})

INLINE_COMPLETION_SYSTEM_PROMPT = """You are a code completion assistant. Your task is to generate intelligent autocomplete suggestions for the code at the cursor position for given language and active file type. This is not an interactive session, don't ask for clarifying questions, always generate a suggestion. Don't include any explanations for your response, just generate the code. Don't return any thinking or reasoning, just generate the code. You are given a code snippet with a prefix and a suffix. You need to generate a suggestion for the code that fits best in place of <CURSOR/>. You should return only the code that fits best in place of <CURSOR/>. You should provide multiline code if needed. Enclose the code in triple backticks, just return the code in language. You should not return any other text, just the code. DO NOT INCLUDE THE PREFIX OR SUFFIX IN THE RESPONSE. .ipynb files are Jupyter notebook files and for notebook files, you generate suggestions for a cell within the notebook. A cell can be a code cell with code or a markdown cell with markdown text. If the language is markdown, only return markdown text. If you need to install a Python package within a notebook cell code (for .ipynb files), use %pip install <package_name> instead of !pip install <package_name>. Follow the tags very carefully for proper spacing and indentations."""

CHATBOOK_INLINE_COMPLETION_SYSTEM_PROMPT = """You complete Chatbook notebook cells.

A Chatbook cell is a natural-language prompt for an AI, not source code.
Suggest a continuation of that prompt in the same natural language as PREFIX/SUFFIX (English unless the existing text is clearly another language).
Do not suggest Python, JavaScript, SQL, or any programming-language code.
Do not wrap the suggestion in a markdown code fence.
This is not interactive; always return a suggestion with no explanation or questions.
Return only the text that belongs at <CURSOR/>. Do not repeat PREFIX or SUFFIX.
"""


def is_chatbook_inline_language(language: str | None) -> bool:
    return (language or "").strip().lower() in CHATBOOK_INLINE_LANGUAGES


def copilot_inline_language(language: str | None) -> str:
    """GitHub Copilot extra.language: prose engine for Chatbook, else as-is."""
    if is_chatbook_inline_language(language):
        return "markdown"
    return language or "python"


def inline_completion_system_prompt(language: str | None) -> str:
    if is_chatbook_inline_language(language):
        return CHATBOOK_INLINE_COMPLETION_SYSTEM_PROMPT
    return INLINE_COMPLETION_SYSTEM_PROMPT


def inline_completion_user_prompt(
    prefix: str, suffix: str, language: str | None, filename: str | None
) -> str:
    file_label = filename or ""
    lang = language or ""
    if is_chatbook_inline_language(language):
        return f"""Generate a single natural-language continuation at the cursor. The cell prompt is between <CODE> tags; <CURSOR/> is where the new text goes. Write in the same language as PREFIX/SUFFIX (English unless that text is another language). Do not write code. Active file is {file_label}.

<CODE><PREFIX>{prefix}</PREFIX><CURSOR/><SUFFIX>{suffix}</SUFFIX></CODE>
"""
    return f"""Generate a single suggestion that fits best in place of cursor. The code is below in between <CODE> tags and <CURSOR/> is the placeholder for the code to be filled in. Current language is {lang} and the active file is {file_label}.

<CODE><PREFIX>{prefix}</PREFIX><CURSOR/><SUFFIX>{suffix}</SUFFIX></CODE>
"""


def chatbook_inline_prefix_hint() -> str:
    return (
        "# Chatbook cell: complete the natural-language prompt in the same "
        "language as the text (English unless the text is another language). "
        "Do not generate programming code.\n"
    )


def extract_inline_completion(text: str, language: str | None = None) -> str:
    raw = text or ""
    if not is_chatbook_inline_language(language):
        return raw
    stripped = raw.strip()
    fenced = re.match(r"^```(?:\w+)?\n?(.*?)```\s*$", stripped, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    return stripped
