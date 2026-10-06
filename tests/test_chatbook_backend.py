# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
from types import SimpleNamespace

import pytest
from jupyter_client.session import Session

from jupyter_client.kernelspec import KernelSpecManager
from traitlets.config import Config

from notebook_intelligence.chatbook_kernel import backend as backend_module
from notebook_intelligence.chatbook_kernel.backend import (
    KERNEL_SPEC_MANAGER_ENV,
    ChatbookBackend,
    describe_kernel_spec_manager,
    is_chatbook_spec,
    kernel_spec_manager,
    list_backend_kernels,
    load_kernel_specs,
    resolve_backend_kernel,
    set_kernel_spec_manager,
)
from notebook_intelligence.chatbook_kernel.codegen import (
    cell_codegen_instructions,
    extract_code_cell,
)
from notebook_intelligence.chatbook_kernel.danger import scan_generated_code
from notebook_intelligence.chatbook_kernel.nbi_client import NBIClientError
from notebook_intelligence.chatbook_kernel.kernel import ChatbookKernel, is_code_execute
from tests.conftest import RenamingKernelSpecManager


SPECS = {
    'chatbook': {
        'spec': {'language': 'chatbook', 'display_name': 'Chatbook'}
    },
    'python3': {
        'spec': {'language': 'python', 'display_name': 'Python 3'}
    },
    'ir': {'spec': {'language': 'R', 'display_name': 'R'}},
}


def test_list_backend_kernels_excludes_chatbook():
    names = [item['name'] for item in list_backend_kernels(SPECS)]
    assert names == ['ir', 'python3']


def test_resolve_backend_kernel_prefers_named_spec():
    chosen = resolve_backend_kernel('ir', SPECS)
    assert chosen['name'] == 'ir'
    assert chosen['language'] == 'R'


def test_resolve_backend_kernel_skips_chatbook_and_defaults_to_python3():
    chosen = resolve_backend_kernel('chatbook', SPECS)
    assert chosen['name'] == 'python3'


def test_resolve_backend_kernel_missing_name_raises():
    with pytest.raises(RuntimeError, match='does-not-exist'):
        resolve_backend_kernel('does-not-exist', SPECS)


# How nb_conda_kernels lists the same kernels: Chatbook keeps its language
# under a new name, and there is no `python3`.
CONDA_SPECS = {
    'conda-base-chatbook': {
        'spec': {'language': 'chatbook', 'display_name': 'Chatbook [conda env:base] *'}
    },
    'conda-base-py': {
        'spec': {'language': 'python', 'display_name': 'Python [conda env:base] *'}
    },
    'conda-base-ir': {'spec': {'language': 'R', 'display_name': 'R [conda env:base] *'}},
}


@pytest.fixture
def fresh_kernel_spec_manager(monkeypatch):
    """Forget the process-wide kernelspec manager and any server description."""
    monkeypatch.setattr(backend_module, '_kernel_spec_manager', None)
    monkeypatch.delenv(KERNEL_SPEC_MANAGER_ENV, raising=False)


def test_is_chatbook_spec_goes_by_language_not_name():
    assert is_chatbook_spec(CONDA_SPECS['conda-base-chatbook'])
    assert is_chatbook_spec(SimpleNamespace(language='Chatbook'))
    assert not is_chatbook_spec(CONDA_SPECS['conda-base-py'])
    assert not is_chatbook_spec(None)


def test_list_backend_kernels_excludes_a_renamed_chatbook():
    names = [item['name'] for item in list_backend_kernels(CONDA_SPECS)]
    assert names == ['conda-base-ir', 'conda-base-py']


def test_resolve_backend_kernel_accepts_renamed_kernels():
    assert resolve_backend_kernel('conda-base-py', CONDA_SPECS)['name'] == 'conda-base-py'
    # With no `python3`, the default is the first Python kernel.
    assert resolve_backend_kernel('', CONDA_SPECS)['name'] == 'conda-base-py'
    # A renamed Chatbook cannot be its own backend any more than `chatbook` can.
    assert (
        resolve_backend_kernel('conda-base-chatbook', CONDA_SPECS)['name']
        == 'conda-base-py'
    )


