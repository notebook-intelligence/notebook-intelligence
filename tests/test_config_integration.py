import pytest
import tempfile
from unittest.mock import patch, MagicMock

from notebook_intelligence.config import NBIConfig


class TestNBIConfigRulesIntegration:
    def test_rules_enabled_default(self, mock_nbi_config):
        """Test that rules are enabled by default."""
        config = mock_nbi_config
        config.user_config = {}
        config.env_config = {}
        
        assert config.rules_enabled is True
    
    def test_rules_enabled_configured(self, mock_nbi_config):
        """Test rules_enabled configuration."""
        config = mock_nbi_config
        config.user_config = {'rules_enabled': False}
        
        assert config.rules_enabled is False
    
    def test_rules_directory_default(self, mock_nbi_config):
        """Test default rules directory path."""
        config = mock_nbi_config
        
        expected_path = config.nbi_user_dir + "/rules"
        assert config.rules_directory == expected_path
    
    def test_active_rules_default(self, mock_nbi_config):
        """Test active_rules default value."""
        config = mock_nbi_config
        config.user_config = {}
        
        assert config.active_rules == {}
    
    def test_active_rules_configured(self, mock_nbi_config):
        """Test configured active_rules."""
        config = mock_nbi_config
        config.user_config = {
            'active_rules': {
                '01-test.md': True,
                '02-test.md': False
            }
        }
        
        active_rules = config.active_rules
        assert active_rules['01-test.md'] is True
        assert active_rules['02-test.md'] is False
    
    
    def test_config_inheritance_user_overrides_env(self, mock_nbi_config):
        """Test that user config overrides environment config for rules."""
        config = mock_nbi_config
        config.env_config = {'rules_enabled': True}
        config.user_config = {'rules_enabled': False}
        
        # User config should override env config
        assert config.rules_enabled is False
    
    def test_config_falls_back_to_env(self, mock_nbi_config):
        """Test fallback to environment config when user config is missing."""
        config = mock_nbi_config
        config.env_config = {'rules_enabled': False}
        config.user_config = {}
        
        # Should fall back to env config
        assert config.rules_enabled is False
    
    def test_backward_compatibility_no_rules_config(self, mock_nbi_config):
        """Test that existing configs without rules settings still work."""
        config = mock_nbi_config
        config.user_config = {
            'chat_model': {'provider': 'github-copilot', 'model': 'gpt-4'},
            'inline_completion_model': {'provider': 'github-copilot', 'model': 'gpt-4o'}
        }
        config.env_config = {}
        
        # Should not affect existing functionality
        assert config.chat_model['provider'] == 'github-copilot'
        
        # Should provide defaults for new rules functionality
        assert config.rules_enabled is True
        assert config.active_rules == {}


class TestNBIConfigSaveAndLoad:
    def test_save_includes_rules_config(self, tmp_path, mock_nbi_config):
        """Test that saving config includes rules configuration."""
        config = mock_nbi_config
        config.nbi_user_dir = str(tmp_path)
        config.user_config_file = str(tmp_path / "config.json")
        config.user_mcp_file = str(tmp_path / "mcp.json")

        config.user_config = {
            'rules_enabled': False,
            'active_rules': {'test.md': True}
        }
        config.user_mcp = {}

        # Mock the actual save methods since we're testing the integration
        with patch('builtins.open'), patch('json.dump') as mock_dump, patch('os.makedirs'):
            config.save()

            # Should be called twice (once for config, once for mcp)
            assert mock_dump.call_count == 2

            # First call should be for the main config
            first_call_args = mock_dump.call_args_list[0][0]
            saved_config = first_call_args[0]

            assert 'rules_enabled' in saved_config
            assert saved_config['rules_enabled'] is False
            assert saved_config['active_rules'] == {'test.md': True}


