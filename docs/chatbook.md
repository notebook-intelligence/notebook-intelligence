# Chatbook execution

Chatbook cells generate code from a natural-language prompt and run that code in a **backend Jupyter kernelspec** you choose in Settings → **Chatbook**. Chatbook remains the notebook kernel (`chatbook`); it starts the selected kernelspec as a child and executes generated (or Cd-authored) code there. Language, syntax highlighting, and export follow that kernelspec.

Generation uses Notebook Intelligence (`POST /notebook-intelligence/chatbook/generate`). There is no per-cell sandbox. Isolation, when you need it, is the Jupyter kernel process itself (for example a JupyterHub user container).

## Backend kernel

The Chatbook setting **Execution kernel** lists installed Jupyter kernelspecs except `chatbook`. The default is `python3` when that spec exists, otherwise the first Python spec, otherwise the first non-chatbook spec. Change requires restarting open Chatbook notebooks so the child kernel is recreated.

Kernelspecs are listed and started through the Jupyter server's kernelspec manager, so a custom manager works unchanged. With [nb_conda_kernels](https://github.com/anaconda/nb_conda_kernels), for example, the Chatbook kernel is `Chatbook [conda env:base]` (`conda-base-chatbook`) and the backends are its names such as `conda-base-py`. Chatbook is recognized by its kernelspec language, `chatbook`, whatever the kernelspec is named.

Tab completion and contextual help are proxied to that child kernel. Interrupt uses Jupyter's message-mode interrupt so it reaches the child rather than only the Chatbook wrapper.

Cell badges show **NL** (natural language) and **Cd** (code). Code cells use the backend language for highlighting.

## Writing cells

Every cell is either a natural-language prompt (**NL**) or ordinary code (**Cd**), and the badge at the top of the cell shows which. The badge is a button: click it to switch that cell. `Ctrl J` switches the active cell and `Ctrl Shift J` switches every cell in the notebook. Both are literal `Ctrl`, including on macOS, where most JupyterLab bindings use `Cmd`. The notebook toolbar carries the same switch-all action and an export button, and ordinary notebooks get a button to convert them to a Chatbook.

Chatbook adds seven commands, all reachable from the command palette:

| Command                         | What it does                                                       |
| ------------------------------- | ------------------------------------------------------------------ |
| New Chatbook                    | Creates a notebook against the `chatbook` kernel.                  |
| Switch cell to code / to prompt | Same as `Ctrl J`.                                                  |
| Switch all cells                | Same as `Ctrl Shift J`.                                            |
| Show generated code             | Opens the code a prompt cell generated, which is otherwise hidden. |
| Refresh English representation  | Regenerates the English description of a code cell.                |
| Export as code notebook         | Writes a plain notebook for the backend language.                  |
| Convert notebook to Chatbook    | Writes a Chatbook copy of an ordinary notebook.                    |

**Running a code cell also sends it to the model.** The first time a **Cd** cell runs successfully, Chatbook asks the model for a one-line English description of that source and stores it in the cell's metadata, so the cell can be read as prose and switched back to **NL** later. It does not ask again on later runs, and it does not refresh the description when you edit the code: use **Refresh English representation** for that. This is worth knowing in a deployment where code is more sensitive than prompts, because it means hand-written code in a Chatbook reaches the model even under **Always confirm**.

## Context with @ mentions

Typing `@` in a prompt cell opens a menu of context to attach. The built-in **Files & folders** provider browses the Jupyter root, skipping dotfiles, `__pycache__`, and `node_modules`; a selected file is read and truncated at 16,000 characters. Extensions can register providers of their own, which appear in the same menu under their own root (see [`chatbook-extensions.md`](chatbook-extensions.md)).

A mention ends at the first whitespace or `@`, so a path that contains either is written in double quotes, `@file:"data/my notes.md"`; the menu inserts that form for you. A quoted path is taken exactly as written and cannot contain a double quote or a line break, so a name with a double quote can only be mentioned when it needs no quoting, and a name that itself begins and ends with a double quote cannot be mentioned at all.

Sentence punctuation typed right after an unquoted file or folder mention (`.` `,` `;` `:` `!` `?` and `…`, closing brackets and quotes, and their full-width forms) is not part of the path, so `Summarize @file:data/README.md, then chart it` refers to `data/README.md`. The name as written is tried first, then with up to five of those characters removed one at a time, and the first name that exists is used even if it cannot be read. So a file whose real name ends in punctuation still resolves, and a shorter name is never reached past one that exists. Other characters, such as `+` or `-`, are always part of the path.

Mentions work only in prompt cells. A prompt that carries one is regenerated every run rather than reusing the previously approved code, because the mentioned content may have changed since.

## Convert a notebook to a Chatbook

