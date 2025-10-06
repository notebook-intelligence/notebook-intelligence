from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
import os
import yaml
import re
import fnmatch
import logging

log = logging.getLogger(__name__)

@dataclass
class RuleScope:
    """Defines the scope where a rule applies."""
    file_patterns: List[str] = field(default_factory=list)
    kernels: List[str] = field(default_factory=list)
    directory_patterns: List[str] = field(default_factory=list)
    cell_types: Optional[List[str]] = None
    
    def matches_file(self, filename: str) -> bool:
        """Check if the filename matches any of the file patterns."""
        if not self.file_patterns:
            return True  # No patterns means matches all files
        
        for pattern in self.file_patterns:
            if fnmatch.fnmatch(filename, pattern):
                return True
        return False
    
    def matches_directory(self, directory: str) -> bool:
        """Check if the directory matches any of the directory patterns.
        
        Patterns support wildcards (*,?, [seq], [!seq]) via fnmatch.
        Examples:
            - "*/private-notebooks/*" matches any path with /private-notebooks/
            - "*workspace*" matches any path containing "workspace"
        """
        if not self.directory_patterns:
            return True  # No patterns means matches all directories
        
        for pattern in self.directory_patterns:
            if fnmatch.fnmatch(directory, pattern):
                return True
        return False
    
    def matches_kernel(self, kernel_name: str) -> bool:
        """Check if the kernel matches any of the specified kernels."""
        if not self.kernels:
            return True  # No kernels specified means matches all kernels
        
        return kernel_name in self.kernels

