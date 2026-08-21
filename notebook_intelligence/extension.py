# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import asyncio
import atexit
import base64
from dataclasses import asdict, dataclass
import json
from os import path
import datetime as dt
import os
import shutil
import tempfile
import time
from typing import Optional, Union
import uuid
import threading
import logging
import tiktoken

from jupyter_server.extension.application import ExtensionApp
from jupyter_server.auth.decorator import ws_authenticated
from jupyter_server.base.handlers import APIHandler, JupyterHandler
from jupyter_server.base.websocket import WebSocketMixin
from jupyter_server.utils import url_path_join
import tornado
from tornado import websocket
from traitlets import Bool, Enum as TraitletEnum, Int, List, Unicode
from notebook_intelligence.api import CancelToken, ChatMode, ChatResponse, ChatRequest, ContextRequest, ContextRequestType, RequestDataType, RequestToolSelection, ResponseStreamData, ResponseStreamDataType, BackendMessageType, SignalImpl
from notebook_intelligence.ai_service_manager import AIServiceManager
from notebook_intelligence.cell_output import coerce_payload as _coerce_output_context, format_output_context as _format_output_context
from notebook_intelligence.feature_flags import (
    CHAT_MODEL_OVERRIDES,
    CLAUDE_CODE_TOOLS_ID,
    CLAUDE_SETTINGS_OVERRIDES,
    ACP_SETTINGS_OVERRIDES,
    INLINE_COMPLETION_MODEL_OVERRIDES,
    JUPYTER_UI_TOOLS_ID,
    POLICY_FORCE_OFF,
    POLICY_FORCE_ON,
    POLICY_USER_CHOICE,
    VALID_POLICIES,
    apply_claude_policies,
    apply_acp_policies,
    apply_string_overrides,
    is_force_off,
    is_locked,
    resolve_feature_flag,
)
from notebook_intelligence.acp_registry import ACP_AGENTS
from notebook_intelligence._claude_cli import validate_scope
from notebook_intelligence.mcp_config_validation import (
    MCPConfigValidationError,
    validate_mcp_config,
)
from notebook_intelligence.mcp_policy import (
    reject_dangerous_env_keys,
    validate_mcp_stdio_command,
)
from notebook_intelligence.claude import (
    CLAUDE_BYPASS_PERMISSION_MODE,
    ClaudeCodeChatParticipant,
    DEFAULT_PERMISSION_MODE,
    claude_bypass_disabled_by_managed_settings,
    claude_managed_default_permission_mode,
    fetch_claude_models,
    model_info_from_id,
    resolve_permission_mode,
)
from notebook_intelligence.claude_mcp_manager import ClaudeMCPManager
from notebook_intelligence.plugin_manager import PluginManager
from notebook_intelligence.tour_config import load_tour_config
from notebook_intelligence.claude_sessions import (
    NBI_CONTEXT_PREFIX,
    list_all_sessions as list_all_claude_sessions,
)
import notebook_intelligence.github_copilot as github_copilot
from notebook_intelligence.built_in_toolsets import built_in_toolsets
from notebook_intelligence.util import ThreadSafeWebSocketConnector, get_claude_config_dir, get_jupyter_root_dir, set_jupyter_root_dir, is_builtin_tool_enabled_in_env, is_provider_enabled_in_env, VALID_CODING_AGENT_LAUNCHERS, compute_effective_disabled_launchers, validate_coding_agent_launcher_ids, resolve_claude_cli_path, resolve_opencode_cli_path, resolve_pi_cli_path, resolve_copilot_cli_path, resolve_codex_cli_path, safe_anchor_uri, has_dangerous_text_codepoints, split_csv
from notebook_intelligence.context_factory import RuleContextFactory
from notebook_intelligence.skillset import SKILL_NAME_REGEX

ai_service_manager: AIServiceManager = None
log = logging.getLogger(__name__)
tiktoken_encoding = tiktoken.encoding_for_model('gpt-4o')
thread_safe_websocket_connector: ThreadSafeWebSocketConnector = None


def _token_count(text: str) -> int:
    return len(tiktoken_encoding.encode(text))


def _truncate_context_content(content: str, token_budget: int) -> str:
    if token_budget <= 0 or content == '':
        return ''

    encoded = tiktoken_encoding.encode(content)
    if len(encoded) <= token_budget:
        return content

    truncated = tiktoken_encoding.decode(encoded[:token_budget]).rstrip()
    if truncated == '':
        return ''

    return truncated + "\n...[truncated]"


def _build_additional_context_message(
    file_path: str,
    context_filename: str,
    start_line: int,
    end_line: int,
    context_content: str,
    current_cell_context: str = ''
) -> str:
    message = (
        f"This file was provided as additional context: '{context_filename}' "
        f"at path '{file_path}', lines: {start_line} - {end_line}."
    )

    if current_cell_context:
        message += f" {current_cell_context}"

    if context_content != '':
        message += f"\n\nFile contents:\n```\n{context_content}\n```"

    return message

def _build_cell_output_features_response(
    explain_error_policy: str,
    output_followup_policy: str,
    output_toolbar_policy: str,
    nbi_config,
) -> dict:
    explain_enabled, explain_locked = resolve_feature_flag(
        explain_error_policy, nbi_config.enable_explain_error
    )
    followup_enabled, followup_locked = resolve_feature_flag(
        output_followup_policy, nbi_config.enable_output_followup
    )
    toolbar_enabled, toolbar_locked = resolve_feature_flag(
        output_toolbar_policy, nbi_config.enable_output_toolbar
    )
    return {
        "explain_error": {"enabled": explain_enabled, "locked": explain_locked},
        "output_followup": {
            "enabled": followup_enabled,
            "locked": followup_locked,
        },
        "output_toolbar": {
            "enabled": toolbar_enabled,
            "locked": toolbar_locked,
        },
    }


def _resolve_supports_vision(ai_service_manager) -> bool:
    """Whether the active chat model can render images.

    In Claude Code mode the active model is Claude (always vision-capable),
    not ``ai_service_manager.chat_model`` (which still reflects the user's
    most recent non-Claude selection). Fall through to the regular chat
    model's capability otherwise.
    """
    if ai_service_manager.is_claude_code_mode:
        return True
    chat_model = ai_service_manager.chat_model
    return chat_model.supports_vision if chat_model is not None else False


def _resolve_context_token_limit(ai_service_manager) -> int:
    """Token budget source for attachment/output context.

    In Claude Code mode the active model is Claude, not
    ``ai_service_manager.chat_model`` — that property still reflects the
    user's most recent non-Claude provider selection and is ``None`` on a
    Claude-only setup. Falling through to the legacy 100-token floor in
    that state shrank the context budget to 80 tokens, which silently
    dropped cell-output attachments and truncated every attachment after
    the first. Resolve the budget from the configured Claude model
    instead (``model_info_from_id`` falls back to a 200K window for
    unknown or default model ids).
    """
    if ai_service_manager.is_claude_code_mode:
        model_id = ai_service_manager.nbi_config.claude_settings.get('chat_model', '')
        model_id = model_id.strip() if isinstance(model_id, str) else ''
        return model_info_from_id(model_id)["context_window"]
    chat_model = ai_service_manager.chat_model
    return 100 if chat_model is None else chat_model.context_window


def _resolve_policy_with_env(env_var_name: str, traitlet_value: str) -> str:
    """Resolve a feature policy: env var wins if valid, else traitlet.

    Raises ValueError on unrecognized env values so a typo can't silently
    relax a force-off gate. Matches the polarity of `_resolve_bool_with_env`.
    """
    env_value = os.environ.get(env_var_name, "").strip()
    if not env_value:
        return traitlet_value
    if env_value in VALID_POLICIES:
        return env_value
    raise ValueError(
        f"Invalid {env_var_name}={env_value!r}: "
        f"must be one of {', '.join(VALID_POLICIES)}"
    )


_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})
_BOOL_ENV_VOCAB = "true, false, 1, 0, yes, no, on, off"


def _resolve_skills_manifest_sources(traitlet_value: str) -> list:
    """Resolve the multi-manifest wire format: env var wins, otherwise traitlet.

    `NBI_SKILLS_MANIFEST` and the matching traitlet both accept either a
    single source (URL or filesystem path) or a comma-separated list of
    either. Whitespace is stripped, empty fragments are dropped, so an
    operator who writes ``"  ,,,  "`` (or leaves the env empty) lands on
    "no manifests configured" rather than constructing phantom entries.
    Extracted so the resolver can be unit-tested independent of the
    extension-class instantiation harness.
    """
    raw = (
        os.environ.get("NBI_SKILLS_MANIFEST", "").strip()
        or (traitlet_value or "").strip()
    )
    return split_csv(raw)


def _resolve_csv_appended(env_var_name: str, traitlet_value):
    """Merge a traitlet List with the comma-separated env-var value (append).

    Env *adds to* the traitlet list rather than replacing it — the use case
    is per-pod profiles layering on an org-wide baseline. Tokens are split
    via the shared ``util.split_csv`` helper and exact duplicates are
    collapsed while preserving first-seen order.
    """
    base = list(traitlet_value or [])
    extras = split_csv(os.environ.get(env_var_name, ""))
    return list(dict.fromkeys(base + extras))


def _resolve_bool_with_env(env_var_name: str, fallback: bool | None) -> bool:
    """Resolve a boolean admin gate: env var wins if recognized, else fallback.

    Raises ValueError when the env var is set but unrecognized — silent
    fall-back can flip a security gate either direction depending on the
    fallback polarity, so a typo must surface at startup. ``None`` fallback
    is coerced to ``False``.
    """
    env_value = os.environ.get(env_var_name, "").strip().lower()
    if not env_value:
        return bool(fallback)
    if env_value in _TRUE_VALUES:
        return True
    if env_value in _FALSE_VALUES:
        return False
    raise ValueError(
        f"Invalid {env_var_name}={env_value!r}: must be one of {_BOOL_ENV_VOCAB}"
    )


def _resolve_positive_int_with_env(env_var_name: str, traitlet_value: int) -> int:
    """Resolve a non-negative int tunable, falling back to the traitlet.

    Unlike ``_resolve_bool_with_env`` this warns-and-clamps rather than
    raising: int tunables are tuning parameters (size caps, intervals,
    retention windows), and a typo in one is unambiguously "off-ish"
    rather than security-gate-flipping. Negative values are clamped to
    0 with a warning so callers see "feature disabled" rather than
    silently treating the bad input as a positive cap.
    """
    env_value = os.environ.get(env_var_name, "").strip()
    resolved = traitlet_value
    if env_value:
        try:
            resolved = int(env_value)
        except ValueError:
            log.warning(
                "Ignoring invalid %s=%r: must be a non-negative integer",
                env_var_name,
                env_value,
            )
            resolved = traitlet_value
    if resolved < 0:
        log.warning(
            "%s resolved to a negative value (%d); clamping to 0",
            env_var_name,
            resolved,
        )
        return 0
    return resolved


# Single source of truth for the boolean policies. Each entry is
# ``(policy_name, env_var, traitlet_attr)``. Drives env-var resolution, the
# capabilities response, and the lock-rejection set in ConfigHandler.
#
# Adding a new policy requires updates in *seven* places — keep them all in
# sync or the policy will silently no-op:
#   1. A new tuple entry below.
#   2. A ``TraitletEnum`` declaration on ``NotebookIntelligence`` further
#      down in this file.
#   3. A ``user_values`` entry in ``_build_feature_policies_response``
#      (admin-only gates use ``True``).
#   4. (For backend gates) An ``is_force_off`` call in ``_setup_handlers``
#      that sets the resolved bool on the handler class.
#   5. A row in the Admin policies table in ``README.md``.
#   6. A section in ``docs/admin-guide.md``.
#   7. ``FeaturePolicyName`` union + the ``names`` array in
#      ``src/api.ts`` ``featurePolicies``. Admin-only gates also need an
#      entry in the ``defaultOpen`` set if they should default visible.
FEATURE_POLICY_SPEC = (
    ("explain_error", "NBI_EXPLAIN_ERROR_POLICY", "explain_error_policy"),
    ("output_followup", "NBI_OUTPUT_FOLLOWUP_POLICY", "output_followup_policy"),
    ("output_toolbar", "NBI_OUTPUT_TOOLBAR_POLICY", "output_toolbar_policy"),
    ("claude_mode", "NBI_CLAUDE_MODE_POLICY", "claude_mode_policy"),
    ("acp_mode", "NBI_ACP_MODE_POLICY", "acp_mode_policy"),
    (
        "acp_full_access",
        "NBI_ACP_FULL_ACCESS_POLICY",
        "acp_full_access_policy",
    ),
    (
        "claude_continue_conversation",
        "NBI_CLAUDE_CONTINUE_CONVERSATION_POLICY",
        "claude_continue_conversation_policy",
    ),
    (
        "claude_code_tools",
        "NBI_CLAUDE_CODE_TOOLS_POLICY",
        "claude_code_tools_policy",
    ),
    (
        "claude_jupyter_ui_tools",
        "NBI_CLAUDE_JUPYTER_UI_TOOLS_POLICY",
        "claude_jupyter_ui_tools_policy",
    ),
    (
        "claude_setting_source_user",
        "NBI_CLAUDE_SETTING_SOURCE_USER_POLICY",
        "claude_setting_source_user_policy",
    ),
    (
        "claude_setting_source_project",
        "NBI_CLAUDE_SETTING_SOURCE_PROJECT_POLICY",
        "claude_setting_source_project_policy",
    ),
    (
        "store_github_access_token",
        "NBI_STORE_GITHUB_ACCESS_TOKEN_POLICY",
        "store_github_access_token_policy",
    ),
    (
        "skills_management",
        "NBI_SKILLS_MANAGEMENT_POLICY",
        "skills_management_policy",
    ),
    (
        "claude_mcp_management",
        "NBI_CLAUDE_MCP_MANAGEMENT_POLICY",
        "claude_mcp_management_policy",
    ),
    (
        "claude_plugins_management",
        "NBI_CLAUDE_PLUGINS_MANAGEMENT_POLICY",
        "claude_plugins_management_policy",
    ),
    (
        "claude_bypass_permissions",
        "NBI_CLAUDE_BYPASS_PERMISSIONS_POLICY",
        "claude_bypass_permissions_policy",
    ),
    (
        "terminal_drag_drop",
        "NBI_TERMINAL_DRAG_DROP_POLICY",
        "terminal_drag_drop_policy",
    ),
    (
        "refresh_open_files_on_disk_change",
        "NBI_REFRESH_OPEN_FILES_ON_DISK_CHANGE_POLICY",
        "refresh_open_files_on_disk_change_policy",
    ),
)
FEATURE_POLICY_NAMES = tuple(name for name, _, _ in FEATURE_POLICY_SPEC)

# Fallback used when a policies dict hasn't been populated (handler classes
# before _setup_handlers, direct construction in tests). Everything defaults
# to user-choice except bypass, the experimental ACP mode, and ACP full
# access, which must fail closed.
FEATURE_POLICY_DEFAULTS = {name: POLICY_USER_CHOICE for name in FEATURE_POLICY_NAMES}
FEATURE_POLICY_DEFAULTS["claude_bypass_permissions"] = POLICY_FORCE_OFF
FEATURE_POLICY_DEFAULTS["acp_mode"] = POLICY_FORCE_OFF
FEATURE_POLICY_DEFAULTS["acp_full_access"] = POLICY_FORCE_OFF

# ``(setting_lock_name, env_var)`` pairs for the value-presence-locks. The
# claude_api_key entry maps to ANTHROPIC_API_KEY (the SDK's native convention)
# rather than an NBI-prefixed env var. Same for claude_base_url. The acp_*
# entries follow the same idea against OpenAI's native env vars (the Codex
# agent's convention; the default ACP agent).
STRING_OVERRIDE_SPEC = (
    ("chat_model_provider", "NBI_CHAT_MODEL_PROVIDER"),
    ("chat_model_id", "NBI_CHAT_MODEL_ID"),
    ("inline_completion_model_provider", "NBI_INLINE_COMPLETION_MODEL_PROVIDER"),
    ("inline_completion_model_id", "NBI_INLINE_COMPLETION_MODEL_ID"),
    ("claude_chat_model", "NBI_CLAUDE_CHAT_MODEL"),
    ("claude_inline_completion_model", "NBI_CLAUDE_INLINE_COMPLETION_MODEL"),
    ("claude_api_key", "ANTHROPIC_API_KEY"),
    ("claude_base_url", "ANTHROPIC_BASE_URL"),
    ("acp_chat_model", "NBI_ACP_CHAT_MODEL"),
    ("acp_api_key", "OPENAI_API_KEY"),
    ("acp_base_url", "OPENAI_BASE_URL"),
)
SETTING_LOCK_NAMES = tuple(name for name, _ in STRING_OVERRIDE_SPEC)


