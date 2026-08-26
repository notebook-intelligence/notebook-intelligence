# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from time import time
import json
from notebook_intelligence.api import ChatResponse, MarkdownPartData, Toolset
import logging
import notebook_intelligence.api as nbapi
from notebook_intelligence.api import BuiltinToolset
from pathlib import Path
import subprocess
import shlex
import os
import fnmatch

from notebook_intelligence.util import (
    get_jupyter_root_dir,
    redact_env_secrets,
    safe_jupyter_path,
)

# Backwards-compatible alias: this used to live here as a private helper.
# Lifted to util.py so Claude-mode tool registrations can share the same
# gate without an import cycle. Existing call sites in this module keep
# working through the alias; new code should call ``safe_jupyter_path``
# directly.
_get_safe_path = safe_jupyter_path


def _glob_pattern_escapes(pattern: str) -> bool:
    """True if a glob pattern would range outside the search root.

    ``Path.glob`` evaluates ``..`` segments (and rejects absolute
    patterns only with an unhelpful error), so a pattern like
    ``../secret.txt`` or ``**/../secret.txt`` makes ``glob`` enumerate
    and stat paths outside ``jupyter_root_dir`` before the per-match
    ``safe_jupyter_path`` gate can skip them. That both defeats the
    workspace confinement during enumeration and leaks outside-file
    existence through the differing "no files found" vs "found" replies.
    Reject absolute patterns and any ``..`` component up front so the
    boundary holds during enumeration too.
    """
    norm = pattern.replace("\\", "/")
    if norm.startswith("/"):
        return True
    return ".." in norm.split("/")


def _explicit_descendable(entry, root_dir):
    """Whether an explicit (non-``**``) path segment may recurse into ``entry``.

    Real directories are descendable. A symlinked directory is descendable
    only when it resolves back inside ``root_dir`` -- an outbound symlink is
    refused so its target tree is never enumerated -- which mirrors how
    ``Path.glob`` follows an explicit path component through an in-workspace
    symlink but the ``safe_jupyter_path`` gate rejects an outbound one.
    """
    try:
        if entry.is_symlink():
            # Resolve first and reject an outbound target before probing it as
            # a directory, so an outbound symlink is never stat-followed past
            # the workspace boundary during enumeration.
            real = os.path.realpath(entry.path)
            try:
                Path(real).relative_to(root_dir)
            except ValueError:
                return False  # outbound symlinked path
            # In-workspace target: safe to confirm it is a directory.
            return os.path.isdir(real)
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _iter_pattern_matches(base_dir, segments, root_dir):
    """Yield paths under ``base_dir`` matching the glob ``segments``.

    A drop-in, sandbox-safe replacement for ``Path.glob`` enumeration:

    * It recurses only as deep as the pattern requires (a non-recursive
      pattern like ``*.py`` or ``foo.txt`` scans a single directory level,
      not the whole subtree), so a simple search stays O(entries at that
      depth) rather than walking every descendant.
    * ``**`` recursion descends real directories only and never crosses a
      symlink, exactly like ``Path.glob``; that also makes it cycle-free.
      An explicit path component (e.g. ``link/*.txt``) may follow an
      in-workspace symlinked directory but never an outbound one (see
      ``_explicit_descendable``), so an outside tree is never enumerated,
      and explicit depth is bounded by the finite pattern.
    * Each path segment is matched with ``fnmatch.fnmatch``, which honors the
      full glob syntax ``pathlib`` supports -- ``*``, ``?`` and character
      classes such as ``[co]`` / ``[!x]`` -- with the platform-default case
      sensitivity ``Path.glob`` uses; ``**`` spans zero or more levels.

    Matches are yielded regardless of type; the caller resolves each one
    through ``safe_jupyter_path`` and applies its own ``is_file`` filter.
    """
    if not segments:
        return
    seg, rest = segments[0], segments[1:]
    try:
        entries = list(os.scandir(base_dir))
    except OSError:
        return
    if seg == "**":
        # ``**`` matches zero or more directory levels, following real
        # directories only (Path.glob does not cross symlinks for ``**``).
        if rest:
            yield from _iter_pattern_matches(base_dir, rest, root_dir)
        else:
            yield Path(base_dir)
        for entry in entries:
            try:
                is_real_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_real_dir:
                yield from _iter_pattern_matches(entry.path, segments, root_dir)
        return
    for entry in entries:
        if not fnmatch.fnmatch(entry.name, seg):
            continue
        if rest:
            if _explicit_descendable(entry, root_dir):
                yield from _iter_pattern_matches(entry.path, rest, root_dir)
        else:
            yield Path(entry.path)


log = logging.getLogger(__name__)

