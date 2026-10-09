# Parallel Search MCP

[Parallel Search MCP](https://docs.parallel.ai/integrations/mcp/search-mcp) provides
`web_search` for finding web sources and `web_fetch` for reading pages. The
anonymous endpoint is free for light use and requires no Parallel API key. It has
lower rate limits than authenticated access.

This example uses NBI's Streamable HTTP transport in **Default mode**. Claude mode
has a [separate MCP configuration](../README.md#claude-mcp-servers).

## Configure

Install NBI in a fresh Python environment if it is not already installed:

```bash
pip install "jupyterlab>=4,<5" notebook-intelligence
```

Add the `parallel-search` entry below to `mcpServers` in
`~/.jupyter/nbi/mcp.json`. If the file already exists, merge the entry into it;
keep your other servers and participants.

```json
{
  "mcpServers": {
    "parallel-search": {
      "url": "https://search.parallel.ai/mcp",
      "headers": {
        "User-Agent": "NotebookIntelligence-ParallelSearch/1.0"
      }
    }
  }
}
```

Restart JupyterLab after saving the file:

```bash
jupyter lab
```

Select a tool-calling-capable chat model in NBI Settings. In Default mode, the
`@mcp` participant exposes the server's `web_search` and `web_fetch` tools. For
example, ask:

```text
@mcp Find the JupyterLab documentation for installing extensions, then fetch
https://jupyterlab.readthedocs.io/en/stable/user/extensions.html and summarize it.
```

Approve each MCP tool call when prompted. This configuration leaves tool approval
on and does not change your selected LLM provider. Search queries and requested
URLs are sent to Parallel; see [Parallel's privacy policy](https://parallel.ai/privacy-policy).

## Troubleshooting

- If the tools are missing, check the JSON syntax, restart JupyterLab, and check
  the server status in NBI Settings. Also check whether your administrator has
  disabled MCP access.
- If a request is rate limited, wait before trying again. The anonymous service
  is intended for exploration and light use, not unlimited searches.
- If the model does not call a tool, check that it supports tool calling and that
  the message is addressed to `@mcp` in Default mode.
- To stop using this server while retaining the configuration, add
  `"disabled": true` to its entry and restart JupyterLab.
