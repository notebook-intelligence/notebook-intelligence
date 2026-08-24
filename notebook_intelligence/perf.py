# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Opt-in, off-by-default performance diagnostics recorder.

This module is the Phase A foundation described in
``.plans/perf-diagnostics-plan.md`` / ``.plans/perf-contracts.md``: a turn
recorder that Phase B1 (extension.py/claude.py/acp_agent.py/api.py
instrumentation) and Phase B2 (the environment probe) build on. Its public
API is frozen by ``perf-contracts.md`` and must not change shape here.

Threading model (see the plan's "Verified threading model" section): a turn
starts on the tornado ioloop thread, is driven from a per-request thread
running ``asyncio.run``, and streams through an SDK client thread fed by a
queue. Those are three distinct OS threads with no shared ``contextvars``
context, so the turn registry below is a plain dict keyed by ``message_id``
and guarded by a dedicated lock -- never a ``ContextVar``. A ``ContextVar``
is used only for intra-thread span parent/child nesting, which is safe
because Python's default per-thread ``Context`` isolation already keeps each
thread's nesting stack separate.

Every public entry point checks the module-level ``_enabled`` flag first and
returns immediately (no allocation, no locking) when diagnostics are off, so
the disabled fast path costs one attribute read.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Optional

from notebook_intelligence.feature_flags import POLICY_FORCE_OFF, POLICY_FORCE_ON

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Fixed span-name vocabulary (perf-contracts.md) and per-span attr allowlist.
#
# Unknown attrs are dropped and counted rather than silently recorded, so
# the privacy guarantee (only these keys ever reach the ring buffer / JSONL
# sink) can't erode by a later PR just starting to pass a new kwarg. Adding
# a legitimately useful attr means editing this dict, on purpose, here.
# --------------------------------------------------------------------------
SPAN_NAMES = (
    "ingress",
    "context_prep",
    "dispatch",
    "connect",
    "first_token",
    "stream",
    "waiting_on_user",
    "ui_command",
    "finish",
)

# Attrs allowed on every span regardless of name.
_COMMON_SPAN_ATTRS = {"model", "status", "count", "bytes"}

_SPAN_ATTR_ALLOWLIST: dict[str, set] = {
    "ingress": set(),
    "context_prep": {"rule_count", "skill_count", "file_count", "file"},
    "dispatch": {"provider"},
    "connect": {"cold", "gateway_host"},
    "first_token": set(),
    "stream": {"chunk_count"},
    "waiting_on_user": set(),
    "tool": {"tool", "server", "ok"},
    "ui_command": {"command"},
    "finish": {"reason"},
}

# Event names aren't a fixed vocabulary (stalls/retries/etc.), so they share
# one allowlist bucket instead of one entry per event name.
_EVENT_ATTR_ALLOWLIST = _COMMON_SPAN_ATTRS | {"reason", "gap_ms", "attempt", "after"}

# Attr keys whose string value is a filename: keep the extension, hash the
# stem, so "notebook.ipynb" becomes "a1b2c3d4.ipynb" -- useful for grouping,
# not for identifying the notebook.
_BASENAME_ATTR_KEYS = {"file", "filename", "path"}

# Attr keys whose string value is a model/tool/server/host identifier:
# hashed in full, no plaintext survives redaction.
_NAME_ATTR_KEYS = {"model", "tool", "server", "gateway_host", "provider"}

_ATTR_DETAIL_VALUES = ("redacted", "full")

_MAX_SPANS_PER_TURN = 500

_RETENTION_MAX_BYTES = 50 * 1024 * 1024
_RETENTION_MAX_AGE_DAYS = 14
_SINK_MAX_CONSECUTIVE_FAILURES = 5

_SENTINEL = object()


def _allowlist_bucket(span_name: str) -> str:
    return "tool" if span_name.startswith("tool:") else span_name


def _hash8(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _redact_value(key: str, value: Any) -> Any:
    if _attr_detail == "full":
        return value
    if isinstance(value, str) and key in _BASENAME_ATTR_KEYS:
        stem, ext = os.path.splitext(value)
        return f"{_hash8(stem)}{ext}"
    if isinstance(value, str) and key in _NAME_ATTR_KEYS:
        return _hash8(value)
    return value


def _filter_attrs(allowlist_key: str, attrs: dict) -> tuple[dict, int]:
    """Drop unknown attrs and redact the rest. Returns (kept, dropped_count)."""
    allowed = _SPAN_ATTR_ALLOWLIST.get(allowlist_key, set()) | _COMMON_SPAN_ATTRS
    kept: dict = {}
    dropped = 0
    for k, v in attrs.items():
        if k not in allowed:
            dropped += 1
            continue
        kept[k] = _redact_value(k, v)
    return kept, dropped


def _filter_event_attrs(attrs: dict) -> tuple[dict, int]:
    kept: dict = {}
    dropped = 0
    for k, v in attrs.items():
        if k not in _EVENT_ATTR_ALLOWLIST:
            dropped += 1
            continue
        kept[k] = _redact_value(k, v)
    return kept, dropped


def _percentile(values: list, pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] + (s[hi] - s[lo]) * frac


# --------------------------------------------------------------------------
# Module state. All of it is process-wide by design: one recorder per
# kernel/server process, configured once at startup and again on every
# ConfigHandler.post via configure().
# --------------------------------------------------------------------------
_enabled = False
_attr_detail = "redacted"
_log_to_file = False
_log_dir = ""
_ring_maxlen = 50

_registry_lock = threading.Lock()
_turns: dict[str, "TurnHandle"] = {}

_ring_lock = threading.Lock()
_ring: deque = deque(maxlen=_ring_maxlen)

_current_span: ContextVar[Optional["_SpanRecord"]] = ContextVar(
    "nbi_perf_current_span", default=None
)

_sink_lock = threading.Lock()
_sink: Optional["_JsonlSink"] = None


def enabled() -> bool:
    return _enabled


class _SpanRecord:
    """Mutable in-flight span. Only ``TurnHandle.span()`` constructs these."""

    __slots__ = ("name", "span_id", "parent_id", "t0", "status", "attrs")

    def __init__(self, name: str, span_id: int, parent_id: Optional[int]):
        self.name = name
        self.span_id = span_id
        self.parent_id = parent_id
        self.t0 = time.monotonic()
        self.status = "ok"
        self.attrs: dict = {}

    def set_attr(self, key: str, value: Any) -> None:
        self.attrs[key] = value


class _NullSpan:
    """Yielded by ``span()`` when the turn/recorder is inactive.

    Lets instrumentation call ``span.set_attr(...)`` unconditionally inside a
    ``with`` block without a disabled-check of its own.
    """

    __slots__ = ()

    def set_attr(self, key: str, value: Any) -> None:
        pass


class TurnHandle:
    """One conversation turn's recording session.

    Created by ``begin_turn`` only when diagnostics are enabled; every
    method here is safe to call from any of the turn's three threads, but a
    single ``TurnHandle`` is not meant to be mutated concurrently by more
    than one span at a time per thread (span nesting relies on the calling
    thread's own stack order).
    """

    def __init__(self, message_id: str, mode: str, ingress_wall: float, ingress_mono: float):
        self.turn_id = uuid.uuid4().hex
        self.message_id = message_id
        self.mode = mode
        self.model: Optional[str] = None
        self.t_wall = ingress_wall
        self._t0_mono = ingress_mono

        self._lock = threading.Lock()
        self._closed = False
        self._status: Optional[str] = None
        self._span_id_seq = 0
        self._spans: list = []
        self._events: list = []
        self.dropped_spans = 0
        self.dropped_attrs = 0
        self._user_wait_seconds = 0.0
        self._tokens = {"input": None, "output": None}
        self._sdk = {"duration_ms": None, "duration_api_ms": None, "num_turns": None}

    def _next_span_id(self) -> int:
        with self._lock:
            self._span_id_seq += 1
            return self._span_id_seq

    def _maybe_capture_model(self, attrs: dict) -> None:
        model = attrs.get("model")
        if isinstance(model, str) and model and self.model is None:
            self.model = model

    @contextmanager
    def span(self, name: str, **attrs) -> Iterator[Any]:
        if self._closed:
            yield _NullSpan()
            return

        self._maybe_capture_model(attrs)
        parent = _current_span.get()
        rec = _SpanRecord(name, self._next_span_id(), parent.span_id if parent else None)
        rec.attrs.update(attrs)
        token = _current_span.set(rec)
        try:
            yield rec
        except BaseException:
            rec.status = "error"
            raise
        finally:
            try:
                _current_span.reset(token)
            except ValueError:
                # The span was entered and exited in different contextvars
                # Contexts (the acp library runs each session notification in
                # its own task). Nesting bookkeeping is meaningless across
                # contexts, but the span itself must still be recorded.
                pass
            self._finish_span(rec)

    def _finish_span(self, rec: _SpanRecord) -> None:
        dur_ms = (time.monotonic() - rec.t0) * 1000.0
        builtin = bool(rec.attrs.pop("builtin", False))
        name = rec.name
        if (
            not builtin
            and _attr_detail == "redacted"
            and name.startswith("tool:")
        ):
            # Span names are served verbatim everywhere (report, aggregates,
            # JSONL), so external tool names must get the same hashing the
            # tool attr does or redacted mode leaks them through the name.
            name = "tool:" + _hash8(name[5:])
        rec.name = name
        with self._lock:
            if self._closed:
                return
            if len(self._spans) >= _MAX_SPANS_PER_TURN:
                self.dropped_spans += 1
                return
            kept_attrs, dropped = _filter_attrs(_allowlist_bucket(rec.name), rec.attrs)
            self.dropped_attrs += dropped
            self._spans.append(
                {
                    "name": rec.name,
                    "dur_ms": round(dur_ms, 3),
                    "status": rec.status,
                    "attrs": kept_attrs,
                }
            )

    def event(self, name: str, **attrs) -> None:
        if self._closed:
            return
        self._maybe_capture_model(attrs)
        kept_attrs, dropped = _filter_event_attrs(attrs)
        with self._lock:
            if self._closed:
                return
            self.dropped_attrs += dropped
            if len(self._events) >= _MAX_SPANS_PER_TURN:
                self.dropped_spans += 1
                return
            self._events.append({"name": name, "t_ms": (time.monotonic() - self._t0_mono) * 1000.0, "attrs": kept_attrs})

    def set_result(
        self,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        duration_ms: Optional[float] = None,
        duration_api_ms: Optional[float] = None,
        num_turns: Optional[int] = None,
    ) -> None:
        if self._closed:
            return
        with self._lock:
            if self._closed:
                return
            if input_tokens is not None:
                self._tokens["input"] = input_tokens
            if output_tokens is not None:
                self._tokens["output"] = output_tokens
            if duration_ms is not None:
                self._sdk["duration_ms"] = duration_ms
            if duration_api_ms is not None:
                self._sdk["duration_api_ms"] = duration_api_ms
            if num_turns is not None:
                self._sdk["num_turns"] = num_turns

    def add_user_wait(self, seconds: float) -> None:
        if self._closed or not seconds:
            return
        with self._lock:
            if self._closed:
                return
            self._user_wait_seconds += max(0.0, seconds)

    def close(self, status: str) -> Optional[dict]:
        """Close the turn and return its finished document (None if already
        closed). The return value is additive to the frozen contract: existing
        callers ignore it; the INFO summary line uses it to log per-phase
        durations without re-locking."""
        with self._lock:
            if self._closed:
                return None
            self._closed = True
            self._status = status
            total_ms = (time.monotonic() - self._t0_mono) * 1000.0
            active_ms = max(0.0, total_ms - self._user_wait_seconds * 1000.0)
            doc = {
                "turn_id": self.turn_id,
                "message_id": self.message_id,
                "mode": self.mode,
                "model": self.model,
                "status": status,
                "t_wall": self.t_wall,
                "total_ms": round(total_ms, 3),
                "active_ms": round(active_ms, 3),
                "spans": list(self._spans),
                "events": list(self._events),
                "tokens": dict(self._tokens),
                "sdk": dict(self._sdk),
                "dropped_spans": self.dropped_spans,
                "dropped_attrs": self.dropped_attrs,
            }

        with _registry_lock:
            if _turns.get(self.message_id) is self:
                _turns.pop(self.message_id, None)

        with _ring_lock:
            _ring.append(doc)

        if _log_to_file:
            sink = _get_or_create_sink()
            if sink is not None:
                sink.enqueue(doc)
        return doc


def begin_turn(message_id: str, mode: str, ingress_wall: float, ingress_mono: float) -> Optional[TurnHandle]:
    if not _enabled:
        return None
    handle = TurnHandle(message_id, mode, ingress_wall, ingress_mono)
    with _registry_lock:
        _turns[message_id] = handle
    return handle


def current_span():
    """The innermost open span in this thread's context, or None.

    Lets a callee annotate the span its caller opened (mcp_manager adds the
    server name to api.py's tool span) without opening a duplicate.
    """
    if not _enabled:
        return None
    return _current_span.get()


def get_turn(message_id: str) -> Optional[TurnHandle]:
    if not _enabled:
        return None
    with _registry_lock:
        return _turns.get(message_id)


# --------------------------------------------------------------------------
# Ring buffer aggregation / report snapshot.
# --------------------------------------------------------------------------
def _compute_aggregates(turns: list) -> dict:
    span_durations: dict[str, list] = {}
    tool_durations: list = []
    dropped_spans_total = 0
    dropped_attrs_total = 0
    for t in turns:
        dropped_spans_total += t.get("dropped_spans", 0) or 0
        dropped_attrs_total += t.get("dropped_attrs", 0) or 0
        for s in t.get("spans", []):
            name = s.get("name")
            dur = s.get("dur_ms")
            if not name or dur is None:
                continue
            span_durations.setdefault(name, []).append(dur)
            if name.startswith("tool:"):
                tool_durations.append((name, dur))

    tool_durations.sort(key=lambda item: item[1], reverse=True)

    return {
        "span_p50_ms": {name: round(_percentile(vals, 50), 3) for name, vals in span_durations.items()},
        "span_p95_ms": {name: round(_percentile(vals, 95), 3) for name, vals in span_durations.items()},
        "slowest_tools": [{"name": n, "dur_ms": d} for n, d in tool_durations[:10]],
        "totals": {
            "turn_count": len(turns),
            "dropped_spans": dropped_spans_total,
            "dropped_attrs": dropped_attrs_total,
        },
    }


def report_snapshot() -> dict:
    """Snapshot the ring buffer under a short-lived lock, then aggregate.

    The lock only guards the O(1) copy-out; the (potentially non-trivial)
    aggregation math runs on the snapshot afterwards so writers (turn close)
    never block behind a report request.
    """
    with _ring_lock:
        turns_snapshot = list(_ring)
    return {
        "schema_version": 1,
        "turns": turns_snapshot,
        "aggregates": _compute_aggregates(turns_snapshot),
    }


# --------------------------------------------------------------------------
# JSONL sink: lazily-started daemon writer thread draining a queue.
# --------------------------------------------------------------------------
class _JsonlSink:
    def __init__(self, log_dir: str):
        self._log_dir = log_dir
        self._queue: "queue.Queue" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._start_lock = threading.Lock()
        self._consecutive_failures = 0
        self._disabled = False
        self._current_date: Optional[str] = None
        self._current_path: Optional[str] = None

    def start(self) -> None:
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._run, name="nbi-perf-writer", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._queue.put(_SENTINEL)

    def enqueue(self, doc: dict) -> None:
        if self._disabled:
            return
        self._queue.put(doc)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            batch = [item]
            while True:
                try:
                    nxt = self._queue.get_nowait()
                except queue.Empty:
                    break
                if nxt is _SENTINEL:
                    self._write_batch(batch)
                    return
                batch.append(nxt)
            self._write_batch(batch)

    def _resolve_path(self) -> str:
        perf_dir = os.path.join(self._log_dir, "perf")
        today = time.strftime("%Y%m%d")
        path = os.path.join(perf_dir, f"perf-{today}.jsonl")
        if today != self._current_date:
            os.makedirs(perf_dir, mode=0o700, exist_ok=True)
            self._apply_retention(perf_dir)
            is_new_file = not os.path.exists(path)
            if is_new_file:
                meta = {"meta": {"schema_version": 1, "log_fs_type": _detect_fs_type(perf_dir)}}
                with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600),
                "a",
                encoding="utf-8",
            ) as fh:
                    fh.write(json.dumps(meta) + "\n")
            self._current_date = today
            self._current_path = path
        return path

    def _apply_retention(self, perf_dir: str) -> None:
        try:
            files = glob.glob(os.path.join(perf_dir, "perf-*.jsonl"))
            now = time.time()
            keep = []
            for f in files:
                try:
                    age_days = (now - os.path.getmtime(f)) / 86400.0
                except OSError:
                    continue
                if age_days > _RETENTION_MAX_AGE_DAYS:
                    os.remove(f)
                else:
                    keep.append(f)
            keep.sort(key=lambda f: os.path.getmtime(f))
            total = sum(os.path.getsize(f) for f in keep)
            idx = 0
            while total > _RETENTION_MAX_BYTES and idx < len(keep):
                sz = os.path.getsize(keep[idx])
                os.remove(keep[idx])
                total -= sz
                idx += 1
        except OSError:
            pass

    def _write_batch(self, batch: list) -> None:
        try:
            path = self._resolve_path()
            with os.fdopen(
                os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600),
                "a",
                encoding="utf-8",
            ) as fh:
                for doc in batch:
                    fh.write(json.dumps(doc) + "\n")
            self._consecutive_failures = 0
        except OSError as exc:
            self._consecutive_failures += 1
            log.debug("perf JSONL sink write failed: %s", exc)
            if self._consecutive_failures >= _SINK_MAX_CONSECUTIVE_FAILURES:
                self._disabled = True
                log.warning(
                    "perf JSONL sink disabled after %d consecutive write failures",
                    self._consecutive_failures,
                )


