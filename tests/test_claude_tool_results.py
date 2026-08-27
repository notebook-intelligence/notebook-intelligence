import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import notebook_intelligence.claude as claude
from notebook_intelligence.claude import (
    APPROX_BYTES_PER_TOOL_RESULT_TOKEN,
    CLAUDE_TOOL_RESULT_TRUNCATION_MARKER,
    MIN_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS,
    tool_text_response,
)


def _response_text(result: dict) -> str:
    return result["content"][0]["text"]


def test_short_tool_result_is_returned_verbatim():
    result = tool_text_response("small result", max_output_tokens=16)

    assert _response_text(result) == "small result"
    assert "is_error" not in result


def test_oversized_tool_result_preserves_beginning_and_end():
    text = "HEAD" + ("x" * 500) + "TAIL"
    max_output_tokens = 32

    result = _response_text(
        tool_text_response(text, max_output_tokens=max_output_tokens)
    )

    assert result.startswith("HEAD")
    assert result.endswith("TAIL")
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in result
    assert f"{len(text.encode('utf-8'))} UTF-8 bytes" in result
    assert "request a narrower result." in result
    assert len(result.encode("utf-8")) <= (
        max_output_tokens * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


def test_multibyte_tool_result_is_valid_utf8_and_stays_within_budget():
    text = "start:" + ("🙂" * 100) + ":end"
    max_output_tokens = 24

    result = _response_text(
        tool_text_response(text, max_output_tokens=max_output_tokens)
    )

    assert result.startswith("start:")
    assert result.endswith(":end")
    assert result.encode("utf-8").decode("utf-8") == result
    assert len(result.encode("utf-8")) <= (
        max_output_tokens * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


def test_truncated_error_result_preserves_error_signal():
    result = tool_text_response(
        "failure:" + ("x" * 500),
        is_error=True,
        max_output_tokens=24,
    )

    assert result["is_error"] is True
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in _response_text(result)


def test_non_string_result_is_stringified_before_bounding():
    result = tool_text_response({"value": 42}, max_output_tokens=16)

    assert _response_text(result) == "{'value': 42}"


def test_structured_result_is_lossless_when_truncation_is_requested():
    value = {"payload": "x" * 500}

    result = tool_text_response(value, truncate=True, max_output_tokens=16)

    assert _response_text(result) == str(value)
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER not in _response_text(result)


def test_structured_result_is_lossless_by_default():
    text = '{"source":"' + ("x" * 500) + '"}'

    result = tool_text_response(text)

    assert _response_text(result) == text


def test_tiny_budget_returns_complete_minimal_marker():
    result = tool_text_response(
        "x" * 100,
        max_output_tokens=3,
    )

    assert _response_text(result) == "[100B cut]"


def test_one_token_budget_still_signals_truncation():
    result = tool_text_response("x" * 100, max_output_tokens=1)

    assert _response_text(result) == "…"


@pytest.mark.parametrize("budget", ["256", 1.5, True, False])
def test_non_integer_programmatic_budget_is_rejected(budget):
    with pytest.raises(TypeError, match="must be an integer"):
        tool_text_response("x" * 100, max_output_tokens=budget)


def test_negative_programmatic_budget_normalizes_to_positive_minimum():
    text = "HEAD" + ("x" * 5_000) + "TAIL"

    result = _response_text(tool_text_response(text, max_output_tokens=-1))

    assert result.startswith("HEAD")
    assert result.endswith("TAIL")
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in result
    assert len(result.encode("utf-8")) <= (
        MIN_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS
        * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


def test_zero_budget_restores_unbounded_output():
    text = "x" * 500

    result = tool_text_response(text, max_output_tokens=0)

    assert _response_text(result) == text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0),
        ("00", 0),
        ("+0", 0),
        ("-1", MIN_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS),
        ("1", MIN_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS),
        ("999999", claude.MAX_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS),
    ],
)
def test_tool_result_env_reserves_only_explicit_zero_as_unbounded(
    monkeypatch,
    raw,
    expected,
):
    monkeypatch.setenv("NBI_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS", raw)

    assert claude._tool_result_max_output_tokens_from_env() == expected


