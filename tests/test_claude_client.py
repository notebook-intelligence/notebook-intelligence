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
    ClaudeChatModel,
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
    client._connect_lock = threading.Lock()
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

    def test_returns_false_when_status_is_failed_to_connect(self):
        # `is_connected()` must respect the FailedToConnect status even
        # when the thread reference happens to be alive. The
        # publish-after-start race in `_start_worker_thread` can briefly
        # resurrect a dead thread reference whose `is_alive()` reads True;
        # the status check is the post-handshake truth.
        client = _make_client()
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        try:
            client._client_thread = thread
            client._status = ClaudeAgentClientStatus.FailedToConnect
            assert client.is_connected() is False
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
    def test_subscribes_before_publishing_request(self):
        client = _make_client()
        live_thread = Mock()
        live_thread.is_alive.return_value = True
        client._client_thread = live_thread

        class ImmediateResponseQueue:
            def put(self, event):
                client._client_thread_signal.emit({
                    "id": event["id"],
                    "data": "done",
                })

        client._client_queue = ImmediateResponseQueue()

        result = client._send_claude_agent_request(
            ClaudeAgentEventType.Query, {}
        )

        assert result == {"data": "done", "success": True, "error": None}

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


class TestSendClaudeAgentRequestHeartbeat:
    def test_heartbeat_sent_during_live_request(self, monkeypatch):
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME", 0)
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_HEARTBEAT_INTERVAL", 0)

        client = _make_client()
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        client._client_thread = thread

        # Capture event_id so the heartbeat side-effect can emit the matching response.
        captured_id = []
        orig_put = client._client_queue.put
        def capturing_put(event):
            orig_put(event)
            captured_id.append(event["id"])
        client._client_queue.put = capturing_put

        connector = MagicMock()
        def respond_on_heartbeat(payload):
            client._client_thread_signal.emit({"id": captured_id[0], "data": "done"})
        connector.write_message.side_effect = respond_on_heartbeat
        client._websocket_connector = connector

        try:
            client._send_claude_agent_request(ClaudeAgentEventType.Query, {})
        finally:
            stop.set()
            thread.join(timeout=1)

        connector.write_message.assert_called_once()
        assert connector.write_message.call_args[0][0] == {
            "type": "claude-code-heartbeat",
            "data": {},
        }

    def test_heartbeat_skipped_when_connector_none(self, monkeypatch):
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME", 0)
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_HEARTBEAT_INTERVAL", 0)
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT", 0.05)

        client = _make_client()
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        client._client_thread = thread
        client._websocket_connector = None

        try:
            client._send_claude_agent_request(ClaudeAgentEventType.Query, {})
        finally:
            stop.set()
            thread.join(timeout=1)
        # Passes if no AttributeError raised when connector is None.

    def test_heartbeat_failure_does_not_abort_request(self, monkeypatch):
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_CLIENT_RESPONSE_WAIT_TIME", 0)
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_HEARTBEAT_INTERVAL", 0)
        monkeypatch.setattr("notebook_intelligence.claude.CLAUDE_AGENT_CLIENT_RESPONSE_TIMEOUT", 0.05)

        client = _make_client()
        stop = threading.Event()
        thread = threading.Thread(target=stop.wait, daemon=True)
        thread.start()
        client._client_thread = thread
        connector = MagicMock()
        connector.write_message.side_effect = RuntimeError("socket closed")
        client._websocket_connector = connector

        try:
            result = client._send_claude_agent_request(ClaudeAgentEventType.Query, {})
        finally:
            stop.set()
            thread.join(timeout=1)

        assert isinstance(result, dict)
        assert connector.write_message.called


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
        # ``is_connected`` checks ``Thread.is_alive``; the worker signals
        # ``_connect_resolved`` from its except branch *before* the
        # coroutine returns, so on slow runners the thread can briefly
        # report alive while the spawn-failure unwinds. Wait for it to
        # actually exit before asserting on liveness.
        if client._client_thread is not None:
            client._client_thread.join(timeout=2)
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

    def test_failed_to_connect_status_is_set_before_event_signal(self):
        # The worker must call `_set_status(FailedToConnect)` *before*
        # `_connect_resolved.set()`. `is_connected()`'s status short-circuit
        # relies on this ordering — if the writes were reversed, callers
        # waking from `connect()`'s wait could observe the prior status
        # while the worker thread is briefly still alive, mistakenly
        # returning True until the status write caught up.
        client = _make_client()
        client._update_server_info_async = Mock()

        async def spawn_failure():
            raise RuntimeError("SDK spawn failed")

        client._get_client = spawn_failure

        call_order = []
        original_set_status = client._set_status
        original_event_set = client._connect_resolved.set

        def recording_set_status(status):
            if status == ClaudeAgentClientStatus.FailedToConnect:
                call_order.append("status_failed")
            original_set_status(status)

        def recording_event_set():
            call_order.append("event_set")
            original_event_set()

        client._set_status = recording_set_status
        client._connect_resolved.set = recording_event_set

        client.connect()

        assert "status_failed" in call_order, (
            f"Expected _set_status(FailedToConnect) to be called, got: {call_order}"
        )
        assert "event_set" in call_order, (
            f"Expected _connect_resolved.set() to be called, got: {call_order}"
        )
        assert call_order.index("status_failed") < call_order.index("event_set"), (
            f"Expected status_failed before event_set, got: {call_order}"
        )

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


