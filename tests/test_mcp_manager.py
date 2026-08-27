"""Regression tests for MCPServerImpl worker lifecycle behavior.

Mirrors TestWorkerThreadSignalRace in test_claude_client.py for the MCP code path,
confirming the snapshot pattern applied to mcp_manager._client_thread_func() is
equally locked in there.
"""

import asyncio
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import mcp
import notebook_intelligence.mcp_manager as mcp_manager_module
import pytest
from mcp.types import ErrorData, METHOD_NOT_FOUND
from notebook_intelligence.api import SignalImpl
from notebook_intelligence.mcp_manager import (
    MCPServerEventType,
    MCPServerImpl,
    MCPServerStatus,
)


def _make_mcp_server():
    """Build an ``MCPServerImpl`` without invoking ``__init__`` / ``connect``."""
    server = MCPServerImpl.__new__(MCPServerImpl)
    server._manager = Mock(websocket_connector=None)
    server._name = "test"
    server._stdio_params = None
    server._streamable_http_params = None
    server._auto_approve_tools = set()
    server._tried_to_get_tool_list = False
    server._mcp_tools = []
    server._mcp_prompts = []
    server._session = None
    server._client_queue = Queue()
    server._client_thread_signal = SignalImpl()
    server._client_thread = None
    server._status = MCPServerStatus.NotConnected
    server._tool_prompt_list_lock = threading.Lock()
    server._connection_state_lock = threading.RLock()
    server._connection_generation = 1
    server._capability_retry_attempts = 0
    server._capability_retry_timer = None
    server._capability_retry_limit = 0
    return server


def _disconnect(server):
    """Simulate the field-nulling that disconnect() performs on the server instance."""
    server._client_queue = None
    server._client_thread_signal = None
    server._client_thread = None
    server._status = MCPServerStatus.NotConnected


class _SignalingQueue(Queue):
    def __init__(self):
        super().__init__()
        self.item_added = threading.Event()

    def put(self, item, block=True, timeout=None):
        super().put(item, block=block, timeout=timeout)
        self.item_added.set()


class TestMCPManagerWorkerThreadSignalRace:
    def test_snapshot_survives_disconnect(self):
        server = _make_mcp_server()
        original_signal = server._client_thread_signal
        received = []
        original_signal.connect(lambda data: received.append(data))
        signal = server._client_thread_signal
        _disconnect(server)
        assert server._client_thread_signal is None
        if signal is not None:
            signal.emit({"id": "x", "data": "stopped"})
        assert received == [{"id": "x", "data": "stopped"}]

    def test_signal_already_none_at_snapshot_time_is_safe(self):
        server = _make_mcp_server()
        _disconnect(server)
        signal = server._client_thread_signal
        assert signal is None
        if signal is not None:
            signal.emit({"id": "x", "data": "stopped"})

    def test_queue_snapshot_survives_disconnect(self):
        server = _make_mcp_server()
        original_queue = server._client_queue
        original_queue.put({"id": "x", "type": "list-tools"})
        queue = server._client_queue
        _disconnect(server)
        assert server._client_queue is None
        event = queue.get(block=False)
        assert event == {"id": "x", "type": "list-tools"}

    def test_queue_already_none_at_snapshot_time_exits_cleanly(self):
        server = _make_mcp_server()
        _disconnect(server)
        queue = server._client_queue
        assert queue is None
        if queue is None:
            return
        queue.get(block=False)


class _FailingPromptClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def get_prompt(self, _name, _args):
        raise RuntimeError("prompt exploded")

    async def list_tools(self):
        return ["worker-survived"]


class _NonePayloadClient(_FailingPromptClient):
    async def list_tools(self):
        return None


class _ToolsOnlyClient(_FailingPromptClient):
    supports_tools = True
    supports_prompts = False

    async def list_prompts(self):
        raise mcp.McpError(ErrorData(
            code=METHOD_NOT_FOUND,
            message="Method not found",
        ))


