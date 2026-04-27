# Contributing

Thanks for considering a contribution to Notebook Intelligence!

> **Just want to use NBI?** You don't need to read this file. `pip install notebook-intelligence` and the [README](README.md) quick start are all you need.

## Filing a good bug report

Include the following so we can reproduce the issue:

- **NBI version** — output of `pip show notebook-intelligence`.
- **JupyterLab version** — output of `jupyter --version`.
- **Python version and OS** — `python --version`; macOS, Linux, or Windows plus version.
- **Browser** — Chrome, Firefox, or Safari plus version, if the issue is in the chat sidebar or settings UI.
- **LLM provider** — GitHub Copilot, OpenAI-compatible, LiteLLM-compatible, Ollama, or Claude mode, plus the model name.
- **Claude mode** — on or off.
- **Reproduction steps** — minimum sequence of clicks and messages.
- **Logs** — relevant excerpts from the JupyterLab terminal (server-side errors), the browser DevTools console (frontend errors), and any redacted contents of `~/.jupyter/nbi/config.json` if the issue is configuration-related.

See [`docs/troubleshooting.md`](docs/troubleshooting.md) for common problems with copy-pasteable fixes — check there first.

## Reporting a security issue

Do not open a public GitHub issue. See [SECURITY.md](SECURITY.md) for the private-disclosure address.

## Architecture overview

NBI has two halves:

- **Server extension** — Python package `notebook_intelligence/`. Runs inside Jupyter Server. Key entry points:
  - `notebook_intelligence/extension.py` — tornado handlers, traitlets, route registration, server lifecycle.
  - `notebook_intelligence/ai_service_manager.py` — composes LLM providers, MCP, skills, and rules into the request pipeline.
  - `notebook_intelligence/llm_providers/` — provider adapters (GitHub Copilot, OpenAI-compatible, LiteLLM-compatible, Ollama).
  - `notebook_intelligence/claude.py` + `notebook_intelligence/claude_sessions.py` — Claude Code integration via [`claude-agent-sdk`](https://pypi.org/project/claude-agent-sdk/).
  - `notebook_intelligence/mcp_manager.py` — MCP server management via [`fastmcp`](https://pypi.org/project/fastmcp/).
  - `notebook_intelligence/skill_manager.py`, `skill_github_import.py`, `skill_manifest.py`, `skill_reconciler.py`, `skillset.py` — Claude Skills storage, GitHub import, and managed-manifest reconciliation.
  - `notebook_intelligence/rule_manager.py`, `rule_injector.py`, `ruleset.py` — ruleset discovery and prompt injection.
  - `notebook_intelligence/built_in_toolsets.py` — built-in tool implementations (`nbi-notebook-edit`, `nbi-command-execute`, etc.).
  - `notebook_intelligence/github_copilot.py` — GitHub Copilot device-flow auth and token storage.
- **Frontend extension** — TypeScript package `src/`. Compiled to a JupyterLab labextension. Key entry points:
  - `src/index.ts` — JupyterLab plugin registration.
  - `src/chat-sidebar.tsx` — chat sidebar React tree.
  - `src/components/settings-panel.tsx` — settings dialog.
  - `src/components/skills-panel.tsx` — Claude Skills management UI.
  - `src/api.ts` — high-level client for the server extension (chat WebSocket, capabilities, config).
  - `src/handler.ts` — thin wrapper over Jupyter's `ServerConnection.makeRequest`.

The two halves communicate over the routes registered in `extension.py` (REST and WebSocket). All routes live under `/notebook-intelligence/`. See [`docs/admin-guide.md`](docs/admin-guide.md#http-api-surface) for the full list.

## Development install

You'll need Node.js 18 or newer to build the frontend. The `jlpm` command is JupyterLab's pinned version of [yarn](https://yarnpkg.com/) — install JupyterLab first to get it.

```bash
# Clone the repo and change into the directory.
# Install the package in development mode.
pip install -e "."

# Link the development version of the extension with JupyterLab.
jupyter labextension develop . --overwrite

# Server extension must be manually installed in develop mode.
jupyter server extension enable notebook_intelligence

# Build the TypeScript source.
jlpm build
```

Run JupyterLab and the watch loop in two terminals to pick up source changes automatically:

```bash
# Terminal 1
jlpm watch
# Terminal 2
jupyter lab
```

Refresh the browser tab to load the rebuilt frontend. To get source maps for JupyterLab core extensions as well:

```bash
jupyter lab build --minimize=False
```

### Development uninstall

```bash
jupyter server extension disable notebook_intelligence
pip uninstall notebook_intelligence
```

The `jupyter labextension develop` command leaves a symlink behind. Run `jupyter labextension list` to find the labextensions directory, then remove the `@notebook-intelligence/notebook-intelligence` symlink there.

## Running tests

TypeScript unit tests:

```bash
jlpm test
```

There is no Python test suite at the moment. Manual end-to-end verification is documented per change in pull request descriptions.

## Linting

```bash
jlpm lint:check   # check, no fixes
jlpm lint         # check and auto-fix prettier, eslint, and stylelint
```

CI runs `lint:check`. Identifiers prefixed with `_` are treated as intentionally unused and excluded from the unused-vars rule.

If `jlpm install` produces unexpected lockfile changes, your local Yarn version probably differs from the one bundled with JupyterLab. `jlpm` ships with JupyterLab — use it directly instead of a system-wide `yarn`.

## Packaging

See [RELEASE.md](RELEASE.md).

## Frontend extension layout sanity check

If you see the frontend extension but it isn't working, check the server extension is enabled:

```bash
jupyter server extension list
```

If the server extension is enabled but the frontend isn't loading:

```bash
jupyter labextension list
```

## Resources

- [Copilot Internals blog post](https://thakkarparth007.github.io/copilot-explorer/posts/copilot-internals.html)
- [B00TK1D/copilot-api](https://github.com/B00TK1D/copilot-api) — GitHub Copilot auth and inline completions
