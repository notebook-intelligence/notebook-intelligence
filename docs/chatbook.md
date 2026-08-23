# Chatbook execution

Chatbook cells generate Python from a natural-language prompt and can run that Python in the Chatbook kernel. Generation uses Notebook Intelligence (`POST /notebook-intelligence/chatbook/generate`). Execution uses the same IPython kernel as the rest of the notebook: files, network, environment variables, `%pip`, and display all work, and PREFIX cells share state with later cells.

There is no per-cell sandbox. Isolation, when you need it, is the Jupyter kernel process itself (for example a JupyterHub user container).

## Execution modes

Configure these in Settings → **Chatbook**. The default is **Always confirm**.

| Mode             | Natural-language Run      | Executes generated Python?                                                          |
| ---------------- | ------------------------- | ----------------------------------------------------------------------------------- |
| Generate only    | Generate and store Python | Never from NL Run. Switch the cell to Py and run.                                   |
| Always confirm   | Generate, show a preview  | Only after **Run** on the confirm bar.                                              |
| Confirm if risky | Generate, static scan     | Auto-run when the scan is clean; confirm when it is risky or cannot parse the cell. |
| Auto-run         | Generate and execute      | Yes, no prompt.                                                                     |

The confirm bar names the mode that produced it and links to Settings → **Chatbook**, so the policy behind a prompt is always one click away.

Python-authored cells are unchanged in every mode: the user typed the source, so Run executes it.

An unchanged prompt that already executed in this session skips another confirm, except in **Generate only** (NL Run still never executes).

## Confirm-if-risky detection

The scan is a speed bump, not a security boundary. False positives (for example saving a CSV) are expected: click Run. False negatives are inevitable; data libraries can still exfiltrate.

**Static scan (always on for this mode):**

- `ast.parse` failure → risky (fail closed).
- Imports such as `os`, `subprocess`, `socket`, `requests`, `http`, `urllib`, `ctypes`, `importlib`, `pickle`, `webbrowser`.
- Calls such as `eval`, `exec`, `compile`, `__import__`, `Path.unlink` / `rmdir`, `shutil.rmtree` / `move`, `open(..., "w"|"a"|"x")`, `to_csv` / `to_parquet` / `to_sql`.
- IPython/shell: lines starting with `!`, and magics `%run`, `%env`, `%set_env`, `%pip`, `%conda`, `%%bash` / `%%sh` / `%%script`.

Optional **Also classify with the chat model** (off by default): a second JSON classifier may raise risk. A static hit always wins. Classifier timeout or invalid output confirms instead of auto-running. Mention and dynamic context are not sent to the classifier, only the generated Python.

## Admin cap

`NBI_CHATBOOK_MAX_EXECUTION_MODE` (traitlet `chatbook_max_execution_mode`) caps how permissive a user can be. Values, from safest to least: `generate-only`, `always-confirm`, `confirm-if-risky`, `auto-run` (default, no cap). A tenant can set `always-confirm` to hide Auto-run.

User preference is stored as `chatbook_execution_mode` in `~/.jupyter/nbi/config.json`.