def test_kernel_lists_backends_with_the_servers_kernel_spec_manager(
    monkeypatch, fresh_kernel_spec_manager
):
    allowed = {'conda-env-tools-chatbook', 'conda-env-tools-py'}
    # The server describes its manager, config included, in the kernel's env ...
    server_manager = RenamingKernelSpecManager(
        config=Config(
            {
                'RenamingKernelSpecManager': {'prefix': 'conda-env-tools-'},
                'KernelSpecManager': {'allowed_kernelspecs': allowed},
            }
        )
    )
    monkeypatch.setenv(
        KERNEL_SPEC_MANAGER_ENV, describe_kernel_spec_manager(server_manager)
    )

    # ... so the kernel resolves the names Settings and the kernel picker show.
    manager = kernel_spec_manager()
    assert type(manager) is RenamingKernelSpecManager
    assert manager.prefix == 'conda-env-tools-'
    assert manager.allowed_kernelspecs == allowed
    specs = load_kernel_specs()
    assert sorted(specs) == ['conda-env-tools-chatbook', 'conda-env-tools-py']
    assert resolve_backend_kernel('conda-env-tools-py', specs)['language'] == 'python'


def test_describe_kernel_spec_manager_keeps_only_its_own_json_config():
    manager = RenamingKernelSpecManager(
        config=Config(
            {
                'RenamingKernelSpecManager': {'prefix': 'p-', 'not_json': object()},
                'ServerApp': {'port': 8888},
            }
        )
    )
    description = json.loads(describe_kernel_spec_manager(manager))
    assert description['class'] == (
        f'{RenamingKernelSpecManager.__module__}.RenamingKernelSpecManager'
    )
    assert description['config'] == {'RenamingKernelSpecManager': {'prefix': 'p-'}}


def test_kernel_spec_manager_defaults_without_a_server_description(
    fresh_kernel_spec_manager,
):
    assert type(kernel_spec_manager()) is KernelSpecManager


def test_kernel_spec_manager_falls_back_on_a_bad_description(
    monkeypatch, fresh_kernel_spec_manager
):
    monkeypatch.setenv(KERNEL_SPEC_MANAGER_ENV, '{"class": "no_such_module.Manager"}')
    assert type(kernel_spec_manager()) is KernelSpecManager


def test_server_resolves_backends_with_its_own_kernel_spec_manager(
    fresh_kernel_spec_manager,
):
    server_manager = RenamingKernelSpecManager()
    set_kernel_spec_manager(server_manager)
    assert kernel_spec_manager() is server_manager
    assert resolve_backend_kernel('', load_kernel_specs())['name'] == 'conda-base-py'


