import io
from unittest.mock import patch

import pytest

from notebook_intelligence.skill_manifest import (
    ManifestError,
    MAX_MANIFEST_BYTES,
    load_manifest,
)


class TestFileLoading:
    def test_loads_yaml_from_file(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(
            "skills:\n"
            "  - url: https://github.com/org/repo/tree/main/a\n"
            "  - url: https://github.com/org/repo/tree/main/b\n"
            "    name: beta\n"
            "    scope: project\n",
            encoding="utf-8",
        )
        manifest = load_manifest(str(p))
        assert len(manifest.entries) == 2
        assert manifest.entries[0].url == "https://github.com/org/repo/tree/main/a"
        assert manifest.entries[0].name is None
        assert manifest.entries[0].scope == "user"
        assert manifest.entries[1].name == "beta"
        assert manifest.entries[1].scope == "project"

    def test_loads_json_from_file(self, tmp_path):
        # yaml.safe_load handles JSON as a subset of YAML.
        p = tmp_path / "m.json"
        p.write_text(
            '{"skills": [{"url": "https://github.com/org/repo/tree/main/a"}]}',
            encoding="utf-8",
        )
        manifest = load_manifest(str(p))
        assert len(manifest.entries) == 1

    def test_expands_tilde_in_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        p = tmp_path / "m.yaml"
        p.write_text("skills: []\n", encoding="utf-8")
        manifest = load_manifest("~/m.yaml")
        assert manifest.entries == []

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ManifestError, match="not found"):
            load_manifest(str(tmp_path / "nope.yaml"))

    def test_size_cap_enforced(self, tmp_path):
        p = tmp_path / "big.yaml"
        p.write_bytes(b"a" * (MAX_MANIFEST_BYTES + 10))
        with pytest.raises(ManifestError, match="exceeds"):
            load_manifest(str(p))


class TestValidation:
    def test_rejects_empty_source(self):
        with pytest.raises(ManifestError, match="empty"):
            load_manifest("")

    def test_rejects_non_mapping_root(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text("- url: foo\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="mapping"):
            load_manifest(str(p))

    def test_rejects_missing_skills_key(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text("other: []\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="skills"):
            load_manifest(str(p))

    def test_rejects_non_list_skills(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text("skills:\n  foo: bar\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="must be a list"):
            load_manifest(str(p))

    def test_rejects_entry_missing_url(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text("skills:\n  - name: nope\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="url is required"):
            load_manifest(str(p))

    def test_rejects_invalid_scope(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(
            "skills:\n  - url: https://x/y\n    scope: global\n", encoding="utf-8"
        )
        with pytest.raises(ManifestError, match="scope"):
            load_manifest(str(p))

    def test_rejects_invalid_name(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text(
            "skills:\n  - url: https://x/y\n    name: Bad Name\n", encoding="utf-8"
        )
        with pytest.raises(ManifestError, match="not a valid skill name"):
            load_manifest(str(p))

    def test_rejects_malformed_yaml(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text("skills: [\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="not valid YAML"):
            load_manifest(str(p))


class TestUrlLoading:
    @staticmethod
    def _fake_response(body: bytes):
        resp = io.BytesIO(body)
        resp.__enter__ = lambda self: self  # type: ignore[attr-defined]
        resp.__exit__ = lambda self, *a: False  # type: ignore[attr-defined]
        return resp

    def test_fetches_over_https(self):
        body = b"skills:\n  - url: https://github.com/org/repo/tree/main/a\n"
        with patch(
            "notebook_intelligence.skill_manifest.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value = self._fake_response(body)
            manifest = load_manifest("https://example.com/manifest.yaml")
        assert len(manifest.entries) == 1
        # Verify auth header not set when no token.
        req = mock_open.call_args[0][0]
        assert "Authorization" not in req.headers

    def test_adds_bearer_token_header(self):
        body = b"skills: []\n"
        with patch(
            "notebook_intelligence.skill_manifest.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value = self._fake_response(body)
            load_manifest("https://example.com/manifest.yaml", token="abc123")
        req = mock_open.call_args[0][0]
        assert req.headers.get("Authorization") == "Bearer abc123"

    def test_url_size_cap_enforced(self):
        big = b"a" * (MAX_MANIFEST_BYTES + 10)
        with patch(
            "notebook_intelligence.skill_manifest.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value = self._fake_response(big)
            with pytest.raises(ManifestError, match="exceeds"):
                load_manifest("https://example.com/manifest.yaml")

    def test_github_host_uses_gh_token_fallback(self):
        body = b"skills: []\n"
        with patch(
            "notebook_intelligence.skill_manifest._get_github_token",
            return_value="gh_fallback",
        ), patch(
            "notebook_intelligence.skill_manifest.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value = self._fake_response(body)
            load_manifest(
                "https://raw.githubusercontent.com/org/repo/main/manifest.yaml"
            )
        req = mock_open.call_args[0][0]
        assert req.headers.get("Authorization") == "Bearer gh_fallback"

    def test_non_github_host_skips_gh_token_fallback(self):
        body = b"skills: []\n"
        with patch(
            "notebook_intelligence.skill_manifest._get_github_token",
            return_value="gh_fallback",
        ) as probe, patch(
            "notebook_intelligence.skill_manifest.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value = self._fake_response(body)
            load_manifest("https://example.com/manifest.yaml")
        req = mock_open.call_args[0][0]
        assert "Authorization" not in req.headers
        probe.assert_not_called()

    def test_explicit_token_wins_over_gh_token_fallback(self):
        body = b"skills: []\n"
        with patch(
            "notebook_intelligence.skill_manifest._get_github_token",
            return_value="gh_fallback",
        ) as probe, patch(
            "notebook_intelligence.skill_manifest.urllib.request.urlopen"
        ) as mock_open:
            mock_open.return_value = self._fake_response(body)
            load_manifest(
                "https://raw.githubusercontent.com/org/repo/main/manifest.yaml",
                token="explicit",
            )
        req = mock_open.call_args[0][0]
        assert req.headers.get("Authorization") == "Bearer explicit"
        probe.assert_not_called()
