# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ipykernel.ipkernel import IPythonKernel

from .codegen import (
    CHATBOOK_MSG_TYPE,
    ChatbookCodegenError,
    resolve_executable_source,
)
from .danger import merge_danger_scans, scan_generated_python
from .execution import parse_execution_mode, should_execute_generated
from .nbi_client import NBIClient, NBIClientError


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
    banner = "Chatbook — natural-language cells, Python via Notebook Intelligence"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._nbi = NBIClient()

    def execute_request(self, stream, ident, parent):
        try:
            self.set_parent(ident, parent)
        except TypeError:
            self.set_parent(ident, parent, "shell")
        content = dict(parent.get("content") or {})
        prompt = content.get("code") or ""
        metadata = parent.get("metadata") or {}
        chatbook_meta = metadata.get("nbi_chatbook") or {}
        cell_id = chatbook_meta.get("cellId") or metadata.get("cellId")

        if is_python_execute(chatbook_meta):
            python = chatbook_meta.get("pythonSource")
            if isinstance(python, str) and python:
                content["code"] = python
                parent = dict(parent)
                parent["content"] = content
            return super().execute_request(stream, ident, parent)

        try:
            generated, info = resolve_executable_source(
                prompt, chatbook_meta, self._generate
            )
        except (ChatbookCodegenError, NBIClientError) as exc:
            content["code"] = _raise_runtime(str(exc))
            parent = dict(parent)
            parent["content"] = content
            return super().execute_request(stream, ident, parent)

        scan = scan_generated_python(generated)
        if chatbook_meta.get("llmDangerScan") and scan.get("level") != "risky":
            scan = merge_danger_scans(
                scan,
                self._llm_danger_scan(generated, chatbook_meta),
            )

        payload = {
            "cellId": cell_id,
            "generatedCode": generated,
            "promptHash": info.get("promptHash"),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "cacheHit": bool(info.get("cacheHit")),
            "dangerLevel": scan.get("level"),
            "dangerReasons": list(scan.get("reasons") or []),
        }
        context_hash = chatbook_meta.get("contextHash")
        if context_hash:
            payload["contextHash"] = context_hash
        self._publish_chatbook_code(parent, payload)

        policy = parse_execution_mode(chatbook_meta.get("executionPolicy"))
        if generated and should_execute_generated(policy, scan.get("level") or "risky"):
            content["code"] = generated
            parent = dict(parent)
            parent["content"] = content
            return super().execute_request(stream, ident, parent)
        return self._reply_ok_without_execute(stream, ident, parent)

    def _generate(self, prompt: str, chatbook_meta: dict) -> dict[str, Any]:
        context = chatbook_meta.get("notebookContext")
        rec = self._nbi.generate(
            prompt,
            generate_url=str(chatbook_meta.get("generateUrl") or ""),
            notebook_context=context if isinstance(context, dict) else None,
            notebook_path=str(chatbook_meta.get("notebookPath") or ""),
            cell_id=str(chatbook_meta.get("cellId") or ""),
            prompt_hash=str(chatbook_meta.get("promptHash") or ""),
            context_hash=str(chatbook_meta.get("contextHash") or ""),
        )
        return {
            "generatedCode": rec.get("generatedCode") or rec.get("output") or ""
        }

    def _llm_danger_scan(self, code: str, chatbook_meta: dict) -> dict[str, Any]:
        try:
            return self._nbi.danger_scan(
                code,
                generate_url=str(chatbook_meta.get("generateUrl") or ""),
            )
        except NBIClientError as exc:
            return {
                "level": "risky",
                "reasons": [f"Danger classifier failed: {exc}"],
            }

    def _publish_chatbook_code(self, parent, payload: dict) -> None:
        self.send_response(self.iopub_socket, CHATBOOK_MSG_TYPE, payload)

    def _reply_ok_without_execute(self, stream, ident, parent):
        """Complete execute_request without running generated Python."""
        publish_status = getattr(self, "_publish_status", None)
        if callable(publish_status):
            try:
                publish_status("busy", parent)
            except TypeError:
                publish_status("busy")
        reply_content = {
            "status": "ok",
            "execution_count": self.execution_count,
            "user_expressions": {},
            "payload": [],
        }
        self.session.send(
            stream, "execute_reply", reply_content, parent, ident=ident
        )
        if callable(publish_status):
            try:
                publish_status("idle", parent)
            except TypeError:
                publish_status("idle")


def _raise_runtime(message: str) -> str:
    return f"raise RuntimeError({message!r})"


def is_python_execute(chatbook_meta: dict | None) -> bool:
    return bool(chatbook_meta and chatbook_meta.get("executeMode") == "python")