APPROX_BYTES_PER_OUTPUT_TOKEN = 4
DEFAULT_READ_FILE_MAX_OUTPUT_TOKENS = 10_000
DEFAULT_CELL_OUTPUT_MAX_OUTPUT_TOKENS = 10_000
READ_FILE_TRUNCATION_MARKER_TEMPLATE = (
    "\n[output truncated within line {line} column {column}]"
)


def _truncate_utf8_to_byte_budget(text: str, byte_budget: int) -> str:
    """Trim text to a UTF-8 byte budget without returning invalid Unicode."""
    if byte_budget <= 0 or not text:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_budget:
        return text
    return encoded[:byte_budget].decode("utf-8", errors="ignore")


def _read_position_after_content(
    start_line: int,
    content: str,
) -> tuple[int, int]:
    """Return the next unread 1-based line/column after ``content``."""
    if not content:
        return start_line, 1

    newline_count = content.count("\n")
    line = start_line + newline_count

    last_newline = content.rfind("\n")
    if last_newline == -1:
        column = len(content) + 1
    else:
        column = len(content) - last_newline

    return line, column


def _read_file_truncation_marker(start_line: int, content: str) -> str:
    line, column = _read_position_after_content(start_line, content)
    return READ_FILE_TRUNCATION_MARKER_TEMPLATE.format(
        line=line,
        column=column,
    )


def _truncate_read_file_output(
    prefix: str,
    content: str,
    start_line: int,
    max_output_tokens: int,
) -> str:
    """Apply the read-file output cap to prefix+content only; marker is extra."""
    byte_budget = max(0, max_output_tokens) * APPROX_BYTES_PER_OUTPUT_TOKEN
    prefix_bytes = len(prefix.encode("utf-8"))
    content_bytes = len(content.encode("utf-8"))

    if prefix_bytes + content_bytes <= byte_budget:
        return prefix + content

    if byte_budget <= prefix_bytes:
        truncated_prefix = _truncate_utf8_to_byte_budget(prefix, byte_budget)
        marker = _read_file_truncation_marker(start_line, "")
        return truncated_prefix + marker

    content_budget = byte_budget - prefix_bytes
    truncated_content = _truncate_utf8_to_byte_budget(content, content_budget)
    marker = _read_file_truncation_marker(start_line, truncated_content)
    return prefix + truncated_content + marker


def _bounded_cell_output(
    value,
    offset: int = 0,
    limit: int = None,
    max_output_tokens: int = DEFAULT_CELL_OUTPUT_MAX_OUTPUT_TOKENS,
) -> str:
    """Return a resumable cell-output page within an approximate token cap."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return "Error: offset must be a non-negative integer"
    if (
        limit is not None
        and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1)
    ):
        return "Error: limit must be a positive integer"

    output = str(value)
    start = min(offset, len(output))
    end = len(output) if limit is None else min(len(output), start + limit)
    page = output[start:end]
    pagination_marker = ""
    if start > 0 or end < len(output):
        range_note = f"cell output characters {start}-{end} of {len(output)}"
        if end < len(output):
            range_note += f"; request offset={end} to continue"
        pagination_marker = f"\n\n[{range_note}.]"

    byte_budget = max(0, max_output_tokens) * APPROX_BYTES_PER_OUTPUT_TOKEN
    normalized_page = page.encode("utf-8", errors="replace").decode("utf-8")
    if len((normalized_page + pagination_marker).encode("utf-8")) <= byte_budget:
        return normalized_page + pagination_marker

    truncation_marker = (
        f"\n\n[cell output truncated; request offset={offset} with a smaller limit.]"
    )
    content_budget = max(
        0,
        byte_budget - len(truncation_marker.encode("utf-8")),
    )
    head_budget = (content_budget * 3) // 4
    tail_budget = content_budget - head_budget
    head = _truncate_utf8_to_byte_budget(normalized_page, head_budget)
    tail_bytes = (
        normalized_page.encode("utf-8")[-tail_budget:]
        if tail_budget > 0
        else b""
    )
    tail = tail_bytes.decode("utf-8", errors="ignore")
    return head + truncation_marker + tail


@nbapi.auto_approve
@nbapi.tool
async def list_available_notebook_kernels(**args) -> str:
    """Lists Jupyter kernels available in the current frontend environment.

    Use this before creating a notebook when you need a kernel that may differ
    from the current notebook context.
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command(
        'notebook-intelligence:list-available-notebook-kernels',
        {}
    )
    return json.dumps(ui_cmd_response)


