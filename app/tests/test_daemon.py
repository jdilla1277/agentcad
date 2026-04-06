import json
import os
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest
from click.testing import CliRunner


def _short_sock_path(name):
    """Return a short /tmp socket path to avoid AF_UNIX length limits."""
    return f"/tmp/agentcad-test-{name}-{os.getpid()}.sock"


def _short_pid_path(name):
    """Return a short /tmp PID path matching the socket."""
    return f"/tmp/agentcad-test-{name}-{os.getpid()}.pid"


# ---------- Phase 1: Protocol ----------

class TestProtocol:
    def test_encode_message(self):
        from agentcad.daemon import encode_message

        msg = {"type": "ping"}
        encoded = encode_message(msg)
        # 4-byte big-endian length prefix + UTF-8 JSON
        payload = json.dumps(msg).encode("utf-8")
        assert encoded == struct.pack("!I", len(payload)) + payload

    def test_decode_message(self):
        from agentcad.daemon import decode_message

        msg = {"type": "pong"}
        payload = json.dumps(msg).encode("utf-8")
        data = struct.pack("!I", len(payload)) + payload
        decoded, remainder = decode_message(data)
        assert decoded == msg
        assert remainder == b""

    def test_decode_message_with_remainder(self):
        from agentcad.daemon import decode_message

        msg = {"type": "pong"}
        payload = json.dumps(msg).encode("utf-8")
        extra = b"leftover"
        data = struct.pack("!I", len(payload)) + payload + extra
        decoded, remainder = decode_message(data)
        assert decoded == msg
        assert remainder == extra

    def test_decode_message_incomplete_header(self):
        from agentcad.daemon import decode_message

        decoded, remainder = decode_message(b"\x00\x00")
        assert decoded is None
        assert remainder == b"\x00\x00"

    def test_decode_message_incomplete_payload(self):
        from agentcad.daemon import decode_message

        data = struct.pack("!I", 100) + b"short"
        decoded, remainder = decode_message(data)
        assert decoded is None
        assert remainder == data

    def test_roundtrip(self):
        from agentcad.daemon import decode_message, encode_message

        original = {"command": "run", "args": {"script": "box.py", "output": "v1"}}
        encoded = encode_message(original)
        decoded, remainder = decode_message(encoded)
        assert decoded == original
        assert remainder == b""


# ---------- Phase 1: Client fallback ----------

class TestSendRequest:
    def test_send_request_returns_none_when_no_socket(self, tmp_path):
        from agentcad.daemon import send_request

        sock_path = str(tmp_path / "nonexistent.sock")
        result = send_request({"type": "ping"}, socket_path=sock_path)
        assert result is None

    def test_send_request_returns_none_on_connection_refused(self, tmp_path):
        from agentcad.daemon import send_request

        # Create a socket file but nothing is listening
        sock_path = _short_sock_path("dead")
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(sock_path)
            s.close()
            result = send_request({"type": "ping"}, socket_path=sock_path)
            assert result is None
        finally:
            if os.path.exists(sock_path):
                os.unlink(sock_path)


# ---------- Phase 2: Server handlers ----------

