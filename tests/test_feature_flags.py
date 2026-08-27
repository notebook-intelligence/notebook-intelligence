# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from notebook_intelligence.feature_flags import (
    CLAUDE_CODE_TOOLS_ID,
    clamp_inline_chat_model,
    CLAUDE_SETTINGS_OVERRIDES,
    JUPYTER_UI_TOOLS_ID,
    POLICY_FORCE_OFF,
    POLICY_FORCE_ON,
    POLICY_USER_CHOICE,
    VALID_POLICIES,
    apply_claude_policies,
    apply_member_policy,
    apply_string_overrides,
    is_locked,
    resolve_feature_flag,
)


class TestResolveFeatureFlag:
    def test_user_choice_returns_user_setting_unlocked(self):
        assert resolve_feature_flag(POLICY_USER_CHOICE, True) == (True, False)
        assert resolve_feature_flag(POLICY_USER_CHOICE, False) == (False, False)

    def test_force_on_returns_enabled_locked_regardless_of_user_pref(self):
        assert resolve_feature_flag(POLICY_FORCE_ON, True) == (True, True)
        assert resolve_feature_flag(POLICY_FORCE_ON, False) == (True, True)

    def test_force_off_returns_disabled_locked_regardless_of_user_pref(self):
        assert resolve_feature_flag(POLICY_FORCE_OFF, True) == (False, True)
        assert resolve_feature_flag(POLICY_FORCE_OFF, False) == (False, True)

    def test_unknown_policy_falls_back_to_user_choice(self):
        # Fail open: a typo'd policy must not lock users out of features.
        assert resolve_feature_flag("nonsense", True) == (True, False)
        assert resolve_feature_flag("", False) == (False, False)


class TestPolicyConstants:
    def test_valid_policies_lists_the_three_known_values(self):
        assert set(VALID_POLICIES) == {
            POLICY_USER_CHOICE,
            POLICY_FORCE_ON,
            POLICY_FORCE_OFF,
        }


class TestIsLocked:
    def test_force_policies_are_locked(self):
        assert is_locked(POLICY_FORCE_ON) is True
        assert is_locked(POLICY_FORCE_OFF) is True

    def test_user_choice_is_not_locked(self):
        assert is_locked(POLICY_USER_CHOICE) is False

    def test_unknown_policy_is_not_locked(self):
        assert is_locked("nonsense") is False
        assert is_locked("") is False


class TestApplyMemberPolicy:
    def test_force_on_adds_missing_member(self):
        assert apply_member_policy(["a"], "b", POLICY_FORCE_ON) == ["a", "b"]

    def test_force_on_is_idempotent_when_already_present(self):
        assert apply_member_policy(["a", "b"], "b", POLICY_FORCE_ON) == ["a", "b"]

    def test_force_off_removes_present_member(self):
        assert apply_member_policy(["a", "b"], "b", POLICY_FORCE_OFF) == ["a"]

    def test_force_off_is_idempotent_when_already_absent(self):
        assert apply_member_policy(["a"], "b", POLICY_FORCE_OFF) == ["a"]

    def test_user_choice_leaves_list_untouched(self):
        assert apply_member_policy(["a"], "b", POLICY_USER_CHOICE) == ["a"]
        assert apply_member_policy(["a", "b"], "b", POLICY_USER_CHOICE) == ["a", "b"]


