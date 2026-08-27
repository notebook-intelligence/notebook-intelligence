# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""HTTP client for Chatbook codegen against the NBI Jupyter server."""

from __future__ import annotations

import glob
import json
import logging
import os
import re
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from jupyter_core.paths import jupyter_runtime_dir

log = logging.getLogger(__name__)


class NBIClientError(Exception):
    pass


class NBIClient:
    def generate(
        self,
        prompt: str,
        generate_url: str = "",
        timeout: float = 600.0,
        notebook_context: Optional[dict] = None,
        notebook_path: str = "",
        cell_id: str = "",
        prompt_hash: str = "",
        context_hash: str = "",
        language: str = "python",
        kernel_name: str = "",
        display_name: str = "",
    ) -> dict:
        payload: dict = {"prompt": prompt}
        if notebook_context:
            payload["notebookContext"] = notebook_context
        if notebook_path:
            payload["notebookPath"] = notebook_path
        if cell_id:
            payload["cellId"] = cell_id
        if prompt_hash:
            payload["promptHash"] = prompt_hash
        if context_hash:
            payload["contextHash"] = context_hash
        if language:
            payload["language"] = language
        if kernel_name:
            payload["kernelName"] = kernel_name
        if display_name:
            payload["kernelDisplayName"] = display_name
        return self._post(payload, generate_url=generate_url, timeout=timeout)

    def danger_scan(
        self,
        code: str,
        generate_url: str = "",
        timeout: float = 20.0,
        language: str = "python",
    ) -> dict:
        rec = self._post(
            {
                "operation": "danger_scan",
                "code": code,
                "language": language,
            },
            generate_url=generate_url,
            timeout=timeout,
        )
        level = rec.get("level") or rec.get("dangerLevel") or "risky"
        reasons = rec.get("reasons") or rec.get("dangerReasons") or []
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        if level not in {"clean", "risky"}:
            level = "risky"
            reasons = list(reasons) + ["Danger classifier returned an invalid level"]
        return {
            "level": level,
            "reasons": [str(item) for item in reasons if str(item).strip()],
        }

    def _post(self, payload: dict, generate_url: str = "", timeout: float = 600.0) -> dict:
        url = (generate_url or "").strip() or resolve_generate_url()
        token = jupyter_api_token()
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            # Token-authenticated requests skip Jupyter's XSRF check.
            headers["Authorization"] = f"token {token}"
        else:
            _apply_xsrf_headers(headers, url)
        raw = b""
        for attempt in (0, 1):
            try:
                raw = _send(url, body, headers, timeout)
                break
            except HTTPError as exc:
                stale_xsrf = (
                    attempt == 0
                    and exc.code == 403
                    and _apply_xsrf_headers(headers, url, refresh=True)
                )
                if stale_xsrf:
                    continue
                detail = exc.read().decode("utf-8", errors="replace")
                raise NBIClientError(
                    f"Notebook Intelligence generate failed at {url}: "
                    f"{exc.code} {detail.strip()}"
                ) from exc
            except URLError as exc:
                raise NBIClientError(
                    f"Notebook Intelligence is not reachable at {url}: {exc.reason}"
                ) from exc
        if not raw:
            raise NBIClientError("Notebook Intelligence returned an empty response")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise NBIClientError("Notebook Intelligence returned an invalid response")
        if data.get("error"):
            raise NBIClientError(str(data["error"]))
        return data


def _send(url: str, body: bytes, headers: dict, timeout: float) -> bytes:
    req = Request(url, data=body, method="POST", headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


_xsrf_cache: dict[str, str] = {}


def _apply_xsrf_headers(headers: dict, url: str, refresh: bool = False) -> bool:
    """Attach an ``_xsrf`` cookie and matching header. True when they changed.

    A server started without a token (``--ServerApp.token=''``) authenticates
    the kernel as an anonymous user but still enforces XSRF on POST, and the
    kernel has no browser cookie jar to draw on. Returning False means no
    token could be minted, so there is nothing new to retry with.
    """
    token = _fetch_xsrf_token(url, refresh=refresh)
    if not token or token == headers.get("X-XSRFToken"):
        return False
    headers["X-XSRFToken"] = token
    headers["Cookie"] = f"_xsrf={token}"
    return True


def _fetch_xsrf_token(url: str, refresh: bool = False) -> str:
    base = _server_base_url(url)
    if not refresh and base in _xsrf_cache:
        return _xsrf_cache[base]
    # Any page handler mints the cookie; /login is the cheapest, and it still
    # sets the cookie on the 404 it returns when logins are disabled.
    request = Request(base + "login", method="GET")
    try:
        with urlopen(request, timeout=10.0) as resp:
            cookies = resp.headers.get_all("Set-Cookie") or []
    except HTTPError as exc:
        cookies = exc.headers.get_all("Set-Cookie") or []
    except URLError:
        return ""
    for cookie in cookies:
        match = re.match(r"\s*_xsrf=([^;]+)", cookie)
        if match:
            token = match.group(1).strip()
            _xsrf_cache[base] = token
            return token
    return ""


def _server_base_url(generate_url: str) -> str:
    """Jupyter root of a generate URL, keeping any JupyterHub prefix."""
    parsed = urlparse(generate_url)
    suffix = "/notebook-intelligence/chatbook/generate"
    path = parsed.path
    prefix = path[: -len(suffix)] if path.endswith(suffix) else ""
    return f"{parsed.scheme}://{parsed.netloc}{prefix}/"


def jupyter_api_token() -> str:
    for key in ("JUPYTERHUB_API_TOKEN", "JPY_API_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    runtime = _jupyter_server_runtime()
    if runtime:
        token = runtime.get("token") or ""
        if isinstance(token, str):
            return token.strip()
    return ""


def resolve_generate_url() -> str:
    runtime = _jupyter_server_runtime()
    base = str((runtime or {}).get("url") or "").rstrip("/")
    if not base:
        raise NBIClientError(
            "Cannot reach Notebook Intelligence: no Jupyter server runtime"
        )
    return base + "/notebook-intelligence/chatbook/generate"


def _read_runtime_file(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and data.get("url"):
        return data
    return None


def _jupyter_server_runtime() -> Optional[dict]:
    runtime_dir = jupyter_runtime_dir()
    parent_pid = os.environ.get("JPY_PARENT_PID", "").strip()
    if parent_pid:
        if not parent_pid.isdigit():
            raise NBIClientError(
                "Cannot identify the parent Jupyter server: "
                f"invalid JPY_PARENT_PID {parent_pid!r}"
            )
        path = os.path.join(runtime_dir, f"jpserver-{parent_pid}.json")
        data = _read_runtime_file(path)
        if data:
            return data
        # Silently falling back can send the prompt and notebook context to a
        # different server owned by the same user.
        raise NBIClientError(
            "Cannot identify the parent Jupyter server: "
            f"runtime file {path!r} is missing or invalid"
        )
    log.warning(
        "JPY_PARENT_PID is not set; falling back to the newest Jupyter "
        "server runtime file"
    )
    return _latest_jupyter_server_runtime()


def _latest_jupyter_server_runtime() -> Optional[dict]:
    pattern = os.path.join(jupyter_runtime_dir(), "jpserver-*.json")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for path in files:
        data = _read_runtime_file(path)
        if data:
            return data
    return None
