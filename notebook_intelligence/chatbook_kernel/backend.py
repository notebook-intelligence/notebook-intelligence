# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Resolve and proxy the Jupyter kernelspec that executes Chatbook-generated code."""

from __future__ import annotations

import json
import logging
import os
import time
from queue import Empty
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

CHATBOOK_KERNEL_NAME = "chatbook"
CHATBOOK_LANGUAGE = "chatbook"
DEFAULT_BACKEND_KERNEL_NAME = "python3"

# The Jupyter server's kernelspec manager, described for the Chatbook kernel
# process (see `describe_kernel_spec_manager`).
KERNEL_SPEC_MANAGER_ENV = "NBI_CHATBOOK_KERNEL_SPEC_MANAGER"

_IOPUB_RELAY_TYPES = {
    "stream",
    "display_data",
    "update_display_data",
    "execute_result",
    "execute_input",
    "error",
    "clear_output",
    "comm_open",
    "comm_msg",
    "comm_close",
}

# After the child goes idle, wait this long for execute_reply before
# treating a missing reply as success. A single empty 100 ms poll is
# not enough: a slightly late error reply would otherwise report ok.
_EXECUTE_REPLY_WAIT_S = 2.0
_SHELL_REQUEST_TIMEOUT_S = 10.0


def _spec_fields(record: Any) -> tuple[str, str]:
    spec = record
    if isinstance(record, dict) and "spec" in record:
        spec = record.get("spec") or {}
    if isinstance(spec, dict):
        language = str(spec.get("language") or "").strip()
        display_name = str(spec.get("display_name") or "").strip()
        return language, display_name
    language = str(getattr(spec, "language", "") or "").strip()
    display_name = str(getattr(spec, "display_name", "") or "").strip()
    return language, display_name


def is_chatbook_spec(record: Any) -> bool:
    """Whether a kernelspec runs the Chatbook kernel, whatever it is named.

    A custom kernelspec manager can list Chatbook under its own name
    (nb_conda_kernels lists it as ``conda-base-chatbook``), so the name alone
    does not identify it; the language does. Accepts a ``KernelSpec``, a spec
    dict, or a ``get_all_specs()`` record.
    """
    language, _ = _spec_fields(record)
    return language.lower() == CHATBOOK_LANGUAGE


def list_backend_kernels(specs: Optional[dict] = None) -> list[dict[str, str]]:
    """Installed kernelspecs excluding the Chatbook wrapper itself."""
    backends: list[dict[str, str]] = []
    for name, record in (specs or {}).items():
        if not name or name == CHATBOOK_KERNEL_NAME or is_chatbook_spec(record):
            continue
        language, display_name = _spec_fields(record)
        backends.append(
            {
                "name": name,
                "language": language,
                "display_name": display_name or name,
            }
        )
    backends.sort(key=lambda item: item["name"])
    return backends


_kernel_spec_manager: Any = None


def describe_kernel_spec_manager(manager: Any) -> str:
    """The class and JSON-safe config of ``manager``, for the Chatbook kernel.

    The Jupyter server may use a custom kernelspec manager that lists kernels
    under its own names (nb_conda_kernels lists ``python3`` as
    ``conda-base-py``). The Chatbook kernel has to resolve backend names the
    same way, so the server passes this description to it in
    ``KERNEL_SPEC_MANAGER_ENV``. Only the config sections of the manager's own
    classes are kept. Sets (``allowed_kernelspecs``) travel as lists, which
    traitlets turns back into sets; other values that are not JSON are dropped.
    """
    cls = type(manager)
    config = getattr(manager, "config", None) or {}
    sections: dict[str, dict] = {}
    for klass in cls.__mro__:
        if klass.__name__ not in config:
            continue
        section = {}
        for key, value in dict(config[klass.__name__]).items():
            if isinstance(value, (set, frozenset)):
                value = sorted(value, key=str)
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            section[key] = value
        if section:
            sections[klass.__name__] = section
    return json.dumps(
        {"class": f"{cls.__module__}.{cls.__qualname__}", "config": sections}
    )


def set_kernel_spec_manager(manager: Any) -> None:
    """Resolve backend kernels with ``manager`` in this process.

    The server extension passes the Jupyter server's own manager, so names
    resolved in the server match the ones the kernel picker lists.
    """
    global _kernel_spec_manager
    _kernel_spec_manager = manager


def _kernel_spec_manager_from_env() -> Any:
    raw = os.environ.get(KERNEL_SPEC_MANAGER_ENV, "").strip()
    if not raw:
        return None
    try:
        from traitlets.config import Config
        from traitlets.utils.importstring import import_item

        description = json.loads(raw)
        cls = import_item(str(description["class"]))
        return cls(config=Config(description.get("config") or {}))
    except Exception as exc:
        log.warning(
            "Could not create the Jupyter server's kernelspec manager (%s); "
            "using Jupyter's default",
            exc,
        )
        return None


