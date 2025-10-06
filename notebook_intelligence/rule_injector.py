# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from notebook_intelligence.api import ChatRequest
from notebook_intelligence.rule_manager import RuleManager


class RuleInjector:
    """Handles rule injection logic - easily mockable."""
    
    def inject_rules(self, base_prompt: str, request: ChatRequest) -> str:
        """Inject applicable rules into system prompt based on request context."""
        if not request.notebook_context:
            return base_prompt
            
        rule_manager: RuleManager = request.host.get_rule_manager()
        if not rule_manager or not request.host.nbi_config.rules_enabled:
            return base_prompt
            
        if not request.host.nbi_config.rules_context_injection_enabled:
            return base_prompt
            
        applicable_rules = rule_manager.get_applicable_rules(request.notebook_context)
        if not applicable_rules:
            return base_prompt
            
        rule_context = rule_manager.format_rules_for_llm(applicable_rules)
        return f"{base_prompt}\n\n# Additional Guidelines\n{rule_context}"
