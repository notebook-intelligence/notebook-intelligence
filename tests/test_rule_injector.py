import pytest
from unittest.mock import Mock, MagicMock
from notebook_intelligence.rule_injector import RuleInjector
from notebook_intelligence.api import ChatRequest
from notebook_intelligence.ruleset import RuleContext, Rule, RuleScope


class TestRuleInjector:
    def test_inject_rules_no_context(self):
        """Test rule injection when no notebook context is provided."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = None
        
        base_prompt = "You are a helpful assistant."
        result = injector.inject_rules(base_prompt, request)
        
        assert result == base_prompt
    
    def test_inject_rules_no_rule_manager(self):
        """Test rule injection when no rule manager is available."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        request.host.get_rule_manager.return_value = None
        
        base_prompt = "You are a helpful assistant."
        result = injector.inject_rules(base_prompt, request)
        
        assert result == base_prompt
    
    def test_inject_rules_disabled(self):
        """Test rule injection when rules are disabled."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        request.host.get_rule_manager.return_value = Mock()
        request.host.nbi_config.rules_enabled = False
        
        base_prompt = "You are a helpful assistant."
        result = injector.inject_rules(base_prompt, request)
        
        assert result == base_prompt
    
    def test_inject_rules_context_injection_disabled(self):
        """Test rule injection when context injection is disabled."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        request.host.get_rule_manager.return_value = Mock()
        request.host.nbi_config.rules_enabled = True
        request.host.nbi_config.rules_context_injection_enabled = False
        
        base_prompt = "You are a helpful assistant."
        result = injector.inject_rules(base_prompt, request)
        
        assert result == base_prompt
    
    def test_inject_rules_no_applicable_rules(self):
        """Test rule injection when no rules apply to the context."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        
        rule_manager = Mock()
        rule_manager.get_applicable_rules.return_value = []
        request.host.get_rule_manager.return_value = rule_manager
        request.host.nbi_config.rules_enabled = True
        request.host.nbi_config.rules_context_injection_enabled = True
        
        base_prompt = "You are a helpful assistant."
        result = injector.inject_rules(base_prompt, request)
        
        assert result == base_prompt
        rule_manager.get_applicable_rules.assert_called_once_with(request.rule_context)
    
    def test_inject_rules_with_applicable_rules(self):
        """Test rule injection with applicable rules."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        
        # Create mock rules
        rule1 = Mock(spec=Rule)
        rule2 = Mock(spec=Rule)
        applicable_rules = [rule1, rule2]
        
        rule_manager = Mock()
        rule_manager.get_applicable_rules.return_value = applicable_rules
        rule_manager.format_rules_for_llm.return_value = "# Test Rules\n- Follow coding standards\n- Use descriptive names"
        
        request.host.get_rule_manager.return_value = rule_manager
        request.host.nbi_config.rules_enabled = True
        request.host.nbi_config.rules_context_injection_enabled = True
        
        base_prompt = "You are a helpful assistant."
        result = injector.inject_rules(base_prompt, request)
        
        expected = "You are a helpful assistant.\n\n# Additional Guidelines\n# Test Rules\n- Follow coding standards\n- Use descriptive names"
        assert result == expected
        
        rule_manager.get_applicable_rules.assert_called_once_with(request.rule_context)
        rule_manager.format_rules_for_llm.assert_called_once_with(applicable_rules)
    
    def test_inject_rules_empty_base_prompt(self):
        """Test rule injection with empty base prompt."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        
        rule_manager = Mock()
        rule_manager.get_applicable_rules.return_value = [Mock(spec=Rule)]
        rule_manager.format_rules_for_llm.return_value = "# Test Rules\n- Be helpful"
        
        request.host.get_rule_manager.return_value = rule_manager
        request.host.nbi_config.rules_enabled = True
        request.host.nbi_config.rules_context_injection_enabled = True
        
        base_prompt = ""
        result = injector.inject_rules(base_prompt, request)
        
        expected = "\n\n# Additional Guidelines\n# Test Rules\n- Be helpful"
        assert result == expected
