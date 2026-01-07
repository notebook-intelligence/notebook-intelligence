# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

import json
from typing import Any
from notebook_intelligence.api import ChatModel, EmbeddingModel, InlineCompletionModel, LLMProvider, CancelToken, ChatResponse, CompletionContext
import ollama
import logging

from notebook_intelligence.util import extract_llm_generated_code

log = logging.getLogger(__name__)

OLLAMA_EMBEDDING_FAMILIES = set(["nomic-bert", "bert"])
QWEN_INLINE_COMPL_PROMPT = """<|fim_prefix|>{prefix}<|fim_suffix|>{suffix}<|fim_middle|>"""
DEEPSEEK_INLINE_COMPL_PROMPT = """<｜fim▁begin｜>{prefix}<｜fim▁hole｜>{suffix}<｜fim▁end｜>"""
CODELLAMA_INLINE_COMPL_PROMPT = """<PRE> {prefix} <SUF>{suffix} <MID>"""
STARCODER_INLINE_COMPL_PROMPT = """<fim_prefix>{prefix}<fim_suffix>{suffix}<fim_middle>"""
CODESTRAL_INLINE_COMPL_PROMPT = """[SUFFIX]{suffix}[PREFIX]{prefix}"""

class OllamaChatModel(ChatModel):
    def __init__(self, provider: LLMProvider, model_id: str, model_name: str, context_window: int):
        super().__init__(provider)
        self._model_id = model_id
        self._model_name = model_name
        self._context_window = context_window

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return self._context_window

    def completions(self, messages: list[dict], tools: list[dict] = None, response: ChatResponse = None, cancel_token: CancelToken = None, options: dict = {}) -> Any:
        stream = response is not None
        completion_args = {
            "model": self._model_id, 
            "messages": messages.copy(),
            "stream": stream,
        }
        if tools is not None and len(tools) > 0:
            completion_args["tools"] = tools

        ollama_response = ollama.chat(**completion_args)

        if stream:
            for chunk in ollama_response:
                response.stream({
                        "choices": [{
                            "delta": {
                                "role": chunk['message']['role'],
                                "content": chunk['message']['content']
                            }
                        }]
                    })
            response.finish()
            return
        else:
            json_resp = json.loads(ollama_response.model_dump_json())

            return {
                'choices': [
                    {
                        'message': json_resp['message']
                    }
                ]
            }


class OllamaInlineCompletionModel(InlineCompletionModel):
    def __init__(self, provider: LLMProvider, model_id: str, model_name: str, context_window: int, prompt_template: str):
        super().__init__(provider)
        self._model_id = model_id
        self._model_name = model_name
        self._context_window = context_window
        self._prompt_template = prompt_template

    @property
    def id(self) -> str:
        return self._model_id
    
    @property
    def name(self) -> str:
        return self._model_name
    
    @property
    def context_window(self) -> int:
        return self._context_window

    def inline_completions(self, prefix, suffix, language, filename, context: CompletionContext, cancel_token: CancelToken) -> str:
        has_suffix = suffix.strip() != ""
        if has_suffix:
            prompt = self._prompt_template.format(prefix=prefix, suffix=suffix.strip())
        else:
            prompt = prefix

        try:
            generate_args = {
                "model": self._model_id, 
                "prompt": prompt,
                "raw": True,
                "options": {
                    'num_predict': 128,
                    "temperature": 0,
                    "stop" : [
                        "<|end▁of▁sentence|>",
                        "<｜end▁of▁sentence｜>",
                        "<|EOT|>",
                        "<EOT>",
                        "\\n",
                        "</s>",
                        "<|eot_id|>",
                    ],
                },
            }

            ollama_response = ollama.generate(**generate_args)
            code = ollama_response.response
            code = extract_llm_generated_code(code)

            return code
        except Exception as e:
            log.error(f"Error occurred while generating using completions ollama: {e}")
            return ""

class OllamaLLMProvider(LLMProvider):
    def __init__(self):
        super().__init__()
        self._chat_models = []
        self.update_chat_model_list()

    @property
    def id(self) -> str:
        return "ollama"
    
    @property
    def name(self) -> str:
        return "Ollama"

    @property
    def chat_models(self) -> list[ChatModel]:
        return self._chat_models

    @property
    def inline_completion_models(self) -> list[InlineCompletionModel]:
        return [
            OllamaInlineCompletionModel(self, "deepseek-coder-v2", "deepseek-coder-v2", 163840, DEEPSEEK_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "qwen2.5-coder", "qwen2.5-coder", 32768, QWEN_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "codestral", "codestral", 32768, CODESTRAL_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "starcoder2", "starcoder2", 16384, STARCODER_INLINE_COMPL_PROMPT),
            OllamaInlineCompletionModel(self, "codellama:7b-code", "codellama:7b-code", 16384, CODELLAMA_INLINE_COMPL_PROMPT),
        ]
    
    @property
    def embedding_models(self) -> list[EmbeddingModel]:
        return []
    
    def update_chat_model_list(self):
        try:
            response = ollama.list()
            models = response.models
            self._chat_models = []
            for model in models:
                try:
                    model_family = model.details.family
                    if model_family in OLLAMA_EMBEDDING_FAMILIES:
                        continue
                    model_show = ollama.show(model.model)
                    model_info = model_show.modelinfo
                    context_window = model_info[f"{model_family}.context_length"]
                    self._chat_models.append(
                        OllamaChatModel(self, model.model, model.model, context_window)
                    )
                except Exception as e:
                    log.error(f"Error getting Ollama model info {model}: {e}")
        except Exception as e:
            log.warning(f"Failed to update supported Ollama models: {e}")
