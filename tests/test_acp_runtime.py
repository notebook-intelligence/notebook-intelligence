"""ACP interpreter checks inspect inert fixtures without launching programs."""

import os
import subprocess

import pytest

from notebook_intelligence.acp_runtime import validate_acp_adapter_runtime


def _executable(path, contents=b"inert executable fixture\n"):
    path.write_bytes(contents)
    path.chmod(0o700)
    return path


@pytest.fixture
def runtime_files(tmp_path, monkeypatch):
    trusted = tmp_path / "trusted-bin"
    trusted.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = _executable(trusted / "env")
    node = _executable(trusted / "node")
    adapter = trusted / "adapter"

    def fail_if_executed(*args, **kwargs):
        pytest.fail("runtime validation must not launch adapters or interpreters")

    monkeypatch.setattr(subprocess, "Popen", fail_if_executed)
    return trusted, workspace, env, node, adapter


def test_non_utf8_shebang_names_the_decoding_failure(runtime_files):
    trusted, _, env, _, adapter = runtime_files
    _executable(adapter, f"#!{env} ".encode() + b"\xff\n")
    with pytest.raises(ValueError, match="interpreter line is not valid UTF-8"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


@pytest.mark.parametrize("suffix", [b"\r", b"\x00", b"\x01", b"\x0b", b" " * 4096])
def test_control_characters_and_overlong_shebangs_fail_before_lookup(runtime_files, suffix):
    trusted, _, env, _, adapter = runtime_files
    _executable(adapter, f"#!{env} node".encode() + suffix + b"\n")
    with pytest.raises(ValueError, match="invalid or overlong interpreter line"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


def test_relative_shebang_interpreter_is_rejected_even_when_executable_exists(runtime_files, monkeypatch):
    trusted, _, _, _, adapter = runtime_files
    monkeypatch.chdir(trusted)
    _executable(adapter, b"#!node\n")
    with pytest.raises(ValueError, match="must declare an absolute interpreter path"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


@pytest.mark.parametrize(
    "arguments",
    [
        "",
        "node --no-warnings",
        "-i node",
        "-S",
        "-S PATH=/tmp node",
        "-S ${NODE}",
        "-S 'node' --no-warnings",
        '-S "node" --no-warnings',
        "-S node\\ --no-warnings",
        "-S node #comment",
        "-S -i node",
    ],
)
def test_ambiguous_env_syntax_is_rejected_with_a_parser_error(runtime_files, arguments):
    trusted, _, env, _, adapter = runtime_files
    _executable(adapter, f"#!{env} {arguments}\n".encode())
    with pytest.raises(ValueError, match="Cannot validate ACP adapter's env interpreter line"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


@pytest.mark.parametrize("arguments", ["node", "-S node --no-warnings --stack-size=2048"])
def test_plain_env_program_and_split_string_flags_are_supported(runtime_files, arguments):
    trusted, _, env, _, adapter = runtime_files
    _executable(adapter, f"#!{env} {arguments}\n".encode())
    validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


@pytest.mark.parametrize("contents", [b"", b"plain text without a shebang\n", b"\x7fELF\xff\x00", b"MZ\x00\xff"])
def test_no_shebang_and_native_bytes_do_not_require_an_interpreter(runtime_files, contents):
    _, _, _, _, adapter = runtime_files
    _executable(adapter, contents)
    validate_acp_adapter_runtime([str(adapter)], env_path="")


@pytest.mark.skipif(os.name == "nt", reason="opening directories as file descriptors is POSIX-specific")
def test_directory_adapter_reports_its_read_failure(runtime_files):
    trusted, _, _, _, adapter = runtime_files
    adapter.mkdir()
    with pytest.raises(ValueError, match="Cannot read ACP adapter.*IsADirectoryError"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO fixtures require POSIX")
def test_fifo_adapter_is_rejected_without_waiting_for_a_writer(runtime_files):
    trusted, _, _, _, adapter = runtime_files
    os.mkfifo(adapter)
    with pytest.raises(ValueError, match="is not a regular file"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))


def test_env_relative_program_is_resolved_from_launch_cwd(runtime_files, tmp_path, monkeypatch):
    trusted, _, env, _, adapter = runtime_files
    launch = tmp_path / "launch"
    (launch / "bin").mkdir(parents=True)
    _executable(launch / "bin" / "node")
    server = tmp_path / "server"
    server.mkdir()
    monkeypatch.chdir(server)
    _executable(adapter, f"#!{env} bin/node\n".encode())
    validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted), cwd=str(launch))
    with pytest.raises(ValueError, match="working directory is needed to check its relative interpreter"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted))
    with pytest.raises(ValueError, match="requires interpreter 'bin/node'.*adapter PATH"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted), cwd=str(server))