def _build_feature_policies_response(policies: dict, nbi_config) -> dict:
    """Resolve every boolean policy against the user's stored config.

    Returns ``{name: {enabled, locked}}`` for every name in
    FEATURE_POLICY_NAMES. The frontend iterates this dict, so adding a new
    feature only needs an entry here plus a matching env-var resolution.
    """
    claude_settings = nbi_config.claude_settings or {}
    acp_settings = nbi_config.acp_settings or {}
    tools = claude_settings.get("tools") or []
    sources = claude_settings.get("setting_sources") or []

    user_values = {
        "explain_error": nbi_config.enable_explain_error,
        "output_followup": nbi_config.enable_output_followup,
        "output_toolbar": nbi_config.enable_output_toolbar,
        "claude_mode": bool(claude_settings.get("enabled", False)),
        # Experimental ACP agent mode; defaults to force-off (#378).
        "acp_mode": bool(acp_settings.get("enabled", False)),
        # ACP agent autonomous "full access" posture; defaults to force-off
        # so the agent asks before risky actions unless an admin opts in
        # (#378).
        "acp_full_access": bool(acp_settings.get("full_access", False)),
        "claude_continue_conversation": bool(
            claude_settings.get("continue_conversation", False)
        ),
        "claude_code_tools": CLAUDE_CODE_TOOLS_ID in tools,
        "claude_jupyter_ui_tools": JUPYTER_UI_TOOLS_ID in tools,
        "claude_setting_source_user": "user" in sources,
        "claude_setting_source_project": "project" in sources,
        "store_github_access_token": bool(nbi_config.store_github_access_token),
        # Admin-only gates; user has no toggle, so user_value is always
        # True. The policy resolver still applies force-off / force-on
        # correctly so admins can flip them. The frontend keys off
        # `locked && !enabled` to know when to hide the feature.
        "skills_management": True,
        "claude_mcp_management": True,
        "claude_plugins_management": True,
        # Gates only whether the Bypass Permissions option is offered in
        # the permission-mode selector; the user still arms it per
        # session. force-on grants the same availability as user-choice
        # and never auto-arms bypass. Defaults to force-off, like the ACP
        # mode and full-access policies.
        "claude_bypass_permissions": True,
        "terminal_drag_drop": True,
        "refresh_open_files_on_disk_change": nbi_config.refresh_open_files_on_disk_change,
    }

    response = {}
    for name in FEATURE_POLICY_NAMES:
        enabled, locked = resolve_feature_flag(
            policies.get(name, FEATURE_POLICY_DEFAULTS[name]), user_values[name]
        )
        response[name] = {"enabled": enabled, "locked": locked}

    # Defense in depth: Claude Code's enterprise managed settings can
    # disable bypass independently of the NBI policy. Fold that into the
    # one answer the frontend reads so the selector and the server's
    # request clamp can't disagree.
    if claude_bypass_disabled_by_managed_settings():
        response["claude_bypass_permissions"] = {"enabled": False, "locked": True}
    return response


def _resolve_default_permission_mode() -> str:
    """Starting permission mode for the selector in a new Claude session.

    Honors managed settings' ``permissions.defaultMode`` except for bypass,
    which never applies without the user arming it explicitly, no matter
    who asks; a managed default of bypass collapses to default the same
    way the reconnect reset does.
    """
    managed_default = claude_managed_default_permission_mode()
    if managed_default is None or managed_default == CLAUDE_BYPASS_PERMISSION_MODE:
        return DEFAULT_PERMISSION_MODE
    return managed_default


def _build_setting_locks_response(string_overrides: dict) -> dict:
    """Surface lock state for non-boolean settings (model pickers, API key, base URL).

    The values themselves are still served through their existing capabilities
    fields (chat_model, claude_settings, ...). This dict only carries the
    locked flag so the frontend knows which inputs to disable.
    """
    return {
        name: {"locked": bool(string_overrides.get(name))}
        for name in SETTING_LOCK_NAMES
    }


def _scrub_credentials_for_wire(
    settings: dict, string_overrides: dict, api_key_lock: str = "claude_api_key"
) -> dict:
    """Strip the api_key from the capabilities response when locked by env.

    The SDKs read their key env var directly (ANTHROPIC_API_KEY for Claude,
    OPENAI_API_KEY for the Codex ACP agent); surfacing the value through the frontend would
    leak the credential.
    """
    if not string_overrides.get(api_key_lock):
        return settings
    result = dict(settings or {})
    result["api_key"] = ""
    return result


def _read_claude_spinner_verbs() -> Optional[dict]:
    settings_path = os.path.join(get_claude_config_dir(), 'settings.json')
    try:
        with open(settings_path) as f:
            data = json.load(f)
        sv = data.get('spinnerVerbs')
        if (
            isinstance(sv, dict)
            and sv.get('mode') == 'replace'
            and isinstance(sv.get('verbs'), list)
            and sv['verbs']
            and all(isinstance(v, str) for v in sv['verbs'])
        ):
            return sv
        return None
    except Exception:
        return None


class GetCapabilitiesHandler(APIHandler):
    disabled_tools = []
    allow_enabling_tools_with_env = False
    disabled_providers = []
    allow_enabling_providers_with_env = False
    disabled_coding_agent_launchers = []
    allow_enabling_coding_agent_launchers_with_env = False
    enable_chat_feedback = False
    enable_chat_feedback_always_visible = False
    additional_skipped_workspace_directories = []
    feature_policies = {}
    string_overrides = {}
    # Resolved at extension init from NBI_TOUR_CONFIG_PATH (or the
    # tour_config_path traitlet). Empty string disables the override.
    tour_config_path = ""

    @tornado.web.authenticated
    def get(self):
        ai_service_manager.nbi_config.load()
        ai_service_manager.update_models_from_config()
        nbi_config = ai_service_manager.nbi_config
        def is_tool_enabled(tool: str) -> bool:
            if self.disabled_tools is None:
                return True
            return tool not in self.disabled_tools or (self.allow_enabling_tools_with_env and is_builtin_tool_enabled_in_env(tool))
        def is_provider_enabled(provider_id: str) -> bool:
            if self.disabled_providers is None:
                return True
            return provider_id not in self.disabled_providers or \
                   (self.allow_enabling_providers_with_env and is_provider_enabled_in_env(provider_id))
        # Frontend gets the resolved set (denylist plus the per-pod re-enable
        # env, gated by the explicit opt-in flag). Computed once per request
        # in `util.compute_effective_disabled_launchers` so tests can pin the
        # contract without re-implementing it.
        effective_disabled_launchers = compute_effective_disabled_launchers(
            self.disabled_coding_agent_launchers,
            self.allow_enabling_coding_agent_launchers_with_env,
        )
        allowed_builtin_toolsets = [{"id": toolset.id, "name": toolset.name, "description": toolset.description} for toolset in built_in_toolsets.values() if is_tool_enabled(toolset.id)]
        llm_providers = [p for p in ai_service_manager.llm_providers.values() if is_provider_enabled(p.id)]
        mcp_servers = ai_service_manager.get_mcp_servers()
        mcp_server_tools = [{
            "id": mcp_server.name,
            "status": mcp_server.status,
            "tools": [{"name": tool.name, "description": tool.description} for tool in mcp_server.get_tools()],
            "prompts": [{"name": prompt.name, "description": prompt.description, "arguments": [{"name": argument.name, "description": argument.description, "required": argument.required} for argument in prompt.arguments]} for prompt in mcp_server.get_prompts()]
        } for mcp_server in mcp_servers]
        # sort by server id
        mcp_server_tools.sort(key=lambda server: server["id"])

        extensions = []
        for extension_id, toolsets in ai_service_manager.get_extension_toolsets().items():
            ts = []
            for toolset in toolsets:
                tools = []
                for tool in toolset.tools:
                    tools.append({"name": tool.name, "description": tool.description})
                # sort by tool name
                tools.sort(key=lambda tool: tool["name"])
                ts.append({
                    "id": toolset.id,
                    "name": toolset.name,
                    "description": toolset.description,
                    "tools": tools
                })
            # sort by toolset name
            ts.sort(key=lambda toolset: toolset["name"])
            extension = ai_service_manager.get_extension(extension_id)
            extensions.append({
                "id": extension_id,
                "name": extension.name,
                "toolsets": ts
            })
        # sort by extension id
        extensions.sort(key=lambda extension: extension["id"])

        response = {
            "user_home_dir": os.path.expanduser('~'),
            "nbi_user_config_dir": nbi_config.nbi_user_dir,
            "using_github_copilot_service": nbi_config.using_github_copilot_service,
            "llm_providers": [{"id": provider.id, "name": provider.name} for provider in llm_providers],
            "chat_models": ai_service_manager.chat_model_ids,
            "inline_completion_models": ai_service_manager.inline_completion_model_ids,
            "embedding_models": ai_service_manager.embedding_model_ids,
            "chat_model": nbi_config.chat_model,
            "chat_model_supports_vision": _resolve_supports_vision(
                ai_service_manager
            ),
            "inline_completion_model": nbi_config.inline_completion_model,
            "embedding_model": nbi_config.embedding_model,
            "chat_participants": [],
            "store_github_access_token": nbi_config.store_github_access_token,
            "inline_completion_debouncer_delay": nbi_config.inline_completion_debouncer_delay,
            "tool_config": {
                "builtinToolsets": allowed_builtin_toolsets,
                "mcpServers": mcp_server_tools,
                "extensions": extensions
            },
            "mcp_server_settings": nbi_config.mcp_server_settings,
            "claude_settings": _scrub_credentials_for_wire(
                nbi_config.claude_settings, self.string_overrides
            ),
            "acp_settings": _scrub_credentials_for_wire(
                nbi_config.acp_settings, self.string_overrides, "acp_api_key"
            ),
            # The agent types selectable in ACP mode (settings dropdown).
            "acp_agents": [
                {"id": spec.id, "label": spec.label}
                for spec in ACP_AGENTS.values()
            ],
            # The single agent mode handling chat ("claude" / "acp" / None);
            # Claude mode and ACP mode are mutually exclusive (#378).
            "active_agent_mode": ai_service_manager.active_agent_mode,
            "spinner_verbs": _read_claude_spinner_verbs(),
            "claude_models": ai_service_manager.claude_models,
            # Drive launcher-tile visibility (issues #183, #260). Each flag
            # gates one tile under the "Coding Agent" category. Detection is
            # PATH-based with NBI_*_CLI_PATH env overrides.
            "claude_cli_available": resolve_claude_cli_path() is not None,
            "opencode_cli_available": resolve_opencode_cli_path() is not None,
            "pi_cli_available": resolve_pi_cli_path() is not None,
            "github_copilot_cli_available": resolve_copilot_cli_path() is not None,
            "codex_cli_available": resolve_codex_cli_path() is not None,
            "disabled_coding_agent_launchers": effective_disabled_launchers,
            "default_chat_mode": nbi_config.default_chat_mode,
            "chat_feedback_enabled": self.enable_chat_feedback,
            "chat_feedback_always_visible": self.enable_chat_feedback_always_visible,
            # Single source of truth lives on each domain's base handler so
            # `_setup_handlers` only writes one site per flag.
            "allow_github_skill_import": SkillsBaseHandler.allow_github_skill_import,
            "additional_skipped_workspace_directories": self.additional_skipped_workspace_directories,
            "allow_github_plugin_import": PluginsBaseHandler.allow_github_plugin_import,
            "cell_output_features": _build_cell_output_features_response(
                self.feature_policies.get("explain_error", POLICY_USER_CHOICE),
                self.feature_policies.get("output_followup", POLICY_USER_CHOICE),
                self.feature_policies.get("output_toolbar", POLICY_USER_CHOICE),
                nbi_config,
            ),
            "feature_policies": _build_feature_policies_response(
                self.feature_policies, nbi_config
            ),
            "setting_locks": _build_setting_locks_response(self.string_overrides),
            # Starting mode for the permission-mode selector: managed
            # settings' permissions.defaultMode when present and valid,
            # else "default". Bypass never starts armed regardless of
            # managed settings or policy, so it collapses to "default".
            "claude_permission_default_mode": _resolve_default_permission_mode(),
        }
        for participant_id in ai_service_manager.chat_participants:
            participant = ai_service_manager.chat_participants[participant_id]
            # prevent duplicate participants
            if participant.id in [p["id"] for p in response["chat_participants"]]:
                continue
            response["chat_participants"].append({
                "id": participant.id,
                "name": participant.name,
                "description": participant.description,
                "iconPath": participant.icon_path,
                "commands": [command.name for command in participant.commands]
            })

        # Admin tour copy overrides. The loader fails closed: a missing,
        # oversized, or malformed file returns {} and the frontend
        # renders the built-in defaults. Reading on every capabilities
        # call (rather than caching) lets an admin edit the file without
        # restarting Jupyter; the file is small and the path is usually
        # unset, so the cost is at most one stat call per request. The
        # path itself is pre-resolved at initialize_handlers time.
        response["tour_overrides"] = load_tour_config(self.tour_config_path)

        self.finish(json.dumps(response))

class ConfigHandler(APIHandler):
    feature_policies = {}
    string_overrides = {}

    @tornado.web.authenticated
    def post(self):
        data = json.loads(self.request.body)
        valid_keys = set([
            "default_chat_mode",
            "chat_model",
            "inline_completion_model",
            "store_github_access_token",
            "inline_completion_debouncer_delay",
            "mcp_server_settings",
            "claude_settings",
            "acp_settings",
            "enable_explain_error",
            "enable_output_followup",
            "enable_output_toolbar",
            "refresh_open_files_on_disk_change",
        ])
        # Top-level keys whose write is rejected outright when locked.
        locked_keys = set()
        if is_locked(self.feature_policies.get("explain_error", POLICY_USER_CHOICE)):
            locked_keys.add("enable_explain_error")
        if is_locked(self.feature_policies.get("output_followup", POLICY_USER_CHOICE)):
            locked_keys.add("enable_output_followup")
        if is_locked(self.feature_policies.get("output_toolbar", POLICY_USER_CHOICE)):
            locked_keys.add("enable_output_toolbar")
        if is_locked(self.feature_policies.get("store_github_access_token", POLICY_USER_CHOICE)):
            locked_keys.add("store_github_access_token")
        if is_locked(self.feature_policies.get("refresh_open_files_on_disk_change", POLICY_USER_CHOICE)):
            locked_keys.add("refresh_open_files_on_disk_change")
        # chat_model / inline_completion_model are locked when *either* of their
        # provider/id env vars is set; the resolver below preserves the locked
        # subfield so a user can still update the unlocked one.
        chat_model_locked = bool(
            self.string_overrides.get("chat_model_provider")
            or self.string_overrides.get("chat_model_id")
        )
        inline_model_locked = bool(
            self.string_overrides.get("inline_completion_model_provider")
            or self.string_overrides.get("inline_completion_model_id")
        )

        has_model_change = False
        has_claude_settings_change = False
        has_acp_settings_change = False
        # Captured before the loop so the exclusivity check below can tell
        # which mode this POST newly enabled.
        prior_claude_enabled = bool(
            (ai_service_manager.nbi_config.get("claude_settings") or {}).get("enabled", False)
        )
        prior_acp_enabled = bool(
            (ai_service_manager.nbi_config.get("acp_settings") or {}).get("enabled", False)
        )
        for key in data:
            if key in locked_keys:
                continue
            if key not in valid_keys:
                continue
            value = data[key]
            # Re-apply the env override after the user's POST so locked fields
            # stay pinned; non-locked fields keep the user's value.
            if key == "chat_model":
                value = apply_string_overrides(
                    value, self.string_overrides, CHAT_MODEL_OVERRIDES
                )
                if chat_model_locked and value == ai_service_manager.nbi_config.chat_model:
                    continue
                has_model_change = True
            elif key == "inline_completion_model":
                value = apply_string_overrides(
                    value, self.string_overrides, INLINE_COMPLETION_MODEL_OVERRIDES
                )
                if (
                    inline_model_locked
                    and value == ai_service_manager.nbi_config.inline_completion_model
                ):
                    continue
                has_model_change = True
            elif key == "claude_settings":
                value = apply_claude_policies(value, self.feature_policies)
                value = apply_string_overrides(
                    value, self.string_overrides, CLAUDE_SETTINGS_OVERRIDES
                )
                # ANTHROPIC_API_KEY is a credential; don't persist it to
                # config.json. The SDK reads it from process env directly when
                # claude_settings.api_key is empty.
                if self.string_overrides.get("claude_api_key"):
                    value = dict(value)
                    value["api_key"] = ""
            elif key == "acp_settings":
                value = apply_acp_policies(value, self.feature_policies)
                value = apply_string_overrides(
                    value, self.string_overrides, ACP_SETTINGS_OVERRIDES
                )
                # OPENAI_API_KEY is a credential; same handling as Claude above.
                if self.string_overrides.get("acp_api_key"):
                    value = dict(value)
                    value["api_key"] = ""
                # Only act when something actually changed. The settings tab
                # re-POSTs on mount, and an unconditional restart would bounce
                # the live ACP subprocess every time the tab is opened. Compare
                # against the raw stored value (not the acp_settings property,
                # which re-injects env overrides such as OPENAI_API_KEY and would
                # never match the scrubbed value we persist).
                has_acp_settings_change = (
                    value != (ai_service_manager.nbi_config.get("acp_settings") or {})
                )
            ai_service_manager.nbi_config.set(key, value)
            if key == "store_github_access_token":
                if value:
                    github_copilot.store_github_access_token()
                else:
                    github_copilot.delete_stored_github_access_token()
            elif key == "mcp_server_settings":
                disabled_mcp_servers = []
                for server_id in value:
                    server_settings = value[server_id]
                    if server_settings.get("disabled") == True:
                        disabled_mcp_servers.append(server_id)
                ai_service_manager.update_mcp_server_connections(disabled_mcp_servers)
            elif key == "claude_settings":
                has_claude_settings_change = True
                default_chat_participant = ai_service_manager.default_chat_participant
                if isinstance(default_chat_participant, ClaudeCodeChatParticipant):
                    # needed to disconnect
                    default_chat_participant.update_client_debounced()

        # Claude mode and ACP mode are mutually exclusive: turning one on
        # turns the other off, most-recent selection wins. Enforced here at
        # the settings boundary so every client sees the same resolution
        # (the frontend toggles merely reflect it after a config refresh).
        claude_enabled = bool(
            (ai_service_manager.nbi_config.get("claude_settings") or {}).get("enabled", False)
        )
        acp_enabled = bool(
            (ai_service_manager.nbi_config.get("acp_settings") or {}).get("enabled", False)
        )
        if claude_enabled and acp_enabled:
            # ACP wins only when this POST newly enabled it and did not also
            # newly enable Claude; every tie (both newly on, or a hand-edited
            # config with both on) goes to Claude, matching AGENT_MODE_PRIORITY.
            acp_newly_enabled = acp_enabled and not prior_acp_enabled
            claude_newly_enabled = claude_enabled and not prior_claude_enabled
            if acp_newly_enabled and not claude_newly_enabled:
                claude_settings = dict(ai_service_manager.nbi_config.get("claude_settings") or {})
                claude_settings["enabled"] = False
                ai_service_manager.nbi_config.set("claude_settings", claude_settings)
                has_claude_settings_change = True
                # Disconnect the live Claude client now: after
                # update_models_from_config below, the default participant is
                # the ACP one, so the isinstance-gated disconnect at the end
                # of this handler would silently skip and leave the Claude
                # SDK subprocess running alongside the ACP agent.
                default_chat_participant = ai_service_manager.default_chat_participant
                if isinstance(default_chat_participant, ClaudeCodeChatParticipant):
                    default_chat_participant.update_client_debounced()
            else:
                acp_settings = dict(ai_service_manager.nbi_config.get("acp_settings") or {})
                acp_settings["enabled"] = False
                ai_service_manager.nbi_config.set("acp_settings", acp_settings)
                has_acp_settings_change = True

        ai_service_manager.nbi_config.save()
        if (
            has_model_change
            or has_claude_settings_change
            or has_acp_settings_change
        ):
            ai_service_manager.update_models_from_config()
        if has_claude_settings_change:
            default_chat_participant = ai_service_manager.default_chat_participant
            if isinstance(default_chat_participant, ClaudeCodeChatParticipant):
                # needed to reconnect / update
                default_chat_participant.update_client_debounced()
        if has_acp_settings_change:
            # The agent reads its key/model from acp_settings at launch, so the
            # running ACP subprocess must restart to pick up the new values.
            ai_service_manager.restart_acp_client()

        self.finish(json.dumps({}))

