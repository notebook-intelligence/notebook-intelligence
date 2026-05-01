"""Tests for image/screenshot context handling in the chat websocket handler.

Covers the backend image branch added to WebsocketCopilotHandler.on_message:
- Valid image produces an OpenAI vision multimodal content block
- The base64 payload round-trips to the original bytes
- The mimeType field is reflected in the data URL prefix
- A missing file logs a warning and produces no chat history entry
- Non-image uploads still use the existing text file-path hint
- Mixed image + text context items both appear in history
"""

import base64
import json
import logging
from unittest.mock import Mock, patch

from tornado.httputil import HTTPServerRequest
from tornado.web import Application

from notebook_intelligence.extension import WebsocketCopilotHandler


CHAT_ID = "test-chat"


def _make_handler():
    app = Mock(spec=Application)
    app.ui_methods = {}
    app.ui_modules = {}
    request = Mock(spec=HTTPServerRequest)
    request.connection = Mock()
    with patch("notebook_intelligence.extension.ThreadSafeWebSocketConnector"):
        handler = WebsocketCopilotHandler(app, request)
    # get_history() returns a throwaway list for unknown chat IDs; pre-seed so
    # messages appended by on_message are visible after the call returns.
    handler.chat_history.messages[CHAT_ID] = []
    return handler


def _on_message(handler, additional_context, prompt="hello"):
    msg = json.dumps({
        "id": "msg-id",
        "type": "chat-request",
        "data": {
            "chatId": CHAT_ID,
            "prompt": prompt,
            "language": "python",
            "filename": "test.ipynb",
            "chatMode": "ask",
            "toolSelections": {},
            "additionalContext": additional_context,
        }
    })
    handler.on_message(msg)
    return handler.chat_history.messages[CHAT_ID]


def _image_context(file_path, mime_type="image/png"):
    return {
        "isUpload": True,
        "isImage": True,
        "mimeType": mime_type,
        "filePath": str(file_path),
        "content": "",
        "currentCellContents": None,
        "startLine": 1,
        "endLine": 0,
    }


def _text_context(file_path, content, start=1, end=1):
    return {
        "isUpload": False,
        "isImage": False,
        "filePath": str(file_path),
        "content": content,
        "currentCellContents": None,
        "startLine": start,
        "endLine": end,
    }


@patch("notebook_intelligence.extension.ai_service_manager")
@patch("notebook_intelligence.extension.NotebookIntelligence")
@patch("notebook_intelligence.extension.threading.Thread")
class TestImageContextInChatHistory:

    def test_image_produces_multimodal_message(self, _thread, mock_nbi, mock_ai, tmp_path):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = False

        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        img_file = tmp_path / "screenshot.png"
        img_file.write_bytes(image_bytes)

        handler = _make_handler()
        history = _on_message(handler, [_image_context(img_file)])

        assert len(history) == 2
        image_msg = history[0]
        assert image_msg["role"] == "user"
        content = image_msg["content"]
        assert isinstance(content, list), "Image message content should be a list (multimodal)"
        assert content[0] == {
            "type": "text",
            "text": "The user pasted an image 'screenshot.png':",
        }
        assert content[1]["type"] == "image_url"

    def test_base64_payload_round_trips(self, _thread, mock_nbi, mock_ai, tmp_path):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = False

        image_bytes = b"\x89PNG\r\n\x1a\n" + b"\xde\xad\xbe\xef" * 8
        img_file = tmp_path / "screenshot.png"
        img_file.write_bytes(image_bytes)

        handler = _make_handler()
        history = _on_message(handler, [_image_context(img_file)])

        url = history[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")
        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded == image_bytes

    def test_mime_type_reflected_in_data_url(self, _thread, mock_nbi, mock_ai, tmp_path):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = False

        img_file = tmp_path / "photo.jpg"
        img_file.write_bytes(b"\xff\xd8\xff" + b"\x00" * 16)

        handler = _make_handler()
        history = _on_message(handler, [_image_context(img_file, mime_type="image/jpeg")])

        url = history[0]["content"][1]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")

    def test_missing_file_logs_warning_and_skips_entry(self, _thread, mock_nbi, mock_ai, tmp_path, caplog):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = False

        missing = tmp_path / "does_not_exist.png"

        handler = _make_handler()
        with caplog.at_level(logging.WARNING, logger="notebook_intelligence.extension"):
            history = _on_message(handler, [_image_context(missing)])

        assert len(history) == 1
        assert history[0] == {"role": "user", "content": "hello"}
        assert any("does_not_exist.png" in r.message for r in caplog.records)

    def test_non_image_upload_uses_text_hint(self, _thread, mock_nbi, mock_ai, tmp_path):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = False

        handler = _make_handler()
        binary_upload = {
            "isUpload": True,
            "isImage": False,
            "filePath": "/tmp/nbi-uploads/uuid/report.pdf",
            "content": "",
            "currentCellContents": None,
            "startLine": 1,
            "endLine": 0,
        }
        history = _on_message(handler, [binary_upload])

        text_msg = history[0]
        assert isinstance(text_msg["content"], str), "Non-image upload should produce a plain-text message"
        assert "report.pdf" in text_msg["content"]
        assert "Read this file" in text_msg["content"]

    def test_mixed_image_and_text_context(self, _thread, mock_nbi, mock_ai, tmp_path):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = False

        img_file = tmp_path / "shot.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

        handler = _make_handler()
        history = _on_message(handler, [
            _image_context(img_file),
            _text_context("script.py", "print('hello')"),
        ], prompt="what does this do?")

        assert len(history) == 3
        assert isinstance(history[0]["content"], list)
        assert isinstance(history[1]["content"], str)
        assert history[2]["content"] == "what does this do?"

    def test_image_context_not_charged_against_token_budget(self, _thread, mock_nbi, mock_ai, tmp_path):
        """Image items should not consume the token budget, leaving room for text context."""
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = False

        img_file = tmp_path / "shot.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)

        handler = _make_handler()
        history = _on_message(handler, [
            _image_context(img_file),
            _text_context("tiny.py", "x = 1"),
        ])

        assert len(history) == 3

    def test_claude_code_mode_sends_file_path_not_base64(self, _thread, mock_nbi, mock_ai, tmp_path):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = True

        img_file = tmp_path / "screenshot.png"
        img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        handler = _make_handler()
        history = _on_message(handler, [_image_context(img_file)])

        image_msg = history[0]
        assert isinstance(image_msg["content"], str), "Claude Code mode should send plain text, not multimodal list"
        assert str(img_file) in image_msg["content"]
        assert "base64" not in image_msg["content"]

    def test_claude_code_mode_missing_file_still_adds_path_message(self, _thread, mock_nbi, mock_ai, tmp_path):
        mock_nbi.root_dir = str(tmp_path)
        mock_ai.chat_model = None
        mock_ai.is_claude_code_mode = True

        missing = tmp_path / "does_not_exist.png"

        handler = _make_handler()
        history = _on_message(handler, [_image_context(missing)])

        # Claude Code mode passes the path without reading the file, so no error even if missing
        image_msg = history[0]
        assert isinstance(image_msg["content"], str)
        assert str(missing) in image_msg["content"]
