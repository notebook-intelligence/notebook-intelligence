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
  - `notebook_intelligence/mcp_manager.py`: MCP server management via the official [`mcp`](https://pypi.org/project/mcp/) SDK (replaced `fastmcp` in 5.0.0).
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

## Configuration mechanisms

NBI has three places a setting can be configured: a Python traitlet, an `NBI_*` environment variable, and an NBI config file (the Settings dialog is a friendly editor for the user-scope copy of the config file). They look similar but they exist for different audiences, and they override each other in a specific order.

Before adding a new knob at all, ask whether you actually need one. Hardcoded defaults are the simplest thing that works, and settings nobody ever changes shouldn't be settings — most candidates fail this gate.

### Who decides the value?

There are three audiences, and the audience picks the mechanism.

**The end user, via the Settings dialog.** They change their own preference and want it to stick across restarts without touching files. Examples: `default_chat_mode`, `store_github_access_token`.

→ Add a getter on `NBIConfig` (which reads `~/.jupyter/nbi/config.json`), surface the value in `/capabilities`, and add a control in `src/components/settings-panel.tsx`. Writes persist immediately.

**A server admin who runs `jupyter lab` directly.** They set an org-wide baseline in `jupyter_server_config.py` — typical for local installs and dev images. Examples: `disabled_providers`, `*_management_policy`.

→ Declare a traitlet on `NotebookIntelligence` in `extension.py`. The `help=` text shows up in `jupyter` CLI output and in generated config templates.

**A deployment admin running JupyterHub / KubeSpawner.** They need the same setting to vary by pod without rebuilding the container image. Editing Python at spawn time is awkward; env vars are how Kubernetes-style deployments wire per-pod policy. Examples: `NBI_ALLOW_GITHUB_SKILL_IMPORT`, `NBI_*_POLICY`.

→ Pair the traitlet with an `NBI_*` env var resolved at server startup. Use one of the fail-loud helpers in `extension.py`: `_resolve_bool_with_env`, `_resolve_policy_with_env`, or `_resolve_csv_appended`. A typo in the env var must surface at server startup, not silently fall through to the default — that's how deployment-admin debugging spirals into half-day investigations.

#### Rule: admin-policy flags must ship with an env var pair

If the setting is an admin-policy flag, the env var pairing isn't optional. A flag counts as an admin-policy flag when all three are true:

- Small shape: boolean, policy enum (e.g. `force-on` / `force-off` / `user-choice`), or short string-list.
- An admin (not the user) decides the value.
- The intent is to enforce, allow, or block a behavior.

`allow_github_skill_import` and every `*_management_policy` qualify. Numeric tuning knobs (`skills_manifest_interval`, `inline_completion_debouncer_delay`) don't — they aren't enforcement. Nested configs (`mcp_server_settings`, `claude_settings`) don't either — they're too big for a single env var and live in user `config.json` anyway.

The rule exists because every prior PR that shipped a policy traitlet without an env var pair got a follow-up adding the env var: deployment admins always need per-pod variance for policy flags. Doing the pair once is cheaper than doing it twice.

### How values override each other

When more than one source supplies the same value, NBI resolves them in this order (top wins):

```
1. NBI_* environment variable
2. user ~/.jupyter/nbi/config.json
3. env-prefix <env>/share/jupyter/nbi/config.json
4. traitlet in jupyter_server_config.py
5. hardcoded default
```

The mental model: items 2–5 are a chain of _defaults_, each more specific than the last (a default for everyone → installs → users → "what this user picked"). The env var is different — it's not a default, it's _policy enforcement_. A deployment admin sets an env var when they want to force a value regardless of what the user chose. That's why it sits on top of the user's own preference.

Worked example: a user unchecks "Store GitHub access token" in the Settings dialog. The toggle persists to user `config.json` and the value sticks across restarts — until an admin sets `NBI_STORE_GITHUB_ACCESS_TOKEN_POLICY=force-on` on the pod. After the next server restart, the user's `config.json` still says "off" but the value NBI uses is "on" — the env var wins.

That's the trap to know about: **if you expose a Settings-dialog control for a value that also has an admin override path, the user can see their save "stick" in the UI in-session and watch it silently revert on restart.** Two ways to avoid it:

1. **Don't put it in the dialog at all.** If admins are supposed to enforce the value, the user shouldn't be able to toggle it via the UI. Manual `config.json` editing is fine for niche admin-overridable knobs (issue #232 is an example).
2. **Use the policy triad and `settingLocks`.** For admin-enforceable settings, follow `*_management_policy`: the admin picks `force-on`, `force-off`, or `user-choice`. When the admin forces a value, the frontend's `settingLocks` shows a lock icon and disables the control — the user can see their choice is overridden rather than fighting an invisible policy.

### When override-semantics are wrong: the additivity carve-out

Most settings overwrite each other (one layer wins). A small number of settings _append_ across layers instead — each layer can add to the resolved value but none can remove from it. `additional_skipped_workspace_directories` (#232) is the only example today: traitlet + env var + env-prefix config + user config all merge into one combined list, deduped.

If you add a new additive setting, document the polarity explicitly in both the traitlet `help=` and the admin-guide entry. Readers assume override-semantics by default; additivity has to be called out or it's a trap.

### Adding a new admin policy is currently tedious

For historical reasons, every new admin policy has to be wired in seven places: the frontend type union (`src/api.ts`), the capabilities response builder (`extension.py`), the policy resolver, the handler class attribute, the README admin policies table, the admin-guide entry, and at least one pinning test. Miss one and the knob breaks in one direction without any error (e.g. the backend enforces but the UI doesn't lock the control, or vice versa).

The comment above `FEATURE_POLICY_SPEC` in `extension.py` lists every site — that's the closest thing to a guardrail today. A future registration helper that derived most of these from a single declaration would be a welcome cleanup.

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

The `jupyter labextension develop` command leaves a symlink behind. Run `jupyter labextension list` to find the labextensions directory, then remove the `@plmbr/notebook-intelligence` symlink there.

## Running tests

TypeScript unit tests:

```bash
jlpm test
```

Python tests (the `test` extra pulls in pytest, `pytest-timeout`, `psutil`, and `pyyaml`):

```bash
pip install -e ".[test]"
pytest tests/ -q
```

CI runs both suites on every push, in that order. Every test is capped at 30 seconds by `pytest-timeout`, so a hung subprocess or pipe fails the run instead of stalling the job.

## Linting

```bash
jlpm lint:check   # check, no fixes
jlpm lint         # check and auto-fix prettier, eslint, and stylelint
```

CI runs `lint:check`. Identifiers prefixed with `_` are treated as intentionally unused and excluded from the unused-vars rule.

### Automatic formatting

Two optional layers catch formatting drift before it reaches CI, so a common lint failure (an unformatted `.md`, `.ts`, or `.css` file) never makes it into a commit:

- **Pre-commit hook.** `jlpm install` registers a [Husky](https://typicode.github.io/husky/) hook (via a `postinstall` script) that runs `prettier --write` on staged files through [lint-staged](https://github.com/lint-staged/lint-staged). Staged files are formatted and re-staged automatically before the commit completes. Husky no-ops when there is no Git checkout (for example during `pip install` builds from an sdist) and when `HUSKY=0` is set, so it never affects packaging. To skip it for a single commit, use `git commit --no-verify`; to opt out entirely, run `HUSKY=0 jlpm install`.
- **Editor format-on-save.** `.editorconfig` and `.vscode/` settings format files with Prettier on save. Install the recommended VS Code extensions (you will be prompted, or see `.vscode/extensions.json`) to enable it.

Both layers only run Prettier, which is safe to apply automatically; ESLint and Stylelint rule violations are still left for you and CI to surface. Neither layer changes what CI enforces; both just move the formatting fix earlier. `jlpm lint` remains the manual catch-all.

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
