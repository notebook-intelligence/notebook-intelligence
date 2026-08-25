"""Regression tests for bounded, resumable built-in cell output."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import notebook_intelligence.built_in_toolsets as toolsets


def _get_cell_output(value, **kwargs):
    response = SimpleNamespace(run_ui_command=AsyncMock(return_value=value))
    result = asyncio.run(
        toolsets.get_cell_output._tool_function(
            cell_index=2,
            response=response,
            **kwargs,
        )
    )
    return result, response


def test_large_builtin_cell_output_is_bounded():
    result, response = _get_cell_output("HEAD" + ("x" * 50_000) + "TAIL")

    assert result.startswith("HEAD")
    assert result.endswith("TAIL")
    assert "[cell output truncated;" in result
    assert len(result.encode("utf-8")) <= (
        toolsets.DEFAULT_CELL_OUTPUT_MAX_OUTPUT_TOKENS
        * toolsets.APPROX_BYTES_PER_OUTPUT_TOKEN
    )
    response.run_ui_command.assert_awaited_once_with(
        "notebook-intelligence:get-cell-output",
        {"cellIndex": 2},
    )


def test_builtin_cell_output_supports_resumable_ranges():
    result, _response = _get_cell_output("abcdefghij", offset=3, limit=4)

    assert result == (
        "defg\n\n[cell output characters 3-7 of 10; "
        "request offset=7 to continue.]"
    )


def test_builtin_cell_output_rejects_invalid_ranges_before_ui_request():
    result, response = _get_cell_output("unused", offset=-1)

    assert result == "Error: offset must be a non-negative integer"
    response.run_ui_command.assert_not_awaited()
