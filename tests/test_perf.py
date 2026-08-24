# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Tests for the opt-in performance diagnostics recorder (notebook_intelligence.perf).

The module keeps its state at module scope (turn registry, ring buffer, sink),
since turns are looked up by message_id across threads rather than passed
around as an object. The ``_reset_perf_state`` fixture below resets that
state before and after every test so tests don't leak into one another.
"""

import json
import os
import threading
import time

import pytest

from notebook_intelligence import perf
from notebook_intelligence.feature_flags import (
    POLICY_FORCE_OFF,
    POLICY_FORCE_ON,
    POLICY_USER_CHOICE,
)


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
            perf._ring.clear()
            perf._ring = perf.deque(maxlen=perf._ring_maxlen)
        perf._teardown_sink()

    _reset()
    yield
    _reset()


def _close_simple_turn(message_id="m1", status="ok"):
    handle = perf.begin_turn(message_id, "claude", time.time(), time.monotonic())
    with handle.span("ingress"):
        pass
    handle.close(status)
    return handle


class TestDisabledNoOp:
    def test_begin_turn_returns_none_when_disabled(self):
        perf.configure({"enabled": False}, None)
        assert perf.enabled() is False
        assert perf.begin_turn("m1", "claude", time.time(), time.monotonic()) is None

    def test_get_turn_returns_none_when_disabled(self):
        perf.configure({"enabled": False}, None)
        assert perf.get_turn("does-not-exist") is None

    def test_report_snapshot_still_works_when_disabled(self):
        perf.configure({"enabled": False}, None)
        snapshot = perf.report_snapshot()
        assert snapshot["turns"] == []
        assert snapshot["aggregates"]["totals"]["turn_count"] == 0


class TestTurnLifecycle:
    def test_full_lifecycle_produces_expected_span_tree(self):
        perf.configure({"enabled": True, "attr_detail": "full"}, None)
        handle = perf.begin_turn("m-lifecycle", "claude", time.time(), time.monotonic())
        assert handle is not None
        assert perf.get_turn("m-lifecycle") is handle

        with handle.span("dispatch", provider="anthropic"):
            with handle.span("connect", cold=True):
                pass
        handle.event("retry", reason="timeout")
        handle.set_result(input_tokens=10, output_tokens=20, duration_ms=123.4, num_turns=1)
        handle.add_user_wait(0.5)
        handle.close("ok")

        # Closing removes the turn from the live registry.
        assert perf.get_turn("m-lifecycle") is None

        snapshot = perf.report_snapshot()
        docs = [t for t in snapshot["turns"] if t["message_id"] == "m-lifecycle"]
        assert len(docs) == 1
        doc = docs[0]

        assert doc["mode"] == "claude"
        assert doc["status"] == "ok"
        assert doc["dropped_spans"] == 0
        assert doc["dropped_attrs"] == 0
        assert doc["tokens"] == {"input": 10, "output": 20}
        assert doc["sdk"]["duration_ms"] == 123.4
        assert doc["sdk"]["num_turns"] == 1
        assert doc["total_ms"] >= 0
        assert doc["active_ms"] >= 0

        span_names = {s["name"] for s in doc["spans"]}
        assert span_names == {"dispatch", "connect"}
        event_names = {e["name"] for e in doc["events"]}
        assert event_names == {"retry"}

    def test_model_attr_is_captured_from_any_span_or_event(self):
        perf.configure({"enabled": True}, None)
        handle = perf.begin_turn("m-model", "acp", time.time(), time.monotonic())
        with handle.span("dispatch", provider="x"):
            pass
        handle.event("retry", reason="x")
        assert handle.model is None
        with handle.span("connect", cold=False, model="claude-opus"):
            pass
        assert handle.model == "claude-opus"
        handle.close("ok")

    def test_close_respects_status_argument(self):
        perf.configure({"enabled": True}, None)
        _close_simple_turn(message_id="m-cancel", status="cancelled")
        snapshot = perf.report_snapshot()
        doc = next(t for t in snapshot["turns"] if t["message_id"] == "m-cancel")
        assert doc["status"] == "cancelled"

    def test_close_is_idempotent(self):
        perf.configure({"enabled": True}, None)
        handle = perf.begin_turn("m-idempotent", "claude", time.time(), time.monotonic())
        with handle.span("ingress"):
            pass
        handle.close("ok")
        handle.close("cancelled")  # second call must be a no-op, not overwrite status
        handle.event("late", reason="after-close")  # must also be a no-op

        snapshot = perf.report_snapshot()
        docs = [t for t in snapshot["turns"] if t["message_id"] == "m-idempotent"]
        assert len(docs) == 1
        assert docs[0]["status"] == "ok"


class TestCurrentSpanAttr:
    def test_set_current_span_attr_sets_attr_on_innermost_open_span(self):
        perf.configure({"enabled": True, "attr_detail": "full"}, None)
        handle = perf.begin_turn("m-cur", "claude", time.time(), time.monotonic())
        with handle.span("tool:read_file", tool="read_file"):
            perf.set_current_span_attr("server", "filesystem")
        doc = handle.close("ok")
        assert doc["spans"][0]["attrs"]["server"] == "filesystem"

    def test_set_current_span_attr_is_noop_when_disabled(self):
        perf.configure({"enabled": False}, None)
        # No recorder active at all: must not raise even with no open span.
        perf.set_current_span_attr("server", "filesystem")

    def test_set_current_span_attr_is_noop_with_no_open_span(self):
        perf.configure({"enabled": True}, None)
        # Enabled, but nothing has opened a span in this thread's context.
        perf.set_current_span_attr("server", "filesystem")


class TestConcurrentTurns:
    def test_two_threads_do_not_cross_contaminate(self):
        perf.configure({"enabled": True}, None)
        errors = []

        def worker(msg_id, tool_name):
            try:
                handle = perf.begin_turn(msg_id, "claude", time.time(), time.monotonic())
                for _ in range(5):
                    with handle.span(f"tool:{tool_name}", tool=tool_name, ok=True, builtin=True):
                        time.sleep(0.001)
                handle.close("ok")
            except Exception as exc:  # pragma: no cover - failure surfaced via errors list
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("m-thread-1", "read_file"))
        t2 = threading.Thread(target=worker, args=("m-thread-2", "write_file"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors
        snapshot = perf.report_snapshot()
        docs = {t["message_id"]: t for t in snapshot["turns"]}
        assert "m-thread-1" in docs
        assert "m-thread-2" in docs
        assert len(docs["m-thread-1"]["spans"]) == 5
        assert len(docs["m-thread-2"]["spans"]) == 5
        for span in docs["m-thread-1"]["spans"]:
            assert span["name"] == "tool:read_file"
        for span in docs["m-thread-2"]["spans"]:
            assert span["name"] == "tool:write_file"


class TestSpanCap:
    def test_spans_beyond_cap_are_dropped_and_counted(self, monkeypatch):
        monkeypatch.setattr(perf, "_MAX_SPANS_PER_TURN", 3)
        perf.configure({"enabled": True}, None)
        handle = perf.begin_turn("m-cap", "claude", time.time(), time.monotonic())
        for _ in range(5):
            with handle.span("ingress"):
                pass
        handle.close("ok")

        snapshot = perf.report_snapshot()
        doc = next(t for t in snapshot["turns"] if t["message_id"] == "m-cap")
        assert len(doc["spans"]) == 3
        assert doc["dropped_spans"] == 2

    def test_events_beyond_cap_are_dropped_and_counted(self, monkeypatch):
        monkeypatch.setattr(perf, "_MAX_SPANS_PER_TURN", 2)
        perf.configure({"enabled": True}, None)
        handle = perf.begin_turn("m-cap-events", "claude", time.time(), time.monotonic())
        for _ in range(4):
            handle.event("retry", reason="x")
        handle.close("ok")

        snapshot = perf.report_snapshot()
        doc = next(t for t in snapshot["turns"] if t["message_id"] == "m-cap-events")
        assert len(doc["events"]) == 2
        assert doc["dropped_spans"] == 2


class TestAllowlistAndRedaction:
    def test_unknown_attrs_are_dropped_and_counted(self):
        perf.configure({"enabled": True, "attr_detail": "full"}, None)
        handle = perf.begin_turn("m-allow", "claude", time.time(), time.monotonic())
        with handle.span("context_prep", rule_count=3, secret_token="do-not-keep") as span:
            span.set_attr("another_secret", "nope")
        handle.close("ok")

        snapshot = perf.report_snapshot()
        doc = next(t for t in snapshot["turns"] if t["message_id"] == "m-allow")
        span_doc = doc["spans"][0]
        assert span_doc["attrs"] == {"rule_count": 3}
        assert doc["dropped_attrs"] == 2

    def test_basename_attrs_are_redacted_keeping_extension(self):
        perf.configure({"enabled": True, "attr_detail": "redacted"}, None)
        handle = perf.begin_turn("m-file", "claude", time.time(), time.monotonic())
        with handle.span("context_prep", file="notebook.ipynb"):
            pass
        handle.close("ok")

        snapshot = perf.report_snapshot()
        doc = next(t for t in snapshot["turns"] if t["message_id"] == "m-file")
        redacted = doc["spans"][0]["attrs"]["file"]
        assert redacted != "notebook.ipynb"
        assert redacted.endswith(".ipynb")
        assert redacted == f"{perf._hash8('notebook')}.ipynb"

    def test_name_attrs_are_redacted_to_full_hash(self):
        perf.configure({"enabled": True, "attr_detail": "redacted"}, None)
        handle = perf.begin_turn("m-tool", "claude", time.time(), time.monotonic())
        with handle.span("tool:read_file", tool="read_file", server="filesystem"):
            pass
        handle.close("ok")

        snapshot = perf.report_snapshot()
        doc = next(t for t in snapshot["turns"] if t["message_id"] == "m-tool")
        attrs = doc["spans"][0]["attrs"]
        assert attrs["tool"] == perf._hash8("read_file")
        assert attrs["server"] == perf._hash8("filesystem")
        assert attrs["tool"] != "read_file"

    def test_full_detail_mode_passes_values_through_unredacted(self):
        perf.configure({"enabled": True, "attr_detail": "full"}, None)
        handle = perf.begin_turn("m-full", "claude", time.time(), time.monotonic())
        with handle.span("tool:read_file", tool="read_file", server="filesystem"):
            pass
        handle.close("ok")

        snapshot = perf.report_snapshot()
        doc = next(t for t in snapshot["turns"] if t["message_id"] == "m-full")
        attrs = doc["spans"][0]["attrs"]
        assert attrs["tool"] == "read_file"
        assert attrs["server"] == "filesystem"
        # The span name itself must stay plain under full detail, matching
        # the attrs on the same span (see TestToolNameRedaction for the
        # redacted-detail counterpart).
        assert doc["spans"][0]["name"] == "tool:read_file"


class TestRingBufferAndSnapshot:
    def test_ring_buffer_evicts_oldest_turns(self):
        perf.configure({"enabled": True, "ring_buffer_turns": 3}, None)
        for i in range(5):
            _close_simple_turn(message_id=f"m-ring-{i}")

        snapshot = perf.report_snapshot()
        assert len(snapshot["turns"]) == 3
        kept_ids = [t["message_id"] for t in snapshot["turns"]]
        assert kept_ids == ["m-ring-2", "m-ring-3", "m-ring-4"]

    def test_snapshot_under_concurrent_appends_does_not_error(self):
        perf.configure({"enabled": True, "ring_buffer_turns": 20}, None)
        errors = []
        stop = threading.Event()

        def closer():
            i = 0
            try:
                while not stop.is_set():
                    _close_simple_turn(message_id=f"m-concurrent-{i}")
                    i += 1
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def snapshotter():
            try:
                for _ in range(50):
                    snap = perf.report_snapshot()
                    assert isinstance(snap["turns"], list)
                    assert "aggregates" in snap
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        writer = threading.Thread(target=closer)
        reader = threading.Thread(target=snapshotter)
        writer.start()
        reader.start()
        reader.join()
        stop.set()
        writer.join()

        assert not errors

    def test_aggregates_report_slowest_tools_and_totals(self):
        # builtin=True keeps these NBI-builtin tool names plain even under
        # the default redacted config (see test_builtin_tool_span_names_stay_plain),
        # so this test can assert on stable names while still exercising the
        # real aggregation path; it is not exercising redaction itself.
        perf.configure({"enabled": True}, None)
        handle = perf.begin_turn("m-agg", "claude", time.time(), time.monotonic())
        with handle.span("tool:slow_tool", tool="slow_tool", builtin=True):
            time.sleep(0.01)
        with handle.span("tool:fast_tool", tool="fast_tool", builtin=True):
            pass
        handle.close("ok")

        snapshot = perf.report_snapshot()
        aggregates = snapshot["aggregates"]
        assert aggregates["totals"]["turn_count"] == 1
        slowest_names = [t["name"] for t in aggregates["slowest_tools"]]
        assert "tool:slow_tool" in slowest_names
        assert "span_p50_ms" in aggregates
        assert "span_p95_ms" in aggregates


class TestJsonlSink:
    def test_writer_batches_enqueued_docs_to_disk(self, tmp_path):
        perf.configure(
            {"enabled": True, "log_to_file": True, "log_dir": str(tmp_path)}, None
        )
        for i in range(3):
            _close_simple_turn(message_id=f"m-sink-{i}")

        perf_dir = tmp_path / "perf"
        deadline = time.monotonic() + 2.0
        lines = []
        while time.monotonic() < deadline:
            files = list(perf_dir.glob("perf-*.jsonl")) if perf_dir.exists() else []
            if files:
                lines = files[0].read_text(encoding="utf-8").splitlines()
                if len(lines) >= 4:  # meta line + 3 turn docs
                    break
            time.sleep(0.05)

        assert len(lines) >= 4
        meta = json.loads(lines[0])
        assert meta["meta"]["schema_version"] == 1
        message_ids = {json.loads(line)["message_id"] for line in lines[1:]}
        assert message_ids == {"m-sink-0", "m-sink-1", "m-sink-2"}

    def test_sink_self_disables_after_repeated_write_failures(self, monkeypatch):
        sink = perf._JsonlSink("/nonexistent-nbi-perf-dir-xyz")
        monkeypatch.setattr(sink, "_resolve_path", lambda: "/nonexistent-nbi-perf-dir-xyz/perf.jsonl")

        for _ in range(perf._SINK_MAX_CONSECUTIVE_FAILURES - 1):
            sink._write_batch([{"message_id": "x"}])
            assert sink._disabled is False

        sink._write_batch([{"message_id": "x"}])
        assert sink._disabled is True

    def test_get_or_create_sink_refuses_once_file_logging_is_off(self, tmp_path):
        # TurnHandle.close() reads _log_to_file outside _sink_lock, so a
        # configure() that turns file logging off can land between that read
        # and the _get_or_create_sink() call. The sink factory has to
        # re-check under the lock, or that close resurrects a writer thread
        # the user just disabled.
        perf.configure(
            {"enabled": True, "log_to_file": True, "log_dir": str(tmp_path)}, None
        )
        assert perf._sink is not None

        perf.configure(
            {"enabled": True, "log_to_file": False, "log_dir": str(tmp_path)}, None
        )
        assert perf._sink is None

        # Simulate the racing close: it already passed its _log_to_file check.
        assert perf._get_or_create_sink() is None
        assert perf._sink is None

    def test_configure_recreates_sink_after_it_self_disabled(self, tmp_path):
        # A settings save (e.g. after the operator fixes the filesystem)
        # must retry the sink instead of leaving a self-disabled instance
        # in place forever when log_dir hasn't changed.
        perf.configure(
            {"enabled": True, "log_to_file": True, "log_dir": str(tmp_path)}, None
        )
        stale_sink = perf._sink
        assert stale_sink is not None
        stale_sink._disabled = True
        stale_sink._consecutive_failures = perf._SINK_MAX_CONSECUTIVE_FAILURES

        perf.configure(
            {"enabled": True, "log_to_file": True, "log_dir": str(tmp_path)}, None
        )

        assert perf._sink is not None
        assert perf._sink is not stale_sink
        assert perf._sink._disabled is False

        _close_simple_turn(message_id="m-sink-recover")

        perf_dir = tmp_path / "perf"
        deadline = time.monotonic() + 2.0
        lines = []
        while time.monotonic() < deadline:
            files = list(perf_dir.glob("perf-*.jsonl")) if perf_dir.exists() else []
            if files:
                lines = files[0].read_text(encoding="utf-8").splitlines()
                if len(lines) >= 2:  # meta line + the recovered turn doc
                    break
            time.sleep(0.05)

        assert len(lines) >= 2
        message_ids = {json.loads(line)["message_id"] for line in lines[1:]}
        assert "m-sink-recover" in message_ids

    def test_enqueue_is_a_noop_once_disabled(self, monkeypatch):
        sink = perf._JsonlSink("/nonexistent-nbi-perf-dir-xyz")
        sink._disabled = True
        sink.enqueue({"message_id": "x"})
        assert sink._queue.empty()


class TestConfigure:
    def test_runtime_flip_enables_and_disables(self):
        perf.configure({"enabled": False}, None)
        assert perf.enabled() is False
        perf.configure({"enabled": True}, None)
        assert perf.enabled() is True
        perf.configure({"enabled": False}, None)
        assert perf.enabled() is False
        assert perf.begin_turn("m1", "claude", time.time(), time.monotonic()) is None

    def test_force_on_enables_and_locks_attr_detail_to_redacted(self):
        perf.configure({"enabled": False, "attr_detail": "full"}, POLICY_FORCE_ON)
        assert perf.enabled() is True
        assert perf._attr_detail == "redacted"

    def test_force_off_disables_regardless_of_user_setting(self):
        perf.configure({"enabled": True}, POLICY_FORCE_OFF)
        assert perf.enabled() is False

    def test_user_choice_policy_defers_to_settings(self):
        perf.configure({"enabled": True, "attr_detail": "full"}, POLICY_USER_CHOICE)
        assert perf.enabled() is True
        assert perf._attr_detail == "full"

    def test_invalid_attr_detail_falls_back_to_redacted(self):
        perf.configure({"enabled": True, "attr_detail": "verbose"}, None)
        assert perf._attr_detail == "redacted"

    def test_log_dir_defaults_when_not_provided(self):
        perf.configure({"enabled": True, "log_to_file": True, "log_dir": ""}, None)
        assert perf._log_dir
        assert os.path.isabs(perf._log_dir)


def _bare_nbi_config(user_config=None):
    # Mirrors the pattern in tests/conftest.py's config fixture: NBIConfig's
    # real __init__ touches the filesystem (~/.jupyter/nbi), so tests that
    # only exercise the perf_diagnostics property build a bare instance and
    # set just the attributes that property reads.
    from unittest.mock import patch

    from notebook_intelligence.config import NBIConfig

    with patch.object(NBIConfig, "__init__", return_value=None):
        config = NBIConfig()
    config.user_config = user_config or {}
    config.env_config = {}
    config._feature_policies = {}
    config._string_overrides = {}
    return config


class TestConfigWiring:
    def test_nbi_config_perf_diagnostics_applies_policy_and_overrides(self):
        nbi_config = _bare_nbi_config()
        nbi_config.set_feature_policies(
            {"perf_diagnostics": POLICY_FORCE_ON}, {"perf_log_dir": "/forced/dir"}
        )
        resolved = nbi_config.perf_diagnostics
        assert resolved["enabled"] is True
        assert resolved["attr_detail"] == "redacted"
        assert resolved["log_dir"] == "/forced/dir"

    def test_nbi_config_perf_diagnostics_bool_value_lock(self):
        nbi_config = _bare_nbi_config()
        nbi_config.set_feature_policies({}, {"perf_diagnostics_enabled": "true"})
        assert nbi_config.perf_diagnostics["enabled"] is True

    def test_nbi_config_perf_diagnostics_defaults_when_unset(self):
        nbi_config = _bare_nbi_config()
        nbi_config.set_feature_policies({}, {})
        resolved = nbi_config.perf_diagnostics
        assert resolved["enabled"] is False
        assert resolved["attr_detail"] == "redacted"
        assert resolved["ring_buffer_turns"] == 50

class TestToolNameRedaction:
    def test_external_tool_span_names_are_hashed_in_redacted_mode(self):
        perf.configure({"enabled": True}, None)
        handle = perf.begin_turn("m-red", "copilot", time.time(), time.monotonic())
        with handle.span("tool:mcp__corp__lookup", tool="mcp__corp__lookup"):
            pass
        doc = handle.close("ok")
        name = doc["spans"][0]["name"]
        assert name.startswith("tool:")
        assert "corp" not in name and "lookup" not in name

    def test_builtin_tool_span_names_stay_plain(self):
        perf.configure({"enabled": True}, None)
        handle = perf.begin_turn("m-red2", "claude", time.time(), time.monotonic())
        with handle.span("tool:add-code-cell", tool="add-code-cell", builtin=True):
            pass
        doc = handle.close("ok")
        assert doc["spans"][0]["name"] == "tool:add-code-cell"
        assert "builtin" not in doc["spans"][0]["attrs"]

    def test_redacted_hashes_span_name_suffix_full_preserves_it(self):
        # Same non-builtin external tool span name under both attr_detail
        # modes: redacted must hash the "tool:" suffix (matching the tool/
        # server attr hashing on the same span), full must leave it as-is.
        perf.configure({"enabled": True, "attr_detail": "redacted"}, None)
        handle = perf.begin_turn("m-red3", "copilot", time.time(), time.monotonic())
        with handle.span("tool:mcp__acme__search", tool="mcp__acme__search"):
            pass
        redacted_name = handle.close("ok")["spans"][0]["name"]
        assert redacted_name == "tool:" + perf._hash8("mcp__acme__search")

        perf.configure({"enabled": True, "attr_detail": "full"}, None)
        handle = perf.begin_turn("m-red4", "copilot", time.time(), time.monotonic())
        with handle.span("tool:mcp__acme__search", tool="mcp__acme__search"):
            pass
        full_name = handle.close("ok")["spans"][0]["name"]
        assert full_name == "tool:mcp__acme__search"