@nbapi.auto_approve
@nbapi.tool
async def create_new_notebook(
    language: str = "python",
    kernel_name: str = "",
    **args,
) -> str:
    """Creates a new empty notebook.

    Args:
        language: Programming language for the notebook kernel, e.g. python or r.
        kernel_name: Explicit Jupyter kernel name to use when creating the notebook.
    """
    response = args["response"]
    request = args.get("request")
    effective_language = language or getattr(request, "language", "") or "python"
    effective_kernel_name = (
        kernel_name
        or getattr(request, "kernel_name", "")
        or ""
    )
    ui_cmd_response = await response.run_ui_command(
        'notebook-intelligence:create-new-notebook',
        {
            'code': '',
            'language': effective_language,
            'kernelName': effective_kernel_name,
        }
    )
    file_path = ui_cmd_response['path']

    return f"Created new notebook at {file_path}"

@nbapi.auto_approve
@nbapi.tool
async def rename_notebook(new_name: str, **args) -> str: 
    """Renames the notebook.
    Args:
        new_name: New name for the notebook
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:rename-notebook', {'newName': new_name})
    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def add_markdown_cell(source: str, **args) -> str:
    """Adds a markdown cell to notebook.
    Args:
        source: Markdown source
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-markdown-cell-to-active-notebook', {'source': source})
    cell_index = ui_cmd_response.get("cellIndex") if isinstance(ui_cmd_response, dict) else None
    if isinstance(cell_index, int):
        return f"Added markdown cell at index {cell_index}"
    return "Added markdown cell to notebook"

@nbapi.auto_approve
@nbapi.tool
async def add_code_cell(source: str, **args) -> str:
    """Adds a code cell to notebook.
    Args:
        source: Code source for the notebook's current language
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-active-notebook', {'source': source})
    cell_index = ui_cmd_response.get("cellIndex") if isinstance(ui_cmd_response, dict) else None
    if isinstance(cell_index, int):
        return f"Added code cell at index {cell_index}"
    return "Added code cell to notebook"

@nbapi.auto_approve
@nbapi.tool
async def get_number_of_cells(**args) -> str:
    """Get number of cells for the active notebook.
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-number-of-cells', {})

    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def get_cell_type_and_source(cell_index: int, **args) -> str:
    """Get cell type and source for the cell at zero-based index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-type-and-source', {"cellIndex": cell_index })

    return str(ui_cmd_response)


@nbapi.auto_approve
@nbapi.tool
async def get_cell_output(
    cell_index: int,
    offset: int = 0,
    limit: int = None,
    **args,
) -> str:
    """Get cell output for the cell at zero-based index for the active notebook.

    Args:
        cell_index: Zero based cell index
        offset: Optional zero-based character offset into the output
        limit: Optional maximum number of characters to return
    """
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        return "Error: offset must be a non-negative integer"
    if (
        limit is not None
        and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1)
    ):
        return "Error: limit must be a positive integer"
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-output', {"cellIndex": cell_index})

    return _bounded_cell_output(ui_cmd_response, offset=offset, limit=limit)

@nbapi.auto_approve
@nbapi.tool
async def set_cell_type_and_source(cell_index: int, cell_type: str, source: str, **args) -> str:
    """Set cell type and source for the cell at zero-based index for the active notebook.

    Args:
        cell_index: Zero based cell index
        cell_type: Cell type (code or markdown)
        source: Markdown or code source
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:set-cell-type-and-source', {"cellIndex": cell_index, "cellType": cell_type, "source": source})

    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def delete_cell(cell_index: int, **args) -> str:
    """Delete the cell at zero-based index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = args["response"]

    ui_cmd_response = await response.run_ui_command('notebook-intelligence:delete-cell-at-index', {"cellIndex": cell_index})

    return f"Deleted the cell at index: {cell_index}"

@nbapi.auto_approve
@nbapi.tool
async def insert_cell(cell_index: int, cell_type: str, source: str, **args) -> str:
    """Insert cell with type and source at zero-based index for the active notebook.

    Args:
        cell_index: Zero based cell index
        cell_type: Cell type (code or markdown)
        source: Markdown or code source
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:insert-cell-at-index', {"cellIndex": cell_index, "cellType": cell_type, "source": source})

    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def run_cell(cell_index: int, **args) -> str:
    """Run the cell at zero-based index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = args["response"]

    ui_cmd_response = await response.run_ui_command('notebook-intelligence:run-cell-at-index', {"cellIndex": cell_index})

    return f"Ran the cell at index: {cell_index}"

@nbapi.auto_approve
@nbapi.tool
async def save_notebook(**args) -> str:
    """Save the changes in active notebook to disk.
    """
    response: ChatResponse = args["response"]
    ui_cmd_response = await response.run_ui_command('docmanager:save')

    return f"Saved the notebook"

@nbapi.auto_approve
@nbapi.tool
async def create_new_python_file(code: str, **args) -> str:
    """Creates a new Python file.
    Args:
        code: Python code source
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:create-new-file', {'code': code})
    file_path = ui_cmd_response['path']

    return f"Created new Python file at {file_path}"

