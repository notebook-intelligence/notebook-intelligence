# Copyright (c) Mehmet Bektas <mbektasgh@outlook.com>

"""Adapter resolution checks use inert executable fixtures, never real adapters."""

import os
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from notebook_intelligence import acp_registry as registry


@pytest.fixture
def layout(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    trusted = tmp_path / "trusted-bin"
    trusted.mkdir()
    monkeypatch.delenv("NBI_ACP_AGENT_COMMAND", raising=False)
    monkeypatch.setenv("PATH", str(trusted))
    return workspace, trusted


def _executable(path):
    path.write_text("#!/bin/sh\nexit 99\n")
    path.chmod(0o755)
    return path


def _resolve(workspace):
    return registry.resolve_acp_agent_command(
        registry.resolve_acp_agent("codex"), workspace_root=str(workspace)
    )


def test_default_resolves_preinstalled_adapter_to_absolute_target(layout):
    workspace, trusted = layout
    installed = _executable(trusted / "codex-acp")
    assert _resolve(workspace) == [str(installed.resolve())]
    assert registry.acp_adapter_path(str(workspace)) == str(trusted.resolve())


def test_default_command_uses_configured_workspace_when_not_passed(layout, monkeypatch):
    from notebook_intelligence import util

    workspace, trusted = layout
    installed = _executable(trusted / "codex-acp")
    monkeypatch.setattr(util, "_jupyter_root_dir", str(workspace))
    assert registry.resolve_acp_agent_command(registry.resolve_acp_agent(None)) == [
        str(installed.resolve())
    ]


@pytest.mark.parametrize("path_entry", ["", ".", "bin"])
def test_relative_path_entries_never_select_current_directory(
    layout, monkeypatch, path_entry
):
    workspace, trusted = layout
    server_cwd = workspace.parent / "server-cwd"
    server_cwd.mkdir()
    candidate_dir = server_cwd / path_entry if path_entry else server_cwd
    candidate_dir.mkdir(exist_ok=True)
    _executable(candidate_dir / "codex-acp")
    installed = _executable(trusted / "codex-acp")
    monkeypatch.chdir(server_cwd)
    monkeypatch.setenv("PATH", path_entry + os.pathsep + str(trusted))
    assert registry.acp_adapter_path(str(workspace)) == str(trusted.resolve())
    assert _resolve(workspace) == [str(installed.resolve())]


@pytest.mark.parametrize("later_trusted", [False, True])
@pytest.mark.parametrize("kind", ["direct", "directory-link", "adapter-link"])
def test_workspace_candidates_are_skipped_or_fail_closed(
    layout, monkeypatch, kind, later_trusted
):
    workspace, trusted = layout
    planted = _executable(workspace / "codex-acp")
    entry = workspace
    if kind == "directory-link":
        entry = workspace.parent / "workspace-alias"
        entry.symlink_to(workspace, target_is_directory=True)
    elif kind == "adapter-link":
        entry = workspace.parent / "first-bin"
        entry.mkdir()
        (entry / "codex-acp").symlink_to(planted)
    search_path = str(entry)
    if later_trusted:
        installed = _executable(trusted / "codex-acp")
        search_path += os.pathsep + str(trusted)
    monkeypatch.setenv("PATH", search_path)
    if later_trusted:
        assert _resolve(workspace) == [str(installed.resolve())]
    else:
        with pytest.raises(FileNotFoundError, match="outside the workspace"):
            _resolve(workspace)
    if kind != "adapter-link":
        assert str(workspace) not in registry.acp_adapter_path(str(workspace))


@pytest.mark.parametrize("symlinked_parent", [False, True])
def test_workspace_root_can_be_reached_through_a_symlink(
    layout, monkeypatch, symlinked_parent
):
    workspace, trusted = layout
    _executable(workspace / "codex-acp")
    installed = _executable(trusted / "codex-acp")
    alias = workspace.parent / "root-link"
    if symlinked_parent:
        alias.symlink_to(workspace.parent, target_is_directory=True)
        alias = alias / workspace.name
    else:
        alias.symlink_to(workspace, target_is_directory=True)
    monkeypatch.setenv("PATH", str(workspace) + os.pathsep + str(trusted))
    assert registry.acp_adapter_path(str(alias)) == str(trusted.resolve())
    assert _resolve(alias) == [str(installed.resolve())]


def test_workspace_directory_linking_out_is_still_excluded(layout, monkeypatch):
    workspace, trusted = layout
    _executable(trusted / "codex-acp")
    link = workspace / "external-bin"
    link.symlink_to(trusted, target_is_directory=True)
    monkeypatch.setenv("PATH", str(link))
    assert registry.acp_adapter_path(str(workspace)) == ""
    with pytest.raises(FileNotFoundError):
        _resolve(workspace)


@pytest.mark.parametrize("relative", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_path_directory_cannot_traverse_workspace_link_to_external_target(
    layout, monkeypatch, relative, nested
):
    workspace, trusted = layout
    external = workspace.parent / "external-tools"
    candidate_directory = external / "bin" if nested else external
    candidate_directory.mkdir(parents=True)
    _executable(candidate_directory / "codex-acp")
    workspace_link = workspace / "tools-link"
    workspace_link.symlink_to(
        os.path.relpath(external, workspace) if relative else external,
        target_is_directory=True,
    )
    entry = workspace.parent / "external-link"
    entry.symlink_to(
        os.path.relpath(workspace_link, entry.parent) if relative else workspace_link,
        target_is_directory=True,
    )
    if nested:
        entry /= "bin"
    assert entry.resolve() == candidate_directory
    monkeypatch.setenv("PATH", str(entry))
    assert registry.acp_adapter_path(str(workspace)) == ""
    with pytest.raises(FileNotFoundError, match="outside the workspace"):
        _resolve(workspace)

    # A rejected chain must not hide an independent trusted installation.
    installed = _executable(trusted / "codex-acp")
    monkeypatch.setenv("PATH", str(entry) + os.pathsep + str(trusted))
    assert registry.acp_adapter_path(str(workspace)) == str(trusted.resolve())
    assert _resolve(workspace) == [str(installed.resolve())]


@pytest.mark.parametrize("relative", [False, True])
def test_adapter_cannot_traverse_workspace_link_to_external_target(
    layout, monkeypatch, relative
):
    workspace, trusted = layout
    external = _executable(trusted / "adapter-target")
    workspace_link = workspace / "adapter-link"
    workspace_link.symlink_to(
        os.path.relpath(external, workspace) if relative else external
    )
    entry = trusted / "codex-acp"
    entry.symlink_to(
        os.path.relpath(workspace_link, trusted) if relative else workspace_link
    )
    assert entry.resolve() == external
    with pytest.raises(FileNotFoundError, match="outside the workspace"):
        _resolve(workspace)

    # Explicit administrator overrides may deliberately trust this chain.
    monkeypatch.setenv("NBI_ACP_AGENT_COMMAND", shlex.quote(str(entry)))
    assert _resolve(workspace) == [str(entry)]


def test_parent_traversal_cannot_erase_a_workspace_symlink_hop(layout, monkeypatch):
    workspace, trusted = layout
    _executable(trusted / "codex-acp")
    link = workspace.parent / "workspace-link"
    link.symlink_to(workspace, target_is_directory=True)
    entry = link / ".." / trusted.name
    assert entry.resolve() == trusted
    monkeypatch.setenv("PATH", str(entry))
    assert registry.acp_adapter_path(str(workspace)) == ""
    with pytest.raises(FileNotFoundError):
        _resolve(workspace)
    with pytest.raises(ValueError, match="outside the workspace"):
        registry.validate_acp_executable_path(str(entry / "codex-acp"), str(workspace))


def test_trusted_parent_traversal_keeps_kernel_symlink_semantics(layout, monkeypatch):
    workspace, trusted = layout
    external = workspace.parent / "external-tools"
    (external / "nested").mkdir(parents=True)
    (external / "bin").mkdir()
    installed = _executable(external / "bin" / "codex-acp")
    link = trusted / "linked-directory"
    link.symlink_to(external / "nested", target_is_directory=True)
    entry = link / ".." / "bin"
    monkeypatch.setenv("PATH", str(entry))
    assert registry.acp_adapter_path(str(workspace)) == str(external / "bin")
    assert _resolve(workspace) == [str(installed)]


@pytest.mark.parametrize("use_env", [False, True])
@pytest.mark.parametrize("relative", [False, True])
def test_interpreter_preflight_rejects_intermediate_workspace_links(
    layout, use_env, relative
):
    from notebook_intelligence.acp_runtime import validate_acp_adapter_runtime

    workspace, trusted = layout
    interpreter = _executable(trusted / "runtime")
    workspace_link = workspace / "runtime-link"
    workspace_link.symlink_to(
        os.path.relpath(interpreter, workspace) if relative else interpreter
    )
    alias = trusted / "node"
    alias.symlink_to(
        os.path.relpath(workspace_link, trusted) if relative else workspace_link
    )
    assert alias.resolve() == interpreter
    env = _executable(trusted / "env")
    adapter = _executable(trusted / "codex-acp")
    adapter.write_text(f"#!{env} node\n" if use_env else f"#!{alias}\n")
    with pytest.raises(ValueError, match="outside the workspace"):
        validate_acp_adapter_runtime(
            [str(adapter)], env_path=str(trusted), workspace_root=str(workspace)
        )
    validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


def test_retargeted_workspace_link_never_becomes_a_default_candidate(layout, monkeypatch):
    workspace, trusted = layout
    first = _executable(trusted / "first-adapter")
    second = _executable(trusted / "second-adapter")
    workspace_link = workspace / "adapter-link"
    workspace_link.symlink_to(first)
    alias = trusted / "codex-acp"
    alias.symlink_to(workspace_link)
    for target in (first, second):
        workspace_link.unlink()
        workspace_link.symlink_to(target)
        assert alias.resolve() == target
        with pytest.raises(FileNotFoundError):
            _resolve(workspace)
        with pytest.raises(ValueError, match="outside the workspace"):
            registry.validate_acp_executable_path(str(alias), str(workspace))


@pytest.mark.parametrize("spelling", ["case", "unicode"])
@pytest.mark.parametrize("linking_out", [False, True])
def test_filesystem_aliases_of_workspace_remain_excluded(
    layout, monkeypatch, spelling, linking_out
):
    old_workspace, trusted = layout
    name, alternate = (
        ("MixedCaseWorkspace", "mixedcaseworkspace")
        if spelling == "case" else ("caf\u00e9", "cafe\u0301")
    )
    workspace = old_workspace.parent / name
    workspace.mkdir()
    alias = workspace.parent / alternate
    if not alias.exists() or not alias.samefile(workspace):
        pytest.skip(f"this filesystem does not alias {spelling} spellings")
    if linking_out:
        _executable(trusted / "codex-acp")
        (workspace / "bin").symlink_to(trusted, target_is_directory=True)
        alias = alias / "bin"
    else:
        _executable(workspace / "codex-acp")
    monkeypatch.setenv("PATH", str(alias))
    assert registry.acp_adapter_path(str(workspace)) == ""
    with pytest.raises(FileNotFoundError):
        _resolve(workspace)


@pytest.mark.parametrize("linking_out", [False, True])
def test_root_identity_alias_is_excluded_without_textual_or_symlink_match(
    layout, monkeypatch, linking_out
):
    """Model a second mount of the root on filesystems without case aliases."""
    workspace, trusted = layout
    alias = workspace.parent / "identity-alias"
    alias.mkdir()
    if linking_out:
        _executable(trusted / "codex-acp")
        (alias / "bin").symlink_to(trusted, target_is_directory=True)
        entry = alias / "bin"
    else:
        _executable(alias / "codex-acp")
        entry = alias
    root_info = workspace.stat()
    original_stat = Path.stat

    def aliased_stat(path, *args, **kwargs):
        return root_info if path == alias else original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", aliased_stat)
    monkeypatch.setenv("PATH", str(entry))
    assert registry.acp_adapter_path(str(workspace)) == ""
    with pytest.raises(FileNotFoundError):
        _resolve(workspace)


def test_intermediate_workspace_identity_alias_is_excluded(layout, monkeypatch):
    workspace, trusted = layout
    installed = _executable(trusted / "codex-acp")
    alias = workspace.parent / "identity-alias"
    alias.mkdir()
    (alias / "bin").symlink_to(trusted, target_is_directory=True)
    entry = workspace.parent / "external-link"
    entry.symlink_to(alias / "bin", target_is_directory=True)
    assert entry.resolve() == trusted
    root_info = workspace.stat()
    original_stat = Path.stat

    def aliased_stat(path, *args, **kwargs):
        return root_info if path == alias else original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", aliased_stat)
    monkeypatch.setenv("PATH", str(entry))
    assert registry.acp_adapter_path(str(workspace)) == ""
    with pytest.raises(FileNotFoundError):
        _resolve(workspace)
    with pytest.raises(ValueError, match="outside the workspace"):
        registry.validate_acp_executable_path(str(entry / installed.name), str(workspace))


def test_trusted_directory_and_adapter_symlinks_keep_resolved_targets(layout, monkeypatch):
    workspace, trusted = layout
    installed = _executable(trusted / "adapter-target")
    (trusted / "codex-acp").symlink_to(installed)
    directory_link = workspace.parent / "trusted-link"
    directory_link.symlink_to(trusted, target_is_directory=True)
    monkeypatch.setenv("PATH", str(directory_link))
    assert registry.acp_adapter_path(str(workspace)) == str(trusted.resolve())
    assert _resolve(workspace) == [str(installed.resolve())]


def _separator_alias(workspace):
    """The canonical name would serialize a workspace bin as a second entry."""
    workspace_bin = workspace / "bin"
    workspace_bin.mkdir()
    target = Path(str(workspace.parent / "external") + os.pathsep + str(workspace_bin))
    target.mkdir(parents=True)
    alias = workspace.parent / "trusted-alias"
    alias.symlink_to(target, target_is_directory=True)
    return alias, target, workspace_bin


@pytest.mark.parametrize("later_trusted", [False, True])
def test_separator_in_canonical_directory_cannot_select_adapter(
    layout, monkeypatch, later_trusted
):
    workspace, trusted = layout
    alias, target, _ = _separator_alias(workspace)
    _executable(target / "codex-acp")
    search_path = str(alias)
    if later_trusted:
        installed = _executable(trusted / "codex-acp")
        search_path += os.pathsep + str(trusted)
    monkeypatch.setenv("PATH", search_path)

    if later_trusted:
        assert _resolve(workspace) == [str(installed.resolve())]
    else:
        with pytest.raises(FileNotFoundError, match="outside the workspace"):
            _resolve(workspace)


@pytest.mark.parametrize("trusted_interpreter", [False, True])
def test_separator_in_canonical_directory_cannot_inject_interpreter_path(
    layout, monkeypatch, trusted_interpreter
):
    workspace, trusted = layout
    alias, target, workspace_bin = _separator_alias(workspace)
    _executable(workspace_bin / "node")
    installed = _executable(trusted / "codex-acp")
    trusted_node = _executable(trusted / "node") if trusted_interpreter else None
    monkeypatch.setenv("PATH", str(alias) + os.pathsep + str(trusted))

    # The directory is external before serialization; only the separator
    # check prevents it from injecting the workspace directory into PATH.
    assert not target.is_relative_to(workspace)
    assert str(workspace_bin) in str(target).split(os.pathsep)
    assert _resolve(workspace) == [str(installed.resolve())]
    child_path = registry.acp_adapter_path(str(workspace))
    assert child_path.split(os.pathsep) == [str(trusted.resolve())]
    assert shutil.which("node", path=child_path) == (
        str(trusted_node.resolve()) if trusted_node else None
    )


@pytest.mark.parametrize("kind", ["direct", "link-in", "link-out"])
def test_default_executable_validation_rejects_workspace_paths(layout, kind):
    workspace, trusted = layout
    candidate = _executable(workspace / "node")
    if kind == "link-in":
        candidate = trusted / "node"
        candidate.symlink_to(workspace / "node")
    elif kind == "link-out":
        candidate.unlink()
        candidate.symlink_to(_executable(trusted / "node"))
    with pytest.raises(ValueError, match="outside the workspace.*Install the adapter"):
        registry.validate_acp_executable_path(str(candidate), str(workspace))


def test_default_executable_validation_accepts_trusted_target(layout):
    workspace, trusted = layout
    installed = _executable(trusted / "node-real")
    alias = trusted / "node"
    alias.symlink_to(installed)
    registry.validate_acp_executable_path(str(alias), str(workspace))


@pytest.mark.parametrize("kind", ["relative", "missing", "loop", "invalid"])
def test_default_executable_validation_rejects_uninspectable_paths(layout, kind):
    workspace, trusted = layout
    candidate = trusted / "node"
    if kind == "relative":
        candidate = Path("node")
    elif kind == "loop":
        candidate.symlink_to(candidate)
    elif kind == "invalid":
        candidate = "invalid\x00path"
    with pytest.raises(ValueError, match="existing, inspectable absolute path"):
        registry.validate_acp_executable_path(str(candidate), str(workspace))


def test_default_executable_validation_rejects_workspace_identity_alias(layout, monkeypatch):
    workspace, trusted = layout
    alias = workspace.parent / "identity-alias"
    alias.mkdir()
    interpreter = _executable(alias / "node")
    root_info = workspace.stat()
    original_stat = Path.stat

    def aliased_stat(path, *args, **kwargs):
        return root_info if path == alias else original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", aliased_stat)
    with pytest.raises(ValueError, match="outside the workspace"):
        registry.validate_acp_executable_path(str(interpreter), str(workspace))


def test_missing_files_and_symlink_loops_do_not_hide_later_installation(layout, monkeypatch):
    workspace, trusted = layout
    installed = _executable(trusted / "codex-acp")
    loop = workspace.parent / "loop"
    loop.symlink_to(loop)
    not_directory = _executable(workspace.parent / "not-a-directory")
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, [
        workspace.parent / "missing", not_directory, loop, trusted,
    ])))
    assert registry.acp_adapter_path(str(workspace)) == str(trusted.resolve())
    assert _resolve(workspace) == [str(installed.resolve())]