@dataclass
class Rule:
    """Represents a single ruleset rule."""
    filename: str
    apply: str  # 'always', 'auto', 'manual'
    scope: RuleScope
    active: bool
    content: str
    mode: Optional[str] = None  # None for global rules, 'ask'/'agent'/'inline-chat' for mode-specific
    priority: int = 0  # For ordering rules (lower number = higher priority)
    
    @classmethod
    def from_file(cls, filepath: str, mode: Optional[str] = None) -> 'Rule':
        """Load a rule from a markdown file with YAML frontmatter."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Rule file not found: {filepath}")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Parse YAML frontmatter
        if content.startswith('---\n'):
            parts = content.split('---\n', 2)
            if len(parts) >= 3:
                try:
                    frontmatter = yaml.safe_load(parts[1])
                    rule_content = parts[2].strip()
                except yaml.YAMLError as e:
                    log.error(f"Invalid YAML in rule file {filepath}: {e}")
                    raise ValueError(f"Invalid YAML frontmatter in {filepath}: {e}")
            else:
                log.warning(f"Malformed frontmatter in rule file {filepath}")
                frontmatter = {}
                rule_content = content
        else:
            # No frontmatter, use defaults
            frontmatter = {}
            rule_content = content
        
        # Extract rule configuration with defaults
        apply_mode = frontmatter.get('apply', 'always')
        scope_data = frontmatter.get('scope', {})
        active = frontmatter.get('active', True)
        priority = frontmatter.get('priority', 0)
        
        # Validate apply mode
        if apply_mode not in ['always', 'auto', 'manual']:
            log.warning(f"Invalid apply mode '{apply_mode}' in {filepath}, defaulting to 'always'")
            apply_mode = 'always'
        
        # Create scope object
        scope = RuleScope(
            file_patterns=scope_data.get('file_patterns', []),
            kernels=scope_data.get('kernels', []),
            directory_patterns=scope_data.get('directory_patterns', []),
            cell_types=scope_data.get('cell_types')
        )
        
        return cls(
            filename=os.path.basename(filepath),
            apply=apply_mode,
            scope=scope,
            active=active,
            content=rule_content,
            mode=mode,
            priority=priority
        )
    
    def matches_context(self, filename: str, kernel: str = None, cell_type: str = None, mode: str = None, directory: str = None) -> bool:
        """Check if this rule applies to the given context."""
        if not self.active:
            return False
        
        # Check mode compatibility
        if self.mode and mode and self.mode != mode:
            return False
        
        # Check scope matching
        if not self.scope.matches_file(filename):
            return False
        
        if kernel and not self.scope.matches_kernel(kernel):
            return False
        
        if directory and not self.scope.matches_directory(directory):
            return False

        return True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert rule to dictionary for serialization."""
        return {
            'filename': self.filename,
            'apply': self.apply,
            'scope': {
                'file_patterns': self.scope.file_patterns,
                'kernels': self.scope.kernels,
                'directory_patterns': self.scope.directory_patterns,
                'cell_types': self.scope.cell_types
            },
            'active': self.active,
            'content': self.content,
            'mode': self.mode,
            'priority': self.priority
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Rule':
        """Create rule from dictionary."""
        scope_data = data.get('scope', {})
        scope = RuleScope(
            file_patterns=scope_data.get('file_patterns', []),
            kernels=scope_data.get('kernels', []),
            directory_patterns=scope_data.get('directory_patterns', []),
            cell_types=scope_data.get('cell_types')
        )
        
        return cls(
            filename=data['filename'],
            apply=data.get('apply', 'always'),
            scope=scope,
            active=data.get('active', True),
            content=data['content'],
            mode=data.get('mode'),
            priority=data.get('priority', 0)
        )

@dataclass
class RuleSet:
    """Collection of rules organized by type and mode."""
    global_rules: List[Rule] = field(default_factory=list)
    mode_rules: Dict[str, List[Rule]] = field(default_factory=dict)
    
    def add_rule(self, rule: Rule) -> None:
        """Add a rule to the appropriate collection."""
        if rule.mode:
            if rule.mode not in self.mode_rules:
                self.mode_rules[rule.mode] = []
            self.mode_rules[rule.mode].append(rule)
        else:
            self.global_rules.append(rule)
    
    def get_applicable_rules(self, filename: str, kernel: str = None, 
                           cell_type: str = None, mode: str = None, directory: str = None) -> List[Rule]:
        """Get all rules that apply to the given context."""
        applicable_rules = []
        
        # Add applicable global rules
        for rule in self.global_rules:
            if rule.matches_context(filename, kernel, cell_type, mode, directory):
                applicable_rules.append(rule)
        
        # Add applicable mode-specific rules
        if mode and mode in self.mode_rules:
            for rule in self.mode_rules[mode]:
                if rule.matches_context(filename, kernel, cell_type, mode, directory):
                    applicable_rules.append(rule)
        
        # Sort by priority (lower number = higher priority), then by filename
        applicable_rules.sort(key=lambda r: (r.priority, r.filename))
        
        return applicable_rules
    
    def get_all_rules(self) -> List[Rule]:
        """Get all rules in the ruleset."""
        all_rules = self.global_rules.copy()
        for mode_rules in self.mode_rules.values():
            all_rules.extend(mode_rules)
        return all_rules
    
    def get_rules_by_mode(self, mode: Optional[str] = None) -> List[Rule]:
        """Get rules for a specific mode (None for global rules)."""
        if mode is None:
            return self.global_rules.copy()
        return self.mode_rules.get(mode, []).copy()
    
    def toggle_rule(self, filename: str, active: bool) -> bool:
        """Toggle a rule's active state by filename. Returns True if rule was found."""
        for rule in self.get_all_rules():
            if rule.filename == filename:
                rule.active = active
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert ruleset to dictionary for serialization."""
        return {
            'global_rules': [rule.to_dict() for rule in self.global_rules],
            'mode_rules': {
                mode: [rule.to_dict() for rule in rules] 
                for mode, rules in self.mode_rules.items()
            }
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RuleSet':
        """Create ruleset from dictionary."""
        ruleset = cls()
        
        # Load global rules
        for rule_data in data.get('global_rules', []):
            rule = Rule.from_dict(rule_data)
            ruleset.global_rules.append(rule)
        
        # Load mode-specific rules
        for mode, mode_rule_data in data.get('mode_rules', {}).items():
            ruleset.mode_rules[mode] = []
            for rule_data in mode_rule_data:
                rule = Rule.from_dict(rule_data)
                rule.mode = mode
                ruleset.mode_rules[mode].append(rule)
        
        return ruleset

@dataclass 
class NotebookContext:
    """Context information for rule matching."""
    filename: str
    kernel: Optional[str] = None
    mode: Optional[str] = None
    directory: Optional[str] = None
    
    @property
    def basename(self) -> str:
        """Get just the filename without path."""
        return os.path.basename(self.filename)
    
    @property
    def extension(self) -> str:
        """Get file extension."""
        return os.path.splitext(self.filename)[1]