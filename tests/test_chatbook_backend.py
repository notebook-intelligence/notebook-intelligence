# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import pytest
from jupyter_client.session import Session

from notebook_intelligence.chatbook_kernel.backend import (
    ChatbookBackend,
    list_backend_kernels,
    resolve_backend_kernel,
)
from notebook_intelligence.chatbook_kernel.codegen import (
    cell_codegen_instructions,
    extract_code_cell,
)
from notebook_intelligence.chatbook_kernel.danger import scan_generated_code
from notebook_intelligence.chatbook_kernel.kernel import ChatbookKernel, is_code_execute


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


def test_is_code_execute_only_accepts_code_mode():
    assert is_code_execute({'executeMode': 'code'})
    assert not is_code_execute({'executeMode': 'prompt'})
    assert not is_code_execute({'executeMode': 'python'})


def test_non_python_static_scan_is_risky():
    scan = scan_generated_code('plot(1)', 'R')
    assert scan['level'] == 'risky'
    assert scan['reasons']


def test_python_static_scan_still_clean_for_plain_code():
    scan = scan_generated_code('total = 1\ntotal\n', 'python')
    assert scan['level'] == 'clean'


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

    def execute(self, code, **kwargs):
        self.executed.append(code)
        return 'msg-1'

    def start_channels(self):
        return None

    def wait_for_ready(self, timeout=60):
        return None

    def stop_channels(self):
        return None

    def get_iopub_msg(self, timeout=0.1):
        if self._iopub:
            return self._iopub.pop(0)
        from queue import Empty

        raise Empty

    def get_shell_msg(self, timeout=0.1):
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


class _RecordingSession(Session):
    """Session that records outgoing messages instead of writing to a socket."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sent = []

    def send(self, stream, msg_or_type, content=None, *args, **kwargs):
        self.sent.append((msg_or_type, content))
        return None


class _StubBackend:
    def __init__(self, reply):
        self.reply = reply
        self.relayed = []

    def execute(self, code, relay):
        relay('stream', {'name': 'stdout', 'text': 'hi\n'})
        self.relayed.append(code)
        return self.reply


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
