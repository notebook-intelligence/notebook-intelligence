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
    _normalize_anthropic_credential,
    _extract_text_from_content,
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
    client._connect_resolved = threading.Event()
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

        # Patch the policy attribute before sys.platform: on Python 3.14+
        # non-Windows platforms, asyncio.__getattr__'s shim for
        # WindowsProactorEventLoopPolicy is guarded by a sys.platform check
        # and references a windows_events module that wasn't imported. If
        # sys.platform is patched to "win32" first, monkeypatch's getattr
        # (to save the old value) triggers that branch and raises NameError,
        # which raising=False doesn't catch. Ordering the policy patch first
        # lets the shim fall through to AttributeError cleanly.
        monkeypatch.setattr(
            claude_module.asyncio,
            "WindowsProactorEventLoopPolicy",
            lambda: FakeProactorPolicy(),
            raising=False,
        )
        monkeypatch.setattr(claude_module.sys, "platform", "win32", raising=False)
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

    def test_returns_error_when_queue_or_signal_is_none(self):
        """If _mark_as_disconnected ran before us (queue/signal both None),
        the request must surface a "not connected" error rather than crash
        with AttributeError on `None.put` or `None.connect`."""
        client = _make_client()
        client._client_queue = None
        client._client_thread_signal = None

        result = client._send_claude_agent_request(ClaudeAgentEventType.Query, {})

        assert result == {
            "data": None,
            "success": False,
            "error": "Claude agent is not connected",
        }


class TestEnsureConnected:
    """The helper the three callers (query, update_server_info, clear_chat_history)
    all share. Before this refactor each had its own slightly-different guard."""

    def test_noop_when_already_connected(self):
        client = _make_client()
        live = Mock()
        live.is_alive.return_value = True
        client._client_thread = live
        client.connect = Mock()

        assert client._ensure_connected() is True
        client.connect.assert_not_called()

    def test_calls_connect_when_thread_dead(self):
        client = _make_client()
        client._client_thread = None

        live = Mock()
        live.is_alive.return_value = True
        client.connect = Mock(
            side_effect=lambda: setattr(client, "_client_thread", live)
        )

        assert client._ensure_connected() is True
        client.connect.assert_called_once()

    def test_calls_connect_when_reconnect_required_even_if_thread_alive(self):
        # _reconnect_required is set from inside the worker thread when the
        # SDK reports an error but before the thread has fully exited.
        client = _make_client()
        live = Mock()
        live.is_alive.return_value = True
        client._client_thread = live
        client._reconnect_required = True
        client.connect = Mock()

        client._ensure_connected()
        client.connect.assert_called_once()


class TestQueryReconnect:
    def test_reconnects_when_thread_has_died(self):
        # Dead thread: caller observed it as "not connected" and couldn't
        # recover without restarting JupyterLab (#147).
        client = _make_client()
        dead_thread = threading.Thread(target=lambda: None)
        dead_thread.start()
        dead_thread.join()
        client._client_thread = dead_thread

        live_thread = Mock()
        live_thread.is_alive.return_value = True

        def revive_thread():
            client._client_thread = live_thread

        client.connect = Mock(side_effect=revive_thread)
        client._send_claude_agent_request = Mock(
            return_value={"data": "ok", "success": True, "error": None}
        )

        result = client.query(MagicMock(), MagicMock())

        client.connect.assert_called_once()
        client._send_claude_agent_request.assert_called_once()
        assert result is None

    def test_returns_informative_error_when_reconnect_fails(self):
        client = _make_client()
        client._client_thread = None
        client.connect = Mock()
        client._send_claude_agent_request = Mock()

        result = client.query(MagicMock(), MagicMock())

        client.connect.assert_called_once()
        client._send_claude_agent_request.assert_not_called()
        assert "not connected" in result
        assert "server log" in result


class TestConnectWaitsForReadiness:
    """``connect()`` must not return until the worker thread has reached a
    terminal state (Connected or FailedToConnect). Otherwise the caller's
    post-connect ``is_connected()`` check sees a momentarily-alive thread
    that's about to die on subprocess-spawn failure — exactly the #147 race
    that made PR #148's first attempt ineffective for the reporter.
    """

    def test_waits_for_connected_signal(self, monkeypatch):
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CONNECT_TIMEOUT", 5
        )
        client = _make_client()
        client._update_server_info_async = Mock()

        worker_started = threading.Event()
        stop_worker = threading.Event()

        async def fake_worker():
            # Simulate a slow handshake so connect() must actually wait.
            await asyncio.sleep(0.05)
            client._status = ClaudeAgentClientStatus.Connected
            client._connect_resolved.set()
            worker_started.set()
            # Keep thread alive so is_connected() stays True after connect().
            while not stop_worker.is_set():
                await asyncio.sleep(0.01)

        client._client_thread_func = fake_worker

        try:
            start = time.monotonic()
            client.connect()
            elapsed = time.monotonic() - start
            assert worker_started.is_set()
            assert elapsed >= 0.05
            assert client.is_connected()
            client._update_server_info_async.assert_called_once()
        finally:
            stop_worker.set()
            if client._client_thread is not None:
                client._client_thread.join(timeout=1)

    def test_returns_promptly_on_spawn_failure(self, monkeypatch):
        # The #147 race: _get_client() raises (SDK subprocess spawn failed).
        # The outer except in _client_thread_func must set _connect_resolved
        # so connect() returns immediately instead of waiting the full
        # CLAUDE_AGENT_CONNECT_TIMEOUT.
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CONNECT_TIMEOUT", 5
        )
        client = _make_client()
        client._update_server_info_async = Mock()

        async def spawn_failure():
            raise RuntimeError("SDK spawn failed")

        client._get_client = spawn_failure

        start = time.monotonic()
        client.connect()
        elapsed = time.monotonic() - start

        # Well under the 5s timeout — the except branch signals promptly.
        assert elapsed < 2
        assert not client.is_connected()
        assert client._status == ClaudeAgentClientStatus.FailedToConnect
        # No server-info fetch when the handshake failed.
        client._update_server_info_async.assert_not_called()

    def test_returns_after_timeout_when_worker_hangs(self, monkeypatch):
        # Defensive: if the worker hangs in the async setup without raising
        # or setting the event, connect() still returns after the timeout.
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CONNECT_TIMEOUT", 0.1
        )
        client = _make_client()
        client._update_server_info_async = Mock()

        stop_worker = threading.Event()

        async def hanging_worker():
            while not stop_worker.is_set():
                await asyncio.sleep(0.01)

        client._client_thread_func = hanging_worker

        try:
            start = time.monotonic()
            client.connect()
            elapsed = time.monotonic() - start
            assert 0.1 <= elapsed < 2
        finally:
            stop_worker.set()
            if client._client_thread is not None:
                client._client_thread.join(timeout=1)

    def test_query_surfaces_server_log_error_when_spawn_fails(self, monkeypatch):
        """End-to-end of the #147 scenario raffaelemancuso hit after PR #148
        commit 1: with only the naive retry, query()'s post-connect
        is_connected() check passed while the worker thread was momentarily
        alive, so the user saw the stale 'Claude agent is not running'
        error from _send_claude_agent_request's dead-thread path. With
        connect() now blocking until readiness resolves, query() returns
        the informative 'Check the server log' error instead.
        """
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CONNECT_TIMEOUT", 2
        )
        client = _make_client()
        client._update_server_info_async = Mock()

        async def spawn_failure():
            raise RuntimeError("SDK spawn failed")

        client._get_client = spawn_failure

        result = client.query(MagicMock(), MagicMock())

        assert "not connected" in result
        assert "server log" in result


