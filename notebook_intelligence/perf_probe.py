# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>
"""On-demand, opt-in environment probe for NBI performance diagnostics.

``run_probe`` runs a battery of lightweight, best-effort checks (filesystem
latency, CLI subprocess versions, runtime stats, and optionally network
reachability of the currently configured LLM endpoint) and returns a single
scrubbed JSON-serializable document.

Every individual check runs as a future in a small shared thread pool with
its own timeout. Filesystem calls in particular can hit an uninterruptible
syscall (a hung NFS ``stat``, for example) that Python has no way to cancel
from the outside; the only correct bound in that case is to stop waiting on
the future and let the thread leak, which is what "abandon" means below.
This function itself is blocking; callers on an event loop are expected to
run it via ``run_in_executor``.
"""

import concurrent.futures
import contextlib
import datetime
import getpass
import hashlib
import os
import re
import platform
try:
    import resource
except ImportError:  # Windows: only the RSS check needs it
    resource = None
import socket
import ssl
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Optional

from notebook_intelligence.util import resolve_claude_cli_path

DEFAULT_TIMEOUT_S = 2.0
NETWORK_TIMEOUT_S = 5.0
_POOL_SIZE = 4
_MAX_LATENCY_ITERATIONS = 20
_SUSTAINED_IO_SIZE_BYTES = 4 * 1024 * 1024
# Margin between a check body's own internal early-stop bound and the outer
# future timeout that awaits it. Without this slack, a check that legitimately
# runs right up to its internal bound gets its future abandoned at the same
# instant, discarding whatever samples it had already collected.
_SLOW_CHECK_MARGIN_S = 1.0

try:
    from notebook_intelligence._version import __version__ as _NBI_VERSION
except Exception:
    _NBI_VERSION = "0"


class _Skipped(Exception):
    """Raised by a check body to report status="skipped" (not an error)."""


class _CheckTimeout(Exception):
    """Raised by a check body that owns a self-cleaning timeout (e.g.
    subprocess, sockets) so it reports status="timed_out" like an abandoned
    future, instead of status="error"."""


# ---------------------------------------------------------------------------
# Pool submission
# ---------------------------------------------------------------------------
#
# There is no module-global pool: run_probe builds a fresh ThreadPoolExecutor
# for every call (see below) so one run's abandoned/hung checks can never
# occupy the workers a later run needs. _submit_check and _collect_check are
# split so a caller can submit every independent check up front and only
# then start waiting on results, instead of paying each check's timeout one
# at a time.


def _submit_check(
    pool: concurrent.futures.ThreadPoolExecutor,
    check_id: str,
    group: str,
    timeout_s: float,
    fn: Callable[[], dict],
) -> tuple:
    """Submit fn() to the pool without waiting for it. Pairs with
    _collect_check."""
    return (check_id, group, timeout_s, pool.submit(fn))


def _collect_check(check_id: str, group: str, timeout_s: float, future: concurrent.futures.Future) -> dict:
    """Block on an already-submitted future with timeout_s. On timeout the
    future is abandoned (never awaited again, never cancelled) rather than
    joined -- cancelling a future whose thread is already running is a no-op
    in concurrent.futures anyway, so "abandon" is the honest description."""
    try:
        detail = future.result(timeout=timeout_s)
        return {"id": check_id, "group": group, "status": "ok", "detail": detail}
    except (concurrent.futures.TimeoutError, _CheckTimeout):
        return {
            "id": check_id,
            "group": group,
            "status": "timed_out",
            "detail": {"timeout_s": timeout_s},
        }
    except _Skipped as e:
        return {"id": check_id, "group": group, "status": "skipped", "detail": {"reason": str(e)}}
    except Exception as e:
        # Exception message intentionally dropped: it can contain paths,
        # hostnames, or other detail the scrub pass wouldn't know to redact.
        return {
            "id": check_id,
            "group": group,
            "status": "error",
            "detail": {"exception_class": type(e).__name__},
        }


def _run_check(
    pool: concurrent.futures.ThreadPoolExecutor,
    check_id: str,
    group: str,
    timeout_s: float,
    fn: Callable[[], dict],
) -> dict:
    """Submit fn() to the pool and immediately block on its result with
    timeout_s. Kept for callers (including tests) that want the older
    synchronous submit-then-wait behavior against their own pool; run_probe
    itself uses _submit_check/_collect_check directly so independent checks
    run concurrently instead of each one blocking the next submission."""
    check_id, group, timeout_s, future = _submit_check(pool, check_id, group, timeout_s, fn)
    return _collect_check(check_id, group, timeout_s, future)


def _skipped_entry(check_id: str, group: str, reason: str) -> dict:
    return {"id": check_id, "group": group, "status": "skipped", "detail": {"reason": reason}}


