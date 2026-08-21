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
  - [Cell output actions](#cell-output-actions)
  - [Notebook toolbar generation](#notebook-toolbar-generation)
  - [Reload open files when changed on disk](#reload-open-files-when-changed-on-disk)
- [Configuration](#configuration)
  - [Configuration files](#configuration-files)
  - [Remembering GitHub Copilot login](#remembering-github-copilot-login)
- [Built-in tools](#built-in-tools)
- [Model Context Protocol (MCP) support](#model-context-protocol-mcp-support)
  - [MCP config example](#mcp-config-example)
- [Rulesets](#rulesets)
- [Claude Skills](#claude-skills)
- [Claude MCP Servers](#claude-mcp-servers)
- [Claude Plugins](#claude-plugins)
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
- **Skill**: a directory under `~/.claude/skills/` (or `<project>/.claude/skills/`) holding a `SKILL.md` plus helper files. Claude can invoke it like a callable plugin scoped to a workspace.
- **Claude plugin**: a unit packaged for `claude plugin install`, distributed through a **marketplace** (typically a GitHub repo that publishes a manifest of plugins). Distinct from NBI's own labextension; plugins run inside Claude Code sessions.

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

#### Permission modes

In Claude mode the chat input footer shows a shield-icon button (to the left of the send button) that sets the agent's permission mode for the chat panel, matching the modes in Claude Code and the Claude VS Code extension. Click it to choose:

- **Default**: every tool call the agent wants to run goes through NBI's confirmation prompt. You approve or reject each one. This is the starting mode.
- **Accept Edits**: file edits the agent makes apply without a per-edit prompt; other tool calls (running commands, etc.) still go through the confirmation prompt. Useful for iterative work where you trust the edits but still want a gate on everything else.
- **Plan**: the agent researches and proposes a plan **without making any changes**, then presents it for approval. Approving runs the plan and returns the selector to **Default**; rejecting keeps it planning. This replaces the old `/enter-plan-mode` slash command.
- **Bypass Permissions**: NBI's confirmation prompt is skipped for **every** tool call, including the Claude Code CLI's own Bash / Write / Edit running in the agent subprocess. The agent runs everything with your full account access and no confirmation, and any untrusted content it reads can steer what it runs. See the gating notes below.

Default, Accept Edits, and Plan switch the moment you pick them. The selected mode travels with each message you send and is applied to the agent before the turn runs; switching mid-conversation takes effect on your next message.

Bypass Permissions never persists: starting a **New chat session** (or `/clear`) always drops it and it has to be re-armed manually. The other modes carry over across a reset, and a fresh Claude client's starting mode is Default (or an administrator's managed `permissions.defaultMode`).

Choosing **Bypass Permissions** does not arm it immediately. It opens a confirmation step; only after you confirm does bypass take effect, and while it is active the shield turns into a red warning icon as a persistent indicator. Bypass must be re-armed each session: starting a new chat or restarting the Claude client drops back to Default. And because the server re-checks the requested mode on every message, an armed bypass can never outlive a policy that an administrator has since turned off.

**Admin gating.** Bypass Permissions is **off by default** and hidden from the selector unless an administrator enables it: it is governed by the `claude_bypass_permissions` policy (`NBI_CLAUDE_BYPASS_PERMISSIONS_POLICY`), one of three admin policies that default to `force-off` rather than `user-choice` (the others gate the experimental ACP agent mode and ACP full access). The requested mode is also clamped on the server for every message, so the gate can't be bypassed by a hand-crafted request. Independently, NBI honors Claude Code's enterprise [managed settings](https://code.claude.com/docs/en/settings): `permissions.disableBypassPermissionsMode` removes the option regardless of the NBI policy, and `permissions.defaultMode` sets the selector's starting mode (Bypass excepted, since it never auto-arms). See [Allowing Bypass Permissions](docs/admin-guide.md#allowing-bypass-permissions-in-the-claude-permission-mode-selector) in the admin guide.

The `/enter-plan-mode` and `/exit-plan-mode` slash commands still work if typed but are no longer offered in autocomplete; the selector replaces them and will retire the commands in a future release.

#### Resuming a previous Claude session

When Claude mode is on, the chat sidebar shows a history icon next to the gear. Click it to list the Claude Code sessions recorded for the current working directory (the same transcripts the Claude Code CLI stores under `~/.claude/projects/`). Selecting a session reconnects via `resume`, so the next message you send continues that transcript with full prior context. A **New chat session** button next to the gear restarts the SDK client without typing `/clear`.

Long Claude turns surface an elapsed-time counter and a heartbeat-driven pulse with a "may be slow" copy flip after 30 seconds. Each tool the agent runs shows up as a persistent status card with a kind icon and a live in-progress / done / failed state; edits carry an inline diff, and a run of consecutive calls collapses into one expandable group, so the sidebar reflects what the agent is doing rather than appearing stuck.

In Claude mode, workspace files attached as chat context arrive as `@`-mention pointers rather than inlined file contents. Claude's Read tool fetches them on demand, which means images, large files, and notebooks (cell-aware) now work where the older content-injection path silently truncated or skipped them.

#### Usage footer

Enable **Show usage after each turn** in the Settings dialog to append a small footer under each Claude-mode reply with the turn's duration, token counts (with a cached-input breakdown), and cost — for example `12.3s · 45.2K in (38.1K cached) / 1.2K out · $0.0842`. It is off by default.

The cost figure comes from the Claude Code CLI, priced from Anthropic's public list rates. It is shown only when NBI is configured with a direct Anthropic API key on the default endpoint, and is omitted otherwise — on a subscription login (where the marginal cost is effectively $0), an enterprise-negotiated contract, or a custom **Base URL** (proxy, gateway, or non-Anthropic endpoint) — since the list-price figure would not match your actual billing. Duration and token counts are always shown.

#### Claude Code launcher tile

When the Claude CLI is on `PATH`, the JupyterLab launcher (the panel that opens with new tabs) shows a **Claude Code** tile alongside the standard kernel launchers. Clicking it opens a session picker; search across past transcripts and resume one in a fresh terminal, or start a new session in the file browser's active subdirectory. Session IDs are copyable from the picker for paste into a `claude --resume <id>` command.

#### Other coding-agent launcher tiles

When any of the following CLIs are on `PATH`, the launcher adds a tile for each. Clicking a tile opens a terminal at the file browser's current directory and runs the CLI:

- **opencode** (override path with `NBI_OPENCODE_CLI_PATH`)
- **Pi** (override path with `NBI_PI_CLI_PATH`)
- **GitHub Copilot CLI** (override path with `NBI_GITHUB_COPILOT_CLI_PATH`)
- **OpenAI Codex** (override path with `NBI_CODEX_CLI_PATH`)

Tiles add and remove themselves as CLIs become available or unavailable; they do not require Claude mode. Clicking any tile (or **New Session** from the Claude resume dialog) prompts for a start directory, so the terminal opens where you want rather than always at the file-browser cwd.

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

You can paste or attach images alongside a chat prompt — the image goes to the model as input when the active model supports vision.

### Cell output actions

Right-click a cell output (or hover for the toolbar) to send it straight into the chat as context:

- **Explain cell errors** — surfaces a "Troubleshoot errors in output" entry on cells that raised; opens a chat turn with the traceback attached.
- **Ask about cell outputs** — attaches the output as structured context for a follow-up question. Includes images for vision-capable models.
- **Show output toolbar** — the floating toolbar above each output with quick **Explain** / **Ask** / **Troubleshoot** actions.

Each is per-user toggleable from Settings (saved as `enable_explain_error`, `enable_output_followup`, `enable_output_toolbar` in `config.json`, default on) and admin-lockable via `NBI_EXPLAIN_ERROR_POLICY` / `NBI_OUTPUT_FOLLOWUP_POLICY` / `NBI_OUTPUT_TOOLBAR_POLICY`.

### Notebook toolbar generation

Active notebooks show a sparkle icon on the toolbar. Click it to open a popover that scopes the generation request to that specific notebook — handy for multi-notebook sessions where you don't want the chat sidebar to compete for context.

### Reload open files when changed on disk

NBI reloads open document tabs when their files change on disk, so edits an AI agent makes via its Read/Write tools appear in the editor without a manual refresh. Tabs with unsaved local edits are skipped so user work is never clobbered. Toggle via the **NBI Settings dialog → External changes → "Refresh open files when changed on disk"** (default on).

## Configuration

Configure your provider, model, and API key from NBI Settings — the gear icon in the chat panel, the `/settings` chat command, or the JupyterLab command palette. For background, see the [provider blog post](https://nbi.plmbr.dev/blog/archive/support-for-any-llm-provider/).

<img src="media/provider-list.png" alt="Settings dialog" width=500 />

### Configuration files

NBI saves configuration at `~/.jupyter/nbi/config.json`. It also supports an environment-wide base configuration at `<env-prefix>/share/jupyter/nbi/config.json` — organizations can ship default configuration there, and user changes save as overrides on top.

These config files store provider, model, and MCP configuration. **API keys for custom LLM providers are also stored here in plaintext** — never commit `~/.jupyter/nbi/config.json` to git, share it, or sync it across users. If a key leaks, rotate it at the provider immediately.

> Manual edits to `config.json` require a JupyterLab restart to take effect. Edits via the Settings dialog are picked up live.

### Admin policies

Most settings panel toggles can be locked by org administrators. Two shapes:

**Boolean policies** use the `*_POLICY` suffix and accept three values: `user-choice` (default — user toggles freely), `force-on` (locked enabled), `force-off` (locked disabled). When forced, the panel control is disabled with a "Locked by your administrator" tooltip and any client-side write is ignored.

| Env var                                        | Locks the Settings panel control for                                                                                                                                                                                         |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NBI_EXPLAIN_ERROR_POLICY`                     | "Explain cell errors"                                                                                                                                                                                                        |
| `NBI_OUTPUT_FOLLOWUP_POLICY`                   | "Ask about cell outputs"                                                                                                                                                                                                     |
| `NBI_OUTPUT_TOOLBAR_POLICY`                    | "Show output toolbar"                                                                                                                                                                                                        |
| `NBI_CLAUDE_MODE_POLICY`                       | "Enable Claude mode"                                                                                                                                                                                                         |
| `NBI_ACP_MODE_POLICY`                          | "Enable ACP mode" (experimental agent mode over the Agent Client Protocol, with Codex as the first agent type; defaults to `force-off`, set `user-choice` to allow it)                                                       |
| `NBI_ACP_FULL_ACCESS_POLICY`                   | "Full access" for the ACP agent: run tools without asking (defaults to `force-off`, so the agent asks before anything beyond trusted read-only commands; `user-choice` lets users opt into unattended runs)                  |
| `NBI_CLAUDE_CONTINUE_CONVERSATION_POLICY`      | "Remember conversation history"                                                                                                                                                                                              |
| `NBI_CLAUDE_CODE_TOOLS_POLICY`                 | "Claude Code tools"                                                                                                                                                                                                          |
| `NBI_CLAUDE_JUPYTER_UI_TOOLS_POLICY`           | "Jupyter UI tools"                                                                                                                                                                                                           |
| `NBI_CLAUDE_SETTING_SOURCE_USER_POLICY`        | Setting source: User                                                                                                                                                                                                         |
| `NBI_CLAUDE_SETTING_SOURCE_PROJECT_POLICY`     | Setting source: Project                                                                                                                                                                                                      |
| `NBI_STORE_GITHUB_ACCESS_TOKEN_POLICY`         | "Remember my GitHub Copilot access token"                                                                                                                                                                                    |
| `NBI_SKILLS_MANAGEMENT_POLICY`                 | The Skills tab (force-off hides it and 403s the API; also disables the managed-skills reconciler)                                                                                                                            |
| `NBI_CLAUDE_MCP_MANAGEMENT_POLICY`             | The Claude-mode MCP Servers tab (force-off hides it and 403s `/claude-mcp/*`; independent of the non-Claude MCP Servers tab)                                                                                                 |
| `NBI_CLAUDE_PLUGINS_MANAGEMENT_POLICY`         | The Claude-mode Plugins tab (force-off hides it and 403s `/plugins/*`)                                                                                                                                                       |
| `NBI_CLAUDE_BYPASS_PERMISSIONS_POLICY`         | "Bypass Permissions" in the Claude permission-mode selector (defaults to `force-off`, as do `NBI_ACP_MODE_POLICY` and `NBI_ACP_FULL_ACCESS_POLICY`; `user-choice` exposes the option, which the user still arms per session) |
| `NBI_TERMINAL_DRAG_DROP_POLICY`                | Terminal drag-drop file attach feature                                                                                                                                                                                       |
| `NBI_REFRESH_OPEN_FILES_ON_DISK_CHANGE_POLICY` | "Refresh open files when changed on disk"                                                                                                                                                                                    |

The first three also have matching traitlets on `NotebookIntelligence` (`explain_error_policy`, `output_followup_policy`, `output_toolbar_policy`); add the others as needed in the same shape:

```python
c.NotebookIntelligence.claude_mode_policy = "force-on"
c.NotebookIntelligence.claude_jupyter_ui_tools_policy = "force-off"
```

Per-user preferences (default on for the cell-output features) live in `config.json` as `enable_explain_error`, `enable_output_followup`, `enable_output_toolbar`.

**List-shaped denylists** (LLM providers, built-in tools, coding-agent launcher tiles) use traitlets rather than `*_POLICY` env vars. See [`docs/admin-guide.md`](docs/admin-guide.md#restricting-features-for-managed-deployments) for the `disabled_providers`, `disabled_tools`, and `disabled_coding_agent_launchers` recipes.

**Value-presence locks** for non-boolean settings: setting the env var to a non-empty value pins the control to that value and disables it. Empty/unset = user-choice.

| Env var                                | Pins                                                                         |
| -------------------------------------- | ---------------------------------------------------------------------------- |
| `NBI_CHAT_MODEL_PROVIDER`              | General → Chat model → Provider                                              |
| `NBI_CHAT_MODEL_ID`                    | General → Chat model → Model                                                 |
| `NBI_INLINE_COMPLETION_MODEL_PROVIDER` | General → Auto-complete model → Provider                                     |
| `NBI_INLINE_COMPLETION_MODEL_ID`       | General → Auto-complete model → Model                                        |
| `NBI_CLAUDE_CHAT_MODEL`                | Claude → Chat model                                                          |
| `NBI_CLAUDE_INLINE_COMPLETION_MODEL`   | Claude → Auto-complete model                                                 |
| `ANTHROPIC_API_KEY`                    | Claude → API Key (input is locked + blanked; the SDK reads the env directly) |
| `ANTHROPIC_BASE_URL`                   | Claude → Base URL                                                            |
| `NBI_ACP_CHAT_MODEL`                   | ACP → Chat model                                                             |
| `OPENAI_API_KEY`                       | ACP → API Key (input is locked + blanked; the agent reads the env directly)  |
| `OPENAI_BASE_URL`                      | ACP → Base URL                                                               |

Provider IDs: `github-copilot`, `openai-compatible`, `litellm-compatible`, `ollama`, `none`. The `*_MODEL_ID` value is whatever the chosen provider exposes (e.g. `gpt-4o`, `llama3:latest`). Claude model IDs are the literal IDs from the Anthropic API (e.g. `claude-opus-4-7`, `claude-sonnet-4-6`); empty string = "Default (recommended)"; `NBI_CLAUDE_INLINE_COMPLETION_MODEL` also accepts `none` (no inline completion in Claude mode) or `inherit` (use the General-tab Auto-complete model).

**Upload tunables** govern the shared upload endpoint used by both chat-sidebar file attachments and terminal drag-drop:

| Env var                      | Default | Behavior                                                                                                                                                          |
| ---------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `NBI_UPLOAD_MAX_MB`          | `50`    | Per-file size cap in megabytes. Requests over the cap return HTTP 413. Set to `0` to disable the cap entirely.                                                    |
| `NBI_UPLOAD_RETENTION_HOURS` | `24`    | How long staged uploads survive in the temp directory before the next upload sweeps them. Set to `0` to keep only the atexit purge (uploads survive the session). |

The same values are also configurable via the `upload_max_mb` and `upload_retention_hours` traitlets on `NotebookIntelligence`.

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

The Settings panel exposes a top-level **Skills** tab for managing the skills that Claude can invoke. Skills are stored under `~/.claude/skills/` (user) or `<project>/.claude/skills/` (project). You can create and edit skills inline, duplicate, rename, delete (with undo), or import from a public GitHub repo. The tab is visible in any mode; when Claude mode is off, a hint banner notes that skills only take effect inside Claude sessions (handy for authoring skills now and using them when you turn Claude mode on later).

For organization-wide deployments, your admin can ship a curated manifest of skills and keep them in sync by setting `NBI_SKILLS_MANIFEST`. Skills installed this way are marked **Managed** and are read-only in the UI. Admins who want to keep managed skills but disallow ad-hoc GitHub imports use `NBI_ALLOW_GITHUB_SKILL_IMPORT=false`.

For full details, see [`docs/skills.md`](docs/skills.md).

## Claude MCP Servers

When Claude mode is enabled and the Claude CLI is available, the Settings panel exposes an **MCP Servers** tab that manages the user, project, and local-scope MCP entries Claude Code reads from `~/.claude.json` and the project's `.mcp.json`. This is a different tab from NBI's own MCP Servers tab (which manages the servers used by the non-Claude chat path); the two never appear together, and the Settings dialog shows whichever one applies to your current mode.

Reads come from Claude's JSON config files directly. Writes (add and remove) shell out to `claude mcp add` and `claude mcp remove` so Claude remains the source of truth for any side effects (project-trust prompts, OAuth bookkeeping). Each server entry can be toggled on or off per workspace without removing it, and the **Add MCP server** dialog accepts a JSON-paste path that takes a Claude / Cursor / VS Code MCP config blob and pre-fills the form after validating the shape. Admins can lock the tab with `NBI_CLAUDE_MCP_MANAGEMENT_POLICY=force-off`.

## Claude Plugins

When Claude mode is enabled and the Claude CLI is available, the Settings panel exposes a **Plugins** tab wrapping `claude plugin` for install, uninstall, enable, disable, and marketplace add (for example: add a marketplace from a GitHub repo, then install plugins it publishes). Each installed plugin's description, author, version, and source render inline, and the tab surfaces a per-plugin **Update** button when a newer version is available upstream. A marketplace picker lets you browse the configured marketplaces and install plugins directly from there. Marketplaces hosted on GitHub reuse the same `GITHUB_TOKEN` / `GH_TOKEN` / `gh auth token` precedence as Skills imports; the token is passed via env to the subprocess and never appears in argv or DEBUG logs. See Anthropic's [plugin docs](https://code.claude.com/docs/en/plugins) for what a plugin is and how marketplaces work.

Admins can lock the entire tab with `NBI_CLAUDE_PLUGINS_MANAGEMENT_POLICY=force-off`, or keep the tab and block only GitHub-sourced marketplaces with `NBI_ALLOW_GITHUB_PLUGIN_IMPORT=false`.

## Chat feedback

Enable thumbs-up/down feedback on AI responses by setting:

```python
c.NotebookIntelligence.enable_chat_feedback = True
```

…or via CLI:

```bash
jupyter lab --NotebookIntelligence.enable_chat_feedback=true
```

The feedback fires an in-process `telemetry` event. Nothing leaves the process by default. See the [admin guide](docs/admin-guide.md#telemetry-events) for the full set of telemetry events and how to wire them into your observability stack.

The thumbs buttons reveal on hover by default. To keep them always visible, enable:

```python
c.NotebookIntelligence.enable_chat_feedback_always_visible = True
```

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

- [Introducing Notebook Intelligence!](https://nbi.plmbr.dev/blog/archive/introducing-notebook-intelligence/)
- [Building AI Extensions for JupyterLab](https://nbi.plmbr.dev/blog/archive/building-ai-extensions-for-jupyterlab/)
- [Building AI Agents for JupyterLab](https://nbi.plmbr.dev/blog/archive/building-ai-agents-for-jupyterlab/)
- [Notebook Intelligence now supports any LLM Provider and AI Model!](https://nbi.plmbr.dev/blog/archive/support-for-any-llm-provider/)

## Roadmap

NBI 5.x is stable. New features land in minor releases (5.1, 5.2, …); breaking changes are reserved for the next major (6.x) and will be announced in the [changelog](CHANGELOG.md). Upgrading from 4.x? See the [5.0.0 migration note](CHANGELOG.md#migration-note) for the `fastmcp` → `mcp` dependency swap, the new path sandboxes, and the workspace-file-attach behavior change.

## License

Licensed under [GPL-3.0](LICENSE).