class TestConnectInBackground:
    """``__init__`` must return promptly even when the SDK handshake is slow,
    so JupyterLab's server startup isn't blocked for the
    ``CLAUDE_AGENT_CONNECT_TIMEOUT`` window (#163). The synchronous
    ``connect()`` is preserved for chat-request callers that need readiness
    state before issuing a query.
    """

    def test_connect_in_background_returns_immediately_when_worker_slow(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CONNECT_TIMEOUT", 5
        )
        client = _make_client()
        client._update_server_info_async = Mock()

        stop_worker = threading.Event()

        async def slow_worker():
            # Worker takes 0.3s to "handshake" — far longer than the budget
            # we'd accept on the JupyterLab startup path.
            await asyncio.sleep(0.3)
            client._status = ClaudeAgentClientStatus.Connected
            client._connect_resolved.set()
            while not stop_worker.is_set():
                await asyncio.sleep(0.01)

        client._client_thread_func = slow_worker

        try:
            start = time.monotonic()
            client.connect_in_background()
            elapsed = time.monotonic() - start
            # The connect dispatch should return well under the worker's own
            # delay. 100ms is generous for a thread-spawn and lock-acquire.
            assert elapsed < 0.1, (
                f"connect_in_background blocked for {elapsed:.3f}s; expected "
                f"non-blocking dispatch"
            )
        finally:
            stop_worker.set()
            if client._client_thread is not None:
                client._client_thread.join(timeout=1)

    def test_init_returns_quickly_even_when_handshake_hangs(self, monkeypatch):
        """End-to-end of the #163 fix: constructing a ``ClaudeCodeClient`` no
        longer blocks the caller for the full connect timeout when the
        worker takes longer than expected."""
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CONNECT_TIMEOUT", 5
        )

        # Replace the worker entrypoint with a slow stub before constructing
        # the client. _start_worker_thread reads
        # ``self._client_thread_func`` lazily via ``self._client_thread_func()``,
        # so monkey-patching the bound name on the instance is enough.
        original_init = ClaudeCodeClient.__init__
        slow_func_holders = {}

        def patched_init(self, host, options):
            stop = threading.Event()
            slow_func_holders[id(self)] = stop

            async def slow():
                # Check the stop flag frequently so cleanup doesn't have to
                # wait the full 2-second handshake delay.
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline and not stop.is_set():
                    await asyncio.sleep(0.05)
                while not stop.is_set():
                    await asyncio.sleep(0.05)

            # Stub out the bits that touch real infrastructure.
            self._client_thread_func = slow
            original_init(self, host, options)

        monkeypatch.setattr(ClaudeCodeClient, "__init__", patched_init)

        host = MagicMock()
        host.websocket_connector = None
        options = MagicMock()

        start = time.monotonic()
        client = ClaudeCodeClient(host, options)
        elapsed = time.monotonic() - start

        try:
            # 500ms is the budget called out in the issue: well under the
            # 15s default timeout, generous enough to absorb thread-start
            # overhead on a loaded CI runner.
            assert elapsed < 0.5, (
                f"__init__ blocked for {elapsed:.3f}s; should dispatch the "
                f"handshake to a background thread"
            )
            # Status reflects the in-flight connect, not a completed one.
            assert client._status in (
                ClaudeAgentClientStatus.Connecting,
                ClaudeAgentClientStatus.NotConnected,
            )
        finally:
            stop = slow_func_holders.get(id(client))
            if stop is not None:
                stop.set()
            # Give the background connect dispatcher a beat to finish spawning
            # the worker before we try to join it.
            for _ in range(50):
                if client._client_thread is not None:
                    break
                time.sleep(0.05)
            if client._client_thread is not None:
                client._client_thread.join(timeout=5)

    def test_concurrent_connect_does_not_double_spawn(self, monkeypatch):
        """If a chat handler hits ``_ensure_connected()`` while the background
        ``__init__`` connect is mid-handshake, the lock must serialise them
        so we don't end up with two worker threads racing on ``self._client``.
        """
        monkeypatch.setattr(
            "notebook_intelligence.claude.CLAUDE_AGENT_CONNECT_TIMEOUT", 5
        )
        client = _make_client()
        client._update_server_info_async = Mock()

        spawn_count = {"n": 0}
        stop_worker = threading.Event()

        async def counting_worker():
            spawn_count["n"] += 1
            await asyncio.sleep(0.05)
            client._status = ClaudeAgentClientStatus.Connected
            client._connect_resolved.set()
            while not stop_worker.is_set():
                await asyncio.sleep(0.01)

        client._client_thread_func = counting_worker

        try:
            t1 = threading.Thread(target=client.connect)
            t2 = threading.Thread(target=client.connect)
            t1.start()
            t2.start()
            t1.join(timeout=3)
            t2.join(timeout=3)

            assert spawn_count["n"] == 1, (
                f"expected 1 worker thread, got {spawn_count['n']}"
            )
            assert client.is_connected()
        finally:
            stop_worker.set()
            if client._client_thread is not None:
                client._client_thread.join(timeout=1)