class _BlockingToolsClient(_FailingPromptClient):
    def __init__(self, entered, release):
        self._entered = entered
        self._release = release

    async def list_tools(self):
        self._entered.set()
        while not self._release.is_set():
            await asyncio.sleep(0.01)
        return []


class _FailingEnterClient:
    def __init__(self, entered, fail):
        self._entered = entered
        self._fail = fail

    async def __aenter__(self):
        self._entered.set()
        while not self._fail.is_set():
            await asyncio.sleep(0.01)
        raise RuntimeError("client entry exploded")

    async def __aexit__(self, *_args):
        return None


class TestMCPManagerEventFailures:
    def test_timed_out_disconnect_keeps_stale_worker_on_old_queue(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            mcp_manager_module,
            "MCP_SERVER_RESPONSE_TIMEOUT",
            0.01,
        )
        server = _make_mcp_server()
        old_queue = server._client_queue
        old_signal = server._client_thread_signal
        entered = threading.Event()
        release = threading.Event()
        client = _BlockingToolsClient(entered, release)

        async def _get_client():
            return client

        server._get_client = _get_client
        old_worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(old_queue, old_signal, 1),),
            daemon=True,
        )
        server._client_thread = old_worker
        old_worker.start()

        request_result = {}
        sender = threading.Thread(
            target=lambda: request_result.update(
                response=server._send_mcp_request(MCPServerEventType.ListTools)
            ),
            daemon=True,
        )
        sender.start()
        assert entered.wait(timeout=1)

        # StopServer queues behind the blocked tools call and times out. The
        # completed disconnect invalidates generation 1 without joining it.
        server.disconnect()

        # Simulate connect() installing generation 3 while the old client call
        # is still in flight. The stale worker must retain old_queue rather
        # than rereading these replacement fields on its next loop.
        new_queue = Queue()
        replacement_event = {
            "id": "new-generation",
            "type": MCPServerEventType.ListTools,
            "args": None,
        }
        with server._connection_state_lock:
            server._connection_generation += 1
            server._client_queue = new_queue
            server._client_thread_signal = SignalImpl()
            server._client_thread = Mock()
            server._status = MCPServerStatus.Connecting
        new_queue.put(replacement_event)

        release.set()
        old_worker.join(timeout=1)
        sender.join(timeout=1)

        assert new_queue.get(block=False) == replacement_event
        assert server.status == MCPServerStatus.Connecting
        assert "response" in request_result
        assert not old_worker.is_alive()
        assert not sender.is_alive()

    def test_unadvertised_prompt_capability_is_logged_and_returns_empty(
        self,
        caplog,
    ):
        caplog.set_level("DEBUG", logger="notebook_intelligence.mcp_manager")
        server = _make_mcp_server()
        client = _ToolsOnlyClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        response = server._send_mcp_request(MCPServerEventType.ListPrompts)
        server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert response == {"data": [], "success": True, "error": None}
        assert "did not advertise prompts" in caplog.text
        assert not worker.is_alive()

    def test_prompt_failure_returns_immediately_and_worker_stays_alive(self):
        server = _make_mcp_server()
        client = _FailingPromptClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        failed = server._send_mcp_request(
            MCPServerEventType.GetPromptValue,
            {"prompt_name": "broken", "prompt_args": {}},
        )
        survived = server._send_mcp_request(MCPServerEventType.ListTools)
        stopped = server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert failed["success"] is False
        assert "prompt exploded" in failed["error"]
        assert survived == {
            "data": ["worker-survived"],
            "success": True,
            "error": None,
        }
        assert stopped["success"] is True
        assert not worker.is_alive()

    def test_successful_none_payload_returns_as_terminal_response(self):
        server = _make_mcp_server()
        client = _NonePayloadClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        response = server._send_mcp_request(MCPServerEventType.ListTools)
        server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert response == {
            "data": None,
            "success": True,
            "error": None,
        }
        assert not worker.is_alive()

    def test_unknown_event_returns_error_instead_of_timing_out(self):
        server = _make_mcp_server()
        client = _FailingPromptClient()

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()

        response = server._send_mcp_request("unknown-event")
        server._send_mcp_request(MCPServerEventType.StopServer)
        worker.join(timeout=2)

        assert response["success"] is False
        assert "Unknown MCP server event type" in response["error"]
        assert not worker.is_alive()

    def test_in_flight_request_stops_when_worker_exits(self):
        server = _make_mcp_server()
        queue = _SignalingQueue()
        server._client_queue = queue
        entered = threading.Event()
        fail = threading.Event()
        client = _FailingEnterClient(entered, fail)

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()
        assert entered.wait(timeout=1)

        result = {}
        sender = threading.Thread(
            target=lambda: result.update(
                response=server._send_mcp_request(MCPServerEventType.ListTools)
            ),
            daemon=True,
        )
        sender.start()
        assert queue.item_added.wait(timeout=1)
        fail.set()
        sender.join(timeout=1)
        worker.join(timeout=1)

        response = result["response"]
        assert response["success"] is False
        assert "worker stopped" in response["error"]
        assert not sender.is_alive()
        assert not worker.is_alive()

    def test_initial_refresh_preserves_worker_failure_status(self):
        server = _make_mcp_server()
        queue = _SignalingQueue()
        server._client_queue = queue
        server._mcp_tools = [Mock()]
        server._mcp_prompts = [Mock()]
        retry_timer = Mock()
        server._capability_retry_timer = retry_timer
        server._capability_retry_attempts = 3
        entered = threading.Event()
        fail = threading.Event()
        client = _FailingEnterClient(entered, fail)

        async def _get_client():
            return client

        server._get_client = _get_client
        worker = threading.Thread(
            target=asyncio.run,
            args=(server._client_thread_func(),),
            daemon=True,
        )
        server._client_thread = worker
        worker.start()
        assert entered.wait(timeout=1)

        refresh = threading.Thread(
            target=server._update_tool_and_prompt_list,
            daemon=True,
        )
        refresh.start()
        assert queue.item_added.wait(timeout=1)
        fail.set()
        refresh.join(timeout=1)
        worker.join(timeout=1)

        assert server.status == MCPServerStatus.FailedToConnect
        assert server.get_tools() == []
        assert server.get_prompts() == []
        retry_timer.cancel.assert_called_once_with()
        assert server._capability_retry_attempts == 0
        assert not refresh.is_alive()
        assert not worker.is_alive()