def kernel_spec_manager() -> Any:
    """The kernelspec manager backend kernels are listed and started with.

    In the server, the server's own (see `set_kernel_spec_manager`). In the
    Chatbook kernel, one built from the description the server put in the
    kernel's environment, so a backend named in Settings → Chatbook resolves
    to the same kernelspec here. Otherwise Jupyter's default manager.
    """
    global _kernel_spec_manager
    if _kernel_spec_manager is None:
        manager = _kernel_spec_manager_from_env()
        if manager is None:
            from jupyter_client.kernelspec import KernelSpecManager

            manager = KernelSpecManager()
        _kernel_spec_manager = manager
    return _kernel_spec_manager


def load_kernel_specs() -> dict:
    try:
        manager = kernel_spec_manager()
    except ImportError:
        return {}
    try:
        return manager.get_all_specs()
    except Exception as exc:
        log.warning("Could not list Jupyter kernelspecs: %s", exc)
        return {}


def resolve_backend_kernel(
    preferred: str = "",
    specs: Optional[dict] = None,
) -> dict[str, str]:
    """Pick a non-chatbook kernelspec. Prefer ``preferred``, then python3, then any Python, then first."""
    backends = list_backend_kernels(specs)
    if not backends:
        raise RuntimeError(
            "No Jupyter kernel is installed to use as the Chatbook backend. "
            "Install a kernelspec and choose it in Settings → Chatbook."
        )
    by_name = {item["name"]: item for item in backends}
    wanted = (preferred or "").strip()
    if wanted == CHATBOOK_KERNEL_NAME or is_chatbook_spec((specs or {}).get(wanted)):
        wanted = ""
    if wanted in by_name:
        return by_name[wanted]
    if wanted:
        raise RuntimeError(
            f"Chatbook backend kernel '{wanted}' is not installed. "
            "Choose an installed kernelspec in Settings → Chatbook."
        )
    if DEFAULT_BACKEND_KERNEL_NAME in by_name:
        return by_name[DEFAULT_BACKEND_KERNEL_NAME]
    for item in backends:
        if (item.get("language") or "").strip().lower() in {"python", "py"}:
            return item
    return backends[0]


def is_python_language(language: str) -> bool:
    return (language or "").strip().lower() in {"", "python", "py"}