class TestApplyClaudePolicies:
    def test_user_choice_leaves_settings_untouched(self):
        settings = {
            "enabled": True,
            "continue_conversation": False,
            "tools": [CLAUDE_CODE_TOOLS_ID],
            "setting_sources": ["user"],
        }
        result = apply_claude_policies(settings, {})
        assert result["enabled"] is True
        assert result["continue_conversation"] is False
        assert result["tools"] == [CLAUDE_CODE_TOOLS_ID]
        assert result["setting_sources"] == ["user"]

    def test_force_on_overrides_user_off(self):
        result = apply_claude_policies(
            {"enabled": False, "continue_conversation": False},
            {
                "claude_mode": POLICY_FORCE_ON,
                "claude_continue_conversation": POLICY_FORCE_ON,
            },
        )
        assert result["enabled"] is True
        assert result["continue_conversation"] is True

    def test_force_off_overrides_user_on(self):
        result = apply_claude_policies(
            {"enabled": True, "continue_conversation": True},
            {
                "claude_mode": POLICY_FORCE_OFF,
                "claude_continue_conversation": POLICY_FORCE_OFF,
            },
        )
        assert result["enabled"] is False
        assert result["continue_conversation"] is False

    def test_tools_array_membership_is_managed_per_policy(self):
        result = apply_claude_policies(
            {"tools": []},
            {
                "claude_code_tools": POLICY_FORCE_ON,
                "claude_jupyter_ui_tools": POLICY_FORCE_OFF,
            },
        )
        assert CLAUDE_CODE_TOOLS_ID in result["tools"]
        assert JUPYTER_UI_TOOLS_ID not in result["tools"]

    def test_tools_array_force_off_strips_existing_membership(self):
        result = apply_claude_policies(
            {"tools": [CLAUDE_CODE_TOOLS_ID, JUPYTER_UI_TOOLS_ID]},
            {"claude_jupyter_ui_tools": POLICY_FORCE_OFF},
        )
        assert CLAUDE_CODE_TOOLS_ID in result["tools"]
        assert JUPYTER_UI_TOOLS_ID not in result["tools"]

    def test_setting_sources_per_member_policy(self):
        result = apply_claude_policies(
            {"setting_sources": []},
            {
                "claude_setting_source_user": POLICY_FORCE_ON,
                "claude_setting_source_project": POLICY_FORCE_OFF,
            },
        )
        assert "user" in result["setting_sources"]
        assert "project" not in result["setting_sources"]

    def test_does_not_mutate_caller_input(self):
        original = {"tools": [CLAUDE_CODE_TOOLS_ID]}
        apply_claude_policies(
            original, {"claude_jupyter_ui_tools": POLICY_FORCE_ON}
        )
        # The caller's tools list must be unchanged.
        assert original["tools"] == [CLAUDE_CODE_TOOLS_ID]

    def test_handles_missing_tools_and_sources(self):
        # A fresh user with no claude_settings at all.
        result = apply_claude_policies(
            {}, {"claude_code_tools": POLICY_FORCE_ON}
        )
        assert CLAUDE_CODE_TOOLS_ID in result["tools"]
        assert result["setting_sources"] == []

    def test_handles_none_input(self):
        result = apply_claude_policies(None, {})
        assert result["tools"] == []
        assert result["setting_sources"] == []


class TestApplyStringOverrides:
    _MAPPING = (
        ("provider_override", "provider"),
        ("model_override", "model"),
    )

    def test_no_overrides_returns_input_identity(self):
        target = {"provider": "ollama", "model": "llama3:latest"}
        result = apply_string_overrides(target, {}, self._MAPPING)
        # The 99% case (no env vars) must not allocate.
        assert result is target

    def test_empty_string_override_does_not_apply(self):
        target = {"provider": "ollama", "model": "llama3:latest"}
        result = apply_string_overrides(
            target, {"provider_override": ""}, self._MAPPING
        )
        assert result is target

    def test_non_empty_override_writes_dest(self):
        result = apply_string_overrides(
            {"provider": "ollama", "model": "llama3:latest"},
            {"provider_override": "github-copilot"},
            self._MAPPING,
        )
        assert result == {"provider": "github-copilot", "model": "llama3:latest"}

    def test_multiple_overrides_apply_in_mapping_order(self):
        result = apply_string_overrides(
            {},
            {"provider_override": "ollama", "model_override": "llama3:latest"},
            self._MAPPING,
        )
        assert result == {"provider": "ollama", "model": "llama3:latest"}

    def test_does_not_mutate_caller_input(self):
        original = {"provider": "ollama", "model": "llama3:latest"}
        apply_string_overrides(
            original, {"provider_override": "github-copilot"}, self._MAPPING
        )
        assert original == {"provider": "ollama", "model": "llama3:latest"}

    def test_keys_not_in_mapping_are_ignored(self):
        target = {"x": 1}
        result = apply_string_overrides(
            target, {"unrelated": "value"}, self._MAPPING
        )
        assert result is target


