# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Tests for the perf-diagnostics instrumentation wired around the frozen
notebook_intelligence.perf / notebook_intelligence.perf_probe APIs (see
.plans/perf-diagnostics-plan.md and .plans/perf-contracts.md).

These tests exercise the call sites added to extension.py, claude.py,
acp_agent.py and mcp_manager.py: they do not re-test perf.py's own internals
(covered by tests/test_perf.py) or perf_probe.py's checks (covered by
tests/test_perf_probe.py). All SDK/subprocess/network layers are mocked;
nothing here talks to a real Claude/ACP subprocess or the network.
"""

import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import tornado.web

from notebook_intelligence import perf


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_perf_state():
    def _reset():
        perf._enabled = False
        perf._attr_detail = "redacted"
        perf._log_to_file = False
        perf._log_dir = ""
        perf._ring_maxlen = 50
        with perf._registry_lock:
            perf._turns.clear()
        with perf._ring_lock:
            perf._ring = perf.deque(maxlen=perf._ring_maxlen)
        perf._teardown_sink()

    _reset()
    yield
    _reset()


def _enable_perf():
    perf.configure({"enabled": True, "attr_detail": "full"}, None)


def _span_names(turn):
    return [s["name"] for s in turn._spans]


def _spans_named(turn, name):
    return [s for s in turn._spans if s["name"] == name]


# ---------------------------------------------------------------------------
# WebsocketCopilotResponseEmitter: first_token / stream / egress
# ---------------------------------------------------------------------------


def _make_emitter(message_id="m-emit"):
    from notebook_intelligence.extension import WebsocketCopilotResponseEmitter

    emitter = object.__new__(WebsocketCopilotResponseEmitter)
    emitter.chatId = "chat-1"
    emitter.messageId = message_id
    emitter.participant_id = ""
    emitter.websocket_handler = MagicMock()
    emitter.chat_history = MagicMock()
    emitter.streamed_contents = []
    emitter.streamed_reasoning_contents = []
    emitter._io_loop = MagicMock()
    emitter._perf_stream_cm = None
    emitter._perf_stream_span = None
    emitter._perf_chunk_count = 0
    emitter._perf_byte_count = 0
    return emitter


class TestWebsocketEmitterInstrumentation:
    def test_stream_opens_span_and_first_token_event_once(self):
        _enable_perf()
        turn = perf.begin_turn("m-emit", "claude", time.time(), time.monotonic())
        emitter = _make_emitter("m-emit")

        chunk = {"type": "markdown", "content": "hello"}
        emitter.stream(chunk)
        emitter.stream(chunk)

        assert emitter._perf_chunk_count == 2
        assert emitter._perf_byte_count > 0
        # The "stream" span is opened once and left open across chunks; it
        # is only recorded into turn._spans once it is closed by finish().
        assert emitter._perf_stream_cm is not None
        assert _span_names(turn) == []

        events = [e for e in turn._events if e["name"] == "first_token"]
        assert len(events) == 1

    def test_finish_closes_stream_span_and_emits_egress_counts(self):
        _enable_perf()
        turn = perf.begin_turn("m-emit", "claude", time.time(), time.monotonic())
        emitter = _make_emitter("m-emit")
        emitter.stream({"type": "markdown", "content": "a"})
        emitter.stream({"type": "markdown", "content": "b"})

        emitter.finish()

        assert emitter._perf_stream_cm is None
        stream_spans = _spans_named(turn, "stream")
        assert len(stream_spans) == 1
        assert stream_spans[0]["status"] == "ok"

        egress_events = [e for e in turn._events if e["name"] == "egress"]
        assert len(egress_events) == 1
        assert egress_events[0]["attrs"]["count"] == 2
        assert egress_events[0]["attrs"]["bytes"] == emitter._perf_byte_count

    def test_disabled_perf_stream_and_finish_are_noop(self):
        # perf stays disabled (autouse fixture default) -- must not raise
        # even though perf.get_turn() returns None for every message_id.
        emitter = _make_emitter("m-off")
        emitter.stream({"type": "markdown", "content": "a"})
        emitter.finish()
        assert emitter._perf_stream_cm is None
        assert emitter._perf_chunk_count == 0

    def test_no_open_turn_stream_and_finish_are_noop(self):
        _enable_perf()  # enabled, but no turn begun for this message_id
        emitter = _make_emitter("m-no-turn")
        emitter.stream({"type": "markdown", "content": "a"})
        emitter.finish()
        assert emitter._perf_stream_cm is None


# ---------------------------------------------------------------------------
# WebsocketCopilotHandler._run_request_thread: guaranteed turn-close
# ---------------------------------------------------------------------------


class TestRunRequestThreadTurnClose:
    def _make_handler(self):
        from notebook_intelligence.extension import WebsocketCopilotHandler

        handler = MagicMock(spec=WebsocketCopilotHandler)
        handler._messageCallbackHandlers = {}
        return handler

    def test_success_closes_turn_ok_copies_user_wait_and_logs_summary(self, caplog):
        from notebook_intelligence.extension import WebsocketCopilotHandler

        _enable_perf()
        perf.begin_turn("m-ok", "claude", time.time(), time.monotonic())
        handler = self._make_handler()
        handler._messageCallbackHandlers["m-ok"] = SimpleNamespace(
            response_emitter=SimpleNamespace(user_input_wait_seconds=2.5),
            cancel_token=SimpleNamespace(is_cancel_requested=False)
        )

        async def _noop():
            return None

        caplog.set_level(logging.INFO, logger="notebook_intelligence.extension")
        WebsocketCopilotHandler._run_request_thread(handler, _noop(), "m-ok")

        assert perf.get_turn("m-ok") is None
        assert "m-ok" not in handler._messageCallbackHandlers
        assert "message_id=m-ok" in caplog.text
        assert "status=ok" in caplog.text

    def test_exception_closes_turn_error_and_reraises(self, caplog):
        from notebook_intelligence.extension import WebsocketCopilotHandler

        _enable_perf()
        perf.begin_turn("m-err", "claude", time.time(), time.monotonic())
        handler = self._make_handler()

        async def _raise():
            raise RuntimeError("boom")

        caplog.set_level(logging.INFO, logger="notebook_intelligence.extension")
        with pytest.raises(RuntimeError):
            WebsocketCopilotHandler._run_request_thread(handler, _raise(), "m-err")

        assert perf.get_turn("m-err") is None
        assert "status=error" in caplog.text

    def test_disabled_perf_skips_turn_bookkeeping_without_error(self):
        from notebook_intelligence.extension import WebsocketCopilotHandler

        handler = self._make_handler()

        async def _noop():
            return None

        WebsocketCopilotHandler._run_request_thread(handler, _noop(), "m-off")
        assert perf.get_turn("m-off") is None


# ---------------------------------------------------------------------------
# REST surface: GET perf/report, POST perf/probe
# ---------------------------------------------------------------------------


class TestPerfReportHandler:
    def _make_handler(self):
        from notebook_intelligence.extension import PerfReportHandler

        handler = MagicMock(spec=PerfReportHandler)
        handler.request = MagicMock()
        return handler

    def test_404_when_perf_disabled(self):
        from notebook_intelligence.extension import PerfReportHandler

        handler = self._make_handler()
        with pytest.raises(tornado.web.HTTPError) as excinfo:
            _run(PerfReportHandler.get(handler))
        assert excinfo.value.status_code == 404

    def test_200_returns_ring_report_with_probe_target(self):
        from notebook_intelligence import extension as ext
        from notebook_intelligence.extension import PerfReportHandler

        _enable_perf()
        perf.begin_turn("m1", "claude", time.time(), time.monotonic()).close("ok")

        handler = self._make_handler()
        fake_manager = SimpleNamespace(nbi_config=SimpleNamespace())

        with patch.object(ext, "ai_service_manager", fake_manager), patch.object(
            ext.perf_probe, "_resolve_target_base_url", return_value="http://localhost:1234"
        ):
            _run(PerfReportHandler.get(handler))

        body = json.loads(handler.finish.call_args[0][0])
        assert body["probe_target"] == "http://localhost:1234"
        assert "aggregates" in body


class TestPerfProbeHandler:
    def _make_handler(self, body=b"{}"):
        from notebook_intelligence.extension import PerfProbeHandler

        handler = MagicMock(spec=PerfProbeHandler)
        handler.request = MagicMock()
        handler.request.body = body
        handler.perf_probe_network_allowed = True
        return handler

    def test_404_when_perf_disabled(self):
        from notebook_intelligence.extension import PerfProbeHandler

        handler = self._make_handler()
        with pytest.raises(tornado.web.HTTPError) as excinfo:
            _run(PerfProbeHandler.post(handler))
        assert excinfo.value.status_code == 404

    def test_invalid_json_body_returns_400(self):
        from notebook_intelligence.extension import PerfProbeHandler

        _enable_perf()
        handler = self._make_handler(body=b"{not json")
        _run(PerfProbeHandler.post(handler))
        handler.set_status.assert_called_with(400)

    def test_network_probe_forced_off_by_policy_even_if_requested(self):
        from notebook_intelligence import extension as ext
        from notebook_intelligence.extension import PerfProbeHandler

        _enable_perf()
        handler = self._make_handler(body=json.dumps({"network": True}).encode())
        handler.perf_probe_network_allowed = False
        fake_manager = SimpleNamespace(nbi_config=SimpleNamespace())
        recorded = {}

        def fake_run_probe(include_network, nbi_config):
            recorded["include_network"] = include_network
            return {"checks": []}

        with patch.object(ext, "ai_service_manager", fake_manager), patch.object(
            ext.perf_probe, "run_probe", fake_run_probe
        ):
            _run(PerfProbeHandler.post(handler))

        assert recorded["include_network"] is False
        body = json.loads(handler.finish.call_args[0][0])
        assert body == {"checks": []}

    def test_network_probe_allowed_when_requested_and_policy_permits(self):
        from notebook_intelligence import extension as ext
        from notebook_intelligence.extension import PerfProbeHandler

        _enable_perf()
        handler = self._make_handler(body=json.dumps({"network": True}).encode())
        handler.perf_probe_network_allowed = True
        fake_manager = SimpleNamespace(nbi_config=SimpleNamespace())
        recorded = {}

        def fake_run_probe(include_network, nbi_config):
            recorded["include_network"] = include_network
            return {"checks": []}

        with patch.object(ext, "ai_service_manager", fake_manager), patch.object(
            ext.perf_probe, "run_probe", fake_run_probe
        ):
            _run(PerfProbeHandler.post(handler))

        assert recorded["include_network"] is True


# ---------------------------------------------------------------------------
# Claude mode: tool:<name> span via the JUPYTER_UI_TOOLS wrapper
# ---------------------------------------------------------------------------


class TestClaudeToolWrapper:
    def _make_fake_tool(self, name="notebook_get_cells"):
        from claude_agent_sdk import SdkMcpTool

        async def _handler(args):
            return {"content": [{"type": "text", "text": "ok"}]}

        return SdkMcpTool(name=name, description="test tool", input_schema={}, handler=_handler)

    def test_wrapped_tool_records_span_when_single_turn_open(self):
        from notebook_intelligence import claude as claude_mod

        _enable_perf()
        turn = perf.begin_turn("m-tool", "claude", time.time(), time.monotonic())
        wrapped = claude_mod._perf_wrap_tool(self._make_fake_tool())

        result = _run(wrapped.handler({}))

        assert result == {"content": [{"type": "text", "text": "ok"}]}
        turn.close("ok")
        spans = _spans_named(turn, "tool:notebook_get_cells")
        assert len(spans) == 1
        assert spans[0]["attrs"].get("tool") == "notebook_get_cells"

    def test_wrapped_tool_skips_instrumentation_with_no_open_turn(self):
        from notebook_intelligence import claude as claude_mod

        _enable_perf()  # enabled, but no turn open anywhere
        wrapped = claude_mod._perf_wrap_tool(self._make_fake_tool())
        result = _run(wrapped.handler({}))
        assert result == {"content": [{"type": "text", "text": "ok"}]}

    def test_wrapped_tool_skips_instrumentation_when_perf_disabled(self):
        from notebook_intelligence import claude as claude_mod

        wrapped = claude_mod._perf_wrap_tool(self._make_fake_tool())
        result = _run(wrapped.handler({}))
        assert result == {"content": [{"type": "text", "text": "ok"}]}

    def test_perf_single_open_turn_skips_when_multiple_turns_open(self):
        from notebook_intelligence import claude as claude_mod

        _enable_perf()
        perf.begin_turn("m-a", "claude", time.time(), time.monotonic())
        perf.begin_turn("m-b", "claude", time.time(), time.monotonic())
        assert claude_mod._perf_single_open_turn() is None


# ---------------------------------------------------------------------------
# ACP mode: connect/cold span, tool:<kind> span
# ---------------------------------------------------------------------------


class TestAcpConnectSpan:
    def test_cold_connect_span_recorded_when_ensure_started_fails(self):
        from notebook_intelligence.acp_agent import AcpAgentClient

        _enable_perf()
        turn = perf.begin_turn("m-acp-connect", "acp", time.time(), time.monotonic())

        client = object.__new__(AcpAgentClient)
        client._turn_lock = threading.Lock()
        client._thread = None
        client._shutting_down = False
        client._start_error = "boom"
        client._ensure_started = MagicMock(return_value=False)

        request = SimpleNamespace()
        response = SimpleNamespace(message_id="m-acp-connect")

        with patch.object(
            AcpAgentClient, "agent_spec", new_callable=PropertyMock, return_value=SimpleNamespace(label="Codex")
        ):
            result = client.query(request, response)

        assert result == "boom"
        client._ensure_started.assert_called_once()
        turn.close("ok")
        spans = _spans_named(turn, "connect")
        assert len(spans) == 1
        assert spans[0]["attrs"].get("cold") is True

    def test_warm_connect_span_recorded_when_thread_already_running(self):
        from notebook_intelligence.acp_agent import AcpAgentClient

        _enable_perf()
        turn = perf.begin_turn("m-acp-warm", "acp", time.time(), time.monotonic())

        alive_thread = MagicMock()
        alive_thread.is_alive.return_value = True

        client = object.__new__(AcpAgentClient)
        client._turn_lock = threading.Lock()
        client._thread = alive_thread
        client._shutting_down = False
        client._start_error = None
        # Still fail to start so query() returns early, before touching the
        # event loop -- only the cold/warm computation is under test here.
        client._ensure_started = MagicMock(return_value=False)

        response = SimpleNamespace(message_id="m-acp-warm")
        with patch.object(
            AcpAgentClient, "agent_spec", new_callable=PropertyMock, return_value=SimpleNamespace(label="Codex")
        ):
            client.query(SimpleNamespace(), response)

        turn.close("ok")
        spans = _spans_named(turn, "connect")
        assert len(spans) == 1
        assert spans[0]["attrs"].get("cold") is False


class TestAcpToolSpan:
    def _make_client(self):
        from notebook_intelligence.acp_agent import _NbiAcpClient

        client = object.__new__(_NbiAcpClient)
        client._tool_state = {}
        client._tool_perf_spans = {}
        return client

    def test_tool_call_opens_and_closes_span_keyed_by_kind(self):
        _enable_perf()
        turn = perf.begin_turn("m-acp-tool", "acp", time.time(), time.monotonic())
        client = self._make_client()
        resp = MagicMock()
        resp.message_id = "m-acp-tool"

        create_update = SimpleNamespace(
            tool_call_id="tc-1", kind="edit", title="Edit cell", status=None, content=None
        )
        client._emit_tool_call(resp, create_update)
        assert "tc-1" in client._tool_perf_spans

        done_update = SimpleNamespace(
            tool_call_id="tc-1", kind=None, title=None, status="completed", content=None
        )
        client._emit_tool_call(resp, done_update)
        assert "tc-1" not in client._tool_perf_spans

        turn.close("ok")
        spans = _spans_named(turn, "tool:edit")
        assert len(spans) == 1
        assert spans[0]["attrs"].get("tool") == "edit"
        assert spans[0]["status"] == "ok"

    def test_failed_tool_call_closes_span_with_ok_status_but_failed_state(self):
        # The span itself only tracks timing/identity; the ACP-level
        # completed-vs-failed outcome is carried in the streamed
        # ToolCallData, not in the span's own status.
        _enable_perf()
        turn = perf.begin_turn("m-acp-tool2", "acp", time.time(), time.monotonic())
        client = self._make_client()
        resp = MagicMock()
        resp.message_id = "m-acp-tool2"

        client._emit_tool_call(
            resp,
            SimpleNamespace(tool_call_id="tc-2", kind="execute", title="Run", status=None, content=None),
        )
        client._emit_tool_call(
            resp,
            SimpleNamespace(tool_call_id="tc-2", kind=None, title=None, status="failed", content=None),
        )

        assert "tc-2" not in client._tool_perf_spans
        turn.close("ok")
        assert len(_spans_named(turn, "tool:execute")) == 1

    def test_disabled_perf_emit_tool_call_is_noop(self):
        client = self._make_client()
        resp = MagicMock()
        resp.message_id = "m-acp-off"
        client._emit_tool_call(
            resp,
            SimpleNamespace(tool_call_id="tc-3", kind="read", title="Read", status="completed", content=None),
        )
        assert client._tool_perf_spans == {}


# ---------------------------------------------------------------------------
# MCP manager: nested tool:<name> span carrying the server attribute
# ---------------------------------------------------------------------------


class TestMcpManagerToolSpan:
    def test_call_tool_annotates_enclosing_span_with_server_attr(self):
        from notebook_intelligence.mcp_manager import MCPTool

        _enable_perf()
        turn = perf.begin_turn("m-mcp", "claude", time.time(), time.monotonic())

        fake_server = SimpleNamespace(name="my-mcp-server")
        fake_server.call_tool = MagicMock(return_value=object())

        tool = MCPTool(fake_server, "search_docs", "desc", {"properties": {"query": {}}})
        response = SimpleNamespace(message_id="m-mcp")

        # Simulate the enclosing span api.py's dispatch loop opens; the MCP
        # layer must annotate it rather than nest a duplicate (double-count).
        with turn.span("tool:search_docs", tool="search_docs", builtin=True):
            _run(tool.handle_tool_call(None, response, {}, {"query": "x"}))

        fake_server.call_tool.assert_called_once_with("search_docs", {"query": "x"})
        turn.close("ok")
        spans = _spans_named(turn, "tool:search_docs")
        assert len(spans) == 1
        assert spans[0]["attrs"].get("server") == "my-mcp-server"
        assert spans[0]["attrs"].get("tool") == "search_docs"

    def test_call_tool_noop_when_no_turn_open(self):
        from notebook_intelligence.mcp_manager import MCPTool

        _enable_perf()
        fake_server = SimpleNamespace(name="my-mcp-server")
        fake_server.call_tool = MagicMock(return_value=object())
        tool = MCPTool(fake_server, "search_docs", "desc", {"properties": {}})
        response = SimpleNamespace(message_id="m-mcp-no-turn")

        _run(tool.handle_tool_call(None, response, {}, {}))
        fake_server.call_tool.assert_called_once()

    def test_call_tool_error_is_caught_and_returned_as_string(self):
        from notebook_intelligence.mcp_manager import MCPTool

        _enable_perf()
        turn = perf.begin_turn("m-mcp-err", "claude", time.time(), time.monotonic())
        fake_server = SimpleNamespace(name="my-mcp-server")
        fake_server.call_tool = MagicMock(side_effect=RuntimeError("boom"))
        tool = MCPTool(fake_server, "search_docs", "desc", {"properties": {}})
        response = SimpleNamespace(message_id="m-mcp-err")

        with turn.span("tool:search_docs", tool="search_docs", builtin=True):
            result = _run(tool.handle_tool_call(None, response, {}, {}))

        assert "boom" in result
        turn.close("ok")
        spans = _spans_named(turn, "tool:search_docs")
        assert len(spans) == 1
        assert spans[0]["attrs"].get("ok") is False

def test_cancel_requested_records_cancelled_status():
    from notebook_intelligence.extension import WebsocketCopilotHandler

    _enable_perf()
    perf.begin_turn("m-cancel", "claude", time.time(), time.monotonic())
    handler = MagicMock(spec=WebsocketCopilotHandler)
    handler._messageCallbackHandlers = {}
    handler._messageCallbackHandlers["m-cancel"] = SimpleNamespace(
        response_emitter=SimpleNamespace(user_input_wait_seconds=0.0),
        cancel_token=SimpleNamespace(is_cancel_requested=True),
    )

    async def _noop():
        return None

    WebsocketCopilotHandler._run_request_thread(handler, _noop(), "m-cancel")
    snapshot = perf.report_snapshot()
    assert snapshot["turns"][-1]["status"] == "cancelled"

def test_progress_chunk_before_content_does_not_crash_or_open_stream():
    from notebook_intelligence.api import ProgressData, MarkdownData

    _enable_perf()
    perf.begin_turn("m-prog", "claude", time.time(), time.monotonic())
    emitter = _make_emitter("m-prog")

    emitter.stream(ProgressData("Thinking"))
    assert emitter._perf_stream_cm is None

    emitter.stream(MarkdownData("hello"))
    turn = perf.get_turn("m-prog")
    doc = turn.close("ok")
    assert any(ev["name"] == "first_token" for ev in doc["events"])
    assert any(sp["name"] == "stream" for sp in doc["spans"]) or emitter._perf_stream_cm is not None