class ChatbookBackend:
    """Child kernelspec started beside the Chatbook wrapper kernel."""

    def __init__(
        self,
        kernel_name: str,
        cwd: Optional[str] = None,
        manager_factory: Optional[Callable[[str], Any]] = None,
    ):
        self.kernel_name = kernel_name
        self.cwd = cwd or os.getcwd()
        self._manager_factory = manager_factory
        self._km: Any = None
        self._kc: Any = None

    @property
    def ready(self) -> bool:
        return self._kc is not None and self.is_alive()

    def is_alive(self) -> bool:
        if self._kc is None:
            return False
        km = self._km
        check = getattr(km, "is_alive", None) if km is not None else None
        if callable(check):
            try:
                return bool(check())
            except Exception:
                return False
        return True

    def _mark_dead(self) -> None:
        # A dead kernel still leaves the client's ZMQ channels and heartbeat
        # thread alive. Detach and stop them before `_ensure_backend` calls
        # `shutdown()` and starts a replacement.
        kc, self._kc = self._kc, None
        if kc is not None:
            stop = getattr(kc, "stop_channels", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    log.debug("Dead backend channel cleanup failed", exc_info=True)

    def start(self) -> None:
        if self.ready:
            return
        factory = self._manager_factory
        if factory is None:
            from jupyter_client import KernelManager

            factory = lambda name: KernelManager(
                kernel_name=name, kernel_spec_manager=kernel_spec_manager()
            )
        self._km = factory(self.kernel_name)
        start_kernel = getattr(self._km, "start_kernel", None)
        if callable(start_kernel):
            try:
                start_kernel(cwd=self.cwd)
            except TypeError:
                start_kernel()
        client_factory = getattr(self._km, "client", None)
        self._kc = client_factory() if callable(client_factory) else self._km
        start_channels = getattr(self._kc, "start_channels", None)
        if callable(start_channels):
            start_channels()
        wait = getattr(self._kc, "wait_for_ready", None)
        if callable(wait):
            wait(timeout=60)

    def shutdown(self) -> None:
        kc, km = self._kc, self._km
        self._kc = None
        self._km = None
        if kc is not None:
            stop = getattr(kc, "stop_channels", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass
        if km is not None:
            shutdown = getattr(km, "shutdown_kernel", None)
            if callable(shutdown):
                try:
                    shutdown(now=True)
                except Exception:
                    log.debug("Backend kernel shutdown failed", exc_info=True)

    def interrupt(self) -> None:
        km = self._km
        if km is None:
            return
        interrupt = getattr(km, "interrupt_kernel", None)
        if callable(interrupt):
            interrupt()

    def _live_client(self) -> Any:
        if self._kc is None or not self.is_alive():
            self._mark_dead()
            raise RuntimeError(
                f"Chatbook backend kernel '{self.kernel_name}' died. "
                "Restart the Chatbook kernel after choosing a backend in Settings."
            )
        return self._kc

    def forward_shell(self, msg_type: str, content: Optional[dict] = None) -> None:
        """Send a fire-and-forget shell message (comm_open / comm_msg / comm_close)."""
        kc = self._live_client()
        session = getattr(kc, "session", None)
        channel = getattr(kc, "shell_channel", None)
        send = getattr(channel, "send", None) if channel is not None else None
        msg = getattr(session, "msg", None) if session is not None else None
        if not callable(send) or not callable(msg):
            log.debug("Backend client cannot forward %s", msg_type)
            return
        send(msg(msg_type, dict(content or {})))

    def shell_request(
        self,
        msg_type: str,
        content: Optional[dict] = None,
        timeout: float = _SHELL_REQUEST_TIMEOUT_S,
    ) -> dict:
        """Send a shell request to the child and wait for the matching reply."""
        kc = self._live_client()
        session = getattr(kc, "session", None)
        channel = getattr(kc, "shell_channel", None)
        send = getattr(channel, "send", None) if channel is not None else None
        make_msg = getattr(session, "msg", None) if session is not None else None
        if not callable(send) or not callable(make_msg):
            raise RuntimeError(
                f"Chatbook backend kernel '{self.kernel_name}' cannot proxy {msg_type}"
            )
        request = make_msg(msg_type, dict(content or {}))
        msg_id = (request.get("header") or {}).get("msg_id")
        send(request)
        deadline = time.monotonic() + max(0.1, timeout)
        reply_type = msg_type.replace("_request", "_reply")
        while time.monotonic() < deadline:
            try:
                shell_msg = kc.get_shell_msg(timeout=0.1)
            except Empty:
                if not self.is_alive():
                    self._mark_dead()
                    raise RuntimeError(
                        f"Chatbook backend kernel '{self.kernel_name}' died. "
                        "Restart the Chatbook kernel."
                    )
                continue
            parent = shell_msg.get("parent_header") or {}
            if parent.get("msg_id") != msg_id:
                continue
            if (shell_msg.get("header") or {}).get("msg_type") == reply_type:
                return dict(shell_msg.get("content") or {})
        raise RuntimeError(
            f"Timed out waiting for {reply_type} from Chatbook backend kernel "
            f"'{self.kernel_name}'"
        )

    def execute(self, code: str, relay: Callable[[str, dict], None]) -> dict:
        """Run ``code`` in the child kernel and relay IOPub content via ``relay``."""
        if self._kc is None or not self.is_alive():
            self._mark_dead()
            raise RuntimeError(
                f"Chatbook backend kernel '{self.kernel_name}' died. "
                "Restart the Chatbook kernel after choosing a backend in Settings."
            )
        msg_id = self._kc.execute(
            code or "",
            silent=False,
            store_history=True,
            allow_stdin=False,
            stop_on_error=True,
        )
        idle = False
        while not idle:
            try:
                msg = self._kc.get_iopub_msg(timeout=0.1)
            except Empty:
                if not self.is_alive():
                    self._mark_dead()
                    raise RuntimeError(
                        f"Chatbook backend kernel '{self.kernel_name}' died. "
                        "Restart the Chatbook kernel."
                    )
                continue
            except Exception as exc:
                if not self.is_alive():
                    self._mark_dead()
                    raise RuntimeError(
                        f"Chatbook backend kernel '{self.kernel_name}' died. "
                        "Restart the Chatbook kernel."
                    ) from exc
                raise
            header = msg.get("header") or {}
            parent = msg.get("parent_header") or {}
            if parent.get("msg_id") != msg_id:
                continue
            msg_type = header.get("msg_type") or ""
            if msg_type == "status":
                if (msg.get("content") or {}).get("execution_state") == "idle":
                    idle = True
                continue
            if msg_type in _IOPUB_RELAY_TYPES:
                relay(msg_type, dict(msg.get("content") or {}))
        reply: dict = {"status": "ok"}
        deadline = time.monotonic() + _EXECUTE_REPLY_WAIT_S
        while time.monotonic() < deadline:
            try:
                shell_msg = self._kc.get_shell_msg(timeout=0.1)
            except Empty:
                if not self.is_alive():
                    self._mark_dead()
                    raise RuntimeError(
                        f"Chatbook backend kernel '{self.kernel_name}' died. "
                        "Restart the Chatbook kernel."
                    )
                continue
            except Exception as exc:
                if not self.is_alive():
                    self._mark_dead()
                    raise RuntimeError(
                        f"Chatbook backend kernel '{self.kernel_name}' died. "
                        "Restart the Chatbook kernel."
                    ) from exc
                raise
            parent = shell_msg.get("parent_header") or {}
            if parent.get("msg_id") != msg_id:
                continue
            if (shell_msg.get("header") or {}).get("msg_type") == "execute_reply":
                reply = dict(shell_msg.get("content") or {})
                break
        return reply
