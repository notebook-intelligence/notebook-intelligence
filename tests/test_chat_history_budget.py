import logging
from unittest.mock import Mock

import pytest

import notebook_intelligence.chat_history_budget as budget_module
from notebook_intelligence.chat_history_budget import (
    CHAT_INPUT_BUDGET_RATIO,
    CONTEXT_OMISSION_NOTICE,
    TRUNCATION_MARKER,
    budget_chat_messages,
    estimate_message_tokens,
    text_token_count,
    truncate_text,
    warm_tokenizer_encoding,
)


class _CharacterEncoding:
    def encode(self, text, **_kwargs):
        return [ord(character) for character in text]

    def decode(self, tokens):
        return "".join(chr(token) for token in tokens)


class _ImmediateThread:
    def __init__(self, target, args=(), **_kwargs):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


@pytest.fixture(autouse=True)
def stable_tokenizer_state(monkeypatch):
    monkeypatch.setattr(
        budget_module,
        "_tokenizer_encoding",
        _CharacterEncoding(),
    )
    monkeypatch.setattr(budget_module, "_tokenizer_load_attempts", 0)
    monkeypatch.setattr(budget_module, "_tokenizer_load_in_progress", False)
    monkeypatch.setattr(budget_module, "_tokenizer_load_started_at", 0.0)
    monkeypatch.setattr(budget_module, "_tokenizer_last_load_failure_at", 0.0)
    monkeypatch.setattr(budget_module, "_tokenizer_load_generation", 0)
    monkeypatch.setattr(
        budget_module,
        "_tokenizer_fallback_last_logged_at",
        None,
    )


def test_messages_under_budget_are_unchanged():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Question"},
    ]

    assert budget_chat_messages(messages, 4096) == messages


def test_tokenizer_encoding_is_loaded_lazily_and_cached(monkeypatch):
    encoding = _CharacterEncoding()
    encoding_for_model = Mock(return_value=encoding)
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(budget_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        budget_module.tiktoken,
        "encoding_for_model",
        encoding_for_model,
    )

    assert text_token_count("first") == 5
    assert text_token_count("second") == 6
    encoding_for_model.assert_called_once_with("gpt-4o")


def test_tokenizer_load_failure_retries_then_caches_success(
    monkeypatch,
    caplog,
):
    encoding = _CharacterEncoding()
    encoding_for_model = Mock(
        side_effect=[RuntimeError("offline"), encoding]
    )
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(budget_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        budget_module.tiktoken,
        "encoding_for_model",
        encoding_for_model,
    )

    with caplog.at_level(logging.WARNING):
        assert text_token_count("é") == 2
        assert text_token_count("é") == 1
        assert text_token_count("é") == 1

    assert encoding_for_model.call_count == 2
    assert "using the UTF-8 size fallback" in caplog.text


def test_tokenizer_load_retries_are_bounded(monkeypatch):
    encoding_for_model = Mock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(budget_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        budget_module.tiktoken,
        "encoding_for_model",
        encoding_for_model,
    )

    for _ in range(5):
        text_token_count("abcdefgh")

    assert encoding_for_model.call_count == 3


def test_tokenizer_fallback_warning_repeats_after_interval(
    monkeypatch,
    caplog,
):
    monotonic = Mock(return_value=100.0)
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(
        budget_module,
        "_tokenizer_load_attempts",
        budget_module._TOKENIZER_MAX_LOAD_ATTEMPTS,
    )
    monkeypatch.setattr(budget_module.time, "monotonic", monotonic)

    with caplog.at_level(logging.WARNING):
        text_token_count("first")
        text_token_count("second")
        monotonic.return_value = (
            100.0
            + budget_module._TOKENIZER_FALLBACK_WARNING_INTERVAL_SECONDS
        )
        text_token_count("third")

    assert caplog.text.count("Using the UTF-8 size fallback") == 2


