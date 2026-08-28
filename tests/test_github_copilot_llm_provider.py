from unittest.mock import patch, MagicMock

import pytest

from notebook_intelligence import github_copilot as gh_copilot
from notebook_intelligence.llm_providers.github_copilot_llm_provider import GitHubCopilotLLMProvider


@pytest.fixture(autouse=True)
def _reset_models_cache():
    gh_copilot.invalidate_copilot_models_cache()
    original_token = gh_copilot.github_auth.get("token")
    yield
    gh_copilot.invalidate_copilot_models_cache()
    gh_copilot.github_auth["token"] = original_token


def test_chat_models_include_recent_github_copilot_models():
    provider = GitHubCopilotLLMProvider()

    models = {model.id: model for model in provider.chat_models}

    assert models["gpt-5.3-codex"].name == "GPT-5.3-Codex"
    assert models["claude-haiku-4.5"].name == "Claude Haiku 4.5"
    assert models["claude-sonnet-4.6"].name == "Claude Sonnet 4.6"
    assert models["claude-opus-4.6"].name == "Claude Opus 4.6"
    assert models["gemini-3.1-pro"].name == "Gemini 3.1 Pro"

    assert all(model.supports_tools for model in models.values())


def test_chat_models_use_dynamic_cache_when_populated():
    gh_copilot.copilot_models_cache.extend([
        {"id": "future-model-1", "name": "Future 1", "context_window": 200000},
        {"id": "future-model-2", "name": "Future 2", "context_window": 4096},
    ])

    provider = GitHubCopilotLLMProvider()
    ids = [m.id for m in provider.chat_models]

    assert ids == ["future-model-1", "future-model-2"]
    by_id = {m.id: m for m in provider.chat_models}
    assert by_id["future-model-1"].context_window == 200000
    assert by_id["future-model-2"].name == "Future 2"


def test_fetch_copilot_models_returns_empty_without_token():
    gh_copilot.github_auth["token"] = None
    assert gh_copilot.fetch_copilot_models() == []


def test_fetch_copilot_models_floors_zero_context_window_to_default():
    gh_copilot.github_auth["token"] = "fake-bearer-token"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "data": [
            {
                "id": "no-limits-model",
                "name": "No Limits",
                "model_picker_enabled": True,
                "capabilities": {"type": "chat"},
            }
        ]
    }
    with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
        result = gh_copilot.fetch_copilot_models()

    assert result == [{
        "id": "no-limits-model",
        "name": "No Limits",
        "context_window": 4096,
    }]


def test_fetch_copilot_models_preserves_cache_on_empty_response():
    gh_copilot.github_auth["token"] = "fake-bearer-token"
    gh_copilot.copilot_models_cache.append({
        "id": "previously-cached",
        "name": "Cached",
        "context_window": 100000,
    })
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": []}

    with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
        result = gh_copilot.fetch_copilot_models()

    assert [m["id"] for m in result] == ["previously-cached"]
    assert [m["id"] for m in gh_copilot.copilot_models_cache] == ["previously-cached"]


def test_fetch_copilot_models_preserves_cache_on_http_failure():
    gh_copilot.github_auth["token"] = "fake-bearer-token"
    gh_copilot.copilot_models_cache.append({
        "id": "previously-cached",
        "name": "Cached",
        "context_window": 100000,
    })
    response = MagicMock(status_code=503, text="upstream down")

    with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
        result = gh_copilot.fetch_copilot_models()

    assert [m["id"] for m in result] == ["previously-cached"]
    assert [m["id"] for m in gh_copilot.copilot_models_cache] == ["previously-cached"]


def test_fetch_copilot_models_filters_to_picker_enabled_chat_models():
    gh_copilot.github_auth["token"] = "fake-bearer-token"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "data": [
            {
                "id": "gpt-foo",
                "name": "GPT Foo",
                "model_picker_enabled": True,
                "capabilities": {
                    "type": "chat",
                    "limits": {"max_context_window_tokens": 64000},
                },
            },
            {
                "id": "gpt-foo-internal",
                "name": "GPT Foo Internal",
                "model_picker_enabled": False,
                "capabilities": {"type": "chat"},
            },
            {
                "id": "text-embed",
                "model_picker_enabled": True,
                "capabilities": {"type": "embedding"},
            },
            {
                "id": "gpt-foo",  # duplicate id is dropped
                "name": "GPT Foo Dup",
                "model_picker_enabled": True,
                "capabilities": {"type": "chat"},
            },
            {
                "id": "claude-bar",
                "model_picker_enabled": True,
                "capabilities": {
                    "type": "chat",
                    "limits": {"max_prompt_tokens": 128000},
                },
            },
        ]
    }

    with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
        result = gh_copilot.fetch_copilot_models()

    assert [m["id"] for m in result] == ["gpt-foo", "claude-bar"]
    assert result[0]["context_window"] == 64000
    assert result[1]["context_window"] == 128000
    # Falls back to id when name is missing.
    assert result[1]["name"] == "claude-bar"


