import pytest
import tempfile
from pathlib import Path

from notebook_intelligence.ruleset import RuleScope, Rule, RuleSet, NotebookContext


class TestRuleScope:
    def test_matches_file_with_patterns(self):
        scope = RuleScope(file_patterns=["*.py", "*.ipynb"])
        
        assert scope.matches_file("test.py") is True
        assert scope.matches_file("notebook.ipynb") is True
        assert scope.matches_file("data.csv") is False
        assert scope.matches_file("README.md") is False
    
    def test_matches_file_no_patterns_matches_all(self):
        scope = RuleScope()  # No file patterns
        
        assert scope.matches_file("test.py") is True
        assert scope.matches_file("notebook.ipynb") is True
        assert scope.matches_file("data.csv") is True
    
    def test_matches_kernel(self):
        scope = RuleScope(kernels=["python3", "python"])
        
        assert scope.matches_kernel("python3") is True
        assert scope.matches_kernel("python") is True
        assert scope.matches_kernel("r") is False
        assert scope.matches_kernel("julia") is False
    
    def test_matches_kernel_no_kernels_matches_all(self):
        scope = RuleScope()  # No kernels specified
        
        assert scope.matches_kernel("python3") is True
        assert scope.matches_kernel("r") is True
        assert scope.matches_kernel("julia") is True


class TestRule:
    def test_from_file_with_valid_frontmatter(self, tmp_path):
        rule_content = """---
apply: always
scope:
  file_patterns:
    - "*.py"
    - "*.ipynb"
  kernels:
    - python3
  cell_types:
    - code
active: true
priority: 5
---
# Test Rule
This is a test rule for validation."""
        
        rule_file = tmp_path / "test_rule.md"
        rule_file.write_text(rule_content)
        
        rule = Rule.from_file(str(rule_file))
        
        assert rule.filename == "test_rule.md"
        assert rule.apply == "always"
        assert rule.scope.file_patterns == ["*.py", "*.ipynb"]
        assert rule.scope.kernels == ["python3"]
        assert rule.scope.cell_types == ["code"]
        assert rule.active is True
        assert rule.priority == 5
        assert "This is a test rule for validation." in rule.content
    
    def test_from_file_with_mode(self, tmp_path):
        rule_content = """---
apply: auto
active: true
---
# Mode-specific rule"""
        
        rule_file = tmp_path / "mode_rule.md"
        rule_file.write_text(rule_content)
        
        rule = Rule.from_file(str(rule_file), mode="ask")
        
        assert rule.mode == "ask"
        assert rule.apply == "auto"
    
    def test_from_file_no_frontmatter(self, tmp_path):
        rule_content = "# Simple rule without frontmatter\nThis is just content."
        
        rule_file = tmp_path / "simple_rule.md"
        rule_file.write_text(rule_content)
        
        rule = Rule.from_file(str(rule_file))
        
        # Should use defaults
        assert rule.apply == "always"
        assert rule.active is True
        assert rule.priority == 0
        assert rule.scope.file_patterns == []
        assert rule.content == rule_content
    
    def test_from_file_invalid_yaml_raises_error(self, tmp_path):
        # Use YAML that will actually fail parsing
        rule_content = """---
apply: always
scope:
  file_patterns:
    - "*.py"
  invalid_key: [unclosed_list
active: true
---
# Invalid YAML Rule"""
        
        rule_file = tmp_path / "invalid_rule.md"
        rule_file.write_text(rule_content)
        
        with pytest.raises(ValueError, match="Invalid YAML frontmatter"):
            Rule.from_file(str(rule_file))
    
    def test_from_file_nonexistent_file_raises_error(self):
        with pytest.raises(FileNotFoundError):
            Rule.from_file("nonexistent_file.md")
    
    def test_matches_context_active_rule(self):
        scope = RuleScope(file_patterns=["*.py"], kernels=["python3"])
        rule = Rule(
            filename="test.md",
            apply="always",
            scope=scope,
            active=True,
            content="Test content"
        )
        
        assert rule.matches_context("test.py", "python3") is True
        assert rule.matches_context("test.ipynb", "python3") is False
        assert rule.matches_context("test.py", "r") is False
    
    def test_matches_context_inactive_rule(self):
        scope = RuleScope(file_patterns=["*.py"])
        rule = Rule(
            filename="test.md",
            apply="always",
            scope=scope,
            active=False,  # Inactive rule
            content="Test content"
        )
        
        assert rule.matches_context("test.py") is False
    
    def test_matches_context_with_mode(self):
        scope = RuleScope(file_patterns=["*.py"])
        rule = Rule(
            filename="test.md",
            apply="always",
            scope=scope,
            active=True,
            content="Test content",
            mode="ask"
        )
        
        assert rule.matches_context("test.py", mode="ask") is True
        assert rule.matches_context("test.py", mode="agent") is False
        assert rule.matches_context("test.py") is True  # No mode specified
    
    def test_to_dict(self):
        scope = RuleScope(file_patterns=["*.py"], kernels=["python3"])
        rule = Rule(
            filename="test.md",
            apply="always",
            scope=scope,
            active=True,
            content="Test content",
            mode="ask",
            priority=1
        )
        
        result = rule.to_dict()
        
        expected = {
            'filename': 'test.md',
            'apply': 'always',
            'scope': {
                'file_patterns': ['*.py'],
                'kernels': ['python3'],
                'cell_types': None
            },
            'active': True,
            'content': 'Test content',
            'mode': 'ask',
            'priority': 1
        }
        
        assert result == expected
    
    def test_from_dict(self):
        data = {
            'filename': 'test.md',
            'apply': 'auto',
            'scope': {
                'file_patterns': ['*.ipynb'],
                'kernels': ['python3'],
                'cell_types': ['code']
            },
            'active': False,
            'content': 'Test content',
            'mode': 'agent',
            'priority': 2
        }
        
        rule = Rule.from_dict(data)
        
        assert rule.filename == 'test.md'
        assert rule.apply == 'auto'
        assert rule.scope.file_patterns == ['*.ipynb']
        assert rule.scope.kernels == ['python3']
        assert rule.scope.cell_types == ['code']
        assert rule.active is False
        assert rule.content == 'Test content'
        assert rule.mode == 'agent'
        assert rule.priority == 2