class UpdateProviderModelsHandler(APIHandler):
    @tornado.web.authenticated
    def post(self):
        data = json.loads(self.request.body)
        if data.get("provider") == "ollama":
            ai_service_manager.ollama_llm_provider.update_chat_model_list()
        elif data.get("provider") == "claude":
            claude_settings = ai_service_manager.nbi_config.claude_settings
            fetch_claude_models(
                api_key=claude_settings.get('api_key', None),
                base_url=claude_settings.get('base_url', None)
            )
        self.finish(json.dumps({}))

class MCPConfigFileHandler(APIHandler):
    @tornado.web.authenticated
    def get(self):
        ai_service_manager.nbi_config.load()
        mcp_config = ai_service_manager.nbi_config.mcp.copy()
        if "mcpServers" not in mcp_config:
            mcp_config["mcpServers"] = {}
        self.finish(json.dumps(mcp_config))

    @tornado.web.authenticated
    def post(self):
        try:
            data = json.loads(self.request.body)
        except json.JSONDecodeError as exc:
            # Surface the parse error via 400 rather than crashing
            # downstream code with a confusing AttributeError when the
            # JSON loader returns a primitive instead of a dict.
            self.set_status(400)
            self.finish(json.dumps({"status": "error", "message": f"Invalid JSON: {exc}"}))
            return
        try:
            validate_mcp_config(data)
        except MCPConfigValidationError as exc:
            # Schema rejection: refuse the write entirely so a malformed
            # payload cannot persist to disk or install destructive
            # servers on the next reconcile.
            self.set_status(400)
            self.finish(json.dumps({"status": "error", "message": str(exc)}))
            return
        try:
            # Validate stdio entries against the same admin allowlist
            # that the in-process loader uses, so a rejected entry
            # cannot persist to disk and re-trigger the load-time warn
            # on every restart. Apply the same env-key denylist that
            # blocks PATH / LD_PRELOAD / etc. bypasses.
            allowlist = ai_service_manager.get_mcp_stdio_command_allowlist()
            servers = data.get("mcpServers") if isinstance(data, dict) else None
            if isinstance(servers, dict):
                for name, server in servers.items():
                    if not isinstance(server, dict) or "command" not in server:
                        continue
                    validate_mcp_stdio_command(server.get("command", ""), allowlist)
                    reject_dangerous_env_keys(server.get("env"))
            ai_service_manager.nbi_config.user_mcp = data
            ai_service_manager.nbi_config.save()
            ai_service_manager.nbi_config.load()
            ai_service_manager.update_mcp_servers()
            self.finish(json.dumps({"status": "ok"}))
        except ValueError as exc:
            # Policy rejection: surface as HTTP 400 so the Settings UI
            # shows the operator's policy message instead of a generic
            # 500. The body still uses the {status, message} envelope
            # the frontend already parses.
            self.set_status(400)
            self.finish(json.dumps({"status": "error", "message": str(exc)}))
            return
        except Exception as e:
            self.set_status(500)
            self.finish(json.dumps({"status": "error", "message": str(e)}))
            return

class ReloadMCPServersHandler(APIHandler):
    @tornado.web.authenticated
    def post(self):
        ai_service_manager.nbi_config.load()
        ai_service_manager.update_mcp_servers()
        self.finish(json.dumps({
            "mcpServers": [{"id": server.name} for server in ai_service_manager.get_mcp_servers()]
        }))

class EmitTelemetryEventHandler(APIHandler):
    @tornado.web.authenticated
    def post(self):
        event = json.loads(self.request.body)
        log.debug(f"Telemetry event received: type={event.get('type')}, data={json.dumps(event.get('data', {}))}")
        thread = threading.Thread(target=asyncio.run, args=(ai_service_manager.emit_telemetry_event(event),))
        thread.start()
        self.finish(json.dumps({}))

class GetGitHubLoginStatusHandler(APIHandler):
    # The following decorator should be present on all verb methods (head, get, post,
    # patch, put, delete, options) to ensure only authorized user can request the
    # Jupyter server
    @tornado.web.authenticated
    def get(self):
        self.finish(json.dumps(github_copilot.get_login_status()))

class PostGitHubLoginHandler(APIHandler):
    @tornado.web.authenticated
    def post(self):
        device_verification_info = github_copilot.login()
        if device_verification_info is None:
            self.set_status(500)
            self.finish(json.dumps({
                "error": "Failed to get device verification info from GitHub Copilot"
            }))
            return
        self.finish(json.dumps(device_verification_info))

class GetGitHubLogoutHandler(APIHandler):
    @tornado.web.authenticated
    def get(self):
        self.finish(json.dumps(github_copilot.logout()))

class RulesListHandler(APIHandler):
    @tornado.web.authenticated
    def get(self):
        """Get list of all rules with their status."""
        rule_manager = ai_service_manager.get_rule_manager()
        if not rule_manager:
            self.finish(json.dumps({"rules": [], "enabled": False}))
            return
        
        rules_summary = rule_manager.get_rules_summary()
        all_rules = rule_manager.ruleset.get_all_rules()
        
        rules_data = []
        for rule in all_rules:
            rules_data.append({
                "filename": rule.filename,
                "active": rule.active,
                "mode": rule.mode,
                "apply": rule.apply,
                "priority": rule.priority,
                "scope": rule.scope.__dict__,
                "content_preview": rule.content[:200] + "..." if len(rule.content) > 200 else rule.content
            })
        
        response = {
            "enabled": ai_service_manager.nbi_config.rules_enabled,
            "rules": rules_data,
            "summary": rules_summary
        }
        self.finish(json.dumps(response))

class RulesToggleHandler(APIHandler):
    @tornado.web.authenticated
    def put(self, rule_filename):
        """Toggle a rule's active state."""
        data = json.loads(self.request.body)
        active = data.get('active', True)
        
        rule_manager = ai_service_manager.get_rule_manager()
        if not rule_manager:
            self.set_status(404)
            self.finish(json.dumps({"error": "Rule system not enabled"}))
            return
        
        success = rule_manager.toggle_rule(rule_filename, active)
        if success:
            # Also update config
            ai_service_manager.nbi_config.set_rule_active(rule_filename, active)
            self.finish(json.dumps({"success": True}))
        else:
            self.set_status(404)
            self.finish(json.dumps({"error": "Rule not found"}))

class RulesReloadHandler(APIHandler):
    @tornado.web.authenticated
    def post(self):
        """Reload rules from disk."""
        rule_manager = ai_service_manager.get_rule_manager()
        if not rule_manager:
            self.set_status(404)
            self.finish(json.dumps({"error": "Rule system not enabled"}))
            return

        try:
            rule_manager.load_rules(force_reload=True)
            summary = rule_manager.get_rules_summary()
            self.finish(json.dumps({"success": True, "summary": summary}))
        except Exception as e:
            self.set_status(500)
            self.finish(json.dumps({"error": str(e)}))


class PolicyGatedHandler(APIHandler):
    """APIHandler base used by all NBI management surfaces (Skills,
    Claude-MCP, Plugins). Owns three concerns:

    1. **Admin policy gate** in ``prepare()``. Short-circuits with 403
       when the associated ``*_management_policy`` resolves to
       ``force-off``. Subclasses set ``policy_enabled_attr`` to the
       class-attribute name holding the resolved bool (mutated in
       ``_setup_handlers``), and ``policy_disabled_message`` for the
       user-facing error string.

    2. **JSON request parsing** via ``_parse_json_body``. Returns the
       decoded body or ``None`` after writing a 400; callers must ``if
       data is None: return``.

    3. **Domain-aware error mapping** via ``_error`` + the
       ``exception_status_map`` class attribute (exception class → HTTP
       status). Most-specific class wins via MRO depth ordering.

    Subclasses also typically expose a ``manager`` property that returns
    the per-domain CLI/file-backed manager — convention rather than
    contract since not every handler needs one.
    """

    policy_enabled_attr: str = ""
    policy_disabled_message: str = "This feature is disabled by your administrator"

    def __init_subclass__(cls, **kwargs):
        # Force-off must fail closed; a concrete handler that forgets to
        # declare `policy_enabled_attr` would silently bypass the gate.
        # Intermediate `*BaseHandler` classes are allowed to defer the
        # declaration to the concrete subclass; everything else must set it
        # explicitly (or its MRO must, which getattr below picks up).
        super().__init_subclass__(**kwargs)
        if cls.__name__.endswith("BaseHandler"):
            return
        if not getattr(cls, "policy_enabled_attr", ""):
            raise TypeError(
                f"{cls.__name__} subclasses PolicyGatedHandler but does not "
                "declare policy_enabled_attr — set it on the class or on an "
                "intermediate *BaseHandler"
            )

    async def prepare(self):
        # APIHandler.prepare is async (xsrf/auth); must await before applying
        # the policy gate.
        await super().prepare()
        if self._finished:
            return
        attr = self.policy_enabled_attr
        if attr and not getattr(self, attr, True):
            self.set_status(403)
            self.finish(json.dumps({"error": self.policy_disabled_message}))

    # Subclasses extend this to map domain-specific exception types to HTTP
    # statuses. Most-specific class wins (we sort by MRO depth at lookup
    # time), so a subclass that adds ``OSError: 500`` next to an
    # inherited ``FileNotFoundError: 404`` still routes the latter to 404.
    # Anything not covered falls through to 400.
    exception_status_map: dict[type[Exception], int] = {}

    def _parse_json_body(self):
        try:
            return json.loads(self.request.body)
        except json.JSONDecodeError as e:
            self.set_status(400)
            self.finish(json.dumps({"error": f"Invalid JSON: {e}"}))
            return None

    def _error(self, exc: Exception):
        # Sort candidates by MRO depth (deepest = most specific) so
        # narrower exception classes always win over their bases.
        candidates = sorted(
            (cls for cls in self.exception_status_map if isinstance(exc, cls)),
            key=lambda c: -len(c.__mro__),
        )
        if candidates:
            self.set_status(self.exception_status_map[candidates[0]])
            self.finish(json.dumps({"error": str(exc)}))
            return
        self.set_status(400)
        self.finish(json.dumps({"error": str(exc)}))


class ClaudeMCPBaseHandler(PolicyGatedHandler):
    """Shared helpers + policy gate for Claude-MCP endpoints."""

    claude_mcp_management_enabled = True
    policy_enabled_attr = "claude_mcp_management_enabled"
    policy_disabled_message = "Claude MCP management is disabled by your administrator"
    exception_status_map = {
        FileNotFoundError: 404,
        TimeoutError: 504,
    }
    # Set once at startup from the merged (traitlet + env) admin allowlist.
    # Empty list means no enforcement; consult ``AIServiceManager``.
    mcp_stdio_command_allowlist: list = []

    @property
    def manager(self) -> "ClaudeMCPManager":
        return ClaudeMCPManager(
            working_dir=get_jupyter_root_dir() or None,
            stdio_command_allowlist=self.mcp_stdio_command_allowlist,
        )


class ClaudeMCPListHandler(ClaudeMCPBaseHandler):
    @tornado.web.authenticated
    def get(self):
        try:
            servers = [s.to_dict() for s in self.manager.list_servers()]
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)
            return
        self.finish(json.dumps({"servers": servers}))

    @tornado.web.authenticated
    async def post(self):
        data = self._parse_json_body()
        if data is None:
            return
        try:
            srv = await self.manager.add_server(
                name=data.get("name", ""),
                scope=data.get("scope", "user"),
                transport=data.get("transport", "stdio"),
                command_or_url=data.get("command_or_url", ""),
                args=data.get("args"),
                env=data.get("env"),
                headers=data.get("headers"),
            )
            self.finish(json.dumps({"server": srv.to_dict()}))
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)


class ClaudeMCPDetailHandler(ClaudeMCPBaseHandler):
    @tornado.web.authenticated
    def get(self, scope, name):
        srv = self.manager.get_server(name, scope)
        if srv is None:
            self.set_status(404)
            self.finish(json.dumps({
                "error": f"MCP server {name!r} not found in {scope} scope"
            }))
            return
        self.finish(json.dumps({"server": srv.to_dict()}))

    @tornado.web.authenticated
    async def delete(self, scope, name):
        try:
            await self.manager.remove_server(name, scope)
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)

    @tornado.web.authenticated
    async def patch(self, scope, name):
        # `scope` is part of the URL for symmetry with GET/DELETE but the
        # workspace-disable list is a single flat array of names (not
        # per-scope), so any of user/project/local resolves to the same write.
        # We still validate it so a malformed URL fails fast.
        try:
            validate_scope(scope)
        except ValueError as e:
            self._error(e)
            return
        data = self._parse_json_body()
        if data is None:
            return
        if "disabled_for_workspace" not in data:
            self.set_status(400)
            self.finish(json.dumps(
                {"error": "Missing `disabled_for_workspace` in request body"}
            ))
            return
        raw = data["disabled_for_workspace"]
        if not isinstance(raw, bool):
            self.set_status(400)
            self.finish(json.dumps(
                {"error": "`disabled_for_workspace` must be a JSON boolean"}
            ))
            return
        try:
            srv = await self.manager.set_server_disabled(name=name, disabled=raw)
            self.finish(json.dumps({"server": srv.to_dict()}))
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)


