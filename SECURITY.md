# Security Policy

## Reporting a vulnerability

**Do not** open a public GitHub issue for security vulnerabilities.

Email the maintainer directly at **mbektasgh@outlook.com** with:

- A description of the vulnerability and the impact you observed.
- Steps to reproduce, including affected versions of NBI, JupyterLab, and Python.
- Any proof-of-concept code or logs (redact secrets).

Expect an acknowledgement within five business days. Once we confirm the issue, we'll work with you on a coordinated disclosure timeline.

## Supported versions

NBI follows semantic versioning starting with 4.0.0. Security fixes land on the latest minor release of the current major line. Earlier major lines are not actively maintained.

| Version | Supported          |
| ------- | ------------------ |
| 4.x     | Yes (latest minor) |
| < 4.0   | No                 |

## Scope

In-scope:

- The `notebook_intelligence` server extension and its HTTP handlers.
- The `@notebook-intelligence/notebook-intelligence` JupyterLab frontend.
- Built-in tools (`nbi-notebook-edit`, `nbi-notebook-execute`, `nbi-python-file-edit`, `nbi-file-edit`, `nbi-file-read`, `nbi-command-execute`).
- The Claude Skills import and managed-manifest reconciler.

Out of scope:

- Vulnerabilities in upstream dependencies (`litellm`, `anthropic`, `openai`, `ollama`, `claude-agent-sdk`, `fastmcp`) — please report those upstream. We will pick up patched releases when they ship.
- Vulnerabilities in MCP servers users install from third-party sources.
- Vulnerabilities in LLM providers themselves (data handling at OpenAI, Anthropic, GitHub Copilot, etc.).

## Security model

NBI is a **per-user** tool. The server extension runs inside the user's Jupyter Server process and inherits that user's permissions. Built-in tools shell out as the user, MCP stdio servers run as the user, and the Claude Code CLI inherits the user's environment. There is no privilege boundary between the extension and the user's account.

For multi-tenant deployments, see [`docs/admin-guide.md`](docs/admin-guide.md) for guidance on disabling features that are unsafe to expose without additional sandboxing (notably `nbi-command-execute` and `nbi-file-edit`).