@pytest.mark.parametrize("error_type", [OSError, RuntimeError, ValueError])
@pytest.mark.parametrize("failure_at", ["directory", "executable"])
def test_uninspectable_entries_do_not_hide_later_installation(
    layout, monkeypatch, error_type, failure_at
):
    workspace, trusted = layout
    installed = _executable(trusted / "codex-acp")
    broken = workspace.parent / "broken-bin"
    broken.mkdir()
    broken_adapter = _executable(broken / "codex-acp")
    failing_path = broken if failure_at == "directory" else broken_adapter
    original_lstat = Path.lstat

    def failing_lstat(path, *args, **kwargs):
        if path == failing_path:
            raise error_type("uninspectable fixture")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", failing_lstat)
    monkeypatch.setenv("PATH", str(broken) + os.pathsep + str(trusted))
    assert _resolve(workspace) == [str(installed.resolve())]
    if failure_at == "directory":
        assert registry.acp_adapter_path(str(workspace)) == str(trusted.resolve())


def test_unreadable_ancestor_is_skipped(layout, monkeypatch):
    workspace, trusted = layout
    installed = _executable(trusted / "codex-acp")
    unreadable = workspace.parent / "unreadable"
    directory = unreadable / "bin"
    directory.mkdir(parents=True)
    _executable(directory / "codex-acp")
    original_stat = Path.stat

    def failing_stat(path, *args, **kwargs):
        if path == unreadable:
            raise PermissionError("unreadable fixture")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", failing_stat)
    monkeypatch.setenv("PATH", str(directory) + os.pathsep + str(trusted))
    assert registry.acp_adapter_path(str(workspace)) == str(trusted.resolve())
    assert _resolve(workspace) == [str(installed.resolve())]


