# Troubleshooting

Common problems with copy-pasteable fixes. If your problem isn't listed, open an issue with the information requested in [CONTRIBUTING.md](../CONTRIBUTING.md#filing-a-good-bug-report).

## "Extension installed but I don't see anything in JupyterLab"

After `pip install notebook-intelligence`, restart JupyterLab. If a restart doesn't help, verify both halves of the extension are enabled:

```bash
jupyter server extension list   # look for "notebook_intelligence  enabled"
jupyter labextension list       # look for "@plmbr/notebook-intelligence ... enabled"
```

If either is disabled or missing:

```bash
jupyter server extension enable notebook_intelligence
pip install --force-reinstall notebook-intelligence   # if the labextension is missing
```

The chat sidebar appears as a left-rail icon in the JupyterLab UI. Click it to open the panel.

## "Two Notebook Intelligence icons in the sidebar"

Seeing two NBI sidebar tabs with the same sparkle icon means two copies of the labextension are loaded at once. This happens after upgrading across the package rename: the labextension was renamed from `@notebook-intelligence/notebook-intelligence` to `@plmbr/notebook-intelligence` in the 5.0 line, and an upgrade installs the new one but can leave the old one behind. JupyterLab then loads both, and each registers its own sidebar tab.

Confirm it:

```bash
jupyter labextension list
```

If both `@notebook-intelligence/notebook-intelligence` and `@plmbr/notebook-intelligence` are listed as enabled, the first is the stale duplicate. Remove its directory (its path is shown in the list), checking both your environment prefix and the per-user location:

```bash
rm -rf <prefix>/share/jupyter/labextensions/@notebook-intelligence
rm -rf ~/.local/share/jupyter/labextensions/@notebook-intelligence
```

Restart JupyterLab and hard-refresh the browser; only `@plmbr/notebook-intelligence` should remain. If you would rather not delete files, `jupyter labextension disable @notebook-intelligence/notebook-intelligence` stops JupyterLab from loading the old extension reversibly. Note that `pip uninstall` does not remove the stale directory on its own, because the old labextension is no longer tracked by the current package.

## "GitHub login window doesn't open" or Copilot login does nothing

NBI uses GitHub's device-flow login. The server extension prints the URL and one-time code to the JupyterLab terminal. Look there first.

If your browser blocks the popup, copy the URL from the terminal output and paste it into a new tab.

