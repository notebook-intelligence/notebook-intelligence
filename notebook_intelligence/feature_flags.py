# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from typing import Tuple

POLICY_USER_CHOICE = "user-choice"
POLICY_FORCE_ON = "force-on"
POLICY_FORCE_OFF = "force-off"
VALID_POLICIES = (POLICY_USER_CHOICE, POLICY_FORCE_ON, POLICY_FORCE_OFF)

# Tool-array members of `claude_settings.tools` that have their own policy.
CLAUDE_CODE_TOOLS_ID = "claude-code:built-in-tools"
JUPYTER_UI_TOOLS_ID = "nbi:built-in-jupyter-ui-tools"

# Override-key → destination-key mappings for the value-presence-locks env vars.
CHAT_MODEL_OVERRIDES = (
    ("chat_model_provider", "provider"),
    ("chat_model_id", "model"),
)
INLINE_COMPLETION_MODEL_OVERRIDES = (
    ("inline_completion_model_provider", "provider"),
    ("inline_completion_model_id", "model"),
)
CLAUDE_SETTINGS_OVERRIDES = (
    ("claude_chat_model", "chat_model"),
    ("claude_inline_completion_model", "inline_completion_model"),
    ("claude_api_key", "api_key"),
    ("claude_base_url", "base_url"),
)
ACP_SETTINGS_OVERRIDES = (
    ("acp_chat_model", "chat_model"),
    ("acp_api_key", "api_key"),
    ("acp_base_url", "base_url"),
)


def resolve_feature_flag(policy: str, user_setting: bool) -> Tuple[bool, bool]:
    """Return ``(enabled, locked)`` for a feature.

    Unknown policy strings fall through to user-choice so a config typo
    fails open rather than locking the user out.
    """
    if policy == POLICY_FORCE_ON:
        return True, True
    if policy == POLICY_FORCE_OFF:
        return False, True
    return bool(user_setting), False


def is_locked(policy: str) -> bool:
    return policy in (POLICY_FORCE_ON, POLICY_FORCE_OFF)


def is_force_off(policies: dict, name: str) -> bool:
    """True iff ``policies[name]`` is force-off (missing == user-choice).

    The canonical predicate for backend kill-switches: a handler-level prepare
    gate, a reconciler-init guard, etc. all want the same "is this admin-
    disabled" answer.
    """
    return policies.get(name, POLICY_USER_CHOICE) == POLICY_FORCE_OFF


def apply_string_overrides(target: dict, overrides: dict, mapping: tuple) -> dict:
    """Apply value-presence-locks per ``mapping`` to a copy of ``target``.

    Each tuple in ``mapping`` is ``(override_key, dest_key)``. A non-empty
    value in ``overrides[override_key]`` is written to ``dest_key`` in the
    result. Empty/missing entries leave the destination untouched.
    """
    if not any(overrides.get(ov_key) for ov_key, _ in mapping):
        return target
    result = dict(target)
    for ov_key, dest_key in mapping:
        v = overrides.get(ov_key)
        if v:
            result[dest_key] = v
    return result


def apply_member_policy(members: list, item: str, policy: str) -> list:
    """Return a copy of ``members`` with ``item`` added/removed per ``policy``.

    user-choice leaves the list untouched. force-on ensures presence, force-off
    ensures absence. Any unknown policy is treated as user-choice.
    """
    if policy == POLICY_FORCE_ON:
        if item not in members:
            return list(members) + [item]
        return list(members)
    if policy == POLICY_FORCE_OFF:
        return [m for m in members if m != item]
    return list(members)


def apply_acp_policies(acp_settings: dict, policies: dict) -> dict:
    """Apply admin policies to an ``acp_settings`` dict (issue #378).

    Two gates: ``acp_mode`` clamps ``enabled``, and ``acp_full_access``
    clamps ``full_access`` (the autonomous, run-without-asking posture, which
    defaults to force-off like Claude's bypass-permissions). Used on both the
    read path and the write-filter path, like ``apply_claude_policies``.
    """
    result = dict(acp_settings or {})
    mode_policy = policies.get("acp_mode", POLICY_USER_CHOICE)
    if mode_policy == POLICY_FORCE_ON:
        result["enabled"] = True
    elif mode_policy == POLICY_FORCE_OFF:
        result["enabled"] = False
    full_access_policy = policies.get("acp_full_access", POLICY_USER_CHOICE)
    if full_access_policy == POLICY_FORCE_ON:
        result["full_access"] = True
    elif full_access_policy == POLICY_FORCE_OFF:
        result["full_access"] = False
    return result


def apply_claude_policies(claude_settings: dict, policies: dict) -> dict:
    """Apply admin policies to a ``claude_settings`` dict.

    Returns a shallow copy with any forced fields overwritten. ``policies`` is
    a mapping from policy name (e.g. ``claude_mode``) to a value in
    ``VALID_POLICIES``. Missing keys are treated as user-choice.

    The same function is used on both the read path (to compute the resolved
    settings the SDK and UI see) and the write path (to filter incoming POSTs
    so a user can't flip a locked field via a hand-rolled API call).
    """
    result = dict(claude_settings or {})

    mode_policy = policies.get("claude_mode", POLICY_USER_CHOICE)
    if mode_policy == POLICY_FORCE_ON:
        result["enabled"] = True
    elif mode_policy == POLICY_FORCE_OFF:
        result["enabled"] = False

    cc_policy = policies.get("claude_continue_conversation", POLICY_USER_CHOICE)
    if cc_policy == POLICY_FORCE_ON:
        result["continue_conversation"] = True
    elif cc_policy == POLICY_FORCE_OFF:
        result["continue_conversation"] = False

    tools = list(result.get("tools") or [])
    tools = apply_member_policy(
        tools,
        CLAUDE_CODE_TOOLS_ID,
        policies.get("claude_code_tools", POLICY_USER_CHOICE),
    )
    tools = apply_member_policy(
        tools,
        JUPYTER_UI_TOOLS_ID,
        policies.get("claude_jupyter_ui_tools", POLICY_USER_CHOICE),
    )
    result["tools"] = tools

    sources = list(result.get("setting_sources") or [])
    sources = apply_member_policy(
        sources,
        "user",
        policies.get("claude_setting_source_user", POLICY_USER_CHOICE),
    )
    sources = apply_member_policy(
        sources,
        "project",
        policies.get("claude_setting_source_project", POLICY_USER_CHOICE),
    )
    result["setting_sources"] = sources

    return result
