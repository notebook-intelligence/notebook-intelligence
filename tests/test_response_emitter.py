"""Regression coverage for WebsocketCopilotResponseEmitter's IOLoop dispatch.

#264: streaming, finishing, and run_ui_command used to call
``self.websocket_handler.write_message`` directly. From the Claude /
MCP worker threads, that path mutates Tornado's bytearray write buffer
across IOLoop boundaries and raises
``BufferError: Existing exports of data: object cannot be re-sized``.

These tests pin the contract that the emitter marshals every write
through the IOLoop's ``call_soon_threadsafe`` so the bug can't regress.
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from notebook_intelligence import extension
from notebook_intelligence.api import (
    BackendMessageType,
    UICommandCancelledError,
    UserInputCancelledError,
)
from notebook_intelligence.extension import (
    WebsocketCopilotHandler,
    WebsocketCopilotResponseEmitter,
)


def _make_emitter() -> tuple[WebsocketCopilotResponseEmitter, MagicMock, MagicMock]:
    websocket = MagicMock()
    chat_history = MagicMock()
    io_loop = MagicMock()
    io_loop.asyncio_loop = MagicMock()
    with patch.object(extension.tornado.ioloop.IOLoop, "current", return_value=io_loop):
        emitter = WebsocketCopilotResponseEmitter(
            chatId="cid",
            messageId="mid",
            websocket_handler=websocket,
            chat_history=chat_history,
        )
    return emitter, websocket, io_loop


def test_stream_dispatches_through_ioloop_not_direct_write():
    emitter, websocket, io_loop = _make_emitter()
    payload = MagicMock()
    payload.to_dict.return_value = {"content": "hi", "type": "markdown"}

    emitter.stream(payload)

    # The direct write would mutate Tornado's bytearray from a worker
    # thread and trip BufferError. Assert we did NOT take that path.
    websocket.write_message.assert_not_called()
    # And we DID hand off via call_soon_threadsafe.
    call_soon = io_loop.asyncio_loop.call_soon_threadsafe
    assert call_soon.call_count == 1
    callback, message = call_soon.call_args.args
    callback(message)
    assert message["id"] == "mid"
    assert message["type"] == BackendMessageType.StreamMessage


def test_finish_dispatches_through_ioloop_not_direct_write():
    emitter, websocket, io_loop = _make_emitter()

    emitter.finish()

    emitter.chat_history.add_message.assert_not_called()
    websocket.write_message.assert_not_called()
    call_soon = io_loop.asyncio_loop.call_soon_threadsafe
    assert call_soon.call_count == 1
    callback, message = call_soon.call_args.args
    callback(message)
    assert message["type"] == BackendMessageType.StreamEnd


def test_send_async_path_targets_websocket_write_message():
    # run_ui_command shares the _send_async helper with stream/finish.
    # Rather than spin up an event loop to exercise the awaitable, pin
    # the helper directly: it must route through call_soon_threadsafe
    # with a callback that performs the guarded websocket write.
    emitter, websocket, io_loop = _make_emitter()

    payload = {"type": "demo", "data": {"x": 1}}
    emitter._send_async(payload)

    websocket.write_message.assert_not_called()
    call_soon = io_loop.asyncio_loop.call_soon_threadsafe
    callback, message = call_soon.call_args.args
    callback(message)
    websocket.write_message.assert_called_once_with(payload)


def test_send_async_drops_message_when_websocket_is_closed():
    emitter, websocket, io_loop = _make_emitter()
    websocket.ws_connection = None

    emitter._send_async({"type": "demo"})
    callback, message = io_loop.asyncio_loop.call_soon_threadsafe.call_args.args
    callback(message)

    websocket.write_message.assert_not_called()


def test_send_async_swallows_websocket_closed_during_write():
    emitter, websocket, io_loop = _make_emitter()
    websocket.write_message.side_effect = extension.websocket.WebSocketClosedError()

    emitter._send_async({"type": "demo"})
    callback, message = io_loop.asyncio_loop.call_soon_threadsafe.call_args.args
    callback(message)


def test_finish_is_idempotent_and_warns_once_on_late_streams(caplog):
    emitter, _websocket, io_loop = _make_emitter()

    emitter.finish()
    emitter.finish()
    emitter.stream({"choices": [{"delta": {"content": "late"}}]})
    emitter.stream({"choices": [{"delta": {"content": "later"}}]})

    calls = io_loop.asyncio_loop.call_soon_threadsafe.call_args_list
    assert len(calls) == 1
    assert calls[0].args[1]["type"] == BackendMessageType.StreamEnd
    assert caplog.text.count("Ignoring stream data after response mid finished") == 1


def test_finish_normalizes_non_string_raw_deltas():
    emitter, _websocket, io_loop = _make_emitter()
    emitter.stream({
        "choices": [{
            "delta": {
                "content": 123,
                "reasoning_content": ["reasoning"],
            }
        }]
    })

    emitter.finish()

    history_message = emitter.chat_history.add_message.call_args.args[1]
    assert history_message["content"] == "123"
    assert history_message["reasoning_content"] == "['reasoning']"
    assert io_loop.asyncio_loop.call_soon_threadsafe.call_args_list[-1].args[1][
        "type"
    ] == BackendMessageType.StreamEnd


def test_finish_schedules_terminal_message_when_history_persistence_fails(caplog):
    emitter, _websocket, io_loop = _make_emitter()
    emitter.streamed_contents = ["content"]
    emitter.chat_history.add_message.side_effect = RuntimeError("history failed")

    emitter.finish()

    assert emitter._finished is True
    assert io_loop.asyncio_loop.call_soon_threadsafe.call_args.args[1][
        "type"
    ] == BackendMessageType.StreamEnd
    assert "Failed to persist response mid" in caplog.text


def test_finish_closes_when_terminal_loop_is_already_closed():
    emitter, _websocket, _io_loop = _make_emitter()
    emitter._io_loop.asyncio_loop.call_soon_threadsafe.side_effect = RuntimeError(
        "loop unavailable"
    )

    emitter.finish()

    assert emitter._finished is True
    assert emitter._finishing is False


def test_request_thread_converts_uncaught_exception_to_one_terminal_response():
    emitter, _websocket, io_loop = _make_emitter()
    handler = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    handler._messageCallbackHandlers = {}

    async def _explode():
        raise RuntimeError("boom")

    handler._run_request_thread(_explode(), "mid", emitter)

    messages = [
        call.args[1]
        for call in io_loop.asyncio_loop.call_soon_threadsafe.call_args_list
    ]
    assert [message["type"] for message in messages] == [
        BackendMessageType.StreamMessage,
        BackendMessageType.StreamEnd,
    ]
    emitter.chat_history.add_message.assert_not_called()
    assert "mid" not in handler._messageCallbackHandlers


def test_terminal_notice_failure_does_not_replace_original_exception(caplog):
    emitter, _websocket, _io_loop = _make_emitter()
    emitter.stream_transient_markdown = MagicMock(
        side_effect=RuntimeError("event loop closed")
    )
    handler = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    handler._messageCallbackHandlers = {}

    async def _explode():
        raise ValueError("original failure")

    handler._run_request_thread(_explode(), "mid", emitter)

    assert "Failed to stream terminal notice for request mid" in caplog.text


def test_request_thread_preserves_partial_output_before_terminal_error():
    emitter, _websocket, io_loop = _make_emitter()
    handler = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    handler._messageCallbackHandlers = {}

    async def _stream_then_explode():
        emitter.stream({"choices": [{"delta": {"content": "partial"}}]})
        raise RuntimeError("boom")

    handler._run_request_thread(_stream_then_explode(), "mid", emitter)

    emitter.chat_history.add_message.assert_called_once()
    history_message = emitter.chat_history.add_message.call_args.args[1]
    assert history_message["content"] == "partial"
    messages = [
        call.args[1]
        for call in io_loop.asyncio_loop.call_soon_threadsafe.call_args_list
    ]
    assert [message["type"] for message in messages] == [
        BackendMessageType.StreamMessage,
        BackendMessageType.StreamMessage,
        BackendMessageType.StreamEnd,
    ]


def test_request_thread_finishes_empty_response_without_adding_history():
    emitter, _websocket, io_loop = _make_emitter()
    handler = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    handler._messageCallbackHandlers = {}

    async def _complete_without_output():
        return None

    handler._run_request_thread(_complete_without_output(), "mid", emitter)

    emitter.chat_history.add_message.assert_not_called()
    messages = [
        call.args[1]
        for call in io_loop.asyncio_loop.call_soon_threadsafe.call_args_list
    ]
    assert [message["type"] for message in messages] == [
        BackendMessageType.StreamEnd,
    ]


def test_run_ui_command_after_finish_raises_cancellation_error(caplog):
    emitter, _websocket, io_loop = _make_emitter()
    emitter.finish()
    io_loop.asyncio_loop.call_soon_threadsafe.reset_mock()

    with pytest.raises(
        UICommandCancelledError,
        match="Cannot run UI command 'late-command' after response mid finished",
    ):
        asyncio.run(emitter.run_ui_command("late-command"))
    assert "Rejecting UI command" in caplog.text
    io_loop.asyncio_loop.call_soon_threadsafe.assert_not_called()


def test_ui_command_wait_accepts_none_as_a_completed_result():
    emitter, _websocket, _io_loop = _make_emitter()

    async def _wait_for_none():
        task = asyncio.create_task(
            emitter.wait_for_run_ui_command_response(emitter, "callback")
        )
        await asyncio.sleep(0)
        emitter.on_run_ui_command_response({
            "callback_id": "callback",
            "result": None,
        })
        return await asyncio.wait_for(task, timeout=1)

    assert asyncio.run(_wait_for_none()) is None
    assert emitter.run_ui_command_response_signal._listeners == []


def test_ui_command_wait_is_woken_when_websocket_closes():
    emitter, _websocket, _io_loop = _make_emitter()
    handler = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    token = MagicMock()
    handler._messageCallbackHandlers = {
        "mid": extension.MessageCallbackHandlers(emitter, token)
    }

    async def _wait_for_close():
        task = asyncio.create_task(
            emitter.wait_for_run_ui_command_response(emitter, "callback")
        )
        await asyncio.sleep(0)
        handler.on_close()
        return await asyncio.wait_for(task, timeout=1)

    with pytest.raises(
        UICommandCancelledError,
        match="WebSocket closed before the UI command completed",
    ):
        asyncio.run(_wait_for_close())
    token.cancel_request.assert_called_once_with()
    assert emitter.run_ui_command_response_signal._listeners == []


def test_ui_command_wait_observes_cancellation_that_happened_before_registration():
    emitter, _websocket, _io_loop = _make_emitter()
    emitter.cancel_pending_ui_commands("request already cancelled")

    with pytest.raises(
        UICommandCancelledError,
        match="request already cancelled",
    ):
        asyncio.run(
            emitter.wait_for_run_ui_command_response(emitter, "callback")
        )

    assert emitter._pending_ui_commands == {}


def test_ui_command_cancellation_between_registration_and_send_is_not_lost():
    emitter, _websocket, _io_loop = _make_emitter()
    emitter._send_async = MagicMock(
        side_effect=lambda _message: emitter.cancel_pending_ui_commands(
            "cancelled during dispatch"
        )
    )

    with pytest.raises(
        UICommandCancelledError,
        match="cancelled during dispatch",
    ):
        asyncio.run(emitter.run_ui_command("test-command"))

    assert emitter._pending_ui_commands == {}


def test_ui_command_response_during_send_is_not_lost():
    emitter, _websocket, _io_loop = _make_emitter()

    def _respond_immediately(message):
        emitter.on_run_ui_command_response({
            "callback_id": message["data"]["callback_id"],
            "result": None,
        })

    emitter._send_async = MagicMock(side_effect=_respond_immediately)

    assert asyncio.run(emitter.run_ui_command("test-command")) is None
    assert emitter._pending_ui_commands == {}


def test_user_input_wait_accepts_none_after_registration_before_await():
    emitter, _websocket, _io_loop = _make_emitter()

    async def _respond_before_await():
        pending = emitter.prepare_chat_user_input("callback")
        emitter.on_user_input({"callback_id": "callback", "data": None})
        return await emitter.wait_for_chat_user_input(
            emitter,
            "callback",
            pending,
        )

    result = asyncio.run(_respond_before_await())

    assert result is None
    assert emitter._pending_user_inputs == {}


def test_unknown_user_input_callback_is_discarded():
    emitter, _websocket, _io_loop = _make_emitter()
    listener = MagicMock()
    emitter.user_input_signal.connect(listener)

    emitter.on_user_input({"callback_id": "unknown", "data": "payload"})

    assert emitter._pending_user_inputs == {}
    listener.assert_called_once_with({
        "callback_id": "unknown",
        "data": "payload",
    })


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"callback_id": "callback"},
        {"callback_id": [], "data": "answer"},
        {"callback_id": {}, "data": "answer"},
    ],
)
def test_malformed_user_input_is_ignored(payload, caplog):
    emitter, _websocket, _io_loop = _make_emitter()

    emitter.on_user_input(payload)

    assert emitter._pending_user_inputs == {}
    assert "Ignoring malformed user-input response" in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"callback_id": "callback"},
        {"callback_id": [], "result": "result"},
        {"callback_id": {}, "result": "result"},
    ],
)
def test_malformed_ui_command_response_is_ignored(payload, caplog):
    emitter, _websocket, _io_loop = _make_emitter()

    emitter.on_run_ui_command_response(payload)

    assert emitter._pending_ui_commands == {}
    assert "Ignoring malformed UI-command response" in caplog.text


def test_closed_waiter_event_loops_do_not_escape_response_callbacks():
    emitter, _websocket, _io_loop = _make_emitter()
    loop = asyncio.new_event_loop()
    user_input = loop.create_future()
    ui_command = loop.create_future()
    emitter._pending_user_inputs["user"] = user_input
    emitter._pending_ui_commands["ui"] = ui_command
    loop.close()

    emitter.on_user_input({"callback_id": "user", "data": "answer"})
    emitter.on_run_ui_command_response({
        "callback_id": "ui",
        "result": "result",
    })

    assert emitter._pending_user_inputs == {}
    assert emitter._pending_ui_commands == {}


def test_user_input_registration_is_removed_when_prompt_stream_fails():
    emitter, _websocket, _io_loop = _make_emitter()
    emitter.stream = MagicMock(side_effect=RuntimeError("stream failed"))

    async def _stream_prompt():
        with pytest.raises(RuntimeError, match="stream failed"):
            emitter.stream_user_input_request("callback", MagicMock())

    asyncio.run(_stream_prompt())

    assert emitter._pending_user_inputs == {}


def test_late_user_input_cannot_override_cancellation():
    emitter, _websocket, _io_loop = _make_emitter()

    async def _cancel_then_respond():
        pending = emitter.prepare_chat_user_input("callback")
        emitter.cancel_pending_user_inputs("request cancelled")
        emitter.on_user_input({
            "callback_id": "callback",
            "data": {"confirmed": True},
        })
        return await emitter.wait_for_chat_user_input(
            emitter,
            "callback",
            pending,
        )

    with pytest.raises(UserInputCancelledError, match="request cancelled"):
        asyncio.run(_cancel_then_respond())
    assert emitter._pending_user_inputs == {}


def test_user_input_wait_is_woken_when_websocket_closes():
    emitter, _websocket, _io_loop = _make_emitter()
    handler = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    token = MagicMock()
    handler._messageCallbackHandlers = {
        "mid": extension.MessageCallbackHandlers(emitter, token)
    }

    async def _wait_for_close():
        task = asyncio.create_task(
            emitter.wait_for_chat_user_input(emitter, "callback")
        )
        await asyncio.sleep(0)
        handler.on_close()
        return await asyncio.wait_for(task, timeout=1)

    with pytest.raises(
        UserInputCancelledError,
        match="WebSocket closed before user input arrived",
    ):
        asyncio.run(_wait_for_close())
    assert emitter._pending_user_inputs == {}


@pytest.mark.parametrize(
    "error",
    [
        UICommandCancelledError("UI command cancelled"),
        UserInputCancelledError("user input cancelled"),
    ],
)
def test_request_thread_treats_interactive_cancellation_as_normal(error, caplog):
    emitter, _websocket, io_loop = _make_emitter()
    handler = WebsocketCopilotHandler.__new__(WebsocketCopilotHandler)
    handler._messageCallbackHandlers = {}

    async def _cancelled():
        raise error

    handler._run_request_thread(_cancelled(), "mid", emitter)

    messages = [
        call.args[1]
        for call in io_loop.asyncio_loop.call_soon_threadsafe.call_args_list
    ]
    assert [message["type"] for message in messages] == [
        BackendMessageType.StreamEnd,
    ]
    assert "Unhandled error" not in caplog.text
