# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json

import pytest
from jupyter_client.kernelspec import NoSuchKernel

from notebook_intelligence.extension import (
    CHATBOOK_DISABLED_MESSAGE,
    ChatbookGenerateHandler,
    ChatbookMentionsHandler,
    GetCapabilitiesHandler,
    _finish_if_chatbook_disabled,
    _hide_chatbook_kernelspec,
)


def test_chatbook_enabled_defaults_on():
    assert GetCapabilitiesHandler.chatbook_enabled is True
    assert ChatbookGenerateHandler.chatbook_enabled is True
    assert ChatbookMentionsHandler.chatbook_enabled is True


def test_finish_if_chatbook_disabled_is_noop_when_enabled():
    handler = _DummyHandler(chatbook_enabled=True)
    assert _finish_if_chatbook_disabled(handler) is False
    assert handler.status is None
    assert handler.body is None


def test_finish_if_chatbook_disabled_writes_403():
    handler = _DummyHandler(chatbook_enabled=False)
    assert _finish_if_chatbook_disabled(handler) is True
    assert handler.status == 403
    assert json.loads(handler.body) == {"error": CHATBOOK_DISABLED_MESSAGE}


def test_hide_chatbook_kernelspec_drops_chatbook_and_is_idempotent():
    manager = _FakeKernelSpecManager()
    _hide_chatbook_kernelspec(manager)
    assert "chatbook" not in manager.find_kernel_specs()
    assert "python3" in manager.find_kernel_specs()
    assert "chatbook" not in manager.get_all_specs()
    with pytest.raises(NoSuchKernel):
        manager.get_kernel_spec("chatbook")
    assert manager.get_kernel_spec("python3") == "python3-spec"

    _hide_chatbook_kernelspec(manager)
    assert "chatbook" not in manager.find_kernel_specs()


def test_nbi_enable_chatbook_env_overrides_traitlet(monkeypatch):
    from notebook_intelligence.extension import _resolve_bool_with_env

    monkeypatch.delenv("NBI_ENABLE_CHATBOOK", raising=False)
    assert _resolve_bool_with_env("NBI_ENABLE_CHATBOOK", True) is True

    monkeypatch.setenv("NBI_ENABLE_CHATBOOK", "false")
    assert _resolve_bool_with_env("NBI_ENABLE_CHATBOOK", True) is False

    monkeypatch.setenv("NBI_ENABLE_CHATBOOK", "true")
    assert _resolve_bool_with_env("NBI_ENABLE_CHATBOOK", False) is True


def test_hide_chatbook_kernelspec_accepts_none():
    _hide_chatbook_kernelspec(None)


class _DummyHandler:
    def __init__(self, chatbook_enabled: bool):
        self.chatbook_enabled = chatbook_enabled
        self.status = None
        self.body = None

    def set_status(self, status):
        self.status = status

    def finish(self, body):
        self.body = body


class _FakeKernelSpecManager:
    def find_kernel_specs(self):
        return {"python3": "/python3", "chatbook": "/chatbook"}

    def get_kernel_spec(self, name):
        return f"{name}-spec"

    def get_all_specs(self):
        return {"python3": {}, "chatbook": {}}
