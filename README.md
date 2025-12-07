# Notebook Intelligence

Notebook Intelligence (NBI) is an AI coding assistant and extensible AI framework for JupyterLab. It can use GitHub Copilot or AI models from any other LLM Provider, including local models from [Ollama](https://ollama.com/). NBI greatly boosts the productivity of JupyterLab users with AI assistance.

## Feature Highlights

### Agent Mode

In Agent Mode, built-in AI agent creates, edits and executes notebooks for you interactively. It can detect issues in the cells and fix for you.

![Agent mode](media/agent-mode.gif)

### Code generation with inline chat

Use the sparkle icon on cell toolbar or the keyboard shortcuts to show the inline chat popover.

Keyboard shortcuts: `Ctrl + G` / `Cmd + G` is the shortcut to show the inline chat popover and `Ctrl + Enter` / `Cmd + Enter` is the shortcut to accept the suggestion. Clicking `Escape` key closes the popover.

![Generate code](media/generate-code.gif)

### Auto-complete

Auto-complete suggestions are shown as you type. Clicking `Tab` key accepts the suggestion. NBI provides auto-complete suggestions in code cells and Python file editors.

<img src="media/inline-completion.gif" alt="Auto-complete" width=700 />

### Chat interface

<img src="media/copilot-chat.gif" alt="Chat interface" width=600 />

See blog posts for more features and usage.

- [Introducing Notebook Intelligence!](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/01/08/introducing-notebook-intelligence.html)
- [Building AI Extensions for JupyterLab](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/02/05/building-ai-extensions-for-jupyterlab.html)
- [Building AI Agents for JupyterLab](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/02/09/building-ai-agents-for-jupyterlab.html)
- [Notebook Intelligence now supports any LLM Provider and AI Model!](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/03/05/support-for-any-llm-provider.html)

## Installation

NBI requires JupyterLab >= 4.0.0. To install the extension, run the command below and restart JupyterLab.

```bash
pip install notebook-intelligence
```

## Configuration options

### Configuring LLM Provider and models

You can configure the model provider and model options using the Notebook Intelligence Settings dialog. You can access this dialog from JupyterLab Settings menu -> Notebook Intelligence Settings, using `/settings` command in NBI Chat or by using the command palette. For more details, see the [blog post](https://notebook-intelligence.github.io/notebook-intelligence/blog/2025/03/05/support-for-any-llm-provider.html).

<img src="media/provider-list.png" alt="Settings dialog" width=500 />

Notebook Intelligence extension for JupyterLab

This extension is composed of a Python package named `notebook_intelligence`
for the server extension and a NPM package named `@notebook-intelligence/notebook-intelligence`
for the frontend extension.

### Remembering GitHub Copilot login

Notebook Intelligence can remember your GitHub Copilot login so that you don't need to re-login after a JupyterLab or system restart. Please be aware of the security implications of using this feature.

> [!CAUTION]
> If you configure NBI to remember your GitHub Copilot login, it will encrypt the token and store into a data file at `~/.jupyter/nbi/user-data.json`. You should never share this file with others as they can access your tokens.
> Even though the token is encrypted, it is done so by using a default password and that's why it can be decrypted by others. In order to prevent that you can specify a custom password using the environment variable `NBI_GH_ACCESS_TOKEN_PASSWORD`.

```bash
NBI_GH_ACCESS_TOKEN_PASSWORD=my_custom_password
```

To let Notebook Intelligence remember your GitHub access token, go to Notebook Intelligence Settings dialog and check the option `Remember my GitHub Copilot access token` as shown below.

<img src="media/remember-gh-access-token.png" alt="Remember access token" width=500 />

If your stored access token fails to login (due to expiration or other reasons), you will be prompted to relogin on the UI.

### Disabling Built-in tools

All built-in toolas are enabled by default in Agent Mode. However, you can disable them and make them controlled by an environment variable.

In order to disable any built-in tool use the `disabled_tools` config:

```python
c.NotebookIntelligence.disabled_tools = ["nbi-notebook-execute","nbi-python-file-edit"]
```

In order to disable a built-in tool by default but allow re-enabling using an environment variable use the `allow_enabling_tools_with_env` config:

```python
c.NotebookIntelligence.allow_enabling_tools_with_env = True
```

Then the environment variable `NBI_ENABLED_BUILTIN_TOOLS` can be used to re-enable specific built-in tools.

```bash
export NBI_ENABLED_BUILTIN_TOOLS=nbi-notebook-execute,nbi-python-file-edit
```

### Configuration files

NBI saves configuration at `~/.jupyter/nbi/config.json`. It also supports environment wide base configuration at `<env-prefix>/share/jupyter/nbi/config.json`. Organizations can ship default configuration at this environment wide config path. User's changes will be stored as overrides at `~/.jupyter/nbi/config.json`.

These config files are used for saving LLM provider, model and MCP configuration. Note that API keys you enter for your custom LLM providers will also be stored in these config files.

> [!IMPORTANT]
> Note that updating config.json manually requires restarting JupyterLab to take effect.

### Model Context Protocol ([MCP](https://modelcontextprotocol.io)) Support

NBI seamlessly integrates with MCP servers. It supports servers with both Standard Input/Output (stdio) and Server-Sent Events (SSE) transports. The MCP support is limited to server tools at the moment.

You can easily add MCP servers to NBI by editing the configuration file [~/.jupyter/nbi/mcp.json](#configuration-files). Environment wide base configuration is also support using the file at `<env-prefix>/share/jupyter/nbi/mcp.json`.

> [!NOTE]
> Using MCP servers requires an LLM model with tool calling capabilities. All of the GitHub Copilot models provided in NBI support this feature. If you are using other providers make sure you choose a tool calling capable model.

> [!CAUTION]
> Note that most MCP servers are run on the same computer as your JupyterLab installation and they can make irreversible changes to your computer and/or access private data. Make sure that you only install MCP servers from trusted sources.

### MCP Config file example

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/Users/mbektas/mcp-test"
      ]
    }
  }
}
```

You can use Agent mode to access tools provided by MCP servers you configured.

For servers with stdio transport, you can also set additional environment variables by using the `env` key. Environment variables are specified as key value pairs.

```json
"mcpServers": {
    "servername": {
        "command": "",
        "args": [],
        "env": {
            "ENV_VAR_NAME": "ENV_VAR_VALUE"
        }
    },
}
```

Below is an example of a server configuration with Streamable HTTP transport. For Streamable HTTP transport servers, you can also specify headers to be sent as part of the requests.

```json
"mcpServers": {
    "remoterservername": {
        "url": "http://127.0.0.1:8080/mcp",
        "headers": {
            "Authorization": "Bearer mysecrettoken"
        }
    },
}
```

If you have multiple servers configured but you would like to disable some for a while, you can do so by using the `disabled` key. `servername2` will be disabled and not available in `@mcp` chat participant.

```json
"mcpServers": {
    "servername1": {
        "command": "",
        "args": [],
    },
    "servername2": {
        "command": "",
        "args": [],
        "disabled": true
    },
}
```

### Ruleset System

NBI includes a powerful ruleset system that allows you to define custom guidelines and best practices that are automatically injected into AI prompts. This helps ensure consistent coding standards, project-specific conventions, and domain knowledge across all AI interactions.

#### How It Works

Rules are markdown files with optional YAML frontmatter stored in `~/.jupyter/nbi/rules/`. They are automatically discovered and applied based on context (file type, notebook kernel, chat mode).

#### Creating Rules

**Global Rules** - Apply to all contexts:

Create a file like `~/.jupyter/nbi/rules/01-coding-standards.md`:

```markdown
---
priority: 10
---

