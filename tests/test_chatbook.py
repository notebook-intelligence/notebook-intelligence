# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from notebook_intelligence.chatbook_generate import (
    format_chatbook_user_message,
    generate_prompt_with_chat_model,
    generate_python_with_chat_model,
    resolve_chatbook_chat_model,
)
from notebook_intelligence.chatbook_kernel.codegen import (
    ChatbookCodegenError,
    cached_code_if_valid,
    extract_python_cell,
    prompt_hash,
    resolve_executable_source,
    stub_python,
)
from notebook_intelligence.chatbook_kernel.nbi_client import resolve_generate_url
from notebook_intelligence.chatbook_kernel.kernel import is_python_execute


def test_prompt_hash_is_sha256():
    assert prompt_hash('hello') == (
        '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824'
    )


def test_extract_python_cell_from_fence():
    text = 'Sure.\n```python\nprint(1)\n```\n'
    assert extract_python_cell(text) == 'print(1)'


def test_extract_python_cell_bare_code():
    assert extract_python_cell('x = 1\n') == 'x = 1'


def test_extract_python_cell_empty_raises():
    try:
        extract_python_cell('   ')
        assert False, 'expected ChatbookCodegenError'
    except ChatbookCodegenError:
        pass


def test_cache_hit_requires_matching_hash():
    prompt = 'plot sales'
    digest = prompt_hash(prompt)
    assert cached_code_if_valid(prompt, {
        'cachedCode': 'print(1)',
        'promptHash': digest,
    }) == 'print(1)'
    assert cached_code_if_valid(prompt, {
        'cachedCode': 'print(1)',
        'promptHash': 'deadbeef',
    }) is None
    assert cached_code_if_valid(prompt, {'cachedCode': 'print(1)'}) is None


def test_resolve_skips_generate_for_empty_prompt():
    called = []

    def generate(_prompt, _meta):
        called.append(True)
        return {'generatedCode': 'should-not-run'}

    code, info = resolve_executable_source('   ', {}, generate)
    assert code == ''
    assert info['cacheHit'] is False
    assert called == []


def test_resolve_uses_cache_without_calling_generate():
    prompt = 'plot sales'
    called = []

    def generate(_prompt, _meta):
        called.append(True)
        return {'generatedCode': 'should-not-run'}

    code, info = resolve_executable_source(
        prompt,
        {'cachedCode': 'import pandas', 'promptHash': prompt_hash(prompt)},
        generate,
    )
    assert code == 'import pandas'
    assert info['cacheHit'] is True
    assert called == []


def test_resolve_generate_extracts_fence(monkeypatch):
    monkeypatch.delenv('NBI_CHATBOOK_STUB', raising=False)

    def generate(_prompt, _meta):
        return {'generatedCode': '```python\nprint(42)\n```'}

    code, info = resolve_executable_source('answer', {}, generate)
    assert code == 'print(42)'
    assert info['cacheHit'] is False


def test_stub_python_echoes_prompt():
    assert 'hello world' in stub_python('hello world')


class _FakeChatModel:
    def completions(self, messages, tools=None, response=None, cancel_token=None, options=None):
        if response is not None:
            response.stream({
                'choices': [{'delta': {'content': '```python\nx = 1\n```'}}]
            })
            response.finish()
            return None
        return {'choices': [{'message': {'content': '```python\nx = 1\n```'}}]}


def test_generate_python_with_chat_model_extracts_fence():
    assert generate_python_with_chat_model(_FakeChatModel(), 'make x') == 'x = 1'


def test_generate_prompt_with_chat_model_returns_plain_english():
    class SummaryModel:
        def completions(
            self, messages, tools=None, response=None, cancel_token=None, options=None
        ):
            assert 'Python notebook cell' in messages[0]['content']
            response.stream({
                'choices': [{
                    'delta': {
                        'content': 'Calculate the total of values and store it in total.'
                    }
                }]
            })
            response.finish()

    assert generate_prompt_with_chat_model(
        SummaryModel(), 'total = sum(values)'
    ) == 'Calculate the total of values and store it in total.'