class TestAdditionalSkippedWorkspaceDirectories:
    """The setting layers env-prefix and user `config.json` entries.
    Both contribute (set-union); the final merge with the traitlet
    and the `NBI_*` env var happens in `_setup_handlers`."""

    def test_returns_empty_when_neither_layer_sets_it(self, mock_nbi_config):
        config = mock_nbi_config
        config.env_config = {}
        config.user_config = {}
        assert config.additional_skipped_workspace_directories == []

    def test_reads_from_env_config_only(self, mock_nbi_config):
        config = mock_nbi_config
        config.env_config = {'additional_skipped_workspace_directories': ['build']}
        config.user_config = {}
        assert config.additional_skipped_workspace_directories == ['build']

    def test_reads_from_user_config_only(self, mock_nbi_config):
        config = mock_nbi_config
        config.env_config = {}
        config.user_config = {'additional_skipped_workspace_directories': ['venv']}
        assert config.additional_skipped_workspace_directories == ['venv']

    def test_merges_env_and_user_layers_preserving_order(self, mock_nbi_config):
        config = mock_nbi_config
        config.env_config = {'additional_skipped_workspace_directories': ['build', 'dist']}
        config.user_config = {'additional_skipped_workspace_directories': ['venv']}
        assert config.additional_skipped_workspace_directories == [
            'build', 'dist', 'venv'
        ]

    def test_dedupes_across_layers(self, mock_nbi_config):
        config = mock_nbi_config
        config.env_config = {'additional_skipped_workspace_directories': ['build']}
        config.user_config = {'additional_skipped_workspace_directories': ['build', 'venv']}
        assert config.additional_skipped_workspace_directories == ['build', 'venv']

    def test_ignores_non_string_entries(self, mock_nbi_config):
        config = mock_nbi_config
        config.env_config = {
            'additional_skipped_workspace_directories': ['build', None, 42, '']
        }
        config.user_config = {}
        assert config.additional_skipped_workspace_directories == ['build']

    def test_ignores_non_list_payload(self, mock_nbi_config):
        # A malformed config.json (string instead of list) must not crash.
        config = mock_nbi_config
        config.env_config = {'additional_skipped_workspace_directories': 'build,dist'}
        config.user_config = {}
        assert config.additional_skipped_workspace_directories == []


class TestAdditionalSkippedWorkspaceDirectoriesFourLayerMerge:
    """End-to-end merge across all four layers — traitlet, NBI_* env
    var, env-prefix config.json, user config.json — exercising the
    expression in `extension._setup_handlers` that publishes the final
    list onto `GetCapabilitiesHandler`. Reproduces the merge in
    isolation so a regression in the merge order or dedupe doesn't slip
    past the two single-layer test suites that already exist."""

    @staticmethod
    def _merge(traitlet, env_var_value, env_config_layer, user_config_layer,
               mock_nbi_config, monkeypatch):
        from notebook_intelligence import extension as ext_module

        if env_var_value is None:
            monkeypatch.delenv(
                'NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES', raising=False
            )
        else:
            monkeypatch.setenv(
                'NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES', env_var_value
            )
        mock_nbi_config.env_config = (
            {'additional_skipped_workspace_directories': env_config_layer}
            if env_config_layer is not None else {}
        )
        mock_nbi_config.user_config = (
            {'additional_skipped_workspace_directories': user_config_layer}
            if user_config_layer is not None else {}
        )
        merged_traitlet_env = ext_module._resolve_csv_appended(
            'NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES', traitlet
        )
        config_layers = mock_nbi_config.additional_skipped_workspace_directories
        # Same expression as extension._setup_handlers.
        return list(dict.fromkeys(merged_traitlet_env + config_layers))

    def test_all_four_layers_union_in_traversal_order(
        self, mock_nbi_config, monkeypatch
    ):
        result = self._merge(
            traitlet=['from_traitlet'],
            env_var_value='from_env_var',
            env_config_layer=['from_env_config'],
            user_config_layer=['from_user_config'],
            mock_nbi_config=mock_nbi_config,
            monkeypatch=monkeypatch,
        )
        assert result == [
            'from_traitlet',
            'from_env_var',
            'from_env_config',
            'from_user_config',
        ]

    def test_collision_across_layers_keeps_first_seen(
        self, mock_nbi_config, monkeypatch
    ):
        result = self._merge(
            traitlet=['build'],
            env_var_value='dist',
            env_config_layer=['build', 'out'],  # 'build' already from traitlet
            user_config_layer=['dist', 'venv'],  # 'dist' already from env var
            mock_nbi_config=mock_nbi_config,
            monkeypatch=monkeypatch,
        )
        # First-seen order from traitlet → env var → env config → user config.
        assert result == ['build', 'dist', 'out', 'venv']

    def test_only_user_config_set(self, mock_nbi_config, monkeypatch):
        result = self._merge(
            traitlet=None,
            env_var_value=None,
            env_config_layer=None,
            user_config_layer=['venv'],
            mock_nbi_config=mock_nbi_config,
            monkeypatch=monkeypatch,
        )
        assert result == ['venv']

    def test_no_layers_set_yields_empty(self, mock_nbi_config, monkeypatch):
        result = self._merge(
            traitlet=None,
            env_var_value=None,
            env_config_layer=None,
            user_config_layer=None,
            mock_nbi_config=mock_nbi_config,
            monkeypatch=monkeypatch,
        )
        assert result == []


