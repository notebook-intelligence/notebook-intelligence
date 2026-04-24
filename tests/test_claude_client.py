"""Regression tests for the "Claude hangs on Thinking..." fix.

Covers three defects that combined to produce the 30-minute spinner when the
Claude agent failed to start or a query failed:

1. ``ClaudeCodeClient.is_connected`` considered a zombie thread "connected".
2. ``_send_claude_agent_request`` polled for the full response timeout even
   when the worker thread had died.
3. ``ClaudeCodeChatParticipant.handle_chat_request`` swallowed error-string
   returns from ``_client.query`` and never streamed error info or guaranteed
   ``response.finish()`` ran on exceptions.
"""

import asyncio
import threading
import time
from queue import Queue
from unittest.mock import AsyncMock, MagicMock, Mock

import notebook_intelligence.claude as claude_module
from notebook_intelligence.api import ChatResponse, MarkdownData
from notebook_intelligence.claude import (
    ClaudeAgentClientStatus,
    ClaudeAgentEventType,
    ClaudeCodeChatParticipant,
    ClaudeCodeClient,
    SignalImpl,
)


def _make_client():
    """Build a ``ClaudeCodeClient`` without invoking ``__init__`` / ``connect``."""
    client = ClaudeCodeClient.__new__(ClaudeCodeClient)
    client._host = None
    client._client_options = None
    client._websocket_connector = None
    client._client = None
    client._client_queue = Queue()
    client._client_thread_signal = SignalImpl()
    client._client_thread = None
    client._status = ClaudeAgentClientStatus.NotConnected
    client._server_info = None
    client._server_info_lock = threading.Lock()
    client._reconnect_required = False
    client._continue_conversation = None
    return client


class TestIsConnected:
    def test_returns_false_when_thread_is_none(self):
        client = _make_client()
        client._client_thread = None
        assert client.is_connected() is False

    def test_returns_false_when_thread_has_exited(self):
        client = _make_client()
        thread = threading.Thread(target=lambda: None)
        thread.start()
        thread.join()
        client._client_thread = thread
        # Without the is_alive() check this returned True for a zombie thread,
        # causing query() to push events into a queue nobody was reading.
        assert client.is_connected() is False

    def test_returns_true_for_live_thread(self):
        client = _make_client()
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        try:
            client._client_thread = thread
            assert client.is_connected() is True
        finally:
            stop.set()
            thread.join(timeout=1)


class TestClientThreadEventLoop:
    def test_windows_uses_proactor_loop_without_changing_global_policy(self, monkeypatch):
        client = _make_client()
        created_loop = asyncio.new_event_loop()
        set_policy_calls = []

        class FakeProactorPolicy:
            def new_event_loop(self):
                return created_loop

        monkeypatch.setattr(claude_module.sys, "platform", "win32", raising=False)
        monkeypatch.setattr(
            claude_module.asyncio,
            "WindowsProactorEventLoopPolicy",
            lambda: FakeProactorPolicy(),
            raising=False,
        )
        monkeypatch.setattr(
            claude_module.asyncio,
            "set_event_loop_policy",
            lambda *args, **kwargs: set_policy_calls.append((args, kwargs)),
        )

        observed_loop = None

        async def sample():
            nonlocal observed_loop
            observed_loop = asyncio.get_running_loop()

        client._run_client_thread(sample())

        assert observed_loop is created_loop
        assert created_loop.is_closed()
        assert set_policy_calls == []


