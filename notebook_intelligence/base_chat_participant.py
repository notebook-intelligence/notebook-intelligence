# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import os
from typing import Union
import json
from notebook_intelligence.api import ChatCommand, ChatParticipant, ChatRequest, ChatResponse, MarkdownData, ProgressData, Tool, ToolPreInvokeResponse
from notebook_intelligence.prompts import Prompts
import base64
import logging
from notebook_intelligence.built_in_toolsets import built_in_toolsets
from notebook_intelligence.rule_injector import RuleInjector

from notebook_intelligence.util import extract_llm_generated_code

log = logging.getLogger(__name__)

ICON_SVG = '<svg width="16" height="16" viewBox="0 0 16 16" xmlns="http://www.w3.org/2000/svg" fill="currentColor"><path d="M5.39804 10.8069C5.57428 10.9312 5.78476 10.9977 6.00043 10.9973C6.21633 10.9975 6.42686 10.93 6.60243 10.8043C6.77993 10.6739 6.91464 10.4936 6.98943 10.2863L7.43643 8.91335C7.55086 8.56906 7.74391 8.25615 8.00028 7.99943C8.25665 7.74272 8.56929 7.54924 8.91343 7.43435L10.3044 6.98335C10.4564 6.92899 10.5936 6.84019 10.7055 6.7239C10.8174 6.60762 10.9008 6.467 10.9492 6.31308C10.9977 6.15916 11.0098 5.99611 10.9847 5.83672C10.9596 5.67732 10.8979 5.52591 10.8044 5.39435C10.6703 5.20842 10.4794 5.07118 10.2604 5.00335L8.88543 4.55635C8.54091 4.44212 8.22777 4.24915 7.97087 3.99277C7.71396 3.73638 7.52035 3.42363 7.40543 3.07935L6.95343 1.69135C6.88113 1.48904 6.74761 1.31428 6.57143 1.19135C6.43877 1.09762 6.28607 1.03614 6.12548 1.01179C5.96489 0.987448 5.80083 1.00091 5.64636 1.05111C5.49188 1.1013 5.35125 1.18685 5.23564 1.30095C5.12004 1.41505 5.03265 1.55454 4.98043 1.70835L4.52343 3.10835C4.40884 3.44317 4.21967 3.74758 3.97022 3.9986C3.72076 4.24962 3.41753 4.44067 3.08343 4.55735L1.69243 5.00535C1.54065 5.05974 1.40352 5.14852 1.29177 5.26474C1.18001 5.38095 1.09666 5.52145 1.04824 5.67523C0.999819 5.82902 0.987639 5.99192 1.01265 6.1512C1.03767 6.31048 1.0992 6.46181 1.19243 6.59335C1.32027 6.7728 1.50105 6.90777 1.70943 6.97935L3.08343 7.42435C3.52354 7.57083 3.90999 7.84518 4.19343 8.21235C4.35585 8.42298 4.4813 8.65968 4.56443 8.91235L5.01643 10.3033C5.08846 10.5066 5.22179 10.6826 5.39804 10.8069ZM5.48343 3.39235L6.01043 2.01535L6.44943 3.39235C6.61312 3.8855 6.88991 4.33351 7.25767 4.70058C7.62544 5.06765 8.07397 5.34359 8.56743 5.50635L9.97343 6.03535L8.59143 6.48335C8.09866 6.64764 7.65095 6.92451 7.28382 7.29198C6.9167 7.65945 6.64026 8.10742 6.47643 8.60035L5.95343 9.97835L5.50443 8.59935C5.34335 8.10608 5.06943 7.65718 4.70443 7.28835C4.3356 6.92031 3.88653 6.64272 3.39243 6.47735L2.01443 5.95535L3.40043 5.50535C3.88672 5.33672 4.32775 5.05855 4.68943 4.69235C5.04901 4.32464 5.32049 3.88016 5.48343 3.39235ZM11.5353 14.8494C11.6713 14.9456 11.8337 14.9973 12.0003 14.9974C12.1654 14.9974 12.3264 14.9464 12.4613 14.8514C12.6008 14.7529 12.7058 14.6129 12.7613 14.4514L13.0093 13.6894C13.0625 13.5309 13.1515 13.3869 13.2693 13.2684C13.3867 13.1498 13.5307 13.0611 13.6893 13.0094L14.4613 12.7574C14.619 12.7029 14.7557 12.6004 14.8523 12.4644C14.9257 12.3614 14.9736 12.2424 14.9921 12.1173C15.0106 11.9922 14.9992 11.8645 14.9588 11.7447C14.9184 11.6249 14.8501 11.5163 14.7597 11.428C14.6692 11.3396 14.5591 11.2739 14.4383 11.2364L13.6743 10.9874C13.5162 10.9348 13.3724 10.8462 13.2544 10.7285C13.1364 10.6109 13.0473 10.4674 12.9943 10.3094L12.7423 9.53638C12.6886 9.37853 12.586 9.24191 12.4493 9.14638C12.3473 9.07343 12.2295 9.02549 12.1056 9.00642C11.9816 8.98736 11.8549 8.99772 11.7357 9.03665C11.6164 9.07558 11.508 9.142 11.4192 9.23054C11.3304 9.31909 11.2636 9.42727 11.2243 9.54638L10.9773 10.3084C10.925 10.466 10.8375 10.6097 10.7213 10.7284C10.6066 10.8449 10.4667 10.9335 10.3123 10.9874L9.53931 11.2394C9.38025 11.2933 9.2422 11.3959 9.1447 11.5326C9.04721 11.6694 8.99522 11.8333 8.99611 12.0013C8.99699 12.1692 9.0507 12.3326 9.14963 12.4683C9.24856 12.604 9.38769 12.7051 9.54731 12.7574L10.3103 13.0044C10.4692 13.0578 10.6136 13.1471 10.7323 13.2654C10.8505 13.3836 10.939 13.5283 10.9903 13.6874L11.2433 14.4614C11.2981 14.6178 11.4001 14.7534 11.5353 14.8494ZM10.6223 12.0564L10.4433 11.9974L10.6273 11.9334C10.9291 11.8284 11.2027 11.6556 11.4273 11.4284C11.6537 11.1994 11.8248 10.9216 11.9273 10.6164L11.9853 10.4384L12.0443 10.6194C12.1463 10.9261 12.3185 11.2047 12.5471 11.4332C12.7757 11.6617 13.0545 11.8336 13.3613 11.9354L13.5563 11.9984L13.3763 12.0574C13.0689 12.1596 12.7898 12.3322 12.5611 12.5616C12.3324 12.791 12.1606 13.0707 12.0593 13.3784L12.0003 13.5594L11.9423 13.3784C11.8409 13.0702 11.6687 12.7901 11.4394 12.5605C11.2102 12.3309 10.9303 12.1583 10.6223 12.0564Z"/></svg>'
ICON_URL = f"data:image/svg+xml;base64,{base64.b64encode(ICON_SVG.encode('utf-8')).decode('utf-8')}"

