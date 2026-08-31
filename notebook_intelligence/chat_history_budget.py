# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

import tiktoken


CHAT_INPUT_BUDGET_RATIO = 0.8
MESSAGE_OVERHEAD_TOKENS = 4
# Exact vision token cost varies by provider, resolution, and detail mode. Use
# a deliberately high cross-provider allowance rather than the much smaller
# cost of a typical tiled image. Screening is higher again so image-heavy
# requests reliably enter the full budgeting path.
IMAGE_TOKEN_ESTIMATE = 4_096
IMAGE_TOKEN_SCREENING_ESTIMATE = 8_192
CONTEXT_OMISSION_NOTICE = (
    "\n\n[Context note: Some earlier conversation or attached context was "
    "omitted or truncated to fit the model context window.]"
)
SHORT_CONTEXT_OMISSION_NOTICE = "\n\n[Context omitted to fit model window.]"
TRUNCATION_MARKER = "\n...[truncated to fit model context]"

log = logging.getLogger(__name__)
_TOKENIZER_MAX_LOAD_ATTEMPTS = 3
_TOKENIZER_LOAD_TIMEOUT_SECONDS = 30
_TOKENIZER_RETRY_BACKOFF_SECONDS = 300
_TOKENIZER_FALLBACK_WARNING_INTERVAL_SECONDS = 300
_IMAGE_OMISSION_TEXT = "[Image omitted.]"
_ADDITIONAL_GUIDELINES_HEADER = "# Additional Guidelines\n"
_ADDITIONAL_GUIDELINES_MARKER = (
    "\n\n" + _ADDITIONAL_GUIDELINES_HEADER
)
_CELL_OUTPUT_CLOSE_RE = re.compile(r"\n</notebook-cell-[0-9a-f]+>$")
_tokenizer_lock = threading.Lock()
_tokenizer_encoding: Any | None = None
_tokenizer_load_attempts = 0
_tokenizer_load_in_progress = False
_tokenizer_load_started_at = 0.0
_tokenizer_last_load_failure_at = 0.0
_tokenizer_load_generation = 0
_tokenizer_fallback_last_logged_at: float | None = None


def _load_tokenizer_encoding(load_generation: int) -> None:
    global _tokenizer_encoding, _tokenizer_load_in_progress
    global _tokenizer_load_attempts, _tokenizer_load_started_at
    global _tokenizer_last_load_failure_at
    encoding = None
    try:
        encoding = tiktoken.encoding_for_model("gpt-4o")
    except Exception as error:
        log.warning(
            "Could not warm the gpt-4o tokenizer; using the UTF-8 size "
            "fallback until a later retry succeeds: %s",
            error,
        )
    finally:
        with _tokenizer_lock:
            if encoding is not None:
                _tokenizer_encoding = encoding
                _tokenizer_load_attempts = 0
                _tokenizer_last_load_failure_at = 0.0
            elif load_generation == _tokenizer_load_generation:
                _tokenizer_last_load_failure_at = time.monotonic()
            if (
                encoding is not None
                or load_generation == _tokenizer_load_generation
            ):
                _tokenizer_load_in_progress = False
                _tokenizer_load_started_at = 0.0


