# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Bounded-check harness shared by the diagnostics surfaces.

A "check" is a small function that answers one question about the machine or
the configuration and either returns a JSON-serializable detail dict or
raises. The harness runs each one on a worker thread with a wall-clock budget
so a single hung syscall (an unresponsive NFS mount, a black-holed socket, a
subprocess that never execs) degrades to one ``timed_out`` row instead of
hanging the request that asked.

Two rules the callers depend on:

- A check that exceeds its budget is **abandoned**, never joined. Cancelling a
  future whose thread is already running is a no-op in ``concurrent.futures``,
  and joining is exactly what the budget exists to avoid, so "abandon" is the
  honest description. The cost is a leaked thread per genuinely hung syscall.
- An exception's *message* is never recorded, only its class name. Messages
  routinely carry paths, hostnames, and credentials that a downstream scrub
  pass would not know to look for.

Extracted from ``perf_probe`` so the readiness preflight can reuse it rather
than growing a second, subtly different copy. ``perf_probe`` keeps private
aliases for these names, so its own call sites and tests are unchanged.
"""

from __future__ import annotations

import concurrent.futures
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

# Budget for a check that talks to the local machine (a stat, a subprocess).
DEFAULT_TIMEOUT_S = 2.0

# Margin between a check body's own internal early-stop bound and the outer
# future timeout that awaits it. Without this slack, a check that legitimately
# runs right up to its internal bound gets its future abandoned at the same
# instant, discarding whatever it had already collected, and any band above
# that bound becomes unreachable.
SLOW_CHECK_MARGIN_S = 1.0


class Skipped(Exception):
    """Raised by a check body to report ``status="skipped"``.

    Skipped is not a failure: it means the check does not apply here (no such
    directory, no such platform, the feature is off). Callers render it
    differently from an error, so raising this rather than returning a
    sentinel keeps the distinction in one place.
    """


class CheckTimeout(Exception):
    """Raised by a check body that owns a self-cleaning timeout.

    Some check bodies (a ``subprocess.run`` with its own timeout, a socket
    with ``settimeout``) detect the timeout themselves and clean up. Raising
    this makes them report ``timed_out`` like an abandoned future rather than
    ``error``, so the two paths look the same to whoever reads the result.
    """


def submit_check(
    pool: concurrent.futures.ThreadPoolExecutor,
    check_id: str,
    group: str,
    timeout_s: float,
    fn: Callable[[], dict],
) -> tuple:
    """Submit ``fn`` without waiting. Pairs with :func:`collect_check`.

    Split from the collect half so a caller can submit every independent
    check up front and only then start waiting, instead of paying each
    check's timeout one at a time.
    """
    return (check_id, group, timeout_s, pool.submit(fn))


def collect_check(
    check_id: str,
    group: str,
    timeout_s: float,
    future: concurrent.futures.Future,
) -> dict:
    """Block on an already-submitted future with ``timeout_s``.

    Always returns a row; never raises. See the module docstring for why a
    timed-out future is abandoned and why exception messages are dropped.
    """
    try:
        detail = future.result(timeout=timeout_s)
        return {"id": check_id, "group": group, "status": "ok", "detail": detail}
    except (concurrent.futures.TimeoutError, CheckTimeout):
        return {
            "id": check_id,
            "group": group,
            "status": "timed_out",
            "detail": {"timeout_s": timeout_s},
        }
    except Skipped as e:
        return {"id": check_id, "group": group, "status": "skipped", "detail": {"reason": str(e)}}
    except Exception as e:
        return {
            "id": check_id,
            "group": group,
            "status": "error",
            "detail": {"exception_class": type(e).__name__},
        }


def run_check(
    pool: concurrent.futures.ThreadPoolExecutor,
    check_id: str,
    group: str,
    timeout_s: float,
    fn: Callable[[], dict],
) -> dict:
    """Submit ``fn`` and immediately block on its result.

    Use this when a group's checks contend for the same resource and running
    them concurrently would make them measure each other. Use the
    submit/collect pair when they are independent.
    """
    check_id, group, timeout_s, future = submit_check(pool, check_id, group, timeout_s, fn)
    return collect_check(check_id, group, timeout_s, future)


def skipped_entry(check_id: str, group: str, reason: str) -> dict:
    """A ``skipped`` row for a check that was never submitted at all."""
    return {"id": check_id, "group": group, "status": "skipped", "detail": {"reason": reason}}


def run_versioned(cmd: list) -> dict:
    """Run ``cmd`` and return its exit status and version banner.

    Shared because "does this binary resolve *and actually start*" is the
    same question for the perf probe and the readiness preflight, and a
    binary that resolves but fails to exec is the usual cause of a turn
    hanging with no error.
    """
    t0 = None
    try:
        import time

        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise CheckTimeout(cmd[0])
    wall_ms = (time.perf_counter() - t0) * 1000
    return {
        "wall_ms": round(wall_ms, 3),
        "returncode": proc.returncode,
        # Some CLIs print their banner to stderr.
        "version": (proc.stdout.strip() or proc.stderr.strip()),
    }


def _current_username() -> str:
    for var in ("USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    try:
        import getpass

        return getpass.getuser()
    except Exception:
        return ""


def scrub(doc: Any) -> Any:
    """Replace home paths and the login name throughout a document.

    Applied to anything a user is invited to paste into a support ticket. A
    launch command, a CLI banner, or an npm cache path routinely carries an
    absolute home directory, and the home directory carries the login name.
    """
    home = str(Path.home())
    real_home = os.path.realpath(home)
    username = _current_username()
    username_re = re.compile(r"\b" + re.escape(username) + r"\b") if username else None

    def scrub_str(value: str) -> str:
        if home and home in value:
            value = value.replace(home, "~")
        if real_home != home and real_home in value:
            # Symlinked or automounted homes: subprocess output often prints
            # the resolved path rather than $HOME.
            value = value.replace(real_home, "~")
        if username_re is not None:
            # Word-boundary so an id that merely contains the login name as a
            # substring is not mangled.
            value = username_re.sub("~user", value)
        return value

    def walk(obj: Any) -> Any:
        if isinstance(obj, str):
            return scrub_str(obj)
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items() if k != "hostname"}
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    return walk(doc)
