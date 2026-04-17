import io
import tarfile
from unittest.mock import patch

import pytest

from notebook_intelligence.skill_github_import import (
    _derive_name,
    _extract_skill,
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