**Convert notebook to Chatbook** is the reverse of [export](#export-as-a-code-notebook). It appears as a toolbar button on ordinary notebooks and in the command palette, and writes a Chatbook copy next to the original, named after it with `-chatbook` appended (`analysis.ipynb` becomes `analysis-chatbook.ipynb`, and a number is added if that name is taken). The copy opens straight away, and the original is left untouched. It converts what is in the editor, so unsaved changes are included.

Every code cell becomes a **Cd** cell and runs exactly as written. The exception is a Chatbook whose kernel was switched to another one: its natural-language cells that have run, and not been edited since, still hold their prompts and stay natural language. Any other natural-language cell in it would become a code cell holding English, so switch such a Chatbook back to the Chatbook kernel rather than converting it. Everything else is kept: markdown and raw cells, outputs, execution counts, attachments, and other notebook metadata; only the kernelspec and language info become Chatbook's. Conversion itself sends nothing to the model. As with any **Cd** cell, the first time a converted cell without an English description runs successfully, its code is sent to the model for a one-line description. A description a cell already carries is kept only when it is known to describe that exact code, as for a Chatbook that was exported and is converted back.

Chatbook runs code in the one backend kernel chosen in Settings → **Chatbook**, so conversion compares the notebook's language with it first. A notebook in the same language converts, with a note when its kernel differs from the backend, and so does a notebook that records no language. A notebook in another language (an R notebook while the backend is Python) is not converted, because every code cell would fail; the dialog explains why and, when a kernel for that language is installed, offers to open the Chatbook settings. Conversion never changes that setting itself, since it applies to every Chatbook. When the notebook names its kernel, that name is recorded in the copy's metadata under `nbi.chatbook.sourceKernel`.

## Export as a code notebook

**Export as code notebook** writes a plain notebook for the backend language next to the original, named after it with the language appended (`analysis.ipynb` becomes `analysis-python.ipynb`, and a number is added if that name is taken). The Chatbook itself is left untouched.

Prompt cells become their generated code. A prompt cell whose code never ran, or whose prompt changed since it last ran, is written as line comments instead, so an export never passes off code as having been executed when it was not. A code cell switched to natural language exports its own code, as any code cell does, while its English side is empty or is still the description of that code. Once either side changes, it is written as comments too; after editing the code, **Refresh English representation** brings the description back in step. The exported notebook's `kernelspec` and `language_info` are rewritten to the backend kernel. Export fails if the backend language has no line-comment syntax Chatbook knows, since there would be no way to write those cells safely.

## What the notebook file holds

Chatbook stores its state in cell metadata under `nbi.chatbook`: the prompt, the generated code, the code source, hashes of the prompt and context, and when generation happened. A Chatbook you share therefore carries the generated code and the model's English descriptions of your own code, not just the prompts.

Treat a Chatbook from someone else as untrusted input. The persisted generated code is not re-derived on open, so it is whatever the file says it is, which is why approved-code reuse is scoped to the current session rather than read back from the file.

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

Re-running a prompt whose generated code was already approved (via Run on the confirm bar, or under Auto-run / a clean Confirm-if-risky scan) can skip another confirm and execute that code directly. This applies within the session only, and only when the prompt is self-contained: a prompt carrying an `@` mention, a registered context provider, or applicable rules is regenerated instead, because its inputs may have changed. Even then the kernel reuses the approved code only when regeneration produces exactly the same code; anything else reopens the bar.

## Confirm-if-risky detection

The scan is a speed bump, not a security boundary. False positives (for example saving a CSV) are expected: click Run. False negatives are inevitable; data libraries can still exfiltrate.

**Static scan (always on for this mode when the backend language is Python):**

- `ast.parse` failure → risky (fail closed).
- Imports such as `os`, `subprocess`, `socket`, `requests`, `http`, `urllib`, `ctypes`, `importlib`, `pickle`, `webbrowser`.
- Calls such as `eval`, `exec`, `compile`, `__import__`, `Path.unlink` / `rmdir` / `remove` / `rename`, `shutil.rmtree` / `move`, `open(..., "w"|"a"|"x")`, `to_csv` / `to_parquet` / `to_sql`.
- Calls that reach a shell through IPython: `.system()`, `.getoutput()`, `.run_line_magic()`, `.run_cell_magic()`, `.run_cell()`.
- IPython/shell: lines starting with `!`, and magics `%run`, `%env`, `%set_env`, `%pip`, `%conda`, `%sx`, `%system`, `%%bash` / `%%sh` / `%%script`.

For other backend languages the static scan fails closed (treats the cell as risky) so Confirm if risky still prompts. Optional **Also classify with the chat model** (off by default): a second JSON classifier may raise risk. A static hit always wins. Classifier timeout or invalid output confirms instead of auto-running. Mention and dynamic context are not sent to the classifier, only the generated code.

## Enabling and disabling

Chatbook is on by default. Users do not set an environment variable. An admin can turn it off with `NBI_CHATBOOK_POLICY=force-off` (traitlet `chatbook_policy`), the same admin policy shape NBI uses for its other features. That hides the Chatbook kernelspec from the launcher and kernel picker, hides the Settings → Chatbook tab and Chatbook commands, and returns HTTP 403 from the generate and mention APIs. See [Disabling Chatbook natural-language notebooks](admin-guide.md#disabling-chatbook-natural-language-notebooks) in the admin guide.

## Admin cap

`NBI_CHATBOOK_MAX_EXECUTION_MODE` (traitlet `chatbook_max_execution_mode`) caps how permissive a user can be. Values, from safest to least: `always-confirm`, `confirm-if-risky`, `auto-run` (default, no cap). A tenant can set `always-confirm` to hide Auto-run.

The cap applies to natural-language generation. A cell explicitly switched to **Cd** is user-authored code and executes like a normal notebook code cell, without generation, scanning, or confirmation. It is not a sandbox or a restriction on code the user can run directly.

User preference is stored as `chatbook_execution_mode`, `chatbook_backend_kernel`, and `chatbook_llm_danger_scan` in `~/.jupyter/nbi/config.json`.