class TestMCPManagerCapabilityRefresh:
    @pytest.mark.parametrize("value", ["invalid", "0", "-1", "nan", "inf"])
    def test_invalid_timing_environment_values_use_default(
        self,
        monkeypatch,
        value,
    ):
        monkeypatch.setenv("NBI_TEST_TIMING", value)

        assert mcp_manager_module._read_float_env("NBI_TEST_TIMING", 5) == 5

    def test_concurrent_connect_starts_only_one_worker(self, monkeypatch):
        server = _make_mcp_server()
        server._client_thread = None
        worker = Mock()
        thread_factory = Mock(return_value=worker)
        real_thread = threading.Thread
        monkeypatch.setattr(mcp_manager_module.threading, "Thread", thread_factory)
        server._update_tool_and_prompt_list_async = Mock()

        callers = [real_thread(target=server.connect) for _ in range(2)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(timeout=1)

        worker_coroutine = thread_factory.call_args.kwargs["args"][0]
        worker_coroutine.close()
        assert thread_factory.call_count == 1
        worker.start.assert_called_once_with()
        server._update_tool_and_prompt_list_async.assert_called_once_with(2)

    def test_worker_start_failure_clears_aborted_connection_resources(
        self,
        monkeypatch,
    ):
        server = _make_mcp_server()
        server._client_thread = None
        worker = Mock()
        worker.start.side_effect = RuntimeError("worker unavailable")
        thread_factory = Mock(return_value=worker)
        monkeypatch.setattr(mcp_manager_module.threading, "Thread", thread_factory)

        server.connect()
        worker_coroutine = thread_factory.call_args.kwargs["args"][0]
        worker_coroutine.close()

        assert server.status == MCPServerStatus.FailedToConnect
        assert server._client_thread is None
        assert server._client_queue is None
        assert server._client_thread_signal is None

    def test_each_worker_gets_a_fresh_client(self):
        server = _make_mcp_server()
        server._stdio_params = Mock()
        first_client = Mock()
        second_client = Mock()
        server._create_client = Mock(side_effect=[first_client, second_client])

        first = asyncio.run(server._get_client())
        second = asyncio.run(server._get_client())

        assert first is first_client
        assert second is second_client
        assert first is not second

    def test_request_state_snapshot_is_taken_under_connection_lock(self):
        server = _make_mcp_server()
        worker = Mock()
        worker.is_alive.return_value = False
        server._client_thread = worker
        lock = MagicMock()
        server._connection_state_lock = lock

        response = server._send_mcp_request(MCPServerEventType.ListTools)

        lock.__enter__.assert_called_once_with()
        lock.__exit__.assert_called_once()
        assert response["success"] is False

    def test_refresh_thread_start_failure_stops_started_worker(self, monkeypatch):
        server = _make_mcp_server()
        server._client_thread = None
        stale_retry_timer = Mock()
        server._capability_retry_timer = stale_retry_timer
        server._capability_retry_attempts = 3
        worker = Mock()
        thread_factory = Mock(return_value=worker)
        monkeypatch.setattr(mcp_manager_module.threading, "Thread", thread_factory)
        server._update_tool_and_prompt_list_async = Mock(
            side_effect=RuntimeError("refresh thread unavailable")
        )
        server._send_mcp_request = Mock(return_value={
            "data": "stopped",
            "success": True,
            "error": None,
        })

        server.connect()
        worker_coroutine = thread_factory.call_args.kwargs["args"][0]
        worker_coroutine.close()

        worker.start.assert_called_once_with()
        stale_retry_timer.cancel.assert_called_once_with()
        assert server._capability_retry_attempts == 0
        server._send_mcp_request.assert_called_once_with(
            MCPServerEventType.StopServer
        )
        assert server.status == MCPServerStatus.FailedToConnect
        assert server._client_thread is None
        assert server._client_queue is None

    def test_transient_direct_refresh_schedules_retry(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        server._capability_retry_limit = 1
        server._send_mcp_request = Mock(return_value={
            "data": None,
            "success": False,
            "error": "temporary timeout",
        })
        server._schedule_capability_refresh_retry = Mock()

        assert server.update_tool_list() is False

        server._schedule_capability_refresh_retry.assert_called_once_with(1)

    def test_capability_retry_is_bounded_and_runs_for_current_generation(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(
            mcp_manager_module,
            "MCP_CAPABILITY_RETRY_DELAY",
            0.05,
        )
        server = _make_mcp_server()
        server._client_thread = Mock()
        server._capability_retry_limit = 1
        server._update_tool_and_prompt_list = Mock()

        server._schedule_capability_refresh_retry(1)
        timer = server._capability_retry_timer
        timer.join(timeout=1)
        server._schedule_capability_refresh_retry(1)

        server._update_tool_and_prompt_list.assert_called_once_with(1)
        assert server._capability_retry_attempts == 1
        assert not timer.is_alive()

    def test_disconnect_invalidates_in_flight_refresh_status(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        refresh_started = threading.Event()
        release_refresh = threading.Event()

        def _send(event_type, _event_args=None):
            if event_type == MCPServerEventType.StopServer:
                return {"data": "stopped", "success": True, "error": None}
            assert event_type == MCPServerEventType.ListTools
            refresh_started.set()
            assert release_refresh.wait(timeout=1)
            return {"data": [], "success": True, "error": None}

        server._send_mcp_request = Mock(side_effect=_send)
        refresh = threading.Thread(
            target=server._update_tool_and_prompt_list,
            daemon=True,
        )
        refresh.start()
        assert refresh_started.wait(timeout=1)

        server.disconnect()
        release_refresh.set()
        refresh.join(timeout=1)

        assert server.status == MCPServerStatus.NotConnected
        assert not refresh.is_alive()

    def test_delayed_refresh_aborts_after_disconnect(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        server._status = MCPServerStatus.Connected
        server._send_mcp_request = Mock(return_value={
            "data": "stopped",
            "success": True,
            "error": None,
        })

        server.disconnect()
        server._send_mcp_request.reset_mock()
        server._update_tool_and_prompt_list(expected_generation=1)

        assert server.status == MCPServerStatus.NotConnected
        server._send_mcp_request.assert_not_called()

    def test_tool_failure_does_not_suppress_successful_prompt_refresh(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        prompt = SimpleNamespace(
            name="healthy-prompt",
            title="Healthy prompt",
            description="still available",
            arguments=[],
        )
        server._send_mcp_request = Mock(side_effect=[
            {"data": None, "success": False, "error": "tools failed"},
            {"data": [prompt], "success": True, "error": None},
        ])

        server._update_tool_and_prompt_list()

        assert server.get_tools() == []
        assert [item.name for item in server.get_prompts()] == ["healthy-prompt"]
        assert server.status == MCPServerStatus.FailedToUpdateToolList
        assert [item.args for item in server._send_mcp_request.call_args_list] == [
            (MCPServerEventType.ListTools,),
            (MCPServerEventType.ListPrompts,),
        ]

    def test_failed_refreshes_preserve_last_known_tools_and_prompts(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        previous_tools = [Mock()]
        previous_prompts = [Mock()]
        server._mcp_tools = previous_tools
        server._mcp_prompts = previous_prompts
        server._send_mcp_request = Mock(side_effect=[
            {"data": None, "success": False, "error": "tools failed"},
            {"data": None, "success": False, "error": "prompts failed"},
        ])

        server.update_tool_list()
        server.update_prompts_list()

        assert server._mcp_tools is previous_tools
        assert server._mcp_prompts is previous_prompts
        assert server.status == MCPServerStatus.FailedToUpdatePromptList

    def test_disconnected_direct_refresh_preserves_cached_capabilities(self):
        server = _make_mcp_server()
        previous_tools = [Mock()]
        previous_prompts = [Mock()]
        server._mcp_tools = previous_tools
        server._mcp_prompts = previous_prompts

        assert server.update_tool_list() is False
        assert server.update_prompts_list() is False

        assert server._mcp_tools is previous_tools
        assert server._mcp_prompts is previous_prompts

    def test_invalid_list_payloads_preserve_last_known_collections(self):
        server = _make_mcp_server()
        server._client_thread = Mock()
        previous_tools = [Mock()]
        previous_prompts = [Mock()]
        server._mcp_tools = previous_tools
        server._mcp_prompts = previous_prompts
        server._send_mcp_request = Mock(side_effect=[
            {"data": None, "success": True, "error": None},
            {"data": None, "success": True, "error": None},
        ])

        server.update_tool_list()
        server.update_prompts_list()

        assert server._mcp_tools is previous_tools
        assert server._mcp_prompts is previous_prompts
        assert server.status == MCPServerStatus.FailedToUpdatePromptList

    def test_invalid_prompt_messages_return_none(self):
        server = _make_mcp_server()
        prompt = Mock(arguments=[])
        server.get_prompt = Mock(return_value=prompt)
        server._send_mcp_request = Mock(return_value={
            "data": None,
            "success": True,
            "error": None,
        })

        result = server.get_prompt_value("broken")

        assert result is None
