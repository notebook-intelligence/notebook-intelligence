"""Regression tests for terminal-safe chat participant dispatch."""

import asyncio
from unittest.mock import AsyncMock, Mock

from notebook_intelligence.ai_service_manager import AIServiceManager
from notebook_intelligence.api import (
    ChatRequest,
    ContextRequest,
    ContextRequestType,
)
from notebook_intelligence.claude import CLAUDE_CODE_CHAT_PARTICIPANT_ID


class _RecordingResponse:
    def __init__(self):
        self.participant_id = ""
        self.streamed = []
        self.finish_count = 0

    def stream(self, data):
        self.streamed.append(data)

    def finish(self):
        self.finish_count += 1


def _make_manager():
    manager = AIServiceManager.__new__(AIServiceManager)
    config = Mock()
    config.claude_settings = {"enabled": False}
    config.acp_settings = {"enabled": False}
    manager._nbi_config = config
    manager._chat_model = Mock()

    default_participant = Mock()
    default_participant.handle_chat_request = AsyncMock()
    manager._default_chat_participant = default_participant
    manager.chat_participants = {"default": default_participant}
    return manager, default_participant


def test_get_chat_participant_returns_default_object_for_unknown_id():
    manager, default_participant = _make_manager()

    participant = manager.get_chat_participant("@missing explain this")

    assert participant is default_participant


def test_unknown_participant_falls_back_without_dropping_at_mention():
    manager, default_participant = _make_manager()
    request = ChatRequest(prompt="@missing explain this", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_awaited_once()
    assert request.prompt == "@missing explain this"
    assert response.participant_id == "default"
    assert response.finish_count == 0


def test_unknown_participant_fallback_preserves_parsed_command():
    manager, default_participant = _make_manager()
    request = ChatRequest(prompt="@missing /explain this", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_awaited_once()
    assert request.prompt == "@missing this"
    assert request.command == "explain"


def test_mcp_prompt_messages_are_marked_as_current_request_context():
    manager, default_participant = _make_manager()
    manager.get_mcp_server_prompt_value = Mock(
        return_value=[
            {"role": "assistant", "content": "Return strict JSON."},
            {"role": "user", "content": "Use the compact schema."},
        ]
    )
    request = ChatRequest(
        prompt="/mcp:docs:review: inspect this",
        chat_history=[
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ],
    )
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_awaited_once()
    assert request.mcp_prompt_message_count == 2
    assert request.chat_history[-3:] == [
        {"role": "assistant", "content": "Return strict JSON."},
        {"role": "user", "content": "Use the compact schema."},
        {"role": "user", "content": "inspect this"},
    ]


def test_claude_mode_resolves_active_default_outside_participant_map():
    manager, _default_participant = _make_manager()
    manager._nbi_config.claude_settings = {"enabled": True}
    claude_participant = Mock()
    claude_participant.id = CLAUDE_CODE_CHAT_PARTICIPANT_ID
    claude_participant.handle_chat_request = AsyncMock()
    manager._default_chat_participant = claude_participant
    manager.chat_participants = {"default": claude_participant}
    request = ChatRequest(prompt="explain this", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    claude_participant.handle_chat_request.assert_awaited_once()
    assert response.participant_id == CLAUDE_CODE_CHAT_PARTICIPANT_ID
    assert response.finish_count == 0


def test_claude_mode_does_not_fall_back_while_participant_is_starting():
    manager, default_participant = _make_manager()
    manager._nbi_config.claude_settings = {"enabled": True}
    request = ChatRequest(prompt="hello world", chat_history=[])
    response = _RecordingResponse()

    asyncio.run(manager.handle_chat_request(request, response))

    default_participant.handle_chat_request.assert_not_awaited()
    assert request.prompt == "hello world"
    assert response.participant_id == CLAUDE_CODE_CHAT_PARTICIPANT_ID
    assert response.finish_count == 1
    assert [item.content for item in response.streamed] == [
        "Claude Code mode is still starting. Please try again in a moment."
    ]


def test_completion_context_is_empty_when_no_participant_is_available():
    manager, _default_participant = _make_manager()
    request = ContextRequest(
        ContextRequestType.InlineCompletion,
        participant=None,
    )

    context = asyncio.run(manager.get_completion_context(request))

    assert context.items == []
