"""Daemon state must never follow an attacker-controlled path."""

import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile

import pytest

from agentcad import daemon


pytestmark = pytest.mark.skipif(not daemon.daemon_supported(), reason="POSIX daemon")


@pytest.fixture
def short_dir():
    with tempfile.TemporaryDirectory(prefix="acd-", dir="/tmp") as directory:
        yield Path(directory)


def test_pid_publication_refuses_symlink_without_changing_target(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_text("keep this content")
    pid = tmp_path / "daemon.pid"
    pid.symlink_to(target)
    with pytest.raises(OSError):
        daemon._write_pid_metadata(pid, pid=123, instance_id="test")
    assert target.read_text() == "keep this content"
    assert pid.is_symlink()


def test_pid_publication_is_atomic_and_private(tmp_path):
    pid = tmp_path / "daemon.pid"
    daemon._write_pid_metadata(pid, pid=123, instance_id="first")
    old = pid.stat()
    daemon._write_pid_metadata(pid, pid=456, instance_id="second")
    assert pid.stat().st_ino != old.st_ino
    assert stat.S_IMODE(pid.stat().st_mode) == 0o600
    assert daemon._read_pid_metadata(pid)["instance_id"] == "second"
    assert list(tmp_path.iterdir()) == [pid]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory", "public", "wrong_owner"])
@pytest.mark.parametrize("operation", ["status", "start", "stop", "restart", "spawn"])
def test_lifecycle_refuses_unsafe_pid_without_touching_state(
    tmp_path, monkeypatch, kind, operation
):
    pid = tmp_path / "daemon.pid"
    target = tmp_path / "keep.txt"
    target.write_text(json.dumps({"pid": os.getpid(), "instance_id": "test"}))
    target.chmod(0o600)
    if kind == "symlink":
        pid.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, pid)
    elif kind == "directory":
        pid.mkdir()
    else:
        pid.write_bytes(target.read_bytes())
        pid.chmod(0o644 if kind == "public" else 0o600)
    if kind == "wrong_owner":
        actual_uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: actual_uid + 1)
    before = target.read_bytes()
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    monkeypatch.setattr(daemon.os, "kill", lambda *a: pytest.fail("signaled"))
    if operation == "spawn":
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        monkeypatch.setattr(daemon.os, "fork", lambda: pytest.fail("forked"))
        fn = daemon.spawn_daemon_via_fork
    else:
        fn = daemon.daemon_status if operation == "status" else getattr(daemon, operation + "_daemon")
    result = fn(socket_path=str(tmp_path / "daemon.sock"), pid_path=str(pid))
    assert result["error_kind"] == "unsafe_daemon_state"
    assert result["state_preserved"] is True
    assert os.path.lexists(pid)
    assert target.read_bytes() == before


@pytest.mark.parametrize("kind", ["file", "symlink", "directory", "public_socket"])
def test_start_refuses_unsafe_socket_before_spawning(short_dir, monkeypatch, kind):
    endpoint = short_dir / "daemon.sock"
    listener = None
    if kind == "file":
        endpoint.write_text("keep")
    elif kind == "symlink":
        endpoint.symlink_to(short_dir / "missing")
    elif kind == "directory":
        endpoint.mkdir()
    else:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(endpoint))
        endpoint.chmod(0o666)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    try:
        result = daemon.start_daemon(socket_path=str(endpoint), pid_path=str(short_dir / "daemon.pid"))
        assert result["error_kind"] == "unsafe_daemon_state"
        assert result["state_preserved"] is True
        assert os.path.lexists(endpoint)
    finally:
        if listener:
            listener.close()


def test_default_paths_use_private_xdg_runtime_directory(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("AGENTCAD_SOCKET_PATH", raising=False)
    monkeypatch.delenv("AGENTCAD_PID_PATH", raising=False)
    sock, pid = daemon._default_socket_path(), daemon._default_pid_path()
    assert Path(sock).parent == Path(pid).parent == tmp_path / "agentcad"
    result = daemon.daemon_status(socket_path=sock, pid_path=pid)
    assert result == {"running": False}
    assert not (tmp_path / "agentcad").exists(), "status must not create state"


def test_default_fallback_is_a_per_user_directory(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("AGENTCAD_SOCKET_PATH", raising=False)
    monkeypatch.delenv("AGENTCAD_PID_PATH", raising=False)
    directory = Path(daemon._default_socket_path()).parent
    assert directory == Path(f"/tmp/agentcad-{os.getuid()}")
    assert Path(daemon._default_pid_path()).parent == directory


def test_worker_entrypoint_refuses_relative_runtime_before_leaving_project(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_RUNTIME_DIR", "relative-runtime")
    monkeypatch.delenv("AGENTCAD_SOCKET_PATH", raising=False)
    monkeypatch.delenv("AGENTCAD_PID_PATH", raising=False)
    monkeypatch.setattr(sys, "argv", ["agentcad.daemon"])
    monkeypatch.setattr(
        daemon, "_leave_start_directory", lambda: pytest.fail("left before validation")
    )
    with pytest.raises(OSError, match="XDG_RUNTIME_DIR"):
        daemon._main()
    assert not (tmp_path / "relative-runtime").exists()


@pytest.mark.parametrize("kind", ["symlink", "file", "public", "wrong_owner"])
def test_default_directory_is_checked_before_any_cleanup(short_dir, monkeypatch, kind):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir))
    monkeypatch.delenv("AGENTCAD_SOCKET_PATH", raising=False)
    monkeypatch.delenv("AGENTCAD_PID_PATH", raising=False)
    directory = short_dir / "agentcad"
    if kind == "symlink":
        target = short_dir / "target"
        target.mkdir(mode=0o700)
        directory.symlink_to(target, target_is_directory=True)
    elif kind == "file":
        directory.write_text("keep")
    else:
        directory.mkdir(mode=0o755 if kind == "public" else 0o700)
        if kind == "public":
            directory.chmod(0o755)
    if kind == "wrong_owner":
        actual_uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: actual_uid + 1)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))
    result = daemon.start_daemon()
    assert result["started"] is False
    assert result["error_kind"] == "unsafe_daemon_state"
    assert os.path.lexists(directory)