def test_fetch_copilot_models_swallows_http_failure():
    gh_copilot.github_auth["token"] = "fake-bearer-token"
    response = MagicMock(status_code=500, text="oops")

    with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
        assert gh_copilot.fetch_copilot_models() == []


class _FakeEvent:
    def __init__(self, data: str):
        self.data = data


class _FakeSSEClient:
    """Minimal sseclient.SSEClient stand-in for replaying canned events."""

    def __init__(self, events: list[_FakeEvent]):
        self._events = events

    def events(self):
        yield from self._events


def _responses_event(payload: dict) -> _FakeEvent:
    import json as _json

    return _FakeEvent(_json.dumps(payload))


class TestMessagesToResponsesInput:
    def test_extracts_system_messages_into_instructions(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        items, instructions = gh_copilot._messages_to_responses_input(msgs)
        assert instructions == "You are helpful."
        assert items == [{"role": "user", "content": "Hi"}]

    def test_concatenates_multiple_system_messages(self):
        msgs = [
            {"role": "system", "content": "Be terse."},
            {"role": "system", "content": "Never apologize."},
            {"role": "user", "content": "ok"},
        ]
        _, instructions = gh_copilot._messages_to_responses_input(msgs)
        assert instructions == "Be terse.\nNever apologize."

    def test_assistant_tool_calls_emit_function_call_items(self):
        msgs = [
            {"role": "user", "content": "Run it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_42",
                        "type": "function",
                        "function": {"name": "do_thing", "arguments": '{"x": 1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_42", "content": "ok"},
        ]
        items, instructions = gh_copilot._messages_to_responses_input(msgs)
        assert instructions is None
        assert items[0] == {"role": "user", "content": "Run it"}
        assert items[1] == {
            "type": "function_call",
            "call_id": "call_42",
            "name": "do_thing",
            "arguments": '{"x": 1}',
        }
        assert items[2] == {
            "type": "function_call_output",
            "call_id": "call_42",
            "output": "ok",
        }

    def test_tool_result_with_dict_content_is_json_serialized(self):
        msgs = [
            {"role": "tool", "tool_call_id": "c1", "content": {"value": 7}},
        ]
        items, _ = gh_copilot._messages_to_responses_input(msgs)
        assert items[0]["output"] == '{"value": 7}'


class TestChatToolsToResponsesTools:
    def test_flattens_function_wrapper(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search the index.",
                    "parameters": {"type": "object"},
                },
            }
        ]
        result = gh_copilot._chat_tools_to_responses_tools(tools)
        assert result == [
            {
                "type": "function",
                "name": "search",
                "description": "Search the index.",
                "parameters": {"type": "object"},
            }
        ]

    def test_empty_or_none_passes_through_as_none(self):
        assert gh_copilot._chat_tools_to_responses_tools(None) is None
        assert gh_copilot._chat_tools_to_responses_tools([]) is None


class TestAggregateResponsesStreaming:
    def test_aggregates_text_deltas_into_message_content(self):
        events = [
            _responses_event({"type": "response.created", "response": {"id": "r1"}}),
            _responses_event({"type": "response.output_text.delta", "delta": "Hello, "}),
            _responses_event({"type": "response.output_text.delta", "delta": "world"}),
            _responses_event({
                "type": "response.completed",
                "response": {"output": []},
            }),
        ]
        result = gh_copilot._aggregate_responses_streaming(_FakeSSEClient(events))
        assert result == {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Hello, world",
                    "tool_calls": None,
                }
            }]
        }

    def test_extracts_function_calls_from_completed_event(self):
        events = [
            _responses_event({"type": "response.output_text.delta", "delta": "Calling now"}),
            _responses_event({
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "status": "completed",
                            "id": "fc_1",
                            "call_id": "call_x",
                            "name": "lookup",
                            "arguments": '{"q": "foo"}',
                        }
                    ]
                },
            }),
        ]
        result = gh_copilot._aggregate_responses_streaming(_FakeSSEClient(events))
        assert result["choices"][0]["message"]["tool_calls"] == [
            {
                "id": "call_x",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"q": "foo"}'},
            }
        ]

    def test_skips_malformed_event_data(self):
        events = [
            _FakeEvent(""),
            _FakeEvent("not-json"),
            _responses_event({"type": "response.output_text.delta", "delta": "ok"}),
            _responses_event({"type": "response.completed", "response": {"output": []}}),
        ]
        result = gh_copilot._aggregate_responses_streaming(_FakeSSEClient(events))
        assert result["choices"][0]["message"]["content"] == "ok"


