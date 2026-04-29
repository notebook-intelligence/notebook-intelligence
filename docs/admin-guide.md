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
- [Chat feedback event hook](#chat-feedback-event-hook)
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

Traitlets configured via JupyterLab CLI flags or `jupyter_server_config.py` (e.g., `c.NotebookIntelligence.disabled_providers = [...]`) are evaluated at server startup. Where a traitlet has a corresponding env var (e.g., `NBI_ENABLED_PROVIDERS` for `disabled_providers` and `allow_enabling_providers_with_env`), NBI reads the env var at request time.

Manual edits to `config.json` while JupyterLab is running require a JupyterLab restart to take effect. Edits via the Settings dialog are picked up live.

---

## Persistent-volume layout

| Path                                      | Persist?    | Notes                                                                                                                           |
| ----------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `~/.jupyter/nbi/config.json`              | Yes         | User's chosen provider, models, MCP servers, plus plaintext API keys. Treat as a secret.                                        |
| `~/.jupyter/nbi/user-data.json`           | Yes         | Encrypted GitHub Copilot access token, written when "remember login" is enabled. Encrypted with `NBI_GH_ACCESS_TOKEN_PASSWORD`. |
| `~/.jupyter/nbi/rules/`                   | Yes         | User's ruleset markdown files.                                                                                                  |
| `~/.jupyter/nbi/mcp.json`                 | Yes         | User's MCP server config (alternative to managing via the Settings dialog).                                                     |
| `~/.claude/skills/`                       | Yes         | User-scope Claude skills (including managed skills).                                                                            |
| `~/.claude/projects/`                     | Yes         | Claude Code session transcripts. Required for "Resume previous Claude session". Managed by Claude CLI, not NBI.                 |
| `<env-prefix>/share/jupyter/nbi/`         | No (image)  | Org-wide base config. Bake into your container image.                                                                           |
| Project-scope `<project>/.claude/skills/` | Per project | Lives in the user's working directory. Persists if the working directory does.                                                  |

For Kubeflow or KubeSpawner: mount the user's home directory on a PVC and ensure `~/.jupyter` and `~/.claude` are inside that mount. Anything else (`/tmp`, `~/.cache`) can be ephemeral.

---

## Shared filesystem and multi-user notes

If users share a home directory across nodes (NFS-backed shared HPC, classroom labs):

- **Race conditions in `~/.jupyter/nbi/`.** Concurrent writes from two login nodes can corrupt `config.json`. NBI does not file-lock. Pin each user to one node, or use a per-node config prefix.
- **`NBI_GH_ACCESS_TOKEN_PASSWORD` default is unsafe.** The default password (`nbi-access-token-password`) is shared across installs. On a multi-tenant cluster, anyone with read access to another user's `~/.jupyter/nbi/user-data.json` can decrypt their Copilot token. Set a per-user password (e.g., derived from the Hub user secret), or disable "remember login" entirely (see [Restricting features](#restricting-features-for-managed-deployments)).
- **Skill collisions.** Two users sharing `~/.claude/skills/` will see each other's skills. Make sure each user has a unique home.

---

## Environment variables and traitlets

The full surface, in one table.

| Name                                | Type | Default                     | Source                             | Purpose                                                                                                                      |
| ----------------------------------- | ---- | --------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `disabled_providers`                | List | `[]`                        | traitlet on `NotebookIntelligence` | Hide providers from the user dropdown. Values: `github-copilot`, `ollama`, `litellm-compatible`, `openai-compatible`.        |
| `allow_enabling_providers_with_env` | Bool | `False`                     | traitlet                           | If true, `NBI_ENABLED_PROVIDERS` re-enables hidden providers per pod.                                                        |
| `NBI_ENABLED_PROVIDERS`             | csv  | unset                       | env                                | Comma-separated provider IDs to re-enable. Effective only when `allow_enabling_providers_with_env=True`.                     |
| `disabled_tools`                    | List | `[]`                        | traitlet                           | Hide built-in tools from agent mode. Values listed in [Restricting features](#restricting-features-for-managed-deployments). |
| `allow_enabling_tools_with_env`     | Bool | `False`                     | traitlet                           | If true, `NBI_ENABLED_BUILTIN_TOOLS` re-enables hidden tools per pod.                                                        |
| `NBI_ENABLED_BUILTIN_TOOLS`         | csv  | unset                       | env                                | Comma-separated tool IDs to re-enable. Effective only when `allow_enabling_tools_with_env=True`.                             |
| `enable_chat_feedback`              | Bool | `False`                     | traitlet                           | Enables thumbs-up/down UI in chat and emits in-process `telemetry` events.                                                   |
| `skills_manifest`                   | str  | `""`                        | traitlet                           | URL or filesystem path to a managed-skills manifest. See [`docs/skills.md`](skills.md#managed-skills-via-an-org-manifest).   |
| `NBI_SKILLS_MANIFEST`               | str  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                         |
| `skills_manifest_interval`          | int  | `86400`                     | traitlet                           | Seconds between reconciles.                                                                                                  |
| `NBI_SKILLS_MANIFEST_INTERVAL`      | int  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                         |
| `managed_skills_token`              | str  | `""`                        | traitlet                           | Bearer token for managed-skills GitHub fetches.                                                                              |
| `NBI_MANAGED_SKILLS_TOKEN`          | str  | unset                       | env (overrides traitlet)           | Same as above; env takes precedence.                                                                                         |
| `NBI_GH_ACCESS_TOKEN_PASSWORD`      | str  | `nbi-access-token-password` | env                                | Password used to encrypt the stored Copilot token in `user-data.json`. **Change in multi-tenant deployments.**               |
| `NBI_RULES_AUTO_RELOAD`             | bool | `true`                      | env                                | When `false`, ruleset edits require a JupyterLab restart to take effect.                                                     |
| `NBI_CLAUDE_CLI_PATH`               | str  | unset                       | env                                | Absolute path to the Claude Code CLI binary. When unset, NBI looks up `claude` on `PATH`.                                    |
| `NBI_GHE_SUBDOMAIN`                 | str  | `""`                        | env                                | GitHub Enterprise subdomain for GitHub Copilot users on a GHE tenant. Empty selects github.com.                              |
| `NBI_LOG_LEVEL`                     | str  | `INFO`                      | env                                | Python logging level for the `notebook_intelligence` logger.                                                                 |
| `GITHUB_TOKEN`, `GH_TOKEN`          | str  | unset                       | env                                | Used (in that order) by user-initiated skill imports for GitHub auth. Falls back to `gh` CLI auth.                           |

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
- **MCP stdio servers** are launched as user subprocesses with the user's environment. NBI does not sandbox them.
- **Claude Code CLI** inherits the user's environment, including filesystem permissions and any auth tokens in `~/.claude/`.

For regulated tenants:

1. Disable the most powerful tools — at minimum `nbi-command-execute` and `nbi-file-edit`. See [Restricting features](#restricting-features-for-managed-deployments).
2. Restrict the providers the user can pick. Force a single self-hosted endpoint with `disabled_providers` plus the org's base config.
3. Disable user-initiated skill imports (currently network-layer only — see [`skills.md`](skills.md#disabling-user-initiated-github-imports)).
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

## Chat feedback event hook

When `enable_chat_feedback = True`, NBI emits a `telemetry` event in-process whenever a user gives thumbs-up/down feedback in chat. The event payload includes the rating, the prompt, the response, and the model.

The event is **emitted in-process only**. Nothing leaves the process unless you write a custom handler that listens for it. The payload shape is not currently considered stable API; if you build on it, pin to a specific NBI version.

To pipe feedback into your internal observability stack (Kafka, OTel collector), write an extension that registers a listener for the `telemetry` event and forwards it.

---

## HTTP API surface

All routes live under `/notebook-intelligence/`. All require Jupyter authentication (XSRF token plus Jupyter login token) — the labextension obtains these automatically. There is no admin-only route; access control runs through Jupyter Server itself.

| Route                                                       | Method          | Purpose                                                                           |
| ----------------------------------------------------------- | --------------- | --------------------------------------------------------------------------------- |
| `/notebook-intelligence/capabilities`                       | GET             | Capabilities + tool/provider gate state.                                          |
| `/notebook-intelligence/config`                             | GET/POST        | Read or update user-scope config.                                                 |
| `/notebook-intelligence/update-provider-models`             | POST            | Refresh model list for a provider (e.g., Anthropic SDK refresh).                  |
| `/notebook-intelligence/mcp-config-file`                    | GET/POST        | Read or write `~/.jupyter/nbi/mcp.json`.                                          |
| `/notebook-intelligence/reload-mcp-servers`                 | POST            | Re-discover MCP servers without restarting JupyterLab.                            |
| `/notebook-intelligence/emit-telemetry-event`               | POST            | Used by the frontend to emit `telemetry` events (e.g., chat feedback).            |
| `/notebook-intelligence/gh-login-status`                    | GET             | GitHub Copilot login state.                                                       |
| `/notebook-intelligence/gh-login`                           | POST            | Begin GitHub Copilot device-flow login.                                           |
| `/notebook-intelligence/gh-logout`                          | GET             | Sign out of GitHub Copilot.                                                       |
| `/notebook-intelligence/copilot`                            | WS              | Streaming chat / inline-completion WebSocket.                                     |
| `/notebook-intelligence/rules`                              | GET             | List discovered rules.                                                            |
| `/notebook-intelligence/rules/<id>/toggle`                  | PUT             | Toggle a rule's `active` field.                                                   |
| `/notebook-intelligence/rules/reload`                       | POST            | Manually reload all rules.                                                        |
| `/notebook-intelligence/skills`                             | GET/POST        | List or create skills.                                                            |
| `/notebook-intelligence/skills/context`                     | GET             | Skill context info for the active workspace.                                      |
| `/notebook-intelligence/skills/import/preview`              | POST            | Preview a GitHub-hosted skill before installing.                                  |
| `/notebook-intelligence/skills/import`                      | POST            | Install a GitHub-hosted skill (user-initiated).                                   |
| `/notebook-intelligence/skills/reconcile`                   | POST            | Run the managed-skills reconciler. Returns 409 if `NBI_SKILLS_MANIFEST` is unset. |
| `/notebook-intelligence/skills/<scope>/<name>`              | GET/PUT/DELETE  | Skill detail; managed skills are read-only.                                       |
| `/notebook-intelligence/skills/<scope>/<name>/rename`       | POST            | Rename a skill (denied for managed skills).                                       |
| `/notebook-intelligence/skills/<scope>/<name>/files`        | GET/POST/DELETE | Skill bundle file ops.                                                            |
| `/notebook-intelligence/skills/<scope>/<name>/files/rename` | POST            | Rename a file inside a skill bundle.                                              |
| `/notebook-intelligence/upload-file`                        | POST            | Upload a file to attach as chat context.                                          |
| `/notebook-intelligence/claude-sessions`                    | GET             | List Claude Code sessions for the working directory.                              |
| `/notebook-intelligence/claude-sessions/resume`             | POST            | Resume a Claude session.                                                          |

The extension respects `c.ServerApp.base_url`. Behind JupyterHub at `/user/<name>/` everything still works because JupyterLab proxies routes through the per-user base URL automatically.

---

## Failure modes

| Condition                                      | User-visible behavior                                                      | Where to look in logs                                     |
| ---------------------------------------------- | -------------------------------------------------------------------------- | --------------------------------------------------------- |
| LLM provider unreachable                       | Chat shows "Thinking…", then a connection-error toast.                     | JupyterLab terminal (server stderr).                      |
| LLM 401 (bad or expired key)                   | Chat shows the provider's error message.                                   | JupyterLab terminal; the provider's own SDK logs.         |
| Claude CLI missing                             | Chat hangs on "Thinking…" in Claude mode; never returns.                   | JupyterLab terminal — `claude-agent-sdk` connect failure. |
| Claude CLI fails to start (path mismatch)      | Same as above.                                                             | Same — set `NBI_CLAUDE_CLI_PATH` and restart JupyterLab.  |
| MCP stdio server crashes                       | The server's tools disappear from `@mcp` chat participant.                 | JupyterLab terminal — server's stderr.                    |
| MCP `npx -y` package fetch fails (offline)     | Server fails to start; tools missing.                                      | JupyterLab terminal.                                      |
| Managed-skills manifest 5xx / DNS failure      | Reconciler logs the error; existing managed skills remain installed.       | JupyterLab terminal — `skill_reconciler` warning/error.   |
| Managed-skills tarball fetch fails (per entry) | That entry stays at the previously installed version; others succeed.      | JupyterLab terminal — per-entry error.                    |
| `NBI_MANAGED_SKILLS_TOKEN` 401/403             | Reconcile fails; loud log; does not fall back to the `GITHUB_TOKEN` chain. | JupyterLab terminal.                                      |
| Ruleset frontmatter is invalid YAML            | Rule is skipped; others load.                                              | JupyterLab terminal — `rule_manager` warning.             |
| Encrypted token decrypt fails                  | The chat sidebar prompts the user to sign in again.                        | JupyterLab terminal.                                      |

---

## Version matrix

NBI is tested against the JupyterLab and `jupyter_server` versions declared in [`pyproject.toml`](../pyproject.toml).

| NBI version | JupyterLab | jupyter_server | Python    |
| ----------- | ---------- | -------------- | --------- |
| 4.5.x       | 4.x        | 2.x            | 3.10–3.12 |
| 4.4.x       | 4.x        | 2.x            | 3.10–3.12 |
| 4.3.x       | 4.x        | 2.x            | 3.10–3.12 |

Upper bounds for `litellm`, `claude-agent-sdk`, `anthropic`, and `fastmcp` are not pinned in `pyproject.toml`. For production deployments, pin these in your image build:

```bash
pip install \
  "notebook-intelligence==4.5.*" \
  "litellm==1.62.*" \
  "claude-agent-sdk==0.x.*" \
  "anthropic==0.x.*" \
  "fastmcp==2.x.*"
```

Substitute the versions you've validated. The NBI test suite is currently TypeScript-only (`jlpm test`); end-to-end Python testing is a future work item.

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
