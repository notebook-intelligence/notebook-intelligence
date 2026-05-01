from unittest.mock import Mock, patch

from notebook_intelligence.api import ChatRequest
from notebook_intelligence.rule_injector import RuleInjector
from notebook_intelligence.ruleset import Rule, RuleContext


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
    
    def test_inject_rules_no_applicable_rules(self):
        """Test rule injection when no rules apply to the context."""
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        
        rule_manager = Mock()
        rule_manager.get_applicable_rules.return_value = []
        request.host.get_rule_manager.return_value = rule_manager
        request.host.nbi_config.rules_enabled = True
        
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
        
        base_prompt = ""
        result = injector.inject_rules(base_prompt, request)
        
        expected = "\n\n# Additional Guidelines\n# Test Rules\n- Be helpful"
        assert result == expected

    def test_inject_rules_with_agents_md_only(self, tmp_path):
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = None

        agents_path = tmp_path / 'AGENTS.md'
        agents_path.write_text('# Repo Rules\n- Keep notebooks tidy\n', encoding='utf-8')

        with patch('notebook_intelligence.rule_injector.get_jupyter_root_dir', return_value=str(tmp_path)):
            result = injector.inject_rules('You are a helpful assistant.', request)

        assert 'Repository Instructions (AGENTS.md)' in result
        assert 'Keep notebooks tidy' in result

    def test_inject_rules_combines_agents_md_and_rules(self, tmp_path):
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)

        (tmp_path / 'AGENTS.md').write_text('# Repo Rules\n- Prefer small changes\n', encoding='utf-8')

        rule_manager = Mock()
        rule_manager.get_applicable_rules.return_value = [Mock(spec=Rule)]
        rule_manager.format_rules_for_llm.return_value = '# Test Rules\n- Add tests'

        request.host.get_rule_manager.return_value = rule_manager
        request.host.nbi_config.rules_enabled = True

        with patch('notebook_intelligence.rule_injector.get_jupyter_root_dir', return_value=str(tmp_path)):
            result = injector.inject_rules('You are a helpful assistant.', request)

        assert 'Repository Instructions (AGENTS.md)' in result
        assert 'Prefer small changes' in result
        assert '# Test Rules' in result
        assert 'Add tests' in result
