import pytest
from unittest.mock import Mock, patch
from notebook_intelligence.ai_service_manager import AIServiceManager
from notebook_intelligence.rule_manager import RuleManager


# AIServiceManager.__init__ constructs a ClaudeCodeChatParticipant whose own __init__
# reads nbi_config.claude_settings and does `ToolType in settings.get('tools', [])`.
# A bare Mock() returns another Mock from .get(), which isn't iterable — so every
# mock_config in this file sets `claude_settings = {}` to keep that check happy.
class TestAIServiceManagerIntegration:
    def test_init_with_rules_enabled(self):
        """Test AIServiceManager initialization with rules enabled."""
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = True
            mock_config.rules_directory = "/test/rules"
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
            mock_config.user_skills_directory = "/test/user_skills"
            mock_config.project_skills_directory = lambda _root: "/test/project_skills"
            mock_config.claude_settings = {}
            mock_config_class.return_value = mock_config
            
            with patch('notebook_intelligence.ai_service_manager.RuleManager') as mock_rule_manager_class:
                mock_rule_manager = Mock(spec=RuleManager)
                mock_rule_manager_class.return_value = mock_rule_manager
                
                manager = AIServiceManager({"server_root_dir": "/test"})
                
                assert manager._rule_manager is mock_rule_manager
                mock_rule_manager_class.assert_called_once_with("/test/rules")
    
    def test_init_with_rules_disabled(self):
        """Test AIServiceManager initialization with rules disabled."""
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = False
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
            mock_config.user_skills_directory = "/test/user_skills"
            mock_config.project_skills_directory = lambda _root: "/test/project_skills"
            mock_config.claude_settings = {}
            mock_config_class.return_value = mock_config
            
            manager = AIServiceManager({"server_root_dir": "/test"})
            
            assert manager._rule_manager is None
    
    def test_get_rule_manager_when_available(self):
        """Test getting rule manager when it's available."""
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = True
            mock_config.rules_directory = "/test/rules"
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
            mock_config.user_skills_directory = "/test/user_skills"
            mock_config.project_skills_directory = lambda _root: "/test/project_skills"
            mock_config.claude_settings = {}
            mock_config_class.return_value = mock_config
            
            with patch('notebook_intelligence.ai_service_manager.RuleManager') as mock_rule_manager_class:
                mock_rule_manager = Mock(spec=RuleManager)
                mock_rule_manager_class.return_value = mock_rule_manager
                
                manager = AIServiceManager({"server_root_dir": "/test"})
                
                result = manager.get_rule_manager()
                assert result is mock_rule_manager
    
    def test_get_rule_manager_when_not_available(self):
        """Test getting rule manager when it's not available."""
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = False
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
            mock_config.user_skills_directory = "/test/user_skills"
            mock_config.project_skills_directory = lambda _root: "/test/project_skills"
            mock_config.claude_settings = {}
            mock_config_class.return_value = mock_config
            
            manager = AIServiceManager({"server_root_dir": "/test"})
            
            result = manager.get_rule_manager()
            assert result is None
    
    def test_reload_rules_when_available(self):
        """Test reloading rules when rule manager is available."""
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = True
            mock_config.rules_directory = "/test/rules"
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
            mock_config.user_skills_directory = "/test/user_skills"
            mock_config.project_skills_directory = lambda _root: "/test/project_skills"
            mock_config.claude_settings = {}
            mock_config_class.return_value = mock_config
            
            with patch('notebook_intelligence.ai_service_manager.RuleManager') as mock_rule_manager_class:
                mock_rule_manager = Mock(spec=RuleManager)
                mock_rule_manager_class.return_value = mock_rule_manager
                
                manager = AIServiceManager({"server_root_dir": "/test"})
                
                manager.reload_rules()
                
                mock_rule_manager.load_rules.assert_called_once_with(force_reload=True)
    
    def test_reload_rules_when_not_available(self):
        """Test reloading rules when rule manager is not available."""
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = False
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
            mock_config.user_skills_directory = "/test/user_skills"
            mock_config.project_skills_directory = lambda _root: "/test/project_skills"
            mock_config.claude_settings = {}
            mock_config_class.return_value = mock_config

            manager = AIServiceManager({"server_root_dir": "/test"})

            # Should not raise an exception
            manager.reload_rules()

    # The next three tests target the update_models_from_config Claude
    # branch directly rather than going through AIServiceManager(), so
    # we don't get tangled with ClaudeCodeChatParticipant's background
    # SDK handshake thread (which spawns from ClaudeCodeClient.__init__
    # and waits up to 15s on the connect resolver — a real headache to
    # mock through AIServiceManager construction).
    def _make_manager_for_update_test(self, claude_settings):
        """Build a minimal AIServiceManager for testing the Claude
        branch of update_models_from_config in isolation.
        """
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = False
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
            mock_config.user_skills_directory = "/test/user_skills"
            mock_config.project_skills_directory = lambda _root: "/test/project_skills"
            mock_config.claude_settings = {}
            mock_config.chat_model = {"provider": "none", "model": "none"}
            mock_config.inline_completion_model = {"provider": "none", "model": "none"}
            mock_config.using_github_copilot_service = False
            mock_config_class.return_value = mock_config
            manager = AIServiceManager({"server_root_dir": "/test"})
        # Now swap in the real Claude settings for update_models_from_config
        # to read. The participant is already constructed (with an empty
        # claude_settings dict), so this swap only affects the branch
        # we're about to exercise.
        manager.nbi_config.claude_settings = claude_settings
        return manager

    def test_claude_mode_triggers_model_fetch_when_cache_empty(self):
        """When Claude mode is enabled and the model cache is empty,
        update_models_from_config should fire a background fetch so the
        capabilities response surfaces the list to the settings panel
        (issue #235: the persisted chat_model showed as Default because
        the dropdown had no options to render against).
        """
        manager = self._make_manager_for_update_test({
            "enabled": True,
            "chat_model": "claude-sonnet-4-6",
            "api_key": "test-key",
        })
        # Patch Thread so target runs inline; sidesteps a poll loop and
        # the daemon-thread teardown race that flaked the earlier draft.
        def _run_inline(target=None, kwargs=None, **_):
            if target is not None:
                target(**(kwargs or {}))
            return Mock()
        with patch(
            'notebook_intelligence.ai_service_manager.fetch_claude_models'
        ) as mock_fetch, patch(
            'notebook_intelligence.ai_service_manager.get_claude_models',
            return_value=[],
        ), patch(
            'notebook_intelligence.ai_service_manager.threading.Thread',
            side_effect=_run_inline,
        ):
            manager.update_models_from_config()
        assert mock_fetch.call_count >= 1, (
            "Expected fetch_claude_models to be invoked when cache is "
            "empty and Claude mode is enabled"
        )
        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs.get("api_key") == "test-key"

    def test_claude_mode_skips_fetch_when_cache_already_populated(self):
        """If the Claude model cache is already populated (e.g. a prior
        startup or a manual refresh), don't re-fetch on every config
        refresh — the existing list is good enough and the round trip
        wastes a request to the Anthropic API.
        """
        manager = self._make_manager_for_update_test({
            "enabled": True,
            "chat_model": "claude-sonnet-4-6",
            "api_key": "test-key",
        })
        with patch(
            'notebook_intelligence.ai_service_manager.fetch_claude_models'
        ) as mock_fetch, patch(
            'notebook_intelligence.ai_service_manager.get_claude_models',
            return_value=[{"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"}],
        ):
            manager.update_models_from_config()
        assert mock_fetch.call_count == 0

    def test_no_fetch_when_claude_mode_disabled(self):
        """Claude mode disabled = no fetch, regardless of cache state.
        Don't reach out to the Anthropic API for users not using Claude.
        """
        manager = self._make_manager_for_update_test({"enabled": False})
        with patch(
            'notebook_intelligence.ai_service_manager.fetch_claude_models'
        ) as mock_fetch, patch(
            'notebook_intelligence.ai_service_manager.get_claude_models',
            return_value=[],
        ):
            manager.update_models_from_config()
        assert mock_fetch.call_count == 0