class TestInlineChatModelOverride:
    """`claude_inline_chat_model` pins inline chat independently of the chat model.

    Each override writes one destination and only that one. Inline chat still
    follows a chat-model pin when no inline model is set, but that fallback is
    resolved per request in ``handle_inline_chat_request``, not stamped in
    here — which is what keeps a deliberately provisioned ``inline_chat_model``
    from being overwritten by a chat-model pin. The behavioral matrix over both
    layers lives in ``test_claude_client.py``.
    """

    @staticmethod
    def _resolved(stored, overrides):
        return apply_string_overrides(stored, overrides, CLAUDE_SETTINGS_OVERRIDES)

    def test_inline_pin_writes_only_the_inline_key(self):
        assert self._resolved(
            {"chat_model": "user-chat", "inline_chat_model": "user-inline"},
            {"claude_inline_chat_model": "PINNED"},
        ) == {"chat_model": "user-chat", "inline_chat_model": "PINNED"}

    def test_chat_pin_writes_only_the_chat_key(self):
        # The mapping itself never writes across destinations. A chat pin still
        # governs inline chat, but through clamp_inline_chat_model rather than
        # by stamping the pinned id here - see TestClampInlineChatModel.
        assert self._resolved(
            {"chat_model": "user-chat", "inline_chat_model": "user-inline"},
            {"claude_chat_model": "PINNED"},
        ) == {"chat_model": "PINNED", "inline_chat_model": "user-inline"}

    def test_both_pins_apply_to_their_own_destinations(self):
        assert self._resolved(
            {"chat_model": "user-chat", "inline_chat_model": "user-inline"},
            {
                "claude_chat_model": "PIN_CHAT",
                "claude_inline_chat_model": "PIN_INLINE",
            },
        ) == {"chat_model": "PIN_CHAT", "inline_chat_model": "PIN_INLINE"}

    def test_neither_pin_leaves_settings_untouched(self):
        stored = {"chat_model": "user-chat", "inline_chat_model": "user-inline"}
        assert self._resolved(stored, {}) == stored


class TestClampInlineChatModel:
    """A chat-model pin keeps governing inline chat after the keys split.

    Before ``inline_chat_model`` existed, both transports read ``chat_model``,
    so ``NBI_CLAUDE_CHAT_MODEL`` governed inline chat as a side effect. The
    clamp preserves that guarantee by blanking the inline key rather than
    stamping the pinned id into it: "" falls through to ``chat_model``, which
    already holds the pin, so nothing concrete is persisted that could outlive
    it. An admin needing a different raw-API id sets the inline env var, which
    takes precedence and makes the clamp inert.
    """

    def test_chat_pin_blanks_a_stored_inline_model(self):
        assert clamp_inline_chat_model(
            {"chat_model": "pinned", "inline_chat_model": "user-inline"},
            {"claude_chat_model": "pinned"},
        ) == {"chat_model": "pinned", "inline_chat_model": ""}

    def test_inline_pin_makes_the_clamp_inert(self):
        # The case the feature exists for: an admin pinning both keys must get
        # two different models, not one clamped onto the other.
        settings = {"chat_model": "pinned", "inline_chat_model": "pinned-inline"}
        assert clamp_inline_chat_model(
            settings,
            {"claude_chat_model": "pinned", "claude_inline_chat_model": "pinned-inline"},
        ) == settings

    def test_no_chat_pin_is_inert(self):
        settings = {"chat_model": "user-chat", "inline_chat_model": "user-inline"}
        assert clamp_inline_chat_model(settings, {}) == settings

    def test_inline_pin_alone_is_inert(self):
        settings = {"chat_model": "user-chat", "inline_chat_model": "pinned-inline"}
        assert clamp_inline_chat_model(
            settings, {"claude_inline_chat_model": "pinned-inline"}
        ) == settings

    def test_other_keys_are_untouched(self):
        result = clamp_inline_chat_model(
            {"chat_model": "pinned", "inline_chat_model": "x", "api_key": "k",
             "tools": ["a"], "enabled": True},
            {"claude_chat_model": "pinned"},
        )
        assert result["api_key"] == "k"
        assert result["tools"] == ["a"]
        assert result["enabled"] is True

    def test_the_input_is_not_mutated(self):
        settings = {"chat_model": "pinned", "inline_chat_model": "user-inline"}
        clamp_inline_chat_model(settings, {"claude_chat_model": "pinned"})
        assert settings["inline_chat_model"] == "user-inline"
