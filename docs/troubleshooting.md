# Troubleshooting

Common problems with copy-pasteable fixes. If your problem isn't listed, open an issue with the information requested in [CONTRIBUTING.md](../CONTRIBUTING.md#filing-a-good-bug-report).

## "Extension installed but I don't see anything in JupyterLab"

After `pip install notebook-intelligence`, restart JupyterLab. If a restart doesn't help, verify both halves of the extension are enabled:

```bash
jupyter server extension list   # look for "notebook_intelligence  enabled"
jupyter labextension list       # look for "@notebook-intelligence/notebook-intelligence ... enabled"
```

If either is disabled or missing:

```bash
jupyter server extension enable notebook_intelligence
pip install --force-reinstall notebook-intelligence   # if the labextension is missing
```

The chat sidebar appears as a left-rail icon in the JupyterLab UI. Click it to open the panel.

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

> Do not share the contents of `~/.jupyter/nbi/config.json` or `~/.jupyter/nbi/user-data.json` — they contain API keys or your encrypted GitHub token.

## "Skills reloaded" banner keeps appearing

NBI reloads the Claude SDK session whenever a skill changes on disk. If a script or editor frequently rewrites files under `~/.claude/skills/` (autoformatter, sync tool), it triggers the banner. Pause the writer or move the skill out of `~/.claude/skills/` while editing.

## Inline completion is too aggressive or too quiet

Tune the debounce delay in NBI Settings → Inline completion. Lower delays mean more requests, which means higher cost on paid providers. The default balances responsiveness against cost.

## Still stuck?

- Check [GitHub issues](https://github.com/notebook-intelligence/notebook-intelligence/issues) for similar reports.
- Open a new issue including the information listed in [CONTRIBUTING.md](../CONTRIBUTING.md#filing-a-good-bug-report).
