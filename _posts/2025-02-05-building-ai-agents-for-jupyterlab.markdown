---
layout: post
title:  "Building AI Agents for JupyterLab"
date:   2025-02-06 01:00:00 -0800
categories: blog
---

[Notebook Intelligence](https://github.com/notebook-intelligence/notebook-intelligence) (NBI) is an AI coding assistant and extensible AI framework for JupyterLab. For an introduction to NBI see [Introducing Notebook Intelligence blog post]({% post_url 2025-01-07-introducing-notebook-intelligence %}) and for basics of extending NBI see [Extending Copilot Chat in JupyterLab]({% post_url 2025-02-04-building-ai-extensions-for-jupyterlab %}) blog posts.

GitHub Copilot and other AI coding assistants are great at generating code and answering coding related questions. But they can do a lot more than generating text and code thanks to LLM features such as tool calling and AI agents. NBI provides an extensible AI framework to integrate tool calling and AI agents into JupyterLab Copilot Chat.

## What is tool calling and an AI Agent?

**Tool calling** is a feature of LLMs. It lets you introduce your own functions to LLM so that they can be called in response to chat prompts. LLM can convert natural language prompts to function calls with arguments. Tool calls are executed on the client side (i.e. Jupyter server) by your extension and only the function schema is provided to the LLM. Tool calling lets LLM interact with real time data, proprietary or external apps and services.

**AI Agents** are collections of tools that can run tasks on behalf of user. Given a natural language prompt, LLMs can reason, create an execution plan and call multiple tools in a chain. NBI provides a framework to build these type of AI Agent integrations.

## AI Agent Extension Example

Let's build an AI Agent for JupyterLab using Notebook Intelligence extension APIs. This will be an AI agent for map creation and notebook sharing. It will have the following capabilities:
- Looking up geo-coordinates for an address
- Showing the map centered at an address in the Copilot Chat UI
- Creating a notebook that shows a map centered at a specified address
- Sharing notebooks publicly using [notebooksharing.space](https://notebooksharing.space){:target="_blank"}

The tasks above will be run by the AI Agent in response to natural language prompts by the user.

For this extension we will build four tools that will be integrated into JupyterLab Copilot Chat, for each of the tasks above. Tools are defined as classes derived from `Tool` abstract class. A tool needs to implement the methods and properties defined in this base class.

Tool class provides the metadata information for the tool and implements the `pre_invoke` and `handle_tool_call` methods. `pre_invoke` method is called right before `handle_tool_call` with the tool arguments and it gives an opportunity for the tool to prompt for confirmation of the tool call.

`schema` property of the Tool is the function schema based on OpenAI's function calling schema. It lets you describe your function and its parameters as an object. A Tool is expected to return an object as a response from `handle_tool_call` method call.

The full source code for this extension is [available here](https://github.com/notebook-intelligence/nbi-ai-agent-example){:target="_blank"}.

### Geo Coordinates Lookup Tool

This tool looks up geo-coordinates for an address using [Nominatim](https://github.com/geopy/geopy){:target="_blank"} library. `pre_invoke` method for this tool only shows a message in Chat UI before looking up for the geo-coordinates in `handle_tool_call` method.

```python
class GeoCoordinateLookupTool(Tool):
    @property
    def name(self) -> str:
        return "geo_coordinate_lookup"

    @property
    def title(self) -> str:
        return "Get geo-coordinates from an address"
    
    @property
    def description(self) -> str:
        return "This is a tool that converts an address to a geo-coordinates"
    
    @property
    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": "Address to convert to geo-coordinates",
                        }
                    },
                    "required": ["address"],
                    "additionalProperties": False,
                },
            },
        }

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        address = tool_args.get('address')
        return ToolPreInvokeResponse(
            message=f"Getting coordinates for '{address}'"
        )

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> dict:
        address = tool_args.get('address')
        location = geolocator.geocode(address)
        return {"latitude": location.latitude, "longitude": location.longitude}
```

![Geo coordinates lookup tool](/notebook-intelligence/assets/images/ai-agent-blog/ai-agent-geo-coords.png)

### Map Response Generator Tool

This tool shows a map in Copilot Chat UI centered at geo-coordinates. In `pre_invoke` method this method only shows a notification message in Chat UI. In `handle_tool_call` method, this tool returns a `HTMLFrame` response that uses HTML to show a map centered at the requested location using Google Maps.

```python
class MapResponseGeneratorTool(Tool):
    ...

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        geo_coordinates = tool_args.get('geo_coordinates')
        latitude = geo_coordinates.get('latitude')
        longitude = geo_coordinates.get('longitude')
        return ToolPreInvokeResponse(
            message=f"Showing a map centered at latitude: {latitude} and longitude: {longitude}"
        )

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> dict:
        geo_coordinates = tool_args.get('geo_coordinates')
        latitude = geo_coordinates.get('latitude')
        longitude = geo_coordinates.get('longitude')

        response.stream(HTMLFrameData(f"""<iframe width="100%" height="100%" frameborder="0" scrolling="no" marginheight="0" marginwidth="0" id="gmap_canvas" src="https://maps.google.com/maps?width=400&amp;height=400&amp;hl=en&amp;q={latitude},{longitude}&amp;t=&amp;z=11&amp;ie=UTF8&amp;iwloc=B&amp;output=embed"></iframe>""", height=400))
        response.finish()

        return {"result": "I showed the map"}
```

Below is an example prompt showing map centered at "Golden Gate Bridge, San Francisco". Note that an address was provided to Copilot as the input but Map Response Generator Tool accepts only geo-coordinates as input. This is where LLM automatically decided that it needs to first call the Geo Coordinates Lookup Tool to get geo-coordinates for this address and then it called the Map Response Generator Tool with the geo-coordinates. LLM automatically chained multiple tools and NBI handled this chaining to get the correct response for the user's prompt.

![Show map response tool](/notebook-intelligence/assets/images/ai-agent-blog/ai-agent-show-map.png)

### Map Notebook Creator Tool

This tool creates a notebook centered at the specified geo-coordinates. In `pre_invoke` method this method only shows a notification message in Chat UI. In `handle_tool_call` method, the tool creates a notebook using `nbformat` library, saves it to disk and then opens the notebook in JupyterLab UI using the `response.run_ui_command` NBI method.

```python
class MapNotebookCreatorTool(Tool):
    ...

    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        geo_coordinates = tool_args.get('geo_coordinates')
        latitude = geo_coordinates.get('latitude')
        longitude = geo_coordinates.get('longitude')
        return ToolPreInvokeResponse(
            message=f"Creating a map notebook for latitude: {latitude} and longitude: {longitude}"
        )

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> dict:
        geo_coordinates = tool_args.get('geo_coordinates')
        latitude = geo_coordinates.get('latitude')
        longitude = geo_coordinates.get('longitude')

        now = dt.datetime.now()
        map_file_name = f"map_{now.strftime('%Y%m%d_%H%M%S')}.ipynb"

        nb = nbf.v4.new_notebook()
        header = """\
        ### This map notebook was created by an AI Agent using [Notebook Intelligence](https://github.com/notebook-intelligence)
        """

        install_code_cell = "%%capture\n%pip install folium"

        map_code_cell = f"""\
        import folium

        map = folium.Map(location=[{latitude}, {longitude}], zoom_start=13)
        map"""

        nb['cells'] = [
            nbf.v4.new_markdown_cell(header),
            nbf.v4.new_code_cell(install_code_cell),
            nbf.v4.new_code_cell(map_code_cell)
        ]
        nb.metadata["kernelspec"] = { "name": "python3"}
        nbf.write(nb, map_file_name)

        await response.run_ui_command("docmanager:open", {"path": map_file_name})

        return {"result": "I created and opened the map notebook"}
```

![Create map notebook tool](/notebook-intelligence/assets/images/ai-agent-blog/ai-agent-create-notebook.png)

### Notebook Share Tool

This tool shares a notebook publicly by uploading it to [notebooksharing.space](https://notebooksharing.space){:target="_blank"} and displays the link to the shared notebook.

In the `pre_invoke` method implementation, this tool asks for confirmation first as this operation is an undoable share of the notebook publicly. Only after the user confirms, `handle_tool_call` is executed. In `handle_tool_call` method the tool uploads the notebook at the `notebook_file_path` using [nbss_upload](https://github.com/notebook-sharing-space/nbss-upload){:target="_blank"} library and then shows the link to the shared notebook on [notebooksharing.space](https://notebooksharing.space){:target="_blank"}.

```python
class NotebookShareTool(Tool):
    ...
    
    def pre_invoke(self, request: ChatRequest, tool_args: dict) -> Union[ToolPreInvokeResponse, None]:
        file_path = tool_args.get('notebook_file_path')
        file_name = path.basename(file_path)
        return ToolPreInvokeResponse(
            message=f"Sharing notebook '{file_name}'",
            confirmationTitle="Confirm sharing",
            confirmationMessage=f"Are you sure you want to share the notebook at '{file_path}'? This will upload the notebook to public internet and cannot be undone."
        )

    async def handle_tool_call(self, request: ChatRequest, response: ChatResponse, tool_context: dict, tool_args: dict) -> dict:
        file_path = tool_args.get('notebook_file_path')
        file_name = path.basename(file_path)
        share_url = nbss_upload.upload_notebook(file_path, False, False, 'https://notebooksharing.space')
        response.stream(AnchorData(share_url, f"Click here to view the shared notebook '{file_name}'"))

        return {"result": f"Notebook '{file_name}' has been shared at: {share_url}"}
```

![Share notebook publicly](/notebook-intelligence/assets/images/ai-agent-blog/ai-agent-share.gif)

## Tool call schema definitions

It is important to define schemas of the tools clearly and disambiguate the tools as much as possible so that the LLM can invoke the proper tool based on the user prompt. LLM parses the user prompt, decides which tools to call and generates the input parameters for the call. If current file or selection is made visible by the user, NBI can provide the file paths and content as context to the LLM. That way LLM can use those as additional context for a tool call.

## Tool chaining

After parsing the user prompt, LLMs creates an execution plan and can call multiple tools in a chain. NBI handles this tool chaining for you. It is important to define your schemas with the chaining in mind. Consider defining matching tool outputs and inputs so that the output of a tool can be passed onto another one directly if needed.

Notice that in this extension example MapResponseGeneratorTool and MapNotebookCreatorTool both take in geo_coordinates (latitude, longitude) as input and GeoCoordinateLookupTool outputs geo_coordinates. This lets LLM to directly pass the output of GeoCoordinateLookupTool to MapResponseGeneratorTool and MapNotebookCreatorTool. It also lets a user to use an address to trigger MapResponseGeneratorTool and MapNotebookCreatorTool, because LLM knows that there is another tool it can call to generate input (geo_coordinates) from address for these tools. 

## Chat Participant

In NBI AI framework, Tools are tied to Chat Participants. For our extension we create `AIAgentChatParticipant` as our participant (for more details on NBI extensions and chat participants see [this blog]({% post_url 2025-02-04-building-ai-extensions-for-jupyterlab %})). Our chat participant returns list of tools it defines from the `tools` property.

In `handle_chat_request` method our chat participant passes the request to the base `ChatParticipant` class to handle tool calling for us.

```python
class AIAgentChatParticipant(ChatParticipant):
    @property
    def id(self) -> str:
        return "ai-agent"
    ...
    
    @property
    def tools(self) -> list[Tool]:
        return [GeoCoordinateLookupTool(), MapResponseGeneratorTool(), MapNotebookCreatorTool(), NotebookShareTool()]

    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        ...
        await self.handle_chat_request_with_tools(request, response, options)
```

## NBI Extension

Finally we create our NBI extension class `AIAgentExtension`. This class basically registers our chat participant to NBI on extension activation. 

```python
class AIAgentExtension(NotebookIntelligenceExtension):
    ...

    def activate(self, host: Host) -> None:
        self.participant = AIAgentChatParticipant(host)
        host.register_chat_participant(self.participant)
        log.info("AI Agent example extension activated")
```

That is all there is to create an AI Agent for JupyterLab using Notebook Intelligence. The [full source code](https://github.com/notebook-intelligence/nbi-ai-agent-example) for this example is available along with installation instructions for you to use as a reference and/or build on top.

## Try it out and share your feedback!

I am looking forward to seeing the AI Agents built by the community. Please try the extension APIs and share your feedback using project's [GitHub issues](https://github.com/notebook-intelligence/notebook-intelligence/issues)! User feedback from the community will shape the project's roadmap.

## About the Author

[Mehmet Bektas](https://www.linkedin.com/in/mehmet-bektas) is a Senior Software Engineer at Netflix and a Jupyter Distinguished Contributor. He is the author of Notebook Intelligence, and contributes to JupyterLab, JupyterLab Desktop and several other projects in the Jupyter eco-system.

The source code for the example extension in this post is available [on GitHub](https://github.com/notebook-intelligence/nbi-ai-agent-example).
