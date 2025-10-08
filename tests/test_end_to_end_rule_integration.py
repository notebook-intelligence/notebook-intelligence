import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import Mock, patch

from notebook_intelligence.rule_manager import RuleManager
from notebook_intelligence.ruleset import RuleContext, Rule, RuleScope
from notebook_intelligence.rule_injector import RuleInjector
from notebook_intelligence.api import ChatRequest
from notebook_intelligence.context_factory import RuleContextFactory


class TestEndToEndRuleIntegration:
    """End-to-end tests for rule integration functionality."""
    
    @pytest.fixture
    def temp_rules_dir(self):
        """Create a temporary rules directory with test rules."""
        with tempfile.TemporaryDirectory() as temp_dir:
            rules_dir = Path(temp_dir) / "rules"
            rules_dir.mkdir()
            
            # Create modes subdirectory
            modes_dir = rules_dir / "modes"
            modes_dir.mkdir()
            (modes_dir / "ask").mkdir()
            (modes_dir / "agent").mkdir()
            
            # Create a global rule
            global_rule = rules_dir / "01-global.md"
            global_rule.write_text("""---
apply: always
scope:
  file_patterns:
    - '*.ipynb'
    - '*.py'
active: true
priority: 0
---

# Global Coding Standards
- Use descriptive variable names
- Add comments for complex logic
""")
            
            # Create an ask mode rule
            ask_rule = modes_dir / "ask" / "01-ask-specific.md"
            ask_rule.write_text("""---
apply: always
scope:
  file_patterns:
    - '*.ipynb'
active: true
priority: 0
---

# Ask Mode Guidelines
- Provide detailed explanations
- Include examples when possible
""")
            
            # Create an agent mode rule
            agent_rule = modes_dir / "agent" / "01-agent-specific.md"
            agent_rule.write_text("""---
apply: always
scope:
  file_patterns:
    - '*.py'
active: true
priority: 0
---

# Agent Mode Guidelines
- Focus on efficient solutions
- Minimize dependencies
""")
            
            yield str(rules_dir)
    
    def test_rule_manager_loads_rules_correctly(self, temp_rules_dir):
        """Test that RuleManager loads rules from directory structure."""
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        all_rules = rule_manager.ruleset.get_all_rules()
        assert len(all_rules) == 3
        
        # Check global rule
        global_rules = rule_manager.ruleset.get_rules_by_mode(None)
        assert len(global_rules) == 1
        assert "Global Coding Standards" in global_rules[0].content
        
        # Check mode-specific rules
        ask_rules = rule_manager.ruleset.get_rules_by_mode("ask")
        assert len(ask_rules) == 1
        assert "Ask Mode Guidelines" in ask_rules[0].content
        
        agent_rules = rule_manager.ruleset.get_rules_by_mode("agent")
        assert len(agent_rules) == 1
        assert "Agent Mode Guidelines" in agent_rules[0].content
    
    def test_rule_application_for_notebook_ask_mode(self, temp_rules_dir):
        """Test rule application for notebook file in ask mode."""
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        # Create context for notebook in ask mode
        context = RuleContext(
            filename="test.ipynb",
            kernel="python3",
            mode="ask"
        )
        
        applicable_rules = rule_manager.get_applicable_rules(context)
        
        # Should get global rule + ask mode rule (both match *.ipynb)
        assert len(applicable_rules) == 2
        
        rule_contents = [rule.content for rule in applicable_rules]
        assert any("Global Coding Standards" in content for content in rule_contents)
        assert any("Ask Mode Guidelines" in content for content in rule_contents)
    
    def test_rule_application_for_python_agent_mode(self, temp_rules_dir):
        """Test rule application for Python file in agent mode."""
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        # Create context for Python file in agent mode
        context = RuleContext(
            filename="script.py",
            kernel="python3",
            mode="agent"
        )
        
        applicable_rules = rule_manager.get_applicable_rules(context)
        
        # Should get global rule + agent mode rule (both match *.py)
        assert len(applicable_rules) == 2
        
        rule_contents = [rule.content for rule in applicable_rules]
        assert any("Global Coding Standards" in content for content in rule_contents)
        assert any("Agent Mode Guidelines" in content for content in rule_contents)
    
    def test_rule_formatting_for_llm(self, temp_rules_dir):
        """Test that rules are properly formatted for LLM injection."""
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        context = RuleContext(
            filename="test.ipynb",
            kernel="python3",
            mode="ask"
        )
        
        applicable_rules = rule_manager.get_applicable_rules(context)
        formatted_rules = rule_manager.format_rules_for_llm(applicable_rules)
        
        # Check that formatting includes headers and content
        assert "# Global Rules" in formatted_rules
        assert "# Ask Mode Rules" in formatted_rules
        assert "Global Coding Standards" in formatted_rules
        assert "Ask Mode Guidelines" in formatted_rules
        assert "Use descriptive variable names" in formatted_rules
        assert "Provide detailed explanations" in formatted_rules
    
    def test_context_factory_creates_correct_context(self):
        """Test that RuleContextFactory creates correct context."""
        factory = RuleContextFactory()
        
        context = factory.create(
            filename="notebooks/analysis.ipynb",
            language="python",
            chat_mode_id="ask",
            root_dir="/workspace"
        )
        
        assert context.filename == "notebooks/analysis.ipynb"
        assert context.kernel == "python"
        assert context.mode == "ask"
        assert context.directory == "/workspace/notebooks"
        assert context.basename == "analysis.ipynb"
        assert context.extension == ".ipynb"
    
    def test_rule_injector_end_to_end(self, temp_rules_dir):
        """Test complete rule injection flow."""
        # Setup rule manager
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        # Create mock request with context
        context = RuleContext(
            filename="test.ipynb",
            kernel="python3",
            mode="ask"
        )
        
        request = Mock(spec=ChatRequest)
        request.rule_context = context
        
        # Mock host and config
        mock_host = Mock()
        mock_host.get_rule_manager.return_value = rule_manager
        mock_host.nbi_config.rules_enabled = True
        mock_host.nbi_config.rules_context_injection_enabled = True
        request.host = mock_host
        
        # Test rule injection
        injector = RuleInjector()
        base_prompt = "You are a helpful assistant."
        
        enhanced_prompt = injector.inject_rules(base_prompt, request)
        
        # Verify enhancement
        assert enhanced_prompt != base_prompt
        assert "Additional Guidelines" in enhanced_prompt
        assert "Global Coding Standards" in enhanced_prompt
        assert "Ask Mode Guidelines" in enhanced_prompt
        assert "Use descriptive variable names" in enhanced_prompt
        assert "Provide detailed explanations" in enhanced_prompt
    
    def test_rule_priority_ordering(self, temp_rules_dir):
        """Test that rules are applied in correct priority order."""
        # Create additional rule with different priority
        high_priority_rule = Path(temp_rules_dir) / "00-high-priority.md"
        high_priority_rule.write_text("""---
apply: always
scope:
  file_patterns:
    - '*.ipynb'
active: true
priority: -1
---

# High Priority Rule
- This should come first
""")
        
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        context = RuleContext(
            filename="test.ipynb",
            kernel="python3",
            mode="ask"
        )
        
        applicable_rules = rule_manager.get_applicable_rules(context)
        
        # Should have 3 rules now (high priority + global + ask mode)
        assert len(applicable_rules) == 3
        
        # First rule should be the high priority one (priority -1)
        assert applicable_rules[0].priority == -1
        assert "High Priority Rule" in applicable_rules[0].content
    
    def test_inactive_rules_not_applied(self, temp_rules_dir):
        """Test that inactive rules are not applied."""
        # Create inactive rule
        inactive_rule = Path(temp_rules_dir) / "02-inactive.md"
        inactive_rule.write_text("""---
apply: always
scope:
  file_patterns:
    - '*.ipynb'
active: false
priority: 0
---

# Inactive Rule
- This should not be applied
""")
        
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        context = RuleContext(
            filename="test.ipynb",
            kernel="python3",
            mode="ask"
        )
        
        applicable_rules = rule_manager.get_applicable_rules(context)
        
        # Should not include the inactive rule
        rule_contents = [rule.content for rule in applicable_rules]
        assert not any("Inactive Rule" in content for content in rule_contents)
    
    def test_file_pattern_matching(self, temp_rules_dir):
        """Test that file pattern matching works correctly."""
        rule_manager = RuleManager(temp_rules_dir)
        rule_manager.load_rules()
        
        # Test with .txt file (should not match any rules)
        context = RuleContext(
            filename="document.txt",
            kernel="text",
            mode="ask"
        )
        
        applicable_rules = rule_manager.get_applicable_rules(context)
        
        # Should have no applicable rules since .txt doesn't match patterns
        assert len(applicable_rules) == 0