class SecuredExtensionTool(Tool):
    def __init__(self, extension_tool: Tool):
        super().__init__()
        self._ext_tool = extension_tool

    @property
    def name(self) -> str:
        return self._ext_tool.name

    @property
    def title(self) -> str:
        return self._ext_tool.title
    
    @property
    def tags(self) -> list[str]:
        return self._ext_tool.tags
    
    @property
    def description(self) -> str:
        return self._ext_tool.description

    @property
    def schema(self) -> dict:
        return self._ext_tool.schema
    
    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        confirmationTitle = "Approve"
        confirmationMessage = "Are you sure you want to call this extension tool?"
        return ToolPreInvokeResponse(
            message = f"Calling extension tool '{self.name}'",
            detail = {"title": "Parameters", "content": json.dumps(tool_args)},
            confirmationTitle = confirmationTitle,
            confirmationMessage = confirmationMessage
        )

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        return await self._ext_tool.handle_tool_call(request, response, tool_context, tool_args)

class CreateNewNotebookTool(Tool):
    def __init__(self, auto_approve: bool = False):
        self._auto_approve = auto_approve
        super().__init__()

    @property
    def name(self) -> str:
        return "create_new_notebook"

    @property
    def title(self) -> str:
        return "Create new notebook with the provided code and markdown cells"
    
    @property
    def tags(self) -> list[str]:
        return ["default-participant-tool"]
    
    @property
    def description(self) -> str:
        return "This tool creates a new notebook with the provided code and markdown cells"
    
    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cell_sources": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "cell_type": {
                                        "type": "string",
                                        "enum": ["code", "markdown"]
                                    },
                                    "source": {
                                        "type": "string",
                                        "description": "The content of the cell"
                                    }
                                }
                            }
                        }
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        }
    
    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        confirmationTitle = None
        confirmationMessage = None
        if not self._auto_approve:
            confirmationTitle = "Approve"
            confirmationMessage = "Are you sure you want to call this tool?"
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", detail={"title": "Parameters", "content": json.dumps(tool_args)}, confirmationTitle=confirmationTitle, confirmationMessage=confirmationMessage)

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        cell_sources = tool_args.get('cell_sources', [])
    
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:create-new-notebook-from-py', {'code': ''})
        file_path = ui_cmd_response['path']

        for cell_source in cell_sources:
            cell_type = cell_source.get('cell_type')
            if cell_type == 'markdown':
                source = cell_source.get('source', '')
                ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-markdown-cell-to-notebook', {'markdown': source, 'path': file_path})
            elif cell_type == 'code':
                source = cell_source.get('source', '')
                ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-notebook', {'code': source, 'path': file_path})

        return "Notebook created successfully at {file_path}"

