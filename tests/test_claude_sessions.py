import json
import os
from pathlib import Path

import pytest

from notebook_intelligence.claude_sessions import (
    ClaudeSessionInfo,
    encode_cwd,
    get_sessions_dir,
    list_sessions,
)


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for obj in lines:
            fh.write(json.dumps(obj) + "\n")


def _user_line(session_id: str, text: str) -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": text},
        "sessionId": session_id,
    }


def _assistant_line(session_id: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": "ok"},
        "sessionId": session_id,
    }


@pytest.fixture
def fake_claude_home(tmp_path):
    """Create an empty ~/.claude stand-in under a tmp_path."""
    home = tmp_path / "claude_home"
    home.mkdir()
    return home


@pytest.fixture
def project_cwd(tmp_path):
    """Create an arbitrary project directory to act as the Jupyter cwd."""
    cwd = tmp_path / "projects" / "my-notebook"
    cwd.mkdir(parents=True)
    return str(cwd)


@pytest.fixture
def sessions_dir(fake_claude_home, project_cwd):
    return get_sessions_dir(project_cwd, claude_home=str(fake_claude_home))


class TestEncodeCwd:
    def test_replaces_path_separators_with_dashes(self):
        assert encode_cwd("/Users/me/proj") == "-Users-me-proj"

    def test_normalizes_trailing_slash(self):
        assert encode_cwd("/Users/me/proj/") == "-Users-me-proj"

    def test_normalizes_parent_segments(self):
        assert encode_cwd("/Users/me/proj/../proj") == "-Users-me-proj"

    def test_resolves_symlinks(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)

        assert encode_cwd(str(link)) == encode_cwd(str(real))


class TestGetSessionsDir:
    def test_composes_claude_projects_path(self, fake_claude_home, project_cwd):
        result = get_sessions_dir(project_cwd, claude_home=str(fake_claude_home))
        assert result == fake_claude_home / "projects" / encode_cwd(project_cwd)


class TestListSessions:
    def test_returns_empty_when_dir_missing(
        self, fake_claude_home, project_cwd
    ):
        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert result == []

    def test_returns_empty_when_dir_has_no_jsonl_files(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        sessions_dir.mkdir(parents=True)
        (sessions_dir / "notes.txt").write_text("hi")
        (sessions_dir / "subagents").mkdir()

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert result == []

    def test_lists_sessions_with_metadata(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        session_id = "abc123"
        path = sessions_dir / f"{session_id}.jsonl"
        _write_jsonl(
            path,
            [
                _user_line(session_id, "Help me fix this bug"),
                _assistant_line(session_id),
                _user_line(session_id, "Follow-up question"),
            ],
        )

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))

        assert len(result) == 1
        session = result[0]
        assert isinstance(session, ClaudeSessionInfo)
        assert session.session_id == session_id
        assert session.preview == "Help me fix this bug"
        assert session.path == str(path)

    def test_sorts_sessions_newest_first(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        older_path = sessions_dir / "older.jsonl"
        newer_path = sessions_dir / "newer.jsonl"
        _write_jsonl(older_path, [_user_line("older", "first")])
        _write_jsonl(newer_path, [_user_line("newer", "second")])

        # Force distinct mtimes regardless of filesystem resolution.
        os.utime(older_path, (1_000_000_000, 1_000_000_000))
        os.utime(newer_path, (2_000_000_000, 2_000_000_000))

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))

        assert [s.session_id for s in result] == ["newer", "older"]

    def test_skips_files_without_user_messages(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        # A transcript that only contains a file-history-snapshot should be
        # filtered out so the picker doesn't show an empty row.
        snapshot_only = sessions_dir / "snapshot.jsonl"
        _write_jsonl(
            snapshot_only,
            [{"type": "file-history-snapshot", "messageId": "x", "snapshot": {}}],
        )

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert result == []

    def test_ignores_nested_subagent_files(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        # Subagent transcripts live under a nested subagents/ directory and
        # must not surface as top-level sessions.
        main_path = sessions_dir / "main.jsonl"
        _write_jsonl(main_path, [_user_line("main", "hello")])

        nested = sessions_dir / "main" / "subagents"
        nested.mkdir(parents=True)
        _write_jsonl(
            nested / "agent-xyz.jsonl", [_user_line("sub", "sub prompt")]
        )

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert [s.session_id for s in result] == ["main"]

    def test_tolerates_partial_trailing_line(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        # Sessions that are still being written can have a half-flushed
        # trailing line; we should keep parsing earlier messages instead of
        # dropping the whole file.
        sessions_dir.mkdir(parents=True)
        path = sessions_dir / "partial.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_user_line("partial", "first message")) + "\n")
            fh.write('{"type": "user", "message": {"role": "user", "content')

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert len(result) == 1
        assert result[0].preview == "first message"

    def test_preview_is_truncated(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        long_text = "a" * 500
        _write_jsonl(
            sessions_dir / "long.jsonl",
            [_user_line("long", long_text)],
        )

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert len(result[0].preview) < len(long_text)
        assert result[0].preview.endswith("\u2026")

    def test_preview_collapses_whitespace(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        _write_jsonl(
            sessions_dir / "ws.jsonl",
            [_user_line("ws", "line one\n\n   line two\tthree")],
        )

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert result[0].preview == "line one line two three"

    def test_handles_structured_content_blocks(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        _write_jsonl(
            sessions_dir / "blocks.jsonl",
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "hello"},
                            {"type": "image", "source": {}},
                            {"type": "text", "text": "world"},
                        ],
                    },
                }
            ],
        )

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert result[0].preview == "hello world"

    def test_skips_tool_result_user_envelopes(
        self, sessions_dir, fake_claude_home, project_cwd
    ):
        # Tool results are wrapped in user messages but carry no real
        # prompt text. They should not steal the preview from a real user
        # turn.
        _write_jsonl(
            sessions_dir / "tools.jsonl",
            [
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "abc",
                                "content": "done",
                            }
                        ],
                    },
                },
                _user_line("tools", "actual prompt"),
            ],
        )

        result = list_sessions(project_cwd, claude_home=str(fake_claude_home))
        assert result[0].preview == "actual prompt"