def _detect_fs_type(path: str) -> Optional[str]:
    """Best-effort filesystem-type lookup for the perf log directory.

    Purely informational (goes in the JSONL meta line so a DLP reviewer can
    tell EFS/NFS from local disk at a glance); any failure just yields None.
    """
    try:
        target = os.path.realpath(path)
        if sys.platform.startswith("linux"):
            best_match = None
            best_len = -1
            with open("/proc/mounts", "r", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    mount_point, fs_type = parts[1], parts[2]
                    if target.startswith(mount_point) and len(mount_point) > best_len:
                        best_match, best_len = fs_type, len(mount_point)
            return best_match
        result = subprocess.run(
            ["mount"], capture_output=True, text=True, timeout=1, check=False
        )
        best_match = None
        best_len = -1
        for line in result.stdout.splitlines():
            if " on " not in line or "(" not in line:
                continue
            mount_point = line.split(" on ", 1)[1].split(" (", 1)[0]
            if target.startswith(mount_point) and len(mount_point) > best_len:
                fs_type = line.split("(", 1)[1].split(",", 1)[0].strip()
                best_match, best_len = fs_type, len(mount_point)
        return best_match
    except Exception:
        return None


def _get_or_create_sink() -> Optional[_JsonlSink]:
    global _sink
    with _sink_lock:
        if _sink is None:
            _sink = _JsonlSink(_log_dir)
        if _sink._disabled:
            return None
        _sink.start()
        return _sink


def _teardown_sink() -> None:
    global _sink
    with _sink_lock:
        if _sink is not None:
            _sink.stop()
            _sink = None


# --------------------------------------------------------------------------
# Runtime configuration, called from ConfigHandler.post and at startup.
# --------------------------------------------------------------------------
def configure(settings: Optional[dict], policy_forced: Optional[str]) -> None:
    """Apply resolved ``perf_diagnostics`` settings + admin policy.

    ``policy_forced`` is the already-resolved ``feature_policies["perf_diagnostics"]``
    value (one of the ``VALID_POLICIES`` strings, or ``None``/user-choice).
    Force-off always wins regardless of ``settings``; force-on enables and
    additionally locks ``attr_detail`` to "redacted" so a force-on admin
    can't be undermined by a stale "full" value in the user's settings.

    ``settings["log_dir"]`` should already be resolved by the caller (empty
    string falls back to ``nbi_config.nbi_user_dir`` upstream, per the plan's
    "<log_dir or nbi_user_dir>/perf/..." rule) -- this function only applies
    a defensive fallback below so a caller that forgets still gets a sane,
    per-user default instead of writing relative to the process cwd.
    """
    global _enabled, _attr_detail, _log_to_file, _log_dir, _ring_maxlen, _ring

    settings = dict(settings or {})

    if policy_forced == POLICY_FORCE_OFF:
        new_enabled = False
    elif policy_forced == POLICY_FORCE_ON:
        new_enabled = True
    else:
        new_enabled = bool(settings.get("enabled", False))

    new_attr_detail = settings.get("attr_detail", "redacted")
    if new_attr_detail not in _ATTR_DETAIL_VALUES:
        new_attr_detail = "redacted"
    if policy_forced == POLICY_FORCE_ON:
        new_attr_detail = "redacted"

    new_log_dir = str(settings.get("log_dir") or "") or os.path.join(
        os.path.expanduser("~"), ".jupyter", "nbi"
    )
    new_log_to_file = new_enabled and bool(settings.get("log_to_file", False))

    try:
        new_ring_maxlen = max(1, int(settings.get("ring_buffer_turns", 50)))
    except (TypeError, ValueError):
        new_ring_maxlen = 50

    with _ring_lock:
        if new_ring_maxlen != _ring_maxlen:
            _ring = deque(_ring, maxlen=new_ring_maxlen)
            _ring_maxlen = new_ring_maxlen

    _attr_detail = new_attr_detail
    _enabled = new_enabled

    if new_log_to_file:
        # log_dir may change between calls (e.g. NBI_PERF_LOG_DIR flips, or
        # the user edits it); a changed dir needs a fresh sink so the writer
        # thread picks up the new target instead of continuing to append to
        # the old one.
        if _sink is not None and _log_dir != new_log_dir:
            _teardown_sink()
        _log_dir = new_log_dir
        _log_to_file = True
        _get_or_create_sink()
    else:
        _log_dir = new_log_dir
        _log_to_file = False
        _teardown_sink()