def test_failed_atomic_publication_keeps_previous_metadata(tmp_path, monkeypatch):
    pid = tmp_path / "daemon.pid"
    daemon._write_pid_metadata(pid, pid=123, instance_id="keep")
    before = pid.read_bytes()

    def fail_replace(*args, **kwargs):
        raise PermissionError("simulated publication failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError):
        daemon._write_pid_metadata(pid, pid=456, instance_id="new")
    assert pid.read_bytes() == before
    assert list(tmp_path.iterdir()) == [pid]


def test_failed_temp_creation_never_removes_an_existing_file(tmp_path, monkeypatch):
    pid = tmp_path / "daemon.pid"
    daemon._write_pid_metadata(pid, pid=123, instance_id="keep")
    original = pid.read_bytes()
    existing = tmp_path / "existing.tmp"
    existing.write_text("do not delete")
    original_open = os.open

    def occupied_name(path, flags, *args, **kwargs):
        if flags & os.O_EXCL:
            # Deterministically model a collision on the randomly chosen name.
            existing.rename(tmp_path / path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", occupied_name)
    with pytest.raises(OSError):
        daemon._write_pid_metadata(pid, pid=456, instance_id="new")
    assert pid.read_bytes() == original
    remaining = [path for path in tmp_path.iterdir() if path != pid]
    assert len(remaining) == 1 and remaining[0].read_text() == "do not delete"


def test_atomic_metadata_supports_long_operator_pid_names(tmp_path):
    pid = tmp_path / ("p" * 240)
    daemon._write_pid_metadata(pid, pid=123, instance_id="test")
    assert daemon._read_pid_metadata(pid)["pid"] == 123


def test_pid_read_does_not_follow_a_link_swapped_after_validation(tmp_path, monkeypatch):
    pid = tmp_path / "daemon.pid"
    daemon._write_pid_metadata(pid, pid=123, instance_id="original")
    target = tmp_path / "keep.txt"
    target.write_text("keep")
    original_open = os.open

    def swap_before_open(path, flags, *args, **kwargs):
        if Path(path) == pid:
            pid.unlink()
            pid.symlink_to(target)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_open)
    with pytest.raises(OSError):
        daemon._read_pid_metadata(pid)
    assert pid.is_symlink() and target.read_text() == "keep"


def test_pid_fifo_is_refused_without_opening_it(tmp_path, monkeypatch):
    pid = tmp_path / "daemon.pid"
    os.mkfifo(pid, 0o600)
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("opened FIFO"))
    with pytest.raises(OSError):
        daemon._read_pid_metadata(pid)


def test_client_never_connects_to_a_public_socket(short_dir, monkeypatch):
    endpoint = short_dir / "public.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(endpoint))
        listener.listen(1)
        endpoint.chmod(0o666)
        monkeypatch.setattr(socket, "socket", lambda *a, **k: pytest.fail("connected"))
        assert daemon.send_request({"type": "run", "argv": ["anything"]}, socket_path=str(endpoint)) is None


def test_private_default_lifecycle_and_overrides(short_dir, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(short_dir))
    monkeypatch.delenv("AGENTCAD_SOCKET_PATH", raising=False)
    monkeypatch.delenv("AGENTCAD_PID_PATH", raising=False)
    sock, pid = daemon._default_socket_path(), daemon._default_pid_path()
    try:
        started = daemon.start_daemon()
        assert started["started"], started
        assert stat.S_IMODE((short_dir / "agentcad").stat().st_mode) == 0o700
        assert stat.S_IMODE(Path(sock).stat().st_mode) == 0o600
        assert stat.S_IMODE(Path(pid).stat().st_mode) == 0o600
        assert daemon.daemon_status()["running"] is True
        restarted = daemon.restart_daemon()
        assert restarted["started"] is True, restarted
        assert restarted["pid"] != started["pid"]
        assert daemon.stop_daemon()["stopped"] is True
        assert not Path(sock).exists() and not Path(pid).exists()
    finally:
        daemon.stop_daemon()
    # Overrides keep the exact operator-chosen paths.
    monkeypatch.setenv("AGENTCAD_SOCKET_PATH", str(short_dir / "custom.sock"))
    monkeypatch.setenv("AGENTCAD_PID_PATH", str(short_dir / "custom.pid"))
    assert daemon._default_socket_path() == str(short_dir / "custom.sock")
    assert daemon._default_pid_path() == str(short_dir / "custom.pid")