@nbapi.auto_approve
@nbapi.tool
async def get_file_content(**args) -> str:
    """Returns the content of the current file.
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-current-file-content', {})

    return f"Received the file content"

@nbapi.auto_approve
@nbapi.tool
async def set_file_content(content: str, **args) -> str:
    """Sets the content of the current file.
    Args:
        content: File content
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:set-current-file-content', {"content": content})

    return f"Set the file content"

@nbapi.tool
async def search_files(
    pattern: str,
    directory: str = ".",
    file_pattern: str = None,
    **args
) -> str:
    """
    Search for file content within all files matching a pattern in jupyter_root_dir.
    Returns line matches with some context.

    Args:
        pattern: Glob pattern to search for files (e.g., "*.py", "**/*.txt")
        directory: Directory to search in (relative to jupyter_root_dir, default is root)
        file_pattern (optional): Additional glob pattern to filter files (e.g., "*.py").
        content_pattern (optional): Text or regex pattern to search for inside files.
        context_lines (optional): Lines of context around each match (default=2).
    """
    import re

    jupyter_root_dir = get_jupyter_root_dir()
    if jupyter_root_dir is None:
        return "Error! Jupyter root directory is not set"

    try:
        search_dir = _get_safe_path(directory)
        if not search_dir.exists():
            return f"Directory '{directory}' does not exist"
        
        # Input arguments
        content_pattern = args.get('content_pattern', None)
        context_lines = int(args.get('context_lines', 2))
        # Support backward-compatible defaulting, if called with only old pattern argument
        main_pattern = pattern or "**/*"
        # Reject patterns that try to escape the search root before they
        # reach glob(); otherwise glob would stat outside paths and the
        # differing replies would leak outside-file existence even though
        # the per-match gate below refuses to read the contents.
        for pat in (main_pattern, file_pattern):
            if pat and _glob_pattern_escapes(pat):
                return (
                    f"Pattern '{pat}' is not allowed: search is confined to the "
                    f"workspace and patterns cannot traverse outside it."
                )
        # If file_pattern is provided, use it to restrict files further after applying main_pattern
        matched_results = []
        file_count = 0
        match_count = 0

        # Enumerate via a depth-pruned, sandbox-aware walk instead of
        # Path.glob(): glob descends OUTBOUND symlinked directories while
        # expanding a pattern such as "link/*", enumerating the filesystem
        # outside the workspace before any candidate can be rejected, and it
        # walks the whole subtree even for shallow patterns.
        # _iter_pattern_matches recurses only as deep as the pattern requires,
        # descends real and in-workspace symlinked directories but never an
        # outbound one, and preserves full glob syntax; each surviving match is
        # still funneled through the safe_jupyter_path gate before it is stat'd
        # or opened.
        root_dir = Path(jupyter_root_dir).expanduser().resolve()
        # Drop empty and "." segments so a "current directory" component is a
        # no-op, matching Path.glob's normalization of "./*.py" and
        # "sub/./*.txt" rather than searching for an entry literally named ".".
        segments = [
            s
            for s in main_pattern.replace("\\", "/").split("/")
            if s and s != "."
        ]
        files = []
        seen = set()
        for cand in _iter_pattern_matches(search_dir, segments, root_dir):
            # Optional file_pattern filter. Preserve the original path-aware
            # behavior: Path.match is right-anchored, so a bare basename glob
            # ("*.py") and a relative-path glob ("sub/*.py") both work, as does
            # a pattern anchored at the search directory.
            if file_pattern and not (
                cand.match(file_pattern)
                or cand.name == file_pattern
                or cand.match(str(search_dir / file_pattern))
            ):
                continue
            try:
                rel_path = cand.relative_to(root_dir)
            except ValueError:
                continue
            key = str(rel_path)
            if key in seen:
                continue
            try:
                safe_file = _get_safe_path(key)
            except ValueError:
                # Outbound symlink file (e.g. leak.txt -> /etc/passwd)
                # resolves outside the workspace; skipped identically
                # whether or not its target exists, so no outside-path
                # existence can be inferred. Matches read_file's gate.
                continue
            # Safe to stat now: the path is confirmed inside the workspace.
            if safe_file.is_file():
                seen.add(key)
                files.append((safe_file, rel_path))

        # Deterministic order (directory iteration order is arbitrary).
        files.sort(key=lambda item: str(item[1]))

        if not files:
            pinfo = file_pattern if file_pattern else pattern
            return f"No files found matching pattern '{pinfo}' in '{directory}'"

        for safe_file, rel_path in files:
            try:
                with open(safe_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
            except Exception:
                continue  # skip unreadable files

            file_has_match = False
            if content_pattern:
                # Compile as regex, fallback to plain string if invalid
                try:
                    content_re = re.compile(content_pattern)
                except Exception:
                    content_re = None

                for idx, line in enumerate(lines):
                    matched = False
                    if content_re:
                        if content_re.search(line):
                            matched = True
                    elif content_pattern in line:
                        matched = True

                    if matched:
                        file_has_match = True
                        match_count += 1
                        # Show context around match
                        start = max(0, idx - context_lines)
                        end = min(len(lines), idx + context_lines + 1)
                        context_block = ''.join(
                            f"{i+1}: {lines[i]}" for i in range(start, end)
                        )
                        matched_results.append(f"File: {rel_path}\nMatch (line {idx+1}):\n{context_block}\n{'-'*32}")
                if file_has_match:
                    file_count += 1
            else:
                file_count += 1
                matched_results.append(str(rel_path))

        if content_pattern:
            if matched_results:
                return f"Found {match_count} match(es) in {file_count} file(s):\n" + "\n".join(matched_results)
            else:
                pinfo = file_pattern if file_pattern else pattern
                return f"No matches found for pattern '{content_pattern}' in files matching '{pinfo}' in '{directory}'"
        else:
            pinfo = file_pattern if file_pattern else pattern
            return f"Found {file_count} file(s) matching '{pinfo}':\n" + "\n".join(matched_results)
    except Exception as e:
        return f"Error searching files: {str(e)}"

@nbapi.tool
async def list_files(
    pattern: str = "*",
    directory: str = ".", 
    recursive: bool = False, 
    include_files: bool = True, 
    include_dirs: bool = True, 
    max_depth: int = 5, 
    **args
) -> str:
    """List files and/or directories within a directory in jupyter_root_dir.

    Args:
        pattern: Glob pattern to filter files (e.g., "*.py", "**/*.txt")
        directory: Directory to list (relative to jupyter_root_dir, default is root)
        recursive: Whether to list contents recursively (default False)
        include_files: Whether to include files (default True)
        include_dirs: Whether to include directories (default True)
        max_depth: Maximum recursion depth (only applies if recursive=True, default 5)
    """
    try:
        list_dir = _get_safe_path(directory)

        if not list_dir.exists():
            return f"Directory '{directory}' does not exist"

        if not list_dir.is_dir():
            return f"'{directory}' is not a directory"

        items = []
        root_rel_path = Path(directory)

        def _get_depth(item_path: Path, base_path: Path) -> int:
            """Calculate the depth of item_path relative to base_path."""
            try:
                rel = item_path.relative_to(base_path)
                return len(rel.parts)
            except ValueError:
                return 0

        # Use glob for pattern matching - handles ** patterns correctly
        # If pattern contains ** or recursive is True, use recursive glob
        if recursive or "**" in pattern:
            # For recursive listing, prepend **/ if pattern doesn't have it
            glob_pattern = pattern if "**" in pattern else f"**/{pattern}"
            matched_items = list(list_dir.glob(glob_pattern))
        else:
            # Non-recursive: just match in the current directory
            matched_items = list(list_dir.glob(pattern))

        # Sort and filter results
        for item in sorted(matched_items):
            # Apply max_depth filter for recursive searches
            depth = _get_depth(item, list_dir)
            if recursive and depth > max_depth:
                continue

            # Build relative path for display
            try:
                item_relpath = root_rel_path / item.relative_to(list_dir)
            except ValueError:
                item_relpath = item

            if item.is_dir():
                if include_dirs:
                    items.append(f"[dir] {item_relpath}")
            elif item.is_file():
                if include_files:
                    items.append(f"[file] {item_relpath}")

        if items:
            return f"Contents of '{directory}' (pattern: '{pattern}'):\n" + "\n".join(str(x) for x in items)
        else:
            return f"No files or directories matching pattern '{pattern}' in '{directory}'"
    except Exception as e:
        return f"Error listing files: {str(e)}"

@nbapi.tool
async def read_file(
    file_path: str,
    start_line: int = 1,
    end_line: int = -1,
    **args,
) -> str:
    """Read lines from a file within jupyter_root_dir between start_line and end_line (inclusive).
    
    Args:
        file_path: Path to the file (relative to jupyter_root_dir)
        start_line: 1-based line number to start reading from (default = 1)
        end_line: 1-based line number to stop reading at (inclusive, default = -1 for end of file)
    """
    try:
        target_file = _get_safe_path(file_path)
        
        if not target_file.exists():
            return f"File '{file_path}' does not exist"
        if not target_file.is_file():
            return f"'{file_path}' is not a file"
        if start_line < 1:
            return "start_line must be >= 1"
        # Read all lines
        with open(target_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        total_lines = len(lines)
        # Adjust end_line for Python indexing
        if end_line == -1 or end_line > total_lines:
            end_line = total_lines
        if end_line < start_line:
            return f"end_line ({end_line}) must be >= start_line ({start_line})"
        # Slice lines based on user input (start_line and end_line are 1-based and inclusive)
        content_lines = lines[start_line-1:end_line]
        content = "".join(content_lines)
        prefix = f"Content of '{file_path}' (lines {start_line}-{end_line}):\n"
        return _truncate_read_file_output(
            prefix,
            content,
            start_line,
            DEFAULT_READ_FILE_MAX_OUTPUT_TOKENS,
        )
    except UnicodeDecodeError:
        return f"Error: File '{file_path}' is not a text file or uses an unsupported encoding"
    except Exception as e:
        return f"Error reading file: {str(e)}"

@nbapi.tool
async def insert_content(file_path: str, line_number: int, content: str, **args) -> str:
    """Insert content at a specific line in a file within jupyter_root_dir.
    
    Args:
        file_path: Path to the file (relative to jupyter_root_dir)
        line_number: Line number to insert at (1-based, inserts before this line)
        content: Content to insert
    """
    try:
        target_file = _get_safe_path(file_path)
        
        if not target_file.exists():
            return f"File '{file_path}' does not exist"
        
        if not target_file.is_file():
            return f"'{file_path}' is not a file"
        
        # Read existing content
        with open(target_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Validate line number
        if line_number < 1 or line_number > len(lines) + 1:
            return f"Invalid line number {line_number}. File has {len(lines)} lines"
        
        # Insert content (convert to 0-based index)
        insert_index = line_number - 1
        if not content.endswith('\n'):
            content += '\n'
        lines.insert(insert_index, content)
        
        # Write back to file
        with open(target_file, 'w', encoding='utf-8') as f:
            f.writelines(lines)
        
        return f"Inserted content at line {line_number} in '{file_path}'"
    except Exception as e:
        return f"Error inserting content: {str(e)}"

@nbapi.tool
async def write_to_file(file_path: str, content: str, **args) -> str:
    """Write content to a file within jupyter_root_dir (creates or overwrites).
    
    Args:
        file_path: Path to the file (relative to jupyter_root_dir)
        content: Content to write to the file
    """
    try:
        target_file = _get_safe_path(file_path)
        
        # Create parent directories if they don't exist
        target_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Write content to file
        with open(target_file, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return f"Wrote content to '{file_path}'"
    except Exception as e:
        return f"Error writing to file: {str(e)}"

@nbapi.tool
async def execute_command(command: str, working_directory: str = ".", **args) -> str:
    """Execute a shell command within jupyter_root_dir.
    
    Args:
        command: Shell command to execute
        working_directory: Directory to execute command in (relative to jupyter_root_dir, default is root)
    """
    try:
        work_dir = _get_safe_path(working_directory)
        
        if not work_dir.exists():
            return f"Directory '{working_directory}' does not exist"
        
        if not work_dir.is_dir():
            return f"'{working_directory}' is not a directory"
        
        # Execute command
        cmd_list = shlex.split(command)
        result = subprocess.run(
            cmd_list,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            timeout=30  # 30 second timeout
        )
        
        output = []
        if result.stdout:
            output.append(f"STDOUT:\n{result.stdout}")
        if result.stderr:
            output.append(f"STDERR:\n{result.stderr}")
        output.append(f"Return code: {result.returncode}")

        # Scrub the user's own GITHUB_TOKEN / ANTHROPIC_API_KEY / etc. out
        # of the chat-bound result before returning. The LLM-driven
        # command can be `env`, `printenv`, or a misbehaving binary that
        # dumps env on error; without this, those secrets land in chat
        # history (and through to the LLM provider).
        return redact_env_secrets("\n\n".join(output))
    except subprocess.TimeoutExpired:
        return f"Command timed out after 30 seconds"
    except Exception as e:
        return f"Error executing command: {str(e)}"

@nbapi.tool
async def run_command_in_jupyter_terminal(command: str, working_directory: str = ".", **args) -> str:
    """Run a shell command in a Jupyter terminal within working_directory. This can be used to run long running processes like web applications. Returns the output of the command.

    Args:
        command: Shell command to execute in the terminal
        working_directory: Directory to execute command in (relative to jupyter_root_dir, default is root)
    """
    try:
        response = args["response"]
        # Sandbox the cwd the same way execute_command and the embedded-terminal
        # tool do. The Jupyter terminal opens at any absolute path the user can
        # read, so without this check an LLM-supplied path of '/etc' would land
        # a real shell outside the workspace.
        try:
            work_dir = _get_safe_path(working_directory)
        except ValueError as e:
            return f"Error: {e}"
        if not work_dir.exists():
            return f"Directory '{working_directory}' does not exist"
        if not work_dir.is_dir():
            return f"'{working_directory}' is not a directory"
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:run-command-in-terminal', {
            'command': command,
            'cwd': str(work_dir)
        })
        return ui_cmd_response
    except Exception as e:
        return f"Error running command in Jupyter terminal: {str(e)}"

@nbapi.tool
async def run_command_in_embedded_terminal(command: str, working_directory: str = ".", **args) -> str:
    """Run a shell command in an embedded terminal within working_directory. Use this for short running shell commands.

    Args:
        command: Shell command to execute in the terminal
        working_directory: Directory to execute command in (relative to jupyter_root_dir, default is root)
    """
    try:
        response = args["response"]
        # Mirror execute_command's scoping: route working_directory through
        # _get_safe_path so an LLM-supplied path outside jupyter_root_dir
        # cannot spawn a subprocess against the host filesystem. The sibling
        # execute_command already does this; this tool was the outlier.
        try:
            work_dir = _get_safe_path(working_directory)
        except ValueError as e:
            return f"Error: {e}"
        if not work_dir.exists():
            return f"Directory '{working_directory}' does not exist"
        if not work_dir.is_dir():
            return f"'{working_directory}' is not a directory"
        # run the command in a bash process and stream the output to the response
        cmd_list = shlex.split(command)
        process = subprocess.Popen(
            cmd_list,
            shell=False,
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        response.stream(MarkdownPartData("<terminal-output>"))
        # Scrub each streamed line so a verbose command (env, printenv, git
        # in trace mode) can't paste GITHUB_TOKEN / ANTHROPIC_API_KEY into
        # the chat transcript. The redact helper looks at the running
        # process's env at call time, so per-line invocation is correct.
        for line in process.stdout:
            response.stream(MarkdownPartData(redact_env_secrets(line) + "\n"))

        # Wait for the process to finish and get the return code
        process.wait()
        response.stream(MarkdownPartData("</terminal-output>"))

        # Check for any errors
        if process.returncode != 0:
            stderr_output = process.stderr.read()
            response.stream(MarkdownPartData(f"Error executing command: {command}\n"))
            response.stream(MarkdownPartData(redact_env_secrets(stderr_output) + "\n"))
        else:
            response.stream(MarkdownPartData(f"Command executed successfully with return code: {process.returncode}"))
        response.finish()
        return "Command executed in embedded terminal"
    except Exception as e:
        return f"Error running command in embedded terminal: {str(e)}"

NOTEBOOK_EDIT_INSTRUCTIONS = """
You are an assistant that creates and edits Jupyter notebooks. Notebooks are made up of source code cells and markdown cells. Markdown cells have source in markdown format and code cells have source in a specified programming language. If no programming language is specified, then use Python for the language of the code. If the context specifies a kernel or language for the current notebook, keep that kernel and language. Do not silently switch kernels or rewrite the workflow in a different language.

If you need to create a notebook use the create_new_notebook tool. If you need to add a code cell to the notebook use the add_code_cell tool. If you need to add a markdown cell to the notebook use the add_markdown_cell tool.

If you need to create a notebook in a language or kernel that is not already established by the current notebook context, call the list_available_notebook_kernels tool first and choose only from the kernels it returns. Do not guess kernel names.

If you need to rename a notebook use the rename_notebook tool.

You can refer to cells in notebooks by their index. The first cell in the notebook has index 0, the second cell has index 1, and so on. You can get the number of cells in the notebook using the get_number_of_cells tool. You can get the type and source of a cell using the get_cell_type_and_source tool. You can get the output of a cell using the get_cell_output tool.

If you need to make changes to an existing notebook use the tools to get existing cell type and source. Use the set_cell_type_and_source tool for updating cell type and source. You can set the cell type to either code or markdown. You can set the source of the cell to either source code or markdown text.

If you need to install any packages you shoud use %pip install <package_name> in a code cell instead of !pip install <package_name>.

If you need to detect issues in a notebook check the code cell sources and also the cell output for any problems.

Choose the notebook workflow based on the task type.

Default to an exploratory workflow when the task is uncertain, scientific, data-driven, or likely to require intermediate interpretation. In that mode, do not begin by building a complete notebook from start to finish. Start with a minimal notebook or a minimal extension of the current notebook, add only the next small set of code and markdown cells needed to answer the immediate question, run those cells, inspect the outputs, and then decide what to add or revise next.

Use a construction workflow when the task is well-defined, narrow in scope, and largely predetermined in structure. In that mode, it is acceptable to define a fuller plan up front and proceed more linearly, while still validating outputs and revising the notebook if needed.

Do not pre-write a full report, a full modeling pipeline, or dozens of cells before seeing intermediate results in exploratory or scientific tasks.

After you are done making changes to the notebook, save the notebook using the save_notebook tool.

Before calling any tools, first show a short execution plan. The plan should be provisional, minimal, and revisable based on what later cells reveal. Do not present the plan as a fixed end-to-end recipe when the task is exploratory or scientific.
"""

NOTEBOOK_EXECUTE_INSTRUCTIONS = """
Running a notebook and executing a notebook refer to the same thing. Running a notebook means executing all the cells in the notebook in order. If you need to run a cell in the notebook use the run_cell tool with the cell index. Executing a cell and running a cell are the same thing.

If you create a new notebook and run it, then check for errors in the output of the cells. If there are any errors in the output, update the cell code that caused the error to fix it and rerun the cell. Repeat until there are no errors in the output of the cells.

When notebook execution is part of an exploratory or scientific workflow, prefer small iterative cycles:
1. add a small number of cells (markdown cells and code cells),
2. run them,
3. inspect outputs and errors,
4. revise the notebook,
5. then continue only if the results justify the next step.

If outputs suggest the notebook should be revised, stop executing linearly and return to notebook editing rather than continuing through later cells blindly.

After you are done running the notebook, save the notebook using the save_notebook tool.
"""

PYTHON_FILE_EDIT_INSTRUCTIONS = """
If you need to create a new Python file use the create_new_python_file tool. If you need to edit an existing Python file use the get_file_content tool to get the content of the file and then use the set_file_content tool to set the content of the file.

If user is referring to a file, then you can use the get_file_content tool to get the content of the file and then use the set_file_content tool to set the content of the file.
"""

FILE_READ_INSTRUCTIONS = """
Use the file system tools to interact with files and directories inside Jupyter root directory.

- Use list_files to view directory contents.
- Use search_files to find files by pattern (e.g., "*.py" or "**/*.txt").
- Use read_file to view specific file contents.

Paths must be relative to the Jupyter root directory (e.g., "folder/file.txt").
All operations are limited to this directory for security.
"""

FILE_EDIT_INSTRUCTIONS = """
Use file editing tools within Jupyter root directory:

- write_to_file to create or overwrite files.
- insert_content to add content at a specific line.

All changes are restricted to the root directory for safety.
"""

COMMAND_EXECUTE_INSTRUCTIONS = """
Run shell commands with execute_command. All commands execute inside Jupyter root directory for security.
"""

built_in_toolsets: dict[BuiltinToolset, Toolset] = {
    BuiltinToolset.NotebookEdit: Toolset(
        id=BuiltinToolset.NotebookEdit,
        name="Notebook edit",
        description="Edit notebook using the JupyterLab notebook editor",
        provider=None,
        tools=[
            list_available_notebook_kernels,
            create_new_notebook,
            rename_notebook,
            add_markdown_cell,
            add_code_cell,
            get_number_of_cells,
            get_cell_output,
            get_cell_type_and_source,
            set_cell_type_and_source,
            delete_cell,
            insert_cell,
            save_notebook
        ],
        instructions=NOTEBOOK_EDIT_INSTRUCTIONS
    ),
    BuiltinToolset.NotebookExecute: Toolset(
        id=BuiltinToolset.NotebookExecute,
        name="Notebook execute",
        description="Run notebooks in JupyterLab UI",
        provider=None,
        tools=[
            run_cell
        ],
        instructions=NOTEBOOK_EXECUTE_INSTRUCTIONS
    ),
    BuiltinToolset.PythonFileEdit: Toolset(
        id=BuiltinToolset.PythonFileEdit,
        name="Python file edit",
        description="Edit Python files using the JupyterLab file editor",
        provider=None,
        tools=[
            create_new_python_file,
            get_file_content,
            set_file_content
        ],
        instructions=PYTHON_FILE_EDIT_INSTRUCTIONS
    ),
    BuiltinToolset.FileRead: Toolset(
        id=BuiltinToolset.FileRead,
        name="File read",
        description="File system read operations within Jupyter root directory",
        provider=None,
        tools=[
            search_files,
            list_files,
            read_file
        ],
        instructions=FILE_READ_INSTRUCTIONS
    ),
    BuiltinToolset.FileEdit: Toolset(
        id=BuiltinToolset.FileEdit,
        name="File edit",
        description="File system write operations within Jupyter root directory",
        provider=None,
        tools=[
            write_to_file,
            insert_content
        ],
        instructions=FILE_EDIT_INSTRUCTIONS
    ),
    BuiltinToolset.CommandExecute: Toolset(
        id=BuiltinToolset.CommandExecute,
        name="Command execute",
        description="Execute shell commands using embedded terminal in Agent UI or JupyterLab terminal",
        provider=None,
        tools=[
            run_command_in_jupyter_terminal,
            run_command_in_embedded_terminal
        ],
        instructions=COMMAND_EXECUTE_INSTRUCTIONS
    ),
}
