# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Registry of ACP agent types selectable in ACP mode (issue #378).

Kept separate from ``acp_agent`` so surfaces that only need agent metadata
(the capabilities response feeding the settings dropdown) can import it
without pulling in the ``acp`` package -- that dependency stays off the
startup path and loads only when ACP mode is actually used.

Adding an agent type is a registry entry: its adapter package, display
metadata, and how it authenticates. Agent-specific launch quirks that do not
generalize (Codex's approval-policy pin, its CODEX_HOME isolation) stay keyed
off the spec id in ``acp_agent`` rather than growing fields prematurely.
"""

import base64
import os
from dataclasses import dataclass
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
    package: str      # npx package spec that launches the ACP adapter
    icon_url: str
    api_key_env: str  # env var the agent reads its API key from
    auth_method: str  # preferred ACP auth method id when a key is present


ACP_AGENTS: dict[str, AcpAgentSpec] = {
    "codex": AcpAgentSpec(
        id="codex",
        label="Codex",
        description="OpenAI Codex (via the Agent Client Protocol)",
        package=CODEX_ACP_PACKAGE,
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


def resolve_acp_agent_command(spec: AcpAgentSpec) -> list[str]:
    """The command that launches the agent's ACP adapter.

    ``NBI_ACP_AGENT_COMMAND`` overrides (shell-split); otherwise run the
    spec's pinned package via ``npx``. Kept separate from the Claude CLI
    resolver because the adapters are npm packages, not binaries on PATH.
    """
    override = os.environ.get("NBI_ACP_AGENT_COMMAND", "").strip()
    if override:
        import shlex
        return shlex.split(override)
    return ["npx", "-y", spec.package]


def codex_approval_args(full_access: bool) -> list[str]:
    """Codex config overrides that pin its approval posture.

    Default (``full_access`` off) forces ``approval_policy = untrusted`` so
    Codex asks before anything beyond trusted read-only commands, surfacing the
    request through NBI's per-tool confirmation. ``full_access`` (gated by the
    force-off ``acp_full_access`` admin policy) lets it run unattended. The
    flag is honored by the codex-acp binary's ``-c key=value`` override, so it
    works for both API-key and ChatGPT-auth sessions.

    The override takes precedence over the codex config file. In the API-key
    path NBI also isolates ``CODEX_HOME`` (see ``AcpAgentClient._child_env``),
    so the config base is NBI-controlled and neither the workspace nor the
    user's ~/.codex is read. With ChatGPT auth, codex uses the user's own
    ~/.codex; the ``-c`` pin still overrides its top-level approval_policy.
    See the admin guide for the residual caveat on shared deployments.
    """
    policy = "never" if full_access else "untrusted"
    return ["-c", f'approval_policy="{policy}"']


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
