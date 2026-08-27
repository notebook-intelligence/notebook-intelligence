from unittest.mock import Mock, patch

from notebook_intelligence.api import ChatRequest
from notebook_intelligence.rule_injector import RuleInjector, _TOKEN_ENCODING
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
        
        expected = "# Additional Guidelines\n# Test Rules\n- Be helpful"
        assert result == expected

    def test_disabled_rules_also_suppress_agents_md(self, tmp_path):
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = None
        request.host.nbi_config.rules_enabled = False
        (tmp_path / "AGENTS.md").write_text(
            "# Repo Rules\n- Keep notebooks tidy\n",
            encoding="utf-8",
        )

        with patch(
            "notebook_intelligence.rule_injector.get_jupyter_root_dir",
            return_value=str(tmp_path),
        ):
            result = injector.inject_rules("BASE", request)

        assert result == "BASE"

    def test_token_budget_truncates_additional_guidelines(self):
        injector = RuleInjector()
        request = Mock(spec=ChatRequest)
        request.rule_context = Mock(spec=RuleContext)
        request.host.nbi_config.rules_enabled = True
        rule_manager = Mock()
        rule_manager.get_applicable_rules.return_value = [Mock(spec=Rule)]
        rule_manager.format_rules_for_llm.return_value = "rule " * 1_000
        request.host.get_rule_manager.return_value = rule_manager

        result = injector.inject_rules("BASE", request, max_tokens=30)

        assert result.startswith("BASE\n\n# Additional Guidelines")
        assert result.endswith("...[additional guidelines truncated]")
        assert len(_TOKEN_ENCODING.encode(result)) <= 30

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


class TestChatbookGuidelines:
    def test_has_chatbook_guidelines_from_agents_md(self, tmp_path):
        from notebook_intelligence.rule_injector import has_chatbook_guidelines

        (tmp_path / 'AGENTS.md').write_text('# Keep cells short\n', encoding='utf-8')
        host = Mock()
        host.nbi_config.rules_enabled = True
        host.get_rule_manager.return_value = None
        with patch(
            'notebook_intelligence.rule_injector.get_jupyter_root_dir',
            return_value=str(tmp_path),
        ):
            assert has_chatbook_guidelines(host) is True

    def test_has_chatbook_guidelines_false_when_rules_disabled(self, tmp_path):
        from notebook_intelligence.rule_injector import has_chatbook_guidelines

        (tmp_path / 'AGENTS.md').write_text('# Keep cells short\n', encoding='utf-8')
        host = Mock()
        host.nbi_config.rules_enabled = False
        with patch(
            'notebook_intelligence.rule_injector.get_jupyter_root_dir',
            return_value=str(tmp_path),
        ):
            assert has_chatbook_guidelines(host) is False

    def test_has_chatbook_guidelines_from_chatbook_rules(self, tmp_path):
        from notebook_intelligence.rule_injector import has_chatbook_guidelines
        from notebook_intelligence.rule_manager import RuleManager

        rules_dir = tmp_path / 'rules'
        (rules_dir / 'modes' / 'chatbook').mkdir(parents=True)
        (rules_dir / 'modes' / 'chatbook' / '01.md').write_text(
            'Use type hints.\n', encoding='utf-8'
        )
        host = Mock()
        host.nbi_config.rules_enabled = True
        host.get_rule_manager.return_value = RuleManager(str(rules_dir))
        with patch(
            'notebook_intelligence.rule_injector.get_jupyter_root_dir',
            return_value=str(tmp_path / 'empty'),
        ):
            assert has_chatbook_guidelines(host) is True

    def test_has_chatbook_guidelines_false_without_sources(self, tmp_path):
        from notebook_intelligence.rule_injector import has_chatbook_guidelines
        from notebook_intelligence.rule_manager import RuleManager

        rules_dir = tmp_path / 'rules'
        rules_dir.mkdir()
        host = Mock()
        host.nbi_config.rules_enabled = True
        host.get_rule_manager.return_value = RuleManager(str(rules_dir))
        with patch(
            'notebook_intelligence.rule_injector.get_jupyter_root_dir',
            return_value=str(tmp_path),
        ):
            assert has_chatbook_guidelines(host) is False
