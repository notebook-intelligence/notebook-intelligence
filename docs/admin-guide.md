# Administrator Guide

This guide covers deploying Notebook Intelligence at scale — JupyterHub, KubeSpawner, Kubeflow, multi-tenant clusters, regulated environments. For end-user documentation, see the [README](../README.md).

> NBI is a per-user tool. Every section below assumes the extension runs inside a per-user Jupyter Server, not a shared one. Server-side state is per-user; there is no central NBI service.

---

## Table of contents

- [Install layout and config precedence](#install-layout-and-config-precedence)
- [Persistent-volume layout](#persistent-volume-layout)
- [Shared filesystem and multi-user notes](#shared-filesystem-and-multi-user-notes)
- [Environment variables and traitlets](#environment-variables-and-traitlets)
- [Security model](#security-model)
- [API-key handling](#api-key-handling)
- [Self-hosted LLM endpoints](#self-hosted-llm-endpoints)
- [Custom CA certs and corporate proxies](#custom-ca-certs-and-corporate-proxies)
- [Air-gap deployment](#air-gap-deployment)
- [HIPAA / sensitive-data preset](#hipaa--sensitive-data-preset)
- [Restricting features for managed deployments](#restricting-features-for-managed-deployments)
- [Multi-tenancy and per-team scoping](#multi-tenancy-and-per-team-scoping)
- [Managed Claude Skills token](#managed-claude-skills-token)
- [Telemetry events](#telemetry-events)
- [HTTP API surface](#http-api-surface)
- [Failure modes](#failure-modes)
- [Version matrix](#version-matrix)
- [FIPS posture](#fips-posture)
- [Resource footprint](#resource-footprint)

---

## Install layout and config precedence

NBI reads configuration from three layers, listed in order of precedence (later wins):

1. **Environment-wide base config** — `<env-prefix>/share/jupyter/nbi/config.json` and `<env-prefix>/share/jupyter/nbi/mcp.json`. Bake into your image. Read once at startup.
2. **User config** — `~/.jupyter/nbi/config.json` and `~/.jupyter/nbi/mcp.json`. The user mutates these via the Settings dialog. Lives on the per-user PVC.
3. **Environment variables** — `NBI_*` and certain provider variables (see the [reference table](#environment-variables-and-traitlets)). Override at pod startup time.

Traitlets configured via JupyterLab CLI flags or `jupyter_server_config.py` (e.g., `c.NotebookIntelligence.disabled_providers = [...]`) are evaluated at server startup. Most env-var overrides (`NBI_*_POLICY`, `NBI_ALLOW_GITHUB_*`, `NBI_*_MANAGEMENT_POLICY`, etc.) are also resolved once at startup and cached on the handler classes — flipping them requires a JupyterLab restart. The `NBI_ENABLED_PROVIDERS` and `NBI_ENABLED_BUILTIN_TOOLS` re-enable env vars (gated by `allow_enabling_*_with_env`) are the exception: those are read on every request.

Manual edits to `config.json` while JupyterLab is running require a JupyterLab restart to take effect. Edits via the Settings dialog are picked up live.

---

## Persistent-volume layout

| Path                                      | Persist?    | Notes                                                                                                                                                                                                                                               |
| ----------------------------------------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `~/.jupyter/nbi/config.json`              | Yes         | User's chosen provider, models, MCP servers, plus plaintext API keys. Treat as a secret.                                                                                                                                                            |
| `~/.jupyter/nbi/user-data.json`           | Yes         | Encrypted GitHub Copilot access token, written when "remember login" is enabled. Encrypted with `NBI_GH_ACCESS_TOKEN_PASSWORD`.                                                                                                                     |
| `~/.jupyter/nbi/rules/`                   | Yes         | User's ruleset markdown files.                                                                                                                                                                                                                      |
| `~/.jupyter/nbi/mcp.json`                 | Yes         | User's MCP server config (alternative to managing via the Settings dialog).                                                                                                                                                                         |
| `~/.claude/skills/`                       | Yes         | User-scope Claude skills (including managed skills).                                                                                                                                                                                                |
| `~/.claude/projects/`                     | Yes         | Claude Code session transcripts. Required for "Resume previous Claude session". Managed by Claude CLI, not NBI. When `CLAUDE_CONFIG_DIR` is set, this (and `~/.claude/skills/`) lives under `$CLAUDE_CONFIG_DIR` instead; NBI follows the override. |
| `<env-prefix>/share/jupyter/nbi/`         | No (image)  | Org-wide base config. Bake into your container image.                                                                                                                                                                                               |
| Project-scope `<project>/.claude/skills/` | Per project | Lives in the user's working directory. Persists if the working directory does.                                                                                                                                                                      |

For Kubeflow or KubeSpawner: mount the user's home directory on a PVC and ensure `~/.jupyter` and `~/.claude` are inside that mount (or `$CLAUDE_CONFIG_DIR`, if you point the Claude CLI elsewhere). Anything else (`/tmp`, `~/.cache`) can be ephemeral.

---

## Shared filesystem and multi-user notes

If users share a home directory across nodes (NFS-backed shared HPC, classroom labs):

- **Race conditions in `~/.jupyter/nbi/`.** Concurrent writes from two login nodes can corrupt `config.json`. NBI does not file-lock. Pin each user to one node, or use a per-node config prefix.
- **`NBI_GH_ACCESS_TOKEN_PASSWORD` default is unsafe on shared hosts.** The default password (`nbi-access-token-password`) is shared across installs. On a multi-tenant cluster, anyone with read access to another user's `~/.jupyter/nbi/user-data.json` can decrypt their Copilot token. NBI now logs a per-process WARNING on the first read or write of the stored token when the default password is in use, escalated when `~/.jupyter/nbi/` is readable by group or other. Set `NBI_REFUSE_DEFAULT_TOKEN_PASSWORD_ON_SHARED_FS=1` to upgrade the warning to a hard refusal of the write; admins who knowingly accept the risk can opt out per pod with `NBI_ALLOW_DEFAULT_TOKEN_PASSWORD=1`. The hardening is opt-in to preserve backwards compatibility for single-user deployments where the directory mode is incidental. Set a per-user password (e.g., derived from the Hub user secret), or disable "remember login" entirely (see [Restricting features](#restricting-features-for-managed-deployments)).
- **Skill collisions.** Two users sharing `~/.claude/skills/` will see each other's skills. Make sure each user has a unique home.

---

## Environment variables and traitlets

The full surface, in one table.

| Name                                             | Type | Default                     | Source                             | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| ------------------------------------------------ | ---- | --------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `disabled_providers`                             | List | `[]`                        | traitlet on `NotebookIntelligence` | Hide providers from the user dropdown. Values: `github-copilot`, `ollama`, `litellm-compatible`, `openai-compatible`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `allow_enabling_providers_with_env`              | Bool | `False`                     | traitlet                           | If true, `NBI_ENABLED_PROVIDERS` re-enables hidden providers per pod.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `NBI_ENABLED_PROVIDERS`                          | csv  | unset                       | env                                | Comma-separated provider IDs to re-enable. Effective only when `allow_enabling_providers_with_env=True`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `disabled_tools`                                 | List | `[]`                        | traitlet                           | Hide built-in tools from agent mode. Values listed in [Restricting features](#restricting-features-for-managed-deployments).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `allow_enabling_tools_with_env`                  | Bool | `False`                     | traitlet                           | If true, `NBI_ENABLED_BUILTIN_TOOLS` re-enables hidden tools per pod.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `NBI_ENABLED_BUILTIN_TOOLS`                      | csv  | unset                       | env                                | Comma-separated tool IDs to re-enable. Effective only when `allow_enabling_tools_with_env=True`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `disabled_coding_agent_launchers`                | List | `[]`                        | traitlet                           | Hide JupyterLab launcher tiles for coding-agent CLIs even when the CLI is on `PATH`. Valid IDs: `claude-code`, `opencode`, `pi`, `github-copilot-cli`, `codex`. See [Disabling coding-agent launcher tiles](#disabling-coding-agent-launcher-tiles).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `allow_enabling_coding_agent_launchers_with_env` | Bool | `False`                     | traitlet                           | If true, `NBI_ENABLED_CODING_AGENT_LAUNCHERS` re-enables hidden tiles per pod.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `NBI_ENABLED_CODING_AGENT_LAUNCHERS`             | csv  | unset                       | env                                | Comma-separated launcher IDs to re-enable. Effective only when `allow_enabling_coding_agent_launchers_with_env=True`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `enable_chat_feedback`                           | Bool | `False`                     | traitlet                           | Enables thumbs-up/down UI in chat and emits in-process `telemetry` events.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `enable_chat_feedback_always_visible`            | Bool | `False`                     | traitlet                           | Renders thumbs-up/down buttons at full opacity on every assistant reply instead of revealing them only on hover. Requires `enable_chat_feedback=True`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `additional_skipped_workspace_directories`       | List | `[]`                        | traitlet                           | Extra directory names to skip in the chat-sidebar @-mention workspace file picker. Merged with the built-in skips (`__pycache__`, `node_modules`). Match is by directory name only, case-sensitive.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES`   | csv  | unset                       | env (appends to traitlet)          | Comma-separated extra directory names. Resolved at server startup and concatenated with the traitlet value, so a spawn profile can add to (rather than replace) the org-wide list.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `allow_github_skill_import`                      | Bool | `True`                      | traitlet                           | When `False`, hides the **Import from GitHub** button in the Skills panel and rejects `/skills/import` POSTs with 403. Does not affect the managed-skills reconciler.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `NBI_ALLOW_GITHUB_SKILL_IMPORT`                  | bool | unset                       | env (overrides traitlet)           | Per-pod override for `allow_github_skill_import`. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off` (case-insensitive). Useful for varying the policy across spawn profiles.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `skills_manifest`                                | str  | `""`                        | traitlet                           | URL or filesystem path to a managed-skills manifest, or a comma-separated list of either. Manifests are unioned with first-wins URL dedupe; name collisions surface as per-entry errors. See [`docs/skills.md`](skills.md#managed-skills-via-an-org-manifest).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `NBI_SKILLS_MANIFEST`                            | str  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `skills_manifest_interval`                       | int  | `86400`                     | traitlet                           | Seconds between reconciles.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `NBI_SKILLS_MANIFEST_INTERVAL`                   | int  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `managed_skills_token`                           | str  | `""`                        | traitlet                           | Bearer token for managed-skills GitHub fetches.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `NBI_MANAGED_SKILLS_TOKEN`                       | str  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `allow_github_plugin_import`                     | Bool | `True`                      | traitlet                           | When `False`, hides the "From GitHub" affordance in the Plugins panel and rejects `claude plugin marketplace add` requests whose source resolves as a GitHub URL or `owner/repo` shorthand. Local-path and arbitrary-URL sources remain available.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `NBI_ALLOW_GITHUB_PLUGIN_IMPORT`                 | bool | unset                       | env (overrides traitlet)           | Per-pod override for `allow_github_plugin_import`. Accepts `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off` (case-insensitive).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `skill_max_archive_mb`                           | Int  | `100`                       | traitlet                           | Per-archive on-wire size cap (megabytes) for skill bundles fetched from GitHub. Applies to both user imports and managed-skills tarballs. `0` disables the cap.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `NBI_SKILL_MAX_ARCHIVE_MB`                       | int  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `upload_max_mb`                                  | Int  | `50`                        | traitlet                           | Per-file size cap (megabytes) for the shared upload endpoint used by chat-sidebar attachments and terminal drag-drop. Requests over the cap return HTTP 413. `0` disables the cap.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `NBI_UPLOAD_MAX_MB`                              | int  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `upload_retention_hours`                         | Int  | `24`                        | traitlet                           | How long staged uploads survive in the temp directory before the next upload sweeps them. `0` keeps only the atexit purge (uploads survive the session).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `NBI_UPLOAD_RETENTION_HOURS`                     | int  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `mcp_stdio_command_allowlist`                    | List | `[]`                        | traitlet                           | Regex allowlist for the stdio MCP server `command` field. Empty list (default) means no enforcement; non-empty list rejects any stdio MCP server whose command does not match. Matched with `re.search`; anchor (`^...$`) for literal equality. Applies at both Claude `mcp add` and `mcp.json` load. See [Restricting MCP stdio commands](#restricting-mcp-stdio-commands).                                                                                                                                                                                                                                                                                                                                                                      |
| `NBI_MCP_STDIO_COMMAND_ALLOWLIST`                | csv  | unset                       | env (appends to traitlet)          | Comma-separated regex patterns added to the traitlet at startup. Per-pod additions on an org baseline.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `tour_config_path`                               | str  | `""`                        | traitlet                           | Filesystem path to a YAML/JSON file with admin overrides for the first-run sidebar tour copy. See [`docs/admin-tour-config.md`](admin-tour-config.md).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `NBI_TOUR_CONFIG_PATH`                           | str  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `NBI_GH_ACCESS_TOKEN_PASSWORD`                   | str  | `nbi-access-token-password` | env                                | Password used to encrypt the stored Copilot token in `user-data.json`. **Change in multi-tenant deployments.**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `NBI_REFUSE_DEFAULT_TOKEN_PASSWORD_ON_SHARED_FS` | bool | unset                       | env                                | When set, refuse to write `user-data.json` if the default `NBI_GH_ACCESS_TOKEN_PASSWORD` is still in use AND `~/.jupyter/nbi/` is readable by group or other. Opt-in to preserve backwards compatibility on single-user deployments where the directory mode is incidental.                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `NBI_ALLOW_DEFAULT_TOKEN_PASSWORD`               | bool | unset                       | env                                | Per-pod opt-out that disengages the refuse-on-shared-fs guard above. Admins who knowingly accept the risk (e.g., during a transition before rolling out a per-user password) set this so writes continue.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `NBI_RULES_AUTO_RELOAD`                          | bool | `true`                      | env                                | When `false`, ruleset edits require a JupyterLab restart to take effect.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `NBI_CLAUDE_CLI_PATH`                            | str  | unset                       | env                                | Absolute path to the Claude Code CLI binary. When unset, NBI looks up `claude` on `PATH`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `NBI_OPENCODE_CLI_PATH`                          | str  | unset                       | env                                | Absolute path to the opencode CLI. When unset, NBI looks up `opencode` on `PATH`. Gates the opencode launcher tile.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `NBI_PI_CLI_PATH`                                | str  | unset                       | env                                | Absolute path to the Pi CLI. When unset, NBI looks up `pi` on `PATH`. Gates the Pi launcher tile.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `NBI_GITHUB_COPILOT_CLI_PATH`                    | str  | unset                       | env                                | Absolute path to the GitHub Copilot CLI. When unset, NBI looks up `copilot` on `PATH`. Gates the GitHub Copilot launcher tile.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `NBI_CODEX_CLI_PATH`                             | str  | unset                       | env                                | Absolute path to the OpenAI Codex CLI. When unset, NBI looks up `codex` on `PATH`. Gates the Codex launcher tile.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `NBI_GHE_SUBDOMAIN`                              | str  | `""`                        | env                                | GitHub Enterprise subdomain for GitHub Copilot users on a GHE tenant. Empty selects github.com.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `NBI_GITHUB_ENTERPRISE_HOSTS`                    | csv  | `""`                        | env                                | Comma-separated hostnames the plugin marketplace detector treats as GitHub. Cookie-domain shape: bare token (`github.acme.com`) matches exactly; leading-dot token (`.acme.com`) matches any subdomain of `acme.com`. Independent of `NBI_GHE_SUBDOMAIN`, which only configures the Copilot OAuth tenant. Required so `allow_github_plugin_import = False` actually gates GHE marketplace adds and so the `GITHUB_TOKEN` / `gh auth token` chain injects on GHE sources.                                                                                                                                                                                                                                                                          |
| `NBI_LOG_LEVEL`                                  | str  | `INFO`                      | env                                | Python logging level for the `notebook_intelligence` logger.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `LITELLM_LOCAL_MODEL_COST_MAP`                   | bool | `true` (NBI default)        | env                                | litellm setting that NBI defaults to `true` when it loads litellm, so litellm reads the model-cost map bundled with the installed package instead of fetching it over HTTP (litellm's own default), which stalls on proxied networks. Set to `false` before starting JupyterLab to restore litellm's remote fetch.                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `NBI_DISABLE_OUTPUT_SCRUB`                       | bool | unset                       | env                                | When set (`1` / `true` / `yes` / `on`), disables the shell-tool output scrubber so raw stdout/stderr (including any env-var values that leak) is sent through to chat. Default off; the scrubber redacts values for sensitive-named env vars (`TOKEN`, `SECRET`, `API_KEY`, ...) plus tokens with well-known credential prefixes (`ghp_`, `sk-ant-`, `AKIA`, ...). Opt out only when debugging credential helpers where the redaction interferes.                                                                                                                                                                                                                                                                                                 |
| `GITHUB_TOKEN`, `GH_TOKEN`                       | str  | unset                       | env                                | Used (in that order) by user-initiated skill imports and GitHub-sourced plugin marketplace adds for GitHub auth. Falls back to `gh` CLI auth.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `NBI_*_POLICY`                                   | str  | `user-choice`               | env                                | Lock individual Settings panel toggles. See [README → Admin policies](../README.md#admin-policies) for the full list of `*_POLICY` env vars and matching traitlets, including `NBI_SKILLS_MANAGEMENT_POLICY`, `NBI_CLAUDE_MCP_MANAGEMENT_POLICY`, `NBI_CLAUDE_PLUGINS_MANAGEMENT_POLICY`, `NBI_TERMINAL_DRAG_DROP_POLICY`, and `NBI_REFRESH_OPEN_FILES_ON_DISK_CHANGE_POLICY`. Three exceptions to the `user-choice` default all default to `force-off`: `NBI_CLAUDE_BYPASS_PERMISSIONS_POLICY` (see [Allowing Bypass Permissions](#allowing-bypass-permissions-in-the-claude-permission-mode-selector)), `NBI_ACP_MODE_POLICY` (the experimental ACP agent mode), and `NBI_ACP_FULL_ACCESS_POLICY` (the ACP agent running tools without asking). |

Configure traitlets in `jupyter_server_config.py`:

```python
c.NotebookIntelligence.disabled_providers = ["openai-compatible", "litellm-compatible"]
c.NotebookIntelligence.allow_enabling_providers_with_env = True
c.NotebookIntelligence.disabled_tools = ["nbi-command-execute"]
c.NotebookIntelligence.skills_manifest = "https://internal.example.com/manifests/data-science-team.yaml"
```

---

## Security model

NBI runs entirely inside the user's Jupyter Server process. There is no privilege boundary between NBI and the user. In particular:

- **Built-in tools execute as the user.**
  - `nbi-command-execute` runs arbitrary shell commands.
  - `nbi-file-edit` and `nbi-file-read` read and write any file the user can.
  - `nbi-notebook-edit` and `nbi-notebook-execute` modify and run notebooks.
- **MCP stdio servers** are launched as user subprocesses with the user's environment. NBI does not sandbox them; the optional [`mcp_stdio_command_allowlist`](#restricting-mcp-stdio-commands) gates the binary name and refuses `PATH`/`LD_PRELOAD` style env overrides, but does not validate `args` and does not contain the spawned process.
- **Claude Code CLI** inherits the user's environment, including filesystem permissions and any auth tokens in `~/.claude/`.

For regulated tenants:

1. Disable the most powerful tools — at minimum `nbi-command-execute` and `nbi-file-edit`. See [Restricting features](#restricting-features-for-managed-deployments).
2. Restrict the providers the user can pick. Force a single self-hosted endpoint with `disabled_providers` plus the org's base config.
3. Disable user-initiated skill imports with `allow_github_skill_import = False` (env `NBI_ALLOW_GITHUB_SKILL_IMPORT=false`), and plugin marketplace adds from GitHub with `allow_github_plugin_import = False` (env `NBI_ALLOW_GITHUB_PLUGIN_IMPORT=false`). Reinforce at the network layer where stronger isolation is required. See [`skills.md`](skills.md#disabling-user-initiated-github-imports) and the [Plugins tab section](#disabling-the-plugins-tab) below.
4. Run with a non-root container user, with no host-network access and no host-path mounts beyond the user's PVC.

---

## API-key handling

By default, custom-provider API keys (Anthropic, OpenAI-compatible, LiteLLM-compatible) are stored plaintext in `~/.jupyter/nbi/config.json`. This is acceptable for single-tenant developer workstations and unacceptable for multi-tenant clusters.

Recommended approach for clusters:

- **Inject the org's keys via env vars at pod startup.** Set the provider's expected env var (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, etc.) on the pod. Configure the provider in `<env-prefix>/share/jupyter/nbi/config.json` without a key — NBI picks up the provider's standard env var.
- **Source secrets from your secret manager.** Vault, External Secrets Operator, AWS Secrets Manager, GCP Secret Manager, or KubeSpawner's `c.KubeSpawner.environment` callback can all populate the pod env from a secret backend at spawn time.
- **Don't commit `config.json`.** Even the env-prefix base config should not contain keys; pull keys from env at spawn.

`${ENV_VAR}`-style interpolation inside `config.json` is not currently supported. Tracked as a feature request.

---

## Self-hosted LLM endpoints

NBI's `openai-compatible` and `litellm-compatible` providers can target any endpoint that speaks the respective wire format.

**Azure OpenAI** (via the `openai-compatible` provider):

```json
{
  "providers": {
    "openai-compatible": {
      "base_url": "https://my-resource.openai.azure.com/openai/deployments/gpt-4-deployment",
      "api_key": "${AZURE_OPENAI_KEY}",
      "default_chat_model": "gpt-4",
      "default_inline_completion_model": "gpt-4"
    }
  }
}
```

**vLLM, TGI, or any local OpenAI-compatible server**:

```json
{
  "providers": {
    "openai-compatible": {
      "base_url": "http://internal-vllm.example.com:8000/v1",
      "api_key": "any-string-the-server-accepts",
      "default_chat_model": "meta-llama/Meta-Llama-3-70B-Instruct"
    }
  }
}
```

**LiteLLM proxy** (so you can route to many upstream models from one place, including Bedrock, Vertex, etc.):

```json
{
  "providers": {
    "litellm-compatible": {
      "base_url": "https://litellm.internal.example.com",
      "api_key": "${LITELLM_TOKEN}"
    }
  }
}
```

Bake the base config into your image and let users select their model from the dropdown.

---

## Custom CA certs and corporate proxies

NBI's HTTP requests use Python's `requests` and `httpx`, plus the `litellm`, `openai`, and `anthropic` SDKs. All honor standard Python TLS and proxy environment variables:

```bash
REQUESTS_CA_BUNDLE=/etc/pki/tls/certs/ca-bundle.crt
SSL_CERT_FILE=/etc/pki/tls/certs/ca-bundle.crt
HTTPS_PROXY=http://corp-proxy.example.com:3128
HTTP_PROXY=http://corp-proxy.example.com:3128
NO_PROXY=localhost,127.0.0.1,.cluster.local
```

Set these on the pod (`c.KubeSpawner.environment` for Hub; environment in your Dockerfile or compose file otherwise). The Claude Code CLI is a separate Node.js process and reads the same `HTTPS_PROXY` and `NODE_EXTRA_CA_CERTS` conventions.

The frontend talks only to its own Jupyter Server backend, which proxies the LLM calls. The browser does not need a corporate CA trust.

---

## Air-gap deployment

Steps for deploying to a network with no general internet egress:

1. **Pre-build the Docker image** with NBI installed, the Claude Code CLI binary baked in, and any MCP server packages pre-installed (do **not** rely on `npx -y` at runtime).
2. **Manifest hosting.** Set `NBI_SKILLS_MANIFEST` to either a `file://` path (a manifest baked into the image) or an internal `https://` URL on a network the pod can reach.
3. **Skill bundles.** Either bake the skills into the image under `~/.claude/skills/` (managed status will reset since they aren't from the manifest), or host the GitHub-style tarballs at an internal mirror and write the manifest URLs to point at it.
4. **Disable user-initiated GitHub imports** at the network layer — block `github.com`, `codeload.github.com`, and `raw.githubusercontent.com`. Users can still install skills from the local filesystem by dropping bundles into `~/.claude/skills/`.
5. **MCP `npx -y` is incompatible with air-gap.** Pre-install the server binary and reference it directly:

   ```json
   {
     "mcpServers": {
       "filesystem": {
         "command": "/opt/mcp/bin/mcp-server-filesystem",
         "args": ["/home/user/work"]
       }
     }
   }
   ```

6. **LLM endpoint.** Air-gap requires a self-hosted endpoint (vLLM, TGI, or a LiteLLM proxy in front of a VPC-endpoint Bedrock, etc.). See [Self-hosted LLM endpoints](#self-hosted-llm-endpoints).

---

## HIPAA / sensitive-data preset

For deployments that must not transmit PHI to cloud LLM providers, force local-only models:

```python
# jupyter_server_config.py
c.NotebookIntelligence.disabled_providers = [
    "github-copilot",
    "openai-compatible",
    "litellm-compatible",
]
c.NotebookIntelligence.allow_enabling_providers_with_env = False  # users cannot override
c.NotebookIntelligence.disabled_tools = ["nbi-command-execute", "nbi-file-edit"]
c.NotebookIntelligence.allow_enabling_tools_with_env = False
```

Pair with `<env-prefix>/share/jupyter/nbi/config.json` shipping an Ollama provider preconfigured against your local model:

```json
{
  "default_provider": "ollama",
  "providers": {
    "ollama": {
      "base_url": "http://ollama-internal.example.com:11434",
      "default_chat_model": "llama3:70b",
      "default_inline_completion_model": "codellama:7b"
    }
  }
}
```

Block egress to all external LLM hosts at the network layer as defense in depth (see [`PRIVACY.md`](../PRIVACY.md#egress-allowlist) for the full list).

This is a **starting point**, not a HIPAA compliance certification. Run a security review of the full stack (Ollama, your Jupyter image, KubeSpawner, network policy) before treating any data as protected.

---

## Restricting features for managed deployments

NBI's denylist for providers and tools follows the same shape:

### Disabling LLM providers

```python
c.NotebookIntelligence.disabled_providers = ["ollama", "litellm-compatible", "openai-compatible"]
```

Valid IDs: `github-copilot`, `ollama`, `litellm-compatible`, `openai-compatible`.

To allow per-pod re-enable via env var:

```python
c.NotebookIntelligence.allow_enabling_providers_with_env = True
```

```bash
NBI_ENABLED_PROVIDERS=github-copilot,ollama
```

### Disabling built-in tools

```python
c.NotebookIntelligence.disabled_tools = ["nbi-notebook-execute", "nbi-python-file-edit"]
```

Valid IDs: `nbi-notebook-edit`, `nbi-notebook-execute`, `nbi-python-file-edit`, `nbi-file-edit`, `nbi-file-read`, `nbi-command-execute`.

To allow per-pod re-enable:

```python
c.NotebookIntelligence.allow_enabling_tools_with_env = True
```

```bash
NBI_ENABLED_BUILTIN_TOOLS=nbi-notebook-execute,nbi-python-file-edit
```

NBI does not currently support an explicit allowlist mode (`allowed_providers`, `allowed_tools`). A new built-in provider added in a minor release would auto-enable for users with `disabled_providers=[]`. If this matters for your compliance posture, pin to specific NBI versions and review changelog entries before upgrading. Tracked as a feature request.

### Disabling coding-agent launcher tiles

The JupyterLab launcher shows a tile for each coding-agent CLI on `PATH`: Claude Code, opencode, Pi, GitHub Copilot CLI, and Codex. Tile visibility is gated by CLI presence; to hide a tile even when the CLI is present (for example, to keep users in the chat sidebar's audit path), add its ID to the denylist:

```python
c.NotebookIntelligence.disabled_coding_agent_launchers = ["opencode", "pi", "codex"]
```

Valid IDs: `claude-code`, `opencode`, `pi`, `github-copilot-cli`, `codex`. Unknown IDs raise at server startup so a typo can't silently no-op the policy.

The `github-copilot-cli` ID is deliberately distinct from `github-copilot` (the `disabled_providers` value for the Copilot LLM provider). The tile and the provider are independent surfaces; hiding the tile does not affect chat with the Copilot provider, and vice versa.

To vary the policy per spawn profile, opt into per-pod re-enable:

```python
c.NotebookIntelligence.allow_enabling_coding_agent_launchers_with_env = True
```

```bash
NBI_ENABLED_CODING_AGENT_LAUNCHERS=claude-code,codex
```

The env var has effect **only** when `allow_enabling_coding_agent_launchers_with_env = True`; without that flag, the denylist is final. The merged effective set is computed per request and the frontend re-evaluates tile visibility on each capabilities refresh, so an env-var flip applies after a page reload in the same session. Edits to the `disabled_coding_agent_launchers` traitlet in `jupyter_server_config.py` require a JupyterLab server restart, the same as other traitlet edits.

> **Blast radius.** The denylist hides the launcher tile and removes the matching JupyterLab command-palette entry. The CLI binary remains on `PATH` and remains usable from a manually-opened terminal. To prevent terminal use, restrict the binary at the container-image or `PATH` level. To prevent the _Claude chat mode_ itself (a separate surface from the launcher tile), use `NBI_CLAUDE_MODE_POLICY=force-off` from the Admin policies table. Note that `NBI_CLAUDE_MODE_POLICY=force-off` does **not** imply hiding the Claude Code launcher tile: the tile runs `claude` directly in a terminal and is independent of the chat-mode SDK backend. To hide both surfaces, combine `NBI_CLAUDE_MODE_POLICY=force-off` with `disabled_coding_agent_launchers = ["claude-code"]`.

> **Per-pod re-enable trust model.** Setting `allow_enabling_coding_agent_launchers_with_env = True` delegates the final denylist decision to whatever process sets `NBI_ENABLED_CODING_AGENT_LAUNCHERS` on the pod. If your spawn-profile config is itself user-influenced (profile-form fields, untrusted YAML), keep this flag `False` so the traitlet baseline is authoritative.

### Disabling user-initiated GitHub Skill imports

```python
c.NotebookIntelligence.allow_github_skill_import = False
```

Hides the **Import from GitHub** button in the Skills panel and rejects POSTs to `/notebook-intelligence/skills/import` and `/notebook-intelligence/skills/import/preview` with HTTP 403. This does **not** disable the [managed-skills reconciler](#managed-claude-skills-token); admin-curated skills delivered via `NBI_SKILLS_MANIFEST` continue to install. Use this when you want to allow only org-vetted skills.

To vary the policy per spawn profile, override at pod startup:

```bash
NBI_ALLOW_GITHUB_SKILL_IMPORT=false
```

The env var wins over the traitlet and is resolved at server startup. Recognized values: `true`/`false`/`1`/`0`/`yes`/`no`/`on`/`off`, case-insensitive. Unrecognized values raise at startup so a typo can't silently flip the policy.

### Tuning the chat-sidebar workspace file picker

**Audience:** server admins (and end users who edit `~/.jupyter/nbi/config.json` directly). The setting is not exposed in the Settings dialog.

The @-mention picker in the chat sidebar enumerates files from the JupyterLab working directory and skips a built-in set of directories (`__pycache__`, `node_modules`) plus any dotfiles/dot-directories. Because dot-prefixed names are filtered separately, entries starting with `.` are no-ops; list non-dot names only.

Match is by directory name only (not path), case-sensitive. Use this when a project has standard build outputs the picker shouldn't surface.

Four layers can contribute, all **additive** (each layer can only add new skipped names, never re-expose names skipped by an earlier layer):

1. **Traitlet** in `jupyter_server_config.py`:

   ```python
   c.NotebookIntelligence.additional_skipped_workspace_directories = ["build", "dist", "target"]
   ```

2. **Env var** at pod startup (per spawn profile):

   ```bash
   NBI_ADDITIONAL_SKIPPED_WORKSPACE_DIRECTORIES=tmp,artifacts
   ```

3. **Env-prefix NBI config** at `<env-prefix>/share/jupyter/nbi/config.json` (org-wide, baked into the image — useful when the deployment doesn't manage `jupyter_server_config.py` but does ship a curated config file):

   ```json
   { "additional_skipped_workspace_directories": ["coverage", "out"] }
   ```

4. **User NBI config** at `~/.jupyter/nbi/config.json` (per-user extension on top of the admin baseline):

   ```json
   { "additional_skipped_workspace_directories": [".terraform"] }
   ```

Duplicates are collapsed; the merged list is then added to the built-in skip set on the frontend. Edits to `config.json` require a JupyterLab restart to take effect, matching the rest of NBI config — there's no UI control (issue #232).

### Disabling the Skills tab

```python
c.NotebookIntelligence.skills_management_policy = "force-off"
```

Or via env: `NBI_SKILLS_MANAGEMENT_POLICY=force-off`.

Force-off does three things at once:

- Hides the **Skills** tab in the Settings panel.
- Returns HTTP 403 from every `/notebook-intelligence/skills/*` route, so a stale frontend or a direct API caller can't read or write skills.
- Suppresses the [managed-skills reconciler](#managed-claude-skills-token) — the manifest is treated as empty, no `SkillReconciler` is constructed, and no scheduled reconcile runs. Org-curated skills still on disk are not touched, but new manifests aren't pulled. **Takes effect on JupyterLab server restart.** For incident-response without a restart, see the kill switch below.

#### Stopping a running reconciler without a server restart

If the manifest URL or the managed-skills token is compromised and you need to stop the in-flight reconciler immediately, two non-restart paths are now available:

1. **HTTP kill switch:** `POST /notebook-intelligence/skills/reconciler/stop`. Authenticated (Jupyter session token). Stops the background reconciler and returns `{"stopped": true, "was_running": <bool>}`. Idempotent; safe to script across pods.

   ```bash
   curl -X POST -H "Authorization: token $JUPYTER_TOKEN" \
        https://hub.example.com/user/<name>/notebook-intelligence/skills/reconciler/stop
   ```

2. **Env-var flip:** the reconciler re-reads `NBI_SKILLS_MANAGEMENT_POLICY` at the start of each cycle and self-stops if it reads `force-off`. If your platform supports in-place pod env updates (rare), this fires the kill switch on the next reconcile boundary (default 24h). For most deployments the HTTP route is the faster option.

Either mechanism only stops the background thread; existing skill bundles on disk remain. Use `claude` or filesystem tooling to remove them if needed.

> **No live restart.** Once stopped, the reconciler stays stopped for the life of the JupyterLab process. There is no `/start` companion endpoint; bouncing the server is the only path to re-enable reconciliation in the same pod. This is intentional: a kill switch that another script can flip back on isn't a kill switch.
>
> **Per-user trust.** The endpoint is authenticated with the user's Jupyter session token, not an admin claim. In hub deployments where the JupyterLab pod owner is not the policy admin (typical for JupyterHub), a tenant can stop their own pod's reconciler. The reconciler is per-pod, so this only affects that user's managed-skills delivery. For deployments that need to prevent self-stop, leave `skills_management_policy` at `user-choice` and rely on the manifest-fetch token's scope as the auth boundary instead.

Use this section's full-disable when an org wants to disable user-authored Claude skills entirely.

> **Blast radius.** Force-off only kills the _management UI_ — skill bundles already on disk under `~/.claude/skills/` or a project's `.claude/skills/` keep being discovered by Claude Code itself because Claude's skill loader doesn't consult NBI's policy. To stop existing skills from loading, remove them on disk before flipping the policy.

### Disabling the Claude-mode MCP Servers tab

```python
c.NotebookIntelligence.claude_mcp_management_policy = "force-off"
```

Or via env: `NBI_CLAUDE_MCP_MANAGEMENT_POLICY=force-off`.

Force-off:

- Hides the Claude-mode **MCP Servers** tab in the Settings panel (visible only when Claude mode is on and the `claude` CLI is available).
- Returns HTTP 403 from every `/notebook-intelligence/claude-mcp/*` route.

The Claude-mode tab is **independent** of the existing non-Claude **MCP Servers** tab. The former wraps Claude Code's own config (`~/.claude.json`, relocated to `$CLAUDE_CONFIG_DIR/.claude.json` when that env var is set, and project `.mcp.json`); the latter manages NBI's own MCP servers used by the non-Claude chat path. They never appear at the same time; the non-Claude tab is hidden when Claude mode is on, and the Claude-mode tab is hidden when it's off.

Reads come from Claude's JSON config files directly (fast, no health checks). Writes (add / remove) shell out to `claude mcp add` / `claude mcp remove` so Claude remains the source of truth for any side effects (project-trust prompts, OAuth bookkeeping).

> **Blast radius.** Force-off only kills the _management UI_ — MCP servers already configured in `~/.claude.json` or `<cwd>/.mcp.json` keep loading inside Claude Code sessions because Claude's MCP loader doesn't consult NBI's policy. To stop existing servers, remove them on disk (or via the `claude mcp remove` CLI) before flipping the policy.

> **Trust model.** MCP servers run as subprocesses (stdio transport) or accept arbitrary URLs (sse/http transport) inside Claude Code sessions. Beyond CLI flag-smuggling rejection and HTTPS-required URL transports, NBI validates only the binary name against the optional [`mcp_stdio_command_allowlist`](#restricting-mcp-stdio-commands) and refuses a handful of env-key bypasses (`PATH`, `LD_PRELOAD`, `PYTHONPATH`, `NODE_OPTIONS`, etc.) when the gate is engaged. `args` and the spawned process are not contained. For multi-tenant or regulated deployments, prefer `claude_mcp_management_policy = force-off` plus a curated set of servers via `~/.claude/settings.json`.

### Restricting MCP stdio commands

When `mcp_stdio_command_allowlist` is non-empty, every stdio MCP server (whether added via the Claude-mode UI or loaded from `~/.jupyter/nbi/mcp.json`) must match at least one pattern in the list. Empty list (the default) means no enforcement.

```python
c.NotebookIntelligence.mcp_stdio_command_allowlist = [
    "^/usr/local/bin/uv$",
    "^/usr/local/bin/uvx$",
    "^/usr/local/bin/npx$",
]
```

Or via env (appends to the traitlet, useful for per-pod adds on an org baseline):

```
NBI_MCP_STDIO_COMMAND_ALLOWLIST=^/usr/local/bin/uv$,^/usr/local/bin/uvx$
```

Patterns use `re.search`, so anchor with `^...$` for literal equality. `"uv"` matches both `uv` and `uvtool`; `"^uv$"` matches only `uv`.

Whenever the gate engages on a stdio server, NBI additionally refuses dangerous env keys (`PATH`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `LD_AUDIT`, `DYLD_*`, `PYTHONPATH`, `PYTHONSTARTUP`, `PYTHONHOME`, `NODE_OPTIONS`, `NODE_PATH`, `BASH_ENV`, `ENV`) regardless of whether the allowlist is set. This closes the bypass where a poisoned `PATH` resolves an allowlisted binary name to attacker-controlled code, or where a `LD_PRELOAD` injects a shared object into the process before its entry point.

**Scope.** The gate matches the `command` field only. `args` flow through unchecked, so an allowlist that permits `npx` will still accept `args: ['-y', 'evil-pkg']`. If you need argv-level control, point `command` at a wrapper script you own that bakes the safe argv in.

**Behavior on rejection.** Claude-mode `mcp add` returns HTTP 400 with the policy error message so the user sees it in the Settings UI. The `mcp.json` loader logs a warning naming the server and skips it; the rest of the MCP list keeps loading.

### Disabling the Plugins tab

```python
c.NotebookIntelligence.claude_plugins_management_policy = "force-off"
```

Or via env: `NBI_CLAUDE_PLUGINS_MANAGEMENT_POLICY=force-off`.

Force-off hides the **Plugins** tab and returns 403 from every `/notebook-intelligence/plugins/*` route. The tab is otherwise visible only when Claude mode is on and the `claude` CLI is available. Both reads (`claude plugin list --json`) and writes (`claude plugin install` / `uninstall` / `enable` / `disable` / `marketplace add` / `marketplace remove`) shell out to the Claude CLI; Claude owns the plugin state under `~/.claude/plugins/` (`$CLAUDE_CONFIG_DIR/plugins/` when that env var is set); NBI reads the same location.

> **Blast radius.** Force-off only kills the _management UI_ — already-installed plugins keep loading inside Claude Code sessions because Claude's plugin loader doesn't consult NBI's policy. To stop existing plugins from loading, you'd need to remove them on disk or disable them via the `claude plugin disable` CLI before flipping the policy. Force-off prevents user-driven add/remove/enable/disable through NBI; that's the contract.

To allow user-driven plugin management but block GitHub-sourced marketplaces:

```python
c.NotebookIntelligence.allow_github_plugin_import = False
```

Or via env: `NBI_ALLOW_GITHUB_PLUGIN_IMPORT=false` (also accepts `true`/`1`/`0`/`yes`/`no`/`on`/`off`). When False, the "From GitHub" affordance in the Plugins panel hides itself and the backend rejects marketplace-add requests whose source is a GitHub URL, `owner/repo` shorthand, or `git@github.com:` reference. Local-path and arbitrary-URL sources remain available. This is finer-grained than `claude_plugins_management_policy = force-off`, which kills the entire surface.

> **Trust model.** Plugins installed via `claude plugin install` execute as part of Claude Code sessions; NBI does not signature-verify or sandbox them, and the `claude` CLI's validation is best-effort. The marketplace-add path is a network fetch (server-side) — for multi-tenant or regulated deployments, default to `claude_plugins_management_policy = force-off` and curate plugins server-side, or restrict marketplaces to vetted sources only.

#### GitHub auth for marketplace add

When the marketplace source is a GitHub URL or `owner/repo` shorthand, NBI resolves a token with the same precedence as Skills' GitHub import:

1. `GITHUB_TOKEN` env var (server-process scope)
2. `GH_TOKEN` env var
3. `gh auth token` subprocess output (only if `gh` is on PATH)

Resolved tokens are injected into the `claude plugin marketplace add` subprocess via env, never argv — they do not appear in DEBUG logs. The chain is re-evaluated per call, so rotating the token (env update or `gh auth refresh`) takes effect on the next add. Required scope: `repo` for classic PATs, or `contents:read` on the target repo for fine-grained PATs. When `gh` is not installed the third step short-circuits silently; rely on `GITHUB_TOKEN` instead.

**GitHub Enterprise:** the detector recognizes GHE hosts that an admin declares via `NBI_GITHUB_ENTERPRISE_HOSTS` (CSV). Without that env, only public github.com is recognized, and a GHE marketplace URL falls through to anonymous git auth AND silently bypasses `allow_github_plugin_import = False`. Declare every host that should be treated as GitHub:

```bash
# Exact host matches only — safest default.
NBI_GITHUB_ENTERPRISE_HOSTS=github.acme.com,ghe.example.com
```

Tokens follow cookie-domain semantics: a bare token matches the exact host only; a leading-dot token (`.acme.com`) matches every subdomain of `acme.com`. Subdomain matching is opt-in because if suffix-matching were the default, declaring `acme.com` would silently inject `GITHUB_TOKEN` into any `*.acme.com` corp service (jira, artifactory, etc.) that someone happened to point marketplace-add at. Prefer the exact form; reach for the leading-dot form only when you actually have multiple GitHub subdomains under one apex.

```bash
# Explicit opt-in to subdomain matching: covers github.acme.com,
# ghe.acme.com, and any future *.acme.com GitHub-flavored host.
NBI_GITHUB_ENTERPRISE_HOSTS=.acme.com
```

The matcher rejects lookalikes that aren't actual subdomains, so `github.acme.com.evil.test` is correctly excluded regardless of the token shape.

This env is **independent of** `NBI_GHE_SUBDOMAIN` (which only configures GitHub Copilot's OAuth tenant). The two settings serve different surfaces; set whichever applies to your deployment.

For air-gap deployments, marketplace-add inherits the JupyterLab process env, so the same `HTTPS_PROXY` / `HTTP_PROXY` / `NO_PROXY` / `NODE_EXTRA_CA_CERTS` settings documented in [Custom CA certs and corporate proxies](#custom-ca-certs-and-corporate-proxies) apply. Pre-installed plugins (under `~/.claude/plugins/`) keep loading without any network access.

### Allowing Bypass Permissions in the Claude permission-mode selector

```python
c.NotebookIntelligence.claude_bypass_permissions_policy = "user-choice"
```

Or via env: `NBI_CLAUDE_BYPASS_PERMISSIONS_POLICY=user-choice`.

The Claude permission-mode selector (Default / Accept Edits / Plan) always offers its three normal modes; all three keep NBI's tool-call confirmation flow. "Bypass Permissions" removes that confirmation entirely and runs every tool call with the Jupyter user's full access, so it is **disabled by default**: this is one of three policies whose default is `force-off` rather than `user-choice` (the others gate the experimental ACP agent mode and ACP full access, #378). Setting `user-choice` exposes the option, which the user must still arm explicitly per session through a confirm step; the armed state never persists across sessions or reconnects. `force-on` grants the same availability as `user-choice` and never auto-arms bypass.

Independent of this policy, NBI refuses bypass whenever Claude Code's enterprise [managed settings](https://docs.anthropic.com/en/docs/claude-code/settings) set `permissions.disableBypassPermissionsMode`, and honors a managed `permissions.defaultMode` as the selector's starting mode (except bypass, which never starts armed). The clamp is enforced server-side at the request boundary, not just in the UI.

### Gating the experimental ACP agent (#378)

ACP mode runs an external coding agent over the Agent Client Protocol; Codex (via the codex-acp adapter) is the first selectable agent type. ACP mode and Claude mode are mutually exclusive: enabling one disables the other on save, and a hand-edited config that enables both resolves to Claude. The admin controls that apply:

- **`NBI_ACP_MODE_POLICY`** (default `force-off`): whether ACP mode can be enabled at all. While `force-off`, the ACP settings tab is hidden and `enabled` is clamped to false on every config write, so it is the nuclear off switch.
- **`NBI_ACP_FULL_ACCESS_POLICY`** (default `force-off`): whether the agent may run tools without asking. While `force-off`, NBI pins Codex to `approval_policy = untrusted`, so it asks before anything beyond trusted read-only commands and each request surfaces through NBI's per-tool confirmation. `user-choice` exposes the "Full access" toggle in the ACP tab; `force-on` requires unattended runs. Like bypass, the value is clamped server-side, so a hand-crafted config write can't relax it.
- **Credentials and model** follow the same env-override locks as Claude: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `NBI_ACP_CHAT_MODEL` pin and hide the corresponding fields.

How the pin is applied: NBI passes `approval_policy` to codex via the codex-acp binary's `-c key=value` command-line override, which takes precedence over the codex config file (`$CODEX_HOME/config.toml`). When an API key is configured, NBI also runs codex with an isolated `CODEX_HOME` under the NBI user directory, so the codex configuration base is NBI-controlled and neither the workspace's nor the user's `~/.codex` config is read (this isolates configuration only; the agent still operates on workspace files, subject to the approval pin). With ChatGPT (OAuth) auth, codex uses the user's own `~/.codex`; the `-c` pin still overrides its top-level `approval_policy`, but a user who controls that home directory is also the account the agent runs as. On shared or locked-down deployments, prefer API-key auth (so `CODEX_HOME` is isolated) and keep `NBI_ACP_MODE_POLICY=force-off` unless the agent is explicitly needed.

Known limitations:

- NBI gates the agent through the approval posture above and through its per-tool confirmation, but it does not enforce a tool allowlist inside the agent the way Claude's tool policies do. The agent owns its own tool loop and NBI only mediates the approvals the agent chooses to request.
- The pin relies on codex honoring the command-line `-c approval_policy` override above any configuration it loads. NBI does not parse or strip codex's own config; if a future codex version applies a higher-precedence per-project setting, that assumption would need revisiting. Keep `NBI_ACP_MODE_POLICY=force-off` where these constraints are not acceptable.

### Disabling terminal drag-drop file attach

```python
c.NotebookIntelligence.terminal_drag_drop_policy = "force-off"
```

Or via env: `NBI_TERMINAL_DRAG_DROP_POLICY=force-off`.

Force-off hides the per-terminal drag-drop toolbar toggle and rejects upload-staging POSTs from a terminal context. Drag-drop is **enabled by default**; flip it off in regulated tenants where the staging file write or the resulting `@`-mention path is undesirable.

### Disabling the open-files refresh watcher

```python
c.NotebookIntelligence.refresh_open_files_on_disk_change_policy = "force-off"
```

Or via env: `NBI_REFRESH_OPEN_FILES_ON_DISK_CHANGE_POLICY=force-off`.

The refresh watcher reloads open notebook and file editor tabs when their content changes on disk (for example, when an agent edits a file). It skips tabs with unsaved local edits. Enabled by default; users can opt out in the NBI Settings dialog. Use `force-off` in deployments where automatic reloads could surprise users editing the same files via external tooling, or `force-on` to mandate the behavior tenant-wide.

---

Terminal drag-drop and chat-sidebar file attach both write to the shared upload-staging directory under the JupyterLab process's `tempfile.gettempdir()`. Two tunables govern that endpoint:

| Env var                      | Default | Behavior                                                                                                                         |
| ---------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `NBI_UPLOAD_MAX_MB`          | `50`    | Per-file size cap (megabytes). Over the cap returns HTTP 413. `0` disables.                                                      |
| `NBI_UPLOAD_RETENTION_HOURS` | `24`    | How long staged uploads survive before the next upload sweeps them. `0` keeps only the atexit purge (files survive the session). |

> **Trust model.** Staged files live in the user's `tempfile.gettempdir()` and inherit the directory's POSIX permissions. The same `nbi-file-read` denylist that scopes general file reads does **not** apply to upload-staged files because they sit outside the Jupyter root. For sensitive-data tenants, set `NBI_UPLOAD_RETENTION_HOURS=0` to skip retention beyond the session and pair with `terminal_drag_drop_policy = force-off` to keep the surface to chat-sidebar attachments only.

---

## Multi-tenancy and per-team scoping

JupyterHub spawn-time profiles can carry per-team config:

```python
c.KubeSpawner.profile_list = [
    {
        "display_name": "Data Science (managed skills)",
        "kubespawner_override": {
            "environment": {
                "NBI_SKILLS_MANIFEST": "https://manifests.internal/team-ds.yaml",
                "NBI_MANAGED_SKILLS_TOKEN": {"valueFrom": {"secretKeyRef": {...}}},
            },
        },
    },
    {
        "display_name": "ML Research (no managed skills)",
        "kubespawner_override": {"environment": {"NBI_SKILLS_MANIFEST": ""}},
    },
]
```

The reconciler handles skill name collisions between manifests and user-authored skills per entry (managed entries skip user-authored skills with the same name). Across teams, if a user moves between profiles that ship different `data-eda` skills, they see whichever team's skill the active profile reconciled most recently. Keep team skill names distinct (`team-ds-data-eda`, `team-ml-data-eda`) to avoid surprises.

---

## Managed Claude Skills token

`NBI_MANAGED_SKILLS_TOKEN` (or the `managed_skills_token` traitlet) authenticates managed-skills GitHub operations (manifest fetch when hosted on github.com, commits-API probing, tarball downloads).

- **Minimum scope:** `contents:read` on the org/repos that host the manifest and skill bundles.
- **Rotation:** NBI reads the token from env on every reconcile cycle. Restart the pod (or reissue the env, if your KubeSpawner re-reads on resume) to rotate.
- **401/403 behavior:** a single auth failure on a managed operation is logged and **not retried with the fallback token chain** when `NBI_MANAGED_SKILLS_TOKEN` is set. This keeps misconfiguration visible. The reconciler continues with remaining entries.
- **User-initiated imports do not see this token.** They use `GITHUB_TOKEN` → `GH_TOKEN` → `gh` CLI auth. Keep these separate so a misconfigured org token can't unintentionally apply to user imports.

---

## Telemetry events

NBI emits `telemetry` events in-process to signal feature usage. The events are **emitted in-process only**: by default nothing listens for them, so they go nowhere and never leave the process. To collect them (for example, to forward usage into an internal observability stack such as Kafka or an OTel collector), write a JupyterLab extension that registers a listener through NBI's `INotebookIntelligence` token and forwards what it receives. Payload shapes are not currently considered stable API; if you build on them, pin to a specific NBI version.

Each event has a `type` (the identifier below) and a `data` payload carrying the relevant model and context. Auto-complete (inline completion) and cell inline chat are reported as separate event families, so a listener can tell the two features apart by `type` alone.

Auto-complete:

| Event `type`                 | Fires when                                                    |
| ---------------------------- | ------------------------------------------------------------- |
| `inline-completion-request`  | A ghost-text completion is requested as the user types.       |
| `inline-completion-response` | A suggestion is returned (carries `timeElapsed`).             |
| `inline-completion-accepted` | The user accepts a suggestion (Tab or the completer toolbar). |

Cell inline chat:

| Event `type`            | Fires when                                                          |
| ----------------------- | ------------------------------------------------------------------- |
| `inline-chat-request`   | The user submits an inline-chat prompt (carries the `prompt`).      |
| `inline-chat-response`  | A generation completes (carries `timeElapsed`).                     |
| `inline-chat-accepted`  | Generated code is applied, with `mode` = `review` or `auto-insert`. |
| `inline-chat-dismissed` | The popover is closed after code was shown but never applied.       |

Chat and feedback:

| Event `type`                     | Fires when                                                                                 |
| -------------------------------- | ------------------------------------------------------------------------------------------ |
| `chat-request` / `chat-response` | A message is sent to / answered by the chat sidebar.                                       |
| `feedback`                       | The user gives thumbs-up/down on a chat response (`sentiment` = `positive` or `negative`). |

NBI also emits request events for the chat sidebar's code and output actions (`generate-code-request`, `explain-this-request`, `fix-this-code-request`, `explain-this-output-request`, `troubleshoot-this-output-request`, `output-follow-up-request`).

The `inline-completion-*`, `inline-chat-*`, and chat/action events fire whenever those features are used; they are not gated by any setting. To measure how often auto-complete suggestions are taken, compare `inline-completion-accepted` against `inline-completion-response`; the gap is the ignore rate. Only the `feedback` event is gated, by `enable_chat_feedback` (off by default), which also controls the thumbs-up/down UI:

```python
c.NotebookIntelligence.enable_chat_feedback = True
```

The thumbs buttons reveal on hover by default. To keep them always visible, enable:

```python
c.NotebookIntelligence.enable_chat_feedback_always_visible = True
```

---

## HTTP API surface

All routes live under `/notebook-intelligence/`. All require Jupyter authentication (XSRF token plus Jupyter login token) including the `/copilot` WebSocket upgrade, which now inherits Jupyter's `WebSocketMixin` + `JupyterHandler` so the same `allow_origin` and identity-provider checks that apply to REST handlers also apply to the chat WS endpoint. The labextension obtains these automatically. There is no admin-only route; access control runs through Jupyter Server itself.

| Route                                                       | Method          | Purpose                                                                                                                                              |
| ----------------------------------------------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/notebook-intelligence/capabilities`                       | GET             | Capabilities + tool/provider gate state.                                                                                                             |
| `/notebook-intelligence/config`                             | GET/POST        | Read or update user-scope config.                                                                                                                    |
| `/notebook-intelligence/update-provider-models`             | POST            | Refresh model list for a provider (e.g., Anthropic SDK refresh).                                                                                     |
| `/notebook-intelligence/mcp-config-file`                    | GET/POST        | Read or write `~/.jupyter/nbi/mcp.json`.                                                                                                             |
| `/notebook-intelligence/reload-mcp-servers`                 | POST            | Re-discover MCP servers without restarting JupyterLab.                                                                                               |
| `/notebook-intelligence/emit-telemetry-event`               | POST            | Used by the frontend to emit `telemetry` events (chat feedback, inline completion and inline chat usage). See [Telemetry events](#telemetry-events). |
| `/notebook-intelligence/gh-login-status`                    | GET             | GitHub Copilot login state.                                                                                                                          |
| `/notebook-intelligence/gh-login`                           | POST            | Begin GitHub Copilot device-flow login.                                                                                                              |
| `/notebook-intelligence/gh-logout`                          | GET             | Sign out of GitHub Copilot.                                                                                                                          |
| `/notebook-intelligence/copilot`                            | WS              | Streaming chat / inline-completion WebSocket.                                                                                                        |
| `/notebook-intelligence/rules`                              | GET             | List discovered rules.                                                                                                                               |
| `/notebook-intelligence/rules/<id>/toggle`                  | PUT             | Toggle a rule's `active` field.                                                                                                                      |
| `/notebook-intelligence/rules/reload`                       | POST            | Manually reload all rules.                                                                                                                           |
| `/notebook-intelligence/skills`                             | GET/POST        | List or create skills.                                                                                                                               |
| `/notebook-intelligence/skills/context`                     | GET             | Skill context info for the active workspace.                                                                                                         |
| `/notebook-intelligence/skills/import/preview`              | POST            | Preview a GitHub-hosted skill before installing.                                                                                                     |
| `/notebook-intelligence/skills/import`                      | POST            | Install a GitHub-hosted skill (user-initiated).                                                                                                      |
| `/notebook-intelligence/skills/reconcile`                   | POST            | Run the managed-skills reconciler. Returns 409 if `NBI_SKILLS_MANIFEST` is unset.                                                                    |
| `/notebook-intelligence/skills/reconciler/stop`             | POST            | Incident-response kill switch. Stops the background reconciler without a server restart. Idempotent.                                                 |
| `/notebook-intelligence/skills/<scope>/<name>`              | GET/PUT/DELETE  | Skill detail; managed skills are read-only.                                                                                                          |
| `/notebook-intelligence/skills/<scope>/<name>/rename`       | POST            | Rename a skill (denied for managed skills).                                                                                                          |
| `/notebook-intelligence/skills/<scope>/<name>/files`        | GET/POST/DELETE | Skill bundle file ops.                                                                                                                               |
| `/notebook-intelligence/skills/<scope>/<name>/files/rename` | POST            | Rename a file inside a skill bundle.                                                                                                                 |
| `/notebook-intelligence/upload-file`                        | POST            | Upload a file to attach as chat context (size and retention governed by `upload_max_mb` / `upload_retention_hours`).                                 |
| `/notebook-intelligence/claude-sessions`                    | GET             | List Claude Code sessions for the working directory.                                                                                                 |
| `/notebook-intelligence/claude-sessions/resume`             | POST            | Resume a Claude session.                                                                                                                             |
| `/notebook-intelligence/acp-sessions`                       | GET             | List the ACP agent's stored sessions for the working directory (via the agent's `session/list`).                                                     |
| `/notebook-intelligence/acp-sessions/resume`                | POST            | Resume an ACP session (via the agent's `session/load`).                                                                                              |
| `/notebook-intelligence/claude-mcp`                         | GET/POST        | List or add Claude-mode MCP servers. Gated by `claude_mcp_management_policy`.                                                                        |
| `/notebook-intelligence/claude-mcp/<scope>/<name>`          | GET/DELETE      | Get or remove a Claude-mode MCP server by scope (user/project/local) and name.                                                                       |
| `/notebook-intelligence/plugins`                            | GET/POST        | List or install Claude plugins. Gated by `claude_plugins_management_policy`.                                                                         |
| `/notebook-intelligence/plugins/<scope>/<name>`             | POST/DELETE     | Enable or disable (POST with `{"action": "enable"\|"disable"}`) or uninstall (DELETE) a plugin.                                                      |
| `/notebook-intelligence/plugins/marketplace`                | GET/POST        | List or add plugin marketplaces. GitHub-sourced adds are gated by `allow_github_plugin_import`.                                                      |
| `/notebook-intelligence/plugins/marketplace/<name>`         | DELETE          | Remove a plugin marketplace.                                                                                                                         |

The extension respects `c.ServerApp.base_url`. Behind JupyterHub at `/user/<name>/` everything still works because JupyterLab proxies routes through the per-user base URL automatically.

---

## Failure modes

| Condition                                      | User-visible behavior                                                                                            | Where to look in logs                                                                                       |
| ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| LLM provider unreachable                       | Chat shows "Thinking…", then a connection-error toast.                                                           | JupyterLab terminal (server stderr).                                                                        |
| LLM 401 (bad or expired key)                   | Chat shows the provider's error message.                                                                         | JupyterLab terminal; the provider's own SDK logs.                                                           |
| Claude CLI missing                             | Chat hangs on "Thinking…" in Claude mode; never returns.                                                         | JupyterLab terminal — `claude-agent-sdk` connect failure.                                                   |
| Claude CLI fails to start (path mismatch)      | Same as above.                                                                                                   | Same — set `NBI_CLAUDE_CLI_PATH` and restart JupyterLab.                                                    |
| MCP stdio server crashes                       | The server's tools disappear from `@mcp` chat participant.                                                       | JupyterLab terminal — server's stderr.                                                                      |
| MCP `npx -y` package fetch fails (offline)     | Server fails to start; tools missing.                                                                            | JupyterLab terminal.                                                                                        |
| Managed-skills manifest 5xx / DNS failure      | Reconciler logs the error; existing managed skills remain installed.                                             | JupyterLab terminal — `skill_reconciler` warning/error.                                                     |
| Managed-skills tarball fetch fails (per entry) | That entry stays at the previously installed version; others succeed.                                            | JupyterLab terminal — per-entry error.                                                                      |
| `NBI_MANAGED_SKILLS_TOKEN` 401/403             | Reconcile fails; loud log; does not fall back to the `GITHUB_TOKEN` chain.                                       | JupyterLab terminal.                                                                                        |
| Ruleset frontmatter is invalid YAML            | Rule is skipped; others load.                                                                                    | JupyterLab terminal — `rule_manager` warning.                                                               |
| Encrypted token decrypt fails                  | The chat sidebar prompts the user to sign in again.                                                              | JupyterLab terminal.                                                                                        |
| `claude plugin install` fails (network, auth)  | Plugin row stays unchanged; install button surfaces the CLI's stderr.                                            | JupyterLab terminal (`plugin_manager` warning).                                                             |
| `claude plugin marketplace add` from GHE       | Without `NBI_GITHUB_ENTERPRISE_HOSTS`, falls through to anonymous git auth and may fail without a clear message. | JupyterLab terminal; see GHE caveat in [GitHub auth for marketplace add](#github-auth-for-marketplace-add). |
| Copilot model-list endpoint fails              | Chat-model dropdown silently falls back to the hardcoded list.                                                   | JupyterLab terminal (`github_copilot` warning).                                                             |
| Upload exceeds `NBI_UPLOAD_MAX_MB`             | Terminal drag-drop and chat-sidebar attach both return HTTP 413.                                                 | JupyterLab terminal; check `upload_max_mb` traitlet.                                                        |

---

## Version matrix

NBI is tested against the JupyterLab and `jupyter_server` versions declared in [`pyproject.toml`](../pyproject.toml).

| NBI version | JupyterLab | jupyter_server | Python    |
| ----------- | ---------- | -------------- | --------- |
| 5.1.x       | 4.x        | 2.x            | 3.10+     |
| 5.0.x       | 4.x        | 2.x            | 3.10+     |
| 4.8.x       | 4.x        | 2.x            | 3.10+     |
| 4.7.x       | 4.x        | 2.x            | 3.10+     |
| 4.6.x       | 4.x        | 2.x            | 3.10–3.12 |
| 4.5.x       | 4.x        | 2.x            | 3.10–3.12 |
| 4.4.x       | 4.x        | 2.x            | 3.10–3.12 |
| 4.3.x       | 4.x        | 2.x            | 3.10–3.12 |

Upper bounds for `litellm`, `claude-agent-sdk`, `anthropic`, and `mcp` are not pinned in `pyproject.toml`. For production deployments, pin these in your image build:

```bash
pip install \
  "notebook-intelligence==5.1.*" \
  "litellm==1.83.*" \
  "claude-agent-sdk==0.x.*" \
  "anthropic==0.x.*" \
  "mcp==1.27.*"
```

Substitute the versions you've validated. As of 5.0.0 NBI uses the official `mcp` Python SDK (the prior `fastmcp` dependency was removed); see the [5.0.0 changelog migration note](../CHANGELOG.md#migration-note) if your image previously pinned `fastmcp`. The NBI test suite is currently TypeScript-only (`jlpm test`); end-to-end Python testing is a future work item.

---

## FIPS posture

NBI's `cryptography` dependency is used solely to encrypt the stored GitHub Copilot token. The default password (`nbi-access-token-password`) and the encryption are intended for at-rest obfuscation, **not** as a FIPS-validated secret store.

If you operate under FIPS:

- Run with the FIPS-mode OpenSSL build of Python. NBI's `cryptography` calls (Fernet under the hood — AES-128-CBC plus HMAC-SHA256) work under FIPS-mode OpenSSL.
- Set a per-user `NBI_GH_ACCESS_TOKEN_PASSWORD` so the encryption key is not derivable.
- For higher assurance, disable "remember GitHub Copilot login" entirely so no encrypted-at-rest token exists.

NBI itself does not assert FIPS compliance.

---

## Resource footprint

NBI's per-user memory cost depends on which Python dependencies actually get imported. A clean Jupyter Server with NBI installed but no LLM activity uses roughly the baseline of `notebook_intelligence` plus its server-imported dependencies.

We have not published measured numbers. If you size pods aggressively, profile your image: import everything NBI imports lazily (run a chat turn, trigger inline completion, exercise `claude-agent-sdk`) and measure RSS before sizing your pod memory request. Inline completion under load is the chattiest path; consider provider-side rate limits if you have hundreds of simultaneous users on a paid endpoint.

A measured-baseline document is on the roadmap. If you have numbers from a production deployment, share them in a GitHub issue.
