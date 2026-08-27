# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Resolve and proxy the Jupyter kernelspec that executes Chatbook-generated code."""

from __future__ import annotations

import logging
import os
from queue import Empty
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

CHATBOOK_KERNEL_NAME = "chatbook"
DEFAULT_BACKEND_KERNEL_NAME = "python3"

_IOPUB_RELAY_TYPES = {
    "stream",
    "display_data",
    "update_display_data",
    "execute_result",
    "execute_input",
    "error",
    "clear_output",
}


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


def list_backend_kernels(specs: Optional[dict] = None) -> list[dict[str, str]]:
    """Installed kernelspecs excluding the Chatbook wrapper itself."""
    backends: list[dict[str, str]] = []
    for name, record in (specs or {}).items():
        if not name or name == CHATBOOK_KERNEL_NAME:
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


def load_kernel_specs() -> dict:
    try:
        from jupyter_client.kernelspec import KernelSpecManager
    except ImportError:
        return {}
    try:
        return KernelSpecManager().get_all_specs()
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
    if wanted == CHATBOOK_KERNEL_NAME:
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

            factory = lambda name: KernelManager(kernel_name=name)
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
        while True:
            try:
                shell_msg = self._kc.get_shell_msg(timeout=0.1)
            except Empty:
                break
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
