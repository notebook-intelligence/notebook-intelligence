# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Registry of ACP agent types selectable in ACP mode (issue #378).

Kept separate from ``acp_agent`` so surfaces that only need agent metadata
(the capabilities response feeding the settings dropdown) can import it
without pulling in the ``acp`` package -- that dependency stays off the
startup path and loads only when ACP mode is actually used.

Adding an agent type is a registry entry: its adapter package, display
metadata, and how it authenticates. Agent-specific launch quirks that do not
generalize (Codex's approval and sandbox pins, its CODEX_HOME isolation) stay keyed
off the spec id in ``acp_agent`` rather than growing fields prematurely.
"""

import base64
import os
import shlex
import shutil
import stat
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Pinned in Phase 1 (the version the spike validated); revisit per release.
CODEX_ACP_PACKAGE = "@zed-industries/codex-acp@0.16.0"

# The OpenAI mark, matching style/icons/openai.svg. Rendered as an <img> in the
# chat (the response avatar), so the fill is pinned to OpenAI blue rather than
# currentColor, mirroring how the Claude participant pins Anthropic orange.
_CODEX_ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" fill="#0a84ff" fill-rule="evenodd" '
    'viewBox="0 0 24 24" width="24" height="24">'
    '<path clip-rule="evenodd" d="M8.086.457a6.105 6.105 0 013.046-.415c1.333.153 '
    '2.521.72 3.564 1.7a.117.117 0 00.107.029c1.408-.346 2.762-.224 4.061.366l.063.03'
    '.154.076c1.357.703 2.33 1.77 2.918 3.198.278.679.418 1.388.421 2.126a5.655 5.655 '
    '0 01-.18 1.631.167.167 0 00.04.155 5.982 5.982 0 011.578 2.891c.385 1.901-.01 '
    '3.615-1.183 5.14l-.182.22a6.063 6.063 0 01-2.934 1.851.162.162 0 00-.108.102c-.255'
    '.736-.511 1.364-.987 1.992-1.199 1.582-2.962 2.462-4.948 2.451-1.583-.008-2.986-.587'
    '-4.21-1.736a.145.145 0 00-.14-.032c-.518.167-1.04.191-1.604.185a5.924 5.924 0 '
    '01-2.595-.622 6.058 6.058 0 01-2.146-1.781c-.203-.269-.404-.522-.551-.821a7.74 7.74 '
    '0 01-.495-1.283 6.11 6.11 0 01-.017-3.064.166.166 0 00.008-.074.115.115 0 '
    '00-.037-.064 5.958 5.958 0 01-1.38-2.202 5.196 5.196 0 01-.333-1.589 6.915 6.915 0 '
    '01.188-2.132c.45-1.484 1.309-2.648 2.577-3.493.282-.188.55-.334.802-.438.286-.12'
    '.573-.22.861-.304a.129.129 0 00.087-.087A6.016 6.016 0 015.635 2.31C6.315 1.464 '
    '7.132.846 8.086.457zm-.804 7.85a.848.848 0 00-1.473.842l1.694 2.965-1.688 2.848a.849'
    '.849 0 001.46.864l1.94-3.272a.849.849 0 00.007-.854l-1.94-3.393zm5.446 6.24a.849.849 '
    '0 000 1.695h4.848a.849.849 0 000-1.696h-4.848z"/></svg>'
)
CODEX_AGENT_ICON_URL = (
    "data:image/svg+xml;base64,"
    + base64.b64encode(_CODEX_ICON_SVG.encode("utf-8")).decode("utf-8")
)


@dataclass(frozen=True)
class AcpAgentSpec:
    """One selectable agent type for ACP mode."""
    id: str
    label: str
    description: str
    package: str      # pinned package to install before starting the adapter
    executable: str   # preinstalled adapter binary, resolved before spawning
    icon_url: str
    api_key_env: str  # env var the agent reads its API key from
    auth_method: str  # preferred ACP auth method id when a key is present


ACP_AGENTS: dict[str, AcpAgentSpec] = {
    "codex": AcpAgentSpec(
        id="codex",
        label="Codex",
        description="OpenAI Codex (via the Agent Client Protocol)",
        package=CODEX_ACP_PACKAGE,
        executable="codex-acp",
        icon_url=CODEX_AGENT_ICON_URL,
        api_key_env="OPENAI_API_KEY",
        auth_method="openai-api-key",
    ),
}
DEFAULT_ACP_AGENT = "codex"


def resolve_acp_agent(agent_id: Optional[str]) -> AcpAgentSpec:
    """The spec for ``agent_id``, falling back to the default for unknown or
    missing ids so a stale config value cannot break the mode."""
    return ACP_AGENTS.get(agent_id or "", None) or ACP_AGENTS[DEFAULT_ACP_AGENT]


@dataclass(frozen=True)
class _WorkspaceBoundary:
    paths: tuple[Path, Path]
    identity: tuple[int, int]

    def external_target(self, path: Path) -> Optional[Path]:
        """Resolve a path only when every traversal step stays outside the workspace.

        Endpoint checks miss an external link that traverses a workspace link
        on its way to an external target. Walk components and expand each link
        before processing ``..``, checking identities as well as spellings to
        catch case, Unicode, and mount aliases. This inspects the current link
        chain; it does not lock the filesystem against concurrent changes.
        """
        if not path.is_absolute():
            raise ValueError("ACP PATH entry is not absolute")
        if any(path.is_relative_to(root) for root in self.paths):
            return None
        remaining = deque(path.parts[1:])
        current = Path(path.anchor)
        links = 0
        while True:
            if any(current.is_relative_to(root) for root in self.paths):
                return None
            info = current.lstat()
            if not info.st_ino:
                raise ValueError("ACP PATH entry has no stable filesystem identity")
            if (info.st_dev, info.st_ino) == self.identity:
                return None
            # Windows junctions are reparse points rather than S_ISLNK links.
            # readlink either expands them or fails closed for an unsupported
            # reparse point; never silently follow one in a later stat call.
            is_link = stat.S_ISLNK(info.st_mode) or bool(
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            )
            if is_link:
                links += 1
                if links > 40:
                    raise ValueError("ACP PATH entry has too many symbolic links")
                target = current.readlink()
                if target.is_absolute():
                    current = Path(target.anchor)
                    remaining.extendleft(reversed(target.parts[1:]))
                else:
                    if target.anchor:
                        raise ValueError("ACP symbolic link target is not fully qualified")
                    current = current.parent
                    remaining.extendleft(reversed(target.parts))
                continue
            if not remaining:
                return current
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError("ACP PATH entry traverses a non-directory")
            component = remaining.popleft()
            current = current.parent if component == ".." else current / component


def _workspace_boundary(workspace_root: str) -> _WorkspaceBoundary:
    """Require a known workspace before deciding that any default PATH is safe."""
    try:
        if not workspace_root:
            raise ValueError("the workspace root is unset")
        path = Path(os.path.abspath(workspace_root))
        target = path.resolve(strict=True)
        info = target.stat()
        if not stat.S_ISDIR(info.st_mode) or not info.st_ino:
            raise ValueError("the workspace root is not an identifiable directory")
        return _WorkspaceBoundary((path, target), (info.st_dev, info.st_ino))
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "ACP workspace root must be an existing, inspectable directory before "
            f"resolving a default adapter: {workspace_root!r}"
        ) from exc


def _adapter_directories(boundary: _WorkspaceBoundary) -> list[str]:
    directories = []
    for directory in os.environ.get("PATH", os.defpath).split(os.pathsep):
        try:
            if not Path(directory).is_absolute():
                continue
            target = boundary.external_target(Path(directory))
            # A symlink can resolve to a directory whose name contains the PATH
            # separator. Serializing that target would create additional,
            # unchecked search entries for the adapter's interpreter.
            if target is not None and os.pathsep not in str(target) and target.is_dir():
                directories.append(str(target))
        except (OSError, RuntimeError, ValueError):
            # Missing, malformed, unreadable, or looping entries cannot establish
            # a trusted location. Keep looking so one bad entry cannot hide a
            # later trusted installation.
            continue
    return directories


def acp_adapter_path(workspace_root: str) -> str:
    """PATH for default adapters and their interpreters, excluding workspace aliases."""
    return os.pathsep.join(_adapter_directories(_workspace_boundary(workspace_root)))


def validate_acp_executable_path(executable: str, workspace_root: str) -> None:
    """Require a default adapter interpreter to stay outside the workspace.

    Check every component and symbolic-link hop, including filesystem identity
    aliases. Explicit administrator overrides do not use this default-launch
    restriction.
    """
    try:
        path = Path(executable)
        if not path.is_absolute():
            raise ValueError("the executable path is not absolute")
        if _workspace_boundary(workspace_root).external_target(path) is None:
            raise ValueError("the executable path or its target is in the workspace")
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "ACP executable must be an existing, inspectable absolute path outside "
            f"the workspace: {executable!r}. Install the adapter and its interpreter "
            "in a trusted location outside the workspace."
        ) from exc


def resolve_acp_agent_command(spec: AcpAgentSpec, *, workspace_root: Optional[str] = None) -> list[str]:
    """Resolve a preinstalled adapter independently of the process cwd.

    Never bootstrap with ``npx`` here: npm can select packages, project
    configuration, and executable paths from untrusted ancestors even when
    its cwd is a private directory. Installation and PATH are administrator
    responsibilities; neither startup nor readiness installs a package.
    """
    override = os.environ.get("NBI_ACP_AGENT_COMMAND")
    if override is not None:
        command = shlex.split(override)
        if not command or not Path(command[0]).is_absolute():
            raise ValueError("NBI_ACP_AGENT_COMMAND must name an absolute executable path")
        resolved = shutil.which(command[0])
        if resolved is None:
            raise FileNotFoundError(f"ACP adapter '{command[0]}' was not found or is not executable")
        return [resolved, *command[1:]]

    if workspace_root is None:
        from notebook_intelligence.util import get_jupyter_root_dir
        workspace_root = get_jupyter_root_dir()
    boundary = _workspace_boundary(workspace_root)

    # Check absolute candidates individually. On Windows, which(name, path=...)
    # can prepend the current directory even when PATH contains only absolute
    # entries. A candidate with a directory component bypasses that search.
    for directory in _adapter_directories(boundary):
        try:
            resolved = shutil.which(os.path.join(directory, spec.executable))
            if resolved is not None:
                target = boundary.external_target(Path(resolved))
                if target is not None:
                    return [str(target)]
        except (OSError, RuntimeError, ValueError):
            continue
    raise FileNotFoundError(
        f"ACP adapter '{spec.executable}' was not found on an absolute PATH entry outside the workspace. "
        f"Install {spec.package} in a trusted location, or set "
        "NBI_ACP_AGENT_COMMAND to its absolute executable path."
    )


def codex_approval_args(full_access: bool) -> list[str]:
    """Codex config overrides that pin its approval and sandbox posture.

    Default (``full_access`` off) forces ``approval_policy = untrusted`` so
    Codex asks before anything beyond trusted read-only commands, surfacing the
    request through NBI's per-tool confirmation. ``full_access`` (gated by the
    force-off ``acp_full_access`` admin policy) lets it run unattended. The
    flags are honored by the codex-acp binary's ``-c key=value`` override, so
    they work for both API-key and ChatGPT-auth sessions.

    Full access also pins ``sandbox_mode = workspace-write``. Codex gives a
    project with no trust entry a read-only sandbox, which is always the case
    in the isolated API-key ``CODEX_HOME``, and under ``approval_policy =
    never`` nothing can be approved past it, so without this pin full access
    refuses every edit. ``workspace-write`` limits writes to the workspace and
    temp directories and keeps outbound network access off, unless Codex's own
    config widens the sandbox.

    The overrides take precedence over Codex's user and project config, though
    not over managed config. In the API-key path NBI also isolates
    ``CODEX_HOME`` (see ``AcpAgentClient._child_env``), so the config base is
    NBI-controlled and neither the workspace nor the user's ~/.codex is read.
    With ChatGPT auth, codex uses the user's own ~/.codex; the ``-c`` pins
    still override its top-level ``approval_policy`` and ``sandbox_mode``. See
    the admin guide for the residual caveats on shared deployments.
    """
    if full_access:
        return [
            "-c", 'approval_policy="never"',
            "-c", 'sandbox_mode="workspace-write"',
        ]
    return ["-c", 'approval_policy="untrusted"']


def _codex_setting_value(value: Optional[str]) -> str:
    """A settings string reduced to what can ride in a ``-c key="..."`` override.

    Control characters are dropped rather than escaped: they are never
    legitimate in a URL or model name, an embedded NUL would make the
    subprocess launch raise outright, and a paste artifact (interior newline)
    would otherwise fail the codex launch with an opaque TOML parse error.
    Surrounding whitespace goes too, so a value that was only padding reads
    as unset.
    """
    cleaned = "".join(c for c in (value or "") if ord(c) >= 0x20 and c != "\x7f")
    return cleaned.strip()


def _codex_toml_string(value: str) -> str:
    """Quote an already-cleaned ``value`` as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def codex_model_args(acp_settings: dict) -> list[str]:
    """Codex config overrides carrying NBI's model and base-URL settings.

    Codex ignores the OPENAI_BASE_URL env var (its documented mechanism is
    the ``openai_base_url`` config key), so a custom OpenAI-compatible
    endpoint configured in NBI's ACP settings never reached the agent and
    every request went to api.openai.com (issue observed on PR #380). The
    same launch is the only place the configured chat model can be applied.

    A setting that is empty once cleaned yields no flag at all: an empty
    override is not neutral, it would blank out whatever the config file or
    codex default supplies.
    """
    args: list[str] = []
    model = _codex_setting_value(acp_settings.get("chat_model"))
    if model:
        args += ["-c", f"model={_codex_toml_string(model)}"]
    base_url = _codex_setting_value(acp_settings.get("base_url"))
    if base_url:
        args += ["-c", f"openai_base_url={_codex_toml_string(base_url)}"]
    return args


def codex_auth_args(api_key_env: str) -> list[str]:
    """Codex config overrides that stop Codex from spreading an NBI-supplied key.

    Pass the environment variable that carries the key, or "" when NBI does
    not supply one (a ChatGPT sign-in), which leaves Codex's defaults alone.
    Without these, Codex copies the key three ways:

    - codex-acp's API-key authentication writes it to ``$CODEX_HOME/auth.json``,
      and once that file exists Codex keeps using the saved key, so a changed
      key had no effect. The ephemeral store holds it in memory instead.
    - Codex snapshots the shell environment, key included, to
      ``$CODEX_HOME/shell_snapshots`` when a session starts.
    - Every command Codex runs inherits the variable, so a command (or a
      prompt that asks for one) could print it.

    This does not stop a command from reading the key where it is otherwise
    stored, such as NBI's config.json or a shell startup file that exports it.
    """
    if not api_key_env:
        return []
    return [
        "-c", 'cli_auth_credentials_store="ephemeral"',
        "-c", "features.shell_snapshot=false",
        "-c", f"shell_environment_policy.exclude=[{_codex_toml_string(api_key_env)}]",
    ]
