import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from notebook_intelligence.rule_manager import RuleManager
from notebook_intelligence.ruleset import Rule, RuleSet, NotebookContext


class TestRuleManager:
    def test_init(self, temp_rules_directory):
        manager = RuleManager(temp_rules_directory)
        
        assert str(manager.rules_directory) == temp_rules_directory
        assert isinstance(manager.ruleset, RuleSet)
        assert manager._loaded is False
    
    def test_discover_rules_empty_directory(self, temp_rules_directory):
        manager = RuleManager(temp_rules_directory)
        
        rules = manager.discover_rules()
        
        assert len(rules) == 0
    
    def test_discover_rules_nonexistent_directory(self, tmp_path):
        nonexistent_dir = tmp_path / "nonexistent"
        manager = RuleManager(str(nonexistent_dir))
        
        rules = manager.discover_rules()
        
        assert len(rules) == 0
    
    def test_discover_rules_with_populated_directory(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        
        rules = manager.discover_rules()
        
        # Should find 2 global rules + 2 mode-specific rules
        assert len(rules) == 4
        
        # Check that rules are sorted by filename
        filenames = [rule.filename for rule in rules]
        assert filenames == sorted(filenames)
    
    def test_load_global_rules(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        rules_path = Path(populated_rules_directory)
        
        global_rules = manager._load_global_rules(rules_path)
        
        assert len(global_rules) == 2
        assert all(rule.mode is None for rule in global_rules)
        
        # Check specific rules
        python_rule = next((r for r in global_rules if "python" in r.filename), None)
        assert python_rule is not None
        assert "Python Best Practices" in python_rule.content
    
    def test_load_mode_rules(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        rules_path = Path(populated_rules_directory)
        
        mode_rules = manager._load_mode_rules(rules_path)
        
        assert len(mode_rules) == 2
        
        # Check mode assignments
        ask_rules = [r for r in mode_rules if r.mode == "ask"]
        agent_rules = [r for r in mode_rules if r.mode == "agent"]
        
        assert len(ask_rules) == 1
        assert len(agent_rules) == 1
        
        assert "Ask Mode Guidelines" in ask_rules[0].content
        assert "Agent Mode Standards" in agent_rules[0].content
    
    def test_load_mode_rules_no_modes_directory(self, temp_rules_directory):
        manager = RuleManager(temp_rules_directory)
        rules_path = Path(temp_rules_directory)
        
        mode_rules = manager._load_mode_rules(rules_path)
        
        assert len(mode_rules) == 0
    
    def test_load_mode_rules_invalid_mode_directory(self, temp_rules_directory):
        # Create an invalid mode directory
        rules_path = Path(temp_rules_directory)
        invalid_mode_dir = rules_path / "modes" / "invalid_mode"
        invalid_mode_dir.mkdir(parents=True)
        
        # Add a rule file in the invalid mode
        rule_content = """---
apply: always
active: true
---
# Invalid mode rule"""
        
        with open(invalid_mode_dir / "invalid.md", 'w') as f:
            f.write(rule_content)
        
        manager = RuleManager(temp_rules_directory)
        
        with patch('notebook_intelligence.rule_manager.log') as mock_log:
            mode_rules = manager._load_mode_rules(rules_path)
            
            # Should skip invalid mode and log warning
            assert len(mode_rules) == 0
            mock_log.warning.assert_called()
    
    def test_load_rules(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        
        ruleset = manager.load_rules()
        
        assert manager._loaded is True
        assert isinstance(ruleset, RuleSet)
        assert len(ruleset.global_rules) == 2
        assert "ask" in ruleset.mode_rules
        assert "agent" in ruleset.mode_rules
        assert len(ruleset.mode_rules["ask"]) == 1
        assert len(ruleset.mode_rules["agent"]) == 1
    
    def test_load_rules_force_reload(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        
        # Load rules first time
        first_ruleset = manager.load_rules()
        assert manager._loaded is True
        
        # Load again without force_reload (should return cached)
        second_ruleset = manager.load_rules()
        assert second_ruleset is first_ruleset
        
        # Force reload should create new ruleset
        third_ruleset = manager.load_rules(force_reload=True)
        assert third_ruleset is not first_ruleset
        assert len(third_ruleset.get_all_rules()) == len(first_ruleset.get_all_rules())
    
    def test_get_applicable_rules(self, populated_rules_directory, sample_notebook_context):
        manager = RuleManager(populated_rules_directory)
        
        applicable_rules = manager.get_applicable_rules(sample_notebook_context)
        
        # Should get global rules + ask mode rules that match *.ipynb
        assert len(applicable_rules) > 0
        
        # Check that we get both global and mode-specific rules
        rule_modes = {rule.mode for rule in applicable_rules}
        assert None in rule_modes  # Global rules
        assert "ask" in rule_modes  # Ask mode rules
    
    def test_get_applicable_rules_auto_loads(self, populated_rules_directory, sample_notebook_context):
        manager = RuleManager(populated_rules_directory)
        assert manager._loaded is False
        
        # Getting applicable rules should auto-load
        applicable_rules = manager.get_applicable_rules(sample_notebook_context)
        
        assert manager._loaded is True
        assert isinstance(applicable_rules, list)
    
    def test_validate_rule_file_valid(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        rule_file = Path(populated_rules_directory) / "01-python.md"
        
        result = manager.validate_rule_file(str(rule_file))
        
        assert result['valid'] is True
        assert len(result['errors']) == 0
        assert result['rule'] is not None
        assert isinstance(result['rule'], Rule)
    
    def test_validate_rule_file_nonexistent(self, temp_rules_directory):
        manager = RuleManager(temp_rules_directory)
        
        result = manager.validate_rule_file("nonexistent.md")
        
        assert result['valid'] is False
        assert len(result['errors']) == 1
        assert "File not found" in result['errors'][0]
    
    def test_validate_rule_file_invalid_yaml(self, invalid_rules_directory):
        manager = RuleManager(invalid_rules_directory)
        rule_file = Path(invalid_rules_directory) / "invalid-yaml.md"
        
        result = manager.validate_rule_file(str(rule_file))
        
        assert result['valid'] is False
        assert len(result['errors']) == 1
        assert "Invalid rule format" in result['errors'][0]
    
    def test_validate_rule_file_no_content_warning(self, tmp_path):
        # Create rule with empty content
        rule_content = """---
apply: always
active: true
---
"""
        
        rule_file = tmp_path / "empty_content.md"
        rule_file.write_text(rule_content)
        
        manager = RuleManager(str(tmp_path))
        result = manager.validate_rule_file(str(rule_file))
        
        assert result['valid'] is True
        assert len(result['warnings']) >= 1  # May have multiple warnings
        assert any("no content" in warning for warning in result['warnings'])
    
    def test_get_rule_by_filename(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        
        rule = manager.get_rule_by_filename("01-python.md")
        
        assert rule is not None
        assert rule.filename == "01-python.md"
        assert "Python Best Practices" in rule.content
    
    def test_get_rule_by_filename_not_found(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        
        rule = manager.get_rule_by_filename("nonexistent.md")
        
        assert rule is None
    
    def test_toggle_rule(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        manager.load_rules()
        
        # Find a rule to toggle
        rule = manager.get_rule_by_filename("01-python.md")
        original_state = rule.active
        
        # Toggle the rule
        result = manager.toggle_rule("01-python.md", not original_state)
        
        assert result is True
        assert rule.active == (not original_state)
    
    def test_toggle_rule_not_found(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        
        result = manager.toggle_rule("nonexistent.md", False)
        
        assert result is False
    
    def test_get_rules_summary(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        
        summary = manager.get_rules_summary()
        
        assert summary['total_rules'] == 4
        assert summary['active_rules'] == 4  # All rules are active by default
        assert summary['global_rules'] == 2
        assert 'ask' in summary['mode_rules']
        assert 'agent' in summary['mode_rules']
        assert summary['rules_directory'] == populated_rules_directory
    
    def test_format_rules_for_llm_empty_list(self, temp_rules_directory):
        manager = RuleManager(temp_rules_directory)
        
        formatted = manager.format_rules_for_llm([])
        
        assert formatted == ""
    
    def test_format_rules_for_llm_with_rules(self, populated_rules_directory):
        manager = RuleManager(populated_rules_directory)
        manager.load_rules()
        
        # Get some rules to format - use .ipynb file to match ask mode rules
        context = NotebookContext(filename="test.ipynb", mode="ask")
        applicable_rules = manager.get_applicable_rules(context)
        
        formatted = manager.format_rules_for_llm(applicable_rules)
        
        assert "# Global Rules" in formatted
        # Check that we have content from the rules
        assert "Python Best Practices" in formatted
        # May or may not have ask mode rules depending on file patterns
        assert len(formatted) > 0
    


class TestRuleManagerErrorHandling:
    def test_load_rules_with_invalid_files(self, invalid_rules_directory):
        manager = RuleManager(invalid_rules_directory)
        
        with patch('notebook_intelligence.rule_manager.log') as mock_log:
            rules = manager.discover_rules()
            
            # Should handle invalid files gracefully
            mock_log.error.assert_called()
            # Should still return rules that could be loaded
            assert isinstance(rules, list)
    
    def test_load_rules_with_permission_error(self, tmp_path):
        # Create a rules directory we can't read
        rules_dir = tmp_path / "no_permission"
        rules_dir.mkdir()
        
        # Create a rule file
        rule_file = rules_dir / "test.md"
        rule_file.write_text("# Test rule")
        
        manager = RuleManager(str(rules_dir))
        
        # Mock a permission error
        with patch('pathlib.Path.glob', side_effect=PermissionError("Permission denied")):
            with patch('notebook_intelligence.rule_manager.log') as mock_log:
                rules = manager._load_global_rules(rules_dir)
                
                # Should handle permission errors gracefully
                assert len(rules) == 0