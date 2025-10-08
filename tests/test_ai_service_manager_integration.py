import pytest
from unittest.mock import Mock, patch
from notebook_intelligence.ai_service_manager import AIServiceManager
from notebook_intelligence.rule_manager import RuleManager


class TestAIServiceManagerIntegration:
    def test_init_with_rules_enabled(self):
        """Test AIServiceManager initialization with rules enabled."""
        with patch('notebook_intelligence.ai_service_manager.NBIConfig') as mock_config_class:
            mock_config = Mock()
            mock_config.rules_enabled = True
            mock_config.rules_directory = "/test/rules"
            mock_config.mcp = {"mcpServers": {}, "participants": {}}
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
            mock_config_class.return_value = mock_config
            
            manager = AIServiceManager({"server_root_dir": "/test"})
            
            # Should not raise an exception
            manager.reload_rules()
