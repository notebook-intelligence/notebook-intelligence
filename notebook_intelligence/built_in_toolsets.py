# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from time import time
from notebook_intelligence.api import ChatResponse, MarkdownPartData, Toolset
import logging
import notebook_intelligence.api as nbapi
from notebook_intelligence.api import BuiltinToolset
from pathlib import Path
import subprocess

from notebook_intelligence.util import get_jupyter_root_dir

log = logging.getLogger(__name__)

@nbapi.auto_approve
@nbapi.tool
async def create_new_notebook(**args) -> str:
    """Creates a new empty notebook.
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:create-new-notebook-from-py', {'code': ''})
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

    return "Added markdown cell to notebook"

@nbapi.auto_approve
@nbapi.tool
async def add_code_cell(source: str, **args) -> str:
    """Adds a code cell to notebook.
    Args:
        source: Python code source
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-active-notebook', {'source': source})

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
    """Get cell type and source for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-type-and-source', {"cellIndex": cell_index })

    return str(ui_cmd_response)


@nbapi.auto_approve
@nbapi.tool
async def get_cell_output(cell_index: int, **args) -> str:
    """Get cell output for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-cell-output', {"cellIndex": cell_index})

    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def set_cell_type_and_source(cell_index: int, cell_type: str, source: str, **args) -> str:
    """Set cell type and source for the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
        cell_type: Cell type (code or markdown)
        source: Markdown or Python code source
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:set-cell-type-and-source', {"cellIndex": cell_index, "cellType": cell_type, "source": source})

    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def delete_cell(cell_index: int, **args) -> str:
    """Delete the cell at index for the active notebook.

    Args:
        cell_index: Zero based cell index
    """
    response = args["response"]

    ui_cmd_response = await response.run_ui_command('notebook-intelligence:delete-cell-at-index', {"cellIndex": cell_index})

    return f"Deleted the cell at index: {cell_index}"

@nbapi.auto_approve
@nbapi.tool
async def insert_cell(cell_index: int, cell_type: str, source: str, **args) -> str:
    """Insert cell with type and source at index for the active notebook.

    Args:
        cell_index: Zero based cell index
        cell_type: Cell type (code or markdown)
        source: Markdown or Python code source
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:insert-cell-at-index', {"cellIndex": cell_index, "cellType": cell_type, "source": source})

    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def run_cell(cell_index: int, **args) -> str:
    """Run the cell at index for the active notebook.

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

    return f"Save the notebook"

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

def _get_safe_path(path: str) -> Path:
    """Validate and return a safe path within jupyter_root_dir.
    
    Args:
        path: Relative or absolute path to validate
        
    Returns:
        Resolved absolute Path object
        
    Raises:
        ValueError: If path is outside jupyter_root_dir
    """
    jupyter_root_dir = get_jupyter_root_dir()
    if jupyter_root_dir is None:
        raise ValueError("Jupyter root directory is not set")

    root_dir = Path(jupyter_root_dir).expanduser().resolve()
    target_path = Path(path).expanduser()
    
    # If it's a relative path, make it relative to root_dir
    if not target_path.is_absolute():
        target_path = root_dir / target_path
    
    # Resolve to absolute path
    target_path = target_path.resolve()
    
    # Check if target is within root directory
    try:
        target_path.relative_to(root_dir)
    except ValueError:
        raise ValueError(f"Path '{path}' is outside allowed directory '{jupyter_root_dir}'")
    
    return target_path

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
        # If file_pattern is provided, use it to restrict files further after applying main_pattern
        matched_results = []
        file_count = 0
        match_count = 0

        # Use the main pattern for initial search
        files = [f for f in search_dir.glob(main_pattern) if f.is_file()]
        # Further restrict by file_pattern if provided (matches basename)
        if file_pattern:
            files = [
                f for f in files
                if f.match(file_pattern) or f.name == file_pattern or f.match(str(search_dir / file_pattern))
            ]
        if not files:
            pinfo = file_pattern if file_pattern else pattern
            return f"No files found matching pattern '{pinfo}' in '{directory}'"

        for file_path in files:
            root_dir = Path(jupyter_root_dir).expanduser().resolve()
            try:
                rel_path = file_path.relative_to(root_dir)
            except ValueError:
                rel_path = file_path

            try:
                with open(file_path, 'r', encoding='utf-8') as f:
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
async def read_file(file_path: str, start_line: int = 1, end_line: int = -1, **args) -> str:
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
        return f"Content of '{file_path}' (lines {start_line}-{end_line}):\n{content}"
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
        result = subprocess.run(
            command,
            shell=True,
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
        
        return "\n\n".join(output)
    except subprocess.TimeoutExpired:
        return f"Command timed out after 30 seconds"
    except Exception as e:
        return f"Error executing command: {str(e)}"

@nbapi.tool
async def run_command_in_jupyter_terminal(command: str, working_directory: str = ".", **args) -> str:
    """Run a shell command in a Jupyter terminal within working_directory. This can be used to run long running processes like web applications.
    
    Args:
        command: Shell command to execute in the terminal
        working_directory: Directory to execute command in (relative to jupyter_root_dir, default is root)
    """
    try:
        response = args["response"]
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:run-command-in-terminal', {
            'command': command,
            'cwd': working_directory
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
        # run the command in a bash process and stream the output to the response
        process = subprocess.Popen(command, shell=True, cwd=working_directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        response.stream(MarkdownPartData("<terminal-output>"))
        for line in process.stdout:
            response.stream(MarkdownPartData(line + "\n"))

        # Wait for the process to finish and get the return code
        process.wait()
        response.stream(MarkdownPartData("</terminal-output>"))
        
        # Check for any errors
        if process.returncode != 0:
            stderr_output = process.stderr.read()
            response.stream(MarkdownPartData(f"Error executing command: {command}\n"))
            response.stream(MarkdownPartData(stderr_output + "\n"))
        else:
            response.stream(MarkdownPartData(f"Command executed successfully with return code: {process.returncode}"))
        response.finish()
        return "Command executed in embedded terminal"
    except Exception as e:
        return f"Error running command in embedded terminal: {str(e)}"

NOTEBOOK_EDIT_INSTRUCTIONS = """
You are an assistant that creates and edits Jupyter notebooks. Notebooks are made up of source code cells and markdown cells. Markdown cells have source in markdown format and code cells have source in a specified programming language. If no programming language is specified, then use Python for the language of the code.