def test_tokenizer_load_retries_after_backoff(monkeypatch):
    encoding_for_model = Mock(
        side_effect=[
            RuntimeError("offline"),
            RuntimeError("offline"),
            RuntimeError("offline"),
            _CharacterEncoding(),
        ]
    )
    monotonic = Mock(return_value=100.0)
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(budget_module.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(budget_module.time, "monotonic", monotonic)
    monkeypatch.setattr(
        budget_module.tiktoken,
        "encoding_for_model",
        encoding_for_model,
    )

    for _ in range(4):
        text_token_count("retry")

    assert encoding_for_model.call_count == 3

    monotonic.return_value = (
        100.0 + budget_module._TOKENIZER_RETRY_BACKOFF_SECONDS
    )

    assert text_token_count("retry") == 5
    assert encoding_for_model.call_count == 4
    assert budget_module._tokenizer_load_attempts == 0


def test_stale_tokenizer_load_allows_a_bounded_retry(monkeypatch, caplog):
    thread = Mock()
    thread_class = Mock(return_value=thread)
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(budget_module, "_tokenizer_load_attempts", 1)
    monkeypatch.setattr(budget_module, "_tokenizer_load_in_progress", True)
    monkeypatch.setattr(budget_module, "_tokenizer_load_started_at", 10.0)
    monkeypatch.setattr(budget_module.time, "monotonic", Mock(return_value=41.0))
    monkeypatch.setattr(budget_module.threading, "Thread", thread_class)

    with caplog.at_level(logging.WARNING):
        warm_tokenizer_encoding()

    assert budget_module._tokenizer_load_attempts == 2
    thread.start.assert_called_once_with()
    assert "starting a bounded retry" in caplog.text


def test_three_stale_tokenizer_loads_recover_after_backoff(monkeypatch):
    thread = Mock()
    thread_class = Mock(return_value=thread)
    monotonic = Mock(return_value=41.0)
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(
        budget_module,
        "_tokenizer_load_attempts",
        budget_module._TOKENIZER_MAX_LOAD_ATTEMPTS,
    )
    monkeypatch.setattr(budget_module, "_tokenizer_load_in_progress", True)
    monkeypatch.setattr(budget_module, "_tokenizer_load_started_at", 10.0)
    monkeypatch.setattr(budget_module.time, "monotonic", monotonic)
    monkeypatch.setattr(budget_module.threading, "Thread", thread_class)

    warm_tokenizer_encoding()

    thread.start.assert_not_called()
    assert budget_module._tokenizer_last_load_failure_at == 41.0

    monotonic.return_value = (
        41.0 + budget_module._TOKENIZER_RETRY_BACKOFF_SECONDS
    )
    warm_tokenizer_encoding()

    thread.start.assert_called_once_with()


def test_tokenizer_truncation_uses_a_bounded_number_of_encodes(monkeypatch):
    text = "a" * 1000
    encoding = Mock(wraps=_CharacterEncoding())
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", encoding)

    truncated = truncate_text(text, 100)

    assert truncated.endswith("\n...[truncated]")
    assert encoding.encode.call_count <= 6
    assert sum(call.args[0] == text for call in encoding.encode.call_args_list) == 1


def test_special_token_literals_are_encoded_as_plain_text(monkeypatch):
    class _SpecialTokenEncoding(_CharacterEncoding):
        def encode(self, text, disallowed_special="all"):
            if "<|endoftext|>" in text and disallowed_special != ():
                raise ValueError("disallowed special token")
            return super().encode(text)

    monkeypatch.setattr(
        budget_module,
        "_tokenizer_encoding",
        _SpecialTokenEncoding(),
    )

    assert text_token_count("literal <|endoftext|> text") == 26
    assert truncate_text("<|endoftext|> " * 20, 64).endswith(
        "\n...[truncated]"
    )


def test_tokenizer_warmup_starts_a_daemon_thread(monkeypatch):
    thread = Mock()
    thread_class = Mock(return_value=thread)
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(budget_module.threading, "Thread", thread_class)

    warm_tokenizer_encoding()

    thread_class.assert_called_once_with(
        target=budget_module._load_tokenizer_encoding,
        args=(1,),
        name="nbi-tokenizer-warmup",
        daemon=True,
    )
    thread.start.assert_called_once()


def test_tokenizer_thread_start_failure_uses_utf8_fallback(
    monkeypatch,
    caplog,
):
    thread = Mock()
    thread.start.side_effect = RuntimeError("thread limit")
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(
        budget_module.threading,
        "Thread",
        Mock(return_value=thread),
    )

    with caplog.at_level(logging.WARNING):
        result = text_token_count("é")

    assert result == 2
    assert budget_module._tokenizer_load_in_progress is False
    assert "Could not start the tokenizer warm-up thread" in caplog.text


def test_unexpected_budgeting_error_fails_open(monkeypatch, caplog):
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "Question " * 500},
    ]
    monkeypatch.setattr(
        budget_module,
        "estimate_message_tokens",
        Mock(side_effect=ValueError("malformed message")),
    )

    with caplog.at_level(logging.ERROR):
        result = budget_chat_messages(messages, 128)

    assert result == messages
    assert "sending the original messages" in caplog.text