def test_python_execute_mode_bypasses_codegen():
    assert is_python_execute({'executeMode': 'python'})
    assert not is_python_execute({'executeMode': 'prompt'})
    assert not is_python_execute({})


def test_format_chatbook_user_message_marks_prefix_cursor_suffix():
    text = format_chatbook_user_message(
        'what did I ask?',
        {
            'prefix': [
                {
                    'index': 0,
                    'cellType': 'code',
                    'mode': 'python',
                    'prompt': 'what is 2+2?',
                    'generatedCode': 'print(4)',
                    'output': '4',
                }
            ],
            'current': {
                'index': 1,
                'cellType': 'code',
                'prompt': 'what did I ask?',
            },
            'suffix': [
                {
                    'index': 2,
                    'cellType': 'markdown',
                    'source': '# later',
                }
            ],
        },
    )
    assert '<PREFIX>' in text
    assert '</PREFIX>' in text
    assert '<CURSOR>' in text
    assert 'generate this cell' in text
    assert '<SUFFIX>' in text
    assert 'what is 2+2?' in text
    assert 'python-authored' in text
    assert 'print(4)' in text
    assert 'Output:\n4' in text
    assert '# later' in text
    assert text.strip().endswith('what did I ask?')


def test_generate_python_includes_notebook_context_in_user_message():
    captured = {}

    class CapturingModel:
        def completions(self, messages, tools=None, response=None, cancel_token=None, options=None):
            captured['messages'] = messages
            if response is not None:
                response.stream({
                    'choices': [{'delta': {'content': '```python\nprint(4)\n```'}}]
                })
                response.finish()
            return None

    generate_python_with_chat_model(
        CapturingModel(),
        'what did I ask?',
        notebook_context={
            'prefix': [{
                'index': 0,
                'cellType': 'code',
                'prompt': 'what is 2+2?',
                'generatedCode': 'print(4)',
                'output': '4',
            }],
            'current': {'index': 1, 'cellType': 'code', 'prompt': 'what did I ask?'},
            'suffix': [],
        },
    )
    user = captured['messages'][1]['content']
    assert '<PREFIX>' in user
    assert 'what is 2+2?' in user
    assert 'CURSOR cell prompt:\nwhat did I ask?' in user


def test_resolve_chatbook_chat_model_uses_manager_chat_model():
    class Mgr:
        chat_model = _FakeChatModel()
        is_claude_code_mode = False

    assert resolve_chatbook_chat_model(Mgr()) is Mgr.chat_model


def test_resolve_chatbook_chat_model_none_without_model():
    class Mgr:
        chat_model = None
        is_claude_code_mode = False

    assert resolve_chatbook_chat_model(Mgr()) is None


def test_resolve_generate_url_keeps_absolute():
    assert resolve_generate_url('http://127.0.0.1:8888/notebook-intelligence/chatbook/generate') == (
        'http://127.0.0.1:8888/notebook-intelligence/chatbook/generate'
    )


def test_chatbook_inline_completion_uses_natural_language_prompt():
    from notebook_intelligence.inline_completion import (
        copilot_inline_language,
        extract_inline_completion,
        inline_completion_system_prompt,
        inline_completion_user_prompt,
        is_chatbook_inline_language,
    )

    assert is_chatbook_inline_language('chatbook')
    assert not is_chatbook_inline_language('python')
    system = inline_completion_system_prompt('chatbook')
    assert 'natural-language' in system
    assert 'Do not suggest Python' in system
    user = inline_completion_user_prompt('plot sales', '', 'chatbook', 'nb.ipynb')
    assert 'Do not write code' in user
    assert 'plot sales' in user
    assert copilot_inline_language('chatbook') == 'markdown'
    assert extract_inline_completion('by region', 'chatbook') == 'by region'
    assert extract_inline_completion('```\nby region\n```', 'chatbook') == 'by region'
    code_system = inline_completion_system_prompt('python')
    assert 'code completion assistant' in code_system