class TestDaemonServer:
    def test_handles_ping(self):
        from agentcad.daemon import DaemonServer

        server = DaemonServer.__new__(DaemonServer)
        response = server.handle_request({"type": "ping"})
        assert response["type"] == "pong"

    def test_handles_shutdown(self):
        from agentcad.daemon import DaemonServer

        server = DaemonServer.__new__(DaemonServer)
        server._running = True
        response = server.handle_request({"type": "shutdown"})
        assert response["type"] == "ack"
        assert server._running is False

    def test_handles_run(self, isolated_dir):
        from agentcad.daemon import DaemonServer

        # Set up a agentcad project
        from agentcad.cli import cli
        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "test"])

        # Write a simple script
        script = isolated_dir / "box.py"
        script.write_text("result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        server = DaemonServer.__new__(DaemonServer)
        response = server.handle_request({
            "type": "run",
            "cwd": str(isolated_dir),
            "argv": ["run", str(script), "--output", "box"],
        })
        assert response["exit_code"] == 0
        output = json.loads(response["output"])
        assert output["status"] == "success"
        assert output["label"] == "box"

    def test_handles_unknown_type(self):
        from agentcad.daemon import DaemonServer

        server = DaemonServer.__new__(DaemonServer)
        response = server.handle_request({"type": "unknown_thing"})
        assert response["type"] == "error"
        assert "unknown" in response["message"].lower()

    def test_run_preserves_cwd(self, isolated_dir):
        """Daemon restores original CWD after handling a run request."""
        from agentcad.daemon import DaemonServer

        original_cwd = os.getcwd()
        server = DaemonServer.__new__(DaemonServer)

        # Set up project in a subdirectory
        project_dir = isolated_dir / "proj"
        project_dir.mkdir()

        from agentcad.cli import cli
        runner = CliRunner()
        old_cwd = os.getcwd()
        os.chdir(project_dir)
        runner.invoke(cli, ["init", "--name", "test"])
        os.chdir(old_cwd)

        script = project_dir / "box.py"
        script.write_text("result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        server.handle_request({
            "type": "run",
            "cwd": str(project_dir),
            "argv": ["run", str(script), "--output", "box"],
        })
        # CWD should be restored
        assert os.getcwd() == old_cwd


# ---------- Phase 3: Lifecycle ----------

@pytest.fixture
def daemon_paths():
    """Provide short socket/PID paths and clean up after test."""
    import random
    tag = f"{os.getpid()}-{random.randint(0, 999999)}"
    sock = f"/tmp/agentcad-test-{tag}.sock"
    pid = f"/tmp/agentcad-test-{tag}.pid"
    yield sock, pid
    for p in (sock, pid):
        if os.path.exists(p):
            os.unlink(p)


class TestLifecycle:
    def test_server_creates_socket_and_pid(self, daemon_paths):
        """serve() creates socket and PID file, removed on shutdown."""
        from agentcad.daemon import DaemonServer, send_request

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)

        # Run server in a thread
        t = threading.Thread(target=server.serve)
        t.start()

        # Wait for socket to appear
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        assert os.path.exists(sock_path)
        assert os.path.exists(pid_path)

        # PID file should contain an integer
        pid = int(open(pid_path).read().strip())
        assert pid == os.getpid()

        # Ping to verify it's alive
        resp = send_request({"type": "ping"}, socket_path=sock_path)
        assert resp["type"] == "pong"

        # Shut it down
        resp = send_request({"type": "shutdown"}, socket_path=sock_path)
        assert resp["type"] == "ack"
        t.join(timeout=5)
        assert not t.is_alive()

        # Socket and PID cleaned up
        assert not os.path.exists(sock_path)
        assert not os.path.exists(pid_path)

    def test_daemon_status_running(self, daemon_paths):
        from agentcad.daemon import DaemonServer, daemon_status, send_request

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        status = daemon_status(socket_path=sock_path, pid_path=pid_path)
        assert status["running"] is True
        assert status["pid"] == os.getpid()

        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)

    def test_daemon_status_not_running(self, daemon_paths):
        from agentcad.daemon import daemon_status

        sock_path, pid_path = daemon_paths
        status = daemon_status(socket_path=sock_path, pid_path=pid_path)
        assert status["running"] is False

    def test_stale_socket_cleanup(self, daemon_paths):
        """If PID file exists but process is dead, start_daemon cleans up."""
        from agentcad.daemon import daemon_status

        sock_path, pid_path = daemon_paths

        # Create stale PID file with a dead PID
        with open(pid_path, "w") as f:
            f.write("999999999")  # Almost certainly not running
        # Create stale socket file
        with open(sock_path, "w") as f:
            f.write("")

        status = daemon_status(socket_path=sock_path, pid_path=pid_path)
        # Should report not running (dead process)
        assert status["running"] is False

    def test_stop_daemon_sends_shutdown(self, daemon_paths):
        from agentcad.daemon import DaemonServer, send_request, stop_daemon

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        result = stop_daemon(socket_path=sock_path, pid_path=pid_path)
        assert result["stopped"] is True
        t.join(timeout=5)
        assert not t.is_alive()

    def test_stop_daemon_not_running(self, daemon_paths):
        from agentcad.daemon import stop_daemon

        sock_path, pid_path = daemon_paths
        result = stop_daemon(socket_path=sock_path, pid_path=pid_path)
        assert result["stopped"] is False

    def test_start_daemon_creates_process(self, daemon_paths):
        from agentcad.daemon import start_daemon, stop_daemon, send_request

        sock_path, pid_path = daemon_paths
        result = start_daemon(socket_path=sock_path, pid_path=pid_path)
        assert result["started"] is True
        assert "pid" in result

        # Verify it responds to ping
        for _ in range(100):
            resp = send_request({"type": "ping"}, socket_path=sock_path)
            if resp is not None:
                break
            time.sleep(0.1)
        assert resp is not None
        assert resp["type"] == "pong"

        # Clean up
        stop_daemon(socket_path=sock_path, pid_path=pid_path)
        # Wait for process to exit
        for _ in range(50):
            if not os.path.exists(sock_path):
                break
            time.sleep(0.1)

    def test_start_daemon_already_running(self, daemon_paths):
        from agentcad.daemon import DaemonServer, send_request, start_daemon

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        # Try to start again — should report already running
        result = start_daemon(socket_path=sock_path, pid_path=pid_path)
        assert result["started"] is False
        assert "already" in result.get("message", "").lower()

        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)


