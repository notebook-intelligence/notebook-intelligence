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

