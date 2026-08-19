# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from ipykernel.ipkernel import IPythonKernel

from .codegen import (
    CHATBOOK_MSG_TYPE,
    ChatbookCodegenError,
    build_run_message,
    extract_python_cell,
    resolve_executable_source,
    stub_enabled,
)
from .nui_client import NuiClient, NuiClientError, configured_agent_type


class ChatbookKernel(IPythonKernel):
    implementation = "chatbook"
    implementation_version = "1.0"
    language = "chatbook"
    language_version = "1.0"
    language_info = {
        "name": "chatbook",
        "mimetype": "text/x-chatbook",
        "file_extension": ".chatbook",
        "pygments_lexer": "text",
    }
    banner = "Chatbook — natural-language cells, hidden Python via nui"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nui = NuiClient()
        self._nui_session_id: Optional[str] = None
        self._current_run_id: Optional[str] = None
        self._stop = threading.Event()

    def interrupt_request(self, stream, ident, parent):
        self._stop.set()
        session_id = self._nui_session_id
        run_id = self._current_run_id
        if session_id:
            try:
                self._nui.stop_run(session_id, run_id or "")
            except Exception:
                pass
        handler = getattr(super(), "interrupt_request", None)
        if handler is not None:
            return handler(stream, ident, parent)
        return None

    def execute_request(self, stream, ident, parent):
        self._stop.clear()
        try:
            self.set_parent(ident, parent)
        except TypeError:
            self.set_parent(ident, parent, "shell")
        content = dict(parent.get("content") or {})
        prompt = content.get("code") or ""
        metadata = parent.get("metadata") or {}
        chatbook_meta = metadata.get("nbi_chatbook") or {}
        cell_id = chatbook_meta.get("cellId") or metadata.get("cellId")

        try:
            generated, info = resolve_executable_source(
                prompt, chatbook_meta, self._generate
            )
        except (ChatbookCodegenError, NuiClientError) as exc:
            content["code"] = _raise_runtime(str(exc))
            parent = dict(parent)
            parent["content"] = content
            return super().execute_request(stream, ident, parent)

        self._publish_chatbook_code(
            parent,
            {
                "cellId": cell_id,
                "generatedCode": generated,
                "promptHash": info.get("promptHash"),
                "nuiSessionId": info.get("nuiSessionId") or self._nui_session_id,
                "nuiRunId": info.get("nuiRunId"),
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "cacheHit": bool(info.get("cacheHit")),
            },
        )
        content["code"] = generated
        parent = dict(parent)
        parent["content"] = content
        return super().execute_request(stream, ident, parent)

    def _generate(self, prompt: str, chatbook_meta: dict) -> dict[str, Any]:
        if stub_enabled():
            # resolve_executable_source handles stubs before calling generate.
            return {"generatedCode": ""}

        self.log.info("Chatbook: generating code with nui")
        working_dir = (
            chatbook_meta.get("workingDir")
            or os.environ.get("JPY_SESSION_NAME")
            or os.getcwd()
        )
        if isinstance(working_dir, str) and working_dir.endswith(".ipynb"):
            working_dir = os.path.dirname(working_dir) or os.getcwd()

        session_id = chatbook_meta.get("nuiSessionId") or self._nui_session_id
        agent_type = configured_agent_type()
        try:
            agent_type = self._nui.resolve_agent_type(agent_type)
        except NuiClientError:
            # Fall through; create_session will raise a clearer error.
            pass

        if not session_id:
            session = self._nui.create_session(
                name="chatbook",
                agent_type=agent_type,
                working_dir=str(working_dir),
            )
            session_id = session.get("id")
            if not session_id:
                raise NuiClientError("nui create session did not return an id")
        self._nui_session_id = session_id

        started = self._nui.start_run(session_id, build_run_message(prompt))
        run_id = started.get("runId")
        if not run_id:
            raise NuiClientError("nui start run did not return a runId")
        self._current_run_id = run_id
        try:
            rec = self._nui.wait_run(
                session_id,
                run_id,
                should_stop=self._stop.is_set,
            )
        finally:
            self._current_run_id = None

        status = rec.get("status")
        if status == "cancelled":
            raise NuiClientError("code generation was interrupted")
        if status == "failed":
            raise NuiClientError(rec.get("error") or "nui run failed")
        output = rec.get("output") or ""
        code = extract_python_cell(output)
        return {
            "generatedCode": code,
            "nuiSessionId": session_id,
            "nuiRunId": run_id,
            "output": output,
        }

    def _publish_chatbook_code(self, parent, payload: dict) -> None:
        self.send_response(self.iopub_socket, CHATBOOK_MSG_TYPE, payload)


def _raise_runtime(message: str) -> str:
    return f"raise RuntimeError({message!r})"