class AddMarkdownCellToNotebookTool(Tool):
    def __init__(self, auto_approve: bool = False):
        self._auto_approve = auto_approve
        super().__init__()

    @property
    def name(self) -> str:
        return "add_markdown_cell_to_notebook"

    @property
    def title(self) -> str:
        return "Add markdown cell to notebook"
    
    @property
    def tags(self) -> list[str]:
        return ["default-participant-tool"]
    
    @property
    def description(self) -> str:
        return "This is a tool that adds markdown cell to a notebook"
    
    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "notebook_file_path": {
                            "type": "string",
                            "description": "Notebook file path to add the markdown cell to",
                        },
                        "markdown_cell_source": {
                            "type": "string",
                            "description": "Markdown to add to the notebook",
                        }
                    },
                    "required": ["notebook_file_path", "markdown_cell_source"],
                    "additionalProperties": False,
                },
            },
        }

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        confirmationTitle = None
        confirmationMessage = None
        if not self._auto_approve:
            confirmationTitle = "Approve"
            confirmationMessage = "Are you sure you want to call this tool?"
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", detail={"title": "Parameters", "content": json.dumps(tool_args)}, confirmationTitle=confirmationTitle, confirmationMessage=confirmationMessage)

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        notebook_file_path = tool_args.get('notebook_file_path', '')
        server_root_dir = request.host.nbi_config.server_root_dir
        if notebook_file_path.startswith(server_root_dir):
            notebook_file_path = os.path.relpath(notebook_file_path, server_root_dir)
        source = tool_args.get('markdown_cell_source')
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-markdown-cell-to-notebook', {'markdown': source, 'path': notebook_file_path})
        return f"Added markdown cell to notebook"

class AddCodeCellTool(Tool):
    def __init__(self, auto_approve: bool = False):
        self._auto_approve = auto_approve
        super().__init__()

    @property
    def name(self) -> str:
        return "add_code_cell_to_notebook"

    @property
    def title(self) -> str:
        return "Add code cell to notebook"
    
    @property
    def tags(self) -> list[str]:
        return ["default-participant-tool"]
    
    @property
    def description(self) -> str:
        return "This is a tool that adds code cell to a notebook"
    
    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "notebook_file_path": {
                            "type": "string",
                            "description": "Notebook file path to add the markdown cell to",
                        },
                        "code_cell_source": {
                            "type": "string",
                            "description": "Code to add to the notebook",
                        }
                    },
                    "required": ["notebook_file_path", "code_cell_source"],
                    "additionalProperties": False,
                },
            },
        }

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        confirmationTitle = None
        confirmationMessage = None
        if not self._auto_approve:
            confirmationTitle = "Approve"
            confirmationMessage = "Are you sure you want to call this tool?"
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", detail={"title": "Parameters", "content": json.dumps(tool_args)}, confirmationTitle=confirmationTitle, confirmationMessage=confirmationMessage)

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        notebook_file_path = tool_args.get('notebook_file_path', '')
        server_root_dir = request.host.nbi_config.server_root_dir
        if notebook_file_path.startswith(server_root_dir):
            notebook_file_path = os.path.relpath(notebook_file_path, server_root_dir)
        source = tool_args.get('code_cell_source')
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-notebook', {'code': source, 'path': notebook_file_path})
        return "Added code cell added to notebook"