class PluginsBaseHandler(PolicyGatedHandler):
    """Shared helpers + policy gate for plugin endpoints."""

    claude_plugins_management_enabled = True
    allow_github_plugin_import = True
    policy_enabled_attr = "claude_plugins_management_enabled"
    policy_disabled_message = "Plugins management is disabled by your administrator"
    exception_status_map = {
        FileNotFoundError: 404,
        PermissionError: 403,
        TimeoutError: 504,
    }

    @property
    def manager(self) -> "PluginManager":
        # Same as ClaudeMCPBaseHandler: scope=project resolves against the
        # CLI cwd, so the user's Jupyter root must be passed through.
        return PluginManager(working_dir=get_jupyter_root_dir() or None)


class PluginsListHandler(PluginsBaseHandler):
    @tornado.web.authenticated
    async def get(self):
        try:
            plugins = await self.manager.list_plugins()
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)
            return
        self.finish(json.dumps({"plugins": plugins}))

    @tornado.web.authenticated
    async def post(self):
        data = self._parse_json_body()
        if data is None:
            return
        try:
            await self.manager.install_plugin(
                plugin=data.get("plugin", ""),
                scope=data.get("scope", "user"),
            )
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)


class PluginsDetailHandler(PluginsBaseHandler):
    @tornado.web.authenticated
    async def delete(self, scope, plugin):
        try:
            await self.manager.uninstall_plugin(plugin=plugin, scope=scope)
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)

    @tornado.web.authenticated
    async def post(self, scope, plugin):
        # Body: {"action": "enable" | "disable"}.
        data = self._parse_json_body()
        if data is None:
            return
        action = data.get("action")
        if action not in ("enable", "disable"):
            self.set_status(400)
            self.finish(json.dumps({
                "error": "Missing or invalid 'action' (must be 'enable' or 'disable')"
            }))
            return
        try:
            await self.manager.set_plugin_enabled(
                plugin=plugin, enabled=action == "enable", scope=scope
            )
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)


class PluginsMarketplaceListHandler(PluginsBaseHandler):
    @tornado.web.authenticated
    async def get(self):
        try:
            marketplaces = await self.manager.list_marketplaces()
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)
            return
        self.finish(json.dumps({"marketplaces": marketplaces}))

    @tornado.web.authenticated
    async def post(self):
        data = self._parse_json_body()
        if data is None:
            return
        try:
            await self.manager.add_marketplace(
                source=data.get("source", ""),
                scope=data.get("scope", "user"),
                allow_github=bool(self.allow_github_plugin_import),
            )
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, PermissionError, TimeoutError, ValueError) as e:
            self._error(e)


class PluginsMarketplaceDetailHandler(PluginsBaseHandler):
    @tornado.web.authenticated
    async def delete(self, name):
        try:
            await self.manager.remove_marketplace(name=name)
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)


class PluginsMarketplacePluginsHandler(PluginsBaseHandler):
    @tornado.web.authenticated
    async def get(self, name):
        try:
            plugins = await self.manager.list_marketplace_plugins(name)
        except (FileNotFoundError, TimeoutError, ValueError) as e:
            self._error(e)
            return
        self.finish(json.dumps({"plugins": plugins}))


class PluginsMarketplaceUpdateHandler(PluginsBaseHandler):
    @tornado.web.authenticated
    async def post(self, name):
        try:
            await self.manager.update_marketplace(name=name)
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, PermissionError, TimeoutError, ValueError) as e:
            self._error(e)


class SkillsBaseHandler(PolicyGatedHandler):
    """Shared helpers for skills endpoints."""

    allow_github_skill_import = True
    skills_management_enabled = True
    policy_enabled_attr = "skills_management_enabled"
    policy_disabled_message = "Skills management is disabled by your administrator"
    exception_status_map = {
        FileExistsError: 409,
        FileNotFoundError: 404,
        # RuntimeError is reserved for "upstream unreachable" in the sync
        # path. 502 reflects the wire reality (we proxied to GitHub and
        # got nothing usable) rather than the default 400 fallback, which
        # would mislead clients into thinking the request was malformed.
        RuntimeError: 502,
    }

    @property
    def skill_manager(self):
        return ai_service_manager.get_skill_manager()

    def _reject_if_github_import_disabled(self) -> bool:
        if self.allow_github_skill_import:
            return False
        self.set_status(403)
        self.finish(json.dumps({"error": "GitHub Skill import is disabled by configuration"}))
        return True


    def _bundle_rel_path(self):
        rel_path = self.get_query_argument("path", default=None)
        if not rel_path:
            self.set_status(400)
            self.finish(json.dumps({"error": "Missing required 'path' query parameter"}))
            return None
        return rel_path


class SkillsListHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def get(self):
        # Skip the per-bundle file walk — the list view only needs metadata.
        skills = [s.to_dict(include_files=False) for s in self.skill_manager.list_skills()]
        self.finish(json.dumps({"skills": skills}))

    @tornado.web.authenticated
    def post(self):
        data = self._parse_json_body()
        if data is None:
            return
        try:
            skill = self.skill_manager.create_skill(
                scope=data["scope"],
                name=data["name"],
                description=data.get("description", ""),
                allowed_tools=data.get("allowed_tools", []),
                body=data.get("body", ""),
            )
            self.finish(json.dumps({"skill": skill.to_dict(include_body=True)}))
        except (FileExistsError, FileNotFoundError, ValueError, KeyError) as e:
            self._error(e)


class SkillsContextHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def get(self):
        project_root = get_jupyter_root_dir() or ""
        project_name = os.path.basename(os.path.normpath(project_root)) if project_root else ""
        self.finish(json.dumps({
            "project_root": project_root,
            "project_name": project_name,
            "user_skills_dir": str(self.skill_manager.scope_dir("user")),
            "project_skills_dir": str(self.skill_manager.scope_dir("project")),
        }))


class SkillDetailHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def get(self, scope, name):
        try:
            skill = self.skill_manager.get_skill(scope, name)
        except ValueError as e:
            self._error(e)
            return
        if skill is None:
            self.set_status(404)
            self.finish(json.dumps({"error": f"Skill '{name}' not found in {scope} scope"}))
            return
        self.finish(json.dumps({"skill": skill.to_dict(include_body=True)}))

    @tornado.web.authenticated
    def put(self, scope, name):
        data = self._parse_json_body()
        if data is None:
            return
        # The `tracks_upstream` toggle opts a skill into a future sync
        # action, which would phone home to GitHub. `allow_github_skill_import
        # = False` is the network-egress kill switch: blocking sync without
        # also blocking the toggle would let the bit get set on disk while
        # the admin's kill switch is supposed to be in effect.
        if "tracks_upstream" in data and self._reject_if_github_import_disabled():
            return
        try:
            skill = self.skill_manager.update_skill(
                scope=scope,
                name=name,
                description=data.get("description"),
                allowed_tools=data.get("allowed_tools"),
                body=data.get("body"),
                # None preserves the current value; explicit True/False applies.
                # The manager rejects invalid combinations (managed + tracking,
                # no source + tracking).
                tracks_upstream=data.get("tracks_upstream"),
            )
            self.finish(json.dumps({"skill": skill.to_dict(include_body=True)}))
        except (FileNotFoundError, ValueError) as e:
            self._error(e)

    @tornado.web.authenticated
    def delete(self, scope, name):
        try:
            self.skill_manager.delete_skill(scope, name)
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, ValueError) as e:
            self._error(e)


class SkillsImportPreviewHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def post(self):
        if self._reject_if_github_import_disabled():
            return
        data = self._parse_json_body()
        if data is None:
            return
        url = data.get("url")
        if not url:
            self.set_status(400)
            self.finish(json.dumps({"error": "Missing required 'url'"}))
            return
        try:
            preview = self.skill_manager.preview_github_import(url)
            self.finish(json.dumps({"preview": preview}))
        except (FileNotFoundError, ValueError) as e:
            self._error(e)


class SkillsImportHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def post(self):
        if self._reject_if_github_import_disabled():
            return
        data = self._parse_json_body()
        if data is None:
            return
        url = data.get("url")
        scope = data.get("scope")
        if not url or scope not in ("user", "project"):
            self.set_status(400)
            self.finish(json.dumps({
                "error": "Missing 'url' or invalid 'scope' (must be 'user' or 'project')"
            }))
            return
        try:
            skill = self.skill_manager.import_from_github(
                url=url,
                scope=scope,
                name_override=data.get("name"),
                overwrite=bool(data.get("overwrite", False)),
                tracks_upstream=bool(data.get("tracks_upstream", False)),
            )
            self.finish(json.dumps({"skill": skill.to_dict(include_body=True)}))
        except (FileExistsError, FileNotFoundError, ValueError) as e:
            self._error(e)


class SkillSyncHandler(SkillsBaseHandler):
    """Re-fetch a single user-imported skill that has opted into tracking.

    Gated by the same `allow_github_skill_import` policy as the initial
    import: sync is the same network egress with the same trust
    boundary, and an admin who's disabled imports does not want sync
    silently keeping skills fresh on the side.
    """

    @tornado.web.authenticated
    async def post(self, scope, name):
        if self._reject_if_github_import_disabled():
            return
        # The sync action does blocking HTTP (commits-API probe plus
        # optional tarball download). Run off the event loop so the
        # Tornado IO thread keeps serving other requests.
        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: self.skill_manager.sync_tracking_skill(scope, name),
            )
            self.finish(json.dumps(result))
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            self._error(e)


class SkillsSyncAllTrackingHandler(SkillsBaseHandler):
    """Batch sync every skill that has `tracks_upstream` enabled.

    Returns per-skill results so the UI can show which ones updated,
    which were unchanged, and which failed. Failures are isolated per
    skill: a single broken upstream doesn't stop the rest of the sync.
    """

    # Bound concurrency on the GitHub commits-API probe to keep a
    # 10-tracking-skill click from exhausting the unauthenticated 60/hour
    # rate limit in a single burst. Three keeps the probes civil while
    # collapsing serial worst-case (N * 15s) to roughly N/3 * 15s.
    SYNC_CONCURRENCY = 3

    @tornado.web.authenticated
    async def post(self):
        if self._reject_if_github_import_disabled():
            return
        loop = asyncio.get_event_loop()
        skills = await loop.run_in_executor(
            None, self.skill_manager.list_tracking_skills
        )
        if not skills:
            self.finish(json.dumps({"results": []}))
            return

        sem = asyncio.Semaphore(self.SYNC_CONCURRENCY)

        async def sync_one(skill):
            async with sem:
                try:
                    # `lambda s=skill:` captures the skill at iteration
                    # time so all coroutines don't share the same loop
                    # variable.
                    outcome = await loop.run_in_executor(
                        None,
                        lambda s=skill: self.skill_manager.sync_tracking_skill(
                            s.scope, s.name
                        ),
                    )
                    return {
                        "scope": skill.scope,
                        "name": skill.name,
                        **outcome,
                    }
                except Exception as e:  # noqa: BLE001 — per-skill isolation
                    return {
                        "scope": skill.scope,
                        "name": skill.name,
                        "error": str(e),
                    }

        results = await asyncio.gather(*(sync_one(s) for s in skills))
        self.finish(json.dumps({"results": results}))


class SkillsReconcileHandler(SkillsBaseHandler):
    """Manual trigger for the managed-skills reconciler."""

    @tornado.web.authenticated
    async def post(self):
        reconciler = ai_service_manager.get_skill_reconciler()
        if reconciler is None:
            self.set_status(409)
            self.finish(json.dumps({
                "error": "No managed-skills manifest configured (set NBI_SKILLS_MANIFEST, comma-separated for multiple manifests)."
            }))
            return
        # reconcile() does blocking HTTP + tarball extraction; run off the event loop.
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, reconciler.reconcile)
        self.finish(json.dumps(result.to_dict()))


class SkillsReconcilerStopHandler(APIHandler):
    """Incident-response kill switch for the managed-skills reconciler.

    Not gated by ``SkillsBaseHandler.policy_enabled_attr`` because the
    intended use is "stop the background loop regardless of current policy
    state" — e.g. when a compromised manifest URL or leaked managed token
    needs to be neutralized before the pod can be restarted.
    """

    @tornado.web.authenticated
    async def post(self):
        reconciler = ai_service_manager.get_skill_reconciler()
        if reconciler is None:
            # Already not running. Idempotent: don't 404 / 409 the caller
            # since the desired end state matches.
            self.finish(json.dumps({"stopped": True, "was_running": False}))
            return
        was_running = reconciler.is_running()
        reconciler.stop()
        self.finish(json.dumps({"stopped": True, "was_running": was_running}))


class SkillRenameHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def post(self, scope, name):
        data = self._parse_json_body()
        if data is None:
            return
        new_name = data.get("new_name")
        if not new_name:
            self.set_status(400)
            self.finish(json.dumps({"error": "Missing required 'new_name'"}))
            return
        try:
            skill = self.skill_manager.rename_skill(scope, name, new_name)
            self.finish(json.dumps({"skill": skill.to_dict(include_body=True)}))
        except (FileExistsError, FileNotFoundError, ValueError) as e:
            self._error(e)


class SkillBundleFileHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def get(self, scope, name):
        rel_path = self._bundle_rel_path()
        if rel_path is None:
            return
        try:
            content = self.skill_manager.read_bundle_file(scope, name, rel_path)
            self.finish(json.dumps({"content": content}))
        except (FileNotFoundError, ValueError) as e:
            self._error(e)

    @tornado.web.authenticated
    def put(self, scope, name):
        rel_path = self._bundle_rel_path()
        if rel_path is None:
            return
        data = self._parse_json_body()
        if data is None:
            return
        if "content" not in data:
            self.set_status(400)
            self.finish(json.dumps({"error": "Missing required 'content' field"}))
            return
        try:
            self.skill_manager.write_bundle_file(scope, name, rel_path, data["content"])
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, ValueError) as e:
            self._error(e)

    @tornado.web.authenticated
    def delete(self, scope, name):
        rel_path = self._bundle_rel_path()
        if rel_path is None:
            return
        try:
            self.skill_manager.delete_bundle_file(scope, name, rel_path)
            self.finish(json.dumps({"success": True}))
        except (FileNotFoundError, ValueError) as e:
            self._error(e)


class SkillBundleFileRenameHandler(SkillsBaseHandler):
    @tornado.web.authenticated
    def post(self, scope, name):
        data = self._parse_json_body()
        if data is None:
            return
        try:
            old_path = data["from"]
            new_path = data["to"]
        except KeyError as e:
            self.set_status(400)
            self.finish(json.dumps({"error": f"Invalid request: missing {e}"}))
            return
        try:
            self.skill_manager.rename_bundle_file(scope, name, old_path, new_path)
            self.finish(json.dumps({"success": True}))
        except (FileExistsError, FileNotFoundError, ValueError) as e:
            self._error(e)


_upload_dir: str | None = None
_DEFAULT_UPLOAD_MAX_MB = 50
_DEFAULT_UPLOAD_RETENTION_HOURS = 24
_SWEEP_INTERVAL_SECONDS = 60
_sweep_lock = threading.Lock()
_last_sweep_at: float = 0.0


def _get_upload_dir() -> str:
    """Return a temp directory for uploaded files, creating it on first call."""
    global _upload_dir
    if _upload_dir is None:
        _upload_dir = tempfile.mkdtemp(prefix="nbi-uploads-")
        atexit.register(lambda d=_upload_dir: shutil.rmtree(d, ignore_errors=True))
    return _upload_dir


def _resolve_upload_path(file_path: str) -> str | None:
    """Return a real upload path only when it stays under NBI's upload root."""
    upload_root = os.path.realpath(_get_upload_dir())
    resolved = os.path.realpath(file_path)
    try:
        if os.path.commonpath([resolved, upload_root]) != upload_root:
            return None
    except ValueError:
        return None
    return resolved


def _sweep_upload_dir(retention_hours: int) -> None:
    """Best-effort removal of upload subdirs past the retention window.

    Runs lazily after each successful upload, rate-limited to one sweep per
    ``_SWEEP_INTERVAL_SECONDS`` so a burst of parallel uploads doesn't pay
    the full ``listdir + stat`` cost on every request.
    ``retention_hours <= 0`` keeps the atexit-only purge path by skipping
    the sweep entirely.
    """
    if retention_hours <= 0:
        return
    global _last_sweep_at
    now = time.time()
    with _sweep_lock:
        if now - _last_sweep_at < _SWEEP_INTERVAL_SECONDS:
            return
        _last_sweep_at = now
    root = _get_upload_dir()
    cutoff = now - retention_hours * 3600
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for entry in entries:
        candidate = path.join(root, entry)
        try:
            stat_result = os.stat(candidate)
        except OSError:
            # Includes FileNotFoundError when a concurrent sweep beats us.
            continue
        if not os.path.isdir(candidate):
            continue
        if stat_result.st_mtime >= cutoff:
            continue
        shutil.rmtree(candidate, ignore_errors=True)


