import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from notebook_intelligence.skill_manager import SkillManager
from notebook_intelligence.skillset import SKILL_ENTRY_FILE, Skill, serialize_skill_md
from tests.conftest import build_tarball


@pytest.fixture
def skill_dirs(tmp_path):
    user_dir = tmp_path / "user_skills"
    project_dir = tmp_path / "project_skills"
    user_dir.mkdir()
    project_dir.mkdir()
    return user_dir, project_dir


@pytest.fixture
def manager(skill_dirs):
    user_dir, project_dir = skill_dirs
    return SkillManager(user_dir, project_dir)


def _write_bundle(scope_dir: Path, name: str, description: str = "test", body: str = "body", tools=None):
    bundle = scope_dir / name
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / SKILL_ENTRY_FILE).write_text(
        serialize_skill_md(name, description, tools or [], body), encoding="utf-8"
    )
    return bundle


class TestDiscovery:
    def test_empty_dirs_return_empty_list(self, manager):
        assert manager.list_skills() == []

    def test_discovers_user_skill(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        _write_bundle(user_dir, "alpha", description="user skill")
        skills = manager.list_skills()
        assert len(skills) == 1
        assert skills[0].name == "alpha"
        assert skills[0].scope == "user"
        assert skills[0].description == "user skill"

    def test_discovers_project_skill(self, manager, skill_dirs):
        _, project_dir = skill_dirs
        _write_bundle(project_dir, "beta")
        skills = manager.list_skills()
        assert len(skills) == 1
        assert skills[0].scope == "project"

    def test_discovers_bundle_with_extra_files(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        bundle = _write_bundle(user_dir, "multi", description="bundle")
        (bundle / "helper.py").write_text("print('hi')", encoding="utf-8")
        skills = manager.list_skills()
        assert len(skills) == 1
        assert skills[0].name == "multi"
        assert "helper.py" in skills[0].list_files()
        assert SKILL_ENTRY_FILE in skills[0].list_files()

    def test_discovers_mixed_scopes(self, manager, skill_dirs):
        user_dir, project_dir = skill_dirs
        _write_bundle(user_dir, "one")
        _write_bundle(project_dir, "two")
        _write_bundle(user_dir, "three")
        skills = manager.list_skills()
        assert {(s.name, s.scope) for s in skills} == {
            ("one", "user"),
            ("three", "user"),
            ("two", "project"),
        }

    def test_top_level_md_files_are_ignored(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        (user_dir / "loose.md").write_text(
            serialize_skill_md("loose", "d", [], "b"), encoding="utf-8"
        )
        assert manager.list_skills() == []

    def test_directory_without_skill_md_is_ignored(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        stray = user_dir / "not-a-skill"
        stray.mkdir()
        (stray / "readme.md").write_text("not a skill", encoding="utf-8")
        assert manager.list_skills() == []

    def test_malformed_skill_is_skipped_without_crash(self, manager, skill_dirs, caplog):
        user_dir, _ = skill_dirs
        _write_bundle(user_dir, "good", description="desc", body="body")
        bad = user_dir / "bad"
        bad.mkdir()
        (bad / SKILL_ENTRY_FILE).write_text(
            "---\napply: [unclosed\n---\nbody", encoding="utf-8"
        )
        skills = manager.list_skills()
        assert len(skills) == 1
        assert skills[0].name == "good"


class TestCreate:
    def test_create_skill(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        skill = manager.create_skill(
            scope="user",
            name="my-skill",
            description="does a thing",
            allowed_tools=["Read", "Grep"],
            body="# Body",
        )
        assert skill.name == "my-skill"
        assert (user_dir / "my-skill" / SKILL_ENTRY_FILE).exists()
        reloaded = Skill.from_path(user_dir / "my-skill", "user")
        assert reloaded.description == "does a thing"
        assert reloaded.allowed_tools == ["Read", "Grep"]

    def test_create_rejects_duplicate_name(self, manager):
        manager.create_skill("user", "dup", "d", [], "b")
        with pytest.raises(ValueError, match="already exists"):
            manager.create_skill("user", "dup", "d", [], "b")

    def test_create_rejects_invalid_name(self, manager):
        for bad in ["UPPER", "with space", "has/slash", "../escape", "", "a" * 100]:
            with pytest.raises(ValueError, match="Invalid skill name"):
                manager.create_skill("user", bad, "d", [], "b")

    def test_create_same_name_in_different_scopes_is_allowed(self, manager):
        manager.create_skill("user", "shared", "u", [], "")
        manager.create_skill("project", "shared", "p", [], "")
        skills = manager.list_skills()
        assert len(skills) == 2


class TestUpdate:
    def test_update_changes_description(self, manager):
        manager.create_skill("user", "edit-me", "old", [], "body")
        updated = manager.update_skill("user", "edit-me", description="new")
        assert updated.description == "new"

    def test_update_missing_skill_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.update_skill("user", "nope", description="x")

    def test_update_writes_to_skill_md(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        manager.create_skill("user", "bun", "d", [], "initial")
        manager.update_skill("user", "bun", body="updated body")
        content = (user_dir / "bun" / SKILL_ENTRY_FILE).read_text()
        assert "updated body" in content


class TestDelete:
    def test_delete_removes_directory(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        manager.create_skill("user", "bun", "d", [], "")
        (user_dir / "bun" / "helper.txt").write_text("x")
        manager.delete_skill("user", "bun")
        assert not (user_dir / "bun").exists()

    def test_delete_missing_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.delete_skill("user", "nope")


class TestRenameSkill:
    def test_rename_moves_directory(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        manager.create_skill("user", "old", "d", [], "body")
        renamed = manager.rename_skill("user", "old", "new")
        assert renamed.name == "new"
        assert not (user_dir / "old").exists()
        assert (user_dir / "new" / SKILL_ENTRY_FILE).exists()

    def test_rename_updates_frontmatter(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        manager.create_skill("user", "old", "d", [], "body")
        manager.rename_skill("user", "old", "new")
        content = (user_dir / "new" / SKILL_ENTRY_FILE).read_text()
        assert "name: new" in content

    def test_rename_preserves_bundle_files(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        manager.create_skill("user", "old", "d", [], "")
        manager.write_bundle_file("user", "old", "helper.py", "print(1)")
        manager.rename_skill("user", "old", "new")
        assert (user_dir / "new" / "helper.py").read_text() == "print(1)"

    def test_rename_rejects_invalid_new_name(self, manager):
        manager.create_skill("user", "old", "d", [], "")
        with pytest.raises(ValueError, match="Invalid skill name"):
            manager.rename_skill("user", "old", "UPPER")

    def test_rename_rejects_duplicate(self, manager):
        manager.create_skill("user", "a", "d", [], "")
        manager.create_skill("user", "b", "d", [], "")
        with pytest.raises(FileExistsError):
            manager.rename_skill("user", "a", "b")

    def test_rename_missing_raises(self, manager):
        with pytest.raises(FileNotFoundError):
            manager.rename_skill("user", "nope", "new")

    def test_rename_to_same_name_is_noop(self, manager):
        manager.create_skill("user", "x", "d", [], "")
        result = manager.rename_skill("user", "x", "x")
        assert result.name == "x"

    def test_rename_fires_listener(self, manager):
        manager.create_skill("user", "old", "d", [], "")
        calls = []
        manager.on_skills_changed(lambda: calls.append(1))
        manager.rename_skill("user", "old", "new")
        assert calls == [1]


class TestBundleFiles:
    def test_write_read_bundle_file(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "helper.py", "print('hi')")
        assert manager.read_bundle_file("user", "bun", "helper.py") == "print('hi')"

    def test_write_rejects_path_traversal(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        for bad in ["../escape.py", "/absolute.py", "subdir\\win.py"]:
            with pytest.raises(ValueError):
                manager.write_bundle_file("user", "bun", bad, "x")

    def test_write_allows_nested_paths(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "scripts/run.sh", "#!/bin/sh")
        assert manager.read_bundle_file("user", "bun", "scripts/run.sh") == "#!/bin/sh"

    def test_delete_skill_md_is_blocked(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        with pytest.raises(ValueError, match="Cannot delete"):
            manager.delete_bundle_file("user", "bun", SKILL_ENTRY_FILE)

    def test_delete_bundle_file(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "extra.txt", "junk")
        manager.delete_bundle_file("user", "bun", "extra.txt")
        with pytest.raises(FileNotFoundError):
            manager.read_bundle_file("user", "bun", "extra.txt")

    def test_rename_bundle_file(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "old.txt", "x")
        manager.rename_bundle_file("user", "bun", "old.txt", "new.txt")
        assert manager.read_bundle_file("user", "bun", "new.txt") == "x"
        with pytest.raises(FileNotFoundError):
            manager.read_bundle_file("user", "bun", "old.txt")

    def test_rename_bundle_file_to_nested_path(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "a.txt", "x")
        manager.rename_bundle_file("user", "bun", "a.txt", "sub/b.txt")
        assert manager.read_bundle_file("user", "bun", "sub/b.txt") == "x"

    def test_rename_bundle_file_rejects_skill_md(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "other.txt", "x")
        with pytest.raises(ValueError):
            manager.rename_bundle_file("user", "bun", SKILL_ENTRY_FILE, "other.md")
        with pytest.raises(ValueError):
            manager.rename_bundle_file("user", "bun", "other.txt", SKILL_ENTRY_FILE)

    def test_rename_bundle_file_rejects_path_traversal(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "a.txt", "x")
        with pytest.raises(ValueError):
            manager.rename_bundle_file("user", "bun", "a.txt", "../escape.txt")

    def test_rename_bundle_file_rejects_collision(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        manager.write_bundle_file("user", "bun", "a.txt", "x")
        manager.write_bundle_file("user", "bun", "b.txt", "y")
        with pytest.raises(FileExistsError):
            manager.rename_bundle_file("user", "bun", "a.txt", "b.txt")

    def test_rename_missing_file_raises(self, manager):
        manager.create_skill("user", "bun", "d", [], "")
        with pytest.raises(FileNotFoundError):
            manager.rename_bundle_file("user", "bun", "missing.txt", "other.txt")


class TestChangeNotifications:
    def test_create_fires_listener(self, manager):
        calls = []
        manager.on_skills_changed(lambda: calls.append(1))
        manager.create_skill("user", "x", "d", [], "")
        assert calls == [1]

    def test_update_fires_listener(self, manager):
        manager.create_skill("user", "x", "d", [], "")
        calls = []
        manager.on_skills_changed(lambda: calls.append(1))
        manager.update_skill("user", "x", description="new")
        assert calls == [1]

    def test_delete_fires_listener(self, manager):
        manager.create_skill("user", "x", "d", [], "")
        calls = []
        manager.on_skills_changed(lambda: calls.append(1))
        manager.delete_skill("user", "x")
        assert calls == [1]

    def test_listener_exception_does_not_block_others(self, manager):
        calls = []
        manager.on_skills_changed(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        manager.on_skills_changed(lambda: calls.append(1))
        manager.create_skill("user", "x", "d", [], "")
        assert calls == [1]


class TestWatcher:
    def test_watcher_detects_external_change(self, skill_dirs):
        user_dir, project_dir = skill_dirs
        manager = SkillManager(user_dir, project_dir)
        manager.WATCH_INTERVAL_SECONDS = 0.05  # type: ignore[attr-defined]
        fired = threading.Event()
        manager.on_skills_changed(fired.set)
        manager.start_watching()
        try:
            # Ensure mtime baseline is recorded before we mutate
            time.sleep(0.1)
            _write_bundle(user_dir, "external")
            assert fired.wait(timeout=2.0), "watcher did not fire on external change"
        finally:
            manager.stop_watching()

    def test_watcher_stop_is_idempotent(self, manager):
        manager.stop_watching()
        manager.stop_watching()


class TestGitHubImport:
    def _patch(self, tar: bytes):
        return patch(
            "notebook_intelligence.skill_github_import._fetch_tarball",
            return_value=tar,
        )

    def test_preview_returns_metadata(self, manager):
        tar = build_tarball({
            "repo-xyz/SKILL.md": (
                "---\nname: my-skill\ndescription: desc\n"
                "allowed-tools: [Read]\n---\nbody text"
            ),
            "repo-xyz/notes.md": "notes",
        })
        with self._patch(tar):
            preview = manager.preview_github_import(
                "https://github.com/owner/repo"
            )
        assert preview["name"] == "my-skill"
        assert preview["description"] == "desc"
        assert preview["allowed_tools"] == ["Read"]
        assert preview["files"] == ["notes.md"]
        assert preview["exists_in_user_scope"] is False
        assert preview["exists_in_project_scope"] is False

    def test_preview_flags_existing_skill(self, manager):
        manager.create_skill("user", "my-skill", "d", [], "")
        tar = build_tarball({
            "repo-xyz/SKILL.md": "---\nname: my-skill\ndescription: d\n---\nb",
        })
        with self._patch(tar):
            preview = manager.preview_github_import(
                "https://github.com/owner/repo"
            )
        assert preview["exists_in_user_scope"] is True
        assert preview["exists_in_project_scope"] is False

    def test_import_installs_skill_with_source(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        tar = build_tarball({
            "repo-xyz/SKILL.md": (
                "---\nname: imported\ndescription: from gh\n---\nhello"
            ),
            "repo-xyz/helper.txt": "assets",
        })
        with self._patch(tar):
            skill = manager.import_from_github(
                "https://github.com/owner/repo", scope="user"
            )
        assert skill.name == "imported"
        assert skill.source == "https://github.com/owner/repo"
        assert (user_dir / "imported" / SKILL_ENTRY_FILE).exists()
        assert (user_dir / "imported" / "helper.txt").exists()
        md = (user_dir / "imported" / SKILL_ENTRY_FILE).read_text()
        assert "source: https://github.com/owner/repo" in md

    def test_import_with_name_override(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        tar = build_tarball({
            "repo-xyz/SKILL.md": "---\nname: orig\ndescription: d\n---\nb",
        })
        with self._patch(tar):
            skill = manager.import_from_github(
                "https://github.com/owner/repo",
                scope="user",
                name_override="renamed",
            )
        assert skill.name == "renamed"
        assert (user_dir / "renamed").is_dir()
        assert not (user_dir / "orig").exists()

    def test_import_collision_without_overwrite_raises(self, manager):
        manager.create_skill("user", "dup", "existing", [], "orig")
        tar = build_tarball({
            "repo-xyz/SKILL.md": "---\nname: dup\ndescription: new\n---\nnew body",
        })
        with self._patch(tar):
            with pytest.raises(FileExistsError):
                manager.import_from_github(
                    "https://github.com/owner/repo", scope="user"
                )

    def test_import_collision_with_overwrite_replaces(self, manager, skill_dirs):
        user_dir, _ = skill_dirs
        manager.create_skill("user", "dup", "existing", [], "orig")
        (user_dir / "dup" / "old_file.txt").write_text("old")
        tar = build_tarball({
            "repo-xyz/SKILL.md": "---\nname: dup\ndescription: new\n---\nnew body",
            "repo-xyz/new_file.txt": "new",
        })
        with self._patch(tar):
            skill = manager.import_from_github(
                "https://github.com/owner/repo",
                scope="user",
                overwrite=True,
            )
        assert skill.description == "new"
        assert (user_dir / "dup" / "new_file.txt").exists()
        assert not (user_dir / "dup" / "old_file.txt").exists()

    def test_import_invalid_name_override_raises(self, manager):
        tar = build_tarball({
            "repo-xyz/SKILL.md": "---\nname: ok\ndescription: d\n---\nb",
        })
        with self._patch(tar):
            with pytest.raises(ValueError, match="Invalid skill name"):
                manager.import_from_github(
                    "https://github.com/owner/repo",
                    scope="user",
                    name_override="INVALID NAME!!",
                )

    def test_import_fires_change_listener(self, manager):
        tar = build_tarball({
            "repo-xyz/SKILL.md": "---\nname: gh-skill\ndescription: d\n---\nb",
        })
        calls = []
        manager.on_skills_changed(lambda: calls.append(1))
        with self._patch(tar):
            manager.import_from_github(
                "https://github.com/owner/repo", scope="user"
            )
        assert calls == [1]
