import io
import subprocess
import tarfile
import urllib.error
from unittest.mock import patch

import pytest

from notebook_intelligence.skill_github_import import (
    _derive_name,
    _extract_skill,
    _fetch_tarball,
    _get_github_token,
    _slug,
    parse_github_url,
    stage_skill_from_github,
)
from tests.conftest import build_tarball


class TestParseGitHubUrl:
    def test_basic_repo(self):
        ref = parse_github_url("https://github.com/owner/repo")
        assert ref.owner == "owner"
        assert ref.repo == "repo"
        assert ref.ref is None
        assert ref.subpath == ""

    def test_dot_git_suffix_stripped(self):
        ref = parse_github_url("https://github.com/owner/repo.git")
        assert ref.repo == "repo"

    def test_tree_with_ref(self):
        ref = parse_github_url("https://github.com/owner/repo/tree/main")
        assert ref.ref == "main"
        assert ref.subpath == ""

    def test_tree_with_ref_and_subpath(self):
        ref = parse_github_url(
            "https://github.com/owner/repo/tree/main/skills/foo"
        )
        assert ref.ref == "main"
        assert ref.subpath == "skills/foo"

    def test_rejects_non_github(self):
        with pytest.raises(ValueError, match="github.com"):
            parse_github_url("https://gitlab.com/owner/repo")

    def test_rejects_non_https(self):
        with pytest.raises(ValueError, match="https://"):
            parse_github_url("ftp://github.com/owner/repo")

    def test_rejects_missing_repo(self):
        with pytest.raises(ValueError, match="repo"):
            parse_github_url("https://github.com/owner")


class TestSlugAndDerivedName:
    def test_slug_basic(self):
        assert _slug("My Skill Name") == "my-skill-name"

    def test_slug_special_chars(self):
        assert _slug("Foo_Bar!@#") == "foo-bar"

    def test_derive_name_prefers_frontmatter(self):
        ref = parse_github_url("https://github.com/owner/my-repo")
        assert _derive_name("my-skill", ref) == "my-skill"

    def test_derive_name_falls_back_to_subpath(self):
        ref = parse_github_url(
            "https://github.com/owner/repo/tree/main/skills/my-cool-skill"
        )
        assert _derive_name(None, ref) == "my-cool-skill"

    def test_derive_name_falls_back_to_repo(self):
        ref = parse_github_url("https://github.com/owner/my-repo")
        assert _derive_name(None, ref) == "my-repo"


