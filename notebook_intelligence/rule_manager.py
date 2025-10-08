import os
import logging
from typing import List, Optional, Dict, Any
from pathlib import Path

from .ruleset import Rule, RuleSet, RuleContext

log = logging.getLogger(__name__)

class RuleManager:
    """Manages rule discovery, loading, and application."""
    
    def __init__(self, rules_directory: str):
        """Initialize the rule manager with a rules directory."""
        self.rules_directory = Path(rules_directory)
        self.ruleset = RuleSet()
        self._loaded = False
        self._last_modified_time = None
        self._auto_reload_enabled = os.environ.get('NBI_RULES_AUTO_RELOAD', 'false').lower() == 'true'
        
    def discover_rules(self, base_path: Optional[str] = None) -> List[Rule]:
        """Discover all rule files in the rules directory structure."""
        if base_path:
            search_path = Path(base_path)
        else:
            search_path = self.rules_directory
            
        if not search_path.exists():
            log.info(f"Rules directory not found: {search_path}")
            return []
        
        discovered_rules = []
        
        # Load global rules from the base rules directory
        global_rules = self._load_global_rules(search_path)
        discovered_rules.extend(global_rules)
        
        # Load mode-specific rules from modes/ subdirectory
        mode_rules = self._load_mode_rules(search_path)
        discovered_rules.extend(mode_rules)
        
        # Sort by filename for consistent ordering
        discovered_rules.sort(key=lambda r: r.filename)
        
        log.info(f"Discovered {len(discovered_rules)} rules from {search_path}")
        return discovered_rules
    
    def _load_global_rules(self, rules_dir: Path) -> List[Rule]:
        """Load global rules from the base rules directory."""
        global_rules = []
        
        if not rules_dir.is_dir():
            return global_rules
        
        try:
            # Find all .md files in the base directory (not in subdirectories)
            for rule_file in rules_dir.glob("*.md"):
                try:
                    rule = Rule.from_file(str(rule_file), mode=None)
                    global_rules.append(rule)
                    log.info(f"Loaded global rule: {rule.filename}")
                except Exception as e:
                    log.error(f"Failed to load global rule from {rule_file}: {e}")
        except Exception as e:
            log.error(f"Failed to access rules directory {rules_dir}: {e}")
        
        return global_rules
    
    def _load_mode_rules(self, rules_dir: Path) -> List[Rule]:
        """Load mode-specific rules from modes/ subdirectory."""
        mode_rules = []
        modes_dir = rules_dir / "modes"
        
        if not modes_dir.is_dir():
            log.debug(f"No modes directory found at {modes_dir}")
            return mode_rules
        
        # Valid workflow modes
        valid_modes = ['ask', 'agent', 'inline-chat']
        
        for mode_dir in modes_dir.iterdir():
            if not mode_dir.is_dir():
                continue
                
            mode_name = mode_dir.name
            if mode_name not in valid_modes:
                log.warning(f"Unknown mode directory: {mode_name}, skipping")
                continue
            
            # Load all .md files in the mode directory
            for rule_file in mode_dir.glob("*.md"):
                try:
                    rule = Rule.from_file(str(rule_file), mode=mode_name)
                    mode_rules.append(rule)
                    log.info(f"Loaded {mode_name} mode rule: {rule.filename}")
                except Exception as e:
                    log.error(f"Failed to load {mode_name} rule from {rule_file}: {e}")
        
        return mode_rules
    
    def _get_rules_directory_mtime(self) -> float:
        """Get the latest modification time of any file in the rules directory."""
        if not self.rules_directory.exists():
            return 0
        
        max_mtime = 0
        for rule_file in self.rules_directory.rglob("*.md"):
            try:
                mtime = rule_file.stat().st_mtime
                max_mtime = max(max_mtime, mtime)
            except OSError:
                pass
        return max_mtime
    
    def _should_reload(self) -> bool:
        """Check if rules should be reloaded based on file modifications."""
        if not self._auto_reload_enabled:
            return False
        
        if not self._loaded:
            return True
        
        current_mtime = self._get_rules_directory_mtime()
        if self._last_modified_time is None or current_mtime > self._last_modified_time:
            log.info(f"Rules directory modified (mtime: {current_mtime} > {self._last_modified_time}), triggering auto-reload")
            return True
        
        log.debug(f"No rule file changes detected (mtime: {current_mtime})")
        return False
    
    def load_rules(self, force_reload: bool = False) -> RuleSet:
        """Load all rules into the ruleset."""
        if self._loaded and not force_reload:
            return self.ruleset
        
        reload_reason = "auto-reload" if self._auto_reload_enabled and self._loaded else "initial load"
        if force_reload:
            reload_reason = "forced reload"
        log.info(f"Loading rules from {self.rules_directory} ({reload_reason})")
        
        # Clear existing rules
        self.ruleset = RuleSet()
        
        # Discover and load all rules
        discovered_rules = self.discover_rules()
        
        # Add rules to the ruleset
        for rule in discovered_rules:
            self.ruleset.add_rule(rule)
        
        self._loaded = True
        self._last_modified_time = self._get_rules_directory_mtime()
        log.info(f"✓ Successfully loaded {len(self.ruleset.get_all_rules())} rules total")
        return self.ruleset
    
    def get_applicable_rules(self, context: RuleContext) -> List[Rule]:
        """Get all rules that apply to the given context."""
        # Auto-reload if enabled and files have changed
        if self._should_reload():
            log.info("Auto-reload triggered - reloading rules now")
            self.load_rules(force_reload=True)
        elif not self._loaded:
            self.load_rules()
        
        applicable_rules = self.ruleset.get_applicable_rules(
            filename=context.basename,
            kernel=context.kernel,
            mode=context.mode,
            directory=context.directory
        )
        
        log.debug(f"Found {len(applicable_rules)} applicable rules for context: "
                 f"file={context.basename}, kernel={context.kernel}, mode={context.mode}, dir={context.directory}")
        
        return applicable_rules
    
    def validate_rule_file(self, filepath: str) -> Dict[str, Any]:
        """Validate a rule file and return validation results."""
        validation_result = {
            'valid': False,
            'errors': [],
            'warnings': [],
            'rule': None
        }
        
        try:
            rule = Rule.from_file(filepath)
            validation_result['valid'] = True
            validation_result['rule'] = rule
            
            # Additional validation checks
            if not rule.content.strip():
                validation_result['warnings'].append("Rule has no content")
            
            if rule.apply not in ['always', 'auto', 'manual']:
                validation_result['warnings'].append(f"Unknown apply mode: {rule.apply}")
            
            if not rule.scope.file_patterns and not rule.scope.kernels:
                validation_result['warnings'].append("Rule has no scope restrictions, will apply to all contexts")
                
        except FileNotFoundError:
            validation_result['errors'].append(f"File not found: {filepath}")
        except ValueError as e:
            validation_result['errors'].append(f"Invalid rule format: {e}")
        except Exception as e:
            validation_result['errors'].append(f"Unexpected error: {e}")
        
        return validation_result
    
    def get_rule_by_filename(self, filename: str) -> Optional[Rule]:
        """Get a rule by its filename."""
        if not self._loaded:
            self.load_rules()
        
        for rule in self.ruleset.get_all_rules():
            if rule.filename == filename:
                return rule
        return None
    
    def toggle_rule(self, filename: str, active: bool) -> bool:
        """Toggle a rule's active state."""
        if not self._loaded:
            self.load_rules()
        
        return self.ruleset.toggle_rule(filename, active)
    
    def get_rules_summary(self) -> Dict[str, Any]:
        """Get a summary of loaded rules."""
        if not self._loaded:
            self.load_rules()
        
        all_rules = self.ruleset.get_all_rules()
        active_rules = [r for r in all_rules if r.active]
        
        return {
            'total_rules': len(all_rules),
            'active_rules': len(active_rules),
            'global_rules': len(self.ruleset.global_rules),
            'mode_rules': {mode: len(rules) for mode, rules in self.ruleset.mode_rules.items()},
            'rules_directory': str(self.rules_directory)
        }
    
    def format_rules_for_llm(self, rules: List[Rule]) -> str:
        """Format rules for injection into LLM context."""
        if not rules:
            return ""
        
        formatted_sections = []
        
        # Group rules by type
        global_rules = [r for r in rules if r.mode is None]
        mode_rules = {}
        for rule in rules:
            if rule.mode:
                if rule.mode not in mode_rules:
                    mode_rules[rule.mode] = []
                mode_rules[rule.mode].append(rule)
        
        # Format global rules
        if global_rules:
            formatted_sections.append("# Global Rules\n")
            for rule in global_rules:
                formatted_sections.append(f"## {rule.filename}\n{rule.content}\n")
        
        # Format mode-specific rules
        for mode, rules_list in mode_rules.items():
            formatted_sections.append(f"# {mode.title()} Mode Rules\n")
            for rule in rules_list:
                formatted_sections.append(f"## {rule.filename}\n{rule.content}\n")
        
        return "\n".join(formatted_sections)
    