def warm_tokenizer_encoding() -> None:
    """Warm tokenizer data asynchronously, retrying transient failures."""
    global _tokenizer_load_attempts, _tokenizer_load_in_progress
    global _tokenizer_load_started_at, _tokenizer_load_generation
    global _tokenizer_last_load_failure_at
    stale_load = False
    with _tokenizer_lock:
        if _tokenizer_encoding is not None:
            return
        now = time.monotonic()
        if _tokenizer_load_in_progress:
            if (
                now - _tokenizer_load_started_at
                < _TOKENIZER_LOAD_TIMEOUT_SECONDS
            ):
                return
            stale_load = True
            _tokenizer_load_in_progress = False
            _tokenizer_last_load_failure_at = now
        if _tokenizer_load_attempts >= _TOKENIZER_MAX_LOAD_ATTEMPTS:
            if (
                _tokenizer_last_load_failure_at <= 0
                or now - _tokenizer_last_load_failure_at
                < _TOKENIZER_RETRY_BACKOFF_SECONDS
            ):
                return
            _tokenizer_load_attempts = 0
        _tokenizer_load_attempts += 1
        _tokenizer_load_in_progress = True
        _tokenizer_load_started_at = now
        _tokenizer_load_generation += 1
        load_generation = _tokenizer_load_generation
    if stale_load:
        log.warning(
            "Tokenizer warm-up exceeded %d seconds; starting a bounded retry",
            _TOKENIZER_LOAD_TIMEOUT_SECONDS,
        )
    thread = threading.Thread(
        target=_load_tokenizer_encoding,
        args=(load_generation,),
        name="nbi-tokenizer-warmup",
        daemon=True,
    )
    try:
        thread.start()
    except Exception as error:
        with _tokenizer_lock:
            if load_generation == _tokenizer_load_generation:
                _tokenizer_load_in_progress = False
                _tokenizer_load_started_at = 0.0
                _tokenizer_last_load_failure_at = time.monotonic()
        log.warning(
            "Could not start the tokenizer warm-up thread; using the UTF-8 "
            "size fallback until a later retry succeeds: %s",
            error,
        )


def _get_encoding():
    with _tokenizer_lock:
        encoding = _tokenizer_encoding
    if encoding is None:
        warm_tokenizer_encoding()
        with _tokenizer_lock:
            encoding = _tokenizer_encoding
    return encoding


def _warn_tokenizer_fallback() -> None:
    global _tokenizer_fallback_last_logged_at
    now = time.monotonic()
    with _tokenizer_lock:
        if (
            _tokenizer_fallback_last_logged_at is not None
            and now - _tokenizer_fallback_last_logged_at
            < _TOKENIZER_FALLBACK_WARNING_INTERVAL_SECONDS
        ):
            return
        _tokenizer_fallback_last_logged_at = now
    log.warning(
        "Using the UTF-8 size fallback for chat context budgeting while the "
        "gpt-4o tokenizer is unavailable"
    )


def _fallback_text_token_count(text: str) -> int:
    if text == "":
        return 0
    # One token per byte is intentionally conservative across tokenizers. A
    # three-bytes-per-token estimate can substantially undercount symbol-heavy
    # text and allow the fallback path to exceed the provider context window.
    return len(text.encode("utf-8"))


def _encode_text(encoding, text: str):
    """Encode special-token literals as ordinary user-provided text."""
    return encoding.encode(text, disallowed_special=())


def text_token_count(text: str) -> int:
    # TODO: select provider-specific tokenizers where a stable local encoder
    # exists. The shared estimate is not exact for every provider; the output
    # reserve reduces, but does not eliminate, that cross-tokenizer risk.
    encoding = _get_encoding()
    if encoding is None:
        _warn_tokenizer_fallback()
        return _fallback_text_token_count(text)
    return len(_encode_text(encoding, text))


def truncate_text(
    text: str,
    token_budget: int,
    marker: str = "\n...[truncated]",
) -> str:
    if token_budget <= 0 or text == "":
        return ""

    encoding = _get_encoding()
    if encoding is None:
        _warn_tokenizer_fallback()
        if _fallback_text_token_count(text) <= token_budget:
            return text
        marker_bytes = marker.encode("utf-8")
        available_bytes = token_budget - len(marker_bytes)
        if available_bytes <= 0:
            return ""
        prefix = text.encode("utf-8")[:available_bytes].decode(
            "utf-8", errors="ignore"
        ).rstrip()
        return prefix + marker if prefix else ""

    encoded = _encode_text(encoding, text)
    if len(encoded) <= token_budget:
        return text
    marker_tokens = len(_encode_text(encoding, marker))
    prefix_size = token_budget - marker_tokens
    if prefix_size <= 0:
        return ""

    # BPE merges at the prefix/marker boundary can make the combined candidate
    # a few tokens larger than the sum of its parts. Correct a bounded number
    # of times; if the boundary remains pathological, return the marker alone.
    for _ in range(4):
        prefix = encoding.decode(encoded[:prefix_size]).rstrip()
        candidate = prefix + marker
        candidate_tokens = len(_encode_text(encoding, candidate))
        if candidate_tokens <= token_budget:
            return candidate
        prefix_size -= max(1, candidate_tokens - token_budget)
        if prefix_size <= 0:
            break
    return marker if marker_tokens <= token_budget else ""


