import concurrent.futures
import datetime
import http.server
import json
import os
import socket
import threading
import time

import notebook_intelligence.perf_probe as pp


class FakeNBIConfig:
    """Minimal duck-typed stand-in for NBIConfig -- run_probe only ever
    reads nbi_user_dir, claude_settings, acp_settings, and chat_model off
    the object it's handed."""

    def __init__(self, nbi_user_dir):
        self.nbi_user_dir = nbi_user_dir
        self.claude_settings = {}
        self.acp_settings = {}
        self.chat_model = {"provider": "github-copilot"}


def _make_config(tmp_path):
    user_dir = tmp_path / "nbi_user_dir"
    user_dir.mkdir()
    return FakeNBIConfig(str(user_dir))


# ---------------------------------------------------------------------------
# Document schema
# ---------------------------------------------------------------------------


def test_run_probe_schema(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doc = pp.run_probe(False, _make_config(tmp_path))

    assert doc["schema_version"] == 1
    datetime.datetime.fromisoformat(doc["generated_at"])  # raises if not iso8601
    assert isinstance(doc["checks"], list) and doc["checks"]

    for check in doc["checks"]:
        assert set(check) == {"id", "group", "status", "detail"}
        assert check["group"] in {"filesystem", "subprocess", "runtime", "network"}
        assert check["status"] in {"ok", "timed_out", "error", "skipped"}
        assert isinstance(check["detail"], dict)

    # network group didn't run -> no internal-hostname flag
    assert "contains_internal_hostnames" not in doc


def test_network_skipped_when_include_network_false(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    doc = pp.run_probe(False, _make_config(tmp_path))

    net_checks = [c for c in doc["checks"] if c["group"] == "network"]
    assert len(net_checks) == 1
    assert net_checks[0]["status"] == "skipped"
    assert "contains_internal_hostnames" not in doc


# ---------------------------------------------------------------------------
# Filesystem checks
# ---------------------------------------------------------------------------


def test_fs_latency_and_sustained_io_plausible(tmp_path):
    latency = pp._latency_loop(tmp_path, 2.0)
    assert latency["n_completed"] > 0
    for key in ("stat_ms", "read_ms", "write_fsync_unlink_ms"):
        stats = latency[key]
        assert stats["min_ms"] is not None
        assert stats["min_ms"] >= 0
        assert stats["max_ms"] >= stats["min_ms"]
        assert stats["first_iteration_ms"] is not None

    io = pp._sustained_io(tmp_path)
    assert io["write_mb_s"] is None or io["write_mb_s"] > 0
    assert io["read_mb_s"] is None or io["read_mb_s"] > 0
    assert io["bytes_read_back"] == pp._SUSTAINED_IO_SIZE_BYTES


def test_latency_loop_early_stop(tmp_path, monkeypatch):
    real_fsync = os.fsync

    def slow_fsync(fd):
        time.sleep(0.1)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", slow_fsync)
    result = pp._latency_loop(tmp_path, bound_s=0.25)

    assert 0 < result["n_completed"] < pp._MAX_LATENCY_ITERATIONS


def test_claude_home_skipped_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(pp.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / ".claude").exists()

    doc = pp.run_probe(False, _make_config(tmp_path))
    claude_check = next(c for c in doc["checks"] if c["id"] == "fs.claude_home")
    assert claude_check["status"] == "skipped"


# ---------------------------------------------------------------------------
# Runtime checks
# ---------------------------------------------------------------------------


def test_process_rss_reports_max_rss_kb():
    result = pp._process_rss()
    # ru_maxrss is a lifetime high-water mark, not the current RSS -- the key
    # says so (max_rss_kb, not rss_kb) so callers don't read it as "now".
    assert "max_rss_kb" in result
    assert "rss_kb" not in result
    assert result["max_rss_kb"] > 0


def test_process_rss_skipped_when_resource_unavailable(monkeypatch):
    # On Windows the stdlib has no `resource` module at all; perf_probe
    # guards the import so the rest of the probe still works, and this check
    # alone reports itself skipped rather than raising and taking the whole
    # import down.
    monkeypatch.setattr(pp, "resource", None)
    try:
        pp._process_rss()
        assert False, "expected _Skipped"
    except pp._Skipped:
        pass


# ---------------------------------------------------------------------------
# Timeout abandonment
# ---------------------------------------------------------------------------


def test_run_check_timeout_abandons_future():
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        start = time.monotonic()
        result = pp._run_check(
            pool, "test.sleep", "runtime", 0.1, lambda: (time.sleep(2.0), {})[1]
        )
        elapsed = time.monotonic() - start

        assert result == {"id": "test.sleep", "group": "runtime", "status": "timed_out", "detail": {"timeout_s": 0.1}}
        assert elapsed < 1.0  # returned promptly, did not wait for the sleeper
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def test_run_probe_returns_promptly_despite_a_hung_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pp, "DEFAULT_TIMEOUT_S", 0.15)
    monkeypatch.setattr(pp, "_loadavg", lambda: (time.sleep(1.5), {})[1])

    start = time.monotonic()
    doc = pp.run_probe(False, _make_config(tmp_path))
    elapsed = time.monotonic() - start

    assert elapsed < 5.0
    loadavg_check = next(c for c in doc["checks"] if c["id"] == "runtime.loadavg")
    assert loadavg_check["status"] == "timed_out"


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------


def test_scrub_removes_home_path_and_username(monkeypatch, tmp_path):
    fake_home = tmp_path / "home" / "someuser"
    fake_home.mkdir(parents=True)
    monkeypatch.setattr(pp.Path, "home", staticmethod(lambda: fake_home))
    monkeypatch.setattr(pp.os, "getlogin", lambda: "someuser")
    monkeypatch.setattr(pp.getpass, "getuser", lambda: "someuser")

    doc = {
        "schema_version": 1,
        "checks": [
            {
                "id": "fs.nbi_user_dir.mount",
                "group": "filesystem",
                "status": "ok",
                "detail": {
                    "path": f"{fake_home}/.jupyter/nbi",
                    "note": "owned by someuser, run by someuser",
                    "hostname": "should-be-dropped",
                },
            }
        ],
    }
    scrubbed = pp._scrub(doc)
    serialized = json.dumps(scrubbed)

    assert str(fake_home) not in serialized
    assert "someuser" not in serialized
    assert "~" in serialized
    assert "hostname" not in scrubbed["checks"][0]["detail"]


def test_proxy_env_flags_booleans_only(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.internal:8080")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "/some/internal/ca-bundle.pem")

    flags = pp._proxy_env_flags("example.com")

    assert flags["proxy_vars_set"]["HTTPS_PROXY"] is True
    assert flags["proxy_vars_set"]["HTTP_PROXY"] is False
    assert flags["proxy_vars_set"]["NO_PROXY"] is False
    assert flags["ca_bundle_env"]["NODE_EXTRA_CA_CERTS"] is True
    assert flags["ca_bundle_env"]["SSL_CERT_FILE"] is False
    assert isinstance(flags["no_proxy_matches_host"], bool)

    serialized = json.dumps(flags)
    assert "proxy.example.internal" not in serialized
    assert "/some/internal/ca-bundle.pem" not in serialized


# ---------------------------------------------------------------------------
# Network target resolution (never accept a URL from anywhere but nbi_config)
# ---------------------------------------------------------------------------


def test_resolve_target_base_url_priority(tmp_path):
    cfg = _make_config(tmp_path)

    cfg.acp_settings = {"base_url": "https://acp.example.com"}
    cfg.claude_settings = {"base_url": "https://claude.example.com"}
    assert pp._resolve_target_base_url(cfg) == "https://acp.example.com"

    cfg.acp_settings = {}
    assert pp._resolve_target_base_url(cfg) == "https://claude.example.com"

    cfg.claude_settings = {}
    cfg.chat_model = {
        "provider": "openai-compatible",
        "properties": [{"id": "base_url", "value": "https://oai.example.com"}],
    }
    assert pp._resolve_target_base_url(cfg) == "https://oai.example.com"

    cfg.chat_model = {"provider": "github-copilot"}
    assert pp._resolve_target_base_url(cfg) == "https://api.anthropic.com"


def test_resolve_target_base_url_falls_back_to_top_level_key(tmp_path):
    # The real persisted/POSTed shape always carries base_url inside
    # properties (see ai_service_manager.py and the settings panel), but a
    # top-level "base_url" key is still honored as a secondary fallback.
    cfg = _make_config(tmp_path)
    cfg.chat_model = {
        "provider": "openai-compatible",
        "properties": [],
        "base_url": "https://fallback.example.com",
    }
    assert pp._resolve_target_base_url(cfg) == "https://fallback.example.com"


# ---------------------------------------------------------------------------
# No-redirect HTTP probe (local loopback server only, never a real network call)
# ---------------------------------------------------------------------------


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(302)
        self.send_header("Location", "http://example.invalid/elsewhere")
        self.end_headers()

    def log_message(self, *args):
        pass  # keep test output quiet


def test_http_probe_no_redirect_returns_3xx():
    server = http.server.HTTPServer(("127.0.0.1", 0), _RedirectHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = pp._http_probe(f"http://127.0.0.1:{port}/", None)
        assert result["status_code"] == 302
        assert result["caption"] == "unauthenticated; may not reflect authenticated latency"
    finally:
        server.shutdown()
        thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Network probe error handling (local loopback only, never a real network call)
# ---------------------------------------------------------------------------


def test_network_probe_tls_failure_returns_partial_doc():
    """A handshake failure (here: the peer isn't speaking TLS at all, the
    same shape of failure an intercepting proxy with an untrusted cert would
    produce) must not discard the dns/tcp timings already measured, and must
    not raise -- it returns a partial document carrying a tls_error field
    instead."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def _accept_and_close():
        conn, _ = server.accept()
        conn.close()  # never speaks TLS -- the client handshake fails

    thread = threading.Thread(target=_accept_and_close, daemon=True)
    thread.start()
    try:
        result = pp._network_probe(f"https://127.0.0.1:{port}")
        assert isinstance(result.get("tls_error"), str) and result["tls_error"]
        assert result["timings_ms"].get("dns_ms") is not None
        assert result["timings_ms"].get("tcp_connect_ms") is not None
        assert "tls_handshake_ms" not in result["timings_ms"]
        assert "http" not in result
        assert "tls" not in result
    finally:
        server.close()
        thread.join(timeout=2)


def test_mount_info_darwin_resolves_macos_firmlinks(monkeypatch):
    """/Users is a firmlink onto the Data volume, and Path.resolve() does not
    follow it. A naive longest-prefix match lands on "/", the sealed
    read-only system volume, and reports a writable home as read-only."""
    mount_output = (
        "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
        "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, root data)\n"
    )

    class _Proc:
        stdout = mount_output

    monkeypatch.setattr(pp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(pp.subprocess, "run", lambda *a, **k: _Proc())
    # Only the Data-volume view of the home path exists on disk.
    monkeypatch.setattr(
        pp.os.path,
        "exists",
        lambda p: p == "/System/Volumes/Data/Users/someone/.claude",
    )

    info = pp._mount_info_darwin("/Users/someone/.claude")
    assert info["fstype"] == "apfs"
    assert "root data" in info["options"]
    assert "read-only" not in info["options"]


def test_mount_info_darwin_leaves_system_paths_on_the_system_volume(monkeypatch):
    mount_output = (
        "/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)\n"
        "/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, root data)\n"
    )

    class _Proc:
        stdout = mount_output

    monkeypatch.setattr(pp.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(pp.subprocess, "run", lambda *a, **k: _Proc())
    # No Data-volume counterpart for a genuine system path.
    monkeypatch.setattr(pp.os.path, "exists", lambda p: False)

    info = pp._mount_info_darwin("/usr/bin")
    assert "read-only" in info["options"]