def test_backend_starts_through_the_resolved_kernel_spec_manager(
    monkeypatch, fresh_kernel_spec_manager
):
    import jupyter_client

    server_manager = RenamingKernelSpecManager()
    set_kernel_spec_manager(server_manager)
    created = {}

    def kernel_manager(**kwargs):
        created.update(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(jupyter_client, 'KernelManager', kernel_manager)
    ChatbookBackend('conda-base-py').start()
    assert created == {
        'kernel_name': 'conda-base-py',
        'kernel_spec_manager': server_manager,
    }


def test_is_code_execute_only_accepts_code_mode():
    assert is_code_execute({'executeMode': 'code'})
    assert not is_code_execute({'executeMode': 'prompt'})
    assert not is_code_execute({'executeMode': 'python'})


def test_non_python_static_scan_is_risky():
    scan = scan_generated_code('plot(1)', 'R')
    assert scan['level'] == 'risky'
    assert scan['reasons']


def test_unknown_language_static_scan_is_risky():
    scan = scan_generated_code('total = 1\ntotal\n', '')
    assert scan['level'] == 'risky'
    missing = scan_generated_code('total = 1\ntotal\n', None)  # type: ignore[arg-type]
    assert missing['level'] == 'risky'


def test_r_codegen_instructions_omit_ipython_magics():
    text = cell_codegen_instructions('R')
    assert '```R' in text
    assert '%pip' not in text
    python = cell_codegen_instructions('python')
    assert '%pip' in python
    assert '```python' in python


def test_extract_code_cell_accepts_non_python_fence():
    assert extract_code_cell('```r\nplot(1)\n```') == 'plot(1)'


class _FakeClient:
    def __init__(self):
        self.executed = []
        self._iopub = []
        self._shell = []
        self.channels_stopped = False

    def execute(self, code, **kwargs):
        self.executed.append(code)
        return 'msg-1'

    def start_channels(self):
        return None

    def wait_for_ready(self, timeout=60):
        return None

    def stop_channels(self):
        self.channels_stopped = True

    def get_iopub_msg(self, timeout=0.1):
        if self._iopub:
            return self._iopub.pop(0)
        from queue import Empty

        raise Empty

    def get_shell_msg(self, timeout=0.1):
        empties = getattr(self, '_shell_empty_before_reply', 0)
        if empties:
            self._shell_empty_before_reply = empties - 1
            from queue import Empty

            raise Empty
        if self._shell:
            return self._shell.pop(0)
        from queue import Empty

        raise Empty


class _FakeManager:
    def __init__(self, kernel_name):
        self.kernel_name = kernel_name
        self.client_obj = _FakeClient()
        self.shutdown_called = False
        self.interrupted = False

    def start_kernel(self, cwd=None):
        self.cwd = cwd

    def client(self):
        return self.client_obj

    def shutdown_kernel(self, now=True):
        self.shutdown_called = True

    def interrupt_kernel(self):
        self.interrupted = True

    def is_alive(self):
        return getattr(self, 'alive', True)


class _RecordingSession(Session):
    """Session that records outgoing messages instead of writing to a socket."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sent = []

    def send(self, stream, msg_or_type, content=None, *args, **kwargs):
        self.sent.append((msg_or_type, content))
        return None


class _StubBackend:
    ready = True

    def __init__(self, reply):
        self.reply = reply
        self.relayed = []
        self.interrupted = False
        self.shell_requests = []
        self.forwarded = []

    def execute(self, code, relay):
        relay('stream', {'name': 'stdout', 'text': 'hi\n'})
        self.relayed.append(code)
        return self.reply

    def interrupt(self):
        self.interrupted = True

    def shell_request(self, msg_type, content, timeout=10.0):
        self.shell_requests.append((msg_type, content))
        return {'status': 'ok', 'matches': ['import']}

    def forward_shell(self, msg_type, content):
        self.forwarded.append((msg_type, content))


def _kernel_with_backend(reply):
    """A kernel wired to a stub backend, with no sockets or event loop."""
    kernel = ChatbookKernel()
    kernel.session = _RecordingSession()
    kernel.iopub_socket = object()
    kernel._backend = _StubBackend(reply)
    return kernel


def test_kernel_does_not_start_the_backend_before_the_event_loop_runs():
    # Starting the child kernel from start() blocks the wrapper before it can
    # answer kernel_info, so the frontend never connects.
    assert 'start' not in ChatbookKernel.__dict__


def test_kernel_reply_uses_child_execution_count_and_relays_output():
    kernel = _kernel_with_backend({'status': 'ok', 'execution_count': 7})

    kernel._execute_in_backend(None, b'ident', {'content': {}}, 'print(1)')

    assert kernel.execution_count == 7
    assert ('stream', {'name': 'stdout', 'text': 'hi\n'}) in kernel.session.sent
    reply = [item for item in kernel.session.sent if item[0] == 'execute_reply']
    assert reply[0][1]['status'] == 'ok'
    assert reply[0][1]['execution_count'] == 7
    # The base dispatcher owns busy/idle for the request.
    assert not [item for item in kernel.session.sent if item[0] == 'status']


def test_kernel_reply_forwards_child_error():
    kernel = _kernel_with_backend(
        {
            'status': 'error',
            'ename': 'ValueError',
            'evalue': 'boom',
            'traceback': ['line'],
        }
    )

    kernel._execute_in_backend(None, b'ident', {'content': {}}, 'print(1)')

    reply = [item for item in kernel.session.sent if item[0] == 'execute_reply']
    assert reply[0][1]['ename'] == 'ValueError'
    assert reply[0][1]['traceback'] == ['line']
    # The code ran and the child numbered it, so the failure keeps that
    # number; only a request that never reached the child reports none.
    assert reply[0][1]['execution_count'] == 1


def test_backend_execute_relays_iopub_and_returns_reply():
    manager = _FakeManager('python3')
    client = manager.client_obj
    client._iopub = [
        {
            'header': {'msg_type': 'stream'},
            'parent_header': {'msg_id': 'msg-1'},
            'content': {'name': 'stdout', 'text': 'hi\n'},
        },
        {
            'header': {'msg_type': 'status'},
            'parent_header': {'msg_id': 'msg-1'},
            'content': {'execution_state': 'idle'},
        },
    ]
    client._shell = [
        {
            'header': {'msg_type': 'execute_reply'},
            'parent_header': {'msg_id': 'msg-1'},
            'content': {'status': 'ok', 'execution_count': 1},
        }
    ]
    backend = ChatbookBackend(
        'python3', cwd='/tmp', manager_factory=lambda name: manager
    )
    backend.start()
    relayed = []
    reply = backend.execute('print(1)', lambda t, c: relayed.append((t, c)))
    assert client.executed == ['print(1)']
    assert relayed == [('stream', {'name': 'stdout', 'text': 'hi\n'})]
    assert reply['status'] == 'ok'
    backend.interrupt()
    backend.shutdown()
    assert manager.interrupted
    assert manager.shutdown_called


def test_backend_execute_waits_for_late_error_reply():
    manager = _FakeManager('python3')
    client = manager.client_obj
    client._iopub = [
        {
            'header': {'msg_type': 'error'},
            'parent_header': {'msg_id': 'msg-1'},
            'content': {
                'ename': 'ValueError',
                'evalue': 'boom',
                'traceback': ['line'],
            },
        },
        {
            'header': {'msg_type': 'status'},
            'parent_header': {'msg_id': 'msg-1'},
            'content': {'execution_state': 'idle'},
        },
    ]
    client._shell_empty_before_reply = 1
    client._shell = [
        {
            'header': {'msg_type': 'execute_reply'},
            'parent_header': {'msg_id': 'msg-1'},
            'content': {
                'status': 'error',
                'ename': 'ValueError',
                'evalue': 'boom',
                'traceback': ['line'],
            },
        }
    ]
    backend = ChatbookBackend(
        'python3', cwd='/tmp', manager_factory=lambda name: manager
    )
    backend.start()
    reply = backend.execute('raise ValueError("boom")', lambda t, c: None)
    assert reply['status'] == 'error'
    assert reply['ename'] == 'ValueError'


def test_backend_execute_raises_when_child_dies():
    manager = _FakeManager('python3')
    manager.alive = False
    backend = ChatbookBackend(
        'python3', cwd='/tmp', manager_factory=lambda name: manager
    )
    backend.start()
    with pytest.raises(RuntimeError, match='died'):
        backend.execute('print(1)', lambda t, c: None)
    assert backend.ready is False
    assert manager.client_obj.channels_stopped is True


def test_interrupt_targets_child_and_aborts_queue_without_wrapper_sigint():
    kernel = _kernel_with_backend({'status': 'ok'})
    aborted = []
    kernel._abort_queues = lambda *args, **kwargs: aborted.append(args)

    assert kernel.interrupt_request(None, b'ident', {'content': {}}) is None
    assert kernel._backend.interrupted is True
    assert aborted
    replies = [item for item in kernel.session.sent if item[0] == 'interrupt_reply']
    assert replies[0][1]['status'] == 'ok'


def test_interrupt_during_generation_does_not_run_code(monkeypatch):
    monkeypatch.setattr(
        'notebook_intelligence.chatbook_kernel.kernel.NBIConfig',
        lambda: SimpleNamespace(
            chatbook_execution_mode='auto-run',
            chatbook_llm_danger_scan=False,
        ),
    )
    monkeypatch.delenv('NBI_CHATBOOK_MAX_EXECUTION_MODE', raising=False)
    kernel = _kernel_with_backend({'status': 'ok'})
    kernel.execution_count = 5
    ran = []

    def execute_in_backend(stream, ident, parent, code):
        ran.append(code)
        return None

    kernel._execute_in_backend = execute_in_backend

    def generate(prompt, meta):
        kernel.interrupt_request(None, b'ident', {'content': {}})
        return {'generatedCode': 'value = 1\n'}

    kernel._generate = generate
    kernel.execute_request(
        None,
        b'ident',
        {
            'content': {'code': 'plot the values'},
            'metadata': {
                'nbi_chatbook': {
                    'cellId': 'c1',
                    'executionPolicy': 'auto-run',
                    'llmDangerScan': False,
                }
            },
        },
    )
    assert ran == []
    replies = [item for item in kernel.session.sent if item[0] == 'execute_reply']
    assert replies[-1][1]['status'] == 'error'
    # Nothing ran, so the cell must not be stamped with the number the last
    # real run earned; the interrupt reply goes out through `_reply_error`.
    assert replies[-1][1]['execution_count'] is None
    assert replies[-1][1]['ename'] == 'KeyboardInterrupt'


def _always_confirm_kernel(monkeypatch, generated='value = 1\n'):
    monkeypatch.setattr(
        'notebook_intelligence.chatbook_kernel.kernel.NBIConfig',
        lambda: SimpleNamespace(
            chatbook_execution_mode='always-confirm',
            chatbook_llm_danger_scan=False,
        ),
    )
    monkeypatch.delenv('NBI_CHATBOOK_MAX_EXECUTION_MODE', raising=False)
    kernel = _kernel_with_backend({'status': 'ok'})
    kernel._generate = lambda prompt, meta: {'generatedCode': generated}
    return kernel


def _run_prompt(kernel, chatbook_meta):
    kernel.execute_request(
        None,
        b'ident',
        {
            'content': {'code': 'plot the values'},
            'metadata': {'nbi_chatbook': {'cellId': 'c1', **chatbook_meta}},
        },
    )
    payloads = [
        content
        for msg_type, content in kernel.session.sent
        if msg_type == 'execute_reply'
    ]
    return payloads[-1]


def test_kernel_runs_regenerated_code_the_user_already_approved(monkeypatch):
    kernel = _always_confirm_kernel(monkeypatch)
    published = []
    kernel._publish_chatbook_code = lambda parent, payload: published.append(
        payload
    )
    # `approvedCode` is what an earlier payload carried, so it is already the
    # extracted (stripped) cell that regeneration is compared against.
    reply = _run_prompt(
        kernel,
        {'executionPolicy': 'always-confirm', 'approvedCode': 'value = 1'},
    )
    assert kernel._backend.relayed == ['value = 1']
    assert reply['status'] == 'ok'
    assert published[-1]['executed'] is True
    assert published[-1]['executionPolicy'] == 'always-confirm'


def test_kernel_confirms_when_regenerated_code_differs_from_approved(monkeypatch):
    kernel = _always_confirm_kernel(monkeypatch)
    published = []
    kernel._publish_chatbook_code = lambda parent, payload: published.append(
        payload
    )
    reply = _run_prompt(
        kernel,
        {'executionPolicy': 'always-confirm', 'approvedCode': 'value = 2'},
    )
    assert kernel._backend.relayed == []
    assert reply['status'] == 'ok'
    assert published[-1]['executed'] is False
    assert published[-1]['generatedCode'] == 'value = 1'


def test_kernel_awaiting_confirmation_reports_no_execution_count(monkeypatch):
    """A cell whose code has not run must stay unnumbered.

    The frontend stamps the cell from this reply, so the number the last cell
    that really ran earned would be shown again here, on a cell the user has
    not answered for and that has no output.
    """
    kernel = _always_confirm_kernel(monkeypatch)
    kernel._publish_chatbook_code = lambda parent, payload: None
    # A code cell already ran in this session, which is what makes the stale
    # number visible; on a fresh kernel the counter is 0 and renders blank.
    kernel.execution_count = 3

    reply = _run_prompt(kernel, {'executionPolicy': 'always-confirm'})

    assert kernel._backend.relayed == []
    assert reply['status'] == 'ok'
    assert reply['execution_count'] is None
    # Nothing ran, so the wrapper's own counter must not move either.
    assert kernel.execution_count == 3


def test_kernel_rerun_left_awaiting_confirmation_drops_the_old_number(monkeypatch):
    """A re-run cell must lose the number its earlier run earned.

    The frontend clears the outputs when the request starts, so keeping the
    number would leave a numbered cell with nothing to show for it.
    """
    kernel = _always_confirm_kernel(monkeypatch)
    kernel._backend = _StubBackend({'status': 'ok', 'execution_count': 4})
    kernel._publish_chatbook_code = lambda parent, payload: None

    first = _run_prompt(
        kernel, {'executionPolicy': 'always-confirm', 'approvedCode': 'value = 1'}
    )
    # Guards the other direction: the approved run still reports the child's
    # number, so this is not a blanket None on every reply.
    assert first['execution_count'] == 4

    second = _run_prompt(kernel, {'executionPolicy': 'always-confirm'})

    assert kernel._backend.relayed == ['value = 1']
    assert second['execution_count'] is None


def test_kernel_error_reply_reports_no_execution_count(monkeypatch):
    """A request that failed before running code must not be numbered either.

    Generation failures reach the error reply with the counter still holding
    the last real run's number, which would show twice in the notebook.
    """
    kernel = _always_confirm_kernel(monkeypatch)
    kernel._publish_chatbook_code = lambda parent, payload: None
    kernel.execution_count = 4

    def _boom(prompt, meta):
        raise NBIClientError('generate is unreachable')

    kernel._generate = _boom

    reply = _run_prompt(kernel, {'executionPolicy': 'always-confirm'})

    assert reply['status'] == 'error'
    assert reply['execution_count'] is None
    assert kernel._backend.relayed == []


def test_kernel_payload_reports_the_policy_it_resolved(monkeypatch):
    kernel = _always_confirm_kernel(monkeypatch)
    monkeypatch.setenv('NBI_CHATBOOK_MAX_EXECUTION_MODE', 'always-confirm')
    monkeypatch.setattr(
        'notebook_intelligence.chatbook_kernel.kernel.NBIConfig',
        lambda: SimpleNamespace(
            chatbook_execution_mode='auto-run',
            chatbook_llm_danger_scan=False,
        ),
    )
    published = []
    kernel._publish_chatbook_code = lambda parent, payload: published.append(
        payload
    )
    _run_prompt(kernel, {'executionPolicy': 'auto-run'})
    assert kernel._backend.relayed == []
    assert published[-1]['executed'] is False
    assert published[-1]['executionPolicy'] == 'always-confirm'


def test_chatbook_kernelspec_declares_message_interrupts():
    from pathlib import Path

    spec_path = (
        Path(__file__).resolve().parents[1]
        / 'notebook_intelligence'
        / 'chatbook_kernel'
        / 'kernelspec'
        / 'kernel.json'
    )
    spec = json.loads(spec_path.read_text(encoding='utf-8'))
    assert spec['interrupt_mode'] == 'message'


def test_kernel_proxies_complete_request_to_backend():
    kernel = _kernel_with_backend({'status': 'ok'})
    kernel.complete_request(
        None,
        b'ident',
        {'content': {'code': 'impor', 'cursor_pos': 5}},
    )
    assert kernel._backend.shell_requests == [
        ('complete_request', {'code': 'impor', 'cursor_pos': 5})
    ]
    replies = [item for item in kernel.session.sent if item[0] == 'complete_reply']
    assert replies[0][1]['matches'] == ['import']


def test_kernel_forwards_comm_messages_to_backend():
    kernel = _kernel_with_backend({'status': 'ok'})
    kernel._forward_comm(
        None,
        b'ident',
        {
            'header': {'msg_type': 'comm_open'},
            'content': {'target_name': 'jupyter.widget', 'comm_id': 'c1'},
        },
    )
    assert kernel._backend.forwarded == [
        ('comm_open', {'target_name': 'jupyter.widget', 'comm_id': 'c1'})
    ]


def test_kernel_clamps_client_policy_to_admin_cap(monkeypatch):
    monkeypatch.setenv('NBI_CHATBOOK_MAX_EXECUTION_MODE', 'always-confirm')
    kernel = ChatbookKernel()
    monkeypatch.setattr(
        'notebook_intelligence.chatbook_kernel.kernel.NBIConfig',
        lambda: SimpleNamespace(chatbook_execution_mode='auto-run'),
    )
    assert (
        kernel._execution_policy({'executionPolicy': 'auto-run'})
        == 'always-confirm'
    )


def test_kernel_aborts_queue_on_error_reply():
    kernel = _kernel_with_backend(
        {
            'status': 'error',
            'ename': 'ValueError',
            'evalue': 'boom',
            'traceback': ['line'],
        }
    )
    called = []
    kernel._abort_queues = lambda *args, **kwargs: called.append(args)
    kernel._execute_in_backend(None, b'ident', {'content': {}}, 'print(1)')
    assert called
