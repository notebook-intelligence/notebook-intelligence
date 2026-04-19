"""Tests for the file upload handler and upload directory management.

Covers:
- Temp directory creation and cleanup
- FileUploadHandler: successful uploads, missing file, path traversal protection
"""

import json
import os
import shutil
from unittest.mock import MagicMock

import pytest

import notebook_intelligence.extension as ext
from notebook_intelligence.extension import FileUploadHandler, _get_upload_dir


@pytest.fixture
def upload_dir(tmp_path):
    """Point _upload_dir at a temp directory for the duration of the test."""
    original = ext._upload_dir
    ext._upload_dir = str(tmp_path)
    yield str(tmp_path)
    ext._upload_dir = original


@pytest.fixture
def reset_upload_dir():
    """Reset _upload_dir to None so _get_upload_dir creates a fresh one."""
    original = ext._upload_dir
    ext._upload_dir = None
    yield
    if ext._upload_dir and ext._upload_dir != original:
        shutil.rmtree(ext._upload_dir, ignore_errors=True)
    ext._upload_dir = original


def _make_handler(files=None):
    """Create a mock FileUploadHandler with the given request files."""
    handler = MagicMock(spec=FileUploadHandler)
    handler.request = MagicMock()
    handler.request.files = files or {}
    handler.set_status = MagicMock()
    handler.finish = MagicMock()
    return handler


def _parse_response(handler):
    return json.loads(handler.finish.call_args[0][0])


# ---------------------------------------------------------------------------
# _get_upload_dir
# ---------------------------------------------------------------------------

class TestGetUploadDir:
    def test_creates_directory(self, reset_upload_dir):
        result = _get_upload_dir()
        assert os.path.isdir(result)
        assert "nbi-uploads-" in result

    def test_returns_same_directory_on_repeated_calls(self, reset_upload_dir):
        first = _get_upload_dir()
        second = _get_upload_dir()
        assert first == second


# ---------------------------------------------------------------------------
# FileUploadHandler
# ---------------------------------------------------------------------------

class TestFileUploadHandler:
    def test_returns_400_when_no_file_provided(self):
        handler = _make_handler(files={})
        FileUploadHandler.post(handler)
        handler.set_status.assert_called_once_with(400)
        assert "No file provided" in _parse_response(handler)["error"]

    def test_returns_400_when_file_list_empty(self):
        handler = _make_handler(files={"file": []})
        FileUploadHandler.post(handler)
        handler.set_status.assert_called_once_with(400)

    def test_successful_text_upload(self, upload_dir):
        file_body = b"hello world"
        handler = _make_handler(files={
            "file": [{"filename": "test.txt", "body": file_body}]
        })

        FileUploadHandler.post(handler)

        response = _parse_response(handler)
        assert response["filename"] == "test.txt"
        assert response["serverPath"].startswith(upload_dir)
        with open(response["serverPath"], "rb") as f:
            assert f.read() == file_body

    def test_binary_file_upload(self, upload_dir):
        file_body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        handler = _make_handler(files={
            "file": [{"filename": "screenshot.png", "body": file_body}]
        })

        FileUploadHandler.post(handler)

        response = _parse_response(handler)
        assert response["filename"] == "screenshot.png"
        with open(response["serverPath"], "rb") as f:
            assert f.read() == file_body

    def test_path_traversal_protection(self, upload_dir):
        handler = _make_handler(files={
            "file": [{"filename": "../../../etc/passwd", "body": b"sneaky"}]
        })

        FileUploadHandler.post(handler)

        response = _parse_response(handler)
        assert response["filename"] == "passwd"
        assert response["serverPath"].startswith(upload_dir)

    def test_missing_filename_defaults_to_upload(self, upload_dir):
        handler = _make_handler(files={
            "file": [{"body": b"data"}]
        })

        FileUploadHandler.post(handler)

        assert _parse_response(handler)["filename"] == "upload"

    def test_unique_subdirectories_per_upload(self, upload_dir):
        paths = []
        for i in range(3):
            handler = _make_handler(files={
                "file": [{"filename": "same.txt", "body": f"v{i}".encode()}]
            })
            FileUploadHandler.post(handler)
            paths.append(_parse_response(handler)["serverPath"])

        assert len(set(paths)) == 3
        for p in paths:
            assert os.path.isfile(p)