class TestWorkerThreadSignalRace:
    """Guard against the cancellation race fixed in _client_thread_func.

    When the user clicks Stop, _mark_as_disconnected() runs on the polling
    thread and sets self._client_thread_signal = None. The worker thread may
    simultaneously be in its finally block about to call
    self._client_thread_signal.emit(...), which without the snapshot fix raises
    AttributeError and puts the agent into FailedToConnect state.

    The fix snapshots the signal into a local variable at the top of each event
    loop iteration so the finally block holds a valid reference even if the
    client's field is nulled mid-flight.
    """

    def test_snapshot_survives_mark_as_disconnected(self):
        """Local snapshot keeps signal alive after _mark_as_disconnected nulls
        the client's reference — emit must succeed without raising."""
        client = _make_client()
        original_signal = client._client_thread_signal
        received = []
        original_signal.connect(lambda data: received.append(data))

        # Worker snapshots signal at the top of the event loop iteration.
        signal = client._client_thread_signal

        # Cancel path: _mark_as_disconnected() nulls the client's own reference.
        client._mark_as_disconnected()
        assert client._client_thread_signal is None

        # Worker's finally block uses the snapshot — must not raise.
        if signal is not None:
            signal.emit({"id": "x", "data": "query completed"})

        assert received == [{"id": "x", "data": "query completed"}]

    def test_signal_already_none_at_snapshot_time_is_safe(self):
        """If disconnect ran before the snapshot, the null guard in the finally
        block must skip the emit silently rather than raising AttributeError."""
        client = _make_client()
        client._mark_as_disconnected()

        # Signal is already None when the worker reaches the snapshot line.
        signal = client._client_thread_signal
        assert signal is None

        # Finally block null guard — must not raise, must not emit.
        if signal is not None:
            signal.emit({"id": "x", "data": "query completed"})
        # Reaching here without exception is the assertion.

    def test_queue_snapshot_survives_mark_as_disconnected(self):
        """Local queue snapshot keeps the Queue alive after _mark_as_disconnected
        nulls the client's reference — get() must not raise AttributeError."""
        client = _make_client()
        original_queue = client._client_queue
        original_queue.put({"id": "x", "type": "query"})

        # Worker snapshots queue at the top of the event loop iteration.
        queue = client._client_queue

        # Cancel path: _mark_as_disconnected() nulls the client's own reference.
        client._mark_as_disconnected()
        assert client._client_queue is None

        # Worker's queue.get() uses the snapshot — must not raise.
        event = queue.get(block=False)
        assert event == {"id": "x", "type": "query"}

    def test_queue_already_none_at_snapshot_time_exits_cleanly(self):
        """If disconnect ran before the queue snapshot, the null guard must
        cause the worker to return rather than raising AttributeError."""
        client = _make_client()
        client._mark_as_disconnected()

        # Queue is already None when the worker reaches the snapshot line.
        queue = client._client_queue
        assert queue is None

        # Null guard — worker returns instead of calling get() on None.
        if queue is None:
            return
        queue.get(block=False)  # must not be reached


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

    def test_inline_chat_forwards_rule_enhanced_system_prompt(self, monkeypatch):
        participant = _make_participant()
        participant._rule_injector.inject_rules.return_value = (
            "inline edit prompt\n\nworkspace rule"
        )
        request = _make_chat_request("inline-chat")
        request.host.nbi_config.claude_settings = {
            "chat_model": "claude-sonnet-test",
            "api_key": "test-key",
            "base_url": "https://example.test",
        }
        request.chat_history = [{"role": "user", "content": "change this"}]
        request.cancel_token = MagicMock()
        response = Mock(spec=ChatResponse)
        chat_model = MagicMock()
        monkeypatch.setattr(
            claude_module,
            "ClaudeChatModel",
            MagicMock(return_value=chat_model),
        )
        options = {"system_prompt": "inline edit prompt", "other": "value"}

        asyncio.run(
            participant.handle_inline_chat_request(request, response, options)
        )

        participant._rule_injector.inject_rules.assert_called_once_with(
            "inline edit prompt",
            request,
        )
        chat_model.completions.assert_called_once_with(
            request.chat_history,
            response=response,
            cancel_token=request.cancel_token,
            options={
                "system_prompt": "inline edit prompt\n\nworkspace rule",
                "other": "value",
            },
        )
        assert options == {
            "system_prompt": "inline edit prompt",
            "other": "value",
        }

    def test_inline_chat_applies_system_prompt_token_budget(self, monkeypatch):
        participant = _make_participant()
        participant._rule_injector.inject_rules.return_value = "bounded prompt"
        request = _make_chat_request("inline-chat")
        request.host.nbi_config.claude_settings = {
            "chat_model": "claude-sonnet-test",
        }
        request.chat_history = [{"role": "user", "content": "change this"}]
        request.cancel_token = MagicMock()
        response = Mock(spec=ChatResponse)
        chat_model = MagicMock()
        monkeypatch.setattr(
            claude_module,
            "ClaudeChatModel",
            MagicMock(return_value=chat_model),
        )

        asyncio.run(
            participant.handle_inline_chat_request(
                request,
                response,
                {
                    "system_prompt": "inline edit prompt",
                    "system_prompt_token_budget": 500,
                },
            )
        )

        participant._rule_injector.inject_rules.assert_called_once_with(
            "inline edit prompt",
            request,
            max_tokens=500,
        )
        completion_options = chat_model.completions.call_args.kwargs["options"]
        assert completion_options == {"system_prompt": "bounded prompt"}