# Coding Standards

- Always use type hints in Python functions
- Prefer list comprehensions over loops when appropriate
- Add docstrings to all public functions
```

**Mode-Specific Rules** - Apply only to specific chat modes:

NBI supports mode-specific rules for three modes:

- **ask** - Question/answer mode
- **agent** - Autonomous agent mode with tool access
- **inline-chat** - Inline code generation and editing

Create a file like `~/.jupyter/nbi/rules/modes/agent/01-testing.md`:

```markdown
---
priority: 20
scope:
  kernels: ['python3']
---

# Testing Guidelines

When writing code in agent mode:

- Always include error handling
- Add logging for debugging
- Test edge cases
```

#### Rule Frontmatter Options

```yaml
---
apply: always # 'always', 'auto', or 'manual'
active: true # Enable/disable the rule
priority: 10 # Lower numbers = higher priority
scope:
  file_patterns: # Apply to specific file patterns
    - '*.py'
    - 'test_*.ipynb'
  kernels: # Apply to specific notebook kernels
    - 'python3'
    - 'ir'
  directories: # Apply to specific directories
    - '/projects/ml'
---
```

#### Configuration

**Enable/Disable Rules System:**

Edit `~/.jupyter/nbi/config.json`:

```json
{
  "rules_enabled": true
}
```

**Auto-Reload Configuration:**

Rules are automatically reloaded when changed (enabled by default). This behavior is controlled by the `NBI_RULES_AUTO_RELOAD` environment variable.

To disable auto-reload:

```bash
export NBI_RULES_AUTO_RELOAD=false
jupyter lab
```

Or to enable (default):

```bash
export NBI_RULES_AUTO_RELOAD=true
jupyter lab
```

#### Managing Rules

Rules are automatically discovered from:

- **Global rules**: `~/.jupyter/nbi/rules/*.md`
- **Mode-specific rules**: `~/.jupyter/nbi/rules/modes/{mode}/*.md` where `{mode}` can be:
  - `ask` - For question/answer interactions
  - `agent` - For autonomous agent operations
  - `inline-chat` - For inline code generation

Rules are applied in priority order (lower numbers first) and can be toggled on/off without deleting the files.

### Developer documentation

For building locally and contributing see the [developer documentatation](CONTRIBUTING.md).
