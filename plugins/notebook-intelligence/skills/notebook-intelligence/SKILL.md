---
name: notebook-intelligence
description: Use when the user wants to install, upgrade, configure, inspect, or troubleshoot the Notebook Intelligence (NBI) AI coding assistant for JupyterLab. Covers Python and JupyterLab environment selection, provider setup, extension validation, and common startup or connectivity failures.
---

# Notebook Intelligence

Help the user operate Notebook Intelligence (NBI), the JupyterLab extension from
`plmbr/notebook-intelligence`.

## Scope

Use this skill for:

- installing or upgrading NBI;
- checking whether the server and frontend extensions are enabled;
- configuring GitHub Copilot, OpenAI-compatible, LiteLLM-compatible, Ollama, or
  Claude Code providers;
- diagnosing blank panels, missing commands, authentication failures, model
  connectivity problems, and extension version mismatches;
- source-development setup for this repository.

This Codex plugin is an operational assistant. It does not itself expose a
Jupyter kernel, notebook editor, or NBI's Jupyter UI as a Codex tool. Never
claim that installing this plugin installs or starts the JupyterLab extension.

## Workflow

### 1. Identify the intended environment

Before installing or changing packages, establish which Python environment
owns the JupyterLab server the user intends to run.

Inspect, as applicable:

```bash
python --version
python -m pip --version
python -m pip show notebook-intelligence jupyterlab
command -v python
command -v jupyter
jupyter lab --version
```

Require Python 3.10 or newer and JupyterLab 4.x. If `python` and `jupyter`
resolve to different environments, stop and explain the mismatch before
installing anything. Prefer a fresh virtualenv or conda environment.

### 2. Install or upgrade deliberately

For a normal PyPI installation, use the selected interpreter:

```bash
python -m pip install --upgrade notebook-intelligence
```

Do not add `--user`, change conda environments, or replace an existing
environment unless the user requested it. After installation, restart the
JupyterLab server; a browser refresh alone does not reload the server
extension.

For source development, follow the repository's `CONTRIBUTING.md` and use its
declared Yarn/Jupyter build commands. Do not invent a development workflow from
the PyPI install path.

### 3. Configure the provider safely

Use NBI Settings in JupyterLab for provider selection. Supported paths include
GitHub Copilot, OpenAI-compatible or LiteLLM-compatible endpoints, Ollama, and
Claude Code mode.

- Never print, commit, or persist API keys in project files.
- Redact tokens and credential-bearing URLs from diagnostics.
- For Claude mode, validate the executable named by `NBI_CLAUDE_CLI_PATH` when
  the override is set; otherwise verify that the Claude Code CLI is on `PATH`.
- For Ollama, verify the configured daemon endpoint and model availability.
- For OpenAI-compatible endpoints, confirm base URL, model name, and
  authentication separately.

### 4. Verify the installation

Use the same environment chosen in step 1:

```bash
python -m pip show notebook-intelligence
jupyter server extension list
jupyter labextension list
```

Confirm that the Python package is installed and that both the NBI server
extension and the `@plmbr/notebook-intelligence` frontend extension are
reported without errors. Then start or restart JupyterLab and verify that the
NBI sidebar icon and Settings panel appear.

### 5. Diagnose before changing

Gather the smallest relevant evidence first:

- exact NBI, Python, and JupyterLab versions;
- the selected provider and model name, with secrets removed;
- server-extension and labextension status;
- the first relevant JupyterLab server error;
- browser-console errors only when the failure is frontend-specific.

Use the project's troubleshooting guide at
`https://github.com/plmbr/notebook-intelligence/blob/main/docs/troubleshooting.md`
as the primary reference. Avoid broad reinstalls until the failing layer is
identified.

## Response contract

End with:

1. the environment that was inspected or changed;
2. the installed NBI and JupyterLab versions;
3. the verification result for server and frontend extensions;
4. any remaining manual action, such as restarting JupyterLab or completing a
   provider login.

If commands were not run, label the steps as instructions rather than completed
results.