class _FakeMessageStream:
    """Stand-in for the Anthropic SDK's MessageStreamManager.

    The SDK returns a sync context manager whose ``text_stream`` attribute
    yields decoded text deltas. ``ClaudeChatModel.completions`` only touches
    those two pieces, so the fake mirrors exactly that surface."""

    def __init__(self, chunks):
        self.text_stream = iter(chunks)
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited = True
        return False


def _make_chat_model(chunks):
    """Build a ``ClaudeChatModel`` without calling __init__ (avoids hitting
    the live Anthropic API). The returned model has its ``_client.messages
    .stream`` wired to a ``_FakeMessageStream`` that yields ``chunks``."""
    model = ClaudeChatModel.__new__(ClaudeChatModel)
    model._model_id = "claude-sonnet-test"
    model._model_name = "Claude Sonnet (test)"
    model._context_window = 200000
    model._supports_tools = True
    model._client = MagicMock()
    fake_stream = _FakeMessageStream(chunks)
    model._client.messages.stream = MagicMock(return_value=fake_stream)
    return model, fake_stream


class TestClaudeChatModelStreaming:
    """``ClaudeChatModel.completions`` powers Claude-mode inline chat
    (Ctrl+G). It must stream deltas as they arrive so the diff pane fills
    in progressively, honour the cancel token between chunks, and always
    end with ``response.finish()`` so the front-end stops the spinner."""

    def test_each_chunk_is_streamed_as_its_own_delta(self):
        # Three deltas in -> three response.stream calls out -> one finish.
        # Proves we no longer buffer the whole response into a single delta.
        model, fake_stream = _make_chat_model(["def ", "foo():", "\n    pass"])
        response = MagicMock()

        model.completions(messages=[{"role": "user", "content": "x"}], response=response)

        assert response.stream.call_count == 3
        emitted = [
            call.args[0]["choices"][0]["delta"]["content"]
            for call in response.stream.call_args_list
        ]
        assert emitted == ["def ", "foo():", "\n    pass"]
        response.finish.assert_called_once()
        # Context manager entered and exited cleanly so the SDK can release
        # the underlying HTTP stream.
        assert fake_stream.entered and fake_stream.exited

    def test_uses_messages_stream_not_messages_create(self):
        # Regression guard: if someone reverts this to messages.create the
        # diff pane goes back to "empty until done". Easier to assert the
        # API choice directly than to test the user-visible symptom.
        model, _ = _make_chat_model(["hi"])
        response = MagicMock()

        model.completions(messages=[{"role": "user", "content": "x"}], response=response)

        model._client.messages.stream.assert_called_once()
        kwargs = model._client.messages.stream.call_args.kwargs
        assert kwargs["model"] == "claude-sonnet-test"
        assert kwargs["messages"] == [{"role": "user", "content": "x"}]
        assert kwargs["max_tokens"] == 10000
        assert "system" not in kwargs
        model._client.messages.create.assert_not_called()

    def test_system_prompt_is_forwarded_to_anthropic_stream(self):
        model, _ = _make_chat_model(["updated code"])
        response = MagicMock()

        model.completions(
            messages=[{"role": "user", "content": "change this"}],
            response=response,
            options={"system_prompt": "return only replacement code"},
        )

        kwargs = model._client.messages.stream.call_args.kwargs
        assert kwargs["system"] == "return only replacement code"

    def test_empty_chunks_are_skipped(self):
        # The Anthropic stream can occasionally yield empty strings between
        # content blocks. Forwarding those wastes a websocket frame and
        # creates an empty assistant turn in the front-end's accumulator.
        model, _ = _make_chat_model(["hello", "", " world", ""])
        response = MagicMock()

        model.completions(messages=[{"role": "user", "content": "x"}], response=response)

        assert response.stream.call_count == 2
        emitted = [
            call.args[0]["choices"][0]["delta"]["content"]
            for call in response.stream.call_args_list
        ]
        assert emitted == ["hello", " world"]

    def test_pre_cancelled_request_skips_anthropic_stream_open(self):
        # A fast CancelChatRequest can flip the token before the worker
        # thread reaches completions(). The check must run BEFORE the with
        # block, otherwise the underlying messages.stream call still opens
        # the HTTP request and burns an Anthropic call whose output has
        # nowhere to go. finish() still runs so the websocket end event
        # closes the front-end's spinner.
        model, _ = _make_chat_model(["alpha", "beta"])
        response = MagicMock()
        cancel_token = MagicMock()
        cancel_token.is_cancel_requested = True

        model.completions(
            messages=[{"role": "user", "content": "x"}],
            response=response,
            cancel_token=cancel_token,
        )

        # The actual API call must not have been opened — that's the whole
        # point of the early-out. Asserting on response.stream alone (as an
        # earlier version of this test did) was a false positive: a fake
        # MessageStreamManager has no side effect analogous to opening the
        # HTTP stream, so a missing emit doesn't prove the network was
        # spared.
        model._client.messages.stream.assert_not_called()
        response.stream.assert_not_called()
        response.finish.assert_called_once()

    def test_cancel_token_short_circuits_mid_stream(self):
        # User hits Escape while Claude is mid-response. The token flips on
        # the next iteration so we stop emitting and let the with-block
        # close the stream — abandoning the rest of the chunks.
        model, _ = _make_chat_model(["alpha", "beta", "gamma"])
        response = MagicMock()
        cancel_token = MagicMock()
        # Allow one chunk, then request cancel before the second.
        cancel_token.is_cancel_requested = False

        emit_count = {"n": 0}
        original_stream = response.stream

        def stream_then_cancel(payload):
            original_stream(payload)
            emit_count["n"] += 1
            if emit_count["n"] == 1:
                cancel_token.is_cancel_requested = True

        response.stream = stream_then_cancel

        model.completions(
            messages=[{"role": "user", "content": "x"}],
            response=response,
            cancel_token=cancel_token,
        )

        # First chunk slipped through, second triggered the cancel check
        # and broke the loop, third never reached the user.
        emitted = [
            payload["choices"][0]["delta"]["content"]
            for (payload,), _ in original_stream.call_args_list
        ]
        assert emitted == ["alpha"]
        # finish() still runs so the front-end's spinner closes — the user
        # already saw the popover dismiss on Escape, but the websocket end
        # event must arrive regardless.
        response.finish.assert_called_once()

    def test_no_cancel_token_is_safe(self):
        # The non-Claude code paths still call completions without a cancel
        # token. Defaulting to None must not crash.
        model, _ = _make_chat_model(["a", "b"])
        response = MagicMock()

        model.completions(
            messages=[{"role": "user", "content": "x"}],
            response=response,
            cancel_token=None,
        )

        assert response.stream.call_count == 2
        response.finish.assert_called_once()

    def test_mid_stream_exception_emits_error_marker_and_reraises(self):
        # Anthropic raising mid-stream used to leave the diff pane showing
        # silently truncated code (the outer handler's MarkdownData error
        # never reaches the inline popover, which only consumes payloads with
        # delta.content). The fix surfaces the failure as a final delta into
        # the same channel and re-raises so the caller still finishes.
        class ExplodingStream(_FakeMessageStream):
            def __init__(self, before_chunks, exc):
                super().__init__(before_chunks)
                self._exc = exc

                def gen():
                    for c in before_chunks:
                        yield c
                    raise exc

                self.text_stream = gen()

        model = ClaudeChatModel.__new__(ClaudeChatModel)
        model._model_id = "claude-sonnet-test"
        model._model_name = "Claude Sonnet (test)"
        model._context_window = 200000
        model._supports_tools = True
        model._client = MagicMock()
        boom = RuntimeError("connection reset by peer")
        model._client.messages.stream = MagicMock(
            return_value=ExplodingStream(["def add(a, b):\n", "    return a + b"], boom)
        )
        response = MagicMock()

        try:
            model.completions(
                messages=[{"role": "user", "content": "x"}], response=response
            )
        except RuntimeError as e:
            assert e is boom
        else:
            raise AssertionError("expected the underlying stream error to propagate")

        payloads = [call.args[0] for call in response.stream.call_args_list]
        emitted = [p["choices"][0]["delta"]["content"] for p in payloads]
        # Two real chunks, then a marker so the user sees the response was
        # cut short instead of trusting the truncated code.
        assert emitted[:2] == ["def add(a, b):\n", "    return a + b"]
        assert "Stream interrupted" in emitted[-1]
        assert "connection reset by peer" in emitted[-1]
        # The marker payload must also carry the structured nbi_stream_error
        # field so the front-end auto-insert path can detect the failure and
        # skip writing the partial buffer to the user's cell.
        assert payloads[-1].get("nbi_stream_error") == "connection reset by peer"
        # finish() is the caller's responsibility on the error path; we must
        # not double-finish here.
        response.finish.assert_not_called()

    def test_mid_stream_exception_with_bracketed_text_keeps_structured_field(self):
        # Many real Anthropic / network errors contain bracketed fragments
        # like ``[SSL: CERTIFICATE_VERIFY_FAILED]`` or ``[Errno 11001]``.
        # The frontend marker-strip regex used to anchor on the first inner
        # ``]`` and leak the suffix into the user's code; the popover and
        # auto-insert paths now branch on nbi_stream_error rather than
        # parsing the marker, so this asserts the contract the frontend
        # depends on: the structured field carries the verbatim error text
        # regardless of how many brackets it contains.
        class ExplodingStream(_FakeMessageStream):
            def __init__(self, before_chunks, exc):
                super().__init__(before_chunks)

                def gen():
                    for c in before_chunks:
                        yield c
                    raise exc

                self.text_stream = gen()

        model = ClaudeChatModel.__new__(ClaudeChatModel)
        model._model_id = "claude-sonnet-test"
        model._model_name = "Claude Sonnet (test)"
        model._context_window = 200000
        model._supports_tools = True
        model._client = MagicMock()
        boom = RuntimeError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
        )
        model._client.messages.stream = MagicMock(
            return_value=ExplodingStream(["partial code"], boom)
        )
        response = MagicMock()

        try:
            model.completions(
                messages=[{"role": "user", "content": "x"}], response=response
            )
        except RuntimeError:
            pass

        payloads = [call.args[0] for call in response.stream.call_args_list]
        # Marker text contains the brackets verbatim — that's fine because
        # the frontend strips by line, not by inner-bracket boundary.
        assert "[SSL:" in payloads[-1]["choices"][0]["delta"]["content"]
        # Structured field is the source of truth for the frontend's
        # error-vs-success branch.
        assert payloads[-1].get("nbi_stream_error") == str(boom)


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


