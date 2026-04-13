"""Tests for the file upload handler and upload directory management.

Covers:
- Temp directory creation and cleanup
- FileUploadHandler: successful uploads, missing file, path traversal protection
- Uploaded file context processing in the WebSocket handler
"""

import json
import os
import shutil
import tempfile
from os import path
from unittest.mock import MagicMock, patch

import pytest

from notebook_intelligence.extension import (
    FileUploadHandler,
    _get_upload_dir,
)


# ---------------------------------------------------------------------------
# _get_upload_dir
# ---------------------------------------------------------------------------

class TestGetUploadDir:
    def test_creates_directory(self):
        """_get_upload_dir should return a directory that exists."""
        import notebook_intelligence.extension as ext
        original = ext._upload_dir
        try:
            ext._upload_dir = None
            result = _get_upload_dir()
            assert path.isdir(result)
            assert "nbi-uploads-" in result
        finally:
            # Restore and clean up
            if ext._upload_dir and ext._upload_dir != original:
                shutil.rmtree(ext._upload_dir, ignore_errors=True)
            ext._upload_dir = original

    def test_returns_same_directory_on_repeated_calls(self):
        """Repeated calls should return the same directory (singleton)."""
        import notebook_intelligence.extension as ext
        original = ext._upload_dir
        try:
            ext._upload_dir = None
            first = _get_upload_dir()
            second = _get_upload_dir()
            assert first == second
        finally:
            if ext._upload_dir and ext._upload_dir != original:
                shutil.rmtree(ext._upload_dir, ignore_errors=True)
            ext._upload_dir = original


# ---------------------------------------------------------------------------
# FileUploadHandler
# ---------------------------------------------------------------------------

def _make_handler(files=None):
    """Create a mock FileUploadHandler with the given request files."""
    handler = MagicMock(spec=FileUploadHandler)
    handler.request = MagicMock()
    handler.request.files = files or {}
    handler.set_status = MagicMock()
    handler.finish = MagicMock()
    return handler


class TestFileUploadHandler:
    def test_returns_400_when_no_file_provided(self):
        handler = _make_handler(files={})
        FileUploadHandler.post(handler)
        handler.set_status.assert_called_once_with(400)
        response = json.loads(handler.finish.call_args[0][0])
        assert "No file provided" in response["error"]

    def test_returns_400_when_file_list_empty(self):
        handler = _make_handler(files={"file": []})
        FileUploadHandler.post(handler)
        handler.set_status.assert_called_once_with(400)

    def test_successful_upload(self, tmp_path):
        """A valid upload should write the file and return serverPath + filename."""
        import notebook_intelligence.extension as ext
        original = ext._upload_dir
        ext._upload_dir = str(tmp_path)
        try:
            file_body = b"hello world"
            handler = _make_handler(files={
                "file": [{"filename": "test.txt", "body": file_body}]
            })

            FileUploadHandler.post(handler)

            handler.set_status.assert_not_called()
            response = json.loads(handler.finish.call_args[0][0])
            assert response["filename"] == "test.txt"
            assert "test.txt" in response["serverPath"]

            # Verify the file was actually written
            assert path.isfile(response["serverPath"])
            with open(response["serverPath"], "rb") as f:
                assert f.read() == file_body
        finally:
            ext._upload_dir = original

    def test_binary_file_upload(self, tmp_path):
        """Binary files (images, PDFs) should be stored correctly."""
        import notebook_intelligence.extension as ext
        original = ext._upload_dir
        ext._upload_dir = str(tmp_path)
        try:
            # Minimal PNG header bytes
            file_body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
            handler = _make_handler(files={
                "file": [{"filename": "screenshot.png", "body": file_body}]
            })

            FileUploadHandler.post(handler)

            response = json.loads(handler.finish.call_args[0][0])
            assert response["filename"] == "screenshot.png"
            with open(response["serverPath"], "rb") as f:
                assert f.read() == file_body
        finally:
            ext._upload_dir = original

    def test_path_traversal_protection(self, tmp_path):
        """Filenames with path traversal attempts should be sanitised."""
        import notebook_intelligence.extension as ext
        original = ext._upload_dir
        ext._upload_dir = str(tmp_path)
        try:
            handler = _make_handler(files={
                "file": [{"filename": "../../../etc/passwd", "body": b"sneaky"}]
            })

            FileUploadHandler.post(handler)

            response = json.loads(handler.finish.call_args[0][0])
            assert response["filename"] == "passwd"
            # File should be inside the upload dir, not escaped
            assert response["serverPath"].startswith(str(tmp_path))
        finally:
            ext._upload_dir = original

    def test_missing_filename_defaults_to_upload(self, tmp_path):
        """If no filename is provided, it should default to 'upload'."""
        import notebook_intelligence.extension as ext
        original = ext._upload_dir
        ext._upload_dir = str(tmp_path)
        try:
            handler = _make_handler(files={
                "file": [{"body": b"data"}]
            })

            FileUploadHandler.post(handler)

            response = json.loads(handler.finish.call_args[0][0])
            assert response["filename"] == "upload"
        finally:
            ext._upload_dir = original

    def test_unique_subdirectories_per_upload(self, tmp_path):
        """Each upload should go into a unique subdirectory."""
        import notebook_intelligence.extension as ext
        original = ext._upload_dir
        ext._upload_dir = str(tmp_path)
        try:
            paths = []
            for i in range(3):
                handler = _make_handler(files={
                    "file": [{"filename": "same.txt", "body": f"v{i}".encode()}]
                })
                FileUploadHandler.post(handler)
                response = json.loads(handler.finish.call_args[0][0])
                paths.append(response["serverPath"])

            # All paths should be unique despite same filename
            assert len(set(paths)) == 3
            # All should exist
            for p in paths:
                assert path.isfile(p)
        finally:
            ext._upload_dir = original
