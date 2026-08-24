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
    return {"fstype": fstype, "options": options}


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
    best = _longest_matching_mount(target, entries)
    if not best:
        return {"fstype": None, "options": None}
    _, fstype, options = best
    return {"fstype": fstype, "options": options}


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

    claude_home = Path.home() / ".claude"
    if claude_home.is_dir():
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
                DEFAULT_TIMEOUT_S,
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
    checks.append(_run_check(pool, "subprocess.node_version", "subprocess", DEFAULT_TIMEOUT_S, _node_version))
    checks.append(
        _run_check(pool, "subprocess.claude_cli_version", "subprocess", DEFAULT_TIMEOUT_S, _claude_cli_version)
    )
    checks.append(_run_check(pool, "subprocess.npm_cache_path", "subprocess", DEFAULT_TIMEOUT_S, _npm_cache_path))


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


def _capture_tls_cert(tls_sock: ssl.SSLSocket, host: str, port: int) -> dict:
    cert_bin = tls_sock.getpeercert(binary_form=True)
    cert_dict = tls_sock.getpeercert()
    fingerprints = []
    if cert_bin:
        fingerprints.append(hashlib.sha256(cert_bin).hexdigest())
    # Full chain (first 3) requires SSLSocket.get_verified_chain(), added in
    # Python 3.13; on older runtimes only the leaf certificate is available
    # via the stdlib, so this best-effort attempt leaves the leaf-only
    # fingerprint captured above untouched on any failure.
    get_chain = getattr(tls_sock, "get_verified_chain", None)
    if get_chain is not None:
        try:
            chain_fps = [
                hashlib.sha256(cert.public_bytes(ssl.Encoding.DER)).hexdigest() for cert in get_chain()[:3]
            ]
            if chain_fps:
                fingerprints = chain_fps
        except Exception:
            pass

    # None = could not determine (connection-level failure); True only after
    # a successful verifying handshake. Anything else must not report True:
    # a MITM that resets this second connection would otherwise be described
    # as a verified chain, inverting the interception signal.
    verified_default: Optional[bool] = None
    with contextlib.suppress(Exception):
        default_ctx = ssl.create_default_context()
        probe_sock = socket.create_connection((host, port), timeout=NETWORK_TIMEOUT_S)
        try:
            with default_ctx.wrap_socket(probe_sock, server_hostname=host):
                verified_default = True
        except ssl.SSLCertVerificationError:
            verified_default = False
        finally:
            with contextlib.suppress(OSError):
                probe_sock.close()

    return {
        "issuer_cn": _dn_component(cert_dict.get("issuer")) if cert_dict else None,
        "subject_cn": _dn_component(cert_dict.get("subject")) if cert_dict else None,
        "fingerprint_sha256": fingerprints[:3],
        "verified_against_default_bundle": verified_default,
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

    timings: dict = {}
    raw_sock: Optional[socket.socket] = None
    tls_sock: Optional[ssl.SSLSocket] = None
    tls_error: Optional[str] = None
    try:
        try:
            if proxy_url:
                path = "via_proxy"
                p = urllib.parse.urlsplit(proxy_url)
                proxy_host, proxy_port = p.hostname, p.port or 80

                t0 = time.perf_counter()
                socket.getaddrinfo(proxy_host, proxy_port)
                timings["proxy_dns_ms"] = round((time.perf_counter() - t0) * 1000, 3)

                t0 = time.perf_counter()
                raw_sock = socket.create_connection((proxy_host, proxy_port), timeout=NETWORK_TIMEOUT_S)
                timings["proxy_tcp_connect_ms"] = round((time.perf_counter() - t0) * 1000, 3)

                connect_req = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n"
                raw_sock.sendall(connect_req.encode("ascii"))
                resp = raw_sock.recv(4096)
                status_line = resp.split(b"\r\n", 1)[0]
                if b" 200 " not in (b" " + status_line):
                    raise ConnectionError("proxy CONNECT failed")

                t0 = time.perf_counter()
                ctx = ssl.create_default_context()
                try:
                    tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
                except (socket.timeout, TimeoutError):
                    raise
                except Exception as e:
                    tls_error = type(e).__name__
                else:
                    timings["tls_handshake_ms"] = round((time.perf_counter() - t0) * 1000, 3)
            else:
                path = "direct"
                t0 = time.perf_counter()
                socket.getaddrinfo(host, port)
                timings["dns_ms"] = round((time.perf_counter() - t0) * 1000, 3)

                t0 = time.perf_counter()
                raw_sock = socket.create_connection((host, port), timeout=NETWORK_TIMEOUT_S)
                timings["tcp_connect_ms"] = round((time.perf_counter() - t0) * 1000, 3)

                t0 = time.perf_counter()
                ctx = ssl.create_default_context()
                try:
                    tls_sock = ctx.wrap_socket(raw_sock, server_hostname=host)
                except (socket.timeout, TimeoutError):
                    raise
                except Exception as e:
                    tls_error = type(e).__name__
                else:
                    timings["tls_handshake_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        except (socket.timeout, TimeoutError):
            raise _CheckTimeout("connect")
    finally:
        # tls_sock (once it exists) owns the underlying fd; only close
        # raw_sock directly when the handshake never got that far, or failed
        # before/at wrap_socket. Without this, a non-timeout handshake
        # failure (an intercepting proxy presenting an untrusted cert, a
        # reset mid-handshake, ...) leaked the raw socket on every failure.
        if raw_sock is not None and tls_sock is None:
            with contextlib.suppress(OSError):
                raw_sock.close()

    if tls_error is not None:
        # A TLS handshake failure below the timeout threshold is real,
        # reportable data, not something to discard: return what was
        # already measured (dns/tcp timing) plus the failure itself, instead
        # of raising and losing it to _run_check's generic error path.
        # Exception message intentionally dropped (privacy rule): only the
        # class name is safe to keep.
        return {
            "target": {"scheme": scheme, "host": host, "port": port},
            "path": path,
            "timings_ms": timings,
            "tls_error": tls_error,
        }

    try:
        cert_info = _capture_tls_cert(tls_sock, host, port)
    finally:
        with contextlib.suppress(OSError):
            tls_sock.close()

    target_url = base_url if base_url.endswith("/") else base_url + "/"
    http_result = _http_probe(target_url, proxy_url)
    env_flags = _proxy_env_flags(host)

    return {
        "target": {"scheme": scheme, "host": host, "port": port},
        "path": path,
        "timings_ms": timings,
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
