# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Nonexecuting checks for an ACP adapter's declared interpreter."""

import os
import re
import shutil
import stat
from typing import Optional

from notebook_intelligence.acp_registry import validate_acp_executable_path


_SHEBANG_LIMIT = 4096


def _resolve_interpreter(program: str, env_path: str, cwd: Optional[str]) -> Optional[str]:
    if cwd is not None and not os.path.isabs(cwd):
        cwd = os.path.join(os.getcwd(), cwd)
    if os.path.isabs(program):
        return shutil.which(program)
    if os.path.dirname(program):
        if cwd is None:
            raise ValueError("The adapter working directory is needed to check its relative interpreter")
        return shutil.which(os.path.join(cwd, program))
    for directory in env_path.split(os.pathsep):
        if not os.path.isabs(directory):
            if cwd is None:
                raise ValueError("The adapter working directory is needed to check its relative PATH entries")
            directory = os.path.join(cwd, directory)
        # Absolute candidates avoid shutil.which's implicit current-directory
        # search on Windows and model env's lookup from the adapter's cwd.
        # Keep symlink/.. traversal intact: env and the kernel do not collapse
        # it lexically, so doing so here could validate a different executable.
        resolved = shutil.which(os.path.join(directory, program))
        if resolved is not None:
            return resolved
    return None


def validate_acp_adapter_runtime(
    command: list[str], *, env_path: str, cwd: Optional[str] = None,
    workspace_root: Optional[str] = None,
) -> None:
    """Check a shebang using the same PATH and cwd as the eventual launch.

    Read at most one bounded line, never execute the adapter or interpreter,
    and report unsupported env syntax instead of guessing how env expands it.
    This checks the declared interpreter's availability, not adapter behavior
    or the internal dependencies of a native binary or administrator shim.
    Default startup supplies workspace_root to exclude interpreters in the
    workspace, including executable symlinks. Explicit administrator overrides
    leave it unset and retain their trusted runtime choice.
    """
    executable = command[0]
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(executable, flags), "rb") as adapter:
            if not stat.S_ISREG(os.fstat(adapter.fileno()).st_mode):
                raise ValueError(f"ACP adapter '{executable}' is not a regular file")
            first_line = adapter.readline(_SHEBANG_LIMIT + 1)
    except OSError as exc:
        raise ValueError(
            f"Cannot read ACP adapter '{executable}' to check its interpreter ({type(exc).__name__}). "
            "Check the installed executable's read permissions and filesystem links."
        ) from exc
    if not first_line.startswith(b"#!"):
        return
    if len(first_line) > _SHEBANG_LIMIT or any(
        char < 0x20 and char not in (0x09, 0x0A) for char in first_line
    ):
        raise ValueError("ACP adapter has an invalid or overlong interpreter line; reinstall the adapter")
    try:
        shebang = re.split(r"[ \t]+", first_line[2:].decode("utf-8").strip(" \t\n"), maxsplit=1)
    except UnicodeError as exc:
        raise ValueError("ACP adapter's interpreter line is not valid UTF-8; reinstall the adapter") from exc
    if not shebang or not os.path.isabs(shebang[0]):
        raise ValueError("ACP adapter must declare an absolute interpreter path; reinstall the adapter")
    interpreter = shebang[0]
    if shutil.which(interpreter) is None:
        raise ValueError(
            f"ACP adapter requires interpreter '{interpreter}', which is missing or not executable. "
            "Install the declared interpreter or reinstall the adapter."
        )
    if workspace_root is not None:
        validate_acp_executable_path(interpreter, workspace_root)
    if os.path.basename(interpreter) != "env":
        return

    arguments = re.split(r"[ \t]+", shebang[1]) if len(shebang) == 2 else []
    # With no -S, kernels pass the remaining shebang text as one argument.
    # For -S support the common plain command/flag form, but do not attempt
    # GNU/BSD env's quoting, expansion, assignment, or option grammars.
    if arguments and arguments[0] == "-S":
        arguments = arguments[1:]
        supported = not any(char in shebang[1] for char in "\\\"'$#")
    else:
        supported = len(arguments) == 1
    if not supported or not arguments or arguments[0].startswith("-") or "=" in arguments[0]:
        raise ValueError(
            "Cannot validate ACP adapter's env interpreter line. Use an installed adapter with "
            "a simple '#!/usr/bin/env node' or '#!/usr/bin/env -S node <flags>' line."
        )
    program = arguments[0]
    resolved_program = _resolve_interpreter(program, env_path, cwd)
    if resolved_program is None:
        raise ValueError(
            f"ACP adapter requires interpreter '{program}', which is missing or not executable on "
            "the adapter PATH. Install it in a trusted directory available to the adapter."
        )
    if workspace_root is not None:
        validate_acp_executable_path(resolved_program, workspace_root)