class TestModelChatEndpoint:
    def test_defaults_to_chat_completions_for_unknown_model(self):
        assert gh_copilot._model_chat_endpoint("gpt-4.1") == "/chat/completions"

    def test_codex_substring_falls_back_to_responses(self):
        # No live catalogue entry; the substring heuristic protects the
        # hardcoded fallback list and offline sessions.
        assert gh_copilot._model_chat_endpoint("gpt-5.3-codex") == "/responses"
        assert gh_copilot._model_chat_endpoint("FUTURE-Codex-Mini") == "/responses"

    def test_live_catalogue_overrides_heuristic(self):
        gh_copilot.copilot_model_endpoints["gpt-5.3-codex"] = "/chat/completions"
        try:
            assert gh_copilot._model_chat_endpoint("gpt-5.3-codex") == "/chat/completions"
        finally:
            gh_copilot.copilot_model_endpoints.clear()


class TestFetchCopilotModelsEndpointMap:
    def test_supported_endpoints_responses_only_routes_to_responses(self):
        gh_copilot.github_auth["token"] = "fake-bearer-token"
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "data": [
                {
                    "id": "gpt-5.3-codex",
                    "name": "GPT-5.3-Codex",
                    "model_picker_enabled": True,
                    "capabilities": {"type": "chat"},
                    "supported_endpoints": ["/responses"],
                },
                {
                    "id": "gpt-4.1",
                    "name": "GPT-4.1",
                    "model_picker_enabled": True,
                    "capabilities": {"type": "chat"},
                    "supported_endpoints": ["/chat/completions"],
                },
            ]
        }
        with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
            gh_copilot.fetch_copilot_models()
        assert gh_copilot.copilot_model_endpoints == {
            "gpt-5.3-codex": "/responses",
            "gpt-4.1": "/chat/completions",
        }

    def test_dual_listed_model_prefers_chat_completions(self):
        gh_copilot.github_auth["token"] = "fake-bearer-token"
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "data": [
                {
                    "id": "future-dual",
                    "model_picker_enabled": True,
                    "capabilities": {"type": "chat"},
                    "supported_endpoints": ["/chat/completions", "/responses"],
                }
            ]
        }
        with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
            gh_copilot.fetch_copilot_models()
        assert gh_copilot.copilot_model_endpoints["future-dual"] == "/chat/completions"

    def test_non_chat_type_admitted_when_responses_supported(self):
        # Defensive: future Codex models may drop `capabilities.type == chat`.
        # If they list /responses, accept and route them.
        gh_copilot.github_auth["token"] = "fake-bearer-token"
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "data": [
                {
                    "id": "future-codex",
                    "model_picker_enabled": True,
                    "capabilities": {"type": "responses"},
                    "supported_endpoints": ["/responses"],
                }
            ]
        }
        with patch("notebook_intelligence.github_copilot.requests.get", return_value=response):
            result = gh_copilot.fetch_copilot_models()
        assert [m["id"] for m in result] == ["future-codex"]
        assert gh_copilot.copilot_model_endpoints["future-codex"] == "/responses"


class TestResponsesDispatch:
    def test_chat_routes_codex_through_responses(self):
        sentinel = object()
        with patch.object(gh_copilot, "responses", return_value=sentinel) as mock_responses, \
             patch.object(gh_copilot, "completions") as mock_completions:
            result = gh_copilot.chat("gpt-5.3-codex", [{"role": "user", "content": "hi"}])
        assert result is sentinel
        mock_responses.assert_called_once()
        mock_completions.assert_not_called()

    def test_chat_routes_non_codex_through_completions(self):
        sentinel = object()
        with patch.object(gh_copilot, "completions", return_value=sentinel) as mock_completions, \
             patch.object(gh_copilot, "responses") as mock_responses:
            result = gh_copilot.chat("gpt-4.1", [{"role": "user", "content": "hi"}])
        assert result is sentinel
        mock_completions.assert_called_once()
        mock_responses.assert_not_called()


