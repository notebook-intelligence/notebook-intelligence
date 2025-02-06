---
layout: post
title:  "Building AI Extensions for JupyterLab"
date:   2025-02-04 18:49:32 -0800
categories: blog
---

[Notebook Intelligence](https://github.com/notebook-intelligence/notebook-intelligence) (NBI) is an AI coding assistant and extensible AI framework for JupyterLab. For an introduction to NBI see [Introducing Notebook Intelligence blog post]({{site.baseurl}}{% post_url 2025-01-07-introducing-notebook-intelligence %}) first.

GitHub Copilot and other AI coding assistants generate chat responses based on publicly available knowledge and they do not have access to your workspace, tools and services. NBI provides Extension APIs to build AI extensions for JupyterLab. By extending NBI, you can build custom chat interactions and provide access to proprietary or external data, tools and services. This lets you build custom, AI powered chat experiences, natural language interface to JupyterLab and your tools.

## NBI Extensions Overview

NBI can be extended by building an NBI extension using Python. Extensions are distributed as Python packages and they need to be installed on the same Python environment as the NBI and JupyterLab. Once installed they add a `extension.json` file with the extension metadata so that NBI can load the extension during initialization. This metadata file needs to be installed into `<python-env>/share/jupyter/nbi_extensions/<extension-name>/extension.json`.

extension.json file contains the extension's class name.
```json
{
  "class": "<extension-name>.<ExtensionClass>"
}

```

NBI extensions are executed in the backend as part of Jupyter server process. NBI instantiates an instance of each NBI extension during Jupyter server launch and interacts with the extensions as needed.

Extensions can add custom chat participants and commands specific to that participant.

![Extension demo](/notebook-intelligence/assets/images/response-types-demo.gif)

### UI Access by extensions

NBI extensions can show UI components on the chat interface as part of the chat responses they generate. Extensions can also trigger JupyterLab UI commands and listen for the command responses.

### Anatomy of a chat request

A chat request input in Copilot Chat is made up of three parts and in the order: participant, command and prompt
```
@<participant-id> /<command-name> <prompt>
```
Example: `@example /repeat Hello world!`

Participants and commands are defined by the Copilot Chat or NBI extensions. They are automatically listed on the chat auto-complete list.

If no participant is specified then the request is handled by the default participant which is GitHub Copilot. Commands are specific to a participant type. NBI parses the user input and routes the requests to user specified participants in the prompt.

## Extension Example

Let's walk through the NBI extensibility features with an example extension. The full source code for this extension is [available here](https://github.com/notebook-intelligence/nbi-extension-example){:target="_blank"}.

### Extension class

Extensions are defined as a class derived from  `NotebookIntelligenceExtension` which is an abstract class and an extension needs to implement the methods and properties defined in this base class. Extension class provides the metadata information on the extension and implements the `activate` method. `activate` method is called when NBI initializes the extension.

`activate` method is called with a `host` parameter which is the extension host provided by NBI. Using the host methods, you can register your chat participants.

```python
class ExampleExtension(NotebookIntelligenceExtension):
    @property
    def id(self) -> str:
        return "example-extension"

    @property
    def name(self) -> str:
        return "Example Extension"

    @property
    def provider(self) -> str:
        return "Mehmet Bektas"

    @property
    def url(self) -> str:
        return "https://github.com/mbektas"

    def activate(self, host: Host) -> None:
        self.participant = ExampleChatParticipant(host)
        host.register_chat_participant(self.participant)
        log.info("Example extension activated")

```

### Chat Participant

NBI lists all available chat participants with their commands in the prompt auto-complete list. Chat participants are identified by `@participant-id`. If a chat prompt starts with `@participant-id` the request is routed to the particular chat participant with that id.

![Chat participants](/notebook-intelligence/assets/images/chat-participants.png){: width="400" }

Extensions can define chat participants to handle the chat requests. Chat participants can define commands. Commands start with `/` and they can be designed to work with or without a prompt. It lets the chat participant developer have more control over the prompt handling. Commands defined by chat participants are listed in the prompt auto-complete list as well.

A chat participant class derives from the abstract base class `ChatParticipant` and implements the required methods and properties of it. In the example below `ExampleChatParticipant` defines a command named `repeat`. If the prompt is called with `repeat` command (`@example /repeat some prompt`) it simply echoes the prompt. If no command is specified it passes the request to the default chat participant which is GitHub Copilot.

```python
class ExampleChatParticipant(ChatParticipant):
    @property
    def id(self) -> str:
        return "example"

    @property
    def name(self) -> str:
        return "Example Participant"
    
    @property
    def description(self) -> str:
        return "An example participant"

    @property
    def icon_path(self) -> str:
        return PARTICIPANT_ICON_URL

    @property
    def commands(self) -> list[ChatCommand]:
        return [
            ChatCommand(name='repeat', description='Repeats the prompt')
        ]

    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        if (request.command == 'repeat'):
            response.stream(MarkdownData(f"Repeating: {request.prompt}"))
            response.finish()
            return

        await self.host.default_chat_participant.handle_chat_request(request, response, options)
```

### Chat response types

NBI routes requests to chat participants as needed and passes a `response` object of type `ChatResponse` to the participants. Chat participant sends messages to the UI using the `response` object as it is generating a response for the user prompt. NBI supports various types of response message types as listed below. Once a chat participant is done generating, it calls `response.finish()` method to signal to the UI that it is done.

Chat participant calls the `response.stream(<message type>)` method to send streaming responses to the UI. Below are the message types with examples.

#### Markdown
This is the most common response type, any text response with formatting can be sent in response to a prompt with this message type.

```python
response.stream(MarkdownData("Hello world!"))
```

![Markdown response type](/notebook-intelligence/assets/images/response-types/response-type-markdown.png){: width="400" }

Markdown response can contain code sections. Code sections will be rendered in a special frame with action buttons on the header area for easy integration into the workspace.

```python
response.stream(MarkdownData("""Here is a Python method I generated. \n```python\ndef show_message():\n  print('Hello world!')\n```\n"""))
```

![Markdown code response](/notebook-intelligence/assets/images/response-types/response-type-markdown-code.png){: width="400" }

#### HTMLFrame

HTML content can be sent using this message type. Note that the content will always be rendered in a sandboxed iframe that allows scripts. You can also specify height of the frame in pixels as it won't auto resize to content.

```python
response.stream(HTMLFrameData(f"""
    <div>
        <img style="width: 100%" src="https://jupyter.org/assets/homepage/main-logo.svg" />
    </div>
    """, height=400))
```

![HTMLFrame response type](/notebook-intelligence/assets/images/response-types/response-type-htmlframe.png){: width="400" }

#### Button
This response type lets you show an action button on chat interface. You can specify the title of the button, the UI command ID to trigger when the button is clicked and also any arguments to pass to the UI command. The example button below shows a notification message on JupyterLab UI when clicked.

```python
response.stream(ButtonData(
    title="Button title",
    commandId="apputils:notify",
    args ={
        "message": 'Copilot chat button was clicked',
        "type": 'success',
        "options": { "autoClose": False }
    })
)
```

![Button response type](/notebook-intelligence/assets/images/response-types/response-type-button.png)

#### Anchor
Anchor response type lets you show a link on the chat interface. You can specify the URL and the title of the link. These links are always opened on a new browser tab.

```python
response.stream(AnchorData("https://www.jupyter.org", "Click me! I am a link!"))
```
![Anchor response type](/notebook-intelligence/assets/images/response-types/response-type-anchor.png){: width="400" }

#### Progress
Progress response type lets you show a progress message. You can send this response before executing a long running task. It will be automatically removed once a new response is received by the UI.

```python
response.stream(ProgressData("Running..."))
```

![Progress response type](/notebook-intelligence/assets/images/response-types/response-type-progress.png){: width="400" }

#### Confirmation
Confirmation response type shows a confirmation message with confirm and cancel buttons on the chat UI and waits for the user to click an option. You can use this before applying irreversible changes or time consuming tasks for example.

```python
callback_id = uuid.uuid4().hex
response.stream(ConfirmationData(
    title="Confirm",
    message="Are you sure you want to continue?",
    confirmArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": True}}},
    cancelArgs={"id": response.message_id, "data": { "callback_id": callback_id, "data": {"confirmed": False}}},
    confirmLabel="Confirm",
    cancelLabel="Cancel"
))

user_input = await ChatResponse.wait_for_chat_user_input(response, callback_id)
if user_input['confirmed'] == False:
    response.stream(MarkdownData("User cancelled the action."))
    response.finish()
else:
    response.stream(MarkdownData("User confirmed the action."))
    response.finish()
```

![Confirmation response type](/notebook-intelligence/assets/images/response-types/response-type-confirmation.gif)

### Running UI Commands
You can trigger Jupyter UI commands directly from your extension when processing prompt requests. You can do that by calling `response.run_ui_command` method. The response for the command will be returned if any.

```python
ui_cmd_response = await response.run_ui_command(
    command='apputils:notify',
    args={
        "message": 'Copilot chat button was clicked',
        "type": 'success',
        "options": { "autoClose": False }
    }
)
```

### Handling chat request cancellations

Users might want to cancel chat requests for various reasons such as if they are taking longer than expected. NBI lets you easily handle cancel requests coming from the user. ChatRequest object has a member named `cancel_token` which passes cancel signals from the user to your extension. In your chat request handler you should check for cancellation flag (`request.cancel_token.is_cancel_requested`) and handle cancellations before running sections of your code and especially before time consuming steps. You can also listen for cancellation signal (`request.cancel_token.cancellation_signal.connect`) if that works for your use case.

```python
async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
    def cancellation_handler():
        response.stream(MarkdownData("Cancel event received")

    request.cancel_token.cancellation_signal.connect(cancellation_handler)

    if request.cancel_token.is_cancel_requested:
        response.stream(MarkdownData("Cancelled"))
        response.finish()
        return

    # time consuming execution
    ...
    response.stream(MarkdownData("Handled chat request"))
    response.finish()
```

![Cancelling request](/notebook-intelligence/assets/images/canceling-chat-request.gif)

## Try it out and share your feedback!

I am looking forward to seeing the extensions built by the community. Please try the extension APIs and share your feedback using project's [GitHub issues](https://github.com/notebook-intelligence/notebook-intelligence/issues)! User feedback from the community will shape the project's roadmap. Please note that [Notebook Intelligence](https://github.com/notebook-intelligence/notebook-intelligence) is currently in beta and extension APIs are subject to change.

## About the Author

[Mehmet Bektas](https://www.linkedin.com/in/mehmet-bektas) is a Senior Software Engineer at Netflix and a Jupyter Distinguished Contributor. He is the author of Notebook Intelligence, and contributes to JupyterLab, JupyterLab Desktop and several other projects in the Jupyter eco-system.

The source code for the example extension in this post is available [on GitHub](https://github.com/notebook-intelligence/nbi-extension-example).