@pytest.mark.parametrize("root_kind", ["empty", "missing", "file", "loop"])
def test_unknown_workspace_root_fails_closed(layout, monkeypatch, root_kind):
    workspace, trusted = layout
    _executable(trusted / "codex-acp")
    root = workspace.parent / root_kind
    if root_kind == "file":
        root.write_text("not a directory")
    elif root_kind == "loop":
        root.symlink_to(root)
    elif root_kind == "empty":
        root = ""
    with pytest.raises(ValueError, match="workspace root.*existing, inspectable"):
        registry.acp_adapter_path(str(root))
    with pytest.raises(ValueError, match="workspace root.*existing, inspectable"):
        _resolve(root)


@pytest.mark.parametrize("zero_identity_at", ["root", "entry"])
def test_unknown_filesystem_identity_fails_closed(layout, monkeypatch, zero_identity_at):
    workspace, trusted = layout
    _executable(trusted / "codex-acp")
    failing_path = workspace if zero_identity_at == "root" else trusted
    original_stat = Path.stat

    def zero_identity_stat(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if path == failing_path:
            return SimpleNamespace(st_mode=info.st_mode, st_dev=0, st_ino=0)
        return info

    monkeypatch.setattr(Path, "stat", zero_identity_stat)
    if zero_identity_at == "root":
        with pytest.raises(ValueError, match="workspace root"):
            _resolve(workspace)
    else:
        assert registry.acp_adapter_path(str(workspace)) == ""
        with pytest.raises(FileNotFoundError):
            _resolve(workspace)


@pytest.mark.parametrize("command", ["", "  ", "'unclosed", "codex-acp", "./codex-acp"])
def test_override_requires_nonempty_absolute_command(layout, monkeypatch, command):
    workspace, _ = layout
    monkeypatch.setenv("NBI_ACP_AGENT_COMMAND", command)
    with pytest.raises(ValueError):
        _resolve(workspace)


def test_missing_absolute_override_fails_without_path_fallback(layout, monkeypatch):
    workspace, trusted = layout
    _executable(trusted / "codex-acp")
    monkeypatch.setenv("NBI_ACP_AGENT_COMMAND", str(trusted / "missing"))
    with pytest.raises(FileNotFoundError, match="missing"):
        _resolve(workspace)


def test_explicit_override_preserves_arguments_and_admin_workspace_trust(layout, monkeypatch):
    workspace, _ = layout
    adapter = _executable(workspace / "custom adapter")
    monkeypatch.setenv(
        "NBI_ACP_AGENT_COMMAND", shlex.join([str(adapter), "--label", "two words"])
    )
    assert _resolve(workspace) == [str(adapter), "--label", "two words"]
    # This explicit administrator decision does not depend on workspace discovery.
    assert _resolve("") == [str(adapter), "--label", "two words"]


@pytest.mark.parametrize("error_type", [PermissionError, RuntimeError])
def test_uninspectable_workspace_root_has_a_specific_error(layout, monkeypatch, error_type):
    workspace, trusted = layout
    _executable(trusted / "codex-acp")
    original_resolve = Path.resolve

    def failing_resolve(path, *args, **kwargs):
        if path == workspace:
            raise error_type("uninspectable root fixture")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)
    with pytest.raises(ValueError, match="workspace root.*existing, inspectable"):
        _resolve(workspace)


def test_explicit_override_preserves_shim_name(layout, monkeypatch):
    workspace, trusted = layout
    target = _executable(trusted / "toolchain-shim")
    adapter = trusted / "codex-acp"
    adapter.symlink_to(target)
    monkeypatch.setenv("NBI_ACP_AGENT_COMMAND", shlex.quote(str(adapter)))
    assert _resolve(workspace) == [str(adapter)]
    assert str(adapter) != str(target)
