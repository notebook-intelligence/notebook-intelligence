# Extending Chatbook context

Server-side NBI extensions can add context to Chatbook code generation and add
new roots to the `@` mention menu. Providers are registered from an extension's
`activate` method and run in the Jupyter Server process.

## Dynamic generation context

Subclass `ChatbookContextProvider` and register it with
`Host.register_chatbook_context_provider`. The provider runs before each
generation and returns supplemental reference text:

```python
from notebook_intelligence import ChatbookContextProvider


class ProjectContext(ChatbookContextProvider):
    @property
    def id(self):
        return "project-context"

    def provide_context(self, request):
        return f"Notebook: {request.notebook_path}"


def activate(host):
    host.register_chatbook_context_provider(ProjectContext())
```

`ChatbookContextRequest` includes the prompt, project-relative notebook path,
PREFIX/CURSOR/SUFFIX notebook context, cell ID and index, Jupyter working
directory, prompt and context hashes, kernel name, and operation. Provider failures are logged and skipped.
Returned text is bounded and sent to the model as reference data, not as system
instructions.

Durable conventions belong in the ruleset (`~/.jupyter/nbi/rules/` and optional
`modes/chatbook/`) or project `AGENTS.md`, not in a context provider. See
[`rulesets.md`](rulesets.md). Execution policy (confirm before running generated
Python) is configured in Settings → Chatbook; see [`chatbook.md`](chatbook.md).

## Mention providers

Subclass `ChatbookMentionProvider` to add a browsable root. List item values are
provider-local opaque strings; NBI namespaces them as
`@ext:<provider-id>:<value>`.

```python
from notebook_intelligence import (
    ChatbookMentionItem,
    ChatbookMentionList,
    ChatbookMentionProvider,
)


class CatalogMentions(ChatbookMentionProvider):
    id = "catalog"
    name = "Data catalog"
    description = "Browse available datasets"

    def list_mentions(self, request):
        return ChatbookMentionList(items=[
            ChatbookMentionItem(label="Orders", value="orders")
        ])

    def resolve_mention(self, request):
        if request.value == "orders":
            return "Columns: order_id, created_at, amount"
        return ""


def activate(host):
    host.register_chatbook_mention_provider(CatalogMentions())
```

List requests include `parent`, `query`, `limit`, `notebook_path`, and
`working_directory`. Resolve requests additionally include the full prompt,
notebook context, cell ID, and cell index. Resolution is soft-failing: an
unavailable provider is represented as unavailable context instead of failing
the whole cell. NBI enforces the requested list limit and truncates resolved
provider text to 16,000 characters.