def test_utf8_fallback_keeps_truncated_history_within_budget(monkeypatch):
    monkeypatch.setattr(budget_module, "_tokenizer_encoding", None)
    monkeypatch.setattr(
        budget_module,
        "_tokenizer_load_attempts",
        budget_module._TOKENIZER_MAX_LOAD_ATTEMPTS,
    )
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "🔥" * 500},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert messages[1] not in result
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        128 * CHAT_INPUT_BUDGET_RATIO
    )


def test_keeps_newest_complete_turns_and_drops_older_turns():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "old question " * 500},
        {"role": "assistant", "content": "old answer " * 500},
        {"role": "user", "content": "recent question"},
        {"role": "assistant", "content": "recent answer"},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 256)

    contents = [message["content"] for message in result]
    assert "old question " * 500 not in contents
    assert "old answer " * 500 not in contents
    assert "recent question" in contents
    assert "recent answer" in contents
    assert contents[-1] == "current question"
    assert CONTEXT_OMISSION_NOTICE in contents[0]


def test_assistant_only_mcp_prompt_is_prioritized_over_old_history():
    mcp_prompt = {"role": "assistant", "content": "Return strict JSON."}
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "old question " * 500},
        {"role": "assistant", "content": "old answer " * 500},
        mcp_prompt,
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(
        messages,
        128,
        mcp_prompt_message_count=1,
    )

    assert mcp_prompt in result
    assert result[-2:] == [mcp_prompt, messages[-1]]
    assert messages[1] not in result
    assert messages[2] not in result


def test_multi_message_mcp_prompt_is_kept_as_an_atomic_sequence():
    mcp_prompt = [
        {"role": "user", "content": "Use the compact schema."},
        {"role": "assistant", "content": "Return strict JSON."},
    ]
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "old question " * 500},
        {"role": "assistant", "content": "old answer " * 500},
        *mcp_prompt,
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(
        messages,
        320,
        mcp_prompt_message_count=2,
    )

    assert result[-3:] == [*mcp_prompt, messages[-1]]
    assert messages[1] not in result
    assert messages[2] not in result


def test_current_context_is_prioritized_and_truncated():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "attached context " * 500},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 256)

    assert result[-1] == messages[-1]
    assert result[-2]["role"] == "user"
    assert result[-2]["content"].endswith(TRUNCATION_MARKER)
    assert len(result[-2]["content"]) < len(messages[-2]["content"])
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        256 * CHAT_INPUT_BUDGET_RATIO
    )


