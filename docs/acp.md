# ACP agent mode

ACP mode drives an external coding agent over the [Agent Client Protocol](https://agentclientprotocol.com/) instead of using NBI's own chat path. It is experimental, off by default, and mutually exclusive with Claude mode: enabling one disables the other.

OpenAI Codex, through the `codex-acp` adapter, is the only agent type available today.

For the admin side of this feature (the two policies, the approval posture NBI pins, credential containment, and the known limitations) see [Gating the experimental ACP agent](admin-guide.md#gating-the-experimental-acp-agent-378). This page is about using it.

## Turning it on

The **ACP** tab in Settings is hidden until an admin allows the mode, because `acp_mode_policy` defaults to `force-off`. If you do not see the tab, that is why; ask for `NBI_ACP_MODE_POLICY=user-choice`.

With the tab visible, pick the agent type, then set the chat model, API key, and base URL. An administrator must first install the tested `@zed-industries/codex-acp@0.16.0` in a trusted location outside the Jupyter workspace. NBI finds its `codex-acp` executable through absolute directories on the server's `PATH`, or uses an absolute path to the installed adapter configured through `NBI_ACP_AGENT_COMMAND`. Do not use package runners such as `npx`, `npm exec`, `pnpm dlx`, or `bunx`, or an `env` wrapper, for that override. NBI does not download an adapter at startup or verify its version; the administrator maintains the installation. Settings → Status reports a missing or invalid adapter command or declared script interpreter. See the [installation and migration guidance](admin-guide.md#gating-the-experimental-acp-agent-378), including a concrete install command, home-directory workspace layouts, and the requirements for shims and Windows with Python 3.10 or 3.11. Empty or whitespace-only `NBI_ACP_AGENT_COMMAND` values are invalid; unset the variable to restore default discovery.

## What a turn looks like

The agent runs as a subprocess and does its own work: it reads and writes files itself, and runs its own commands. NBI deliberately does not serve it the client filesystem or terminal methods, so the agent cannot borrow the Jupyter server process's reach; it is confined to whatever sandbox the agent applies to itself, which is what the admin policies pin.

When the agent wants to do something it considers worth asking about, NBI shows an approval card naming the command and the reason the agent gave. Approving grants a lasting permission for that kind of action in the session, not a one-off, so a later action of the same kind may not ask again. The agent decides what to ask about; NBI shows what it is asked, and cannot promise that everything the agent does passes through a card.

Previous sessions are listed by the history control, and picking one resumes it through the agent's own session storage rather than NBI's.

## Limits worth knowing

- **Agent commands use a filtered `PATH` by default.** Workspace directories, including a project's `.venv/bin`, are removed. Commands such as `python` or `pytest` may therefore select another installation or be unavailable. An administrator's explicit adapter override retains the full server `PATH` and requires trusting its contents.
- **Startup is bounded.** An agent has 60 seconds to spawn, initialize, authenticate, and create a session. If this takes too long, the failure reads `ACP agent did not start in time`. Check the adapter installation and authentication before retrying.
- **A turn is capped at 30 minutes**, after which it fails with an agent response timeout.
- **Session listing is optional.** Resuming depends on the agent advertising the capability, and listing is an optional protocol extension. An agent that implements neither reports that it does not support listing sessions; this is the agent's limitation, not a misconfiguration.
- **Chatbook uses ACP differently.** When ACP mode is active, Chatbook generation runs in a separate session with full access forced off, no MCP servers, and permission requests denied, so a Chatbook cell cannot approve its way into running commands.

## Where its state lives

When you configure an API key, NBI points the agent at an isolated `CODEX_HOME` under `~/.jupyter/nbi/codex-home/` so it reads neither the workspace's nor your personal `~/.codex` configuration. Its session files live there. Signing in through the agent's own ChatGPT flow instead uses your `~/.codex` as usual.

See [`PRIVACY.md`](../PRIVACY.md) for what leaves the machine in this mode.