class FileUploadHandler(APIHandler):
    """Accepts a file upload and stores it in a temp directory.

    Returns the absolute server-side path so the frontend can reference it
    in chat context and Claude Code can read it natively.
    """

    upload_max_mb: int = _DEFAULT_UPLOAD_MAX_MB
    upload_retention_hours: int = _DEFAULT_UPLOAD_RETENTION_HOURS

    @tornado.web.authenticated
    def post(self):
        fileinfo = self.request.files.get("file")
        if not fileinfo or len(fileinfo) == 0:
            self.set_status(400)
            self.finish(json.dumps({"error": "No file provided"}))
            return

        upload = fileinfo[0]
        body = upload["body"]

        # Reject oversize uploads before writing to disk so a giant payload
        # can't briefly fill the staging dir. 0 disables the cap.
        if self.upload_max_mb > 0:
            max_bytes = self.upload_max_mb * 1024 * 1024
            if len(body) > max_bytes:
                self.set_status(413)
                self.finish(json.dumps({
                    "error": f"File exceeds {self.upload_max_mb} MB upload limit"
                }))
                return

        original_name = upload.get("filename", "upload")
        # Sanitise filename: keep only the basename to prevent path traversal.
        safe_name = path.basename(original_name)
        if not safe_name:
            safe_name = "upload"

        upload_id = uuid.uuid4().hex[:12]
        dest_dir = path.join(_get_upload_dir(), upload_id)
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = path.join(dest_dir, safe_name)

        with open(dest_path, "wb") as fh:
            fh.write(body)

        _sweep_upload_dir(self.upload_retention_hours)

        self.finish(json.dumps({
            "serverPath": dest_path,
            "filename": safe_name,
        }))


class ClaudeSessionsListHandler(APIHandler):
    """Lists Claude Code sessions for both pickers.

    Both pickers (chat sidebar and launcher tile) consume this endpoint
    via the same ``list_all_sessions`` backend so they cannot disagree on
    previews. ``?scope=cwd`` filters the response down to sessions whose
    transcript lives under the current Jupyter cwd (the chat sidebar
    case); ``?scope=all`` (the default) returns every project's sessions
    (the launcher tile case).

    ``current_cwd`` is the realpath-resolved Jupyter root the chat picker
    pairs with the session id to render ``cd ... && claude --resume <id>``.

    Guarded by ``is_claude_code_mode`` because both consumers are
    Claude-Code-specific surfaces — the launcher tile literally launches
    the ``claude`` CLI in a terminal.
    """

    @tornado.web.authenticated
    def get(self):
        if not ai_service_manager.is_claude_code_mode:
            self.set_status(404)
            self.finish(json.dumps({"error": "Claude Code mode is not enabled"}))
            return

        scope = self.get_query_argument("scope", "all")
        try:
            cwd = get_jupyter_root_dir()
            sessions = list_all_claude_sessions(cwd=cwd)
            if scope == "cwd" and cwd:
                # Compare realpaths so symlinked workspaces (common on
                # JupyterHub with NFS user dirs) match transcripts that
                # were written against the resolved path. The old
                # implementation compared the encoded directory name,
                # which produced different strings whenever the user's
                # cwd was a symlink alias.
                #
                # `realpath` can be an NFS round trip per call, so cache
                # per-cwd within this request — many sessions share the
                # same cwd and re-resolving each time turns a 1k-session
                # filter into 1k NFS lookups.
                #
                # Sessions whose cwd is empty (older transcripts that
                # carried no cwd field and whose project dir name also
                # failed the dash-decode fallback) are dropped from
                # scope=cwd results: they cannot be matched against the
                # current cwd anyway. They remain visible under
                # scope=all.
                target = os.path.realpath(cwd)
                realpath_cache: dict[str, str] = {}

                def _rp(p: str) -> str:
                    cached = realpath_cache.get(p)
                    if cached is None:
                        cached = os.path.realpath(p)
                        realpath_cache[p] = cached
                    return cached

                sessions = [
                    s for s in sessions if s.cwd and _rp(s.cwd) == target
                ]
            self.finish(json.dumps({
                "sessions": [asdict(s) for s in sessions],
                "current_cwd": os.path.realpath(cwd) if cwd else "",
            }))
        except Exception as e:
            log.exception("Failed to list Claude sessions")
            self.set_status(500)
            self.finish(json.dumps({"error": str(e)}))


class ClaudeSessionsResumeHandler(APIHandler):
    """Reconnects the Claude client so the next query resumes a session."""

    @tornado.web.authenticated
    def post(self):
        if not ai_service_manager.is_claude_code_mode:
            self.set_status(404)
            self.finish(json.dumps({"error": "Claude Code mode is not enabled"}))
            return

        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "Request body must be JSON"}))
            return

        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            self.set_status(400)
            self.finish(json.dumps({"error": "session_id is required"}))
            return

        default_chat_participant = ai_service_manager.default_chat_participant
        if not isinstance(default_chat_participant, ClaudeCodeChatParticipant):
            self.set_status(404)
            self.finish(json.dumps({"error": "Claude Code mode is not enabled"}))
            return

        try:
            default_chat_participant.resume_session(session_id)
        except Exception as e:
            log.exception("Failed to resume Claude session %s", session_id)
            self.set_status(500)
            self.finish(json.dumps({"error": str(e)}))
            return

        self.finish(json.dumps({"success": True, "session_id": session_id}))


def _acp_participant_or_none():
    """The active ACP participant, or None when ACP mode is not active."""
    if not ai_service_manager.is_acp_mode:
        return None
    participant = ai_service_manager.default_chat_participant
    if not callable(getattr(participant, "list_sessions", None)):
        return None
    return participant


class AcpSessionsListHandler(APIHandler):
    """Lists the ACP agent's stored sessions for the current workspace.

    Backed by the agent's own ``session/list`` (codex-acp implements it), so
    the picker shows the same sessions the agent CLI would resume. Unlike the
    Claude handler there is no scope switch: the agent call is already
    filtered to the Jupyter cwd on the client side.
    """

    @tornado.web.authenticated
    async def get(self):
        participant = _acp_participant_or_none()
        if participant is None:
            self.set_status(404)
            self.finish(json.dumps({"error": "ACP mode is not enabled"}))
            return
        try:
            # list_sessions may cold-start the agent subprocess (up to a
            # minute through npx); keep the IO loop free while it does.
            loop = asyncio.get_event_loop()
            sessions, error = await loop.run_in_executor(
                None, participant.list_sessions
            )
            if error:
                self.finish(json.dumps({
                    "sessions": [], "current_cwd": "", "error": error,
                }))
                return
            cwd = get_jupyter_root_dir()
            self.finish(json.dumps({
                "sessions": sessions,
                "current_cwd": os.path.realpath(cwd) if cwd else "",
            }))
        except Exception as e:
            log.exception("Failed to list ACP sessions")
            self.set_status(500)
            self.finish(json.dumps({"error": str(e)}))


class AcpSessionsResumeHandler(APIHandler):
    """Resumes an ACP session via the agent's ``session/load``."""

    @tornado.web.authenticated
    async def post(self):
        participant = _acp_participant_or_none()
        if participant is None:
            self.set_status(404)
            self.finish(json.dumps({"error": "ACP mode is not enabled"}))
            return

        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError:
            self.set_status(400)
            self.finish(json.dumps({"error": "Request body must be JSON"}))
            return

        session_id = body.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            self.set_status(400)
            self.finish(json.dumps({"error": "session_id is required"}))
            return

        try:
            loop = asyncio.get_event_loop()
            error = await loop.run_in_executor(
                None, participant.resume_session, session_id
            )
        except Exception as e:
            log.exception("Failed to resume ACP session %s", session_id)
            self.set_status(500)
            self.finish(json.dumps({"error": str(e)}))
            return
        if error:
            self.set_status(500)
            self.finish(json.dumps({"error": error}))
            return

        self.finish(json.dumps({"success": True, "session_id": session_id}))

class ChatHistory:
    """
    History of chat messages, key is chat id, value is list of messages
    keep the last 10 messages in the same chat participant
    """
    MAX_MESSAGES = 10

    def __init__(self):
        self.messages = {}

    def clear(self, chatId = None):
        if chatId is None:
            self.messages = {}
            return True
        elif chatId in self.messages:
            del self.messages[chatId]
            return True

        return False

    def add_message(self, chatId, message):
        if chatId not in self.messages:
            self.messages[chatId] = []

        # clear the chat history if participant changed
        if message["role"] == "user":
            existing_messages = self.messages[chatId]
            prev_user_message = next((m for m in reversed(existing_messages) if m["role"] == "user"), None)
            if prev_user_message is not None:
                current_prompt_parts = AIServiceManager.parse_prompt(message["content"])
                prev_prompt_parts = AIServiceManager.parse_prompt(prev_user_message["content"])
                if current_prompt_parts.participant != prev_prompt_parts.participant:
                    self.messages[chatId] = []

        self.messages[chatId].append(message)
        # limit number of messages kept in history
        if len(self.messages[chatId]) > ChatHistory.MAX_MESSAGES:
            self.messages[chatId] = self.messages[chatId][-ChatHistory.MAX_MESSAGES:]

    def get_history(self, chatId):
        return self.messages.get(chatId, [])

class WebsocketCopilotResponseEmitter(ChatResponse):
    def __init__(self, chatId, messageId, websocket_handler, chat_history):
        super().__init__()
        self.chatId = chatId
        self.messageId = messageId
        self.websocket_handler = websocket_handler
        self.chat_history = chat_history
        self.streamed_contents = []
        self.streamed_reasoning_contents = []
        # Capture the Tornado IOLoop the websocket lives on. stream() /
        # finish() / run_ui_command() get called from worker threads
        # (Claude SDK, MCP, base chat participant); writing directly to
        # the websocket from those threads is unsafe because Tornado's
        # internal write buffer is a bytearray with active exports and a
        # cross-thread mutation raises `BufferError: Existing exports of
        # data: object cannot be re-sized` (issue #264). Marshaling the
        # write back to the IOLoop's thread fixes it.
        self._io_loop = tornado.ioloop.IOLoop.current()

    def _send_async(self, message: dict) -> None:
        self._io_loop.asyncio_loop.call_soon_threadsafe(
            self.websocket_handler.write_message, message
        )

    @property
    def chat_id(self) -> str:
        return self.chatId

    @property
    def message_id(self) -> str:
        return self.messageId

    def stream(self, data: Union[ResponseStreamData, dict]):
        data_type = ResponseStreamDataType.LLMRaw if type(data) is dict else data.data_type

        if data_type == ResponseStreamDataType.Markdown:
            self.chat_history.add_message(self.chatId, {"role": "assistant", "content": data.content, "reasoning_content": data.reasoning_content})
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": data.content,
                                "reasoning_content": data.reasoning_content,
                                "detail": data.detail
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.Image:
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": data.content
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.HTMLFrame:
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content" : {
                                    "source": data.source,
                                    "height": data.height
                                }
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.Anchor:
            # Anchors stream from arbitrary LLM/tool output. Reject schemes
            # outside the allowlist server-side before they reach the React
            # tree; the renderer applies the same check as defense in depth.
            # Log at DEBUG only: a misbehaving model can flood the stream
            # with rejected anchors, and the original title may contain
            # attacker-supplied text we don't want in the server log.
            sanitized_uri = safe_anchor_uri(data.uri)
            if not sanitized_uri and data.uri:
                log.debug("Dropping anchor with disallowed uri scheme")
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": {
                                    "uri": sanitized_uri,
                                    "title": data.title
                                }
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.Button:
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": {
                                    "title": data.title,
                                    "commandId": data.commandId,
                                    "args": data.args if data.args is not None else {}
                                }
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.Progress:
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": data.title
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.ToolCall:
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": {
                                    "id": data.id,
                                    "title": data.title,
                                    "kind": data.kind,
                                    "status": data.status,
                                    "diffs": data.diffs or []
                                }
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.Confirmation:
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": {
                                    "title": data.title,
                                    "message": data.message,
                                    "confirmArgs": data.confirmArgs if data.confirmArgs is not None else {},
                                    "confirmSessionArgs": data.confirmSessionArgs,
                                    "cancelArgs": data.cancelArgs if data.cancelArgs is not None else {},
                                    "confirmLabel": data.confirmLabel if data.confirmLabel is not None else "Approve",
                                    "confirmSessionLabel": data.confirmSessionLabel if data.confirmSessionLabel is not None else "Approve for this request",
                                    "cancelLabel": data.cancelLabel if data.cancelLabel is not None else "Cancel"
                                }
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.AskUserQuestion:
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": {
                                    "identifier": data.identifier,
                                    "title": data.title,
                                    "message": data.message,
                                    "questions": data.questions if data.questions is not None else [],
                                    "submitLabel": data.submitLabel if data.submitLabel is not None else "Submit",
                                    "cancelLabel": data.cancelLabel if data.cancelLabel is not None else "Cancel"
                                }
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
        elif data_type == ResponseStreamDataType.MarkdownPart:
            content = data.content
            reasoning_content = data.reasoning_content
            data = {
                "choices": [
                    {
                        "delta": {
                            "nbiContent": {
                                "type": data_type,
                                "content": data.content,
                                "reasoning_content": data.reasoning_content
                            },
                            "content": "",
                            "role": "assistant"
                        }
                    }
                ]
            }
            if content is not None:
                self.streamed_contents.append(content)
            if reasoning_content is not None:
                self.streamed_reasoning_contents.append(reasoning_content)
        else: # ResponseStreamDataType.LLMRaw
            if len(data.get("choices", [])) > 0:
                delta = data["choices"][0].get("delta", {})
                content = delta.get("content", "")
                reasoning_content = delta.get("reasoning_content", "")
                if content is not None:
                    self.streamed_contents.append(content)
                if reasoning_content is not None:
                    self.streamed_reasoning_contents.append(reasoning_content)

        self._send_async({
            "id": self.messageId,
            "participant": self.participant_id,
            "type": BackendMessageType.StreamMessage,
            "data": data,
            "created": dt.datetime.now().isoformat()
        })

    def finish(self) -> None:
        self.chat_history.add_message(self.chatId, {"role": "assistant", "content": "".join(self.streamed_contents), "reasoning_content": "".join(self.streamed_reasoning_contents)})
        self.streamed_contents = []
        self.streamed_reasoning_contents = []
        self._send_async({
            "id": self.messageId,
            "participant": self.participant_id,
            "type": BackendMessageType.StreamEnd,
            "data": {}
        })

    async def run_ui_command(self, command: str, args: dict = {}) -> None:
        callback_id = str(uuid.uuid4())
        self._send_async({
            "id": self.messageId,
            "participant": self.participant_id,
            "type": BackendMessageType.RunUICommand,
            "data": {
                "callback_id": callback_id,
                "commandId": command,
                "args": args
            }
        })
        response = await ChatResponse.wait_for_run_ui_command_response(self, callback_id)
        return response

class CancelTokenImpl(CancelToken):
    def __init__(self):
        super().__init__()
        self._cancellation_signal = SignalImpl()

    def cancel_request(self) -> None:
        self._cancellation_requested = True
        self._cancellation_signal.emit()

@dataclass
class MessageCallbackHandlers:
    response_emitter: WebsocketCopilotResponseEmitter
    cancel_token: CancelTokenImpl