class TestSendClaudeAgentRequestDeadThread:
    def test_bails_out_quickly_when_worker_thread_dead(self, monkeypatch):
        # Make the poll loop's sleep a no-op so a slow test host doesn't mask
        # the bug: the behavior we're guarding is structural (returns without
        # waiting for the 30-min timeout), not microsecond-level.
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME",
            0,
        )

        client = _make_client()
        dead_thread = threading.Thread(target=lambda: None)
        dead_thread.start()
        dead_thread.join()
        client._client_thread = dead_thread

        start = time.monotonic()
        result = client._send_claude_agent_request(ClaudeAgentEventType.Query, {})
        elapsed = time.monotonic() - start

        assert result == {
            "data": None,
            "success": False,
            "error": "Claude agent is not running",
        }
        # Should return on the first loop iteration, long before the 1800s
        # response timeout. A generous 5s ceiling keeps CI noise from flaking.
        assert elapsed < 5
        # Dead-thread branch must mark the client disconnected so subsequent
        # requests don't block waiting on the same corpse.
        assert client._status == ClaudeAgentClientStatus.NotConnected
        assert client._client_thread is None

    def test_signal_disconnect_runs_on_dead_thread_exit(self, monkeypatch):
        """The try/finally must disconnect even when the loop short-circuits."""
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME",
            0,
        )
        client = _make_client()
        dead_thread = threading.Thread(target=lambda: None)
        dead_thread.start()
        dead_thread.join()
        client._client_thread = dead_thread

        signal_before = client._client_thread_signal
        listeners_before = len(signal_before._listeners)
        client._send_claude_agent_request(ClaudeAgentEventType.Query, {})
        # _mark_as_disconnected nulls the signal ref on the client, but the
        # locally-captured signal in _send_claude_agent_request should still
        # have had its listener removed.
        assert len(signal_before._listeners) == listeners_before


def _make_participant():
    """Build a ``ClaudeCodeChatParticipant`` without spinning up a real client."""
    participant = ClaudeCodeChatParticipant.__new__(ClaudeCodeChatParticipant)
    participant._rule_injector = MagicMock()
    participant._update_client_debounced_timer = None
    participant._host = MagicMock()
    participant._client_options = MagicMock()
    participant._client = MagicMock()
    return participant


def _make_chat_request(mode_id="ask"):
    request = MagicMock()
    request.chat_mode.id = mode_id
    return request


class TestHandleChatRequestErrorHandling:
    def test_finish_called_when_query_raises(self):
        participant = _make_participant()
        participant._client.query.side_effect = RuntimeError("boom")

        response = Mock(spec=ChatResponse)
        asyncio.run(participant.handle_chat_request(_make_chat_request(), response))

        response.finish.assert_called_once()
        # The exception path should have streamed a user-visible error.
        stream_calls = [c.args[0] for c in response.stream.call_args_list]
        markdown_messages = [
            d.content for d in stream_calls if isinstance(d, MarkdownData)
        ]
        assert any("boom" in m for m in markdown_messages)

    def test_error_string_from_query_is_surfaced(self):
        participant = _make_participant()
        participant._client.query.return_value = "Claude agent is not connected"

        response = Mock(spec=ChatResponse)
        asyncio.run(participant.handle_chat_request(_make_chat_request(), response))

        response.finish.assert_called_once()
        stream_calls = [c.args[0] for c in response.stream.call_args_list]
        markdown_messages = [
            d.content for d in stream_calls if isinstance(d, MarkdownData)
        ]
        # The string-return bail-out path must reach the user instead of
        # leaving a silent "Thinking..." spinner.
        assert any(
            "Claude agent error" in m and "not connected" in m
            for m in markdown_messages
        )

    def test_finish_swallows_closed_websocket(self):
        """A closed socket at finish() must not propagate into the task loop."""
        participant = _make_participant()
        participant._client.query.return_value = None
        response = Mock(spec=ChatResponse)
        response.finish.side_effect = RuntimeError("WebSocketClosedError")

        # Must not raise — the whole point of the finally-wrapped finish().
        asyncio.run(participant.handle_chat_request(_make_chat_request(), response))
        response.finish.assert_called_once()

    def test_inline_chat_mode_delegates_without_wrapping(self):
        """Sanity check: the ask/agent try/finally block is skipped for inline."""
        participant = _make_participant()
        participant.handle_inline_chat_request = AsyncMock()

        response = Mock(spec=ChatResponse)
        asyncio.run(
            participant.handle_chat_request(_make_chat_request("inline-chat"), response)
        )

        participant.handle_inline_chat_request.assert_awaited_once()
        # Inline path owns its own response lifecycle; the outer handler
        # shouldn't have touched the response.
        response.finish.assert_not_called()
        response.stream.assert_not_called()
