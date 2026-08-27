# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import logging
from pathlib import Path

import tiktoken

from notebook_intelligence.api import ChatRequest
from notebook_intelligence.rule_manager import RuleManager
from notebook_intelligence.util import get_jupyter_root_dir

log = logging.getLogger(__name__)
_TOKEN_ENCODING = tiktoken.encoding_for_model("gpt-4o")
_TRUNCATION_MARKER = "\n...[additional guidelines truncated]"


def _rules_enabled(host) -> bool:
    """``rules_enabled`` is the master switch for all injected guidance."""
    if host is None:
        return False
    nbi_config = getattr(host, "nbi_config", None)
    return bool(getattr(nbi_config, "rules_enabled", False))


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
        return self.inject_guidelines(
            base_prompt,
            host=getattr(request, "host", None),
            rule_context=getattr(request, "rule_context", None),
            max_tokens=max_tokens,
        )

    def inject_guidelines(
        self,
        base_prompt: str,
        host=None,
        rule_context=None,
        max_tokens: int = None,
    ) -> str:
        """Inject AGENTS.md and applicable rules into a system prompt."""
        if not _rules_enabled(host):
            return base_prompt

        sections = []

        agents_md = self._read_agents_md()
        if agents_md:
            sections.append(f"# Repository Instructions (AGENTS.md)\n{agents_md}")

        if rule_context and host is not None:
            rule_manager: RuleManager = host.get_rule_manager()
            if rule_manager:
                applicable_rules = rule_manager.get_applicable_rules(rule_context)
                if applicable_rules:
                    formatted_rules = rule_manager.format_rules_for_llm(applicable_rules)
                    sections.append(formatted_rules)

        if not sections:
            return base_prompt

        guidelines = "# Additional Guidelines\n" + "\n\n".join(sections)
        separator = "\n\n" if base_prompt else ""
        if max_tokens is not None:
            base_tokens = len(_TOKEN_ENCODING.encode(base_prompt + separator))
            guideline_budget = max_tokens - base_tokens
            if guideline_budget <= 0:
                return base_prompt
            encoded_guidelines = _TOKEN_ENCODING.encode(guidelines)
            if len(encoded_guidelines) > guideline_budget:
                marker_tokens = _TOKEN_ENCODING.encode(_TRUNCATION_MARKER)
                content_budget = guideline_budget - len(marker_tokens)
                if content_budget <= 0:
                    return base_prompt
                guidelines = (
                    _TOKEN_ENCODING.decode(encoded_guidelines[:content_budget]).rstrip()
                    + _TRUNCATION_MARKER
                )

        return base_prompt + separator + guidelines


def has_chatbook_guidelines(host) -> bool:
    """True when Chatbook generation should include rules or AGENTS.md."""
    if not _rules_enabled(host):
        return False
    if RuleInjector()._read_agents_md():
        return True
    rule_manager = host.get_rule_manager()
    if not rule_manager:
        return False
    rule_manager.load_rules()
    rules = list(rule_manager.ruleset.global_rules)
    rules.extend(rule_manager.ruleset.mode_rules.get("chatbook") or [])
    return any(rule.active for rule in rules)