def _content_token_count(content: Any) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return text_token_count(content)
    if isinstance(content, list):
        return sum(_content_token_count(item) for item in content)
    if isinstance(content, dict):
        content_type = content.get("type")
        if content_type in {"image", "image_url"} or "image_url" in content:
            return IMAGE_TOKEN_ESTIMATE
        if content_type == "text" and isinstance(content.get("text"), str):
            return text_token_count(content["text"])
        return sum(
            _content_token_count(value)
            for key, value in content.items()
            if key != "type"
        )
    return text_token_count(str(content))


def estimate_message_tokens(message: dict) -> int:
    """Estimate a chat message's input cost without serializing image data."""
    tokens = MESSAGE_OVERHEAD_TOKENS + _content_token_count(
        message.get("content")
    )
    for key, value in message.items():
        if key not in {"role", "content"}:
            tokens += _content_token_count(value)
    return tokens


def _content_token_screening_estimate(content: Any) -> int:
    """Return a cheap conservative estimate without invoking the tokenizer."""
    if content is None:
        return 0
    if isinstance(content, str):
        return len(content.encode("utf-8"))
    if isinstance(content, list):
        return sum(_content_token_screening_estimate(item) for item in content)
    if isinstance(content, dict):
        content_type = content.get("type")
        if content_type in {"image", "image_url"} or "image_url" in content:
            return IMAGE_TOKEN_SCREENING_ESTIMATE
        if content_type == "text" and isinstance(content.get("text"), str):
            return len(content["text"].encode("utf-8"))
        return sum(
            _content_token_screening_estimate(value)
            for key, value in content.items()
            if key != "type"
        )
    return len(str(content).encode("utf-8"))


def _message_token_screening_estimate(message: dict) -> int:
    tokens = MESSAGE_OVERHEAD_TOKENS + _content_token_screening_estimate(
        message.get("content")
    )
    for key, value in message.items():
        if key not in {"role", "content"}:
            tokens += _content_token_screening_estimate(value)
    return tokens


def _truncate_text_message(message: dict, token_budget: int) -> dict | None:
    content = message.get("content")
    if not isinstance(content, str):
        return None

    fixed_fields = message.copy()
    fixed_fields["content"] = ""
    content_budget = token_budget - estimate_message_tokens(fixed_fields)
    truncated_content = truncate_text(
        content,
        content_budget,
        TRUNCATION_MARKER,
    )
    if truncated_content == "":
        return None

    truncated = message.copy()
    truncated["content"] = truncated_content
    return truncated


def _truncate_mandatory_message(
    message: dict,
    token_budget: int,
) -> dict | None:
    if isinstance(message.get("content"), str):
        return _truncate_text_message(message, token_budget)

    content = message.get("content")
    if not isinstance(content, list):
        return None
    text_parts = []
    omitted_non_text = False
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
        elif (
            isinstance(item, dict)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ):
            text_parts.append(item["text"])
        else:
            omitted_non_text = True
    if omitted_non_text:
        text_parts.insert(0, _IMAGE_OMISSION_TEXT)
    elif not text_parts:
        text_parts.append(_IMAGE_OMISSION_TEXT)

    text_message = message.copy()
    text_message["content"] = "\n".join(text_parts)
    truncated = _truncate_text_message(text_message, token_budget)
    if truncated is None:
        return None
    if omitted_non_text and not truncated["content"].startswith(
        _IMAGE_OMISSION_TEXT
    ):
        return None
    truncated["content"] = [
        {"type": "text", "text": truncated["content"]}
    ]
    return truncated


def _has_protected_context_delimiter(message: dict) -> bool:
    content = message.get("content")
    if not isinstance(content, str):
        return False
    # Any fenced context must remain whole: file attachments, inline-edit
    # selections, and legacy selected-cell input/output all use code fences.
    if "```" in content:
        return True
    return _CELL_OUTPUT_CLOSE_RE.search(content) is not None