class TestSkillsManifestResolver:
    """Pin the env-var-vs-traitlet contract for the multi-manifest wire
    format. The resolver is the only place that knows about CSV split
    semantics for `NBI_SKILLS_MANIFEST`; without this test, a future
    refactor that swaps `or` for `if` could silently lose the second
    manifest source.
    """

    def test_env_wins_over_traitlet(self, monkeypatch):
        from notebook_intelligence import extension as ext_module
        monkeypatch.setenv("NBI_SKILLS_MANIFEST", "https://from-env/m.yaml")
        result = ext_module._resolve_skills_manifest_sources(
            "https://from-traitlet/m.yaml"
        )
        assert result == ["https://from-env/m.yaml"]

    def test_traitlet_used_when_env_empty(self, monkeypatch):
        from notebook_intelligence import extension as ext_module
        monkeypatch.delenv("NBI_SKILLS_MANIFEST", raising=False)
        result = ext_module._resolve_skills_manifest_sources(
            "https://from-traitlet/m.yaml"
        )
        assert result == ["https://from-traitlet/m.yaml"]

    def test_comma_separated_env_yields_list(self, monkeypatch):
        from notebook_intelligence import extension as ext_module
        monkeypatch.setenv(
            "NBI_SKILLS_MANIFEST",
            "  https://a/m.yaml, https://b/m.yaml ,/srv/c.yaml",
        )
        assert ext_module._resolve_skills_manifest_sources("") == [
            "https://a/m.yaml",
            "https://b/m.yaml",
            "/srv/c.yaml",
        ]

    def test_comma_separated_traitlet_yields_list(self, monkeypatch):
        from notebook_intelligence import extension as ext_module
        monkeypatch.delenv("NBI_SKILLS_MANIFEST", raising=False)
        assert ext_module._resolve_skills_manifest_sources(
            "https://a/m.yaml,https://b/m.yaml"
        ) == ["https://a/m.yaml", "https://b/m.yaml"]

    def test_all_separator_input_disables(self, monkeypatch):
        # Operator who writes `"  ,,,  "` is functionally clearing the env
        # var; the resolver should land on "no manifests" rather than
        # construct phantom empty sources that would then fail to load.
        from notebook_intelligence import extension as ext_module
        monkeypatch.setenv("NBI_SKILLS_MANIFEST", " , ,  , ")
        # Falls through to traitlet (which is also empty here).
        assert ext_module._resolve_skills_manifest_sources("") == []

    def test_empty_env_and_traitlet_returns_empty(self, monkeypatch):
        from notebook_intelligence import extension as ext_module
        monkeypatch.delenv("NBI_SKILLS_MANIFEST", raising=False)
        assert ext_module._resolve_skills_manifest_sources("") == []

    def test_whitespace_only_env_falls_through_to_traitlet(self, monkeypatch):
        from notebook_intelligence import extension as ext_module
        monkeypatch.setenv("NBI_SKILLS_MANIFEST", "   ")
        assert ext_module._resolve_skills_manifest_sources(
            "https://from-traitlet/m.yaml"
        ) == ["https://from-traitlet/m.yaml"]


