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

        payload = {
            "cellId": cell_id,
            "generatedCode": generated,
            "promptHash": info.get("promptHash"),
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "cacheHit": bool(info.get("cacheHit")),
        }
        context_hash = chatbook_meta.get("contextHash")
        if context_hash:
            payload["contextHash"] = context_hash
        self._publish_chatbook_code(parent, payload)
        content["code"] = generated
        parent = dict(parent)
        parent["content"] = content
        return super().execute_request(stream, ident, parent)

    def _generate(self, prompt: str, chatbook_meta: dict) -> dict[str, Any]:
        context = chatbook_meta.get("notebookContext")
        rec = self._nbi.generate(
            prompt,
            generate_url=str(chatbook_meta.get("generateUrl") or ""),
            notebook_context=context if isinstance(context, dict) else None,
        )
        return {
            "generatedCode": rec.get("generatedCode") or rec.get("output") or ""
        }

    def _publish_chatbook_code(self, parent, payload: dict) -> None:
        self.send_response(self.iopub_socket, CHATBOOK_MSG_TYPE, payload)


def _raise_runtime(message: str) -> str:
    return f"raise RuntimeError({message!r})"


def is_python_execute(chatbook_meta: dict | None) -> bool:
    return bool(chatbook_meta and chatbook_meta.get("executeMode") == "python")
