# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from notebook_intelligence.api import ChatResponse, Toolset
import logging
import notebook_intelligence.api as nbapi
from notebook_intelligence.api import BuiltinToolset

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
async def get_mlflow_experiments(**args) -> str:
    """Get all the mlflow experiments.
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-mlflow-experiments', {})
    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def get_experiment_details(experiment_id: str, **args) -> str:
    """Get details of a specific MLflow experiment (creation date, run count, etc).

    Args:
        experiment_id: The MLflow experiment id
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-mlflow-experiment-details', { 'experimentId': experiment_id })
    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def get_runs_today(experiment_id: str, **args) -> str:
    """Get the list of runs created today in a given experiment.

    Args:
        experiment_id: The MLflow experiment id
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-mlflow-runs-today', { 'experimentId': experiment_id })
    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def get_run_details(run_id: str, **args) -> str:
    """Get the details of a run (parameters, metrics, model artifact names, deployment status).

    Args:
        run_id: The MLflow run id
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-mlflow-run-details', { 'runId': run_id })
    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def get_best_run_in_experiment(experiment_id: str, metric_key: str = None, higher_is_better: bool = True, **args) -> str:
    """Identify the best run in an experiment.

    Args:
        experiment_id: The MLflow experiment id
        metric_key: Optional metric key to rank by (default picks a sensible metric like Test_f1score)
        higher_is_better: Whether higher metric values are better (default True)
    """
    response = args["response"]
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-mlflow-best-run-in-experiment', {
        'experimentId': experiment_id,
        'metricKey': metric_key,
        'higherIsBetter': higher_is_better
    })
    return str(ui_cmd_response)

@nbapi.auto_approve
@nbapi.tool
async def get_best_run_from_runs(runs_json: str, metric_key: str = None, higher_is_better: bool = True, **args) -> str:
    """Identify the best run within the given list of runs.

    Args:
        runs_json: JSON string containing an array of MLflow run objects as returned by the API
        metric_key: Optional metric key to rank by
        higher_is_better: Whether higher metric values are better (default True)
    """
    response = args["response"]
    try:
        import json
        runs = json.loads(runs_json)
    except Exception:
        # If not a JSON array already, pass through raw string and let frontend validate
        runs = runs_json
    ui_cmd_response = await response.run_ui_command('notebook-intelligence:get-mlflow-best-run-from-runs', {
        'runs': runs,
        'metricKey': metric_key,
        'higherIsBetter': higher_is_better
    })
    return str(ui_cmd_response)

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

NOTEBOOK_EDIT_INSTRUCTIONS = """
You are an assistant that creates and edits Jupyter notebooks. Notebooks are made up of source code cells and markdown cells. Markdown cells have source in markdown format and code cells have source in a specified programming language. If no programming language is specified, then use Python for the language of the code.

If you need to create a notebook use the create_new_notebook tool. If you need to add a code cell to the notebook use the add_code_cell tool. If you need to add a markdown cell to the notebook use the add_markdown_cell tool.

If you need to rename a notebook use the rename_notebook tool.

You can refer to cells in notebooks by their index. The first cell in the notebook has index 0, the second cell has index 1, and so on. You can get the number of cells in the notebook using the get_number_of_cells tool. You can get the type and source of a cell using the get_cell_type_and_source tool. You can get the output of a cell using the get_cell_output tool.

If you need to make changes to an existing notebook use the tools to get existing cell type and source. Use the set_cell_type_and_source tool for updating cell type and source. You can set the cell type to either code or markdown. You can set the source of the cell to either source code or markdown text.

If you need to install any packages you shoud use %pip install <package_name> in a code cell instead of !pip install <package_name>.

If you need to detect issues in a notebook check the code cell sources and also the cell output for any problems.

After you are done making changes to the notebook, save the notebook using the save_notebook tool.

If you are asked to list experiments in mlflow, use the get_mlflow_experiments tool.

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

built_in_toolsets: dict[BuiltinToolset, Toolset] = {
    BuiltinToolset.NotebookEdit: Toolset(
        id=BuiltinToolset.NotebookEdit,
        name="Notebook edit",
        description="Notebook edit",
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
            save_notebook,
            get_mlflow_experiments,
            get_experiment_details,
            get_run_details,
            get_runs_today,
            get_best_run_in_experiment,
            get_best_run_from_runs
        ],
        instructions=NOTEBOOK_EDIT_INSTRUCTIONS
    ),
    BuiltinToolset.NotebookExecute: Toolset(
        id=BuiltinToolset.NotebookExecute,
        name="Notebook execute",
        description="Notebook execute",
        provider=None,
        tools=[
            run_cell
        ],
        instructions=NOTEBOOK_EXECUTE_INSTRUCTIONS
    ),
    BuiltinToolset.PythonFileEdit: Toolset(
        id=BuiltinToolset.PythonFileEdit,
        name="Python file edit",
        description="Python file edit",
        provider=None,
        tools=[
            create_new_python_file,
            get_file_content,
            set_file_content
        ],
        instructions=PYTHON_FILE_EDIT_INSTRUCTIONS
    ),
}