# ---------- Phase 4: CLI commands ----------

class TestDaemonCLI:
    def test_daemon_status_cli_not_running(self, runner, monkeypatch):
        from agentcad.cli import cli

        monkeypatch.setattr("agentcad.commands.daemon_cmd._socket_path",
                            lambda: "/tmp/agentcad-test-nonexistent.sock")
        monkeypatch.setattr("agentcad.commands.daemon_cmd._pid_path",
                            lambda: "/tmp/agentcad-test-nonexistent.pid")
        result = runner.invoke(cli, ["daemon", "status"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["command"] == "daemon"
        assert output["status"] == "success"
        assert output["running"] is False

    def test_daemon_start_cli(self, runner, daemon_paths, monkeypatch):
        from agentcad.cli import cli
        from agentcad.daemon import stop_daemon

        sock_path, pid_path = daemon_paths
        monkeypatch.setattr("agentcad.commands.daemon_cmd._socket_path",
                            lambda: sock_path)
        monkeypatch.setattr("agentcad.commands.daemon_cmd._pid_path",
                            lambda: pid_path)

        result = runner.invoke(cli, ["daemon", "start"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["command"] == "daemon"
        assert output["status"] == "success"
        assert output["started"] is True

        # Clean up
        stop_daemon(socket_path=sock_path, pid_path=pid_path)
        for _ in range(50):
            if not os.path.exists(sock_path):
                break
            time.sleep(0.1)

    def test_daemon_stop_cli_not_running(self, runner, monkeypatch):
        from agentcad.cli import cli

        monkeypatch.setattr("agentcad.commands.daemon_cmd._socket_path",
                            lambda: "/tmp/agentcad-test-nonexistent.sock")
        monkeypatch.setattr("agentcad.commands.daemon_cmd._pid_path",
                            lambda: "/tmp/agentcad-test-nonexistent.pid")
        result = runner.invoke(cli, ["daemon", "stop"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["command"] == "daemon"
        assert output["status"] == "error"
        assert output["stopped"] is False

    def test_daemon_start_cli_fail_reports_error(self, runner, monkeypatch):
        """CLI reports status=error when daemon fails to start."""
        from agentcad.cli import cli

        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._socket_path",
            lambda: "/tmp/agentcad-test-nonexistent.sock",
        )
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._pid_path",
            lambda: "/tmp/agentcad-test-nonexistent.pid",
        )
        # Monkeypatch start_daemon to simulate failure
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd.start_daemon",
            lambda **kw: {"started": False, "message": "Daemon failed to start: ImportError"},
        )
        result = runner.invoke(cli, ["daemon", "start"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["command"] == "daemon"
        assert output["status"] == "error"
        assert output["started"] is False
        assert "ImportError" in output["message"]

    def test_daemon_start_cli_already_running_is_success(self, runner, monkeypatch):
        """CLI reports status=success when daemon is already running."""
        from agentcad.cli import cli

        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._socket_path",
            lambda: "/tmp/agentcad-test-nonexistent.sock",
        )
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._pid_path",
            lambda: "/tmp/agentcad-test-nonexistent.pid",
        )
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd.start_daemon",
            lambda **kw: {"started": False, "message": "Daemon already running", "pid": 12345},
        )
        result = runner.invoke(cli, ["daemon", "start"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["command"] == "daemon"
        assert output["status"] == "success"


# ---------- Phase 5: Run routing ----------

class TestRunRouting:
    def test_run_via_daemon_returns_same_output(self, isolated_dir, daemon_paths):
        """Run via daemon produces the same output as direct run."""
        from agentcad.cli import cli
        from agentcad.daemon import DaemonServer, send_request

        # Set up project
        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "test"])
        script = isolated_dir / "box.py"
        script.write_text("result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        # Start in-process daemon server
        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        # Send run request via daemon
        resp = send_request({
            "type": "run",
            "cwd": str(isolated_dir),
            "argv": ["run", str(script), "--output", "box"],
        }, socket_path=sock_path)

        assert resp is not None
        assert resp["exit_code"] == 0
        output = json.loads(resp["output"])
        assert output["status"] == "success"
        assert output["label"] == "box"
        assert "metrics" in output

        # Clean up
        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)

    def test_run_fallback_when_daemon_not_running(self, runner, isolated_dir):
        """agentcad run still works when daemon is not running."""
        from agentcad.cli import cli

        runner.invoke(cli, ["init", "--name", "test"])
        script = isolated_dir / "box.py"
        script.write_text("result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        result = runner.invoke(cli, ["run", str(script), "--output", "box"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"

    def test_run_routing_uses_daemon_when_available(self, isolated_dir, daemon_paths, monkeypatch):
        """agentcad run routes through daemon when it is running."""
        from agentcad.cli import cli
        from agentcad.daemon import DaemonServer, send_request

        sock_path, pid_path = daemon_paths

        # Set up project
        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "test"])
        script = isolated_dir / "box.py"
        script.write_text("result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        # Start in-process daemon
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        # Monkeypatch the socket path in run.py
        monkeypatch.setattr("agentcad.commands.run._daemon_socket_path",
                            lambda: sock_path)

        result = runner.invoke(cli, ["run", str(script), "--output", "box"])
        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output["status"] == "success"

        # Clean up
        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)

    def test_run_via_daemon_correct_cwd(self, isolated_dir, daemon_paths):
        """Daemon respects CWD in the request."""
        from agentcad.cli import cli
        from agentcad.daemon import DaemonServer, send_request

        # Create project in a subdirectory
        project_dir = isolated_dir / "proj"
        project_dir.mkdir()
        old_cwd = os.getcwd()
        os.chdir(project_dir)
        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "test"])
        os.chdir(old_cwd)

        script = project_dir / "box.py"
        script.write_text("result = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        resp = send_request({
            "type": "run",
            "cwd": str(project_dir),
            "argv": ["run", str(script), "--output", "box"],
        }, socket_path=sock_path)

        assert resp is not None
        assert resp["exit_code"] == 0
        # Version dir should be in project_dir
        assert (project_dir / "v1_box").exists()

        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)