class TestResponsesEndToEnd:
    """Pins the actual wire behavior: when a Codex model is selected, the
    HTTP POST must target `/responses`, not `/chat/completions`. The bug
    in issue #340 was that this was reversed; without this assertion a
    refactor that swapped the URL would still pass every transform test.
    """

    def _make_request_mock(self, events: list[_FakeEvent]):
        request_mock = MagicMock(status_code=200)
        sse_mock = MagicMock(events=lambda: iter(events))
        return request_mock, sse_mock

    def test_chat_dispatches_codex_to_responses_url(self):
        events = [
            _responses_event({"type": "response.output_text.delta", "delta": "Hi"}),
            _responses_event({"type": "response.completed", "response": {"output": []}}),
        ]
        request_mock, sse_mock = self._make_request_mock(events)
        with patch.object(gh_copilot, "generate_copilot_headers", return_value={}), \
             patch("notebook_intelligence.github_copilot.requests.post", return_value=request_mock) as post, \
             patch("notebook_intelligence.github_copilot.sseclient.SSEClient", return_value=sse_mock):
            gh_copilot.chat("gpt-5.3-codex", [{"role": "user", "content": "hi"}])
        called_url = post.call_args.args[0]
        assert called_url.endswith("/responses")
        assert "input" in post.call_args.kwargs["json"]

    def test_chat_dispatches_gpt4_to_chat_completions_url(self):
        request_mock, sse_mock = self._make_request_mock([_FakeEvent("[DONE]")])
        with patch.object(gh_copilot, "generate_copilot_headers", return_value={}), \
             patch("notebook_intelligence.github_copilot.requests.post", return_value=request_mock) as post, \
             patch("notebook_intelligence.github_copilot.sseclient.SSEClient", return_value=sse_mock):
            gh_copilot.chat("gpt-4.1", [{"role": "user", "content": "hi"}])
        called_url = post.call_args.args[0]
        assert called_url.endswith("/chat/completions")
        assert "messages" in post.call_args.kwargs["json"]

    def test_chat_completions_stops_streaming_after_cancellation(self):
        events = [
            _FakeEvent('{"choices":[{"delta":{"content":"late"}}]}'),
            _FakeEvent("[DONE]"),
        ]
        request_mock, sse_mock = self._make_request_mock(events)
        response = MagicMock()

        class CancelAfterRequestStarts:
            checks = 0

            @property
            def is_cancel_requested(self):
                self.checks += 1
                return self.checks > 1

        cancel_token = CancelAfterRequestStarts()

        with patch.object(gh_copilot, "generate_copilot_headers", return_value={}), \
             patch("notebook_intelligence.github_copilot.requests.post", return_value=request_mock), \
             patch("notebook_intelligence.github_copilot.sseclient.SSEClient", return_value=sse_mock):
            gh_copilot.completions(
                "gpt-4.1",
                [{"role": "user", "content": "hi"}],
                response=response,
                cancel_token=cancel_token,
            )

        response.finish.assert_called_once_with()
        response.stream.assert_not_called()


