# Ruleset System

NBI's ruleset system lets you inject custom guidelines into AI prompts so the assistant follows project conventions, coding standards, or domain knowledge consistently. Rules are markdown files with optional YAML frontmatter, discovered automatically and applied based on context.

## How it works

Rules live in `~/.jupyter/nbi/rules/`. NBI loads them at startup, watches the directory for changes, and selects which rules apply to each chat turn based on the file frontmatter and the current context (file type, notebook kernel, chat mode).

Selected rules are concatenated in priority order and prepended to the system prompt sent to the LLM.

## Creating rules

### Global rules — apply to all contexts

Create `~/.jupyter/nbi/rules/01-coding-standards.md`:

```markdown
---
priority: 10
---

# Coding Standards

- Always use type hints in Python functions.
- Prefer list comprehensions over loops when appropriate.
- Add docstrings to all public functions.
```

### Mode-specific rules — apply only to a chat mode

NBI has three chat modes: `ask` (Q&A), `agent` (autonomous tool use), and `inline-chat` (cell-level code generation and edit).

Create `~/.jupyter/nbi/rules/modes/agent/01-testing.md`:

```markdown
---
priority: 20
scope:
  kernels: ['python3']
---

# Testing Guidelines

When writing code in agent mode:

- Always include error handling.
- Add logging for debugging.
- Test edge cases.
```

## Frontmatter reference

```yaml
---
apply: always # 'always', 'auto', or 'manual'. Default: 'always'.
active: true # Set false to disable without deleting. Default: true.
priority: 10 # Lower numbers apply first. Default: 100.
scope:
  file_patterns: # Apply only when the active file matches.
    - '*.py'
    - 'test_*.ipynb'
  kernels: # Apply only for these notebook kernels.
    - 'python3'
    - 'ir'
  directories: # Apply only when working under these paths.
    - '/projects/ml'
---
```

All `scope` fields are optional. A rule with no `scope` applies to every context (subject to mode and `apply`).

## Discovery layout

```
~/.jupyter/nbi/rules/
├── *.md                          # global rules
└── modes/
    ├── ask/*.md                  # apply only in ask mode
    ├── agent/*.md                # apply only in agent mode
    └── inline-chat/*.md          # apply only in inline-chat mode
```

## Enabling, disabling, and managing rules

The Settings dialog has a Rules tab listing every discovered rule with a toggle. Toggling marks the rule inactive for the current session — it does not delete the file or rewrite the frontmatter. To persistently disable a rule, set `active: false` in its frontmatter.

To disable the system entirely, edit `~/.jupyter/nbi/config.json`:

```json
{
  "rules_enabled": false
}
```

## Auto-reload

By default, NBI watches `~/.jupyter/nbi/rules/` and reloads rules on change without requiring a JupyterLab restart. The `NBI_RULES_AUTO_RELOAD` environment variable controls this:

```bash
export NBI_RULES_AUTO_RELOAD=false   # disable; restart JupyterLab to pick up rule changes
export NBI_RULES_AUTO_RELOAD=true    # default
```

## Tips

- Use `priority` to break ties when multiple rules cover the same topic. Lower number wins.
- Keep individual rules short and focused. The LLM benefits more from five concise rules than one sprawling one.
- For machine-generated rules (e.g., committed to a project repo), set `apply: manual` so users must explicitly enable them in the Rules tab.