class TestParticipantCommandsDedupe:
    """`ClaudeCodeChatParticipant.commands` merges NBI built-ins with SDK
    server_info commands. When the SDK now lists a name NBI also ships
    (e.g. `/clear`), the slash must appear once in @-autocomplete."""

    def test_clear_not_duplicated_when_sdk_lists_it(self):
        participant = _make_participant()
        participant._client.server_info = {
            "commands": [
                {"name": "clear", "description": "Claude SDK clear"},
                {"name": "compact", "description": "Compact chat history"},
            ]
        }
        names = [cmd.name for cmd in participant.commands]
        assert names.count("clear") == 1
        # SDK description wins when both sources collide.
        clear = next(cmd for cmd in participant.commands if cmd.name == "clear")
        assert clear.description == "Claude SDK clear"
        assert "compact" in names

    def test_sdk_commands_appended_when_no_collision(self):
        participant = _make_participant()
        participant._client.server_info = {
            "commands": [
                {"name": "cost", "description": "Show cost"},
            ]
        }
        names = [cmd.name for cmd in participant.commands]
        assert names == ["clear", "cost"]

    def test_fallback_when_server_info_missing(self):
        participant = _make_participant()
        participant._client.server_info = None
        names = [cmd.name for cmd in participant.commands]
        assert names == ["compact", "context", "cost", "clear"]