def _partition_turns(
    messages: list[dict],
) -> tuple[list[list[dict]], list[dict]]:
    """Split complete user turns from the unfinished trailing sequence."""
    turns = []
    current_turn = []
    for message in messages:
        role = message.get("role")
        if role == "user" and any(
            item.get("role") in {"assistant", "tool"}
            for item in current_turn
        ):
            # A new user message cannot continue an unfinished tool-call
            # exchange. Discard that incomplete turn and start a fresh one.
            current_turn = [message]
            continue
        if not current_turn:
            if role != "user":
                continue
            current_turn = [message]
        else:
            current_turn.append(message)

        if role == "assistant" and not message.get("tool_calls"):
            turns.append(current_turn)
            current_turn = []
    return turns, current_turn


def _with_omission_notice(
    system_messages: list[dict],
    notice: str = CONTEXT_OMISSION_NOTICE,
    prepend: bool = False,
) -> list[dict]:
    updated = [message.copy() for message in system_messages]
    if prepend:
        if updated and isinstance(updated[0].get("content"), str):
            updated[0]["content"] = (
                notice.strip() + "\n\n" + updated[0]["content"]
            )
        else:
            updated.insert(0, {"role": "system", "content": notice.strip()})
    elif updated and isinstance(updated[-1].get("content"), str):
        updated[-1]["content"] += notice
    else:
        updated.append({"role": "system", "content": notice.strip()})
    return updated


def _join_protected_system_sections(
    notice: str,
    base_prompt: str,
    guidelines: str,
) -> str:
    return "\n\n".join(
        section for section in (notice, base_prompt, guidelines) if section
    )


def _truncate_system_prompt_preserving_guidelines(
    content: str,
    token_budget: int,
) -> dict | None:
    if content.startswith(_ADDITIONAL_GUIDELINES_HEADER):
        base_prompt = ""
        guidelines = content
    else:
        guidelines_index = content.find(_ADDITIONAL_GUIDELINES_MARKER)
        if guidelines_index < 0:
            return None
        base_prompt = content[:guidelines_index]
        guidelines = content[guidelines_index + len("\n\n"):]
    protected_notice = SHORT_CONTEXT_OMISSION_NOTICE.strip()
    notice = ""
    if base_prompt.startswith(protected_notice):
        notice = protected_notice
        base_prompt = base_prompt[len(protected_notice):].lstrip("\n")
    base_prompt = base_prompt.rstrip()

    protected_content = _join_protected_system_sections(
        notice,
        "",
        guidelines,
    )
    protected_message = {"role": "system", "content": protected_content}
    protected_tokens = estimate_message_tokens(protected_message)
    if protected_tokens > token_budget:
        # Never silently cut repository or workspace rules. The caller will
        # fail open if the protected rules and newest request cannot coexist.
        return protected_message

    separator_tokens = text_token_count("\n\n") if base_prompt else 0
    base_budget = max(
        0,
        token_budget - protected_tokens - separator_tokens,
    )
    for _ in range(4):
        truncated_base = truncate_text(
            base_prompt,
            base_budget,
            TRUNCATION_MARKER,
        )
        candidate = {
            "role": "system",
            "content": _join_protected_system_sections(
                notice,
                truncated_base,
                guidelines,
            ),
        }
        candidate_tokens = estimate_message_tokens(candidate)
        if candidate_tokens <= token_budget:
            return candidate
        base_budget -= max(1, candidate_tokens - token_budget)
        if base_budget <= 0:
            break
    return protected_message


