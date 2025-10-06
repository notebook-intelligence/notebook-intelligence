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

ICON_SVG = '<svg width="15" height="16" viewBox="0 0 15 16" fill="none" xmlns="http://www.w3.org/2000/svg"> <path d="M12.2734 7.61838C12.303 7.66057 12.1709 7.73361 12.1371 7.75565C10.9546 8.53959 9.41936 9.08173 8.19951 9.83797C7.48763 11.1338 6.97728 12.7647 6.23932 14.0209C6.21798 14.0568 6.14922 14.1972 6.1101 14.1657L4.12087 9.88708C2.89746 9.07796 1.34567 8.5547 0.140627 7.75565C0.106841 7.73298 -0.0253401 7.65994 0.00429685 7.61838L4.06574 5.47813L6.10951 1.13215C6.14922 1.10066 6.21798 1.24108 6.23873 1.27697C6.99091 2.55709 7.48407 4.20557 8.24515 5.50521L12.2728 7.61838H12.2734ZM9.42825 7.61838L7.29736 6.51268C7.15332 6.36848 6.21087 4.07397 6.10951 4.15456L5.01353 6.4856L2.84885 7.61838C2.77298 7.72542 4.93292 8.72723 5.06866 8.88024L6.10951 11.1439C6.14922 11.1754 6.21798 11.035 6.23873 10.9991C6.49953 10.5552 7.01403 9.08236 7.25053 8.83112C7.48703 8.57989 8.87345 8.03333 9.29133 7.75628C9.32512 7.73361 9.4573 7.66057 9.42766 7.61901L9.42825 7.61838Z" fill="url(#paint0_linear_16449_285)"/> <path d="M12.2135 10.0735C12.4743 10.5356 12.7891 11.6376 13.102 11.9952C13.3154 12.2395 14.4571 12.7156 14.824 12.9391C14.885 12.9762 14.9727 12.9359 14.9413 13.0638C14.9158 13.1683 13.3522 13.8213 13.1317 14.0391C12.9112 14.257 12.4536 15.4786 12.2432 15.8683C12.2082 15.9332 12.2461 16.0264 12.1258 15.993C12.0274 15.9659 11.4127 14.3048 11.2077 14.0706C11.0026 13.8364 9.85265 13.3503 9.48575 13.1267C9.4247 13.0896 9.33697 13.1299 9.36839 13.0021C9.39387 12.8975 10.9575 12.2446 11.178 12.0267C11.4542 11.7541 11.8253 10.5741 12.0808 10.1541C12.1181 10.093 12.0742 10.0414 12.2123 10.0735H12.2135Z" fill="url(#paint1_linear_16449_285)"/> <path d="M14.9994 2.26618L13.546 3.08224L12.7766 4.5947C12.7037 4.63248 12.1958 3.2151 12.0333 3.05516C11.7791 2.80581 10.9504 2.5319 10.6137 2.32726C10.5527 2.241 11.9302 1.60251 12.0375 1.47972C12.1904 1.30531 12.6818 0 12.7772 0C13.0754 0.337503 13.2603 1.22912 13.5466 1.51184C13.8329 1.79456 14.6426 2.0521 15 2.26681L14.9994 2.26618Z" fill="url(#paint2_linear_16449_285)"/> <defs> <linearGradient id="paint0_linear_16449_285" x1="-3" y1="19.5" x2="15" y2="3" gradientUnits="userSpaceOnUse"> <stop stop-color="#00ADB5"/> <stop offset="1" stop-color="#FFD900"/> </linearGradient> <linearGradient id="paint1_linear_16449_285" x1="-3" y1="19.5" x2="15" y2="3" gradientUnits="userSpaceOnUse"> <stop stop-color="#00ADB5"/> <stop offset="1" stop-color="#FFD900"/> </linearGradient> <linearGradient id="paint2_linear_16449_285" x1="-3" y1="19.5" x2="15" y2="3" gradientUnits="userSpaceOnUse"> <stop stop-color="#00ADB5"/> <stop offset="1" stop-color="#FFD900"/> </linearGradient> </defs> </svg>'
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
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", confirmationTitle, confirmationMessage)

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
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", confirmationTitle, confirmationMessage)

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
        return ToolPreInvokeResponse(f"Calling tool '{self.name}'", confirmationTitle, confirmationMessage)

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
        return "Softie"

    @property
    def description(self) -> str:
        return "Softie"
    
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
