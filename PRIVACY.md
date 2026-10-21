# Privacy and Data Flow

This page documents what NBI sends to external services, when, and how administrators can restrict it. NBI is a per-user tool that runs inside your Jupyter Server process — it has no central server of its own and collects no telemetry by default.

## What NBI sends, by provider

The table below describes what each LLM provider receives **when you actively use a feature** (chat message, inline completion, agent action). An idle JupyterLab does not contact the provider.

| Provider                          | What is sent                                                                                                     | When                                                          | Destination                                                                                                                               |
| --------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **GitHub Copilot**                | Prompt, surrounding cell source, attached files (when you click _attach_)                                        | Per request (chat) and as you type (inline complete)          | `api.githubcopilot.com`, `api.github.com` (auth)                                                                                          |
| **OpenAI-compatible**             | Prompt, surrounding cell source, attached files                                                                  | Per request and per inline-completion request                 | The Base URL you configured (`api.openai.com` by default)                                                                                 |
| **LiteLLM-compatible**            | Same as OpenAI-compatible; the LiteLLM proxy forwards to the upstream model you configured                       | Per request                                                   | The Base URL of your LiteLLM proxy                                                                                                        |
| **Ollama (local)**                | Prompt, surrounding cell source, attached files                                                                  | Per request                                                   | Localhost (or the host you configured); **no external network**                                                                           |
| **Anthropic API** (Claude mode)   | Prompt, surrounding cell source, attached files                                                                  | Per inline-chat or auto-complete request                      | `api.anthropic.com` (or your configured Base URL)                                                                                         |
| **Claude Code CLI** (Claude mode) | Prompt, working-directory file reads requested by Claude, shell-command output for tools Claude invokes          | Per agent turn in the chat panel                              | Whatever the Claude Code CLI is configured to talk to (typically `api.anthropic.com`)                                                     |
| **ACP agent** (ACP mode)          | Prompt and NBI's notebook context, plus whatever files the agent reads and command output it collects on its own | Per agent turn in the chat panel, and per Chatbook generation | Whatever the agent is configured to talk to (`api.openai.com` or your Base URL on the API-key path, or the agent's own signed-in backend) |

### Cell outputs are included when the cell is attached

NBI does **not** automatically include rendered cell outputs in every prompt. Outputs go out only when:

- You attach a notebook or cell explicitly via the _attach files_ UI.
- The active context references a notebook and the agent (or inline chat) reads its source — the `.ipynb` JSON includes any saved outputs.

If your cells contain sensitive outputs (PHI, PII, secrets), clear them before invoking AI features, or use a local-only provider (Ollama). Inline completion is keystroke-driven and sends only the cell source; it does not transmit unrelated cells or outputs.

## Egress allowlist

Hosts NBI may contact, depending on which features are enabled:

| Host                                            | Purpose                                                                                                          |
| ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `api.githubcopilot.com`                         | GitHub Copilot chat and inline completion                                                                        |
| `api.github.com`                                | GitHub Copilot device-flow login; managed-skills manifest fetches when hosted on github.com; skill imports       |
| `github.com`, `codeload.github.com`             | Skill tarball downloads (Import from GitHub and the managed-skills reconciler)                                   |
| `raw.githubusercontent.com`                     | Manifest fetches when `NBI_SKILLS_MANIFEST` points at a `raw.githubusercontent.com` URL                          |
| `api.anthropic.com`                             | Anthropic API for Claude-mode inline chat and auto-complete; also the default destination of the Claude Code CLI |
| `api.openai.com`                                | OpenAI-compatible provider (default Base URL)                                                                    |
| Your configured Base URL                        | OpenAI-compatible, LiteLLM-compatible, or Claude when pointed at a self-hosted endpoint                          |
| `localhost:11434` (or your Ollama host)         | Ollama local model serving                                                                                       |
| `registry.npmjs.org` and configured npm mirrors | MCP servers configured to launch via `npx -y`                                                                    |

For the configurable destinations above (Base URLs, Ollama host, MCP `npx` packages), the destination is whatever you or your admin set. NBI itself starts no other network activity. ACP uses a preinstalled adapter; NBI does not fetch it from npm at startup. In ACP mode the agent subprocess makes its own calls, which NBI neither proxies nor enumerates: the destinations there are the agent's to document, and blocking egress at the network layer is the only way to bound them from outside.

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
| `~/.jupyter/nbi/codex-home/`    | ACP agent configuration and session files, when an API key is configured   |

> Treat `~/.jupyter/nbi/config.json` and `~/.jupyter/nbi/user-data.json` as secrets. They contain your API keys and (encrypted) GitHub token. Do not commit them to git, share them, or sync them across users. If a key leaks, rotate it at the provider immediately.

The encrypted GitHub token uses a default password (`nbi-access-token-password`) unless you set `NBI_GH_ACCESS_TOKEN_PASSWORD`. The default is **shared across installs** and provides obfuscation, not real protection. Set a custom password before enabling "remember login" on any shared or multi-tenant system. NBI logs a per-process WARNING when the default is in use and escalates the message when `~/.jupyter/nbi/` is group/other-accessible. Operators on shared filesystems can set `NBI_REFUSE_DEFAULT_TOKEN_PASSWORD_ON_SHARED_FS=1` to refuse the write entirely until a per-user password is configured, with `NBI_ALLOW_DEFAULT_TOKEN_PASSWORD=1` available as an explicit per-pod opt-out during a rollout.

## Telemetry

NBI has no central server and sends no telemetry, analytics, or usage data to any external service.

Internally, NBI emits `telemetry` events that signal feature usage: chat, inline completion, and cell inline chat lifecycle events (including when a suggestion is accepted or dismissed), plus thumbs-up/down chat feedback when `enable_chat_feedback` is on. These events are **emitted in-process only**. By default nothing listens for them, so they go nowhere and never leave the process; an administrator can register a listener to collect them. See [`docs/admin-guide.md`](docs/admin-guide.md#telemetry-events).

## Reproducibility caveat

LLM outputs are non-deterministic. Pinning the model name, temperature, and seed does **not** guarantee identical output across runs — provider-side updates, load balancing, and silent model deprecation can all shift behavior. Treat AI-generated code as a draft to be reviewed, tested, and committed like any other contribution. For research artifacts that need reproducibility, save the exact prompt, model name, and date alongside the generated output.

## Privacy-sensitive deployment recipes

For HIPAA, FedRAMP, classroom, or otherwise restricted environments:

- **Force local-only models.** Disable every cloud provider via `disabled_providers`, force both agent modes off with `claude_mode_policy` and `acp_mode_policy`, and use Ollama. Disabling providers is not sufficient on its own: neither agent mode is a provider, and Claude mode is available to users unless an admin turns it off. See the [HIPAA / sensitive-data preset](docs/admin-guide.md#hipaa--sensitive-data-preset) in the admin guide.
- **Restrict skill imports.** Block egress to `github.com` and serve managed skills from an internal manifest URL.
- **Disable "remember GitHub Copilot login"** for shared systems where users share home directories.
- **Pre-pull MCP servers** rather than allowing `npx -y` (which downloads from npmjs).

## Reporting privacy issues

Email `mbektasgh@outlook.com` with details. Privacy concerns are treated like security issues — see [SECURITY.md](SECURITY.md).