class TestNBIConfigPolicyResolution:
    """The boolean policies and string overrides feed through NBIConfig getters
    so SDK consumers (claude.py, ai_service_manager) see resolved values."""

    def test_chat_model_string_overrides_apply(self, mock_nbi_config):
        config = mock_nbi_config
        config.user_config = {
            'chat_model': {'provider': 'github-copilot', 'model': 'gpt-4o'}
        }
        config.set_feature_policies(
            {},
            {'chat_model_provider': 'ollama', 'chat_model_id': 'llama3:latest'},
        )
        assert config.chat_model == {'provider': 'ollama', 'model': 'llama3:latest'}

    def test_chat_model_partial_override_preserves_other_field(self, mock_nbi_config):
        config = mock_nbi_config
        config.user_config = {
            'chat_model': {'provider': 'github-copilot', 'model': 'gpt-4o'}
        }
        config.set_feature_policies({}, {'chat_model_provider': 'ollama'})
        # provider locked, model still user's choice
        assert config.chat_model == {'provider': 'ollama', 'model': 'gpt-4o'}

    def test_claude_settings_force_on_overrides_user_off(self, mock_nbi_config):
        from notebook_intelligence.feature_flags import POLICY_FORCE_ON
        config = mock_nbi_config
        config.user_config = {'claude_settings': {'enabled': False}}
        config.set_feature_policies({'claude_mode': POLICY_FORCE_ON}, {})
        assert config.claude_settings['enabled'] is True

    def test_claude_settings_api_key_override_applied_for_sdk(self, mock_nbi_config):
        config = mock_nbi_config
        config.user_config = {'claude_settings': {'api_key': 'sk-user'}}
        config.set_feature_policies(
            {}, {'claude_api_key': 'sk-locked-by-env'}
        )
        # SDK side sees the env-supplied key (claude.py forwards it).
        assert config.claude_settings['api_key'] == 'sk-locked-by-env'

    def test_no_overrides_returns_user_value_unchanged(self, mock_nbi_config):
        config = mock_nbi_config
        config.user_config = {
            'chat_model': {'provider': 'github-copilot', 'model': 'gpt-4o'}
        }
        config.set_feature_policies({}, {})
        # Identity check guarantees no needless allocation in the common case.
        assert config.chat_model is config.user_config['chat_model']



class TestClaudeSettingsInlineChatModelPin:
    """The read path must apply the clamp, not merely define it.

    ``clamp_inline_chat_model`` is unit-tested in ``test_feature_flags.py``.
    This pins that ``NBIConfig.claude_settings`` actually calls it, so deleting
    the call cannot silently reopen the chat-model pin bypass.
    """

    def _config(self, mock_nbi_config, stored, overrides):
        config = mock_nbi_config
        config.user_config = {"claude_settings": dict(stored)}
        config.env_config = {}
        config.set_feature_policies({}, overrides)
        return config

    def test_chat_pin_blanks_a_stored_inline_model(self, mock_nbi_config):
        config = self._config(
            mock_nbi_config,
            {"chat_model": "user-chat", "inline_chat_model": "user-inline"},
            {"claude_chat_model": "pinned"},
        )
        resolved = config.claude_settings
        assert resolved["chat_model"] == "pinned"
        assert resolved["inline_chat_model"] == ""

    def test_inline_pin_survives_a_chat_pin(self, mock_nbi_config):
        # The case the feature exists for: two pins, two different models.
        config = self._config(
            mock_nbi_config,
            {"chat_model": "user-chat", "inline_chat_model": "user-inline"},
            {
                "claude_chat_model": "pinned",
                "claude_inline_chat_model": "pinned-inline",
            },
        )
        resolved = config.claude_settings
        assert resolved["chat_model"] == "pinned"
        assert resolved["inline_chat_model"] == "pinned-inline"

    def test_no_pins_leaves_the_stored_inline_model(self, mock_nbi_config):
        config = self._config(
            mock_nbi_config,
            {"chat_model": "user-chat", "inline_chat_model": "user-inline"},
            {},
        )
        assert config.claude_settings["inline_chat_model"] == "user-inline"
