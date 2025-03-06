---
layout: post
title:  "Notebook Intelligence now supports any LLM Provider and AI Model!"
date:   2025-03-05 01:00:00 -0800
categories: blog
---

[Notebook Intelligence](https://github.com/notebook-intelligence/notebook-intelligence) (NBI) is an AI coding assistant and extensible AI framework for JupyterLab. (*For an introduction to NBI see [Introducing Notebook Intelligence]({{site.baseurl}}{% post_url 2025-01-07-introducing-notebook-intelligence %}) and for basics of extending NBI see [Building AI Extensions for JupyterLab]({{site.baseurl}}{% post_url 2025-02-04-building-ai-extensions-for-jupyterlab %}) blog posts.*)

Notebook Intelligence now supports any LLM Provider and compatible model for chat and auto-complete. Chat model is used for Copilot Chat in the sidebar and inline chat popups that are accessible from notebook and file editors. Auto-complete model is used for providing completion suggestions as you type in a notebook or file editor (as ghost text). Your chat model and auto-complete model don't have to be from the same provider for use with NBI.

GitHub Copilot is still the recommended and the default model provider. Now, you can choose which model to use from the options provided by GitHub Copilot service.

![which AI model prompt](/notebook-intelligence/assets/images/llm-providers/llm-provider-model.gif){: width="600" }

## Notebook Intelligence Settings Dialog

You can configure the model provider and model options using the Notebook Intelligence Settings dialog. You can access this dialog from JupyterLab Settings menu -> `Notebook Intelligence Settings`, using `/settings` command in Copilot Chat or by using the command palette.

![LLM provider list](/notebook-intelligence/assets/images/llm-providers/provider-list.png){: width="600" }

Your settings are stored as a file on the disk at location `~/.jupyter/nbi-config.json`. Saved data includes your API keys for OpenAI and LiteLLM compatible providers you choose. Removing this file would reset the LLM provider to GitHub Copilot.

## GitHub Copilot Model Options

GitHub Copilot provides multiple model options both for chat and auto-complete. You can now specify the models to use. The default chat model has been `GPT-4o` and auto-complete model `copilot-codex`. Chat model options available are: `GPT-4o`, `o3-mini`, `Claude 3.5 Sonnet`, `Claude 3.7 Sonnet`. Auto-complete model options are: `copilot-codex` and `gpt-4o-copilot`.

![GitHub Copilot provider](/notebook-intelligence/assets/images/llm-providers/github-copilot-models.png){: width="600" }

# OpenAI Compatible Model Provider

If you have an OpenAI subscription or any other OpenAI compatible LLM provider such as OpenRouter then you can use the `OpenAI Compatible` provider option. Enter your API key, model ID (e.g. gpt-4o) and service base URL in the settings dialog. You can leave Base URL blank if you are using OpenAI as the service provider.

Not all models support auto-complete (insertion). If you have an OpenAI subscription, you can use the model `gpt-3.5-turbo-instruct` for auto-complete.

![OpenAI compatible provider](/notebook-intelligence/assets/images/llm-providers/openai-compatible-provider.png){: width="600" }

# LiteLLM Compatible Model Provider

You can choose `LiteLLM Compatible` provider option for any provider that is not compatible with OpenAI APIs. LiteLLM supports a wide range of providers. Please check [LiteLLM documentation](https://docs.litellm.ai/docs/providers){:target="_blank"} for the list of providers and models. Enter your API key, model ID (e.g. anthropic/claude-3.5) and service base URL in the settings dialog for the provider and model you would like to use.

Use a model that supports auto-complete (insertion).

![OpenAI compatible provider](/notebook-intelligence/assets/images/llm-providers/litellm-compatible-provider.png){: width="600" }

## Anthropic

If you have an Anthropic subscription then you can use `LiteLLM Compatible` provider.

## Use local models with Ollama

NBI supports Ollama as provider for local chat and auto-complete models. Any Ollama chat model can be used with NBI and they will be automatically listed in the settings dialog. For auto-complete, NBI supports a selected list of models which were tested to work well to support completions. Auto-complete models supported are: `deepseek-coder-v2`, `qwen2.5-coder`, `codestral`, `starcoder2`, `codellama:7b-code`. Make sure you have these models installed before trying to use with NBI.

![Ollama provider](/notebook-intelligence/assets/images/llm-providers/ollama-provider.png){: width="600" }

## Add your custom LLM Provider by creating an NBI Extension

If you work with LLM providers which don't fit in the NBI supported provider types then you can build an extension to add support for those. You can introduce new LLM providers, chat models and auto-complete models using the NBI extension API. Refer to NBI `OllamaLLMProvider` class for an example.

```python
class LLMProviderExtension(NotebookIntelligenceExtension):
    ...

    def activate(self, host: Host) -> None:
        self.llm_provider = CustomLLMProvider()
        host.register_llm_provider(self.llm_provider)
        log.info("Custom LLM Provider extension activated")
```

For building NBI extensions see [Building AI Extensions for JupyterLab]({{site.baseurl}}{% post_url 2025-02-04-building-ai-extensions-for-jupyterlab %}) blog post.

## Try it out and share your feedback!

Please try the LLM provider and model options and share your feedback using project's [GitHub issues](https://github.com/notebook-intelligence/notebook-intelligence/issues)! User feedback from the community will shape the project's roadmap.

## About the Author

[Mehmet Bektas](https://www.linkedin.com/in/mehmet-bektas) is a Senior Software Engineer at Netflix and a Jupyter Distinguished Contributor. He is the author of Notebook Intelligence, and contributes to JupyterLab, JupyterLab Desktop and several other projects in the Jupyter eco-system.