def test_env_relative_program_preserves_symlink_parent_traversal(runtime_files, tmp_path):
    trusted, _, env, _, adapter = runtime_files
    launch = tmp_path / "launch"
    launch.mkdir()
    target = tmp_path / "linked-target"
    (target / "nested").mkdir(parents=True)
    (launch / "bin-link").symlink_to(target / "nested", target_is_directory=True)
    actual_node = _executable(target / "node")
    _executable(adapter, f"#!{env} bin-link/../node\n".encode())
    validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted), cwd=str(launch))
    actual_node.unlink()
    _executable(launch / "node")
    # Lexically collapsing '..' would incorrectly select launch/node. The
    # kernel traverses the symlink first, so this interpreter is absent.
    with pytest.raises(ValueError, match="requires interpreter 'bin-link/../node'.*adapter PATH"):
        validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted), cwd=str(launch))


@pytest.mark.parametrize("search_path", ["", ".", "bin"])
def test_env_relative_path_is_resolved_from_launch_cwd(runtime_files, tmp_path, monkeypatch, search_path):
    _, _, env, _, adapter = runtime_files
    launch = tmp_path / "launch"
    directory = launch / search_path if search_path else launch
    directory.mkdir(parents=True)
    _executable(directory / "node")
    server = tmp_path / "server"
    server.mkdir()
    monkeypatch.chdir(server)
    _executable(adapter, f"#!{env} node\n".encode())
    validate_acp_adapter_runtime([str(adapter)], env_path=search_path, cwd=str(launch))
    with pytest.raises(ValueError, match="working directory is needed to check its relative PATH entries"):
        validate_acp_adapter_runtime([str(adapter)], env_path=search_path)
    with pytest.raises(ValueError, match="requires interpreter 'node'.*adapter PATH"):
        validate_acp_adapter_runtime([str(adapter)], env_path=search_path, cwd=str(server))


@pytest.mark.parametrize("via_symlink", [False, True])
def test_default_rejects_absolute_workspace_interpreter_but_override_may_trust_it(runtime_files, via_symlink):
    trusted, workspace, _, _, adapter = runtime_files
    interpreter = _executable(workspace / "runtime")
    if via_symlink:
        alias = trusted / "runtime"
        alias.symlink_to(interpreter)
        interpreter = alias
    _executable(adapter, f"#!{interpreter}\n".encode())
    with pytest.raises(ValueError, match="outside the workspace"):
        validate_acp_adapter_runtime(
            [str(adapter)], env_path=str(trusted), workspace_root=str(workspace)
        )
    validate_acp_adapter_runtime([str(adapter)], env_path=str(trusted), workspace_root=None)


def test_default_rejects_first_env_interpreter_symlink_into_workspace(runtime_files, tmp_path):
    trusted, workspace, env, node, adapter = runtime_files
    node.unlink()
    node.symlink_to(_executable(workspace / "node"))
    fallback = tmp_path / "fallback-bin"
    fallback.mkdir()
    _executable(fallback / "node")
    env_path = os.pathsep.join((str(trusted), str(fallback)))
    _executable(adapter, f"#!{env} node\n".encode())
    # The actual env process selects the first node. A later safe candidate
    # cannot repair its selection, so preflight must reject the first match.
    with pytest.raises(ValueError, match="outside the workspace"):
        validate_acp_adapter_runtime(
            [str(adapter)], env_path=env_path, workspace_root=str(workspace)
        )
    validate_acp_adapter_runtime([str(adapter)], env_path=env_path, workspace_root=None)


@pytest.mark.parametrize("use_env", [False, True])
def test_default_accepts_interpreter_outside_workspace(runtime_files, use_env):
    trusted, workspace, env, node, adapter = runtime_files
    shebang = f"#!{env} node" if use_env else f"#!{node}"
    _executable(adapter, (shebang + "\n").encode())
    validate_acp_adapter_runtime(
        [str(adapter)], env_path=str(trusted), workspace_root=str(workspace)
    )