class WebsocketCopilotHandler(WebSocketMixin, websocket.WebSocketHandler, JupyterHandler):
    # Cap WS message size at 4 MiB. Largest legitimate payload is a chat
    # request with ~10 attached output-context items (each capped at 1 MiB
    # by `coerce_payload`) + chat history; 4 MiB covers that without
    # leaving the default 10 MiB headroom for memory amplification.
    max_message_size = 4 * 1024 * 1024

    # Resolved from the claude_bypass_permissions policy in _setup_handlers;
    # False (fail closed) until then.
    claude_bypass_permissions_allowed = False

    # Inheritance matches Jupyter's first-party WS handlers (e.g.
    # KernelWebsocketHandler): ``WebSocketMixin`` adds ping/pong
    # keepalive plus a ``prepare`` that routes through Jupyter's
    # identity provider without redirecting to a login page (a 302 on
    # a WS upgrade is meaningless to the browser). ``JupyterHandler``
    # supplies ``check_origin`` (allow_origin-aware) and
    # ``check_xsrf_cookie``. ``ws_authenticated`` decorates ``open`` to
    # raise 403 on unauthenticated upgrade rather than the redirect
    # behavior of ``tornado.web.authenticated``.

    def __init__(self, application, request, context_factory=None, **kwargs):
        super().__init__(application, request, **kwargs)
        # Keyed by request messageId; entries are populated when a chat /
        # inline-completion / generate-code request kicks off, and removed
        # by `_run_request_thread` once the worker thread returns. The
        # entry holds the response emitter (for ChatUserInput and
        # RunUICommandResponse routing) and the cancel token. Without the
        # removal step the dict grew unbounded for the lifetime of the
        # websocket — every long chat session leaked one emitter +
        # cancel token per turn.
        self._messageCallbackHandlers: dict[str, MessageCallbackHandlers] = {}
        self.chat_history = ChatHistory()
        self._context_factory = context_factory or RuleContextFactory()
        ws_connector = ThreadSafeWebSocketConnector(self)
        ai_service_manager.websocket_connector = ws_connector
        github_copilot.websocket_connector = ws_connector

    def _run_request_thread(self, coro, message_id):
        """Worker-thread entrypoint that pops the messageId from
        `_messageCallbackHandlers` on completion (success or failure).
        The dict entry is only needed while the request is in flight —
        ChatUserInput / RunUICommandResponse / Cancel messages from the
        client are routed to the emitter by messageId, and the client
        stops sending those once the response stream ends.
        """
        try:
            asyncio.run(coro)
        finally:
            self._messageCallbackHandlers.pop(message_id, None)

    @ws_authenticated
    def open(self):
        # Audit log of accepted upgrades so a security incident can be
        # correlated with the negotiated user identity and origin. The
        # user is the value Jupyter's identity provider resolved on the
        # upgrade request; the origin is the browser's claimed origin
        # which check_origin already validated against allow_origin.
        log.info(
            "Copilot WS upgrade accepted user=%r origin=%r",
            getattr(self.current_user, "username", self.current_user),
            self.request.headers.get("Origin"),
        )

    def on_message(self, message):
        msg = json.loads(message)

        messageId = msg['id']
        messageType = msg['type']
        if messageType == RequestDataType.ChatRequest:
            data = msg['data']
            chatId = data['chatId']
            prompt = data['prompt']
            language = data['language']
            kernel_name = data.get('kernelName', '')
            kernel_display_name = data.get('kernelDisplayName', '')
            filename = data['filename']
            additionalContext = data.get('additionalContext', [])
            chat_mode = ChatMode('agent', 'Agent') if data.get('chatMode', 'ask') == 'agent' else ChatMode('ask', 'Ask')
            toolSelections = data.get('toolSelections', {})
            tool_selection = RequestToolSelection(
                built_in_toolsets=toolSelections.get('builtinToolsets', []),
                mcp_server_tools=toolSelections.get('mcpServers', {}),
                extension_tools=toolSelections.get('extensions', {})
            )
            # Clamp at the boundary so a hand-rolled request can't reach
            # bypass when the policy or managed settings forbid it.
            permission_mode = resolve_permission_mode(
                data.get('permissionMode', DEFAULT_PERMISSION_MODE),
                self.claude_bypass_permissions_allowed,
            )

            is_claude_code_mode = ai_service_manager.is_claude_code_mode
            # ACP agents hold the conversation in their own session, exactly
            # like the Claude SDK client, so they share Claude's context
            # framing and per-turn history slicing below.
            is_agent_session_mode = is_claude_code_mode or ai_service_manager.is_acp_mode
            chat_history = self.chat_history.get_history(chatId)
            chat_history_initial_size = len(chat_history)

            current_directory = data.get('currentDirectory')
            if (is_agent_session_mode or chat_mode.id == 'agent') and current_directory is not None:
                current_directory_file_msg = f"{NBI_CONTEXT_PREFIX} '{current_directory}'"
                if filename != '':
                    current_directory_file_msg += f" and current file is: '{filename}'"
                if language:
                    current_directory_file_msg += (
                        f" and active programming language is: '{language}'"
                    )
                if kernel_name:
                    current_directory_file_msg += (
                        f" with active kernel name: '{kernel_name}'"
                    )
                if kernel_display_name:
                    current_directory_file_msg += (
                        f" ({kernel_display_name})"
                    )
                chat_history.append({"role": "user", "content": current_directory_file_msg})

            token_limit = _resolve_context_token_limit(ai_service_manager)
            remaining_token_budget = int(0.8 * token_limit)

            # Resolve once; reused for sandbox containment and for
            # workspace-relative @-mention path computation below.
            workspace_root = os.path.realpath(NotebookIntelligence.root_dir)

            for context in additionalContext:
                if remaining_token_budget <= 0:
                    break

                output_context = _coerce_output_context(context.get("outputContext"))
                if output_context is not None:
                    # Estimate cost without re-encoding the whole formatted
                    # message: per-bundle token counts are precomputed by the
                    # client (and capped by `coerce_payload`'s size limits);
                    # `cellSource` we count once. ~50-token allowance for the
                    # wrapper text is comfortably above the actual envelope.
                    bundle_tokens = sum(
                        b.get("sizeTokens", 0)
                        for b in output_context.get("mimeBundles", [])
                    )
                    cell_source = output_context.get("cellSource", "")
                    cell_source_tokens = _token_count(cell_source) if cell_source else 0
                    estimated_tokens = bundle_tokens + cell_source_tokens + 50
                    if estimated_tokens > remaining_token_budget:
                        log.info(
                            "Skipping output context: estimated %d tokens exceeds remaining budget %d",
                            estimated_tokens,
                            remaining_token_budget,
                        )
                        continue
                    supports_vision = _resolve_supports_vision(ai_service_manager)
                    context_message = _format_output_context(output_context, supports_vision=supports_vision)
                    remaining_token_budget -= estimated_tokens
                    chat_history.append({"role": "user", "content": context_message})
                    continue

                is_upload = context.get("isUpload", False)
                is_image = context.get("isImage", False)
                file_path = context["filePath"]
                if is_upload:
                    resolved_upload_path = _resolve_upload_path(file_path)
                    if resolved_upload_path is None:
                        log.warning(
                            "Rejecting out-of-upload-dir context path: %r",
                            context["filePath"],
                        )
                        continue
                    file_path = resolved_upload_path
                else:
                    # Workspace-relative paths arrive verbatim from the
                    # frontend (file browser drag, @-mention picker, ...).
                    # path.join silently passes through absolute paths and
                    # doesn't normalize ``..`` traversal, so sandbox the
                    # resolved path against root_dir before reading.
                    joined = path.join(NotebookIntelligence.root_dir, file_path)
                    resolved = os.path.realpath(joined)
                    try:
                        in_workspace = (
                            os.path.commonpath([resolved, workspace_root])
                            == workspace_root
                        )
                    except ValueError:
                        in_workspace = False
                    if not in_workspace:
                        log.warning(
                            "Rejecting out-of-workspace context path: %r",
                            context["filePath"],
                        )
                        continue
                    file_path = resolved
                context_filename = path.basename(file_path)

                if is_image:
                    if is_agent_session_mode:
                        # Agent CLIs take text prompts only; pass the file
                        # path so the agent can read the image itself.
                        chat_history.append({
                            "role": "user",
                            "content": f"The user pasted an image. It is saved at this path: '{file_path}'. Please read and analyze it."
                        })
                    else:
                        # Use OpenAI vision format for non-Claude-Code providers
                        mime_type = context.get("mimeType", "image/png")
                        try:
                            with open(file_path, "rb") as img_f:
                                b64_data = base64.b64encode(img_f.read()).decode("utf-8")
                            chat_history.append({
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": f"The user pasted an image '{context_filename}':"},
                                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64_data}"}}
                                ]
                            })
                        except Exception as e:
                            log.warning(f"Failed to read pasted image '{file_path}': {e}")
                    continue

                if is_agent_session_mode:
                    # Hand the agent an @-mention rather than the file's
                    # contents: an agent's own read tool handles partial
                    # reads, notebook cell structure, and binary formats
                    # natively, and avoids the 80% context-window truncation
                    # the content-injection path would otherwise apply.
                    if is_upload:
                        mention_path = file_path
                    else:
                        try:
                            mention_path = path.relpath(file_path, workspace_root)
                        except ValueError:
                            mention_path = file_path
                    # Defense in depth: the path already passed the
                    # workspace sandbox above, but a filename containing
                    # newlines, NEL/LS/PS, bidi-override controls, or
                    # other text-rendering hazards would split or visually
                    # impersonate the prose envelope once it reaches the
                    # agent. Reuse the same codepoint set safe_anchor_uri
                    # uses for the same threat profile.
                    if has_dangerous_text_codepoints(mention_path):
                        log.warning(
                            "Rejecting attachment with disallowed-codepoint "
                            "filename (prompt-injection hardening): %r",
                            context["filePath"],
                        )
                        continue
                    # Preserve the pointer prose the legacy path emits so
                    # the agent still has a cursor for "this cell" /
                    # "this code" deictic references; the @-mention alone
                    # gives the agent the file but not the focus.
                    # - currentCellContents fires when a notebook cell is
                    #   active with no text selection (cellOutputAsText
                    #   already bundles execute_result, stream, AND error
                    #   traceback into the `output` string).
                    # - startLine/endLine spans fire when the user has
                    #   selected a range; pass the range as prose rather
                    #   than the content so large selections don't burn
                    #   the token budget.
                    current_cell_contents = context.get("currentCellContents")
                    pointer_parts = []
                    if current_cell_contents is not None:
                        cell_input = current_cell_contents.get("input", "")
                        cell_output = current_cell_contents.get("output", "")
                        pointer_parts.append(
                            f"This is a Jupyter notebook and currently "
                            f"selected cell input is: ```{cell_input}``` "
                            f"and currently selected cell output is: "
                            f"```{cell_output}```. If user asks a question "
                            f"about 'this' cell then assume that user is "
                            f"referring to currently selected cell."
                        )
                    else:
                        start_line = context.get("startLine") or 0
                        end_line = context.get("endLine") or 0
                        if end_line > start_line > 0:
                            pointer_parts.append(
                                f"Their selection spans lines "
                                f"{start_line}-{end_line}."
                            )
                    context_message = " ".join(
                        [
                            f"The user attached @{mention_path}.",
                            *pointer_parts,
                            "Read it if relevant to the request.",
                        ]
                    )
                    # NB: when the user's prompt begins with `/`, the
                    # join logic in claude.py (`query_lines[-1].startswith('/')`)
                    # drops every prior user-role message, including these
                    # context lines. Pre-existing behavior, not introduced
                    # by this branch; documented here so a future reader
                    # doesn't chase the @-mention silently disappearing
                    # when paired with a slash-command.
                    remaining_token_budget -= _token_count(context_message)
                    chat_history.append({"role": "user", "content": context_message})
                    continue

                start_line = context["startLine"]
                end_line = context["endLine"]
                current_cell_contents = context["currentCellContents"]
                current_cell_input = current_cell_contents["input"] if current_cell_contents is not None else ""
                current_cell_output = current_cell_contents["output"] if current_cell_contents is not None else ""
                current_cell_context = f"This is a Jupyter notebook and currently selected cell input is: ```{current_cell_input}``` and currently selected cell output is: ```{current_cell_output}```. If user asks a question about 'this' cell then assume that user is referring to currently selected cell." if current_cell_contents is not None else ""
                context_content = context.get("content", "")

                if context_content:
                    context_content = _truncate_context_content(
                        context_content,
                        remaining_token_budget
                    )

                if context_content == "" and remaining_token_budget <= 0:
                    break

                # For uploaded binary files (images, PDFs, etc.) where no
                # text content was extracted, tell Claude to read the file
                # from disk so it can handle it natively.
                if is_upload and context_content == "":
                    context_message = (
                        f"The user attached a file '{context_filename}' "
                        f"at path '{file_path}'. Read this file to see its contents."
                    )
                else:
                    context_message = _build_additional_context_message(
                        file_path=file_path,
                        context_filename=context_filename,
                        start_line=start_line,
                        end_line=end_line,
                        context_content=context_content,
                        current_cell_context=current_cell_context
                    )
                remaining_token_budget -= _token_count(context_message)
                chat_history.append({"role": "user", "content": context_message})

            chat_history.append({"role": "user", "content": prompt})

            response_emitter = WebsocketCopilotResponseEmitter(chatId, messageId, self, self.chat_history)
            cancel_token = CancelTokenImpl()
            self._messageCallbackHandlers[messageId] = MessageCallbackHandlers(response_emitter, cancel_token)
            
            # Create rule context for rule evaluation
            rule_context = self._context_factory.create(
                filename=filename,
                language=language,
                kernel_name=kernel_name,
                chat_mode_id=chat_mode.id,
                root_dir=NotebookIntelligence.root_dir
            )

            # last prompt is added later
            request_chat_history = chat_history[chat_history_initial_size:-1] if is_agent_session_mode else chat_history[:-1]
            coro = ai_service_manager.handle_chat_request(ChatRequest(chat_mode=chat_mode, tool_selection=tool_selection, prompt=prompt, language=language, kernel_name=kernel_name, chat_history=request_chat_history, cancel_token=cancel_token, rule_context=rule_context, permission_mode=permission_mode), response_emitter)
            thread = threading.Thread(target=self._run_request_thread, args=(coro, messageId))
            thread.start()
        elif messageType == RequestDataType.GenerateCode:
            data = msg['data']
            chatId = data['chatId']
            prompt = data['prompt']
            prefix = data['prefix']
            suffix = data['suffix']
            existing_code = data['existingCode']
            language = data['language']
            kernel_name = data.get('kernelName', '')
            filename = data['filename']
            is_claude_code_mode = ai_service_manager.is_claude_code_mode
            chat_mode = ChatMode('inline-chat', 'Inline Chat') if is_claude_code_mode else ChatMode('ask', 'Ask')
            if prefix != '':
                self.chat_history.add_message(chatId, {"role": "user", "content": f"This code section comes before the code section you will generate, use as context. Leading content: ```{prefix}```"})
            if suffix != '':
                self.chat_history.add_message(chatId, {"role": "user", "content": f"This code section comes after the code section you will generate, use as context. Trailing content: ```{suffix}```"})
            if existing_code != '':
                self.chat_history.add_message(chatId, {"role": "user", "content": f"You are asked to modify the existing code. Generate a replacement for this existing code : ```{existing_code}```"})
            self.chat_history.add_message(chatId, {"role": "user", "content": f"Generate code for: {prompt}"})
            response_emitter = WebsocketCopilotResponseEmitter(chatId, messageId, self, self.chat_history)
            cancel_token = CancelTokenImpl()
            self._messageCallbackHandlers[messageId] = MessageCallbackHandlers(response_emitter, cancel_token)
            existing_code_message = " Update the existing code section and return a modified version. Don't just return the update, recreate the existing code section with the update." if existing_code != '' else ''
            
            # Create rule context for rule evaluation
            # Note: Using 'inline-chat' mode for rule matching even though chat_mode is 'ask' for handler compatibility
            rule_context = self._context_factory.create(
                filename=filename,
                language=language,
                kernel_name=kernel_name,
                chat_mode_id='inline-chat',
                root_dir=NotebookIntelligence.root_dir
            )
            
            coro = ai_service_manager.handle_chat_request(ChatRequest(chat_mode=chat_mode, prompt=prompt, language=language, kernel_name=kernel_name, chat_history=self.chat_history.get_history(chatId), cancel_token=cancel_token, rule_context=rule_context), response_emitter, options={"system_prompt": f"You are an assistant that generates code for '{language}' language. You generate code between existing leading and trailing code sections.{existing_code_message} Be concise and return only code as a response. Don't include leading content or trailing content in your response, they are provided only for context. You can reuse methods and symbols defined in leading and trailing content."})
            thread = threading.Thread(target=self._run_request_thread, args=(coro, messageId))
            thread.start()
        elif messageType == RequestDataType.InlineCompletionRequest:
            data = msg['data']
            chatId = data['chatId']
            prefix = data['prefix']
            suffix = data['suffix']
            language = data['language']
            filename = data['filename']
            chat_history = ChatHistory()

            response_emitter = WebsocketCopilotResponseEmitter(chatId, messageId, self, chat_history)
            cancel_token = CancelTokenImpl()
            self._messageCallbackHandlers[messageId] = MessageCallbackHandlers(response_emitter, cancel_token)

            coro = WebsocketCopilotHandler.handle_inline_completions(prefix, suffix, language, filename, response_emitter, cancel_token)
            thread = threading.Thread(target=self._run_request_thread, args=(coro, messageId))
            thread.start()
        elif messageType == RequestDataType.ChatUserInput:
            handlers = self._messageCallbackHandlers.get(messageId)
            if handlers is None:
                return
            handlers.response_emitter.on_user_input(msg['data'])
        elif messageType == RequestDataType.ClearChatHistory:
            if ai_service_manager.is_claude_code_mode:
                default_chat_participant = ai_service_manager.default_chat_participant
                if isinstance(default_chat_participant, ClaudeCodeChatParticipant):
                    default_chat_participant.clear_chat_history()
            elif ai_service_manager.is_acp_mode:
                default_chat_participant = ai_service_manager.default_chat_participant
                clear = getattr(default_chat_participant, "clear_chat_history", None)
                if callable(clear):
                    # The ACP session swap round-trips to the agent subprocess
                    # (up to the start timeout); run it off the websocket
                    # thread so a slow agent cannot stall the IO loop. A
                    # failed swap already forces a client restart; surface it
                    # in the log since there is no UI channel here.
                    def _clear_acp_session():
                        error = clear()
                        if error:
                            log.warning("ACP new session failed: %s", error)
                    threading.Thread(target=_clear_acp_session, daemon=True).start()
            self.chat_history.clear()
        elif messageType == RequestDataType.RunUICommandResponse:
            handlers = self._messageCallbackHandlers.get(messageId)
            if handlers is None:
                return
            handlers.response_emitter.on_run_ui_command_response(msg['data'])
        elif messageType == RequestDataType.CancelChatRequest or  messageType == RequestDataType.CancelInlineCompletionRequest:
            handlers = self._messageCallbackHandlers.get(messageId)
            if handlers is None:
                return
            handlers.cancel_token.cancel_request()
 
    def on_close(self):
        # Cancel any requests still in flight at disconnect, then drop their
        # handlers. Clearing alone isn't enough: a turn parked on a tool
        # approval no longer self-resolves via the response timeout (the
        # approval wait is excluded from it, #381), so without an explicit
        # cancel its worker thread and the Claude subprocess would spin until
        # the server restarts. Cancelling makes the poll loop tear the
        # subprocess down and return.
        # Snapshot first: a worker thread finishing its request pops its own
        # entry (see _run_request_thread), which would otherwise raise
        # "dictionary changed size during iteration" here on the IOLoop thread.
        for handlers in list(self._messageCallbackHandlers.values()):
            try:
                handlers.cancel_token.cancel_request()
            except Exception as e:
                log.warning(f"Error cancelling in-flight request on close: {e}")
        self._messageCallbackHandlers.clear()

    async def handle_inline_completions(prefix, suffix, language, filename, response_emitter, cancel_token):
        if ai_service_manager.inline_completion_model is None:
            response_emitter.finish()
            return

        context = await ai_service_manager.get_completion_context(ContextRequest(ContextRequestType.InlineCompletion, prefix, suffix, language, filename, participant=ai_service_manager.get_chat_participant(prefix), cancel_token=cancel_token))

        if cancel_token.is_cancel_requested:
            response_emitter.finish()
            return

        completions = ai_service_manager.inline_completion_model.inline_completions(prefix, suffix, language, filename, context, cancel_token)
        if cancel_token.is_cancel_requested:
            response_emitter.finish()
            return

        response_emitter.stream({"completions": completions})
        response_emitter.finish()