If the device-flow request itself fails (timeout, network error), check that your network allows outbound HTTPS to `github.com` and `api.githubcopilot.com`. See [`PRIVACY.md`](../PRIVACY.md#egress-allowlist) for the full egress list.

## "It says 'no models available'"

NBI started successfully but the configured provider returned an empty model list. Check, in order:

1. **Provider auth** — open the NBI Settings dialog. For GitHub Copilot, sign in. For OpenAI-compatible or LiteLLM-compatible, paste an API key. For Ollama, ensure the daemon is running locally. For Claude mode, paste an Anthropic API key in the Claude tab.
2. **Custom Base URL** — if you set one, confirm it points at the provider's chat-completions endpoint and that it's reachable from the JupyterLab process.
3. **Provider gating** — if your admin disabled the provider via `disabled_providers`, the dropdown won't list its models. See [`admin-guide.md`](admin-guide.md#restricting-features-for-managed-deployments).
4. **Model refresh** — for Claude, click the refresh button in the Claude settings panel.

## "I'm getting a 401"

A 401 from the LLM provider almost always means an expired or invalid API key.

- **GitHub Copilot** — sign out and sign in again from NBI Settings → GitHub Copilot.
- **OpenAI-compatible, LiteLLM-compatible, or Claude** — paste a fresh key in NBI Settings under the respective provider.
- **Stored Copilot token corrupted** — delete `~/.jupyter/nbi/user-data.json` and sign in again.

A 401 from a managed-skills manifest fetch means `NBI_MANAGED_SKILLS_TOKEN` is missing or expired. The reconciler logs the failure and leaves installed managed skills in place.

## Claude mode does nothing or hangs on "Thinking…"

Claude mode requires the [Claude Code CLI](https://code.claude.com/) on the user's `PATH`. If the CLI is missing or fails to start, the chat sidebar hangs.

```bash
which claude   # should print a path
claude --version
```

If `claude` is installed in a non-default location, set the `NBI_CLAUDE_CLI_PATH` environment variable to its absolute path before starting JupyterLab.

If Claude mode worked previously but is now stuck, check the JupyterLab terminal for `claude-agent-sdk` errors. A failed-to-start agent thread is the usual culprit; restart JupyterLab to retry.

## MCP server crashes or tools missing in `@mcp`

MCP stdio servers run as subprocesses of the user's Jupyter Server. If a server crashes at startup:

1. Check the JupyterLab terminal for the server's stderr output.
2. Verify the `command` and `args` in `~/.jupyter/nbi/mcp.json` are correct and the binary is on `PATH`.
3. For `npx -y` servers, confirm Node.js is installed (`node --version`).
4. Use the **Reload MCP servers** action from NBI Settings → MCP after fixing the config — this re-runs discovery without restarting JupyterLab.

If the LLM is connected but tools aren't being called, confirm the model supports tool calling. All GitHub Copilot models do; for other providers, check the provider's docs.

## Where do logs live, and how do I turn on debug?

NBI does not have a separate log file. Server-side messages go to **stderr of the JupyterLab process** — the terminal where you ran `jupyter lab`.

To see more detail:

```bash
jupyter lab --debug
```

Frontend errors go to the **browser DevTools console** (`Cmd+Option+I` on macOS, `Ctrl+Shift+I` on Linux or Windows). Look for messages from `notebook-intelligence`.

For configuration inspection:

```bash
cat ~/.jupyter/nbi/config.json
cat ~/.jupyter/nbi/mcp.json
ls ~/.jupyter/nbi/rules/         # ruleset files
ls ~/.claude/skills/             # Claude skills
ls ~/.claude/projects/           # Claude session transcripts
```

If `CLAUDE_CONFIG_DIR` is set, the Claude CLI keeps its skills and session transcripts under `$CLAUDE_CONFIG_DIR` instead of `~/.claude`, and NBI reads from the same place.

> Do not share the contents of `~/.jupyter/nbi/config.json` or `~/.jupyter/nbi/user-data.json` — they contain API keys or your encrypted GitHub token.

## "Skills reloaded" banner keeps appearing

NBI reloads the Claude SDK session whenever a skill changes on disk. If a script or editor frequently rewrites files under `~/.claude/skills/` (autoformatter, sync tool), it triggers the banner. Pause the writer or move the skill out of `~/.claude/skills/` while editing.

## "My shell command output shows `<redacted>`"

The agent's shell-execute tools (`execute_command` and the embedded terminal) automatically redact values for env vars whose name matches sensitive substrings (`TOKEN`, `SECRET`, `API_KEY`, `PASSWORD`, `OAUTH`, `BEARER`, `COOKIE`, `JWT`, `ACCESS_KEY`, …) plus tokens with well-known credential prefixes (`ghp_`, `sk-ant-`, `xoxb-`, `AKIA`, …). This prevents a verbose command like `env`, `printenv`, or `git` with credential-helper tracing from pasting your `GITHUB_TOKEN` / `ANTHROPIC_API_KEY` into chat history.

If you're debugging a credential helper and need the raw value, set `NBI_DISABLE_OUTPUT_SCRUB=1` in the JupyterLab process env and restart. Keep it off in normal use; the redaction is the only line of defense between an LLM-driven command and your secrets going to the model provider.

## The model loses track of earlier messages in a long chat

Ask-mode requests are fitted to 80% of the active model's context window, so a long conversation is pruned rather than sent whole and rejected. Complete prior turns are dropped oldest-first; the system prompt, your workspace rules, any inline-edit source, and your newest message are kept. When something is dropped the reply carries a short context note saying so, so an unexplained gap in the model's memory is usually a different problem.

Pruning only happens when NBI knows the window. GitHub Copilot models report theirs through the models API. **OpenAI-compatible and LiteLLM-compatible providers do not**, so NBI uses whatever **Context window** you set on the model in Settings, and if you leave it blank it passes history through untouched rather than pruning against a guessed number. If a self-hosted or gateway-fronted model starts failing on context length in long chats, setting that value is the fix.

Agent-mode tool loops are not budgeted; this applies to ask mode and the built-in generation commands.

## Inline completion is too aggressive or too quiet

Tune the debounce delay in NBI Settings → Inline completion. Lower delays mean more requests, which means higher cost on paid providers. The default balances responsiveness against cost.

## Still stuck?

- Check [GitHub issues](https://github.com/plmbr/notebook-intelligence/issues) for similar reports.
- Open a new issue including the information listed in [CONTRIBUTING.md](../CONTRIBUTING.md#filing-a-good-bug-report).
