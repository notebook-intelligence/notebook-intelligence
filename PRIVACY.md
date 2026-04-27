# Privacy and Data Flow

This page documents what NBI sends to external services, when, and how administrators can restrict it. NBI is a per-user tool that runs inside your Jupyter Server process — it has no central server of its own and collects no telemetry by default.

## What NBI sends, by provider

The table below describes what each LLM provider receives **when you actively use a feature** (chat message, inline completion, agent action). Idle JupyterLab does not contact the provider.

| Provider                   | What is sent                                                                                            | When                                                 | Destination                                                                           |
| -------------------------- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------- |
| **GitHub Copilot**         | Prompt, surrounding cell source, attached files (when you click "attach")                               | Per request (chat) and as you type (inline-complete) | `api.githubcopilot.com`, `api.github.com` (auth)                                      |
| **Anthropic / Claude API** | Prompt, surrounding cell source, attached files, tool-call results from agent mode                      | Per request and per agent step                       | `api.anthropic.com` (or your configured Base URL)                                     |
| **Claude Code (CLI)**      | Prompt, working-directory file reads requested by Claude, shell-command output for tools Claude invokes | Per agent turn                                       | Whatever the Claude Code CLI is configured to talk to (typically `api.anthropic.com`) |
| **OpenAI-compatible**      | Prompt, surrounding cell source, attached files                                                         | Per request and inline-complete                      | The Base URL you configured (`api.openai.com` by default)                             |
| **LiteLLM-compatible**     | Same as OpenAI-compatible; LiteLLM proxy forwards to the upstream model you configured                  | Per request                                          | The Base URL of your LiteLLM proxy                                                    |
| **Ollama (local)**         | Prompt, surrounding cell source, attached files                                                         | Per request                                          | Localhost (or the host you configured); **no external network**                       |

### Cell outputs are included when the cell is attached

NBI does **not** automatically include rendered cell outputs in every prompt. Outputs are sent only when:

- You attach a notebook or cell explicitly via the "attach files" UI.
- The active context references a notebook and the agent (or inline chat) reads its source — the source view in `.ipynb` JSON includes any saved outputs in the file.

If your cells contain sensitive outputs (PHI, PII, secrets), clear them before invoking AI features, or use a local-only provider (Ollama). Inline completion is keystroke-driven and only sends the cell source; it does not transmit unrelated cells or outputs.

## Egress allowlist

Hosts NBI may contact, depending on which features are enabled:

| Host                                            | Purpose                                                                                                    |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `api.githubcopilot.com`                         | GitHub Copilot chat and inline completion                                                                  |
| `api.github.com`                                | GitHub Copilot device-flow login; managed-skills manifest fetches when hosted on github.com; skill imports |
| `github.com` / `codeload.github.com`            | Skill tarball downloads (`Import from GitHub` and managed-skills reconciler)                               |
| `raw.githubusercontent.com`                     | Manifest fetches when `NBI_SKILLS_MANIFEST` points at a `raw.githubusercontent.com` URL                    |
| `api.anthropic.com`                             | Claude API and Claude Code (default)                                                                       |
| `api.openai.com`                                | OpenAI-compatible provider (default Base URL)                                                              |
| Your configured Base URL                        | OpenAI-compatible / LiteLLM-compatible / Claude when redirected to a self-hosted endpoint                  |
| `localhost:11434` (or your Ollama host)         | Ollama local model serving                                                                                 |
| `registry.npmjs.org` and configured npm mirrors | Only if MCP servers are configured to launch via `npx -y` — `npx` fetches the package on first run         |

For the configurable destinations above (Base URLs, Ollama host, MCP `npx` packages), the destination is whatever you or your admin set. There is no other implicit network activity.

For air-gapped or egress-restricted environments, see [`docs/admin-guide.md`](docs/admin-guide.md#air-gap-deployment).

## Data NBI stores locally

| Path                            | Contents                                                                   |
| ------------------------------- | -------------------------------------------------------------------------- |
| `~/.jupyter/nbi/config.json`    | Provider selection, model choices, API keys (plaintext), MCP server config |
| `~/.jupyter/nbi/user-data.json` | Encrypted GitHub Copilot token (when "remember login" is enabled)          |
| `~/.jupyter/nbi/rules/`         | Your ruleset markdown files                                                |
| `~/.jupyter/nbi/mcp.json`       | MCP server config (if you used the file-based config)                      |
| `~/.claude/skills/`             | User-scope Claude skills                                                   |
| `<project>/.claude/skills/`     | Project-scope Claude skills                                                |
| `~/.claude/projects/`           | Claude Code session transcripts (managed by Claude CLI, not NBI)           |

> Treat `~/.jupyter/nbi/config.json` and `~/.jupyter/nbi/user-data.json` as secrets. They contain your API keys and (encrypted) GitHub token. Do not commit them to git, share them, or sync them across users. If a key leaks, rotate it at the provider immediately.

The encrypted GitHub token uses a default password (`nbi-access-token-password`) unless you set `NBI_GH_ACCESS_TOKEN_PASSWORD`. The default is **shared across installs** and provides obfuscation, not real protection. Set a custom password before enabling "remember login" on any shared or multi-tenant system.

## Telemetry

NBI does not collect telemetry, send analytics, or report usage.

The `enable_chat_feedback` traitlet (off by default) emits an internal `telemetry` event when a user gives thumbs-up/down feedback in chat. The event is **emitted in-process only** — nothing leaves the pod unless you write a custom handler that listens for it. See [`docs/admin-guide.md`](docs/admin-guide.md#chat-feedback-event-hook).

## Reproducibility caveat

LLM outputs are non-deterministic. Pinning the model name, temperature, and seed does **not** guarantee identical output across runs — provider-side updates, load-balancing, and silent model deprecation can all shift behavior. Treat AI-generated code as a draft to be reviewed, tested, and committed like any other contribution. For research artifacts that need reproducibility, save the exact prompt, model name, and date alongside the generated output.

## Privacy-sensitive deployment recipes

For HIPAA, FedRAMP, classroom, or otherwise restricted environments:

- **Force local-only models.** Disable every cloud provider via `disabled_providers` and use Ollama. See the [HIPAA / sensitive-data preset](docs/admin-guide.md#hipaa--sensitive-data-preset) in the admin guide.
- **Restrict skill imports.** Block egress to `github.com` and serve managed skills from an internal manifest URL.
- **Disable "remember GitHub Copilot login"** for shared systems where users share home directories.
- **Pre-pull MCP servers** rather than allowing `npx -y` (which downloads from npmjs).

## Reporting privacy issues

Email `mbektasgh@outlook.com` with details. Privacy concerns are treated like security issues — see [SECURITY.md](SECURITY.md).