class TestRuleSet:
    def test_add_rule_global(self):
        ruleset = RuleSet()
        rule = Rule(
            filename="test.md",
            apply="always",
            scope=RuleScope(),
            active=True,
            content="Test content"
        )
        
        ruleset.add_rule(rule)
        
        assert len(ruleset.global_rules) == 1
        assert ruleset.global_rules[0] == rule
        assert len(ruleset.mode_rules) == 0
    
    def test_add_rule_mode_specific(self):
        ruleset = RuleSet()
        rule = Rule(
            filename="test.md",
            apply="always",
            scope=RuleScope(),
            active=True,
            content="Test content",
            mode="ask"
        )
        
        ruleset.add_rule(rule)
        
        assert len(ruleset.global_rules) == 0
        assert "ask" in ruleset.mode_rules
        assert len(ruleset.mode_rules["ask"]) == 1
        assert ruleset.mode_rules["ask"][0] == rule
    
    def test_get_applicable_rules_global_only(self):
        ruleset = RuleSet()
        
        # Add global rule that matches
        matching_rule = Rule(
            filename="matching.md",
            apply="always",
            scope=RuleScope(file_patterns=["*.py"]),
            active=True,
            content="Matching rule"
        )
        
        # Add global rule that doesn't match
        non_matching_rule = Rule(
            filename="non_matching.md",
            apply="always",
            scope=RuleScope(file_patterns=["*.txt"]),
            active=True,
            content="Non-matching rule"
        )
        
        ruleset.add_rule(matching_rule)
        ruleset.add_rule(non_matching_rule)
        
        applicable = ruleset.get_applicable_rules("test.py")
        
        assert len(applicable) == 1
        assert applicable[0] == matching_rule
    
    def test_get_applicable_rules_with_mode(self):
        ruleset = RuleSet()
        
        # Add global rule
        global_rule = Rule(
            filename="global.md",
            apply="always",
            scope=RuleScope(file_patterns=["*.py"]),
            active=True,
            content="Global rule"
        )
        
        # Add mode-specific rule
        ask_rule = Rule(
            filename="ask.md",
            apply="always",
            scope=RuleScope(file_patterns=["*.py"]),
            active=True,
            content="Ask rule",
            mode="ask"
        )
        
        # Add different mode rule (shouldn't match)
        agent_rule = Rule(
            filename="agent.md",
            apply="always",
            scope=RuleScope(file_patterns=["*.py"]),
            active=True,
            content="Agent rule",
            mode="agent"
        )
        
        ruleset.add_rule(global_rule)
        ruleset.add_rule(ask_rule)
        ruleset.add_rule(agent_rule)
        
        applicable = ruleset.get_applicable_rules("test.py", mode="ask")
        
        assert len(applicable) == 2
        assert global_rule in applicable
        assert ask_rule in applicable
        assert agent_rule not in applicable
    
    def test_get_applicable_rules_priority_sorting(self):
        ruleset = RuleSet()
        
        # Add rules with different priorities
        rule_low_priority = Rule(
            filename="low.md",
            apply="always",
            scope=RuleScope(file_patterns=["*.py"]),
            active=True,
            content="Low priority",
            priority=10
        )
        
        rule_high_priority = Rule(
            filename="high.md",
            apply="always",
            scope=RuleScope(file_patterns=["*.py"]),
            active=True,
            content="High priority",
            priority=1
        )
        
        ruleset.add_rule(rule_low_priority)
        ruleset.add_rule(rule_high_priority)
        
        applicable = ruleset.get_applicable_rules("test.py")
        
        # Should be sorted by priority (lower number = higher priority)
        assert len(applicable) == 2
        assert applicable[0] == rule_high_priority
        assert applicable[1] == rule_low_priority
    
    def test_toggle_rule(self):
        ruleset = RuleSet()
        rule = Rule(
            filename="test.md",
            apply="always",
            scope=RuleScope(),
            active=True,
            content="Test content"
        )
        
        ruleset.add_rule(rule)
        
        # Toggle rule off
        result = ruleset.toggle_rule("test.md", False)
        assert result is True
        assert rule.active is False
        
        # Toggle rule on
        result = ruleset.toggle_rule("test.md", True)
        assert result is True
        assert rule.active is True
        
        # Try to toggle non-existent rule
        result = ruleset.toggle_rule("nonexistent.md", False)
        assert result is False


class TestNotebookContext:
    def test_basename_property(self):
        context = NotebookContext(filename="/path/to/test.ipynb")
        assert context.basename == "test.ipynb"
    
    def test_extension_property(self):
        context = NotebookContext(filename="test.ipynb")
        assert context.extension == ".ipynb"
        
        context = NotebookContext(filename="script.py")
        assert context.extension == ".py"
        
        context = NotebookContext(filename="data.csv")
        assert context.extension == ".csv"