class TestResponsesStreamingMode:
    """Covers the non-aggregate branch of `responses()`. The aggregate path
    is what `chat_model.completions(...)` returns when no `response` arg
    is passed; the streaming path is what powers the chat sidebar."""

    class _FakeChatResponse:
        def __init__(self):
            self.chunks: list = []
            self.finished_count = 0

        def stream(self, data):
            self.chunks.append(data)

        def finish(self):
            self.finished_count += 1

    def _drive(self, events: list[_FakeEvent], model_id: str = "gpt-5.3-codex"):
        request_mock = MagicMock(status_code=200)
        sse_mock = MagicMock(events=lambda: iter(events))
        chat_response = self._FakeChatResponse()
        with patch.object(gh_copilot, "generate_copilot_headers", return_value={}), \
             patch("notebook_intelligence.github_copilot.requests.post", return_value=request_mock), \
             patch("notebook_intelligence.github_copilot.sseclient.SSEClient", return_value=sse_mock):
            gh_copilot.responses(model_id, [{"role": "user", "content": "hi"}], response=chat_response)
        return chat_response

    def test_text_deltas_stream_as_chat_completions_chunks(self):
        chat_response = self._drive([
            _responses_event({"type": "response.output_text.delta", "delta": "Hi "}),
            _responses_event({"type": "response.output_text.delta", "delta": "there"}),
            _responses_event({"type": "response.completed", "response": {"output": []}}),
        ])
        contents = [c["choices"][0]["delta"]["content"] for c in chat_response.chunks if "content" in c["choices"][0]["delta"]]
        assert contents == ["Hi ", "there"]
        assert chat_response.finished_count == 1

    def test_function_call_in_completed_event_emits_tool_calls_chunk(self):
        chat_response = self._drive([
            _responses_event({"type": "response.output_text.delta", "delta": "Calling"}),
            _responses_event({
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "status": "completed",
                            "id": "fc_1",
                            "call_id": "call_x",
                            "name": "lookup",
                            "arguments": '{"q": "foo"}',
                        }
                    ]
                },
            }),
        ])
        tool_chunks = [c for c in chat_response.chunks if "tool_calls" in c["choices"][0]["delta"]]
        assert len(tool_chunks) == 1
        tc = tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
        assert tc["id"] == "call_x"
        assert tc["function"] == {"name": "lookup", "arguments": '{"q": "foo"}'}
        assert "index" in tc

    def test_function_call_arguments_deltas_stitched_when_completed_arguments_empty(self):
        # Defensive against a future Codex variant that streams the tool
        # arguments and ships an empty `arguments` in the terminal item.
        chat_response = self._drive([
            _responses_event({
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '{"q":',
            }),
            _responses_event({
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": ' "foo"}',
            }),
            _responses_event({
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "status": "completed",
                            "id": "fc_1",
                            "call_id": "call_x",
                            "name": "lookup",
                            "arguments": "",
                        }
                    ]
                },
            }),
        ])
        tc = chat_response.chunks[-1]["choices"][0]["delta"]["tool_calls"][0]
        assert tc["function"]["arguments"] == '{"q": "foo"}'

    def test_http_error_raises_and_streams_message(self):
        request_mock = MagicMock(status_code=400, text="bad model")
        chat_response = self._FakeChatResponse()
        with patch.object(gh_copilot, "generate_copilot_headers", return_value={}), \
             patch("notebook_intelligence.github_copilot.requests.post", return_value=request_mock):
            with pytest.raises(Exception, match="bad model"):
                gh_copilot.responses("gpt-5.3-codex", [{"role": "user", "content": "hi"}], response=chat_response)
        assert chat_response.finished_count == 1
        assert any(getattr(c, "content", "") and "bad model" in c.content for c in chat_response.chunks)

    def test_response_failed_event_raises(self):
        chat_response = self._FakeChatResponse()
        events = [
            _responses_event({"type": "response.output_text.delta", "delta": "partial"}),
            _responses_event({
                "type": "response.failed",
                "response": {"error": {"message": "context length exceeded"}},
            }),
        ]
        request_mock = MagicMock(status_code=200)
        sse_mock = MagicMock(events=lambda: iter(events))
        with patch.object(gh_copilot, "generate_copilot_headers", return_value={}), \
             patch("notebook_intelligence.github_copilot.requests.post", return_value=request_mock), \
             patch("notebook_intelligence.github_copilot.sseclient.SSEClient", return_value=sse_mock):
            with pytest.raises(Exception, match="context length exceeded"):
                gh_copilot.responses("gpt-5.3-codex", [{"role": "user", "content": "hi"}], response=chat_response)
        assert chat_response.finished_count == 1


class TestAggregateResponsesTerminalErrors:
    def test_response_failed_raises_with_message(self):
        events = [
            _responses_event({
                "type": "response.failed",
                "response": {"error": {"message": "model overloaded"}},
            }),
        ]
        with pytest.raises(Exception, match="model overloaded"):
            gh_copilot._aggregate_responses_streaming(_FakeSSEClient(events))

    def test_response_incomplete_without_message_falls_back_to_event_type(self):
        events = [
            _responses_event({"type": "response.incomplete"}),
        ]
        with pytest.raises(Exception, match="response.incomplete"):
            gh_copilot._aggregate_responses_streaming(_FakeSSEClient(events))

    def test_function_call_arguments_delta_stitched_in_aggregate(self):
        events = [
            _responses_event({
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": '{"a":',
            }),
            _responses_event({
                "type": "response.function_call_arguments.delta",
                "item_id": "fc_1",
                "delta": ' 1}',
            }),
            _responses_event({
                "type": "response.completed",
                "response": {
                    "output": [
                        {
                            "type": "function_call",
                            "status": "completed",
                            "id": "fc_1",
                            "call_id": "call_y",
                            "name": "do",
                            "arguments": "",
                        }
                    ]
                },
            }),
        ]
        result = gh_copilot._aggregate_responses_streaming(_FakeSSEClient(events))
        assert result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'


