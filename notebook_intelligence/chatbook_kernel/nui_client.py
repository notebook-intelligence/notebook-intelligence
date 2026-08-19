# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Minimal nui HTTP client for Chatbook cell codegen (no Jupyter auth)."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

DEFAULT_NUI_URL = "http://127.0.0.1:8080"


class NuiClientError(Exception):
    pass


class NuiClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 30.0):
        self.base_url = (base_url or _configured_nui_url()).rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        url = self.base_url + path
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout if timeout is not None else self.timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NuiClientError(f"{method} {path} failed: {exc.code} {detail.strip()}") from exc
        except urllib.error.URLError as exc:
            raise NuiClientError(
                f"nui is not reachable at {self.base_url}: {exc.reason}"
            ) from exc

    def health(self) -> None:
        url = self.base_url + "/health"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    raise NuiClientError(f"health check failed: {resp.status}")
        except urllib.error.URLError as exc:
            raise NuiClientError(
                f"nui is not reachable at {self.base_url}: {exc.reason}"
            ) from exc

    def get_settings(self) -> dict:
        return self._request("GET", "/api/settings") or {}

    def list_agents(self) -> list:
        data = self._request("GET", "/api/agent-types")
        return data if isinstance(data, list) else []

    def resolve_agent_type(self, preferred: str = "") -> str:
        preferred = (preferred or "").strip()
        agents = self.list_agents()
        selectable = [a for a in agents if isinstance(a, dict) and a.get("available")]
        if preferred:
            for a in selectable:
                if a.get("id") == preferred:
                    return preferred
            # Allow an explicit id even if availability is unknown.
            for a in agents:
                if isinstance(a, dict) and a.get("id") == preferred:
                    return preferred
            return preferred
        settings = self.get_settings()
        default_id = (settings.get("defaultAgentType") or "").strip()
        if default_id:
            for a in selectable:
                if a.get("id") == default_id:
                    return default_id
        for agent_id in (
            "chatbook-cell",
            "claude-code",
            "pi",
            "codex",
            "opencode",
            "anthropic",
            "openai",
            "gemini",
            "openrouter",
            "ollama",
        ):
            for a in selectable:
                if a.get("id") == agent_id:
                    return agent_id
        if selectable:
            return selectable[0]["id"]
        raise NuiClientError("no available nui agent types")

    def create_session(
        self, name: str, agent_type: str, working_dir: str = ""
    ) -> dict:
        body: dict[str, Any] = {"name": name, "agentType": agent_type}
        if working_dir:
            body["workingDir"] = working_dir
        return self._request("POST", "/api/sessions", body)

    def start_run(self, session_id: str, message: str) -> dict:
        return self._request(
            "POST",
            f"/api/sessions/{session_id}/runs",
            {"message": message},
        )

    def get_run(self, session_id: str, run_id: str) -> dict:
        return self._request("GET", f"/api/sessions/{session_id}/runs/{run_id}")

    def stop_run(self, session_id: str, run_id: str = "") -> None:
        path = f"/api/sessions/{session_id}/stop"
        if run_id:
            path += f"?runId={run_id}"
        try:
            self._request("POST", path, {})
        except NuiClientError:
            # Interrupt is best-effort; the IPython interrupt still fires.
            pass

    def wait_run(
        self,
        session_id: str,
        run_id: str,
        poll_interval: float = 0.5,
        should_stop: Optional[Callable[[], bool]] = None,
        timeout: float = 600.0,
    ) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            if should_stop and should_stop():
                self.stop_run(session_id, run_id)
                raise NuiClientError("code generation was interrupted")
            rec = self.get_run(session_id, run_id)
            status = rec.get("status")
            if status in {"completed", "failed", "cancelled"}:
                return rec
            if time.monotonic() > deadline:
                self.stop_run(session_id, run_id)
                raise NuiClientError("code generation timed out")
            time.sleep(poll_interval)


def _nbi_config_paths() -> list[str]:
    return [
        os.path.join(os.path.expanduser("~"), ".jupyter", "nbi", "config.json"),
        os.path.join(sys.prefix, "share", "jupyter", "nbi", "config.json"),
    ]


def _read_nbi_json() -> dict:
    """Read Chatbook keys from NBI config files without constructing NBIConfig.

    NBIConfig logs deprecation warnings to stderr. During execute_request
    IPython captures stderr into the cell output, so the kernel must not
    instantiate NBIConfig on the execute path.
    """
    merged: dict = {}
    for path in reversed(_nbi_config_paths()):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def _configured_nui_url() -> str:
    env = os.environ.get("NUI_URL", "").strip()
    if env:
        return env
    url = _read_nbi_json().get("chatbook_nui_url", "")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return DEFAULT_NUI_URL


def configured_agent_type() -> str:
    env = os.environ.get("NBI_CHATBOOK_AGENT_TYPE", "").strip()
    if env:
        return env
    value = _read_nbi_json().get("chatbook_agent_type", "")
    return value.strip() if isinstance(value, str) else ""
