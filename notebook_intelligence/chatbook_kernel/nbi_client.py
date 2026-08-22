# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""HTTP client for Chatbook codegen against the NBI Jupyter server."""

from __future__ import annotations

import glob
import json
import os
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jupyter_core.paths import jupyter_runtime_dir


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
    ) -> dict:
        url = resolve_generate_url(generate_url)
        token = jupyter_api_token()
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"token {token}"
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
        req = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NBIClientError(
                f"Notebook Intelligence generate failed: {exc.code} {detail.strip()}"
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


def jupyter_api_token() -> str:
    for key in ("JUPYTERHUB_API_TOKEN", "JPY_API_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    runtime = _latest_jupyter_server_runtime()
    if runtime:
        token = runtime.get("token") or ""
        if isinstance(token, str):
            return token.strip()
    return ""


def resolve_generate_url(generate_url: str = "") -> str:
    candidate = (generate_url or "").strip()
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return candidate
    runtime = _latest_jupyter_server_runtime()
    base = ""
    if runtime:
        base = str(runtime.get("url") or "").rstrip("/")
    if candidate.startswith("/"):
        if not base:
            raise NBIClientError(
                "Cannot resolve Notebook Intelligence URL (no Jupyter server runtime)"
            )
        # base includes the Jupyter origin; candidate is a site-absolute path
        # that already has any JupyterHub prefix.
        from urllib.parse import urlparse

        parsed = urlparse(base + "/")
        return f"{parsed.scheme}://{parsed.netloc}{candidate}"
    if not base:
        raise NBIClientError(
            "Cannot reach Notebook Intelligence: pass generateUrl or run from JupyterLab"
        )
    return base + "/notebook-intelligence/chatbook/generate"


def _latest_jupyter_server_runtime() -> Optional[dict]:
    pattern = os.path.join(jupyter_runtime_dir(), "jpserver-*.json")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    for path in files:
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("url"):
            return data
    return None
