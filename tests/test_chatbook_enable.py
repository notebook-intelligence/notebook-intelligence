# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
from types import SimpleNamespace

import pytest
from jupyter_client.kernelspec import NoSuchKernel

from notebook_intelligence.chatbook_kernel.backend import KERNEL_SPEC_MANAGER_ENV
from notebook_intelligence.extension import (
    CHATBOOK_DISABLED_MESSAGE,
    FEATURE_POLICY_DEFAULTS,
    FEATURE_POLICY_SPEC,
    ChatbookGenerateHandler,
    ChatbookMentionsHandler,
    NotebookIntelligence,
    _finish_if_chatbook_disabled,
    _hide_chatbook_kernelspec,
    _required_chatbook_generate_field,
    _resolve_policy_with_env,
    _set_chatbook_kernelspec_execution_cap,
)
from notebook_intelligence.feature_flags import (
    POLICY_FORCE_OFF,
    POLICY_FORCE_ON,
    POLICY_USER_CHOICE,
    is_force_off,
)
from tests.conftest import RenamingKernelSpecManager


def test_chatbook_enabled_defaults_on():
    assert ChatbookGenerateHandler.chatbook_enabled is True
    assert ChatbookMentionsHandler.chatbook_enabled is True


def test_chatbook_policy_is_a_standard_feature_policy():
    assert ("chatbook", "NBI_CHATBOOK_POLICY", "chatbook_policy") in FEATURE_POLICY_SPEC
    assert FEATURE_POLICY_DEFAULTS["chatbook"] == POLICY_USER_CHOICE
    assert NotebookIntelligence.class_traits()["chatbook_policy"].default_value == (
        POLICY_USER_CHOICE
    )
    assert "enable_chatbook" not in NotebookIntelligence.class_traits()


@pytest.mark.parametrize(
    "env_value, gate_open",
    [
        (None, True),
        (POLICY_USER_CHOICE, True),
        (POLICY_FORCE_ON, True),
        (POLICY_FORCE_OFF, False),
    ],
)
def test_nbi_chatbook_policy_env_resolves_the_gate(monkeypatch, env_value, gate_open):
    if env_value is None:
        monkeypatch.delenv("NBI_CHATBOOK_POLICY", raising=False)
    else:
        monkeypatch.setenv("NBI_CHATBOOK_POLICY", env_value)
    policy = _resolve_policy_with_env("NBI_CHATBOOK_POLICY", POLICY_USER_CHOICE)
    assert (not is_force_off({"chatbook": policy}, "chatbook")) is gate_open


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
    assert manager.get_kernel_spec("python3").name == "python3"

    _hide_chatbook_kernelspec(manager)
    assert "chatbook" not in manager.find_kernel_specs()


def test_hide_chatbook_kernelspec_accepts_none():
    _hide_chatbook_kernelspec(None)


def test_chatbook_kernelspec_inherits_resolved_traitlet_cap():
    manager = _FakeKernelSpecManager()
    _set_chatbook_kernelspec_execution_cap(manager, "always-confirm")

    spec = manager.get_kernel_spec("chatbook")
    assert spec.env["NBI_CHATBOOK_MAX_EXECUTION_MODE"] == "always-confirm"
    assert manager.get_kernel_spec("python3").env == {}

    # Updating settings should update the existing wrapper, not wrap again.
    _set_chatbook_kernelspec_execution_cap(manager, "confirm-if-risky")
    spec = manager.get_kernel_spec("chatbook")
    assert spec.env["NBI_CHATBOOK_MAX_EXECUTION_MODE"] == "confirm-if-risky"


def test_hide_chatbook_kernelspec_drops_a_renamed_chatbook():
    # nb_conda_kernels lists Chatbook as conda-base-chatbook; force-off has to
    # hide that one too.
    manager = RenamingKernelSpecManager()
    _hide_chatbook_kernelspec(manager)
    assert list(manager.find_kernel_specs()) == ["conda-base-py"]
    assert list(manager.get_all_specs()) == ["conda-base-py"]
    with pytest.raises(NoSuchKernel):
        manager.get_kernel_spec("conda-base-chatbook")
    assert manager.get_kernel_spec("conda-base-py").name == "conda-base-py"


def test_renamed_chatbook_kernelspec_gets_the_cap_and_the_server_manager():
    manager = RenamingKernelSpecManager()
    _set_chatbook_kernelspec_execution_cap(manager, "always-confirm")

    env = manager.get_kernel_spec("conda-base-chatbook").env
    assert env["NBI_CHATBOOK_MAX_EXECUTION_MODE"] == "always-confirm"
    description = json.loads(env[KERNEL_SPEC_MANAGER_ENV])
    assert description["class"].endswith(".RenamingKernelSpecManager")
    assert manager.get_kernel_spec("conda-base-py").env == {}


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
        return SimpleNamespace(name=name, env={})

    def get_all_specs(self):
        return {"python3": {}, "chatbook": {}}


@pytest.mark.parametrize(
    'body, field',
    [
        ({}, 'prompt'),
        ({'prompt': None}, 'prompt'),
        ({'operation': 'summarize'}, 'code'),
        ({'operation': 'danger_scan'}, 'code'),
        ({'prompt': ''}, 'prompt'),
        ({'operation': 'summarize', 'code': '  '}, 'code'),
    ],
)
def test_required_chatbook_generate_field_rejects_missing(body, field):
    name, value = _required_chatbook_generate_field(body)
    assert name == field
    assert value == ''


def test_required_chatbook_generate_field_accepts_present_values():
    assert _required_chatbook_generate_field({'prompt': 'plot'}) == (
        'prompt',
        'plot',
    )
    assert _required_chatbook_generate_field(
        {'operation': 'summarize', 'code': 'x = 1'}
    ) == ('code', 'x = 1')
