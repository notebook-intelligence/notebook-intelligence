import pytest
import os
import time
from pathlib import Path
from unittest.mock import patch
from notebook_intelligence.rule_manager import RuleManager
from notebook_intelligence.ruleset import NotebookContext


class TestRuleAutoReload:
    """Tests for automatic rule reloading functionality."""
    
    def test_auto_reload_disabled_by_default(self, tmp_path):
        """Test that auto-reload is disabled by default."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        
        manager = RuleManager(str(rules_dir))
        assert manager._auto_reload_enabled is False
    
    def test_auto_reload_enabled_via_env_var(self, tmp_path):
        """Test that auto-reload can be enabled via environment variable."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            assert manager._auto_reload_enabled is True
    
    def test_auto_reload_env_var_case_insensitive(self, tmp_path):
        """Test that env var is case insensitive."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        
        for value in ['True', 'TRUE', 'TrUe']:
            with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': value}):
                manager = RuleManager(str(rules_dir))
                assert manager._auto_reload_enabled is True
    
    def test_get_rules_directory_mtime_empty_dir(self, tmp_path):
        """Test mtime calculation for empty directory."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        
        manager = RuleManager(str(rules_dir))
        mtime = manager._get_rules_directory_mtime()
        assert mtime == 0
    
    def test_get_rules_directory_mtime_with_files(self, tmp_path):
        """Test mtime calculation with rule files."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        
        # Create a rule file
        rule_file = rules_dir / "test-rule.md"
        rule_file.write_text("---\npriority: 10\n---\nTest rule")
        
        manager = RuleManager(str(rules_dir))
        mtime = manager._get_rules_directory_mtime()
        assert mtime > 0
        assert mtime == rule_file.stat().st_mtime
    
    def test_get_rules_directory_mtime_returns_latest(self, tmp_path):
        """Test that mtime returns the latest file modification time."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        
        # Create first file
        rule1 = rules_dir / "rule1.md"
        rule1.write_text("---\npriority: 10\n---\nRule 1")
        time.sleep(0.01)  # Small delay to ensure different mtimes
        
        # Create second file (should have later mtime)
        rule2 = rules_dir / "rule2.md"
        rule2.write_text("---\npriority: 20\n---\nRule 2")
        
        manager = RuleManager(str(rules_dir))
        mtime = manager._get_rules_directory_mtime()
        assert mtime == rule2.stat().st_mtime
    
    def test_should_reload_when_not_loaded(self, tmp_path):
        """Test that reload is triggered when rules haven't been loaded."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            assert manager._should_reload() is True
    
    def test_should_reload_disabled_when_flag_off(self, tmp_path):
        """Test that reload doesn't trigger when auto-reload is disabled."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        rule_file = rules_dir / "test-rule.md"
        rule_file.write_text("---\npriority: 10\n---\nTest rule")
        
        # Auto-reload disabled (default)
        manager = RuleManager(str(rules_dir))
        manager.load_rules()
        
        # Modify the file
        time.sleep(0.01)
        rule_file.write_text("---\npriority: 20\n---\nModified rule")
        
        # Should not trigger reload
        assert manager._should_reload() is False
    
    def test_should_reload_after_file_modification(self, tmp_path):
        """Test that reload triggers when a rule file is modified."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        rule_file = rules_dir / "test-rule.md"
        rule_file.write_text("---\npriority: 10\n---\nTest rule")
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            manager.load_rules()
            
            # Initially should not reload
            assert manager._should_reload() is False
            
            # Modify the file
            time.sleep(0.01)  # Ensure different mtime
            rule_file.write_text("---\npriority: 20\n---\nModified rule")
            
            # Should trigger reload
            assert manager._should_reload() is True
    
    def test_should_reload_after_new_file_added(self, tmp_path):
        """Test that reload triggers when a new rule file is added."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        rule1 = rules_dir / "rule1.md"
        rule1.write_text("---\npriority: 10\n---\nRule 1")
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            manager.load_rules()
            
            # Add a new file
            time.sleep(0.01)
            rule2 = rules_dir / "rule2.md"
            rule2.write_text("---\npriority: 20\n---\nRule 2")
            
            # Should trigger reload
            assert manager._should_reload() is True
    
    def test_get_applicable_rules_auto_reloads(self, tmp_path):
        """Test that get_applicable_rules triggers auto-reload when files change."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        rule_file = rules_dir / "test-rule.md"
        rule_file.write_text("---\npriority: 10\n---\nOriginal content")
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            
            # First call - loads rules
            context = NotebookContext(filename="test.ipynb", kernel="python", mode="ask")
            rules1 = manager.get_applicable_rules(context)
            assert len(rules1) == 1
            assert rules1[0].content == "Original content"
            
            # Modify the rule
            time.sleep(0.01)
            rule_file.write_text("---\npriority: 10\n---\nModified content")
            
            # Second call - should auto-reload
            rules2 = manager.get_applicable_rules(context)
            assert len(rules2) == 1
            assert rules2[0].content == "Modified content"
    
    def test_auto_reload_updates_last_modified_time(self, tmp_path):
        """Test that auto-reload updates the last_modified_time."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        rule_file = rules_dir / "test-rule.md"
        rule_file.write_text("---\npriority: 10\n---\nTest rule")
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            context = NotebookContext(filename="test.ipynb", kernel="python", mode="ask")
            
            # Initial load
            manager.get_applicable_rules(context)
            first_mtime = manager._last_modified_time
            
            # Modify file
            time.sleep(0.01)
            rule_file.write_text("---\npriority: 10\n---\nModified")
            
            # Trigger reload
            manager.get_applicable_rules(context)
            second_mtime = manager._last_modified_time
            
            assert second_mtime > first_mtime
    
    def test_auto_reload_with_nested_directories(self, tmp_path):
        """Test auto-reload works with mode-specific rules in subdirectories."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        modes_dir = rules_dir / "modes" / "ask"
        modes_dir.mkdir(parents=True)
        
        rule_file = modes_dir / "ask-rule.md"
        rule_file.write_text("---\npriority: 10\n---\nAsk mode rule")
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            context = NotebookContext(filename="test.ipynb", kernel="python", mode="ask")
            
            # Initial load
            rules1 = manager.get_applicable_rules(context)
            assert len(rules1) == 1
            
            # Modify the nested file
            time.sleep(0.01)
            rule_file.write_text("---\npriority: 10\n---\nModified ask mode rule")
            
            # Should trigger reload
            rules2 = manager.get_applicable_rules(context)
            assert rules2[0].content == "Modified ask mode rule"
    
    def test_auto_reload_doesnt_trigger_without_changes(self, tmp_path):
        """Test that multiple calls don't reload when files haven't changed."""
        rules_dir = tmp_path / "rules"
        rules_dir.mkdir()
        rule_file = rules_dir / "test-rule.md"
        rule_file.write_text("---\npriority: 10\n---\nTest rule")
        
        with patch.dict(os.environ, {'NBI_RULES_AUTO_RELOAD': 'true'}):
            manager = RuleManager(str(rules_dir))
            context = NotebookContext(filename="test.ipynb", kernel="python", mode="ask")
            
            # First call
            manager.get_applicable_rules(context)
            load_count = len(manager.ruleset.get_all_rules())
            
            # Multiple calls without file changes
            for _ in range(5):
                manager.get_applicable_rules(context)
            
            # Should still have the same rules (not reloaded)
            assert len(manager.ruleset.get_all_rules()) == load_count