# ---------------------------------------------------------------------------
# Filesystem group
# ---------------------------------------------------------------------------


def _summarize(samples: list) -> dict:
    if not samples:
        return {"min_ms": None, "median_ms": None, "max_ms": None, "first_iteration_ms": None}
    return {
        "min_ms": round(min(samples), 3),
        "median_ms": round(statistics.median(samples), 3),
        "max_ms": round(max(samples), 3),
        "first_iteration_ms": round(samples[0], 3),
    }


def _latency_loop(dir_path: Path, bound_s: float) -> dict:
    """stat / small-file read / write+fsync+unlink latency loops.

    Up to _MAX_LATENCY_ITERATIONS iterations, early-stopping once cumulative
    elapsed time exceeds bound_s. NFS attribute-cache state makes the very
    first iteration meaningfully different from steady state (cold vs warm),
    so it is reported separately via first_iteration_ms rather than folded
    into min/median/max.
    """
    payload = b"nbi-perf-probe-latency-check\n"
    read_target = dir_path / f".nbi-perf-probe-read-{os.getpid()}-{time.monotonic_ns()}"
    read_target.write_bytes(payload)
    try:
        stat_ms: list = []
        read_ms: list = []
        write_ms: list = []
        start = time.monotonic()
        n = 0
        for _ in range(_MAX_LATENCY_ITERATIONS):
            if time.monotonic() - start > bound_s:
                break

            t0 = time.perf_counter()
            os.stat(read_target)
            stat_ms.append((time.perf_counter() - t0) * 1000)

            t0 = time.perf_counter()
            read_target.read_bytes()
            read_ms.append((time.perf_counter() - t0) * 1000)

            write_target = dir_path / f".nbi-perf-probe-write-{os.getpid()}-{time.monotonic_ns()}"
            t0 = time.perf_counter()
            fd = os.open(write_target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.unlink(write_target)
            write_ms.append((time.perf_counter() - t0) * 1000)

            n += 1

        return {
            "stat_ms": _summarize(stat_ms),
            "read_ms": _summarize(read_ms),
            "write_fsync_unlink_ms": _summarize(write_ms),
            "n_completed": n,
        }
    finally:
        with contextlib.suppress(OSError):
            read_target.unlink()


def _sustained_io(dir_path: Path) -> dict:
    """One sustained ~4MB write-then-read-back pass (burst-credit / throughput
    signal that the small-file latency loop above won't surface)."""
    payload = os.urandom(_SUSTAINED_IO_SIZE_BYTES)
    target = dir_path / f".nbi-perf-probe-io-{os.getpid()}-{time.monotonic_ns()}"
    try:
        t0 = time.perf_counter()
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        write_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        with open(target, "rb") as fh:
            read_back = fh.read()
        read_s = time.perf_counter() - t0

        size_mb = _SUSTAINED_IO_SIZE_BYTES / (1024 * 1024)
        return {
            "size_mb": size_mb,
            "write_mb_s": round(size_mb / write_s, 3) if write_s > 0 else None,
            "read_mb_s": round(size_mb / read_s, 3) if read_s > 0 else None,
            "bytes_read_back": len(read_back),
        }
    finally:
        with contextlib.suppress(OSError):
            target.unlink()


def _mount_info(dir_path: Path) -> dict:
    system = platform.system()
    try:
        target = str(dir_path.resolve())
    except OSError:
        target = str(dir_path)
    if system == "Linux":
        return _mount_info_linux(target)
    if system == "Darwin":
        return _mount_info_darwin(target)
    return {"fstype": None, "options": None, "note": f"unsupported platform {system}"}


# Mount options are copied out of /proc/mounts and macOS `mount` verbatim,
# and on CIFS/SMB those carry the server address and the mount credential
# ("addr=10.20.30.40", "username=svc", "unc=\\\\server\\share"). The probe
# document promises no hostnames and no credentials, and _scrub only knows
# about the home path and the login name, so filter here instead: bare flags
# are never identifying and are kept as-is, while a "key=value" option is
# kept only when the key is on this list.
_SAFE_MOUNT_OPTION_KEYS = {
    "vers",
    "minorversion",
    "proto",
    "mountproto",
    "sec",
    "rsize",
    "wsize",
    "bsize",
    "timeo",
    "retrans",
    "actimeo",
    "acregmin",
    "acregmax",
    "acdirmin",
    "acdirmax",
    "lookupcache",
    "local_lock",
    "namlen",
    "cachetype",
}


def _safe_mount_options(options: Optional[str]) -> Optional[str]:
    if not options:
        return options
    kept = []
    dropped = 0
    for token in options.split(","):
        token = token.strip()
        if not token:
            continue
        if "=" not in token:
            kept.append(token)
            continue
        key = token.split("=", 1)[0].strip().lower()
        if key in _SAFE_MOUNT_OPTION_KEYS:
            kept.append(token)
        else:
            dropped += 1
    if dropped:
        # Say that something was withheld rather than silently shortening
        # the list, so nobody reads the result as the full mount line.
        kept.append(f"+{dropped} redacted")
    return ",".join(kept)


def _longest_matching_mount(target: str, entries: list) -> Optional[tuple]:
    """entries: list of (mountpoint, fstype, options). Returns the entry whose
    mountpoint is the longest prefix of target (standard mount-resolution
    rule: the most specific/nested mount wins)."""
    best = None
    for mountpoint, fstype, options in entries:
        mp = mountpoint.rstrip("/") or "/"
        if target == mp or mp == "/" or target.startswith(mp + "/"):
            if best is None or len(mp) > len(best[0].rstrip("/") or "/"):
                best = (mountpoint, fstype, options)
    return best


def _mount_info_linux(target: str) -> dict:
    entries = []
    with open("/proc/mounts", "r") as f:
        for line in f:
            fields = line.split()
            if len(fields) >= 4:
                entries.append((fields[1], fields[2], fields[3]))
    best = _longest_matching_mount(target, entries)
    if not best:
        return {"fstype": None, "options": None}
    _, fstype, options = best
    return {"fstype": fstype, "options": _safe_mount_options(options)}


def _mount_info_darwin(target: str) -> dict:
    out = subprocess.run(["mount"], capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_S).stdout
    entries = []
    for line in out.splitlines():
        # "<device> on <mountpoint> (<fstype>, <opt1>, <opt2>, ...)"
        if " on " not in line or "(" not in line or not line.rstrip().endswith(")"):
            continue
        _, rest = line.split(" on ", 1)
        mountpoint, paren = rest.rsplit(" (", 1)
        parts = [p.strip() for p in paren[:-1].split(",")]
        fstype = parts[0] if parts else None
        options = ",".join(parts[1:])
        entries.append((mountpoint.strip(), fstype, options))
    # macOS firmlinks: /Users lives on the Data volume but is presented at
    # /Users, and Path.resolve() does not follow firmlinks. A naive
    # longest-prefix match therefore lands on "/", the sealed read-only
    # system volume, and a home directory gets reported as read-only apfs.
    # Re-match against the Data-volume path when that mount exists, since
    # that is where the write actually lands.
    data_root = "/System/Volumes/Data"
    if any(mountpoint == data_root for mountpoint, _f, _o in entries):
        if not target.startswith(data_root) and os.path.exists(data_root + target):
            target = data_root + target
    best = _longest_matching_mount(target, entries)
    if not best:
        return {"fstype": None, "options": None}
    _, fstype, options = best
    return {"fstype": fstype, "options": _safe_mount_options(options)}


def _session_scan(claude_home: Path, bound_s: float) -> dict:
    """Bounded file-count + total-bytes scan of ~/.claude's projects/ and
    sessions/ subdirectories, if present."""
    result: dict = {}
    start = time.monotonic()
    for name in ("projects", "sessions"):
        cand = claude_home / name
        if not cand.is_dir():
            continue
        count = 0
        total_bytes = 0
        truncated = False
        for root, _dirs, files in os.walk(cand):
            for fn in files:
                if time.monotonic() - start > bound_s:
                    truncated = True
                    break
                with contextlib.suppress(OSError):
                    total_bytes += os.path.getsize(os.path.join(root, fn))
                    count += 1
            if truncated:
                break
        result[name] = {"file_count": count, "total_bytes": total_bytes, "truncated": truncated}
    return result


def _fs_checks(pool: concurrent.futures.ThreadPoolExecutor, nbi_config: Any, checks: list) -> None:
    """Run the filesystem group strictly one check at a time.

    These checks measure the thing they run on: _latency_loop times
    individual stat/read/write+fsync ops in a directory that _sustained_io
    is writing and fsyncing a 4 MB file into. Overlapping them makes the
    probe measure its own contention, and on exactly the EFS/NFS homes this
    exists to diagnose the inflation is largest. Sequential submission still
    leaves a hung check abandonable: the pool has spare workers, so the next
    check gets one even when a previous thread is stuck on an
    uninterruptible syscall.
    """
    from notebook_intelligence.util import get_jupyter_root_dir

    targets = []

    nbi_user_dir = getattr(nbi_config, "nbi_user_dir", None)
    if nbi_user_dir:
        targets.append(("nbi_user_dir", Path(nbi_user_dir)))

    try:
        jupyter_root = Path(get_jupyter_root_dir())
    except Exception:
        jupyter_root = Path(os.getcwd())
    targets.append(("jupyter_root", jupyter_root))

    # Bounded, like every other filesystem touch in this module. A stat on a
    # hung NFS/EFS mount is uninterruptible, and this one used to run inline
    # on the caller's thread -- which is the Jupyter server's default
    # executor thread -- so exactly the condition the probe exists to
    # diagnose would hang run_probe forever and burn a thread the server
    # also needs. A timeout here is the finding, not an error.
    claude_home = Path.home() / ".claude"
    presence = _run_check(
        pool,
        "fs.claude_home",
        "filesystem",
        DEFAULT_TIMEOUT_S,
        lambda d=claude_home: {"present": d.is_dir()},
    )
    if presence["status"] != "ok":
        checks.append(presence)
    elif presence["detail"].get("present"):
        targets.append(("claude_home", claude_home))
    else:
        checks.append(_skipped_entry("fs.claude_home", "filesystem", "not_present"))

    for label, dir_path in targets:
        checks.append(
            _run_check(
                pool,
                f"fs.{label}.latency",
                "filesystem",
                DEFAULT_TIMEOUT_S,
                lambda d=dir_path: _latency_loop(d, DEFAULT_TIMEOUT_S * 0.7),
            )
        )
        checks.append(
            _run_check(
                pool,
                f"fs.{label}.sustained_io",
                "filesystem",
                DEFAULT_TIMEOUT_S,
                lambda d=dir_path: _sustained_io(d),
            )
        )
        checks.append(
            _run_check(
                pool,
                f"fs.{label}.mount",
                "filesystem",
                # macOS shells out to `mount` with its own DEFAULT_TIMEOUT_S,
                # so the outer budget needs the same margin the subprocess
                # group uses.
                DEFAULT_TIMEOUT_S + _SLOW_CHECK_MARGIN_S,
                lambda d=dir_path: _mount_info(d),
            )
        )
        if label == "claude_home":
            # Outer budget is the inner scan bound plus margin, not equal to
            # it: an equal budget abandons the future (discarding every
            # sample already collected) at the exact instant the scan would
            # have returned on its own.
            checks.append(
                _run_check(
                    pool,
                    "fs.claude_home.session_scan",
                    "filesystem",
                    DEFAULT_TIMEOUT_S + _SLOW_CHECK_MARGIN_S,
                    lambda d=dir_path: _session_scan(d, DEFAULT_TIMEOUT_S),
                )
            )


# ---------------------------------------------------------------------------
# Subprocess group
# ---------------------------------------------------------------------------


def _run_versioned(cmd: list) -> dict:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise _CheckTimeout(cmd[0])
    wall_ms = (time.perf_counter() - t0) * 1000
    return {
        "wall_ms": round(wall_ms, 3),
        "returncode": proc.returncode,
        "version": (proc.stdout.strip() or proc.stderr.strip()),
    }


def _node_version() -> dict:
    return _run_versioned(["node", "--version"])


def _claude_cli_version() -> dict:
    cli_path = resolve_claude_cli_path()
    if not cli_path:
        raise RuntimeError("claude CLI did not resolve")
    return _run_versioned([cli_path, "--version"])


def _npm_cache_path() -> dict:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["npm", "config", "get", "cache"], capture_output=True, text=True, timeout=DEFAULT_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        raise _CheckTimeout("npm")
    wall_ms = (time.perf_counter() - t0) * 1000
    return {"wall_ms": round(wall_ms, 3), "path": proc.stdout.strip()}


def _subprocess_checks(pool: concurrent.futures.ThreadPoolExecutor, checks: list) -> None:
    # Sequential for the same reason as the filesystem group: these spawn
    # node and the Claude CLI, whose cost is dominated by reading their own
    # (often network-mounted) install trees, so running them against each
    # other measures contention rather than cold-start.
    # Outer budget sits above the inner subprocess timeout, not equal to it.
    # Equal budgets mean the outer timer (which starts before the worker even
    # picks the job up) always fires first, so a genuinely slow command is
    # reported "timed_out" instead of "ok" with a large wall_ms, and the
    # slow-but-completing band can never be produced.
    budget = DEFAULT_TIMEOUT_S + _SLOW_CHECK_MARGIN_S
    checks.append(_run_check(pool, "subprocess.node_version", "subprocess", budget, _node_version))
    checks.append(
        _run_check(pool, "subprocess.claude_cli_version", "subprocess", budget, _claude_cli_version)
    )
    checks.append(_run_check(pool, "subprocess.npm_cache_path", "subprocess", budget, _npm_cache_path))


# ---------------------------------------------------------------------------
# Runtime group
# ---------------------------------------------------------------------------


def _process_rss() -> dict:
    if resource is None:
        raise _Skipped("resource module not available")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    # ru_maxrss is KB on Linux, bytes on macOS. It is also the lifetime
    # high-water mark (peak RSS since process start), not the current RSS:
    # it only ever goes up, so a low reading here does not mean memory use
    # has since dropped. Named max_rss_kb (not rss_kb) to keep that honest.
    rss_kb = usage.ru_maxrss / 1024 if platform.system() == "Darwin" else usage.ru_maxrss
    return {"max_rss_kb": round(rss_kb, 1)}


def _loadavg() -> dict:
    one, five, fifteen = os.getloadavg()
    return {"load1": one, "load5": five, "load15": fifteen}


def _cgroup_cpu() -> dict:
    for path in ("/sys/fs/cgroup/cpu.stat", "/sys/fs/cgroup/cpu/cpu.stat"):
        if os.path.isfile(path) and os.access(path, os.R_OK):
            data = {}
            with open(path, "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) == 2 and parts[0] in ("nr_throttled", "throttled_usec", "throttled_time"):
                        data[parts[0]] = parts[1]
            return {"path": path, **data}
    raise _Skipped("cgroup cpu.stat not readable")


def _interpreter_info() -> dict:
    return {"python_version": platform.python_version(), "platform": platform.platform()}


def _runtime_checks(pool: concurrent.futures.ThreadPoolExecutor, checks: list) -> None:
    # loadavg is a contention reading, so it is taken while the probe is
    # doing as little as possible rather than alongside its own 4 MB writes.
    checks.append(_run_check(pool, "runtime.process_rss", "runtime", DEFAULT_TIMEOUT_S, _process_rss))
    checks.append(_run_check(pool, "runtime.loadavg", "runtime", DEFAULT_TIMEOUT_S, _loadavg))
    checks.append(_run_check(pool, "runtime.cgroup_cpu", "runtime", DEFAULT_TIMEOUT_S, _cgroup_cpu))
    checks.append(_run_check(pool, "runtime.interpreter", "runtime", DEFAULT_TIMEOUT_S, _interpreter_info))
    # Event-loop lag is intentionally not measured here: the caller (the
    # handler that owns the event loop) is the only place that can sample
    # it meaningfully, so it is out of scope for this blocking, off-loop probe.


# ---------------------------------------------------------------------------
# Network group
# ---------------------------------------------------------------------------


def _resolve_target_base_url(nbi_config: Any) -> str:
    """Best-effort resolution of the currently configured LLM base URL.

    Checked in acp -> claude -> openai-compatible-provider priority order,
    each read straight off nbi_config (never from an argument, env var, or
    anywhere else -- the network target must be admin/user configured).
    Falls back to Anthropic's default API host when nothing is configured.
    """

    def _acp_base_url():
        settings = getattr(nbi_config, "acp_settings", None) or {}
        return settings.get("base_url")

    def _claude_base_url():
        settings = getattr(nbi_config, "claude_settings", None) or {}
        return settings.get("base_url")

    def _openai_compatible_base_url():
        chat_model = getattr(nbi_config, "chat_model", None) or {}
        if chat_model.get("provider") in ("openai-compatible", "litellm-compatible"):
            # base_url lives in the properties list ({'id','value'} dicts, the
            # shape ai_service_manager consumes and the settings panel POSTs),
            # not as a top-level key. A top-level "base_url" is checked only
            # as a secondary fallback, in case that shape ever changes.
            for prop in chat_model.get("properties") or []:
                if isinstance(prop, dict) and prop.get("id") == "base_url":
                    return prop.get("value")
            return chat_model.get("base_url")
        return None

    for getter in (_acp_base_url, _claude_base_url, _openai_compatible_base_url):
        try:
            url = getter()
        except Exception:
            url = None
        if url:
            return url
    return "https://api.anthropic.com"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # report the 3xx as-is instead of following it


def _dn_component(dn_tuple, key: str = "commonName") -> Optional[str]:
    if not dn_tuple:
        return None
    for rdn in dn_tuple:
        for k, v in rdn:
            if k == key:
                return v
    return None


def _leaf_cert_names(cert_der: bytes, fallback_dict: Optional[dict]) -> tuple:
    """(issuer_cn, subject_cn) for a DER certificate.

    ``SSLSocket.getpeercert()`` returns an empty dict when the peer was not
    validated, and the capture leg below deliberately does not validate, so
    the parsed-dict route yields nothing exactly when an intercepted
    connection makes the issuer most interesting. Parse the DER instead;
    ``cryptography`` is already a hard dependency. The dict is kept as a
    fallback for the verified case.
    """
    if cert_der:
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID

            cert = x509.load_der_x509_certificate(cert_der)

            def _cn(name):
                attrs = name.get_attributes_for_oid(NameOID.COMMON_NAME)
                return attrs[0].value if attrs else None

            return _cn(cert.issuer), _cn(cert.subject)
        except Exception:
            pass
    if fallback_dict:
        return (
            _dn_component(fallback_dict.get("issuer")),
            _dn_component(fallback_dict.get("subject")),
        )
    return None, None


def _capture_tls_cert(tls_sock: ssl.SSLSocket) -> dict:
    """Read what the endpoint presented on an already-open TLS socket.

    Pure inspection: opens nothing, sends nothing.
    """
    cert_bin = tls_sock.getpeercert(binary_form=True)
    issuer_cn, subject_cn = _leaf_cert_names(cert_bin, tls_sock.getpeercert())

    fingerprints = []
    if cert_bin:
        fingerprints.append(hashlib.sha256(cert_bin).hexdigest())
    # Full chain (first 3) needs SSLSocket.get_unverified_chain(), added in
    # Python 3.13. get_verified_chain() is useless on the capture leg, which
    # does not verify. On older runtimes only the leaf is reachable through
    # the stdlib, so this leaves the leaf-only fingerprint above untouched.
    for attr in ("get_unverified_chain", "get_verified_chain"):
        get_chain = getattr(tls_sock, attr, None)
        if get_chain is None:
            continue
        try:
            chain_fps = [
                hashlib.sha256(cert.public_bytes(ssl.Encoding.DER)).hexdigest() for cert in get_chain()[:3]
            ]
        except Exception:
            continue
        if chain_fps:
            fingerprints = chain_fps
            break

    return {
        "issuer_cn": issuer_cn,
        "subject_cn": subject_cn,
        "fingerprint_sha256": fingerprints[:3],
    }


def _clock_skew_s(date_header: Optional[str]) -> Optional[float]:
    if not date_header:
        return None
    try:
        remote = parsedate_to_datetime(date_header)
        if remote.tzinfo is None:
            remote = remote.replace(tzinfo=datetime.timezone.utc)
        local = datetime.datetime.now(datetime.timezone.utc)
        return round((local - remote).total_seconds(), 3)
    except Exception:
        return None


def _http_probe(target_url: str, proxy_url: Optional[str]) -> dict:
    """Exactly one HTTPS request: HEAD, falling back to GET on 405. No
    redirect following, unauthenticated, tagged with the probe's own UA."""
    handlers = [_NoRedirectHandler()]
    if proxy_url:
        handlers.append(urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urllib.request.build_opener(*handlers)
    headers = {"User-Agent": f"nbi-perf-probe/{_NBI_VERSION}"}

    def _do(method: str):
        req = urllib.request.Request(target_url, method=method, headers=headers)
        t0 = time.perf_counter()
        try:
            resp = opener.open(req, timeout=NETWORK_TIMEOUT_S)
        except urllib.error.HTTPError as e:
            ttfb = (time.perf_counter() - t0) * 1000
            return e.code, ttfb, e.headers
        ttfb = (time.perf_counter() - t0) * 1000
        status, resp_headers = resp.status, resp.headers
        resp.close()
        return status, ttfb, resp_headers

    try:
        status, ttfb_ms, resp_headers = _do("HEAD")
        if status == 405:
            status, ttfb_ms, resp_headers = _do("GET")
    except urllib.error.URLError as e:
        # urlopen wraps a raw socket timeout as URLError(reason=TimeoutError(...))
        # rather than raising TimeoutError directly.
        if isinstance(e.reason, TimeoutError):
            raise _CheckTimeout("http")
        raise

    clock_skew_s = _clock_skew_s(resp_headers.get("Date") if resp_headers else None)
    return {
        "status_code": status,
        "ttfb_ms": round(ttfb_ms, 3),
        "caption": "unauthenticated; may not reflect authenticated latency",
        "clock_skew_s": clock_skew_s,
    }


def _proxy_env_flags(host: str) -> dict:
    names = ["HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY"]
    present = {n: (n in os.environ or n.lower() in os.environ) for n in names}
    no_proxy_matches_host = False
    with contextlib.suppress(Exception):
        no_proxy_matches_host = bool(urllib.request.proxy_bypass(host))
    ca_bundle_env = {
        n: (n in os.environ)
        for n in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "NODE_EXTRA_CA_CERTS")
    }
    return {
        "proxy_vars_set": present,
        "no_proxy_matches_host": no_proxy_matches_host,
        "ca_bundle_env": ca_bundle_env,
    }


def _open_tls(
    host: str,
    port: int,
    proxy_url: Optional[str],
    ctx: ssl.SSLContext,
    timings: Optional[dict] = None,
) -> ssl.SSLSocket:
    """Open one TLS connection to (host, port), through the proxy when one is
    configured, and record DNS/TCP/handshake timings into ``timings``.

    Both legs of the network check go through here so they take the same
    route: a verification leg that bypassed the proxy would be measuring a
    path the product never uses, and in a proxy-only egress environment it
    would simply be refused.

    Raises ``_CheckTimeout`` on a timeout and lets TLS errors propagate.
    """
    raw_sock: Optional[socket.socket] = None
    tls_sock: Optional[ssl.SSLSocket] = None

    def _record(key: str, t0: float) -> None:
        if timings is not None:
            timings[key] = round((time.perf_counter() - t0) * 1000, 3)

    try:
        try:
            if proxy_url:
                p = urllib.parse.urlsplit(proxy_url)
                proxy_host, proxy_port = p.hostname, p.port or 80

                t0 = time.perf_counter()
                socket.getaddrinfo(proxy_host, proxy_port)
                _record("proxy_dns_ms", t0)

                t0 = time.perf_counter()
                raw_sock = socket.create_connection((proxy_host, proxy_port), timeout=NETWORK_TIMEOUT_S)
                _record("proxy_tcp_connect_ms", t0)

                connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
                raw_sock.sendall(connect_req.encode("ascii"))
                resp = raw_sock.recv(4096)
                status_line = resp.split(b"\r\n", 1)[0]
                if b" 200 " not in (b" " + status_line):
                    raise ConnectionError("proxy CONNECT failed")
            else:
                t0 = time.perf_counter()
                socket.getaddrinfo(host, port)
                _record("dns_ms", t0)

                t0 = time.perf_counter()
                raw_sock = socket.create_connection((host, port), timeout=NETWORK_TIMEOUT_S)
                _record("tcp_connect_ms", t0)

            t0 = time.perf_counter()
            tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
            _record("tls_handshake_ms", t0)
            return tls_sock
        except (socket.timeout, TimeoutError):
            raise _CheckTimeout("connect")
    finally:
        # wrap_socket detaches raw_sock on success and on most failures, so
        # closing it here would be a double close; only close it when no
        # SSLSocket was ever produced.
        if raw_sock is not None and tls_sock is None:
            with contextlib.suppress(OSError):
                raw_sock.close()


def _capture_context() -> ssl.SSLContext:
    """A deliberately non-verifying context for the certificate-capture leg.

    Verifying here would defeat the purpose: against an interception
    certificate the handshake fails, and the certificate we most need to
    show the operator is exactly the one we would then never see. Nothing is
    sent over this socket; it is opened, read, and closed. The verification
    verdict comes from a separate leg that does verify, and the HTTP leg
    below uses urllib's normal verifying path.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _verifies_against_default_bundle(
    host: str, port: int, proxy_url: Optional[str]
) -> Optional[bool]:
    """True/False from a real verifying handshake, None if undeterminable.

    None means the connection failed for a reason other than verification
    (reset, refused, timeout), which must not be reported as either verdict:
    calling that "verified" would invert the interception signal.
    """
    tls_sock = None
    try:
        tls_sock = _open_tls(host, port, proxy_url, ssl.create_default_context())
        return True
    except ssl.SSLCertVerificationError:
        return False
    except Exception:
        return None
    finally:
        if tls_sock is not None:
            with contextlib.suppress(OSError):
                tls_sock.close()


def _network_probe(base_url: str) -> dict:
    parsed = urllib.parse.urlsplit(base_url)
    host = parsed.hostname
    if not host:
        raise ValueError("configured base URL has no host")
    scheme = parsed.scheme or "https"
    port = parsed.port or (443 if scheme == "https" else 80)

    proxies = urllib.request.getproxies()
    proxy_url = None
    if not urllib.request.proxy_bypass(host):
        proxy_url = proxies.get(scheme) or proxies.get("https") or proxies.get("http")
    path = "via_proxy" if proxy_url else "direct"

    timings: dict = {}
    base_doc = {
        "target": {"scheme": scheme, "host": host, "port": port},
        "path": path,
        "timings_ms": timings,
    }

    # Leg 1: time the connection and read what the endpoint presents.
    try:
        tls_sock = _open_tls(host, port, proxy_url, _capture_context(), timings)
    except _CheckTimeout:
        raise
    except Exception as e:
        # A handshake failure below the timeout threshold is reportable data,
        # not something to discard: keep the dns/tcp timings already measured
        # rather than losing them to _run_check's generic error path. The
        # exception message is dropped on purpose (privacy rule); only the
        # class name is safe to keep.
        return {**base_doc, "tls_error": type(e).__name__}

    try:
        cert_info = _capture_tls_cert(tls_sock)
    finally:
        with contextlib.suppress(OSError):
            tls_sock.close()

    # Leg 2: does that certificate actually verify?
    cert_info["verified_against_default_bundle"] = _verifies_against_default_bundle(
        host, port, proxy_url
    )

    target_url = base_url if base_url.endswith("/") else base_url + "/"
    # The HTTP leg verifies (it is a normal urllib request), so against an
    # interception certificate it fails. Letting that propagate would throw
    # away the DNS/TCP/TLS timings and the captured certificate chain, which
    # is the entire finding in exactly the deployment this check exists for.
    # Report the failure as a field and keep the rest.
    try:
        http_result = _http_probe(target_url, proxy_url)
    except _CheckTimeout:
        http_result = {"error": "timeout"}
    except Exception as e:
        # Class name only: the message can carry the URL and the host.
        http_result = {"error": type(e).__name__}
    env_flags = _proxy_env_flags(host)

    return {
        **base_doc,
        "http": http_result,
        "tls": cert_info,
        "env": env_flags,
    }



def _network_checks(
    pool: concurrent.futures.ThreadPoolExecutor,
    include_network: bool,
    nbi_config: Any,
    checks: list,
    pending: list,
) -> bool:
    """Returns whether the network group ran (drives contains_internal_hostnames)."""
    if not include_network:
        checks.append(_skipped_entry("network.endpoint", "network", "include_network=False"))
        return False

    base_url = _resolve_target_base_url(nbi_config)
    # The check body is sequential DNS + up to two raw connections + HEAD +
    # a possible GET retry, each already bounded by its own NETWORK_TIMEOUT_S
    # -- so the body as a whole can take several times NETWORK_TIMEOUT_S even
    # when every individual leg behaves. The outer budget has to cover that,
    # not just one leg.
    pending.append(
        _submit_check(pool, "network.endpoint", "network", NETWORK_TIMEOUT_S * 4, lambda: _network_probe(base_url))
    )
    return True


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------


def _current_username() -> Optional[str]:
    with contextlib.suppress(Exception):
        return os.getlogin()
    with contextlib.suppress(Exception):
        return getpass.getuser()
    return None


def _scrub(doc: dict) -> dict:
    """Single scrub pass applied to the whole document right before return:
    home directory prefix -> "~" in every string, username stripped
    wherever it appears, and any literal "hostname" key dropped. Network
    checks are allowed to keep the admin-configured target host inside
    their own detail; that is flagged via contains_internal_hostnames
    rather than redacted."""
    home = str(Path.home())
    real_home = os.path.realpath(home)
    username = _current_username()
    _username_re = re.compile(r"\b" + re.escape(username) + r"\b") if username else None

    def scrub_str(s: str) -> str:
        if home and home in s:
            s = s.replace(home, "~")
        if real_home != home and real_home in s:
            # Symlinked/automounted homes: subprocess output (npm cache path,
            # node stderr) often prints the resolved path, not $HOME.
            s = s.replace(real_home, "~")
        if username:
            # Word-boundary match: covers path segments and prose ("mounted
            # by <user>" in macOS mount options) without mangling ids that
            # merely contain the username as a substring.
            s = _username_re.sub("~user", s)
        return s

    def walk(obj):
        if isinstance(obj, str):
            return scrub_str(obj)
        if isinstance(obj, dict):
            return {k: walk(v) for k, v in obj.items() if k != "hostname"}
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    return walk(doc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_probe(include_network: bool, nbi_config: Any) -> dict:
    """Run the full diagnostic battery and return a scrubbed document.

    Blocking -- callers on an event loop must run this via
    ``loop.run_in_executor``.
    """
    # A fresh pool per run: a previous run's abandoned (hung) checks would
    # otherwise permanently occupy the workers and every later probe would
    # report timed_out.
    pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=_POOL_SIZE, thread_name_prefix="nbi-perf-probe"
    )
    checks: list = []
    pending: list = []
    try:
        # The network check is the long pole (up to NETWORK_TIMEOUT_S * 4)
        # and is the one check that touches neither the local disk nor the
        # CPU, so it is submitted first and collected last: it overlaps
        # everything else for free. Everything else runs strictly one at a
        # time, because the filesystem, subprocess, and contention checks
        # all measure resources they would otherwise be competing with each
        # other for.
        network_ran = _network_checks(pool, include_network, nbi_config, checks, pending)
        _fs_checks(pool, nbi_config, checks)
        _subprocess_checks(pool, checks)
        _runtime_checks(pool, checks)

        for check_id, group, timeout_s, future in pending:
            checks.append(_collect_check(check_id, group, timeout_s, future))
    finally:
        # The pool is abandoned, not joined: a check hung on an
        # uninterruptible syscall has already been reported as timed_out
        # above, and waiting for its thread here would defeat the point of
        # that timeout. A leaked thread per truly-hung syscall is the
        # documented cost.
        pool.shutdown(wait=False, cancel_futures=True)

    doc = {
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "checks": checks,
    }
    if network_ran:
        doc["contains_internal_hostnames"] = True

    return _scrub(doc)