class TestActiveAgentMode:
    """Active-agent resolution after the modes became mutually exclusive
    (#378 review feedback): no preference, at most one enabled mode, Claude
    wins as the safety net when a hand-edited config enables both.
    """

    def _manager_with(self, claude_enabled: bool, acp_enabled: bool) -> AIServiceManager:
        # active_agent_mode only reads the two settings dicts, so a bare
        # instance with a stub config is enough — no participant/SDK setup.
        manager = AIServiceManager.__new__(AIServiceManager)
        mock_config = Mock()
        mock_config.claude_settings = {"enabled": claude_enabled}
        mock_config.acp_settings = {"enabled": acp_enabled}
        manager._nbi_config = mock_config
        return manager

    def test_none_enabled_returns_none(self):
        manager = self._manager_with(claude_enabled=False, acp_enabled=False)
        assert manager.active_agent_mode is None
        assert not manager.is_claude_code_mode
        assert not manager.is_acp_mode

    def test_claude_only(self):
        manager = self._manager_with(claude_enabled=True, acp_enabled=False)
        assert manager.active_agent_mode == "claude"
        assert manager.is_claude_code_mode
        assert not manager.is_acp_mode

    def test_acp_only(self):
        manager = self._manager_with(claude_enabled=False, acp_enabled=True)
        assert manager.active_agent_mode == "acp"
        assert manager.is_acp_mode
        assert not manager.is_claude_code_mode

    def test_both_enabled_claude_wins(self):
        # ConfigHandler enforces exclusivity on every save, so both-enabled
        # can only come from a hand-edited config file; the priority order
        # keeps the historical "Claude wins" behavior for that case.
        manager = self._manager_with(claude_enabled=True, acp_enabled=True)
        assert manager.active_agent_mode == "claude"
        assert manager.is_claude_code_mode
        assert not manager.is_acp_mode