class TestAssembleClientQuery:
    """Slash-command queries must not silently discard this turn's context
    lines (attachment @-mentions, cell pointers, output context).

    Only control-only commands (/clear, /cost, ...) drop the context —
    it is meaningless to them. Any other command is moved to the front
    (the CLI only recognizes a command at the start of the query) with
    the context preserved after it.
    """

    def test_plain_prompt_joins_all_lines(self):
        from notebook_intelligence.claude import assemble_client_query
        query = assemble_client_query([
            "The user attached @data.csv. Read it if relevant to the request.",
            "Summarize this file",
        ])
        assert query == (
            "The user attached @data.csv. Read it if relevant to the request."
            "\nSummarize this file"
        )

    def test_control_command_drops_context(self):
        from notebook_intelligence.claude import assemble_client_query
        query = assemble_client_query([
            "The user attached @data.csv. Read it if relevant to the request.",
            "/clear",
        ])
        assert query == "/clear"

    def test_control_command_matching_is_case_insensitive_and_token_based(self):
        from notebook_intelligence.claude import assemble_client_query
        assert assemble_client_query(["context line", "/Cost"]) == "/Cost"

    def test_custom_command_keeps_context_after_command(self):
        from notebook_intelligence.claude import assemble_client_query
        query = assemble_client_query([
            "The user attached @analysis.ipynb. Read it if relevant to the request.",
            "/review-notebook focus on performance",
        ])
        assert query.splitlines()[0] == "/review-notebook focus on performance"
        assert "@analysis.ipynb" in query

    def test_custom_command_without_context_is_unchanged(self):
        from notebook_intelligence.claude import assemble_client_query
        assert assemble_client_query(["/review-notebook"]) == "/review-notebook"

    def test_empty_lines_list(self):
        from notebook_intelligence.claude import assemble_client_query
        assert assemble_client_query([]) == ""

    def test_hidden_plan_mode_alias_still_leads_query(self):
        # The plan-mode aliases are intercepted via client_query.startswith,
        # so the command line must stay first even with context attached.
        from notebook_intelligence.claude import assemble_client_query
        query = assemble_client_query(["context line", "/enter-plan-mode"])
        assert query.startswith("/enter-plan-mode")