def _truncate_system_messages(
    system_messages: list[dict],
    token_budget: int,
) -> list[dict]:
    if token_budget <= 0:
        return []
    if sum(estimate_message_tokens(message) for message in system_messages) <= token_budget:
        return list(system_messages)

    text_parts = [
        message["content"]
        for message in system_messages
        if isinstance(message.get("content"), str)
    ]
    if not text_parts:
        return []
    combined = {"role": "system", "content": "\n\n".join(text_parts)}
    protected = _truncate_system_prompt_preserving_guidelines(
        combined["content"],
        token_budget,
    )
    if protected is not None:
        return [protected]
    truncated = _truncate_text_message(combined, token_budget)
    protected_notice = SHORT_CONTEXT_OMISSION_NOTICE.strip()
    if (
        combined["content"].startswith(protected_notice)
        and (
            truncated is None
            or not truncated["content"].startswith(protected_notice)
        )
    ):
        notice_message = {"role": "system", "content": protected_notice}
        if estimate_message_tokens(notice_message) <= token_budget:
            return [notice_message]
    return [truncated] if truncated is not None else []


def _budget_chat_messages(
    messages: list[dict],
    context_window: int,
    mcp_prompt_message_count: int = 0,
    required_context_message_count: int = 0,
) -> list[dict]:
    """Fit ask-mode messages to a model window without splitting old turns.

    The system prompt, explicitly required current context, and newest user
    message are mandatory. A current-request MCP prompt sequence is kept
    atomic and prioritized next, followed by other current-turn context that
    may be truncated when it is plain text. Complete prior turns then fill the
    remaining budget from newest to oldest.
    """
    if not isinstance(context_window, int) or context_window <= 0:
        return list(messages)

    input_budget = max(1, int(context_window * CHAT_INPUT_BUDGET_RATIO))
    if sum(
        _message_token_screening_estimate(message) for message in messages
    ) <= input_budget:
        return list(messages)

    token_cache = {}

    def message_tokens(message: dict) -> int:
        cache_key = id(message)
        cached = token_cache.get(cache_key)
        if cached is None or cached[0] is not message:
            cached = (message, estimate_message_tokens(message))
            # Retaining the object alongside its estimate prevents Python
            # from reusing its id for a later omission-notice candidate.
            token_cache[cache_key] = cached
        return cached[1]

    total_tokens = 0
    for message in messages:
        total_tokens += message_tokens(message)
        if total_tokens > input_budget:
            break
    else:
        # Budgeting is not a general history normalizer. Preserve unusual but
        # accepted provider sequences byte-for-byte until pruning is required.
        return list(messages)

    system_end = 0
    while (
        system_end < len(messages)
        and messages[system_end].get("role") == "system"
    ):
        system_end += 1
    system_messages = messages[:system_end]
    conversation = messages[system_end:]

    latest_user_index = next(
        (
            index
            for index in range(len(conversation) - 1, -1, -1)
            if conversation[index].get("role") == "user"
        ),
        None,
    )
    if latest_user_index is None:
        log.warning(
            "Reducing over-budget chat history while preserving system "
            "instructions because it has no user request"
        )
        if not conversation:
            truncated_system_messages = _truncate_system_messages(
                _with_omission_notice(
                    system_messages,
                    SHORT_CONTEXT_OMISSION_NOTICE,
                    prepend=True,
                ),
                input_budget,
            )
            return truncated_system_messages or list(messages)

        newest_message = conversation[-1]
        candidate_system_messages = _with_omission_notice(
            system_messages,
            SHORT_CONTEXT_OMISSION_NOTICE,
            prepend=True,
        )
        newest_message_tokens = message_tokens(newest_message)
        minimum_newest_budget = MESSAGE_OVERHEAD_TOKENS + text_token_count(
            TRUNCATION_MARKER
        )
        newest_message_budget = min(
            input_budget,
            newest_message_tokens
            if newest_message_tokens <= input_budget
            else max(minimum_newest_budget, input_budget // 2),
        )
        candidate_system_messages = _truncate_system_messages(
            candidate_system_messages,
            max(0, input_budget - newest_message_budget),
        )
        remaining_for_newest = input_budget - sum(
            message_tokens(message) for message in candidate_system_messages
        )
        truncated_message = (
            newest_message
            if newest_message_tokens <= remaining_for_newest
            else _truncate_mandatory_message(
                newest_message,
                remaining_for_newest,
            )
        )
        if truncated_message is not None:
            return [*candidate_system_messages, truncated_message]
        return list(messages)

    latest_user = conversation[latest_user_index]
    trailing_message_count = len(conversation) - latest_user_index - 1
    if trailing_message_count:
        log.warning(
            "Dropping %d trailing non-user chat messages while pruning; "
            "the newest user request must terminate an API chat request",
            trailing_message_count,
        )
    preceding_messages = conversation[:latest_user_index]
    if (
        not isinstance(mcp_prompt_message_count, int)
        or isinstance(mcp_prompt_message_count, bool)
        or mcp_prompt_message_count < 0
    ):
        mcp_prompt_message_count = 0
    mcp_prompt_message_count = min(
        mcp_prompt_message_count,
        len(preceding_messages),
    )
    if mcp_prompt_message_count:
        mcp_prompt_messages = preceding_messages[-mcp_prompt_message_count:]
        historical_messages = preceding_messages[:-mcp_prompt_message_count]
    else:
        mcp_prompt_messages = []
        historical_messages = preceding_messages

    if (
        not isinstance(required_context_message_count, int)
        or isinstance(required_context_message_count, bool)
        or required_context_message_count < 0
    ):
        required_context_message_count = 0
    required_context_message_count = min(
        required_context_message_count,
        len(historical_messages),
    )
    if required_context_message_count:
        required_context_messages = historical_messages[
            -required_context_message_count:
        ]
        historical_messages = historical_messages[
            :-required_context_message_count
        ]
    else:
        required_context_messages = []

    complete_turns, trailing_sequence = _partition_turns(
        historical_messages
    )
    current_context = (
        trailing_sequence
        if all(message.get("role") == "user" for message in trailing_sequence)
        else []
    )
    base_mandatory_messages = [
        *system_messages,
        *required_context_messages,
        latest_user,
    ]
    base_mandatory_tokens = sum(
        message_tokens(message)
        for message in base_mandatory_messages
    )
    if base_mandatory_tokens > input_budget:
        if any(
            _ADDITIONAL_GUIDELINES_HEADER
            in str(message.get("content", ""))
            for message in system_messages
        ):
            log.warning(
                "Application instructions and injected guidelines cannot both "
                "fit the %d-token input budget; sending the original request "
                "instead of dropping either instruction layer",
                input_budget,
            )
            return list(messages)
        if required_context_messages:
            log.warning(
                "Required current-request context cannot fit the %d-token "
                "input budget; sending the original request so the provider "
                "returns a visible context-limit error instead of generating "
                "without required source context",
                input_budget,
            )
            return list(messages)
        candidate_system_messages = _with_omission_notice(
            system_messages,
            SHORT_CONTEXT_OMISSION_NOTICE,
            prepend=True,
        )
        non_user_mandatory_tokens = sum(
            message_tokens(message)
            for message in candidate_system_messages
        )
        truncated_latest_user = _truncate_mandatory_message(
            latest_user,
            max(0, input_budget - non_user_mandatory_tokens),
        )
        if truncated_latest_user is None:
            # When configured system instructions consume the whole model
            # window, reserve half the input budget for the actual request and
            # truncate both sides. Extremely tiny windows may still be unable
            # to fit even the per-message protocol overhead; those fall open.
            minimum_user_budget = MESSAGE_OVERHEAD_TOKENS + text_token_count(
                _IMAGE_OMISSION_TEXT + "\n" + TRUNCATION_MARKER
            )
            latest_user_tokens = message_tokens(latest_user)
            user_budget = min(
                input_budget,
                latest_user_tokens
                if latest_user_tokens <= input_budget
                else max(minimum_user_budget, input_budget // 2),
            )
            candidate_system_messages = _truncate_system_messages(
                candidate_system_messages,
                max(0, input_budget - user_budget),
            )
            remaining_for_user = input_budget - sum(
                message_tokens(message)
                for message in candidate_system_messages
            )
            truncated_latest_user = _truncate_mandatory_message(
                latest_user,
                remaining_for_user,
            )
            if truncated_latest_user is None:
                has_protected_guidelines = any(
                    _ADDITIONAL_GUIDELINES_HEADER
                    in str(message.get("content", ""))
                    for message in candidate_system_messages
                )
                if not has_protected_guidelines:
                    notice_message = {
                        "role": "system",
                        "content": SHORT_CONTEXT_OMISSION_NOTICE.strip(),
                    }
                    candidate_system_messages = (
                        [notice_message]
                        if message_tokens(notice_message) < input_budget
                        else []
                    )
                    truncated_latest_user = _truncate_mandatory_message(
                        latest_user,
                        input_budget
                        - sum(
                            message_tokens(message)
                            for message in candidate_system_messages
                        ),
                    )
        if truncated_latest_user is not None:
            log.warning(
                "Truncating mandatory system or user context because it "
                "requires %d estimated tokens for a %d-token input budget",
                base_mandatory_tokens,
                input_budget,
            )
            system_messages = candidate_system_messages
            latest_user = truncated_latest_user
        else:
            log.warning(
                "Mandatory chat context requires %d estimated tokens, "
                "exceeding the %d-token input budget; the system prompt "
                "leaves too little room to truncate the newest user request",
                base_mandatory_tokens,
                input_budget,
            )
    else:
        for notice in (
            CONTEXT_OMISSION_NOTICE,
            SHORT_CONTEXT_OMISSION_NOTICE,
        ):
            candidate_system_messages = _with_omission_notice(
                system_messages,
                notice,
            )
            candidate_mandatory_messages = [
                *candidate_system_messages,
                *required_context_messages,
                latest_user,
            ]
            if sum(
                message_tokens(message)
                for message in candidate_mandatory_messages
            ) <= input_budget:
                system_messages = candidate_system_messages
                break

    mandatory_messages = [
        *system_messages,
        *required_context_messages,
        latest_user,
    ]
    remaining_budget = max(
        0,
        input_budget
        - sum(
            message_tokens(message)
            for message in mandatory_messages
        ),
    )

    selected_mcp_prompt = []
    if mcp_prompt_messages:
        mcp_prompt_tokens = sum(
            message_tokens(message) for message in mcp_prompt_messages
        )
        if mcp_prompt_tokens <= remaining_budget:
            selected_mcp_prompt = mcp_prompt_messages
            remaining_budget -= mcp_prompt_tokens
        else:
            log.warning(
                "Dropping a %d-message MCP prompt requiring %d estimated "
                "tokens because only %d tokens remain after mandatory "
                "system and user context",
                len(mcp_prompt_messages),
                mcp_prompt_tokens,
                remaining_budget,
            )

    selected_context = []
    for message in reversed(current_context):
        context_tokens = message_tokens(message)
        if context_tokens <= remaining_budget:
            selected_context.append(message)
            remaining_budget -= context_tokens
            continue

        # File fences and nonced cell-output envelopes contain untrusted data.
        # Drop them whole if they do not fit; prefix truncation would remove
        # the closing delimiter that keeps their payload clearly bounded.
        if _has_protected_context_delimiter(message):
            continue
        truncated = _truncate_text_message(message, remaining_budget)
        if truncated is not None:
            selected_context.append(truncated)
            remaining_budget -= message_tokens(truncated)
        continue
    selected_context.reverse()

    selected_turns = []
    for turn in reversed(complete_turns):
        turn_tokens = sum(message_tokens(message) for message in turn)
        if turn_tokens > remaining_budget:
            break
        selected_turns.append(turn)
        remaining_budget -= turn_tokens
    selected_turns.reverse()

    return [
        *system_messages,
        *(message for turn in selected_turns for message in turn),
        *selected_context,
        *required_context_messages,
        *selected_mcp_prompt,
        latest_user,
    ]


def budget_chat_messages(
    messages: list[dict],
    context_window: int,
    mcp_prompt_message_count: int = 0,
    required_context_message_count: int = 0,
) -> list[dict]:
    """Fail open if estimation encounters malformed data or runtime errors."""
    try:
        return _budget_chat_messages(
            messages,
            context_window,
            mcp_prompt_message_count,
            required_context_message_count,
        )
    except Exception:
        log.exception(
            "Could not budget chat history; sending the original messages"
        )
        return list(messages)