class TestChatCompletionsOmitsStop:
    """No model receives a `stop` sequence on the chat-completions path (#397).

    `stop: ['<END>']` was sent unconditionally until GPT-5 rejected it, at which
    point the two shipping ids were excluded by literal comparison. Every later
    GPT-5 family model inherited the bug: `gpt-5.4` is sent `stop` and the API
    answers 400, making chat unusable for that model.

    The id cannot decide this. `gpt-5.4` refuses the parameter while
    `gpt-5.4-mini` accepts it, so a prefix test would be another reactive guess.
    These pin the parameter's absence rather than any particular exclusion rule,
    so reintroducing a model-keyed allowlist fails here.
    """

    def _post_json_for(self, model_id):
        request_mock = MagicMock(status_code=200)
        sse = _FakeSSEClient([_FakeEvent("[DONE]")])
        with patch.object(gh_copilot, "generate_copilot_headers", return_value={}), \
             patch("notebook_intelligence.github_copilot.requests.post",
                   return_value=request_mock) as post, \
             patch("notebook_intelligence.github_copilot.sseclient.SSEClient",
                   return_value=sse):
            gh_copilot.chat(model_id, [{"role": "user", "content": "hi"}])
        # Anchor the endpoint. The /responses body never carries a 'stop' key at
        # all, so without this the absence assertion below would pass vacuously
        # if any of these ids were ever routed there.
        assert post.call_args.args[0].endswith("/chat/completions")
        return post.call_args.kwargs["json"]

    @pytest.mark.parametrize("model_id", [
        "gpt-5.4",       # the reported failure: was sent stop, answered 400
        "gpt-5.4-mini",  # accepts stop, but has no need of it either
        "gpt-5",         # was already excluded by literal comparison
        "gpt-5-mini",    # was already excluded by literal comparison
        "gpt-4.1",       # pre-reasoning model that used to receive stop
        "gpt-4o",        # pre-reasoning model that used to receive stop
    ])
    def test_stop_is_never_sent(self, model_id):
        assert "stop" not in self._post_json_for(model_id)

    def test_required_body_fields_survive(self):
        """Guards against a deletion that takes a neighbouring key with it.

        Deliberately limited to what the endpoint actually requires. Pinning the
        full parameter set would freeze `temperature`, `top_p`, and `n`, which
        are the same reasoning-model-hostile knobs as `stop`; if one of them
        draws the next 400, the correct fix is to drop it, and that fix should
        not have to fight a test written here.
        """
        body = self._post_json_for("gpt-4.1")
        assert body["model"] == "gpt-4.1"
        assert body["messages"] == [{"role": "user", "content": "hi"}]
        assert body["stream"] is True


class TestInlineCompletionKeepsStop:
    """The FIM proxy endpoint keeps its stop sequences.

    Separate endpoint, separate contract: there `<END>` and the code fence
    genuinely bound a fill-in-the-middle completion, and the reasoning models
    that reject `stop` are not served from it. This pins that the #397 fix did
    not over-reach into a path where the parameter is load-bearing.
    """

    def test_inline_completions_still_send_stop_sequences(self):
        # A 200 with an empty SSE body: this test asserts on the request, and a
        # non-200 shape would quietly model inline_completions' unrelated
        # missing status check rather than the stop sequences under test.
        resp_mock = MagicMock(status_code=200, content=b"")
        cancel_token = MagicMock(is_cancel_requested=False)
        with patch.dict(gh_copilot.github_auth, {"token": "t"}, clear=False), \
             patch("notebook_intelligence.github_copilot.requests.post",
                   return_value=resp_mock) as post:
            gh_copilot.inline_completions(
                "gpt-4o-copilot", "prefix", "suffix", "python", "f.py",
                None, cancel_token,
            )
        assert post.call_args.kwargs["json"]["stop"] == ["<END>", "```"]