class TestExtractSkill:
    def test_extracts_valid_bundle(self, tmp_path):
        tar_bytes = build_tarball({
            "repo-abc123/SKILL.md": "---\nname: foo\ndescription: d\n---\nbody",
            "repo-abc123/helper.py": "print('x')",
        })
        skill_root = _extract_skill(tar_bytes, "", tmp_path)
        assert (skill_root / "SKILL.md").exists()
        assert (skill_root / "helper.py").exists()

    def test_extracts_subpath(self, tmp_path):
        tar_bytes = build_tarball({
            "repo-abc123/README.md": "readme",
            "repo-abc123/skills/foo/SKILL.md": "---\nname: foo\ndescription: d\n---\nb",
        })
        skill_root = _extract_skill(tar_bytes, "skills/foo", tmp_path)
        assert skill_root.name == "foo"
        assert (skill_root / "SKILL.md").exists()

    def test_missing_skill_md_raises(self, tmp_path):
        tar_bytes = build_tarball({
            "repo-abc123/README.md": "readme",
        })
        with pytest.raises(ValueError, match="SKILL.md"):
            _extract_skill(tar_bytes, "", tmp_path)

    def test_missing_subpath_raises(self, tmp_path):
        tar_bytes = build_tarball({
            "repo-abc123/SKILL.md": "---\nname: x\n---\n",
        })
        with pytest.raises(ValueError, match="not found"):
            _extract_skill(tar_bytes, "nope", tmp_path)

    def test_rejects_path_traversal(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"evil"
            info = tarfile.TarInfo(name="repo-abc/../escape.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with pytest.raises(ValueError, match="Unsafe path"):
            _extract_skill(buf.getvalue(), "", tmp_path)

    def test_rejects_absolute_path(self, tmp_path):
        """An entry with an absolute name (e.g. /etc/passwd) must be rejected
        even though it contains no '..' segments."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # Wrapper directory so _extract_skill sees a top_dir.
            wrapper = tarfile.TarInfo(name="repo-abc/")
            wrapper.type = tarfile.DIRTYPE
            tar.addfile(wrapper)
            data = b"evil"
            info = tarfile.TarInfo(name="/tmp/nbi-pwn")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        with pytest.raises(ValueError, match="Unsafe path"):
            _extract_skill(buf.getvalue(), "", tmp_path)

    def test_skips_symlinks(self, tmp_path):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            skill_data = b"---\nname: x\n---\n"
            info = tarfile.TarInfo(name="repo-abc/SKILL.md")
            info.size = len(skill_data)
            tar.addfile(info, io.BytesIO(skill_data))
            sym = tarfile.TarInfo(name="repo-abc/link")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "/etc/passwd"
            tar.addfile(sym)
        skill_root = _extract_skill(buf.getvalue(), "", tmp_path)
        assert not (skill_root / "link").exists()


class TestStageSkillFromGitHub:
    def _patch_fetch(self, tarball: bytes):
        return patch(
            "notebook_intelligence.skill_github_import._fetch_tarball",
            return_value=tarball,
        )

    def test_happy_path(self, tmp_path):
        tar = build_tarball({
            "repo-abc/SKILL.md": (
                "---\n"
                "name: my-skill\n"
                "description: A cool skill\n"
                "allowed-tools: [Read, Bash]\n"
                "---\n"
                "This is the body"
            ),
            "repo-abc/helper.py": "print('x')",
        })
        with self._patch_fetch(tar):
            staged = stage_skill_from_github(
                "https://github.com/owner/repo"
            )
        try:
            assert staged.name == "my-skill"
            assert staged.description == "A cool skill"
            assert "Read" in staged.allowed_tools
            assert "Bash" in staged.allowed_tools
            assert staged.body.strip() == "This is the body"
            assert "helper.py" in staged.files
            assert "SKILL.md" not in staged.files
            assert staged.canonical_url == "https://github.com/owner/repo"
        finally:
            import shutil
            shutil.rmtree(staged.tmp_root, ignore_errors=True)

    def test_missing_skill_md_raises(self):
        tar = build_tarball({
            "repo-abc/README.md": "just a readme",
        })
        with self._patch_fetch(tar):
            with pytest.raises(ValueError, match="SKILL.md"):
                stage_skill_from_github("https://github.com/owner/repo")

    def test_invalid_yaml_frontmatter_raises(self):
        tar = build_tarball({
            "repo-abc/SKILL.md": "---\n: : bad yaml\n---\nbody",
        })
        with self._patch_fetch(tar):
            with pytest.raises(ValueError):
                stage_skill_from_github("https://github.com/owner/repo")

    def test_canonical_url_with_ref_and_subpath(self):
        tar = build_tarball({
            "repo-abc/skills/thing/SKILL.md": "---\nname: thing\ndescription: d\n---\n",
        })
        with self._patch_fetch(tar):
            staged = stage_skill_from_github(
                "https://github.com/owner/repo/tree/v1/skills/thing"
            )
        try:
            assert staged.canonical_url == (
                "https://github.com/owner/repo/tree/v1/skills/thing"
            )
        finally:
            import shutil
            tmp = staged.skill_root
            while tmp.parent != tmp and not tmp.name.startswith("nbi-skill-import-"):
                tmp = tmp.parent
            shutil.rmtree(tmp, ignore_errors=True)


class TestGetGitHubToken:
    def test_prefers_github_token_env(self):
        with patch.dict(
            "os.environ", {"GITHUB_TOKEN": "from-github-token", "GH_TOKEN": "other"}
        ):
            assert _get_github_token() == "from-github-token"

    def test_falls_back_to_gh_token_env(self):
        env = {"GH_TOKEN": "from-gh-token"}
        # Clear GITHUB_TOKEN if present in the real env.
        with patch.dict("os.environ", env, clear=True):
            assert _get_github_token() == "from-gh-token"

    def test_strips_whitespace(self):
        with patch.dict("os.environ", {"GITHUB_TOKEN": "  padded  "}, clear=True):
            assert _get_github_token() == "padded"

    def test_ignores_empty_env(self):
        # Empty env var should not short-circuit; fall through to gh CLI (mocked missing).
        with patch.dict("os.environ", {"GITHUB_TOKEN": "   "}, clear=True):
            with patch(
                "notebook_intelligence.skill_github_import.subprocess.run",
                side_effect=FileNotFoundError,
            ):
                assert _get_github_token() is None

    def test_falls_back_to_gh_cli(self):
        with patch.dict("os.environ", {}, clear=True):
            fake = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="gh-cli-token\n", stderr=""
            )
            with patch(
                "notebook_intelligence.skill_github_import.subprocess.run",
                return_value=fake,
            ):
                assert _get_github_token() == "gh-cli-token"

    def test_gh_cli_missing_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "notebook_intelligence.skill_github_import.subprocess.run",
                side_effect=FileNotFoundError,
            ):
                assert _get_github_token() is None

    def test_gh_cli_error_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            fake = subprocess.CompletedProcess(
                args=[], returncode=1, stdout="", stderr="not logged in"
            )
            with patch(
                "notebook_intelligence.skill_github_import.subprocess.run",
                return_value=fake,
            ):
                assert _get_github_token() is None

    def test_gh_cli_timeout_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "notebook_intelligence.skill_github_import.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["gh"], timeout=5),
            ):
                assert _get_github_token() is None


class _FakeUrlopenResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n):
        return b"tar-bytes"


def _capture_headers_urlopen(captured: dict):
    def fake_urlopen(req, timeout):
        captured["headers"] = dict(req.header_items())
        return _FakeUrlopenResponse()

    return fake_urlopen


class TestFetchTarballAuth:
    def _http_error(self, code: int):
        return urllib.error.HTTPError(
            url="https://api.github.com/...",
            code=code,
            msg="err",
            hdrs=None,
            fp=None,
        )

    def test_adds_auth_header_when_token_present(self):
        captured = {}
        with patch(
            "notebook_intelligence.skill_github_import._get_github_token",
            return_value="secret-token",
        ):
            with patch(
                "notebook_intelligence.skill_github_import.urllib.request.urlopen",
                side_effect=_capture_headers_urlopen(captured),
            ):
                _fetch_tarball("owner", "repo", None)

        # urllib lowercases/title-cases header keys; match case-insensitively.
        lowered = {k.lower(): v for k, v in captured["headers"].items()}
        assert lowered.get("authorization") == "Bearer secret-token"
        assert lowered.get("accept") == "application/vnd.github+json"
        assert lowered.get("x-github-api-version") == "2022-11-28"

    def test_no_auth_header_when_no_token(self):
        captured = {}
        with patch(
            "notebook_intelligence.skill_github_import._get_github_token",
            return_value=None,
        ):
            with patch(
                "notebook_intelligence.skill_github_import.urllib.request.urlopen",
                side_effect=_capture_headers_urlopen(captured),
            ):
                _fetch_tarball("owner", "repo", None)

        lowered = {k.lower(): v for k, v in captured["headers"].items()}
        assert "authorization" not in lowered

    def test_401_without_token_prompts_for_auth(self):
        with patch(
            "notebook_intelligence.skill_github_import._get_github_token",
            return_value=None,
        ):
            with patch(
                "notebook_intelligence.skill_github_import.urllib.request.urlopen",
                side_effect=self._http_error(401),
            ):
                with pytest.raises(ValueError, match="requires authentication"):
                    _fetch_tarball("owner", "repo", None)

    def test_403_with_token_reports_rejection(self):
        with patch(
            "notebook_intelligence.skill_github_import._get_github_token",
            return_value="bad-token",
        ):
            with patch(
                "notebook_intelligence.skill_github_import.urllib.request.urlopen",
                side_effect=self._http_error(403),
            ):
                with pytest.raises(ValueError, match="rejected the token"):
                    _fetch_tarball("owner", "repo", None)

    def test_404_without_token_hints_private(self):
        with patch(
            "notebook_intelligence.skill_github_import._get_github_token",
            return_value=None,
        ):
            with patch(
                "notebook_intelligence.skill_github_import.urllib.request.urlopen",
                side_effect=self._http_error(404),
            ):
                with pytest.raises(ValueError, match="private repos require"):
                    _fetch_tarball("owner", "repo", None)

    def test_404_with_token_no_private_hint(self):
        with patch(
            "notebook_intelligence.skill_github_import._get_github_token",
            return_value="some-token",
        ):
            with patch(
                "notebook_intelligence.skill_github_import.urllib.request.urlopen",
                side_effect=self._http_error(404),
            ):
                with pytest.raises(ValueError) as exc_info:
                    _fetch_tarball("owner", "repo", None)
                assert "private repos require" not in str(exc_info.value)
                assert "not found" in str(exc_info.value)


