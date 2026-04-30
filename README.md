# Notebook Intelligence

Notebook Intelligence (NBI) is an AI coding assistant and extensible AI framework for JupyterLab. It adds chat, inline edit, auto-complete, and an agent that can drive notebooks — backed by GitHub Copilot, an OpenAI-compatible or LiteLLM-compatible endpoint, local [Ollama](https://ollama.com/) models, or Anthropic's Claude Code CLI.

NBI is free and open-source. Connect it to a free or paid LLM provider of your choice — GitHub Copilot, any OpenAI- or LiteLLM-compatible endpoint, Ollama (local), or Anthropic Claude (via the Claude Code CLI). Provider charges, when applicable, are paid directly to the provider.

## Contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Concepts](#concepts)
- [Feature highlights](#feature-highlights)
  - [Claude mode](#claude-mode)
  - [Agent mode](#agent-mode)
  - [Code generation with inline chat](#code-generation-with-inline-chat)
  - [Auto-complete](#auto-complete)
  - [Chat interface](#chat-interface)
- [Configuration](#configuration)
  - [Configuration files](#configuration-files)
  - [Remembering GitHub Copilot login](#remembering-github-copilot-login)
- [Built-in tools](#built-in-tools)
- [Model Context Protocol (MCP) support](#model-context-protocol-mcp-support)
  - [MCP config example](#mcp-config-example)
- [Rulesets](#rulesets)
- [Claude Skills](#claude-skills)
- [Chat feedback](#chat-feedback)
- [Documentation](#documentation)
- [Further reading](#further-reading)
- [Roadmap](#roadmap)
- [License](#license)

## Requirements

- Python 3.10+
- JupyterLab 4.x
- Node.js — only required for [Claude mode](#claude-mode) (the Claude Code CLI) and for MCP servers that launch via `npx`.
- A fresh virtualenv or conda env is recommended so NBI doesn't conflict with system Python.

## Quick start

```bash
pip install notebook-intelligence
jupyter lab     # restart JupyterLab if it was already running
```

After restart:

1. Click the NBI icon in the left sidebar to open the chat panel.
2. Open NBI Settings (gear icon in the chat panel, or _Settings → Notebook Intelligence Settings_).
3. Sign into your provider — for GitHub Copilot, click _Sign in_; for an OpenAI- or LiteLLM-compatible endpoint, paste an API key; for Ollama, point at your local daemon. To use Claude, enable Claude mode (see below).
4. Type a message in the chat panel and press Enter.

If the panel stays empty or login does nothing, see [Troubleshooting](docs/troubleshooting.md).

## Concepts

A short glossary you'll see referenced throughout these docs.

- **LLM Provider** — the service that runs the model. NBI ships with four provider adapters: GitHub Copilot, OpenAI-compatible, LiteLLM-compatible, and Ollama. Anthropic Claude is available through [Claude mode](#claude-mode), not as a top-level provider.
- **Chat Participant** — a `@mention`-able persona inside the chat panel (`@workspace`, `@mcp`, …). Participants route the request to a specific tool surface.
- **Default mode vs Claude mode** — _Default_ uses the configured LLM Provider for chat, inline chat, and auto-complete. _Claude mode_ uses the Claude Code CLI for the chat panel (gaining its tools, skills, MCP servers, and custom commands) and Claude models via the Anthropic API for inline chat and auto-complete. Requires the Claude Code CLI on `PATH`.
- **Claude Code vs the Anthropic API** — the _Anthropic API_ (`api.anthropic.com`) is the HTTPS endpoint NBI calls directly for inline chat and auto-complete in Claude mode. _Claude Code_ is Anthropic's local CLI agent that NBI shells out to for the chat panel; it talks to Anthropic itself.
- **MCP** — [Model Context Protocol](https://modelcontextprotocol.io/). A way for the LLM to call out to external tools (read files, hit APIs, run scripts).
- **Ruleset** — markdown files in `~/.jupyter/nbi/rules/` that get injected into the system prompt to enforce conventions, coding standards, or domain rules.

## Feature highlights

### Claude mode

NBI provides a dedicated mode for [Claude Code](https://code.claude.com/) integration. In **Claude mode**, NBI uses the Claude Code CLI for the chat panel, and Claude models (via the Anthropic API) for inline chat and auto-complete suggestions. This brings Claude Code's tools, skills, MCP servers, and custom commands into JupyterLab.

<img src="media/claude-chat.png" alt="Claude mode" width=500 />

Configure via the NBI Settings dialog (gear icon in the chat panel, or _Settings → Notebook Intelligence Settings_). Toggle _Enable Claude mode_, then:

- **Chat model** — the Claude model used for the chat panel and inline chat.
- **Auto-complete model** — the Claude model used for auto-complete suggestions.
- **Chat Agent setting sources** — user, project, or both, mirroring [Claude Code's settings](https://code.claude.com/docs/en/settings).
- **Chat Agent tools** — which tool sets to activate. _Claude Code tools_ are always on. _Jupyter UI tools_ are NBI's own (authoring notebooks, running cells, etc.).
- **API key** and **Base URL** — point at Anthropic or a self-hosted endpoint.

If the Claude Code CLI is on `PATH`, NBI launches it automatically. To override the location, set the `NBI_CLAUDE_CLI_PATH` environment variable before starting JupyterLab.

<img src="media/claude-settings.png" alt="Claude settings" width=700 />

#### Resuming a previous Claude session

When Claude mode is on, the chat sidebar shows a history icon next to the gear. Click it to list the Claude Code sessions recorded for the current working directory (the same transcripts the Claude Code CLI stores under `~/.claude/projects/`). Selecting a session reconnects via `resume`, so the next message you send continues that transcript with full prior context.

### Agent mode

In Agent mode, the built-in AI agent creates, edits, and executes notebooks for you interactively. It can detect issues in cells and fix them.

![Agent mode](media/agent-mode.gif)

### Code generation with inline chat

Use the sparkle icon on the cell toolbar or the keyboard shortcut to show the inline chat popover.

`Ctrl+G` / `Cmd+G` opens the popover. `Ctrl+Enter` / `Cmd+Enter` accepts the suggestion. `Esc` closes it. The accept shortcut overrides JupyterLab's default _run cell_ binding **only while the popover is open** — outside the popover, `Ctrl+Enter` / `Cmd+Enter` still runs the active cell.

![Generate code](media/generate-code.gif)

### Auto-complete

Auto-complete suggestions are shown as you type. `Tab` accepts. NBI provides auto-complete in code cells and Python file editors.

<img src="media/inline-completion.gif" alt="Auto-complete" width=700 />

### Chat interface

<img src="media/copilot-chat.gif" alt="Chat interface" width=600 />

## Configuration

Configure your provider, model, and API key from NBI Settings — the gear icon in the chat panel, the `/settings` chat command, or the JupyterLab command palette. For background, see the [provider blog post](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/03/05/support-for-any-llm-provider.html).

<img src="media/provider-list.png" alt="Settings dialog" width=500 />

### Configuration files

NBI saves configuration at `~/.jupyter/nbi/config.json`. It also supports an environment-wide base configuration at `<env-prefix>/share/jupyter/nbi/config.json` — organizations can ship default configuration there, and user changes save as overrides on top.

These config files store provider, model, and MCP configuration. **API keys for custom LLM providers are also stored here in plaintext** — never commit `~/.jupyter/nbi/config.json` to git, share it, or sync it across users. If a key leaks, rotate it at the provider immediately.

> Manual edits to `config.json` require a JupyterLab restart to take effect. Edits via the Settings dialog are picked up live.

### Cell output features

Two cell-output context affordances are user-toggleable in the Settings panel:

- **Troubleshoot errors in output** — a context-menu entry on failed cells that sends the traceback + failing code to the chat model.
- **Ask about this output** — a context-menu entry on cell outputs that attaches `{cell code, output payload}` to the chat sidebar as grounded context.

Per-user preferences (default on):

```python
c.NBIConfig.enable_explain_error = True
c.NBIConfig.enable_output_followup = True
```

Org-wide policy traitlets (override the user preference when set to `force-on` or `force-off`):

```python
c.NotebookIntelligence.explain_error_policy = "user-choice"   # default
c.NotebookIntelligence.output_followup_policy = "user-choice"
```

Equivalent environment-variable overrides:

- `NBI_EXPLAIN_ERROR_POLICY` — `user-choice` | `force-on` | `force-off`
- `NBI_OUTPUT_FOLLOWUP_POLICY` — `user-choice` | `force-on` | `force-off`

When a policy is `force-on` / `force-off`, the corresponding Settings panel checkbox is greyed out with a "Locked by your administrator" tooltip and any client-side write is ignored.

### Remembering GitHub Copilot login

NBI can remember your GitHub Copilot login so you don't have to sign in again after a JupyterLab or system restart.

> [!CAUTION]
> If you enable this, NBI encrypts the token and stores it in `~/.jupyter/nbi/user-data.json`. Never share this file. The encryption uses a default password unless you set `NBI_GH_ACCESS_TOKEN_PASSWORD` to a custom value — on shared or multi-tenant systems, set a custom password before enabling this option.

```bash
NBI_GH_ACCESS_TOKEN_PASSWORD=my_custom_password
```

To enable, check _Remember my GitHub Copilot access token_ in the Settings dialog.

<img src="media/remember-gh-access-token.png" alt="Remember access token" width=500 />

If the stored token fails to authenticate (expired, revoked, password mismatch), NBI prompts you to sign in again.

## Built-in tools

These tools are available in Agent mode and to MCP-enabled chats.

| Tool                                          | What it does                                                                           |
| --------------------------------------------- | -------------------------------------------------------------------------------------- |
| **Notebook Edit** (`nbi-notebook-edit`)       | Edit notebooks via the JupyterLab notebook editor.                                     |
| **Notebook Execute** (`nbi-notebook-execute`) | Run notebooks in the JupyterLab UI.                                                    |
| **Python File Edit** (`nbi-python-file-edit`) | Edit Python files via the JupyterLab file editor.                                      |
| **File Edit** (`nbi-file-edit`)               | Edit files in the Jupyter root directory.                                              |
| **File Read** (`nbi-file-read`)               | Read files in the Jupyter root directory.                                              |
| **Command Execute** (`nbi-command-execute`)   | Execute shell commands using the embedded terminal in Agent UI or JupyterLab terminal. |

In multi-tenant deployments, `nbi-command-execute` and `nbi-file-edit` are effectively arbitrary code execution as the user. See [`docs/admin-guide.md`](docs/admin-guide.md#security-model) for guidance on disabling them.

## Model Context Protocol (MCP) support

NBI integrates with [MCP](https://modelcontextprotocol.io) servers. It supports both stdio and Streamable HTTP transports. **MCP server tools are supported; resources and prompts are not yet supported.**

Add MCP servers by editing `~/.jupyter/nbi/mcp.json`. An environment-wide base file at `<env-prefix>/share/jupyter/nbi/mcp.json` is also supported.

> [!NOTE]
> MCP requires an LLM model with tool-calling capability. All GitHub Copilot models in NBI support this. For other providers, choose a tool-calling-capable model.

> [!CAUTION]
> Most MCP servers run on the same machine as JupyterLab and can make irreversible changes or access private data. Only install MCP servers from trusted sources.

### MCP config example

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/Users/mbektas/mcp-test"
      ]
    }
  }
}
```

For stdio servers you can pass extra environment variables under `env`:

```json
"mcpServers": {
    "servername": {
        "command": "",
        "args": [],
        "env": {
            "ENV_VAR_NAME": "ENV_VAR_VALUE"
        }
    }
}
```

For Streamable HTTP servers you can also specify request headers:

```json
"mcpServers": {
    "remoteservername": {
        "url": "http://127.0.0.1:8080/mcp",
        "headers": {
            "Authorization": "Bearer mysecrettoken"
        }
    }
}
```

To temporarily disable a configured server without removing it, set `"disabled": true`:

```json
"mcpServers": {
    "servername2": {
        "command": "",
        "args": [],
        "disabled": true
    }
}
```

## Rulesets

NBI's ruleset system lets you define guidelines and best practices that get injected into AI prompts automatically — for consistent coding standards, project conventions, or domain knowledge. Rules are markdown files in `~/.jupyter/nbi/rules/` and can scope by file pattern, kernel, directory, or chat mode.

A two-line example:

```markdown
---
priority: 10
---

- Always use type hints in Python functions.
- Add docstrings to all public functions.
```

For full details (frontmatter reference, mode-specific rules, auto-reload), see [`docs/rulesets.md`](docs/rulesets.md).

## Claude Skills

When Claude mode is enabled, the Settings panel exposes a **Skills** tab for managing the skills that Claude can invoke. Skills are stored under `~/.claude/skills/` (user) or `<project>/.claude/skills/` (project). You can create and edit skills inline, duplicate, rename, delete (with undo), or import from a public GitHub repo.

For organization-wide deployments, NBI can install and keep a curated set of skills in sync from a YAML manifest pointed at by `NBI_SKILLS_MANIFEST`. Managed skills are read-only in the UI and refreshed on a schedule.

For full details, see [`docs/skills.md`](docs/skills.md).

## Chat feedback

Enable thumbs-up/down feedback on AI responses by setting:

```python
c.NotebookIntelligence.enable_chat_feedback = True
```

…or via CLI:

```bash
jupyter lab --NotebookIntelligence.enable_chat_feedback=true
```

The feedback fires an in-process `telemetry` event. Nothing leaves the process by default — see the [admin guide](docs/admin-guide.md#chat-feedback-event-hook) for how to wire it into your observability stack.

<img src="media/chat-feedback.png" alt="Chat feedback" width=500 />

## Documentation

- [`docs/admin-guide.md`](docs/admin-guide.md) — deployment, env vars, security model, air-gap, multi-tenancy.
- [`docs/skills.md`](docs/skills.md) — Claude Skills management and the org-manifest reconciler.
- [`docs/rulesets.md`](docs/rulesets.md) — ruleset frontmatter and discovery.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — common problems with copy-pasteable fixes.
- [`PRIVACY.md`](PRIVACY.md) — what NBI sends to which provider, and the egress allowlist.
- [`SECURITY.md`](SECURITY.md) — how to report a vulnerability.
- [`CHANGELOG.md`](CHANGELOG.md) — release history.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — building NBI from source. Skip this if you just want to use NBI.

## Further reading

- [Introducing Notebook Intelligence!](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/01/08/introducing-notebook-intelligence.html)
- [Building AI Extensions for JupyterLab](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/02/05/building-ai-extensions-for-jupyterlab.html)
- [Building AI Agents for JupyterLab](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/02/09/building-ai-agents-for-jupyterlab.html)
- [Notebook Intelligence now supports any LLM Provider and AI Model!](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/03/05/support-for-any-llm-provider.html)

## Roadmap

NBI 4.x is stable. New features land in minor releases (4.5, 4.6, …); breaking changes are reserved for the next major (5.x) and will be announced in the [changelog](CHANGELOG.md).

## License

Licensed under [GPL-3.0](LICENSE).
