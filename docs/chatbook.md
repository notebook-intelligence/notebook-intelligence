# Chatbook execution

Chatbook cells generate code from a natural-language prompt and run that code in a **backend Jupyter kernelspec** you choose in Settings → **Chatbook**. Chatbook remains the notebook kernel (`chatbook`); it starts the selected kernelspec as a child and executes generated (or Cd-authored) code there. Language, syntax highlighting, and export follow that kernelspec.

Generation uses Notebook Intelligence (`POST /notebook-intelligence/chatbook/generate`). There is no per-cell sandbox. Isolation, when you need it, is the Jupyter kernel process itself (for example a JupyterHub user container).

## Backend kernel

The Chatbook setting **Execution kernel** lists installed Jupyter kernelspecs except `chatbook`. The default is `python3` when that spec exists, otherwise the first Python spec, otherwise the first non-chatbook spec. Change requires restarting open Chatbook notebooks so the child kernel is recreated.

Cell badges show **NL** (natural language) and **Cd** (code). Code cells use the backend language for highlighting.

## Generation backend

Chatbook follows NBI's active mode:

- Default mode uses the chat model configured under **General**.
- Claude mode uses the model, API key, and base URL configured under
  **Claude**.
- ACP mode uses a dedicated instance of the configured ACP agent and sends each
  request through a fresh session, separate from the chat sidebar conversation.
  The Chatbook agent is always launched without full access, denies agent tool
  permission requests, and is instructed to generate only from the supplied
  notebook context.

## Execution modes

Configure these in Settings → **Chatbook**. The default is **Always confirm**.

| Mode             | Natural-language Run     | Executes generated code?                                                            |
| ---------------- | ------------------------ | ----------------------------------------------------------------------------------- |
| Always confirm   | Generate, show a preview | Only after **Run** on the confirm bar.                                              |
| Confirm if risky | Generate, static scan    | Auto-run when the scan is clean; confirm when it is risky or cannot parse the cell. |
| Auto-run         | Generate and execute     | Yes, no prompt.                                                                     |

The confirm bar names the mode that produced it and links to Settings → **Chatbook**, so the policy behind a prompt is always one click away.

Code-authored cells are unchanged in every mode: the user typed the source, so Run executes it.

An unchanged prompt that already executed in this session skips another confirm.

## Confirm-if-risky detection

The scan is a speed bump, not a security boundary. False positives (for example saving a CSV) are expected: click Run. False negatives are inevitable; data libraries can still exfiltrate.

**Static scan (always on for this mode when the backend language is Python):**

- `ast.parse` failure → risky (fail closed).
- Imports such as `os`, `subprocess`, `socket`, `requests`, `http`, `urllib`, `ctypes`, `importlib`, `pickle`, `webbrowser`.
- Calls such as `eval`, `exec`, `compile`, `__import__`, `Path.unlink` / `rmdir`, `shutil.rmtree` / `move`, `open(..., "w"|"a"|"x")`, `to_csv` / `to_parquet` / `to_sql`.
- IPython/shell: lines starting with `!`, and magics `%run`, `%env`, `%set_env`, `%pip`, `%conda`, `%%bash` / `%%sh` / `%%script`.

For other backend languages the static scan fails closed (treats the cell as risky) so Confirm if risky still prompts. Optional **Also classify with the chat model** (off by default): a second JSON classifier may raise risk. A static hit always wins. Classifier timeout or invalid output confirms instead of auto-running. Mention and dynamic context are not sent to the classifier, only the generated code.

## Enabling and disabling

Chatbook is on by default. Users do not set an environment variable. An admin can turn it off with `NBI_ENABLE_CHATBOOK=false` (traitlet `enable_chatbook`). That hides the Chatbook kernelspec from the launcher and kernel picker, hides the Settings → Chatbook tab and Chatbook commands, and returns HTTP 403 from the generate and mention APIs.

## Admin cap

`NBI_CHATBOOK_MAX_EXECUTION_MODE` (traitlet `chatbook_max_execution_mode`) caps how permissive a user can be. Values, from safest to least: `always-confirm`, `confirm-if-risky`, `auto-run` (default, no cap). A tenant can set `always-confirm` to hide Auto-run.

The cap applies to natural-language generation. A cell explicitly switched to **Cd** is user-authored code and executes like a normal notebook code cell, without generation, scanning, or confirmation. It is not a sandbox or a restriction on code the user can run directly.

User preference is stored as `chatbook_execution_mode` and `chatbook_backend_kernel` in `~/.jupyter/nbi/config.json`.