class NotebookIntelligence(ExtensionApp):
    name = "notebook_intelligence"
    default_url = "/notebook-intelligence"
    load_other_extensions = True
    file_url_prefix = "/render"

    static_paths = []
    template_paths = []
    settings = {}
    handlers = []
    root_dir = ''

    disabled_providers = List(
        trait=Unicode(),
        default_value=None,
        help="""
        List of LLM providers to disable. Valid provider IDs: github-copilot, openai-compatible, litellm-compatible, ollama.

        Example: ['ollama', 'litellm-compatible']
        """,
        allow_none=True,
        config=True,
    )

    allow_enabling_providers_with_env = Bool(
        default_value=False,
        help="""
        Allow enabling disabled providers with environment variable (NBI_ENABLED_PROVIDERS).
        """,
        allow_none=True,
        config=True,
    )

    disabled_tools = List(
        trait=Unicode(),
        default_value=None,
        help="""
        List of built-in tools to disable. Valid tool names: nbi-notebook-edit, nbi-notebook-execute, nbi-python-file-edit, nbi-file-edit, nbi-file-read, nbi-command-execute.

        Example: ['nbi-python-file-edit', 'nbi-command-execute']
        """,
        allow_none=True,
        config=True,
    )

    disabled_coding_agent_launchers = List(
        trait=Unicode(),
        default_value=None,
        help=f"""
        List of coding-agent launcher tiles to hide even when the
        corresponding CLI is on PATH. Valid IDs: {', '.join(VALID_CODING_AGENT_LAUNCHERS)}.

        Example: ['opencode', 'pi']
        """,
        allow_none=True,
        config=True,
    )

    mcp_stdio_command_allowlist = List(
        trait=Unicode(),
        default_value=None,
        help="""
        Regex allowlist for the stdio MCP server `command` field. When
        non-empty, every stdio MCP server (added via Claude `mcp add`
        and loaded from `mcp.json`) must match at least one pattern;
        otherwise the admin gate rejects the server. Empty list (the
        default) means no enforcement.

        Patterns are matched with `re.search`. Anchor with `^...$` to
        require an exact binary, otherwise `'uv'` matches both `uv` and
        `uvtool`. Anchor on an absolute path (`'^/usr/local/bin/uv$'`)
        if you want to defeat PATH-poisoning that points at a different
        binary with the same basename. The complementary `env` denylist
        (PATH, LD_PRELOAD, PYTHONPATH, NODE_OPTIONS, etc.) is always
        applied to stdio servers regardless of this setting.

        Patterns can be added per pod via the
        `NBI_MCP_STDIO_COMMAND_ALLOWLIST` environment variable (CSV;
        appends to this list).

        Scope: this gate validates the binary `command` only. `args`
        flow through unchecked, so an allowlist that permits `npx` will
        still accept `args: ['-y', 'evil-pkg']`. Admins who need
        argv-level control should point `command` at a wrapper script
        they own that bakes the safe argv in.

        Example: ['^uv$', '^uvx$', '^npx$', '^/usr/local/bin/.*']
        """,
        allow_none=True,
        config=True,
    )

    allow_enabling_coding_agent_launchers_with_env = Bool(
        default_value=False,
        help="""
        Allow re-enabling disabled coding-agent launcher tiles per pod via
        the NBI_ENABLED_CODING_AGENT_LAUNCHERS environment variable.
        """,
        allow_none=True,
        config=True,
    )

    allow_enabling_tools_with_env = Bool(
        default_value=False,
        help="""
        Allow enabling disabled tools with environment variable (NBI_ENABLED_BUILTIN_TOOLS).
        """,
        allow_none=True,
        config=True,
    )

    enable_chat_feedback = Bool(
        default_value=False,
        help="""
        Enable chat (thumb up/down) feedback feature.
        """,
        allow_none=True,
        config=True,
    )

    enable_chat_feedback_always_visible = Bool(
        default_value=False,
        help="""
        Show chat feedback (thumb up/down) buttons at full opacity for every
        assistant reply, instead of revealing them only on hover. Has no
        effect unless `enable_chat_feedback` is also True. Use this to drive
        higher feedback engagement when the hover affordance is being missed.
        """,
        allow_none=True,
        config=True,
    )

    allow_github_skill_import = Bool(
        default_value=True,
        help="""
        Allow importing Skills from GitHub via the Skills panel. Set to False
        to hide the "Import from GitHub" affordance and reject backend imports.
        Overridden by the NBI_ALLOW_GITHUB_SKILL_IMPORT env var.
        """,
        allow_none=True,
        config=True,
    )

    additional_skipped_workspace_directories = List(
        trait=Unicode(),
        default_value=None,
        help="""
        Extra directory names to skip when enumerating workspace files for
        the chat-sidebar @-mention picker. Merged with the built-in skip set
        (`__pycache__`, `node_modules`); dotfiles and dot-directories are
        already filtered separately, so entries starting with `.` are no-ops.

        The NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES env var (csv)
        appends to this list at server startup so spawn profiles can vary
        the policy without forking config.

        Match is by directory name only (not path), case-sensitive.

        Example: ['build', 'dist', 'target']
        """,
        allow_none=True,
        config=True,
    )

    explain_error_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the inline error-explanation feature on failed
        cells. "user-choice" (default) lets users toggle the feature in the
        Settings panel. "force-on" locks it enabled, "force-off" locks it
        disabled. Overridden by the NBI_EXPLAIN_ERROR_POLICY env var.
        """,
        config=True,
    )

    output_followup_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the "Ask about this output" affordance on cell
        outputs. Same semantics as explain_error_policy. Overridden by the
        NBI_OUTPUT_FOLLOWUP_POLICY env var.
        """,
        config=True,
    )

    output_toolbar_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the hover toolbar over cell outputs that
        surfaces Explain / Ask / Troubleshoot buttons. Same semantics as
        explain_error_policy. Overridden by the NBI_OUTPUT_TOOLBAR_POLICY
        env var.
        """,
        config=True,
    )

    claude_mode_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for whether Claude mode is enabled. Same semantics as
        explain_error_policy. Overridden by the NBI_CLAUDE_MODE_POLICY env var.
        """,
        config=True,
    )

    acp_mode_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_FORCE_OFF,
        help="""
        Org-wide policy for whether the experimental ACP agent mode (Codex and
        future Agent Client Protocol agents) is available (#378). Defaults to
        force-off; set to user-choice to let users enable it. Overridden by
        the NBI_ACP_MODE_POLICY env var.
        """,
        config=True,
    )

    acp_full_access_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_FORCE_OFF,
        help="""
        Org-wide policy for ACP agent "full access" (#378): running tools
        autonomously without asking. Defaults to force-off, so the agent is
        pinned to ask before anything beyond trusted read-only commands. Set
        to user-choice to let users opt into unattended runs, or force-on to
        require it. Overridden by the NBI_ACP_FULL_ACCESS_POLICY env var.
        """,
        config=True,
    )

    claude_continue_conversation_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for whether Claude remembers conversation history.
        Overridden by the NBI_CLAUDE_CONTINUE_CONVERSATION_POLICY env var.
        """,
        config=True,
    )

    claude_code_tools_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for whether the Claude Code built-in tool set is
        granted to the agent. Overridden by the NBI_CLAUDE_CODE_TOOLS_POLICY
        env var.
        """,
        config=True,
    )

    claude_jupyter_ui_tools_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for whether the Jupyter UI tool set is granted to
        Claude. Overridden by the NBI_CLAUDE_JUPYTER_UI_TOOLS_POLICY env var.
        """,
        config=True,
    )

    claude_setting_source_user_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for whether Claude reads the user-scoped settings
        source (~/.claude/settings.json). Overridden by the
        NBI_CLAUDE_SETTING_SOURCE_USER_POLICY env var.
        """,
        config=True,
    )

    claude_setting_source_project_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for whether Claude reads the project-scoped settings
        source. Overridden by the NBI_CLAUDE_SETTING_SOURCE_PROJECT_POLICY
        env var.
        """,
        config=True,
    )

    store_github_access_token_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for whether the GitHub Copilot access token is
        persisted to disk. Overridden by the
        NBI_STORE_GITHUB_ACCESS_TOKEN_POLICY env var.
        """,
        config=True,
    )

    skills_management_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the Skills management UI. "user-choice" (default)
        and "force-on" both leave the Skills tab visible. There is no user
        toggle to override, so neither value flips a user-visible setting on
        its own — the three-value shape is preserved for symmetry with the
        other feature_policies. "force-off" is the materially different
        case: hides the tab, returns 403 from /skills handlers, and *also*
        disables the managed-skills reconciler. Orgs that ship curated
        skills via NBI_SKILLS_MANIFEST should leave this on user-choice and
        rely on filesystem permissions instead. Overridden by the
        NBI_SKILLS_MANAGEMENT_POLICY env var.
        """,
        config=True,
    )

    claude_mcp_management_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the Claude-mode MCP Servers management tab.
        "user-choice" (default) and "force-on" both leave the tab visible
        when Claude mode is on and the Claude CLI is available; the two are
        behaviorally identical here (no user toggle to override). "force-off"
        hides the tab and returns 403 from /claude-mcp handlers. Independent
        of the existing non-Claude `MCP Servers` tab, which is governed by
        `mcp_server_settings` (its own surface). Overridden by the
        NBI_CLAUDE_MCP_MANAGEMENT_POLICY env var.
        """,
        config=True,
    )

    claude_plugins_management_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the Claude-mode Plugins management tab.
        "user-choice" (default) and "force-on" both leave the tab visible
        when Claude mode is on and the Claude CLI is available; the two are
        behaviorally identical here (no user toggle to override). "force-off"
        hides the tab and returns 403 from /plugins handlers. Overridden by
        the NBI_CLAUDE_PLUGINS_MANAGEMENT_POLICY env var. Mirrors the
        `claude_` prefix on `claude_mcp_management_policy` since both gate
        Claude-only management surfaces.
        """,
        config=True,
    )

    claude_bypass_permissions_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_FORCE_OFF,
        help="""
        Org-wide policy for offering "Bypass Permissions" in the Claude
        permission-mode selector. "force-off" (the default; the only
        policy that doesn't default to user-choice) hides the option and
        the server rejects bypass requests. "user-choice" exposes the
        option; the user still has to arm it explicitly per session.
        "force-on" grants the same availability as user-choice and never
        auto-arms bypass. Independent of this policy, bypass is refused
        whenever Claude Code's enterprise managed settings set
        permissions.disableBypassPermissionsMode. Overridden by the
        NBI_CLAUDE_BYPASS_PERMISSIONS_POLICY env var.
        """,
        config=True,
    )

    terminal_drag_drop_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the terminal drag-drop file-attach feature.
        "user-choice" (default) and "force-on" both enable the listener.
        "force-off" suppresses the listener so files dragged onto a
        terminal fall through to the browser's default behavior; useful
        for hardened deployments that don't want files staged through
        the upload endpoint. Overridden by the
        NBI_TERMINAL_DRAG_DROP_POLICY env var.
        """,
        config=True,
    )

    refresh_open_files_on_disk_change_policy = TraitletEnum(
        list(VALID_POLICIES),
        default_value=POLICY_USER_CHOICE,
        help="""
        Org-wide policy for the open-files refresh watcher. "user-choice"
        (default) honors the user's `refresh_open_files_on_disk_change`
        setting from config.json. "force-on" pins the watcher on
        regardless of the user setting; "force-off" pins it off. Overridden
        by the NBI_REFRESH_OPEN_FILES_ON_DISK_CHANGE_POLICY env var.
        """,
        config=True,
    )

    upload_max_mb = Int(
        default_value=_DEFAULT_UPLOAD_MAX_MB,
        help="""
        Per-file size cap (megabytes) for the chat-sidebar and terminal
        drag-drop upload endpoint. Requests exceeding this limit get a
        413. Set to 0 to disable the cap entirely. Overridden by the
        NBI_UPLOAD_MAX_MB env var.
        """,
        config=True,
    )

    upload_retention_hours = Int(
        default_value=_DEFAULT_UPLOAD_RETENTION_HOURS,
        help="""
        How long staged uploads survive before they're swept on the next
        upload. Files still referenced by a long-running Claude session
        past this window will be unreachable, so tune higher for long
        sessions. Set to 0 to disable the lazy sweep and keep only the
        atexit purge. Overridden by the NBI_UPLOAD_RETENTION_HOURS env
        var.
        """,
        config=True,
    )

    allow_github_plugin_import = Bool(
        default_value=True,
        help="""
        Allow adding plugin marketplaces from GitHub (URL or owner/repo
        shorthand). Set False to hide the "From GitHub" affordance in the
        plugins panel and reject backend marketplace-add requests whose
        source string is a GitHub reference. Local-path and arbitrary-URL
        sources remain available — this is a fine-grained gate paralleling
        `allow_github_skill_import`. Overridden by the
        NBI_ALLOW_GITHUB_PLUGIN_IMPORT env var.
        """,
        allow_none=True,
        config=True,
    )

    skills_manifest = Unicode(
        default_value="",
        help="""
        One or more YAML/JSON manifests describing managed Claude skills to
        install and keep in sync. Each entry is a URL or filesystem path;
        list multiple manifests as a comma-separated string. Manifests are
        unioned with first-wins dedupe on URL collisions and a per-entry
        error on installed-name collisions. Empty disables the feature.
        Overridden by the NBI_SKILLS_MANIFEST environment variable.
        """,
        config=True,
    )

    skills_manifest_interval = Int(
        default_value=86400,
        help="""
        Interval in seconds between managed-skills reconciles.
        Overridden by the NBI_SKILLS_MANIFEST_INTERVAL environment variable.
        """,
        config=True,
    )

    managed_skills_token = Unicode(
        default_value="",
        help="""
        Optional bearer token used for ALL managed-skills GitHub operations:
        fetching the manifest, probing commits, and downloading skill tarballs.
        Lets an org scope a minimal-privilege token for the whole managed
        pathway without affecting user-initiated imports (which continue to use
        GITHUB_TOKEN / GH_TOKEN / `gh auth`). Overridden by the
        NBI_MANAGED_SKILLS_TOKEN environment variable.
        """,
        config=True,
    )

    skill_max_archive_mb = Int(
        default_value=100,
        help="""
        Per-archive on-wire size cap (megabytes) for skill bundles fetched
        from GitHub. Requests exceeding this limit are rejected before
        the archive is written to disk. Default is 100 MB; raise for
        repos with sizable attachments (datasets, fixtures) and lower
        for hardened deployments. Overridden by the
        NBI_SKILL_MAX_ARCHIVE_MB environment variable.
        """,
        config=True,
    )

    tour_config_path = Unicode(
        default_value="",
        help="""
        Filesystem path to a YAML (or JSON) file with admin overrides for
        the in-app first-run tour copy. Lets a deployment rewrite step
        titles, descriptions, button labels, and the launcher-tile
        templates without rebuilding the extension. The file is read on
        every capabilities call so edits take effect on the next sidebar
        mount; a missing, oversized, or malformed file falls back to the
        built-in defaults with a single WARN. Overridden by the
        NBI_TOUR_CONFIG_PATH environment variable.
        """,
        config=True,
    )

    def initialize_settings(self):
        pass

    def _publish_policies(self, feature_policies: dict, string_overrides: dict) -> None:
        """Wire the resolved policies into the HTTP handlers.

        ``nbi_config`` is already configured during ``AIServiceManager.__init__``
        so its model-bootstrap path sees the policy-resolved values.
        """
        GetCapabilitiesHandler.feature_policies = feature_policies
        GetCapabilitiesHandler.string_overrides = string_overrides
        ConfigHandler.feature_policies = feature_policies
        ConfigHandler.string_overrides = string_overrides

    def initialize_handlers(self):
        NotebookIntelligence.root_dir = self.serverapp.root_dir
        set_jupyter_root_dir(NotebookIntelligence.root_dir)
        server_root_dir = os.path.expanduser(self.serverapp.web_app.settings["server_root_dir"])
        # Resolve admin policies first so the AI service sees the locked
        # values during its initial model bootstrap (e.g. NBI_CLAUDE_MODE_POLICY
        # =force-on actually starts Claude mode rather than waiting for the
        # first capabilities GET).
        feature_policies = {
            name: _resolve_policy_with_env(env_var, getattr(self, attr))
            for name, env_var, attr in FEATURE_POLICY_SPEC
        }
        string_overrides = {
            name: os.environ.get(env_var, "").strip()
            for name, env_var in STRING_OVERRIDE_SPEC
        }
        self.initialize_ai_service(
            server_root_dir, feature_policies, string_overrides
        )
        self._setup_handlers(self.serverapp.web_app, feature_policies, string_overrides)
        self.serverapp.log.info(f"Registered {self.name} server extension")

    def initialize_ai_service(
        self,
        server_root_dir: str,
        feature_policies: dict,
        string_overrides: dict,
    ):
        global ai_service_manager
        manifest_sources = _resolve_skills_manifest_sources(self.skills_manifest)
        # When skills management is force-off, suppress all manifests so the
        # reconciler isn't constructed at all (org-curated skills wouldn't
        # have a UI surface anyway, and stopping reconcile is the contract).
        if is_force_off(feature_policies, "skills_management"):
            manifest_sources = []
        managed_token = (
            os.environ.get("NBI_MANAGED_SKILLS_TOKEN", "").strip()
            or self.managed_skills_token.strip()
        )
        interval_env = os.environ.get("NBI_SKILLS_MANIFEST_INTERVAL", "").strip()
        manifest_interval = self.skills_manifest_interval
        if interval_env:
            try:
                manifest_interval = int(interval_env)
            except ValueError:
                log.warning(
                    "Ignoring invalid NBI_SKILLS_MANIFEST_INTERVAL=%r", interval_env
                )
        mcp_command_allowlist = _resolve_csv_appended(
            "NBI_MCP_STDIO_COMMAND_ALLOWLIST",
            self.mcp_stdio_command_allowlist,
        )
        ai_service_manager = AIServiceManager({
            "server_root_dir": server_root_dir,
            "skills_manifest_sources": manifest_sources,
            "skills_manifest_interval": manifest_interval,
            "managed_skills_token": managed_token,
            "feature_policies": feature_policies,
            "string_overrides": string_overrides,
            "mcp_stdio_command_allowlist": mcp_command_allowlist,
        })

    def initialize_templates(self):
        pass

    async def stop_extension(self):
        log.info(f"Stopping {self.name} extension...")
        github_copilot.handle_stop_request()
        ai_service_manager.handle_stop_request()

    def _setup_handlers(self, web_app, feature_policies: dict, string_overrides: dict):
        host_pattern = ".*$"

        base_url = web_app.settings["base_url"]
        route_pattern_capabilities = url_path_join(base_url, "notebook-intelligence", "capabilities")
        route_pattern_config = url_path_join(base_url, "notebook-intelligence", "config")
        route_pattern_update_provider_models = url_path_join(base_url, "notebook-intelligence", "update-provider-models")
        route_pattern_mcp_config_file = url_path_join(base_url, "notebook-intelligence", "mcp-config-file")
        route_pattern_reload_mcp_servers = url_path_join(base_url, "notebook-intelligence", "reload-mcp-servers")
        route_pattern_emit_telemetry_event = url_path_join(base_url, "notebook-intelligence", "emit-telemetry-event")
        route_pattern_github_login_status = url_path_join(base_url, "notebook-intelligence", "gh-login-status")
        route_pattern_github_login = url_path_join(base_url, "notebook-intelligence", "gh-login")
        route_pattern_github_logout = url_path_join(base_url, "notebook-intelligence", "gh-logout")
        route_pattern_copilot = url_path_join(base_url, "notebook-intelligence", "copilot")
        route_pattern_rules = url_path_join(base_url, "notebook-intelligence", "rules")
        route_pattern_rules_toggle = url_path_join(base_url, "notebook-intelligence", "rules", r"([^/]+)", "toggle")
        route_pattern_rules_reload = url_path_join(base_url, "notebook-intelligence", "rules", "reload")
        skill_name = f"({SKILL_NAME_REGEX})"
        route_pattern_skills = url_path_join(base_url, "notebook-intelligence", "skills")
        route_pattern_skills_context = url_path_join(base_url, "notebook-intelligence", "skills", "context")
        route_pattern_skills_import_preview = url_path_join(base_url, "notebook-intelligence", "skills", "import", "preview")
        route_pattern_skills_import = url_path_join(base_url, "notebook-intelligence", "skills", "import")
        route_pattern_skills_reconcile = url_path_join(base_url, "notebook-intelligence", "skills", "reconcile")
        route_pattern_skills_sync_all = url_path_join(base_url, "notebook-intelligence", "skills", "sync-all-tracking")
        route_pattern_skill_sync = url_path_join(base_url, "notebook-intelligence", "skills", r"(user|project)", skill_name, "sync")
        route_pattern_skills_reconciler_stop = url_path_join(
            base_url, "notebook-intelligence", "skills", "reconciler", "stop"
        )
        route_pattern_skill_detail = url_path_join(base_url, "notebook-intelligence", "skills", r"(user|project)", skill_name)
        route_pattern_skill_rename = url_path_join(base_url, "notebook-intelligence", "skills", r"(user|project)", skill_name, "rename")
        route_pattern_skill_bundle_file = url_path_join(base_url, "notebook-intelligence", "skills", r"(user|project)", skill_name, "files")
        route_pattern_skill_bundle_file_rename = url_path_join(base_url, "notebook-intelligence", "skills", r"(user|project)", skill_name, "files", "rename")
        route_pattern_upload_file = url_path_join(base_url, "notebook-intelligence", "upload-file")
        route_pattern_claude_sessions = url_path_join(base_url, "notebook-intelligence", "claude-sessions")
        route_pattern_claude_sessions_resume = url_path_join(base_url, "notebook-intelligence", "claude-sessions", "resume")
        route_pattern_acp_sessions = url_path_join(base_url, "notebook-intelligence", "acp-sessions")
        route_pattern_acp_sessions_resume = url_path_join(base_url, "notebook-intelligence", "acp-sessions", "resume")
        route_pattern_claude_mcp = url_path_join(base_url, "notebook-intelligence", "claude-mcp")
        route_pattern_claude_mcp_detail = url_path_join(
            base_url, "notebook-intelligence", "claude-mcp", r"(user|project|local)", r"([^/]+)"
        )
        route_pattern_plugins = url_path_join(base_url, "notebook-intelligence", "plugins")
        route_pattern_plugins_detail = url_path_join(
            base_url, "notebook-intelligence", "plugins", r"(user|project|local)", r"([^/]+)"
        )
        route_pattern_plugins_marketplace = url_path_join(
            base_url, "notebook-intelligence", "plugins", "marketplace"
        )
        route_pattern_plugins_marketplace_plugins = url_path_join(
            base_url,
            "notebook-intelligence",
            "plugins",
            "marketplace",
            r"([^/]+)",
            "plugins",
        )
        route_pattern_plugins_marketplace_detail = url_path_join(
            base_url, "notebook-intelligence", "plugins", "marketplace", r"([^/]+)"
        )
        route_pattern_plugins_marketplace_update = url_path_join(
            base_url,
            "notebook-intelligence",
            "plugins",
            "marketplace",
            r"([^/]+)",
            "update",
        )
        GetCapabilitiesHandler.disabled_tools = self.disabled_tools
        GetCapabilitiesHandler.allow_enabling_tools_with_env = self.allow_enabling_tools_with_env
        GetCapabilitiesHandler.disabled_providers = self.disabled_providers
        GetCapabilitiesHandler.allow_enabling_providers_with_env = self.allow_enabling_providers_with_env
        # Validate at startup so a typo fails loudly rather than silently
        # no-opping at request time. Empty / None passes through.
        validate_coding_agent_launcher_ids(self.disabled_coding_agent_launchers)
        GetCapabilitiesHandler.disabled_coding_agent_launchers = (
            self.disabled_coding_agent_launchers or []
        )
        GetCapabilitiesHandler.allow_enabling_coding_agent_launchers_with_env = (
            self.allow_enabling_coding_agent_launchers_with_env
        )
        GetCapabilitiesHandler.enable_chat_feedback = self.enable_chat_feedback
        GetCapabilitiesHandler.enable_chat_feedback_always_visible = (
            self.enable_chat_feedback_always_visible
        )
        # Tour copy overrides: env var wins if set, otherwise fall back to
        # the traitlet. Pre-resolve here so the handler doesn't have to
        # re-check os.environ on every call.
        GetCapabilitiesHandler.tour_config_path = (
            os.environ.get("NBI_TOUR_CONFIG_PATH", "").strip()
            or (self.tour_config_path or "").strip()
        )
        SkillsBaseHandler.allow_github_skill_import = _resolve_bool_with_env(
            "NBI_ALLOW_GITHUB_SKILL_IMPORT", self.allow_github_skill_import
        )
        # Three-layer merge for skipped workspace directories: traitlet
        # (admin baseline) → NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES env
        # var (per-pod) → nbi_config.json (admin + user). All layers append
        # rather than override (the policy is set-union semantics so each
        # layer can only add hidden directories, never re-expose them).
        # Manual config.json edits require a JupyterLab restart, matching
        # the rest of NBI config — there's no Settings-dialog control.
        merged_skipped_dirs = _resolve_csv_appended(
            "NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES",
            self.additional_skipped_workspace_directories,
        )
        config_skipped = (
            ai_service_manager.nbi_config.additional_skipped_workspace_directories
        )
        GetCapabilitiesHandler.additional_skipped_workspace_directories = list(
            dict.fromkeys(merged_skipped_dirs + config_skipped)
        )
        SkillsBaseHandler.skills_management_enabled = not is_force_off(
            feature_policies, "skills_management"
        )
        ClaudeMCPBaseHandler.claude_mcp_management_enabled = not is_force_off(
            feature_policies, "claude_mcp_management"
        )
        ClaudeMCPBaseHandler.mcp_stdio_command_allowlist = (
            ai_service_manager.get_mcp_stdio_command_allowlist()
        )
        PluginsBaseHandler.claude_plugins_management_enabled = not is_force_off(
            feature_policies, "claude_plugins_management"
        )
        WebsocketCopilotHandler.claude_bypass_permissions_allowed = not is_force_off(
            feature_policies, "claude_bypass_permissions"
        )
        PluginsBaseHandler.allow_github_plugin_import = _resolve_bool_with_env(
            "NBI_ALLOW_GITHUB_PLUGIN_IMPORT", self.allow_github_plugin_import
        )
        # Resolved on-wire cap for skill tarball fetches. The constant
        # lives in skill_github_import.py because the fetch helper reads
        # it directly; reassigning the module attribute here lets admins
        # tune it without forking the import flow.
        from notebook_intelligence import skill_github_import
        skill_github_import.MAX_ARCHIVE_BYTES = (
            _resolve_positive_int_with_env(
                "NBI_SKILL_MAX_ARCHIVE_MB", self.skill_max_archive_mb
            )
            * 1024
            * 1024
        )
        FileUploadHandler.upload_max_mb = _resolve_positive_int_with_env(
            "NBI_UPLOAD_MAX_MB", self.upload_max_mb
        )
        FileUploadHandler.upload_retention_hours = _resolve_positive_int_with_env(
            "NBI_UPLOAD_RETENTION_HOURS", self.upload_retention_hours
        )
        self._publish_policies(feature_policies, string_overrides)
        NotebookIntelligence.handlers = [
            (route_pattern_capabilities, GetCapabilitiesHandler),
            (route_pattern_config, ConfigHandler),
            (route_pattern_update_provider_models, UpdateProviderModelsHandler),
            (route_pattern_mcp_config_file, MCPConfigFileHandler),
            (route_pattern_reload_mcp_servers, ReloadMCPServersHandler),
            (route_pattern_emit_telemetry_event, EmitTelemetryEventHandler),
            (route_pattern_github_login_status, GetGitHubLoginStatusHandler),
            (route_pattern_github_login, PostGitHubLoginHandler),
            (route_pattern_github_logout, GetGitHubLogoutHandler),
            (route_pattern_rules, RulesListHandler),
            (route_pattern_rules_toggle, RulesToggleHandler),
            (route_pattern_rules_reload, RulesReloadHandler),
            # Skill routes: order matters. Tornado matches in registration order, and the
            # SKILL_NAME_REGEX in `skill_detail` would otherwise eat "import", "context", etc.
            # Always register more specific routes before the {scope}/{name} catch-all.
            (route_pattern_skills, SkillsListHandler),
            (route_pattern_skills_context, SkillsContextHandler),
            (route_pattern_skills_import_preview, SkillsImportPreviewHandler),
            (route_pattern_skills_import, SkillsImportHandler),
            (route_pattern_skills_reconcile, SkillsReconcileHandler),
            (route_pattern_skills_sync_all, SkillsSyncAllTrackingHandler),
            (route_pattern_skill_sync, SkillSyncHandler),
            # Deliberately not gated by SkillsBaseHandler — the kill switch
            # must remain reachable while skills_management_policy=force-off
            # is the active state. See the handler docstring.
            (route_pattern_skills_reconciler_stop, SkillsReconcilerStopHandler),
            (route_pattern_skill_bundle_file_rename, SkillBundleFileRenameHandler),
            (route_pattern_skill_bundle_file, SkillBundleFileHandler),
            (route_pattern_skill_rename, SkillRenameHandler),
            (route_pattern_skill_detail, SkillDetailHandler),
            (route_pattern_upload_file, FileUploadHandler),
            (route_pattern_claude_sessions_resume, ClaudeSessionsResumeHandler),
            (route_pattern_claude_sessions, ClaudeSessionsListHandler),
            (route_pattern_acp_sessions_resume, AcpSessionsResumeHandler),
            (route_pattern_acp_sessions, AcpSessionsListHandler),
            # Claude-MCP routes: detail before list so {scope}/{name} doesn't
            # shadow specialized URLs added later (parallels the skills order).
            (route_pattern_claude_mcp_detail, ClaudeMCPDetailHandler),
            (route_pattern_claude_mcp, ClaudeMCPListHandler),
            # Plugin routes: marketplace endpoints before the {scope}/{plugin}
            # catch-all so the literal "marketplace" segment isn't eaten.
            (
                route_pattern_plugins_marketplace_plugins,
                PluginsMarketplacePluginsHandler,
            ),
            (
                route_pattern_plugins_marketplace_update,
                PluginsMarketplaceUpdateHandler,
            ),
            (route_pattern_plugins_marketplace_detail, PluginsMarketplaceDetailHandler),
            (route_pattern_plugins_marketplace, PluginsMarketplaceListHandler),
            (route_pattern_plugins_detail, PluginsDetailHandler),
            (route_pattern_plugins, PluginsListHandler),
            (route_pattern_copilot, WebsocketCopilotHandler),
        ]
        web_app.add_handlers(host_pattern, NotebookIntelligence.handlers)
