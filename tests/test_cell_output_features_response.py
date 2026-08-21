# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Tests for the capabilities-response helpers that surface admin policies
and value-presence-locks to the frontend."""

from types import SimpleNamespace

from notebook_intelligence.extension import (
    FEATURE_POLICY_NAMES,
    _build_cell_output_features_response,
    _build_feature_policies_response,
    _build_setting_locks_response,
    _scrub_credentials_for_wire,
)
from notebook_intelligence.feature_flags import (
    CLAUDE_CODE_TOOLS_ID,
    JUPYTER_UI_TOOLS_ID,
    POLICY_FORCE_OFF,
    POLICY_FORCE_ON,
    POLICY_USER_CHOICE,
)


def _config(
    *,
    explain=True,
    followup=True,
    toolbar=True,
    store_token=False,
    refresh_open_files=True,
    claude_settings=None,
    acp_settings=None,
):
    return SimpleNamespace(
        enable_explain_error=explain,
        enable_output_followup=followup,
        enable_output_toolbar=toolbar,
        store_github_access_token=store_token,
        refresh_open_files_on_disk_change=refresh_open_files,
        claude_settings=claude_settings if claude_settings is not None else {},
        acp_settings=acp_settings if acp_settings is not None else {},
    )


class TestBuildCellOutputFeaturesResponse:
    def test_user_choice_reflects_user_preferences_and_unlocked(self):
        response = _build_cell_output_features_response(
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            _config(explain=True, followup=False, toolbar=True),
        )
        assert response == {
            "explain_error": {"enabled": True, "locked": False},
            "output_followup": {"enabled": False, "locked": False},
            "output_toolbar": {"enabled": True, "locked": False},
        }

    def test_force_on_overrides_user_off_and_locks(self):
        response = _build_cell_output_features_response(
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            POLICY_FORCE_ON,
            _config(toolbar=False),
        )
        assert response["output_toolbar"] == {"enabled": True, "locked": True}

    def test_force_off_overrides_user_on_and_locks(self):
        response = _build_cell_output_features_response(
            POLICY_FORCE_OFF,
            POLICY_USER_CHOICE,
            POLICY_USER_CHOICE,
            _config(explain=True),
        )
        assert response["explain_error"] == {"enabled": False, "locked": True}

    def test_each_feature_resolved_independently(self):
        response = _build_cell_output_features_response(
            POLICY_FORCE_ON,
            POLICY_FORCE_OFF,
            POLICY_USER_CHOICE,
            _config(explain=False, followup=True, toolbar=False),
        )
        assert response == {
            "explain_error": {"enabled": True, "locked": True},
            "output_followup": {"enabled": False, "locked": True},
            "output_toolbar": {"enabled": False, "locked": False},
        }


class TestBuildFeaturePoliciesResponse:
    def test_empty_policies_match_user_state_unlocked(self):
        response = _build_feature_policies_response(
            {},
            _config(
                explain=True,
                followup=False,
                toolbar=True,
                store_token=True,
                claude_settings={
                    "enabled": True,
                    "continue_conversation": False,
                    "tools": [CLAUDE_CODE_TOOLS_ID],
                    "setting_sources": ["user", "project"],
                },
            ),
        )
        assert response["explain_error"] == {"enabled": True, "locked": False}
        assert response["claude_mode"] == {"enabled": True, "locked": False}
        assert response["claude_continue_conversation"] == {
            "enabled": False,
            "locked": False,
        }
        assert response["claude_code_tools"] == {"enabled": True, "locked": False}
        assert response["claude_jupyter_ui_tools"] == {
            "enabled": False,
            "locked": False,
        }
        assert response["claude_setting_source_user"] == {
            "enabled": True,
            "locked": False,
        }
        assert response["claude_setting_source_project"] == {
            "enabled": True,
            "locked": False,
        }
        assert response["store_github_access_token"] == {
            "enabled": True,
            "locked": False,
        }

    def test_force_on_locks_each_independently(self):
        response = _build_feature_policies_response(
            {
                "claude_mode": POLICY_FORCE_ON,
                "claude_jupyter_ui_tools": POLICY_FORCE_OFF,
                "store_github_access_token": POLICY_FORCE_ON,
            },
            _config(
                store_token=False,
                claude_settings={
                    "enabled": False,
                    "tools": [JUPYTER_UI_TOOLS_ID],
                },
            ),
        )
        assert response["claude_mode"] == {"enabled": True, "locked": True}
        assert response["claude_jupyter_ui_tools"] == {
            "enabled": False,
            "locked": True,
        }
        assert response["store_github_access_token"] == {
            "enabled": True,
            "locked": True,
        }
        # Untouched policies stay unlocked and reflect user prefs.
        assert response["claude_continue_conversation"]["locked"] is False

    def test_handles_missing_claude_settings(self):
        # Brand-new install: claude_settings is {} entirely.
        response = _build_feature_policies_response(
            {}, _config(claude_settings={})
        )
        assert response["claude_mode"]["enabled"] is False
        assert response["claude_continue_conversation"]["enabled"] is False
        assert response["claude_code_tools"]["enabled"] is False

    def test_response_includes_every_known_policy_name(self):
        # Pins the wire contract: the response must expose exactly the
        # FEATURE_POLICY_NAMES set so the frontend's iteration over the
        # capabilities entries lines up. Catches drift when a new policy
        # is added to the spec but not to the response builder.
        response = _build_feature_policies_response({}, _config())
        assert set(response.keys()) == set(FEATURE_POLICY_NAMES)

    def test_refresh_open_files_reflects_user_pref_when_unforced(self):
        response = _build_feature_policies_response(
            {}, _config(refresh_open_files=False)
        )
        assert response["refresh_open_files_on_disk_change"] == {
            "enabled": False,
            "locked": False,
        }

    def test_refresh_open_files_force_off_locks_against_user_on(self):
        response = _build_feature_policies_response(
            {"refresh_open_files_on_disk_change": POLICY_FORCE_OFF},
            _config(refresh_open_files=True),
        )
        assert response["refresh_open_files_on_disk_change"] == {
            "enabled": False,
            "locked": True,
        }

    def test_refresh_open_files_force_on_locks_against_user_off(self):
        response = _build_feature_policies_response(
            {"refresh_open_files_on_disk_change": POLICY_FORCE_ON},
            _config(refresh_open_files=False),
        )
        assert response["refresh_open_files_on_disk_change"] == {
            "enabled": True,
            "locked": True,
        }


class TestBuildSettingLocksResponse:
    def test_empty_overrides_means_nothing_locked(self):
        response = _build_setting_locks_response({})
        for entry in response.values():
            assert entry == {"locked": False}

    def test_presence_of_value_locks_the_field(self):
        response = _build_setting_locks_response(
            {
                "chat_model_provider": "ollama",
                "chat_model_id": "",
                "claude_api_key": "sk-secret",
                "claude_base_url": "https://api.anthropic.com",
            }
        )
        assert response["chat_model_provider"] == {"locked": True}
        # Empty string must not lock — that's the user-choice signal.
        assert response["chat_model_id"] == {"locked": False}
        assert response["claude_api_key"] == {"locked": True}
        assert response["claude_base_url"] == {"locked": True}
        # Unrelated keys remain unlocked.
        assert response["claude_chat_model"] == {"locked": False}

    def test_acp_locks_track_their_env_overrides(self):
        response = _build_setting_locks_response(
            {"acp_api_key": "sk-openai", "acp_base_url": ""}
        )
        assert response["acp_api_key"] == {"locked": True}
        # Empty string is the user-choice signal, not a lock.
        assert response["acp_base_url"] == {"locked": False}
        assert response["acp_chat_model"] == {"locked": False}

    def test_response_includes_every_known_lock_name(self):
        response = _build_setting_locks_response({})
        assert set(response.keys()) == {
            "chat_model_provider",
            "chat_model_id",
            "inline_completion_model_provider",
            "inline_completion_model_id",
            "claude_chat_model",
            "claude_inline_completion_model",
            "claude_api_key",
            "claude_base_url",
            "acp_chat_model",
            "acp_api_key",
            "acp_base_url",
        }


class TestScrubCredentialsForWire:
    def test_unlocked_settings_pass_through_unchanged(self):
        settings = {"api_key": "sk-secret", "base_url": "https://x"}
        assert _scrub_credentials_for_wire(settings, {}) is settings

    def test_claude_key_scrubbed_when_env_locks_it(self):
        settings = {"api_key": "sk-secret", "enabled": True}
        scrubbed = _scrub_credentials_for_wire(
            settings, {"claude_api_key": "sk-env"}
        )
        assert scrubbed["api_key"] == ""
        assert scrubbed["enabled"] is True
        # The source dict is not mutated in place.
        assert settings["api_key"] == "sk-secret"

    def test_acp_key_scrubbed_with_its_own_lock_name(self):
        settings = {"api_key": "sk-openai"}
        # The Claude lock must not scrub the Codex key.
        assert (
            _scrub_credentials_for_wire(
                settings, {"claude_api_key": "sk-env"}, "acp_api_key"
            )
            is settings
        )
        scrubbed = _scrub_credentials_for_wire(
            settings, {"acp_api_key": "sk-env"}, "acp_api_key"
        )
        assert scrubbed["api_key"] == ""