class TestOtherCallersEnsureConnected:
    """update_server_info / clear_chat_history used to check only
    _reconnect_required, not is_connected(), so a dead thread without the
    flag set left them silently no-op'd. Now they route through
    _ensure_connected() and pick up the same recovery behavior as query()."""

    def test_update_server_info_reconnects_on_dead_thread(self):
        client = _make_client()
        client._client_thread = None
        client.connect = Mock()
        client._send_claude_agent_request = Mock()

        client.update_server_info()

        client.connect.assert_called_once()
        client._send_claude_agent_request.assert_not_called()

    def test_clear_chat_history_reconnects_on_dead_thread(self):
        client = _make_client()
        client._client_thread = None
        client.connect = Mock()
        client._send_claude_agent_request = Mock()

        client.clear_chat_history()

        client.connect.assert_called_once()
        client._send_claude_agent_request.assert_not_called()


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

class TestNormalizeAnthropicCredential:
    """Settings panel saves unset string fields as ``""`` rather than ``None``.
    The Anthropic SDK forwards an empty ``base_url`` to httpx which rejects
    it with ``UnsupportedProtocol``, and an empty ``api_key`` blocks the SDK
    from falling back to ``ANTHROPIC_API_KEY``. The normaliser collapses
    whitespace-only / empty values to ``None`` so the SDK defaults engage."""

    def test_none_passes_through(self):
        assert _normalize_anthropic_credential(None) is None

    def test_empty_string_becomes_none(self):
        assert _normalize_anthropic_credential("") is None

    def test_whitespace_only_becomes_none(self):
        assert _normalize_anthropic_credential("   ") is None
        assert _normalize_anthropic_credential("\t\n") is None

    def test_real_value_passes_through(self):
        assert _normalize_anthropic_credential("sk-ant-real") == "sk-ant-real"
        assert _normalize_anthropic_credential("https://api.anthropic.com") == "https://api.anthropic.com"

    def test_surrounding_whitespace_is_stripped(self):
        # A value pasted with stray whitespace must reach the SDK clean.
        assert _normalize_anthropic_credential("  sk-ant-real  ") == "sk-ant-real"

    def test_non_string_values_become_none(self):
        # claude_settings comes from raw JSON. A malformed config writing
        # ``"api_key": false`` or ``"base_url": 123`` would otherwise crash
        # client construction with ``AttributeError: 'bool' object has no
        # attribute 'strip'`` deep inside a request handler.
        assert _normalize_anthropic_credential(False) is None
        assert _normalize_anthropic_credential(True) is None
        assert _normalize_anthropic_credential(0) is None
        assert _normalize_anthropic_credential(123) is None
        assert _normalize_anthropic_credential(1.5) is None
        assert _normalize_anthropic_credential([]) is None
        assert _normalize_anthropic_credential({"key": "x"}) is None


class TestExtractTextFromContent:
    def test_string_passthrough(self):
        assert _extract_text_from_content("hello world") == "hello world"

    def test_list_with_text_block(self):
        content = [{"type": "text", "text": "describe this image"}]
        assert _extract_text_from_content(content) == "describe this image"

    def test_list_strips_image_blocks(self):
        content = [
            {"type": "text", "text": "The user pasted an image 'shot.png':"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
        ]
        result = _extract_text_from_content(content)
        assert result == "The user pasted an image 'shot.png':"
        assert "base64" not in result

    def test_list_with_multiple_text_blocks(self):
        content = [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
            {"type": "text", "text": "second"},
        ]
        assert _extract_text_from_content(content) == "first\nsecond"

    def test_list_with_only_image_returns_empty(self):
        content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}}]
        assert _extract_text_from_content(content) == ""

    def test_list_with_non_dict_entries_skipped(self):
        content = [{"type": "text", "text": "valid"}, "raw string", None]
        assert _extract_text_from_content(content) == "valid"