If you need to create a notebook use the create_new_notebook tool. If you need to add a code cell to the notebook use the add_code_cell tool. If you need to add a markdown cell to the notebook use the add_markdown_cell tool.

If you need to rename a notebook use the rename_notebook tool.

You can refer to cells in notebooks by their index. The first cell in the notebook has index 0, the second cell has index 1, and so on. You can get the number of cells in the notebook using the get_number_of_cells tool. You can get the type and source of a cell using the get_cell_type_and_source tool. You can get the output of a cell using the get_cell_output tool.

If you need to make changes to an existing notebook use the tools to get existing cell type and source. Use the set_cell_type_and_source tool for updating cell type and source. You can set the cell type to either code or markdown. You can set the source of the cell to either source code or markdown text.

If you need to install any packages you shoud use %pip install <package_name> in a code cell instead of !pip install <package_name>.

If you need to detect issues in a notebook check the code cell sources and also the cell output for any problems.

After you are done making changes to the notebook, save the notebook using the save_notebook tool.

First create an execution plan and show before calling any tools. The execution plan should be a list of steps that you will take. Then call the tools to execute the plan.
"""

NOTEBOOK_EXECUTE_INSTRUCTIONS = """
Running a notebook and executing a notebook refer to the same thing. Running a notebook means executing all the cells in the notebook in order. If you need to run a cell in the notebook use the run_cell tool with the cell index. Executing a cell and running a cell are the same thing.

If you create a new notebook and run it, then check for errors in the output of the cells. If there are any errors in the output, update the cell code that caused the error to fix it and rerun the cell. Repeat until there are no errors in the output of the cells.

If you are asked to analyze a dataset, you should fist create a notebook and add the code cells and markdown cells to the notebook which are needed to analyze the dataset and run all the cells.

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
