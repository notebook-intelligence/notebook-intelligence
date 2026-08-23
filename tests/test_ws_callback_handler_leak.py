# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Pin the contract that `_messageCallbackHandlers` does not grow
unbounded for the lifetime of the websocket connection.

Pre-fix: every chat / generate-code / inline-completion request added an
entry keyed by messageId and nothing ever removed them. Across a long
chat session this leaked one response emitter + cancel token per turn.

Fix: the worker thread is wrapped in `_run_request_thread`, which pops
the entry once the request coroutine returns. `on_close` cancels any
requests still in flight at disconnect and clears the whole dict so they
don't pin their state for the worker's lifetime. The cancel matters since
a turn parked on a tool approval no longer self-resolves via the response
timeout (#381).
"""

import asyncio
import json
import threading
from unittest.mock import MagicMock

import pytest

from notebook_intelligence.extension import (
    MessageCallbackHandlers,
    WebsocketCopilotHandler,
)


def _make_handler():
    """Build a WebsocketCopilotHandler without booting the Tornado
    application. Only the dict-management surface is under test, so
    bypassing __init__ is the cleanest approach.
    """
    h = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    h._messageCallbackHandlers = {}
    return h


class TestRunRequestThreadPopsHandler:
    def test_pops_entry_after_coro_returns_normally(self):
        h = _make_handler()
        emitter = MagicMock()
        token = MagicMock()
        h._messageCallbackHandlers["m1"] = MessageCallbackHandlers(emitter, token)

        async def coro():
            return "ok"

        h._run_request_thread(coro(), "m1", emitter)

        assert "m1" not in h._messageCallbackHandlers

    def test_pops_entry_when_coro_raises(self):
        # A worker exception must not leak the entry. The user may keep
        # the chat session open after a failed turn and start another;
        # repeated failures must not grow the dict. The wrapper deliberately
        # logs the original exception without re-raising it through the
        # thread excepthook, which would report the same failure twice.
        h = _make_handler()
        emitter = MagicMock()
        h._messageCallbackHandlers["m1"] = MessageCallbackHandlers(emitter, MagicMock())

        async def boom():
            raise RuntimeError("upstream failure")

        h._run_request_thread(boom(), "m1", emitter)

        assert "m1" not in h._messageCallbackHandlers

    def test_unknown_message_id_is_safe(self):
        # Pop with default short-circuits cleanly so a race where the
        # cleanup runs twice does not crash.
        h = _make_handler()

        async def coro():
            return None

        emitter = MagicMock()
        h._run_request_thread(coro(), "never-registered", emitter)
        # Second call is a no-op.
        h._run_request_thread(coro(), "never-registered", emitter)

    def test_old_request_does_not_remove_replacement_handler(self):
        h = _make_handler()
        old_emitter = MagicMock()
        new_emitter = MagicMock()
        h._messageCallbackHandlers["m1"] = MessageCallbackHandlers(
            new_emitter,
            MagicMock(),
        )

        async def old_coro():
            return None

        h._run_request_thread(old_coro(), "m1", old_emitter)

        assert h._messageCallbackHandlers["m1"].response_emitter is new_emitter

    def test_multiple_concurrent_requests_each_clean_up(self):
        # The realistic concurrency pattern: several inline-completion
        # requests in flight at once. Each thread's cleanup pops only
        # its own entry; no global clear.
        h = _make_handler()
        for i in range(5):
            h._messageCallbackHandlers[f"m{i}"] = MessageCallbackHandlers(
                MagicMock(), MagicMock()
            )

        threads = []
        for i in range(5):
            async def coro():
                return None

            emitter = h._messageCallbackHandlers[f"m{i}"].response_emitter
            t = threading.Thread(
                target=h._run_request_thread, args=(coro(), f"m{i}", emitter)
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()

        assert h._messageCallbackHandlers == {}


class TestDuplicateActiveRequest:
    @pytest.mark.parametrize(
        "request_type",
        ["chat-request", "generate-code", "inline-completion-request"],
    )
    def test_duplicate_id_preserves_existing_request(self, request_type):
        h = _make_handler()
        emitter = MagicMock()
        token = MagicMock()
        h._messageCallbackHandlers["m1"] = MessageCallbackHandlers(emitter, token)

        h.on_message(json.dumps({
            "id": "m1",
            "type": request_type,
            "data": {},
        }))

        token.cancel_request.assert_not_called()
        if request_type == "inline-completion-request":
            emitter.stream_transient_markdown.assert_not_called()
        else:
            emitter.stream_transient_markdown.assert_called_once_with(
                "\n\nA duplicate submission with this message ID was ignored; "
                "the original request is still running."
            )
        emitter.finish.assert_not_called()

    def test_duplicate_id_lookup_uses_a_single_dictionary_read(self):
        class _RacingHandlers(dict):
            def __contains__(self, key):
                present = super().__contains__(key)
                super().pop(key, None)
                return present

        h = _make_handler()
        h._messageCallbackHandlers = _RacingHandlers({
            "m1": MessageCallbackHandlers(MagicMock(), MagicMock())
        })

        # A membership check followed by subscription raises KeyError here.
        # A single get observes the handler without invoking __contains__.
        h.on_message(json.dumps({
            "id": "m1",
            "type": "chat-request",
            "data": {},
        }))


class TestOnCloseClearsHandlers:
    def test_on_close_drops_all_in_flight_entries(self):
        # Long-running requests left in flight at disconnect would
        # otherwise pin their emitter + cancel token until the worker
        # finished. on_close drops everything so the GC can reclaim.
        h = _make_handler()
        for i in range(3):
            h._messageCallbackHandlers[f"m{i}"] = MessageCallbackHandlers(
                MagicMock(), MagicMock()
            )

        h.on_close()

        assert h._messageCallbackHandlers == {}

    def test_on_close_cancels_in_flight_requests(self):
        # A turn parked on a tool approval no longer times out on its own
        # (#381), so on_close must actively cancel in-flight requests; the
        # poll loop then tears down the worker and Claude subprocess.
        h = _make_handler()
        tokens = []
        for i in range(3):
            token = MagicMock()
            tokens.append(token)
            h._messageCallbackHandlers[f"m{i}"] = MessageCallbackHandlers(
                MagicMock(), token
            )

        h.on_close()

        for token in tokens:
            token.cancel_request.assert_called_once_with()
        assert h._messageCallbackHandlers == {}

    def test_on_close_continues_if_a_cancel_raises(self):
        # One bad cancel_token must not prevent the others from cancelling or
        # the dict from clearing.
        h = _make_handler()
        bad = MagicMock()
        bad.cancel_request.side_effect = RuntimeError("boom")
        good = MagicMock()
        h._messageCallbackHandlers["bad"] = MessageCallbackHandlers(MagicMock(), bad)
        h._messageCallbackHandlers["good"] = MessageCallbackHandlers(MagicMock(), good)

        h.on_close()

        bad.cancel_request.assert_called_once_with()
        good.cancel_request.assert_called_once_with()
        assert h._messageCallbackHandlers == {}

    def test_on_close_is_idempotent(self):
        h = _make_handler()
        h.on_close()
        h.on_close()
        assert h._messageCallbackHandlers == {}