def test_tool_result_env_uses_documented_default_when_unset(monkeypatch):
    monkeypatch.delenv(
        "NBI_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS",
        raising=False,
    )

    assert claude._tool_result_max_output_tokens_from_env() == (
        claude.DEFAULT_CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS
    )


def test_utf8_size_matches_encoded_length():
    text = "ascii e\N{LATIN SMALL LETTER E WITH ACUTE} \N{SLIGHTLY SMILING FACE}"

    assert claude._utf8_size(text) == len(text.encode("utf-8"))


def test_ascii_normalization_skips_regex_scan(monkeypatch):
    monkeypatch.setattr(
        claude.re,
        "sub",
        lambda *_args, **_kwargs: pytest.fail("regex scan should be skipped"),
    )

    assert claude._normalize_utf8("plain ascii") == "plain ascii"


def test_lone_surrogates_are_replaced_before_truncation():
    text = "HEAD" + ("\ud800" * 1_000) + "TAIL"
    max_output_tokens = 256

    result = _response_text(
        tool_text_response(text, max_output_tokens=max_output_tokens)
    )

    assert result.startswith("HEAD")
    assert result.endswith("TAIL")
    assert "\N{REPLACEMENT CHARACTER}" in result
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in result
    assert len(result.encode("utf-8")) <= (
        max_output_tokens * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


@pytest.mark.parametrize("raw", [None, "  "])
def test_bounded_int_env_uses_default_when_unset_or_blank(monkeypatch, raw):
    if raw is None:
        monkeypatch.delenv("NBI_TEST_TOOL_RESULT_LIMIT", raising=False)
    else:
        monkeypatch.setenv("NBI_TEST_TOOL_RESULT_LIMIT", raw)

    assert claude._bounded_int_env(
        "NBI_TEST_TOOL_RESULT_LIMIT", 100, 10, 200
    ) == 100


def test_bounded_int_env_warns_and_uses_default_for_invalid_value(
    monkeypatch,
    caplog,
):
    monkeypatch.setenv("NBI_TEST_TOOL_RESULT_LIMIT", "not-an-int")

    with caplog.at_level(logging.WARNING):
        result = claude._bounded_int_env(
            "NBI_TEST_TOOL_RESULT_LIMIT", 100, 10, 200
        )

    assert result == 100
    assert "expected an integer" in caplog.text


@pytest.mark.parametrize(("raw", "expected"), [("1", 10), ("999", 200)])
def test_bounded_int_env_clamps_out_of_range_values(
    monkeypatch,
    raw,
    expected,
):
    monkeypatch.setenv("NBI_TEST_TOOL_RESULT_LIMIT", raw)

    assert claude._bounded_int_env(
        "NBI_TEST_TOOL_RESULT_LIMIT", 100, 10, 200
    ) == expected


def test_get_cell_output_uses_default_tool_result_cap(monkeypatch):
    monkeypatch.setattr(claude, "CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS", 32)
    response = SimpleNamespace(
        run_ui_command=AsyncMock(
            return_value="HEAD" + ("x" * 500) + "TAIL"
        )
    )
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(claude.get_cell_output.handler({"cell_index": 3}))
    text = _response_text(result)

    response.run_ui_command.assert_awaited_once_with(
        "notebook-intelligence:get-cell-output",
        {"cellIndex": 3},
    )
    assert text.startswith("HEAD")
    assert text.endswith("TAIL")
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in text
    assert len(text.encode("utf-8")) <= (
        claude.CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS
        * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


def test_get_cell_output_bounds_unexpected_structured_result(monkeypatch):
    monkeypatch.setattr(claude, "CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS", 32)
    response = SimpleNamespace(
        run_ui_command=AsyncMock(return_value={"payload": "x" * 50_000})
    )
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(claude.get_cell_output.handler({"cell_index": 3}))
    text = _response_text(result)

    assert "is_error" not in result
    assert text.startswith("{'payload': '")
    assert text.endswith("'}")
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in text
    assert len(text.encode("utf-8")) <= (
        claude.CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS
        * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


def test_get_cell_output_preserves_none_as_bounded_text(monkeypatch):
    response = SimpleNamespace(run_ui_command=AsyncMock(return_value=None))
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(claude.get_cell_output.handler({"cell_index": 3}))

    assert _response_text(result) == "None"


def test_get_cell_output_supports_resumable_character_ranges(monkeypatch):
    response = SimpleNamespace(run_ui_command=AsyncMock(return_value="abcdefghij"))
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(
        claude.get_cell_output.handler(
            {"cell_index": 3, "offset": 3, "limit": 4}
        )
    )

    assert _response_text(result) == (
        "defg\n\n[cell output characters 3-7 of 10; "
        "request offset=7 to continue.]"
    )


def test_get_cell_output_schema_exposes_optional_range_arguments():
    schema = claude._tool_json_schema(claude.get_cell_output.input_schema)

    assert schema["required"] == ["cell_index"]
    assert schema["properties"]["offset"] == {
        "type": "integer",
        "minimum": 0,
    }
    assert schema["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
    }


@pytest.mark.parametrize(
    "args",
    [
        {"cell_index": 3, "offset": -1},
        {"cell_index": 3, "offset": True},
        {"cell_index": 3, "limit": 0},
        {"cell_index": 3, "limit": 1.5},
    ],
)
def test_get_cell_output_rejects_invalid_page_arguments(monkeypatch, args):
    response = SimpleNamespace(run_ui_command=AsyncMock())
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(claude.get_cell_output.handler(args))

    assert result["is_error"] is True
    response.run_ui_command.assert_not_awaited()


def test_get_cell_source_remains_lossless(monkeypatch):
    source_result = {"type": "code", "source": "x" * 50_000}
    response = SimpleNamespace(
        run_ui_command=AsyncMock(return_value=source_result)
    )
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(
        claude.get_cell_type_and_source.handler({"cell_index": 4})
    )

    assert _response_text(result) == str(source_result)
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER not in _response_text(result)


def test_terminal_output_uses_default_tool_result_cap(monkeypatch, tmp_path):
    monkeypatch.setattr(claude, "CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS", 32)
    monkeypatch.setattr(claude, "safe_jupyter_path", lambda _: tmp_path)
    response = SimpleNamespace(
        run_ui_command=AsyncMock(
            return_value="HEAD" + ("x" * 500) + "TAIL"
        )
    )
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(
        claude.run_command_in_jupyter_terminal.handler(
            {"command": "printf test", "working_directory": "."}
        )
    )
    text = _response_text(result)

    response.run_ui_command.assert_awaited_once_with(
        "notebook-intelligence:run-command-in-terminal",
        {"command": "printf test", "cwd": str(tmp_path)},
    )
    assert text.startswith("HEAD")
    assert text.endswith("TAIL")
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in text
    assert len(text.encode("utf-8")) <= (
        claude.CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS
        * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


def test_terminal_validation_error_uses_default_tool_result_cap(monkeypatch):
    monkeypatch.setattr(claude, "CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS", 32)

    def reject_path(_):
        raise ValueError("HEAD" + ("x" * 500) + "TAIL")

    monkeypatch.setattr(claude, "safe_jupyter_path", reject_path)

    result = asyncio.run(
        claude.run_command_in_jupyter_terminal.handler(
            {"command": "printf test", "working_directory": "invalid"}
        )
    )
    text = _response_text(result)

    assert result["is_error"] is True
    assert text.startswith("Error: HEAD")
    assert text.endswith("TAIL")
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in text
    assert len(text.encode("utf-8")) <= (
        claude.CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS
        * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )


def test_terminal_structured_output_is_stringified_and_bounded(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(claude, "CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS", 32)
    monkeypatch.setattr(claude, "safe_jupyter_path", lambda _: tmp_path)
    response = SimpleNamespace(
        run_ui_command=AsyncMock(return_value={"payload": "x" * 50_000})
    )
    monkeypatch.setattr(claude, "get_current_response", lambda: response)

    result = asyncio.run(
        claude.run_command_in_jupyter_terminal.handler(
            {"command": "printf test", "working_directory": "."}
        )
    )
    text = _response_text(result)

    assert text.startswith("{'payload': '")
    assert text.endswith("'}")
    assert CLAUDE_TOOL_RESULT_TRUNCATION_MARKER in text
    assert len(text.encode("utf-8")) <= (
        claude.CLAUDE_TOOL_RESULT_MAX_OUTPUT_TOKENS
        * APPROX_BYTES_PER_TOOL_RESULT_TOKEN
    )