def test_newest_current_context_is_selected_before_older_context():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "stale attachment " * 500},
        {"role": "user", "content": "fresh attachment " * 500},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 256)

    contents = [message["content"] for message in result]
    assert not any(content.startswith("stale") for content in contents)
    assert any(content.startswith("fresh") for content in contents)
    assert contents[-1] == "current question"


def test_messages_under_budget_are_not_normalized():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "assistant", "content": "orphaned answer"},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 4096)

    assert result == messages


def test_pruning_drops_an_orphaned_assistant_message():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "assistant", "content": "orphaned answer " * 500},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert [message["role"] for message in result] == ["system", "user"]
    assert result[-1]["content"] == "current question"


def test_tool_call_turn_is_kept_complete_when_history_is_pruned():
    tool_call = {
        "id": "call-1",
        "type": "function",
        "function": {"name": "inspect_data", "arguments": "{}"},
    }
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "old question " * 500},
        {"role": "assistant", "content": "old answer " * 500},
        {"role": "user", "content": "inspect the dataframe"},
        {"role": "assistant", "content": "", "tool_calls": [tool_call]},
        {"role": "tool", "content": "5 rows", "tool_call_id": "call-1"},
        {"role": "assistant", "content": "The dataframe has 5 rows."},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 512)

    assert [message["role"] for message in result[1:]] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "user",
    ]
    assert result[2]["tool_calls"] == [tool_call]
    assert result[3]["tool_call_id"] == "call-1"


def test_reasoning_content_is_counted_when_history_is_pruned():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "old question"},
        {
            "role": "assistant",
            "content": "old answer",
            "reasoning_content": "private reasoning " * 500,
        },
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert messages[1] not in result
    assert messages[2] not in result
    assert result[-1] == messages[-1]


def test_incomplete_tool_call_turn_is_dropped_as_a_unit():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "old question " * 500},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "inspect"}}],
        },
        {"role": "tool", "content": "partial result", "tool_call_id": "call-1"},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert [message["role"] for message in result] == ["system", "user"]
    assert result[-1]["content"] == "current question"


def test_trailing_tool_message_is_dropped_when_pruning():
    messages = [
        {"role": "system", "content": "Be concise."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "inspect"}}],
        },
        {"role": "user", "content": "current question " * 500},
        {"role": "tool", "content": "late result", "tool_call_id": "call-1"},
    ]

    result = budget_chat_messages(messages, 128)

    assert [message["role"] for message in result] == ["system", "user"]
    assert result[-1]["content"].endswith(TRUNCATION_MARKER)


def test_multimodal_context_uses_conservative_image_estimate_and_is_omitted():
    image_context = {
        "role": "user",
        "content": [
            {"type": "text", "text": "Attached image"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + "a" * 10000},
            },
        ],
    }
    messages = [
        {"role": "system", "content": "Be concise."},
        image_context,
        {"role": "user", "content": "small later context"},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 256)

    assert image_context not in result
    assert result[-2]["content"] == "small later context"
    assert result[-1]["content"] == "current question"


def test_image_screening_estimate_is_more_conservative_than_point_estimate():
    image = {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }

    assert budget_module._content_token_screening_estimate(image) > (
        budget_module._content_token_count(image)
    )


def test_multiple_images_fit_the_conservative_aggregate_bound():
    image_contexts = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"Image {index}"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,image-{index}"
                    },
                },
            ],
        }
        for index in range(8)
    ]
    messages = [
        {"role": "system", "content": "Be concise."},
        *image_contexts,
        {"role": "user", "content": "Compare the retained images."},
    ]

    result = budget_chat_messages(messages, 32_768)

    retained_images = sum(
        1
        for message in result
        if isinstance(message.get("content"), list)
    )
    assert 0 < retained_images < len(image_contexts)
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        32_768 * CHAT_INPUT_BUDGET_RATIO
    )

    assert budget_chat_messages(messages, 131_072) == messages


