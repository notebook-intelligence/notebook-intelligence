# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import logging
from pathlib import Path

from notebook_intelligence.api import ChatRequest
from notebook_intelligence.chat_history_budget import (
    text_token_count,
    truncate_text,
)
from notebook_intelligence.rule_manager import RuleManager
from notebook_intelligence.util import get_jupyter_root_dir

log = logging.getLogger(__name__)
_TRUNCATION_MARKER = "\n...[additional guidelines truncated]"


class RuleInjector:
    """Handles rule injection logic - easily mockable."""

    def _read_agents_md(self) -> str:
        project_root = get_jupyter_root_dir()
        if not project_root:
            return ''

        agents_path = Path(project_root) / 'AGENTS.md'
        if not agents_path.is_file():
            return ''

        try:
            return agents_path.read_text(encoding='utf-8').strip()
        except Exception as e:
            log.warning(f"Failed to read AGENTS.md from {agents_path}: {e}")
            return ''
    
    def inject_rules(
        self,
        base_prompt: str,
        request: ChatRequest,
        max_tokens: int = None,
    ) -> str:
        """Inject applicable rules into system prompt based on request context."""
        if not request.host.nbi_config.rules_enabled:
            return base_prompt

        sections = []

        agents_md = self._read_agents_md()
        if agents_md:
            sections.append(f"# Repository Instructions (AGENTS.md)\n{agents_md}")

        if request.rule_context:
            rule_manager: RuleManager = request.host.get_rule_manager()
            if rule_manager:
                applicable_rules = rule_manager.get_applicable_rules(request.rule_context)
                if applicable_rules:
                    formatted_rules = rule_manager.format_rules_for_llm(applicable_rules)
                    sections.append(formatted_rules)

        if not sections:
            return base_prompt

        guidelines = "# Additional Guidelines\n" + "\n\n".join(sections)
        separator = "\n\n" if base_prompt else ""
        if max_tokens is not None:
            base_tokens = text_token_count(base_prompt + separator)
            guideline_budget = max_tokens - base_tokens
            if guideline_budget <= 0:
                return base_prompt
            if text_token_count(guidelines) > guideline_budget:
                guidelines = truncate_text(
                    guidelines,
                    guideline_budget,
                    _TRUNCATION_MARKER,
                )
                if not guidelines:
                    return base_prompt

        return base_prompt + separator + guidelines