# Fallback tool to handle tool errors
class PythonTool(AddCodeCellTool):
    @property
    def name(self) -> str:
        return "python"

    @property
    def title(self) -> str:
        return "Add code cell to notebook"
    
    @property
    def tags(self) -> list[str]:
        return ["default-participant-tool"]
    
    @property
    def description(self) -> str:
        return "This is a tool that adds code cell to a notebook"
    
    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "strict": True,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code_cell_source": {
                            "type": "string",
                            "description": "Code to add to the notebook",
                        }
                    },
                    "required": ["code_cell_source"],
                    "additionalProperties": False,
                },
            },
        }

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> str:
        code = tool_args.get('code_cell_source')
        ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-notebook', {'code': code, 'path': tool_context.get('file_path')})
        return {"result": "Code cell added to notebook"}

class BaseChatParticipant(ChatParticipant):
    def __init__(self, rule_injector=None):
        super().__init__()
        self._current_chat_request: ChatRequest = None
        self._rule_injector = rule_injector or RuleInjector()

    @property
    def id(self) -> str:
        return "default"
    
    @property
    def name(self) -> str:
        return "AI Assistant"

    @property
    def description(self) -> str:
        return "AI Assistant"
    
    @property
    def icon_path(self) -> str:
        return ICON_URL
    
    @property
    def commands(self) -> list[ChatCommand]:
        return [
            ChatCommand(name='newNotebook', description='Create a new notebook'),
            ChatCommand(name='newPythonFile', description='Create a new Python file'),
            ChatCommand(name='clear', description='Clears chat history'),
        ]

    @property
    def tools(self) -> list[Tool]:
        tool_list = []
        chat_mode = self._current_chat_request.chat_mode
        if chat_mode.id == "ask":
            tool_list = [AddMarkdownCellToNotebookTool(), AddCodeCellTool(), PythonTool()]
        elif chat_mode.id == "agent":
            tool_selection = self._current_chat_request.tool_selection
            host = self._current_chat_request.host
            for toolset in tool_selection.built_in_toolsets:
                built_in_toolset = built_in_toolsets[toolset]
                tool_list += built_in_toolset.tools
            for server_name, mcp_server_tool_list in tool_selection.mcp_server_tools.items():
                for tool_name in mcp_server_tool_list:
                    mcp_server_tool = host.get_mcp_server_tool(server_name, tool_name)
                    if mcp_server_tool is not None:
                        tool_list.append(mcp_server_tool)
            for ext_id, ext_toolsets in tool_selection.extension_tools.items():
                for toolset_id, toolset_tools in ext_toolsets.items():
                    for tool_name in toolset_tools:
                        ext_tool = host.get_extension_tool(ext_id, toolset_id, tool_name)
                        if ext_tool is not None:
                            tool_list.append(SecuredExtensionTool(ext_tool))
        return tool_list

    @property
    def allowed_context_providers(self) -> set[str]:
        # any context provider can be used
        return set(["*"])
    
    def chat_prompt(self, model_provider: str, model_name: str) -> str:
        return Prompts.generic_chat_prompt(model_provider, model_name)
    
    def _inject_rules_into_system_prompt(self, base_prompt: str, request: ChatRequest) -> str:
        """Inject applicable rules into system prompt based on request context."""
        return self._rule_injector.inject_rules(base_prompt, request)

    async def generate_code_cell(self, request: ChatRequest) -> str:
        chat_model = request.host.chat_model
        messages = request.chat_history.copy()
        messages.pop()
        messages.insert(0, {"role": "system", "content": f"You are an assistant that creates Python code which will be used in a Jupyter notebook. Generate only Python code and some comments for the code. You should return the code directly, without wrapping it inside ```."})
        messages.append({"role": "user", "content": f"Generate code for: {request.prompt}"})
        generated = chat_model.completions(messages)
        code = generated['choices'][0]['message']['content']
        
        return extract_llm_generated_code(code)
    
    async def generate_markdown_for_code(self, request: ChatRequest, code: str) -> str:
        chat_model = request.host.chat_model
        messages = request.chat_history.copy()
        messages.pop()
        messages.insert(0, {"role": "system", "content": f"You are an assistant that explains the provided code using markdown. Don't include any code, just narrative markdown text. Keep it concise, only generate few lines. First create a title that suits the code and then explain the code briefly. You should return the markdown directly, without wrapping it inside ```."})
        messages.append({"role": "user", "content": f"Generate markdown that explains this code: {code}"})
        generated = chat_model.completions(messages)
        markdown = generated['choices'][0]['message']['content']

        return extract_llm_generated_code(markdown)

    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        self._current_chat_request = request
        if request.chat_mode.id == "ask":
            return await self.handle_ask_mode_chat_request(request, response, options)
        elif request.chat_mode.id == "agent":
            system_prompt = None
            if len(self.tools) > 0:
                system_prompt = "Try to answer the question with a tool first. If the tool you use has default values for parameters and user didn't provide a value for those, make sure to set the default value for the parameter.\n\n"

            for toolset in request.tool_selection.built_in_toolsets:
                built_in_toolset = built_in_toolsets[toolset]
                if built_in_toolset.instructions is not None:
                    system_prompt += built_in_toolset.instructions + "\n"

            for extension_id, toolsets in request.tool_selection.extension_tools.items():
                for toolset_id in toolsets.keys():
                    ext_toolset = request.host.get_extension_toolset(extension_id, toolset_id)
                    if ext_toolset is not None and ext_toolset.instructions is not None:
                        system_prompt += ext_toolset.instructions + "\n"

            # Inject rules into agent mode system prompt
            if system_prompt:
                system_prompt = self._inject_rules_into_system_prompt(system_prompt, request)
            else:
                # Even if no system prompt, we might have rules to inject
                system_prompt = self._inject_rules_into_system_prompt("", request)
                if system_prompt == "":
                    system_prompt = None
            
            options = options.copy()
            options["system_prompt"] = system_prompt

            mcp_servers_used = []
            for server_name in request.tool_selection.mcp_server_tools.keys():
                mcp_server = request.host.get_mcp_server(server_name)
                if mcp_server not in mcp_servers_used:
                    mcp_servers_used.append(mcp_server)

            await self.handle_chat_request_with_tools(request, response, options)

    async def handle_ask_mode_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        chat_model = request.host.chat_model
        if request.command == 'newNotebook':
            # create a new notebook
            ui_cmd_response = await response.run_ui_command('notebook-intelligence:create-new-notebook-from-py', {'code': ''})
            file_path = ui_cmd_response['path']

            code = await self.generate_code_cell(request)
            markdown = await self.generate_markdown_for_code(request, code)

            ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-markdown-cell-to-notebook', {'markdown': markdown, 'path': file_path})
            ui_cmd_response = await response.run_ui_command('notebook-intelligence:add-code-cell-to-notebook', {'code': code, 'path': file_path})

            response.stream(MarkdownData(f"Notebook '{file_path}' created and opened successfully"))
            response.finish()
            return
        elif request.command == 'newPythonFile':
            # create a new python file
            messages = request.chat_history.copy()
            messages.pop()
            messages.insert(0, {"role": "system", "content": f"You are an assistant that creates Python code. You should return the code directly, without wrapping it inside ```."})
            messages.append({"role": "user", "content": f"Generate code for: {request.prompt}"})
            generated = chat_model.completions(messages)
            code = generated['choices'][0]['message']['content']
            code = extract_llm_generated_code(code)
            ui_cmd_response = await response.run_ui_command('notebook-intelligence:create-new-file', {'code': code })
            file_path = ui_cmd_response['path']
            response.stream(MarkdownData(f"File '{file_path}' created successfully"))
            response.finish()
            return
        elif request.command == 'settings':
            ui_cmd_response = await response.run_ui_command('notebook-intelligence:open-configuration-dialog')
            response.stream(MarkdownData(f"Opened the settings dialog"))
            response.finish()
            return

        # Inject rules into system prompt
        base_system_prompt = options.get("system_prompt", self.chat_prompt(chat_model.provider.name, chat_model.name))
        enhanced_system_prompt = self._inject_rules_into_system_prompt(base_system_prompt, request)
        
        messages = [
            {"role": "system", "content": enhanced_system_prompt},
        ] + request.chat_history

        try:
            if chat_model.provider.id != "github-copilot":
                response.stream(ProgressData("Thinking..."))
            chat_model.completions(messages, response=response, cancel_token=request.cancel_token)
        except Exception as e:
            log.error(f"Error while handling chat request!\n{e}")
            response.stream(MarkdownData(f"Oops! There was a problem handling chat request. Please try again with a different prompt."))
            response.finish()

    @staticmethod
    def get_tool_by_name(name: str) -> Tool:
        if name == "create_new_notebook":
            return CreateNewNotebookTool()
        elif name == "add_markdown_cell_to_notebook":
            return AddMarkdownCellToNotebookTool()
        elif name == "add_code_cell_to_notebook":
            return AddCodeCellTool()

        return None