def test_oversized_multimodal_request_is_reduced_to_bounded_text():
    messages = [
        {"role": "system", "content": "Be concise."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "mandatory request " * 500},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ],
        },
    ]

    result = budget_chat_messages(messages, 128)

    assert result[-1]["content"][0]["type"] == "text"
    bounded_text = result[-1]["content"][0]["text"]
    assert bounded_text.startswith("[Image omitted.]")
    assert bounded_text.endswith(TRUNCATION_MARKER)
    assert all(
        item.get("type") != "image_url"
        for item in result[-1]["content"]
    )
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        128 * CHAT_INPUT_BUDGET_RATIO
    )


def test_oversized_image_only_request_gets_a_bounded_text_placeholder():
    messages = [
        {"role": "system", "content": "Be concise."},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ],
        },
    ]

    result = budget_chat_messages(messages, 128)

    assert result[-1]["content"] == [
        {"type": "text", "text": "[Image omitted.]"}
    ]
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        128 * CHAT_INPUT_BUDGET_RATIO
    )


def test_over_budget_messages_without_a_user_request_keep_bounded_tail(caplog):
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "assistant", "content": "orphaned answer " * 500},
    ]

    with caplog.at_level(logging.WARNING):
        result = budget_chat_messages(messages, 128)

    assert [message["role"] for message in result] == ["system", "assistant"]
    assert result[0]["content"].startswith(
        "[Context omitted to fit model window.]"
    )
    assert result[-1]["content"].endswith(TRUNCATION_MARKER)
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        128 * CHAT_INPUT_BUDGET_RATIO
    )
    assert "no user request" in caplog.text


def test_system_and_latest_user_survive_an_extremely_small_window(caplog):
    messages = [
        {"role": "system", "content": "mandatory system instructions"},
        {"role": "user", "content": "mandatory user request"},
    ]

    with caplog.at_level(logging.WARNING):
        result = budget_chat_messages(messages, 1)

    assert result[0]["content"].startswith("mandatory system instructions")
    assert result[-1] == messages[-1]
    assert "Mandatory chat context requires" in caplog.text


def test_oversized_latest_user_request_is_truncated_as_last_resort():
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": "mandatory request " * 500},
    ]

    result = budget_chat_messages(messages, 128)

    assert "Context omitted to fit model window" in result[0]["content"]
    assert result[-1]["content"].endswith(TRUNCATION_MARKER)
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        128 * CHAT_INPUT_BUDGET_RATIO
    )


def test_truncated_latest_user_accounts_for_message_metadata():
    messages = [
        {"role": "system", "content": "Be concise."},
        {
            "role": "user",
            "name": "named-user",
            "content": "mandatory request " * 500,
        },
    ]

    result = budget_chat_messages(messages, 128)

    assert result[-1]["name"] == "named-user"
    assert result[-1]["content"].endswith(TRUNCATION_MARKER)
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        128 * CHAT_INPUT_BUDGET_RATIO
    )


def test_text_message_truncation_accounts_for_tool_fields():
    message = {
        "role": "assistant",
        "content": "tool explanation " * 500,
        "tool_call_id": "call-1",
        "tool_calls": [
            {
                "id": "call-2",
                "type": "function",
                "function": {"name": "inspect", "arguments": "{}"},
            }
        ],
    }

    result = budget_module._truncate_text_message(message, 128)

    assert result is not None
    assert result["tool_call_id"] == "call-1"
    assert result["tool_calls"] == message["tool_calls"]
    assert estimate_message_tokens(result) <= 128


def test_oversized_system_prompt_is_truncated_to_leave_room_for_user():
    messages = [
        {"role": "system", "content": "mandatory system instructions " * 500},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert result[0]["role"] == "system"
    assert result[0]["content"].startswith(
        "[Context omitted to fit model window.]"
    )
    assert result[-1] == messages[-1]
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        128 * CHAT_INPUT_BUDGET_RATIO
    )


