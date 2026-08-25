# Ruleset System

NBI's ruleset system lets you inject custom guidelines into AI prompts so the assistant follows project conventions, coding standards, or domain knowledge consistently. Rules are markdown files with optional YAML frontmatter, discovered automatically and applied based on context.

## How it works

Rules live in `~/.jupyter/nbi/rules/`. NBI loads them at startup, watches the directory for changes, and selects which rules apply to each chat turn based on the file frontmatter and the current context (file name, notebook language and kernel, cell type, working directory, and chat mode).

Selected rules are concatenated in priority order and prepended to the system prompt sent to the LLM.

## Repository-level `AGENTS.md`

If the Jupyter project root contains an `AGENTS.md`, NBI appends it to the system prompt under the **Additional Guidelines** heading alongside the rules described below. This works the same as the [AGENTS.md convention](https://agents.md/) used by other coding agents. The file is project-scoped: each project's `AGENTS.md` only applies when JupyterLab is launched from that directory.

`AGENTS.md` and the ruleset system are additive — both contribute to the same prompt section when both exist.

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
  languages: ['python']
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
active: true # Set false to disable without deleting. Default: true.
priority: 10 # Lower numbers apply first. Default: 0.
apply: always # 'always', 'auto', or 'manual'. Default: 'always'. See the note below.
scope:
  file_patterns: # Apply only when the active file matches (fnmatch).
    - '*.py'
    - 'test_*.ipynb'
  languages: # Apply only for these notebook languages.
    - 'python'
    - 'r'
  kernel_names: # Apply only for these kernelspec names.
    - 'python3'
    - 'ir'
  directory_patterns: # Apply only under these paths (fnmatch, so wildcards).
    - '*/projects/ml/*'
  cell_types: # Apply only for these cell types.
    - 'code'
---
```

All `scope` fields are optional, and every one of them is an allowlist: a field that is absent or empty matches everything. A rule with no `scope` at all applies to every context, subject to its mode directory and `active`.

Two things worth knowing before you write a `scope`:

- **`directory_patterns` are fnmatch patterns, not path prefixes.** `'/projects/ml'` matches only that exact string; to match everything underneath it, write `'*/projects/ml/*'`.
- **`languages` and `kernel_names` are different things.** `languages` matches the notebook's language (`python`, `r`); `kernel_names` matches the installed kernelspec name (`python3`, `ir`). Pick whichever you actually mean. An earlier `scope.kernels` key conflated the two and was removed in 5.3.0: a rule that still uses it is rejected at load with an error naming the file, rather than being silently ignored.

**On `apply`:** the value is parsed, validated, and reported by the rules API, but it does not currently affect whether a rule is selected. Only `active` does. Treat `apply` as metadata until that changes; a rule you do not want applied needs `active: false`.

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

To disable a single rule, set `active: false` in its frontmatter. With auto-reload on (the default), the change takes effect without restarting JupyterLab.

There is no Rules tab in the Settings dialog. The server does expose the rule inventory over REST, so a deployment that wants a management surface can build one against these routes (all of them require Jupyter authentication, like every other NBI route):

| Route                                      | Method | Purpose                                                             |
| ------------------------------------------ | ------ | ------------------------------------------------------------------- |
| `/notebook-intelligence/rules`             | GET    | List every discovered rule with its frontmatter and resolved scope. |
| `/notebook-intelligence/rules/<id>/toggle` | PUT    | Toggle a rule's `active` field for the current session.             |
| `/notebook-intelligence/rules/reload`      | POST   | Re-read the rules directory immediately.                            |

A toggle through that route lasts for the session only: it does not rewrite the file's frontmatter, so the rule comes back on the next reload. `active: false` in the file is what persists.

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
- Scope broadly and rely on `priority` for ordering, rather than writing many narrowly scoped rules. A rule that matches nothing is silent, so an over-tight `scope` is hard to notice.
