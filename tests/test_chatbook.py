# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

from notebook_intelligence.chatbook_kernel.codegen import (
    ChatbookCodegenError,
    cached_code_if_valid,
    extract_python_cell,
    prompt_hash,
    resolve_executable_source,
    stub_python,
)
from notebook_intelligence.chatbook_kernel.nui_client import NuiClient


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
        return {
            'generatedCode': '```python\nprint(42)\n```',
            'nuiSessionId': 'sess',
            'nuiRunId': 'run',
        }

    code, info = resolve_executable_source('answer', {}, generate)
    assert code == 'print(42)'
    assert info['cacheHit'] is False
    assert info['nuiSessionId'] == 'sess'


def test_stub_python_echoes_prompt():
    assert 'hello world' in stub_python('hello world')


def test_nui_resolve_agent_type_prefers_available(monkeypatch):
    client = NuiClient(base_url='http://example.invalid')

    monkeypatch.setattr(
        client,
        'list_agents',
        lambda: [
            {'id': 'openai', 'available': True},
            {'id': 'claude-code', 'available': True},
        ],
    )
    monkeypatch.setattr(client, 'get_settings', lambda: {})
    assert client.resolve_agent_type('') == 'claude-code'
    assert client.resolve_agent_type('openai') == 'openai'