def test_current_context_with_security_delimiters_is_dropped_not_truncated():
    cell_context = (
        "Untrusted output:\n<notebook-cell-0123456789abcdef>\n"
        + "output " * 500
        + "\n</notebook-cell-0123456789abcdef>"
    )
    file_context = (
        "This file was provided as additional context.\n\n"
        "File contents:\n```\n"
        + "file data " * 500
        + "\n```"
    )
    inline_context = (
        "Generate a replacement for this existing code: ```"
        + "selected code " * 500
        + "```"
    )
    messages = [
        {"role": "system", "content": "Be concise."},
        {"role": "user", "content": cell_context},
        {"role": "user", "content": file_context},
        {"role": "user", "content": inline_context},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert all(message.get("content") != cell_context for message in result)
    assert all(message.get("content") != file_context for message in result)
    assert all(message.get("content") != inline_context for message in result)
    assert result[-1] == messages[-1]


def test_required_inline_edit_context_is_kept_when_old_history_is_pruned():
    required_context = {
        "role": "user",
        "content": (
            "Generate a replacement for this existing code: ```"
            "selected = False\n```"
        ),
    }
    messages = [
        {"role": "system", "content": "Modify the selected code."},
        {"role": "user", "content": "old question " * 500},
        {"role": "assistant", "content": "old answer " * 500},
        required_context,
        {"role": "user", "content": "Set selected to true."},
    ]

    result = budget_chat_messages(
        messages,
        256,
        required_context_message_count=1,
    )

    assert result[-2:] == [required_context, messages[-1]]
    assert messages[1] not in result
    assert messages[2] not in result


def test_oversized_required_inline_edit_context_fails_open():
    messages = [
        {"role": "system", "content": "Modify the selected code."},
        {
            "role": "user",
            "content": (
                "Generate a replacement for this existing code: ```"
                + "selected code " * 500
                + "```"
            ),
        },
        {"role": "user", "content": "Set selected to true."},
    ]

    result = budget_chat_messages(
        messages,
        128,
        required_context_message_count=1,
    )

    assert result == messages


def test_multiple_system_messages_keep_omission_notice_when_truncated():
    messages = [
        {"role": "system", "content": "primary instructions " * 500},
        {"role": "system", "content": "secondary instructions " * 500},
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 256)

    assert result[0]["role"] == "system"
    assert result[0]["content"].startswith(
        "[Context omitted to fit model window.]"
    )
    assert result[-1] == messages[-1]


def test_oversized_protected_guidelines_fail_open_without_cutting_rules():
    messages = [
        {
            "role": "system",
            "content": (
                "generic prompt\n\n# Additional Guidelines\n"
                + "mandatory repository rule " * 500
            ),
        },
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert result == messages


def test_protected_guidelines_without_base_prompt_are_not_prefix_truncated():
    messages = [
        {
            "role": "system",
            "content": (
                "# Additional Guidelines\n"
                + "mandatory repository rule " * 500
            ),
        },
        {"role": "user", "content": "current question"},
    ]

    result = budget_chat_messages(messages, 128)

    assert result == messages


def test_short_system_notice_survives_final_mandatory_context_fallback():
    messages = [
        {"role": "system", "content": "mandatory instructions " * 500},
        {
            "role": "user",
            "name": "n" * 80,
            "content": "mandatory request " * 500,
        },
    ]

    result = budget_chat_messages(messages, 256)

    assert result[0] == {
        "role": "system",
        "content": "[Context omitted to fit model window.]",
    }
    assert result[-1]["content"].endswith(TRUNCATION_MARKER)
    assert sum(estimate_message_tokens(message) for message in result) <= int(
        256 * CHAT_INPUT_BUDGET_RATIO
    )


def test_invalid_context_window_leaves_messages_unchanged():
    messages = [{"role": "user", "content": "Question"}]

    assert budget_chat_messages(messages, 0) == messages
    assert budget_chat_messages(messages, "4096") == messages
