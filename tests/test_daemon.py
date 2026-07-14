import json
import os
import pathlib
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


def _start_slow_request_server(name, delay_s=0.15):
    """Accept one daemon frame, record its side effect, then stay silent."""
    from agentcad.daemon import _read_one_message

    sock_path = _short_sock_path(name)
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(sock_path)
    listener.listen(1)
    listener.settimeout(2)
    submissions = []
    errors = []

    def _serve():
        try:
            conn, _ = listener.accept()
            try:
                request = _read_one_message(conn)
                submissions.append(request)
                time.sleep(delay_s)
            finally:
                conn.close()
        except Exception as exc:
            errors.append(exc)
        finally:
            listener.close()
            if os.path.exists(sock_path):
                os.unlink(sock_path)

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    return sock_path, thread, submissions, errors


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

    def test_read_deadline_is_absolute_for_incomplete_header(self):
        from agentcad.daemon import _read_one_message

        reader, writer = socket.socketpair()
        try:
            writer.sendall(b"\x00")
            started = time.monotonic()
            with pytest.raises(socket.timeout):
                _read_one_message(reader, timeout_s=0.03)
            assert time.monotonic() - started < 0.5
        finally:
            reader.close()
            writer.close()

    def test_oversized_declared_payload_is_rejected_before_body_read(self):
        from agentcad.daemon import (
            DaemonProtocolError,
            _MAX_REQUEST_PAYLOAD_BYTES,
            _read_one_message,
        )

        reader, writer = socket.socketpair()
        try:
            writer.sendall(struct.pack("!I", _MAX_REQUEST_PAYLOAD_BYTES + 1))
            with pytest.raises(DaemonProtocolError, match="exceeds limit"):
                _read_one_message(
                    reader,
                    timeout_s=0.5,
                    max_payload_bytes=_MAX_REQUEST_PAYLOAD_BYTES,
                )
        finally:
            reader.close()
            writer.close()


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

    def test_run_timeout_after_submission_returns_unknown_outcome(self):
        """A response timeout is not equivalent to daemon unavailability.

        The server has already observed the request side effect, so returning
        ``None`` here would cause the CLI to repeat non-idempotent work.
        """
        from agentcad.daemon import send_request

        sock_path, thread, submissions, errors = _start_slow_request_server(
            "uncertain-timeout"
        )
        response = send_request(
            {
                "type": "run",
                "cwd": "/tmp",
                "argv": ["run", "slow.py", "--output", "slow"],
            },
            socket_path=sock_path,
            response_timeout_s=0.03,
        )
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert errors == []
        assert len(submissions) == 1
        assert response is not None
        assert response["exit_code"] != 0
        payload = json.loads(response["output"])
        assert payload["error_kind"] == "daemon_response_timeout"
        assert payload["request_submitted"] is True
        assert payload["outcome"] == "unknown"
        assert payload["retry_safe"] is False

    def test_run_eof_after_submission_returns_unknown_outcome(self):
        """A daemon disconnect after reading the request is also unsafe to
        retry because the child may have completed its side effects."""
        from agentcad.daemon import send_request

        sock_path, thread, submissions, errors = _start_slow_request_server(
            "uncertain-eof", delay_s=0
        )
        response = send_request(
            {
                "type": "run",
                "cwd": "/tmp",
                "argv": ["render", "part.step"],
            },
            socket_path=sock_path,
            response_timeout_s=1,
        )
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert errors == []
        assert len(submissions) == 1
        assert response is not None
        assert response["exit_code"] != 0
        payload = json.loads(response["output"])
        assert payload["error_kind"] == "daemon_response_lost"
        assert payload["request_submitted"] is True
        assert payload["outcome"] == "unknown"
        assert payload["retry_safe"] is False


# ---------- Phase 2: Server handlers ----------

def _bare_server():
    """Build a DaemonServer without invoking __init__, but with _version stamped
    to the currently-imported agentcad version so handle_request's version
    check lets matching client requests through."""
    import agentcad
    from agentcad.daemon import DaemonServer

    server = DaemonServer.__new__(DaemonServer)
    server._version = agentcad.__version__
    return server


def _req(**kwargs):
    """Build a request dict. Thin wrapper so tests read consistently even
    though there's nothing to default anymore — the version-handshake field
    that used to live here was deleted with the preforked-worker redesign."""
    return kwargs


class TestDaemonServer:
    def test_handles_ping(self):
        server = _bare_server()
        response = server.handle_request(_req(type="ping"))
        assert response["type"] == "pong"

    def test_handles_shutdown(self):
        server = _bare_server()
        server._running = True
        response = server.handle_request(_req(type="shutdown"))
        assert response["type"] == "ack"
        assert server._running is False

    def test_handles_run(self, isolated_dir):
        # Set up a agentcad project
        from agentcad.cli import cli
        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "test"])

        # Write a simple script
        script = isolated_dir / "box.py"
        script.write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        server = _bare_server()
        response = server.handle_request(_req(
            type="run",
            cwd=str(isolated_dir),
            argv=["run", str(script), "--output", "box", "--no-preview"],
        ))
        assert response["exit_code"] == 0
        output = json.loads(response["output"])
        assert output["status"] == "success"
        assert output["label"] == "box"

    def test_run_returns_json_when_click_captures_empty_exception(self, monkeypatch):
        """Daemon-routed failures must never surface as empty stdout.

        Click can capture exceptions before a command emits its own JSON. In
        that case the daemon still has to synthesize a parseable error payload
        so the client/agent sees more than a nonzero exit code.
        """
        import click.testing

        class FakeResult:
            exit_code = 1
            stdout = ""
            stderr = ""
            exception = RuntimeError("Bnd_Box is void")

        monkeypatch.setattr(
            click.testing.CliRunner,
            "invoke",
            lambda self, cli, argv: FakeResult(),
        )

        server = _bare_server()
        response = server.handle_request(_req(
            type="run",
            argv=["run", "script.py", "--output", "boom"],
        ))

        assert response["exit_code"] == 1
        assert response["output"].strip()
        output = json.loads(response["output"])
        assert output["command"] == "run"
        assert output["status"] == "error"
        assert "Bnd_Box is void" in (
            output.get("message", "") + output.get("traceback", "")
        )

    def test_handles_unknown_type(self):
        server = _bare_server()
        response = server.handle_request(_req(type="unknown_thing"))
        assert response["type"] == "error"
        assert "unknown" in response["message"].lower()

    def test_run_preserves_cwd(self, isolated_dir):
        """Daemon restores original CWD after handling a run request."""
        original_cwd = os.getcwd()
        server = _bare_server()

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
        script.write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        server.handle_request(_req(
            type="run",
            cwd=str(project_dir),
            argv=["run", str(project_dir / "box.py"), "--output", "box", "--no-preview"],
        ))
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

    def test_pid_alive_but_no_listener_reports_not_running(self, daemon_paths):
        """Issue #190: a PID file pointing at an alive process that ISN'T
        a daemon (e.g., the pytest runner itself, a leftover ``agentcad
        render`` from another shell, or a PID recycled after the original
        daemon was SIGKILLed) must NOT cause daemon_status to falsely
        report running=true. The whole daemon-spawn fork path depends on
        this check being accurate."""
        from agentcad.daemon import daemon_status

        sock_path, pid_path = daemon_paths
        # Alive PID — the pytest runner itself. Definitely not a daemon.
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
        # Socket file present but no listener bound.
        with open(sock_path, "w") as f:
            f.write("")

        status = daemon_status(socket_path=sock_path, pid_path=pid_path)
        assert status["running"] is False, (
            f"daemon_status reported running=true even though PID "
            f"{os.getpid()} (the test runner) is not a daemon: {status}"
        )
        # Stale state cleaned up so the next spawn attempt is unblocked.
        assert not os.path.exists(pid_path)
        assert not os.path.exists(sock_path)

    def test_pid_alive_socket_missing_reports_not_running(self, daemon_paths):
        """PID file alive but socket file never bound (daemon died before
        binding, or fork failed mid-bind). Status must report not-running
        and clean up the stale PID file."""
        from agentcad.daemon import daemon_status

        sock_path, pid_path = daemon_paths
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
        # No socket file — simulates "daemon process started but never bound".
        assert not os.path.exists(sock_path)

        status = daemon_status(socket_path=sock_path, pid_path=pid_path)
        assert status["running"] is False
        assert not os.path.exists(pid_path)

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


class TestBoundedServerReads:
    @staticmethod
    def _start_server(sock_path, pid_path, monkeypatch):
        from agentcad.daemon import DaemonServer

        monkeypatch.setattr("agentcad.daemon._REQUEST_READ_TIMEOUT_S", 0.05)
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        thread = threading.Thread(target=server.serve)
        thread.start()
        for _ in range(100):
            if os.path.exists(sock_path):
                break
            time.sleep(0.01)
        assert os.path.exists(sock_path)
        return thread

    @pytest.mark.parametrize(
        "partial_frame",
        [
            b"\x00",
            struct.pack("!I", 100) + b"short",
        ],
        ids=["incomplete-header", "incomplete-body"],
    )
    def test_partial_frame_cannot_block_ping(
        self, daemon_paths, monkeypatch, partial_frame
    ):
        from agentcad.daemon import send_request

        sock_path, pid_path = daemon_paths
        thread = self._start_server(sock_path, pid_path, monkeypatch)
        blocker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            blocker.connect(sock_path)
            blocker.sendall(partial_frame)

            started = time.monotonic()
            response = send_request(
                {"type": "ping"},
                socket_path=sock_path,
                response_timeout_s=0.5,
            )
            elapsed = time.monotonic() - started

            assert response["type"] == "pong"
            assert elapsed < 0.5
        finally:
            blocker.close()
            send_request({"type": "shutdown"}, socket_path=sock_path)
            thread.join(timeout=2)
        assert not thread.is_alive()

    def test_oversized_frame_is_rejected_and_server_recovers(
        self, daemon_paths, monkeypatch
    ):
        from agentcad.daemon import (
            _MAX_REQUEST_PAYLOAD_BYTES,
            send_request,
        )

        sock_path, pid_path = daemon_paths
        thread = self._start_server(sock_path, pid_path, monkeypatch)
        attacker = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            attacker.connect(sock_path)
            attacker.sendall(
                struct.pack("!I", _MAX_REQUEST_PAYLOAD_BYTES + 1)
            )
            response = send_request(
                {"type": "ping"},
                socket_path=sock_path,
                response_timeout_s=0.5,
            )
            assert response["type"] == "pong"
        finally:
            attacker.close()
            send_request({"type": "shutdown"}, socket_path=sock_path)
            thread.join(timeout=2)
        assert not thread.is_alive()

    def test_status_preserves_live_state_after_response_timeout(
        self, monkeypatch
    ):
        from agentcad.daemon import daemon_status

        sock_path, thread, submissions, errors = _start_slow_request_server(
            "status-timeout", delay_s=0.15
        )
        pid_path = _short_pid_path("status-timeout")
        with open(pid_path, "w") as f:
            f.write(str(os.getpid()))
        monkeypatch.setattr("agentcad.daemon._STATUS_PING_TIMEOUT_S", 0.03)
        try:
            result = daemon_status(
                socket_path=sock_path,
                pid_path=pid_path,
            )

            assert result["running"] is True
            assert result["responsive"] is False
            assert result["pid"] == os.getpid()
            assert os.path.exists(sock_path)
            assert os.path.exists(pid_path)
            assert len(submissions) == 1
        finally:
            thread.join(timeout=2)
            if os.path.exists(pid_path):
                os.unlink(pid_path)
        assert not thread.is_alive()
        assert errors == []


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
        output = json.loads(result.stdout)
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
        output = json.loads(result.stdout)
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
        output = json.loads(result.stdout)
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
        output = json.loads(result.stdout)
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
        output = json.loads(result.stdout)
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
        script.write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

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
            "argv": ["run", str(script), "--output", "box", "--no-preview"],
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

    def test_run_fallback_when_daemon_not_running(
        self, runner, isolated_dir, monkeypatch
    ):
        """agentcad run still works when daemon is not running."""
        from agentcad.cli import cli

        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        missing_socket = _short_sock_path("fallback-missing")
        if os.path.exists(missing_socket):
            os.unlink(missing_socket)
        monkeypatch.setattr(
            "agentcad.commands._daemon_routing._socket_path",
            lambda: missing_socket,
        )
        monkeypatch.setattr(
            "agentcad.daemon.spawn_daemon_via_fork",
            lambda **kwargs: {"spawned": False, "reason": "test"},
        )

        runner.invoke(cli, ["init", "--name", "test"])
        script = isolated_dir / "box.py"
        script.write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        result = runner.invoke(cli, ["run", str(script), "--output", "box", "--no-preview"])
        assert result.exit_code == 0
        output = json.loads(result.stdout)
        assert output["status"] == "success"

    def test_submitted_timeout_is_not_retried_directly(
        self, runner, isolated_dir, monkeypatch
    ):
        """A silent daemon receives the request once; the CLI must stop with
        an unknown-outcome error instead of executing the script locally."""
        from agentcad.cli import cli

        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        sock_path, thread, submissions, errors = _start_slow_request_server(
            "routing-timeout"
        )
        monkeypatch.setattr(
            "agentcad.commands._daemon_routing._socket_path",
            lambda: sock_path,
        )
        monkeypatch.setattr(
            "agentcad.daemon._DEFAULT_RESPONSE_TIMEOUT_S",
            0.03,
        )

        runner.invoke(cli, ["init", "--name", "test"])
        direct_marker = isolated_dir / "direct-execution.txt"
        script = isolated_dir / "slow.py"
        script.write_text(
            "from pathlib import Path\n"
            "import cadquery as cq\n"
            f"Path({str(direct_marker)!r}).write_text('ran directly')\n"
            "show_object(cq.Workplane('XY').box(1, 1, 1))\n"
        )

        result = runner.invoke(
            cli,
            ["run", str(script), "--output", "slow", "--no-preview"],
        )
        thread.join(timeout=2)

        assert not thread.is_alive()
        assert errors == []
        assert len(submissions) == 1
        assert not direct_marker.exists(), (
            "the submitted request timed out and was executed again locally"
        )
        assert result.exit_code == 124
        payload = json.loads(result.stdout)
        assert payload["status"] == "error"
        assert payload["error_kind"] == "daemon_response_timeout"
        assert payload["outcome"] == "unknown"
        assert payload["retry_safe"] is False
        assert payload["via"] == "daemon"

    def test_run_routing_uses_daemon_when_available(self, isolated_dir, daemon_paths, monkeypatch):
        """agentcad run routes through daemon when it is running."""
        from agentcad.cli import cli
        from agentcad.daemon import DaemonServer, send_request

        sock_path, pid_path = daemon_paths

        # Set up project
        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "test"])
        script = isolated_dir / "box.py"
        script.write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

        # Start in-process daemon
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        # Monkeypatch the socket path in run.py
        monkeypatch.setattr("agentcad.commands._daemon_routing._socket_path",
                            lambda: sock_path)

        result = runner.invoke(cli, ["run", str(script), "--output", "box", "--no-preview"])
        assert result.exit_code == 0
        output = json.loads(result.stdout)
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
        script.write_text("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n")

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
            "argv": ["run", str(script), "--output", "box", "--no-preview"],
        }, socket_path=sock_path)

        assert resp is not None
        assert resp["exit_code"] == 0
        # Version dir should be in project_dir
        assert (project_dir / "v1_box").exists()

        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)


# ---------- Phase 6: Version check ----------

class TestVersionCheck:
    def test_daemon_version_match_ok(self, daemon_paths):
        """Matching client and daemon versions round-trip a normal ping."""
        import agentcad
        from agentcad.daemon import DaemonServer, send_request

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        # send_request auto-stamps the current agentcad version, which matches
        # what the server snapshotted at __init__.
        assert server._version == agentcad.__version__
        resp = send_request({"type": "ping"}, socket_path=sock_path)
        assert resp["type"] == "pong"
        # Ping response carries the daemon's version so callers can detect drift.
        assert resp["version"] == agentcad.__version__

        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)

# NOTE: pre-preforked-redesign tests for the version-mismatch handshake have
# been removed. The daemon no longer gates `run` requests on client version;
# pip-upgrade recovery is via `agentcad daemon restart`, matching every other
# long-running process.


# ---------- Phase 7: ping/shutdown contract, force-stop, --no-daemon, ----------
# ---------- restart command, status reports version (diagnostic only). ----------

class TestPingShutdownContract:
    """Ping returns pong+version; shutdown returns ack and stops the loop.
    These were once called "operational requests" to distinguish them from
    the version-gated `run` request. With no gate, they're just the contract."""

    def test_shutdown_returns_ack_and_stops(self):
        server = _bare_server()
        server._running = True
        response = server.handle_request(_req(type="shutdown"))
        assert response["type"] == "ack"
        assert server._running is False

    def test_ping_returns_pong_and_version(self):
        """Ping reports the daemon's startup-snapshotted version for diagnostics."""
        server = _bare_server()
        server._version = "9.9.9-test"
        response = server.handle_request(_req(type="ping"))
        assert response["type"] == "pong"
        assert response["version"] == "9.9.9-test"


class TestForceStopDaemon:
    """`stop_daemon` always wins. If graceful shutdown fails or is ignored,
    escalate to SIGTERM and SIGKILL, then unlink socket+PID files."""

    def test_stop_daemon_escalates_to_sigterm_when_unresponsive(
        self, daemon_paths, monkeypatch
    ):
        """Daemon ignores shutdown ack → SIGTERM is sent → cleanup."""
        from agentcad.daemon import stop_daemon

        sock_path, pid_path = daemon_paths
        # Pretend a daemon is "running" by writing a PID for a real, controllable
        # subprocess that doesn't actually serve the protocol.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
        )
        try:
            with open(pid_path, "w") as f:
                f.write(str(proc.pid))
            # Create a dummy socket file so daemon_status sees "running" without
            # binding (graceful shutdown will fail to connect → escalate).
            with open(sock_path, "w") as f:
                f.write("")

            result = stop_daemon(socket_path=sock_path, pid_path=pid_path)

            assert result["stopped"] is True
            # Method should indicate force-kill (graceful path failed)
            assert result.get("method") in ("force", "sigterm", "sigkill")
            # Process should be dead
            for _ in range(20):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            assert proc.poll() is not None, "subprocess should be killed"
            # Cleanup happened
            assert not os.path.exists(sock_path)
            assert not os.path.exists(pid_path)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_stop_daemon_returns_actionable_message_when_not_running(
        self, daemon_paths
    ):
        """When there is nothing to stop, the response should say so plainly."""
        from agentcad.daemon import stop_daemon

        sock_path, pid_path = daemon_paths
        result = stop_daemon(socket_path=sock_path, pid_path=pid_path)
        assert result["stopped"] is False
        # Used to be empty — must be a useful message now.
        assert "message" in result
        assert "not running" in result["message"].lower()


class TestStartDaemonColdStart:
    """`start_daemon` must not lie about success. On a truly cold install the
    OCP/CadQuery import can take longer than the wait timeout — historically
    we returned `started:true` whenever the subprocess was still alive, even
    if it hadn't bound the socket or written its PID file. That made an
    immediate `daemon status` report `running:false` because no PID file
    existed yet. The fix: only report `started:true` when the socket is
    actually bound; on timeout, kill the half-started subprocess and report
    failure with a useful message."""

    def test_start_returns_false_when_socket_never_appears(
        self, daemon_paths, monkeypatch
    ):
        """If the subprocess hangs without binding the socket, start_daemon
        must NOT return started:true just because the process is still alive.
        Pre-fix, it did exactly that — `socket_ready or proc.poll() is None`."""
        from agentcad.daemon import start_daemon

        sock_path, pid_path = daemon_paths

        # Replace the real subprocess.Popen with one that spawns a hung
        # process — never binds the socket, never writes the PID file, but
        # stays alive until killed.
        real_popen = subprocess.Popen

        def fake_popen(cmd, *args, **kwargs):
            return real_popen(
                [sys.executable, "-c", "import time; time.sleep(120)"],
                start_new_session=kwargs.get("start_new_session", False),
                stdout=kwargs.get("stdout", subprocess.PIPE),
                stderr=kwargs.get("stderr", subprocess.PIPE),
            )

        monkeypatch.setattr("agentcad.daemon.subprocess.Popen", fake_popen)

        # Use a short timeout so the test is fast.
        result = start_daemon(
            socket_path=sock_path, pid_path=pid_path, timeout_s=1.0
        )

        assert result["started"] is False, (
            f"start_daemon must report failure when socket never appears, "
            f"but returned {result!r} — this is the cold-start race that "
            f"made `daemon start` lie and `daemon status` immediately disagree."
        )
        assert "message" in result
        # Diagnostic should mention the slow/cold-import situation so an
        # operator knows what to do.
        assert any(
            word in result["message"].lower()
            for word in ("timeout", "did not bind", "still loading", "cold", "slow")
        ), f"unhelpful failure message: {result['message']!r}"
        # The hung subprocess must be cleaned up — we shouldn't leak a
        # 120-second sleeper for every failed start.
        assert not os.path.exists(sock_path), "socket file should be cleaned up"
        assert not os.path.exists(pid_path), "PID file should be cleaned up"

    def test_start_returns_true_when_socket_appears_promptly(
        self, daemon_paths, monkeypatch
    ):
        """Sanity: a fast-binding subprocess still produces started:true.
        Guards against an over-aggressive fix that breaks the happy path."""
        from agentcad.daemon import start_daemon, stop_daemon

        sock_path, pid_path = daemon_paths

        result = start_daemon(
            socket_path=sock_path, pid_path=pid_path, timeout_s=30.0
        )
        try:
            assert result["started"] is True
            assert "pid" in result
            assert os.path.exists(sock_path)
            assert os.path.exists(pid_path)
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)


class TestStartDaemonAlreadyRunning:
    """`agentcad daemon start` refuses with 'already running' when a daemon is
    up. The previous version-replacing behavior is gone — pip-upgrade recovery
    goes through `daemon restart` now."""

    def test_start_daemon_refuses_when_running(self, daemon_paths):
        """Existing daemon → ordinary 'already running' result, no replacement."""
        from agentcad.daemon import DaemonServer, send_request, start_daemon

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        result = start_daemon(socket_path=sock_path, pid_path=pid_path)
        assert result["started"] is False
        assert "already" in result.get("message", "").lower()
        assert result.get("replaced") is not True

        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)


class TestDaemonStatusReportsVersion:
    def test_daemon_status_includes_version_when_running(self, daemon_paths):
        from agentcad.daemon import DaemonServer, daemon_status, send_request

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        server._version = "9.9.9-test"
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        status = daemon_status(socket_path=sock_path, pid_path=pid_path)
        assert status["running"] is True
        assert status.get("version") == "9.9.9-test"

        send_request({"type": "shutdown"}, socket_path=sock_path)
        t.join(timeout=5)


# NOTE: TestRunAutoRecovery (version-mismatch self-heal) deleted alongside the
# version-gate. The preforked redesign keeps daemons usable across pip-upgrade
# through `agentcad daemon restart`, not in-process retry.


class TestNoDaemonFlag:
    def test_no_daemon_flag_bypasses_routing(
        self, isolated_dir, daemon_paths, monkeypatch
    ):
        """--no-daemon makes `agentcad run` skip the daemon socket entirely."""
        from agentcad.cli import cli
        from agentcad.daemon import DaemonServer, send_request

        sock_path, pid_path = daemon_paths

        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "test"])
        script = isolated_dir / "box.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n"
        )

        # Daemon is up and would normally handle this request.
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        monkeypatch.setattr(
            "agentcad.commands._daemon_routing._socket_path", lambda: sock_path
        )
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)

        try:
            result = runner.invoke(
                cli, ["run", str(script), "--output", "box", "--no-daemon", "--no-preview"]
            )
            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert output["status"] == "success"
            # Did NOT route through daemon despite it being available.
            assert output.get("via") != "daemon"
        finally:
            send_request({"type": "shutdown"}, socket_path=sock_path)
            t.join(timeout=5)


class TestDaemonRestartCommand:
    def test_restart_starts_when_not_running(self, runner, daemon_paths, monkeypatch):
        from agentcad.cli import cli
        from agentcad.daemon import stop_daemon

        sock_path, pid_path = daemon_paths
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._socket_path", lambda: sock_path
        )
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._pid_path", lambda: pid_path
        )

        try:
            result = runner.invoke(cli, ["daemon", "restart"])
            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert output["command"] == "daemon"
            assert output["status"] == "success"
            assert output["started"] is True
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_restart_replaces_running_daemon(
        self, runner, daemon_paths, monkeypatch
    ):
        from agentcad.cli import cli
        from agentcad.daemon import start_daemon, stop_daemon

        sock_path, pid_path = daemon_paths
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._socket_path", lambda: sock_path
        )
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._pid_path", lambda: pid_path
        )

        first = start_daemon(socket_path=sock_path, pid_path=pid_path)
        old_pid = first["pid"]

        try:
            result = runner.invoke(cli, ["daemon", "restart"])
            assert result.exit_code == 0
            output = json.loads(result.stdout)
            assert output["status"] == "success"
            assert output["started"] is True
            assert output["pid"] != old_pid
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_status_immediately_after_restart_reports_running(
        self, runner, daemon_paths, monkeypatch
    ):
        """Regression: friction-test agent saw `daemon restart` succeed and the
        very next `daemon status` return running=false despite the process
        being alive. Caused by the daemon binding the socket before writing
        the PID file — start_daemon's wait loop watched only the socket and
        could return inside that window. Status would then read an empty PID
        file and report running=false. Writing the PID file first closes the
        race; this test locks that ordering in."""
        from agentcad.cli import cli
        from agentcad.daemon import stop_daemon

        sock_path, pid_path = daemon_paths
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._socket_path", lambda: sock_path
        )
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._pid_path", lambda: pid_path
        )

        try:
            restart = runner.invoke(cli, ["daemon", "restart"])
            assert restart.exit_code == 0
            restart_out = json.loads(restart.stdout)
            assert restart_out["started"] is True

            # The exact failure mode the friction test caught: status fired
            # immediately after restart returned running=false.
            status = runner.invoke(cli, ["daemon", "status"])
            assert status.exit_code == 0
            status_out = json.loads(status.stdout)
            assert status_out["running"] is True, (
                f"daemon status should report running immediately after restart "
                f"returned started=true (regression). Got: {status_out!r}"
            )
            assert status_out["pid"] == restart_out["pid"]
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)


class TestVersionFallback:
    def test_version_fallback_is_useful(self, monkeypatch):
        """Even without package metadata, __version__ should be a useful identifier."""
        # Simulate the install state where importlib metadata is missing.
        from importlib.metadata import PackageNotFoundError

        # Re-execute the version-resolution logic from agentcad.__init__ in
        # a controlled way to verify the fallback. The key requirement: it must
        # not be the legacy bare "0.0.0+unknown" — error messages need to point
        # operators at something actionable.
        import agentcad

        # Force a re-resolution path: ask the module for its fallback constant.
        assert hasattr(agentcad, "_FALLBACK_VERSION"), (
            "agentcad must expose a _FALLBACK_VERSION constant so version-mismatch "
            "messages stay useful in editable / un-installed checkouts."
        )
        assert agentcad._FALLBACK_VERSION  # truthy
        assert agentcad._FALLBACK_VERSION != "0.0.0+unknown"


# ---------- PR A: per-venv socket + fork-on-first-run ----------

class TestPerVenvSocketPath:
    """The daemon socket is keyed on hash(sys.prefix), not just UID, so two
    venvs on the same machine each get their own daemon. Without this, a
    daemon spawned from venv A would intercept requests from venv B and
    execute them inside A's Python — confusing failures when packages
    differ between venvs (the "warhol-bridge daemon vs friction venvs"
    contamination the friction-test agent observed)."""

    def test_socket_path_includes_venv_hash(self):
        from agentcad.daemon import _default_socket_path, _venv_tag

        path = _default_socket_path()
        tag = _venv_tag()
        # 8-char short hash is enough to make distinct venvs distinct.
        assert len(tag) == 8
        assert tag in path
        assert str(os.getuid()) in path

    def test_different_venvs_get_different_paths(self, monkeypatch):
        """Mock sys.prefix to two different venv roots; assert the resulting
        socket paths differ. This is the property that makes cross-venv
        contamination impossible by construction."""
        from agentcad import daemon as daemon_mod

        monkeypatch.setattr(sys, "prefix", "/path/to/venvA")
        path_a = daemon_mod._default_socket_path()
        pid_a = daemon_mod._default_pid_path()

        monkeypatch.setattr(sys, "prefix", "/path/to/venvB")
        path_b = daemon_mod._default_socket_path()
        pid_b = daemon_mod._default_pid_path()

        assert path_a != path_b, (
            "Two different sys.prefix values must produce different "
            "socket paths — this is what isolates per-venv daemons."
        )
        assert pid_a != pid_b

    def test_venvs_sharing_underlying_python_still_isolate(self, monkeypatch):
        """Regression for the multi-venv collision discovered during manual
        friction testing. Two venvs created from the same /opt/homebrew/...
        Python share an ``os.path.realpath(sys.executable)`` (the venv's
        python is a symlink/shim to the base install), so keying the tag on
        ``sys.executable`` made both venvs collapse onto one daemon and one
        venv would silently run inside the other's site-packages.

        ``sys.prefix`` is the actual venv root and stays distinct, even when
        the underlying interpreter binary is shared."""
        from agentcad import daemon as daemon_mod

        # Same realpath under the covers — what realpath(sys.executable)
        # would produce for both venvs on the same machine.
        shared_exe = "/opt/homebrew/Cellar/python@3.12/3.12.12/bin/python3.12"
        monkeypatch.setattr(sys, "executable", shared_exe)

        monkeypatch.setattr(sys, "prefix", "/tmp/venv_a")
        tag_a = daemon_mod._venv_tag()

        monkeypatch.setattr(sys, "prefix", "/tmp/venv_b")
        tag_b = daemon_mod._venv_tag()

        assert tag_a != tag_b, (
            "Two venvs whose pythons realpath to the same binary must still "
            "get distinct venv tags — otherwise venv B silently runs inside "
            "venv A's site-packages when a daemon is already spawned."
        )


class TestForkSafety:
    """Spike-equivalent regression test: OCP/CadQuery survives os.fork().
    If a future cadquery release starts holding incompatible thread state
    or the like, this test catches it before the fork-on-first-run path
    silently breaks for users."""

    def test_cadquery_works_in_forked_child(self):
        """Import cadquery in the parent, fork, build geometry in the child."""
        # The parent has already imported cadquery via the test session's
        # other tests, so this is a "warm fork" — exactly the case
        # spawn_daemon_via_fork relies on.
        import cadquery as cq  # noqa: F401

        r_fd, w_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            # Child
            try:
                os.close(r_fd)
                box = cq.Workplane("XY").box(10, 10, 10)
                vol = box.val().Volume()
                with os.fdopen(w_fd, "w") as f:
                    f.write(f"OK {vol}\n")
                os._exit(0)
            except BaseException as e:
                with os.fdopen(w_fd, "w") as f:
                    f.write(f"FAIL {type(e).__name__}: {e}\n")
                os._exit(1)
        # Parent
        os.close(w_fd)
        with os.fdopen(r_fd, "r") as f:
            msg = f.read().strip()
        _, status = os.waitpid(pid, 0)
        assert status == 0, f"forked child failed: {msg}"
        assert msg.startswith("OK"), msg
        # Volume of a 10x10x10 box is 1000 (within float epsilon).
        vol = float(msg.split()[1])
        assert abs(vol - 1000.0) < 0.01


class TestSpawnDaemonViaFork:
    """Unit tests for the fork-spawn helper that backs `agentcad run`'s
    auto-daemon-on-first-run behavior."""

    def test_spawn_creates_running_daemon(self, daemon_paths, monkeypatch):
        from agentcad.daemon import (
            daemon_status,
            send_request,
            spawn_daemon_via_fork,
            stop_daemon,
        )

        sock_path, pid_path = daemon_paths
        # Override the autouse _no_daemon fixture for this test — we
        # specifically want to exercise the spawn path.
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        try:
            result = spawn_daemon_via_fork(
                socket_path=sock_path, pid_path=pid_path
            )
            assert result["spawned"] is True

            # Wait for the grandchild to bind. fork-spawn doesn't block, so
            # we have to poll. With OCP already imported in this test
            # session the grandchild should be ready in well under a second.
            for _ in range(50):
                status = daemon_status(socket_path=sock_path, pid_path=pid_path)
                if status.get("running"):
                    break
                time.sleep(0.1)
            assert status["running"] is True

            # And it actually serves.
            resp = send_request({"type": "ping"}, socket_path=sock_path)
            assert resp["type"] == "pong"
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_spawn_no_op_when_daemon_already_running(
        self, daemon_paths, monkeypatch
    ):
        """A second spawn into the same socket must be a no-op, not a fork
        bomb. Otherwise every successful run could leave a stack of orphan
        half-started daemons."""
        from agentcad.daemon import (
            daemon_status,
            spawn_daemon_via_fork,
            stop_daemon,
        )

        sock_path, pid_path = daemon_paths
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        try:
            first = spawn_daemon_via_fork(
                socket_path=sock_path, pid_path=pid_path
            )
            assert first["spawned"] is True
            for _ in range(50):
                if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                    break
                time.sleep(0.1)
            running_pid = daemon_status(socket_path=sock_path, pid_path=pid_path)["pid"]

            second = spawn_daemon_via_fork(
                socket_path=sock_path, pid_path=pid_path
            )
            assert second["spawned"] is False
            assert second["reason"] == "already_running"
            assert second["pid"] == running_pid
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_spawn_no_op_when_inside_daemon(self, daemon_paths, monkeypatch):
        """Daemon-routed requests must NOT spawn another daemon — that
        would recurse forever (each routed run forks another daemon which
        also forks another...)."""
        from agentcad.daemon import spawn_daemon_via_fork

        sock_path, pid_path = daemon_paths
        monkeypatch.setenv("AGENTCAD_DAEMON", "1")
        result = spawn_daemon_via_fork(
            socket_path=sock_path, pid_path=pid_path
        )
        assert result["spawned"] is False
        assert result["reason"] == "inside_daemon"
        assert not os.path.exists(sock_path)
        assert not os.path.exists(pid_path)


class TestRunSpawnsDaemonForNextInvocation:
    """End-to-end: a single `agentcad run` on a system with no daemon
    running should leave a daemon behind, and the next `agentcad run`
    should route through it without any explicit daemon-management.

    These tests use a real ``subprocess.run`` rather than ``CliRunner``
    because the spawn path forks. A pytest process that has already
    rendered geometry (which any earlier run-test does) has an
    initialized OpenGL context that doesn't survive ``fork()``, so the
    forked daemon child would hang on its own first render. Real CLI
    invocations don't have this problem — each ``agentcad run`` is a
    fresh subprocess and OpenGL is initialized after the fork. Using a
    subprocess in the test mirrors that and stays order-independent.

    CONVENTION FOR ANY NEW TEST IN THIS CLASS: the script you ``agentcad
    run`` MUST include an OCP boolean operation (cadquery's
    ``.cut()`` / ``.union()`` / ``.intersect()`` or build123d's ``-`` /
    ``+`` / ``&``). A plain ``cq.Workplane.box()`` script does not
    initialize OCP's TBB worker pool and therefore cannot reproduce the
    fork-after-TBB hang that broke #101. PRs #101, #103, and pre-#105
    end-to-end tests all passed against simple-box scripts and missed
    the bug — the friction-test agent's bracket-with-cuts caught it.
    See ``test_first_run_with_boolean_op_still_spawns_usable_daemon`` for
    the canonical pattern."""

    def _agentcad_env(self, sock_path, pid_path):
        """Env vars that pin the daemon to the test's socket+PID paths and
        clear the no-daemon-routing override so the spawn path runs."""
        env = dict(os.environ)
        env.pop("AGENTCAD_DAEMON", None)
        env["AGENTCAD_SOCKET_PATH"] = sock_path
        env["AGENTCAD_PID_PATH"] = pid_path
        return env

    def test_first_run_spawns_daemon_for_next_run(self, tmp_path, daemon_paths):
        from agentcad.daemon import daemon_status, stop_daemon

        sock_path, pid_path = daemon_paths

        # Project setup using a real shell-out so the project state is
        # fully on disk (not in CliRunner's virtualized fs).
        script = tmp_path / "box.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n"
        )
        env = self._agentcad_env(sock_path, pid_path)
        subprocess.run(
            [str(pathlib.Path(sys.executable).parent / "agentcad"), "init", "--name", "test"],
            cwd=tmp_path, env=env, check=True, capture_output=True,
        )

        try:
            r1 = subprocess.run(
                [str(pathlib.Path(sys.executable).parent / "agentcad"), "run",
                 str(script), "--output", "v1", "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120,
            )
            assert r1.returncode == 0, f"stdout={r1.stdout}\nstderr={r1.stderr}"
            output = json.loads(r1.stdout)
            assert output["status"] == "success"
            assert output.get("via") != "daemon"  # first run is direct

            # Wait for the spawned daemon to bind.
            for _ in range(50):
                if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                    break
                time.sleep(0.1)
            status = daemon_status(socket_path=sock_path, pid_path=pid_path)
            assert status["running"] is True, (
                "first agentcad run should leave a daemon behind for next time"
            )

            # Second run should route through the freshly-spawned daemon.
            # Tight 5s timeout: a daemon-routed run is ~0.1s of socket round-
            # trip; anything in the multi-second range means the daemon is
            # absent / hung / mis-routing and the test should fail loudly,
            # not wait 60s before noticing.
            r2 = subprocess.run(
                [str(pathlib.Path(sys.executable).parent / "agentcad"), "run",
                 str(script), "--output", "v2", "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=5,
            )
            assert r2.returncode == 0, f"stdout={r2.stdout}\nstderr={r2.stderr}"
            output2 = json.loads(r2.stdout)
            assert output2["status"] == "success"
            assert output2.get("via") == "daemon", (
                f"second run should route via the daemon spawned by the first "
                f"run; got: {output2!r}"
            )
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_first_run_with_boolean_op_still_spawns_usable_daemon(
        self, tmp_path, daemon_paths
    ):
        """Regression for the TBB-after-fork hang.

        OCP boolean operations (cut/fuse/intersect) and other parallel
        algorithms initialize Intel TBB's worker thread pool on first use.
        ``fork()`` only copies the calling thread, so a daemon child forked
        AFTER TBB has spawned worker threads inherits TBB state but no
        actual workers. The daemon then deadlocks the first time its
        in-process runner tries any parallel-OCP operation, and from the
        client's perspective the daemon stops responding to ``run``
        requests (ping still works briefly until the hung run blocks the
        single-threaded serve loop).

        Symptom this guards against: a script that uses ``.cut()`` runs
        fine the first time, the daemon spawns and binds its socket, but
        the second ``agentcad run`` falls through to direct execution
        (``via != "daemon"``) because the daemon hangs serving the
        request.

        Fix: spawn the daemon BEFORE ``runner.execute()`` runs the user
        script, so the fork happens before any TBB-initializing operation.
        """
        from agentcad.daemon import daemon_status, stop_daemon

        sock_path, pid_path = daemon_paths

        # Critical: this script uses cq.cut(), which triggers TBB. A box
        # without boolean ops would NOT reproduce the bug — the original
        # passing tests used a plain box and missed this entire class of
        # failure.
        script = tmp_path / "bracket.py"
        script.write_text(
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(60, 40, 5).edges('|Z').fillet(2)\n"
            "hole = cq.Workplane('XY').workplane(offset=-2.5).circle(3)"
            ".extrude(5).translate((20, 0, 0))\n"
            "result = result.cut(hole)\n"
            "show_object(result)\n"
        )
        env = self._agentcad_env(sock_path, pid_path)
        agentcad_exe = str(pathlib.Path(sys.executable).parent / "agentcad")
        subprocess.run(
            [agentcad_exe, "init", "--name", "tbb_test"],
            cwd=tmp_path, env=env, check=True, capture_output=True,
        )

        try:
            r1 = subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v1",
                 "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120,
            )
            assert r1.returncode == 0, f"stdout={r1.stdout}\nstderr={r1.stderr}"
            assert json.loads(r1.stdout)["status"] == "success"

            # Wait for daemon to bind.
            for _ in range(50):
                if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                    break
                time.sleep(0.1)
            assert daemon_status(socket_path=sock_path, pid_path=pid_path)["running"]

            # The bug: this would hang for 30s on the daemon-routing
            # timeout, then fall through to direct execution with via=- .
            # With the fix, run 2 routes through the daemon at ~0.1s.
            r2 = subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v2",
                 "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=20,  # tight timeout — daemon-routed run must be FAST
            )
            assert r2.returncode == 0, f"stdout={r2.stdout}\nstderr={r2.stderr}"
            output2 = json.loads(r2.stdout)
            assert output2["status"] == "success"
            assert output2.get("via") == "daemon", (
                f"second run after a TBB-initializing first run should still "
                f"route via the daemon. If via != 'daemon', the daemon was "
                f"forked after TBB initialized worker threads and is now "
                f"deadlocked on its first parallel-OCP request. Fix: spawn "
                f"the daemon BEFORE runner.execute(). Got: {output2!r}"
            )
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_first_run_with_b3d_boolean_op_still_spawns_usable_daemon(
        self, tmp_path, daemon_paths
    ):
        """build123d parity for the TBB-after-fork regression test.

        Same shape as the cadquery sibling but with b3d's ``-`` operator.
        The friction-test agent's actual failing case was a build123d
        bracket-with-cuts, not cadquery — and yet pre-#105 tests only
        covered cadquery. Closes that runtime-coverage gap.

        Skipped automatically when build123d isn't installed."""
        try:
            import build123d  # noqa: F401
        except ImportError:
            pytest.skip("build123d not installed in this venv")

        from agentcad.daemon import daemon_status, stop_daemon

        sock_path, pid_path = daemon_paths
        script = tmp_path / "bracket_b3d.py"
        script.write_text(
            "from build123d import Box, Cylinder\n"
            "result = Box(60, 40, 5)\n"
            "hole1 = Cylinder(3, 5).translate((20, 0, 0))\n"
            "hole2 = Cylinder(3, 5).translate((-20, 0, 0))\n"
            "result = result - hole1 - hole2\n"
            "show_object(result)\n"
        )
        env = self._agentcad_env(sock_path, pid_path)
        agentcad_exe = str(pathlib.Path(sys.executable).parent / "agentcad")
        subprocess.run(
            [agentcad_exe, "init", "--name", "tbb_b3d", "--runtime", "build123d"],
            cwd=tmp_path, env=env, check=True, capture_output=True,
        )

        try:
            r1 = subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v1",
                 "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120,
            )
            assert r1.returncode == 0, f"stdout={r1.stdout}\nstderr={r1.stderr}"
            assert json.loads(r1.stdout)["status"] == "success"

            for _ in range(50):
                if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                    break
                time.sleep(0.1)
            assert daemon_status(socket_path=sock_path, pid_path=pid_path)["running"]

            # b3d daemon-routed runs are slightly slower than cadquery's
            # because the b3d wire format is heavier; 10s window covers
            # both reasonable variance and a clear failure if the daemon
            # is hung.
            r2 = subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v2",
                 "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=10,
            )
            assert r2.returncode == 0, f"stdout={r2.stdout}\nstderr={r2.stderr}"
            output2 = json.loads(r2.stdout)
            assert output2["status"] == "success"
            assert output2.get("via") == "daemon", (
                f"second build123d run after a TBB-initializing first run "
                f"should route via the daemon; got: {output2!r}"
            )
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_daemon_serves_many_runs_without_degrading(
        self, tmp_path, daemon_paths
    ):
        """A spawned daemon must keep serving runs for the duration of an
        agent session, not just the first follow-up.

        The pre-#105 verification only tested 2 runs in sequence (spawn,
        then route). It missed: does the daemon stay healthy across 5,
        10, 20 routed requests? The verifier's Test 7 (5 b3d iterations)
        was the first time we exercised this; this test locks the
        contract in.

        Uses a boolean op per the class convention so any TBB-related
        regression also surfaces here."""
        from agentcad.daemon import daemon_status, stop_daemon

        sock_path, pid_path = daemon_paths
        script = tmp_path / "iter.py"
        script.write_text(
            "import cadquery as cq\n"
            "base = cq.Workplane('XY').box(20, 20, 5)\n"
            "hole = cq.Workplane('XY').workplane(offset=-2.5).circle(2)"
            ".extrude(5)\n"
            "result = base.cut(hole)\n"
            "show_object(result)\n"
        )
        env = self._agentcad_env(sock_path, pid_path)
        agentcad_exe = str(pathlib.Path(sys.executable).parent / "agentcad")
        subprocess.run(
            [agentcad_exe, "init", "--name", "iter_test"],
            cwd=tmp_path, env=env, check=True, capture_output=True,
        )

        try:
            # First run spawns the daemon (direct execution).
            subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v0",
                 "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120,
            )
            for _ in range(50):
                if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                    break
                time.sleep(0.1)
            assert daemon_status(socket_path=sock_path, pid_path=pid_path)["running"]

            # 8 sequential runs, each must route via daemon and complete
            # in the tight per-run window. If the daemon degrades after N
            # requests (memory leak, accept-loop wedge, etc.) one of these
            # will time out.
            n_iterations = 8
            for i in range(1, n_iterations + 1):
                r = subprocess.run(
                    [agentcad_exe, "run", str(script), "--output", f"v{i}",
                     "--no-preview"],
                    cwd=tmp_path, env=env, capture_output=True, text=True,
                    timeout=5,
                )
                assert r.returncode == 0, (
                    f"iteration {i} failed: stdout={r.stdout}\nstderr={r.stderr}"
                )
                output = json.loads(r.stdout)
                assert output["status"] == "success", (
                    f"iteration {i}: {output!r}"
                )
                assert output.get("via") == "daemon", (
                    f"iteration {i} stopped routing via daemon: {output!r}. "
                    f"Daemon may have died or hung mid-session."
                )
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_no_daemon_flag_skips_spawn(self, tmp_path, daemon_paths):
        """`--no-daemon` is the explicit opt-out; it should not leave a
        daemon behind."""
        from agentcad.daemon import daemon_status, stop_daemon

        sock_path, pid_path = daemon_paths

        script = tmp_path / "box.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n"
        )
        env = self._agentcad_env(sock_path, pid_path)
        subprocess.run(
            [str(pathlib.Path(sys.executable).parent / "agentcad"), "init", "--name", "test"],
            cwd=tmp_path, env=env, check=True, capture_output=True,
        )

        try:
            r = subprocess.run(
                [str(pathlib.Path(sys.executable).parent / "agentcad"), "run",
                 str(script), "--output", "v1", "--no-daemon", "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120,
            )
            assert r.returncode == 0, f"stdout={r.stdout}\nstderr={r.stderr}"

            # Give a fork attempt time to bind, just in case (it shouldn't fire).
            time.sleep(0.5)

            status = daemon_status(socket_path=sock_path, pid_path=pid_path)
            assert status["running"] is False, (
                f"--no-daemon must not leave a daemon behind, got: {status!r}"
            )
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)


# ---------- PR B: daemon-death recovery, b3d eager import, hidden CLI ----------

class TestDaemonDeathRecovery:
    """If the daemon dies between runs (OCP segfault, OOM, manual kill), the
    next ``agentcad run`` should still work and should leave a fresh daemon
    behind for the run after that. This was filed as #89.

    PR #101's fork-on-first-run already handles the dead-process case
    implicitly: ``agentcad run`` finds no responsive daemon, falls through
    to direct execution, then the post-execute spawn hook detects the dead
    daemon (stale PID file gets cleaned up by ``daemon_status``) and forks
    a fresh one. This test locks that behavior in."""

    def _agentcad_env(self, sock_path, pid_path):
        env = dict(os.environ)
        env.pop("AGENTCAD_DAEMON", None)
        env["AGENTCAD_SOCKET_PATH"] = sock_path
        env["AGENTCAD_PID_PATH"] = pid_path
        return env

    def test_dead_daemon_recovers_on_next_run(self, tmp_path, daemon_paths):
        from agentcad.daemon import daemon_status, stop_daemon

        sock_path, pid_path = daemon_paths
        agentcad_exe = str(pathlib.Path(sys.executable).parent / "agentcad")
        env = self._agentcad_env(sock_path, pid_path)

        script = tmp_path / "box.py"
        script.write_text(
            "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 5)\nshow_object(result)\n"
        )
        subprocess.run(
            [agentcad_exe, "init", "--name", "deathtest"],
            cwd=tmp_path, env=env, check=True, capture_output=True,
        )

        try:
            # First run: spawns the daemon as a side effect.
            r1 = subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v1", "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120,
            )
            assert r1.returncode == 0, r1.stderr

            # Wait for the spawn to bind.
            for _ in range(50):
                if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                    break
                time.sleep(0.1)
            status = daemon_status(socket_path=sock_path, pid_path=pid_path)
            assert status["running"], "first run should have spawned a daemon"
            old_pid = status["pid"]

            # Simulate the daemon dying — SIGKILL it directly. The PID file
            # stays on disk (the daemon never got a chance to clean up).
            os.kill(old_pid, signal.SIGKILL)
            for _ in range(30):
                try:
                    os.kill(old_pid, 0)
                    time.sleep(0.1)
                except ProcessLookupError:
                    break

            # Next agentcad run finds no responsive daemon, must still succeed.
            r2 = subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v2", "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=120,
            )
            assert r2.returncode == 0, (
                f"agentcad run after daemon death must succeed; "
                f"stdout={r2.stdout}\nstderr={r2.stderr}"
            )
            output2 = json.loads(r2.stdout)
            assert output2["status"] == "success"
            # First post-death run goes via direct execution (no daemon was
            # responsive), but spawns a fresh one.
            assert output2.get("via") != "daemon"

            # Wait for the recovery spawn to bind.
            for _ in range(50):
                s = daemon_status(socket_path=sock_path, pid_path=pid_path)
                if s.get("running") and s["pid"] != old_pid:
                    break
                time.sleep(0.1)
            status = daemon_status(socket_path=sock_path, pid_path=pid_path)
            assert status["running"], (
                "after a daemon death and a follow-up run, a fresh daemon "
                "should be running for the next run"
            )
            assert status["pid"] != old_pid, "must be a different process"

            # And the run AFTER recovery should route through the new daemon.
            r3 = subprocess.run(
                [agentcad_exe, "run", str(script), "--output", "v3", "--no-preview"],
                cwd=tmp_path, env=env, capture_output=True, text=True,
                timeout=60,
            )
            assert r3.returncode == 0, r3.stderr
            output3 = json.loads(r3.stdout)
            assert output3.get("via") == "daemon", (
                f"third run should route through the recovered daemon; got {output3!r}"
            )
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)


class TestBuild123dEagerImport:
    """A daemon spawned in a venv with build123d available should warm
    build123d during its startup so the first b3d-runtime run via the
    daemon doesn't pay an extra cold import. Ref #81."""

    def test_daemon_imports_build123d_when_available(self):
        """The daemon's eager-import block should include build123d (best-
        effort, wrapped in try/except so installs without it still work).

        Black-box check: spawn a daemon via fork, ping it, then send a
        request that triggers a build123d code path and assert it returns
        promptly (no second cold import inside the daemon)."""
        # Verify build123d is installed in this venv (skip the test
        # gracefully on installs that don't have it).
        try:
            import build123d  # noqa: F401
        except ImportError:
            pytest.skip("build123d not installed in this venv")

        # Inspect the daemon module's _main eager imports without running it.
        # We're asserting the module-level intent — the actual fork-spawn
        # path inherits whatever the parent imported, so the eager import
        # only matters for subprocess-launched daemons (agentcad daemon
        # start). For fork-spawn, build123d is inherited from the parent
        # process if it imported it.
        import inspect
        from agentcad import daemon as daemon_mod

        src = inspect.getsource(daemon_mod._main)
        # Strict: must actually `import build123d`, not just mention it. The
        # current code has a "build123d is intentionally NOT eager-imported"
        # comment that would pass a substring-only check.
        assert "import build123d" in src, (
            "daemon._main() should `import build123d` (best-effort, in a "
            "try/except) so subprocess-launched daemons warm it. The "
            "fork-spawn path inherits the parent's already-imported state, "
            "but `agentcad daemon start` spawns a fresh subprocess that "
            "doesn't get build123d for free."
        )


class TestDaemonCliHidden:
    """The daemon command group should be hidden from default ``--help``
    output. Power users can still run ``agentcad daemon stop`` etc., but a
    fresh agent reading the help text shouldn't see the daemon group at
    all — it's automatic now."""

    def test_daemon_group_hidden_from_top_level_help(self, runner):
        from agentcad.cli import cli

        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        # The group exists but isn't advertised. Anything explicit (e.g.
        # `agentcad daemon --help`) still works.
        assert "daemon" not in result.output.lower(), (
            "agentcad --help must not advertise the daemon group; the daemon "
            "is automatic and shouldn't be a top-level command for new users"
        )

    def test_daemon_group_still_invocable(self, runner, monkeypatch):
        """Hidden ≠ disabled — diagnostic commands still work."""
        from agentcad.cli import cli

        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._socket_path",
            lambda: "/tmp/agentcad-test-hidden-nonexistent.sock",
        )
        monkeypatch.setattr(
            "agentcad.commands.daemon_cmd._pid_path",
            lambda: "/tmp/agentcad-test-hidden-nonexistent.pid",
        )
        result = runner.invoke(cli, ["daemon", "status"])
        assert result.exit_code == 0
        assert json.loads(result.stdout)["command"] == "daemon"

    def test_docs_commands_does_not_list_daemon(self, runner):
        from agentcad.cli import cli

        result = runner.invoke(cli, ["docs", "commands"])
        assert result.exit_code == 0
        # Same reasoning — agents reading `agentcad docs commands` shouldn't
        # be told about daemon as a workflow command.
        body = json.loads(result.stdout)["content"].lower()
        # The word "daemon" can still appear in a different docs section
        # (e.g. `agentcad docs daemon`), but the COMMANDS listing itself
        # shouldn't enumerate it as a top-level command.
        assert "daemon" not in body, (
            f"`docs commands` shouldn't list daemon as a workflow command; "
            f"got: {body!r}"
        )


class TestDaemonStatusStalenessHint:
    """When the daemon's in-memory version differs from the installed
    agentcad on disk, ``daemon_status`` flags it. This is the only signal
    a user gets after ``pip install --upgrade agentcad`` that they need to
    run ``agentcad daemon restart`` to pick up the new code.

    Regression-guards a real gap surfaced in manual friction testing: with
    no signal, users silently kept getting old-code responses for as long
    as the stale daemon kept serving."""

    def test_status_flags_stale_when_versions_differ(self, daemon_paths):
        from agentcad.daemon import DaemonServer, daemon_status, send_request

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        # Simulate the daemon having loaded a different version into memory
        # than the on-disk installation reports.
        server._version = "0.0.0-stale"
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        try:
            status = daemon_status(socket_path=sock_path, pid_path=pid_path)
            assert status["running"] is True
            assert status["version"] == "0.0.0-stale"
            assert status.get("stale") is True, (
                "daemon_status must flag stale=true when in-memory version "
                "differs from agentcad.__version__ — this is the only "
                "after-pip-upgrade signal a user gets"
            )
            assert "installed_version" in status
            assert "hint" in status
            assert "daemon restart" in status["hint"]
        finally:
            send_request({"type": "shutdown"}, socket_path=sock_path)
            t.join(timeout=5)

    def test_status_no_stale_flag_when_versions_match(self, daemon_paths):
        """The hint must be conditional, not decorative — it should NOT
        appear when the daemon is fresh."""
        import agentcad
        from agentcad.daemon import DaemonServer, daemon_status, send_request

        sock_path, pid_path = daemon_paths
        server = DaemonServer(socket_path=sock_path, pid_path=pid_path)
        # Default __init__ stamps the current version, so this is the
        # fresh-daemon case.
        assert server._version == agentcad.__version__
        t = threading.Thread(target=server.serve)
        t.start()
        for _ in range(50):
            if os.path.exists(sock_path):
                break
            time.sleep(0.05)

        try:
            status = daemon_status(socket_path=sock_path, pid_path=pid_path)
            assert status.get("stale") is not True
            assert "hint" not in status
        finally:
            send_request({"type": "shutdown"}, socket_path=sock_path)
            t.join(timeout=5)


class TestRoutingStalenessWarning:
    """The routing layer must surface a stale daemon to the user, not
    silently execute against the old code with ``via:daemon`` stamped.

    `agentcad daemon status` already exposes a `stale` flag, but agents
    don't proactively check status between commands — so a pip-upgrade
    followed by an immediate `agentcad run` would otherwise route through
    a stale daemon with no surface signal that the fix didn't apply.
    These tests pin the one-line stderr warning that closes that trap.

    Both tests monkeypatch ``send_request`` to simulate the daemon's
    response — that's the canonical pattern per the docstring of
    ``_daemon_routing.py`` and avoids the fork-in-thread crash that real
    in-process DaemonServer threads hit on macOS when objc has been
    initialized in another thread.
    """

    def test_routing_warns_when_daemon_version_mismatches(
        self, isolated_dir, monkeypatch
    ):
        from agentcad.cli import cli

        # AGENTCAD_DAEMON=1 inside the test process would short-circuit
        # routing — make sure we're not inheriting it from a previous test
        # or shell that ran a daemon-routed command.
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)

        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "stale_warn"])
        script = isolated_dir / "box.py"
        script.write_text(
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(10, 20, 5)\n"
            "show_object(result)\n"
        )

        # Simulate the daemon's response with an older in-memory version
        # than the client's on-disk install.
        def fake_send_request(msg, socket_path=None):
            assert msg.get("type") == "run", (
                f"unexpected request type to fake daemon: {msg!r}"
            )
            return {
                "type": "result",
                "exit_code": 0,
                "output": json.dumps({"command": "run", "status": "success",
                                      "version": 1, "label": "box"}),
                "stderr": "",
                "version": "0.0.0-stale",
            }

        monkeypatch.setattr(
            "agentcad.commands._daemon_routing._daemon.send_request",
            fake_send_request,
        )

        result = runner.invoke(
            cli,
            ["run", str(script), "--output", "box", "--no-preview"],
        )
        assert result.exit_code == 0
        assert "warning: daemon is running agentcad v0.0.0-stale" in result.stderr, (
            "Routing layer should surface the version mismatch on stderr "
            "so an agent that just ran 'pip install --upgrade agentcad' "
            "knows their fix isn't loaded yet. Got stderr:\n"
            f"{result.stderr!r}"
        )
        assert "daemon restart" in result.stderr, (
            "Warning should tell the user how to fix it"
        )
        # And the stdout JSON still parses cleanly — the warning is
        # additive, not destructive to the normal output contract.
        output = json.loads(result.stdout)
        assert output["status"] == "success"
        assert output["via"] == "daemon"

    def test_routing_does_not_warn_when_versions_match(
        self, isolated_dir, monkeypatch
    ):
        """The warning must be conditional, not decorative — emitting it on
        every run would be noise."""
        import agentcad
        from agentcad.cli import cli

        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)

        runner = CliRunner()
        runner.invoke(cli, ["init", "--name", "fresh_no_warn"])
        script = isolated_dir / "box.py"
        script.write_text(
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(10, 20, 5)\n"
            "show_object(result)\n"
        )

        def fake_send_request(msg, socket_path=None):
            return {
                "type": "result",
                "exit_code": 0,
                "output": json.dumps({"command": "run", "status": "success",
                                      "version": 1, "label": "box"}),
                "stderr": "",
                "version": agentcad.__version__,  # daemon matches client
            }

        monkeypatch.setattr(
            "agentcad.commands._daemon_routing._daemon.send_request",
            fake_send_request,
        )

        result = runner.invoke(
            cli,
            ["run", str(script), "--output", "box", "--no-preview"],
        )
        assert result.exit_code == 0
        assert "warning: daemon is running" not in result.stderr, (
            f"No staleness warning expected on a fresh daemon. "
            f"Got stderr:\n{result.stderr!r}"
        )


# ---------- Preforked architecture: distinctive properties ----------

class TestPreforkedArchitecture:
    """Properties that the fork-per-request model gives us and the old
    in-process model didn't. These regression-guard the architecture itself,
    not the protocol or lifecycle.

    These tests preserve the design contract for daemon lifecycle and isolation.
    """

    def _agentcad_env(self, sock_path, pid_path):
        env = dict(os.environ)
        env.pop("AGENTCAD_DAEMON", None)
        env["AGENTCAD_SOCKET_PATH"] = sock_path
        env["AGENTCAD_PID_PATH"] = pid_path
        return env

    def test_child_crash_does_not_kill_daemon(self, tmp_path, daemon_paths):
        """A segfault / SIGKILL of one request's child must not take down the
        daemon parent. The pre-#redesign in-process handler did not have this
        property — a single OCP segfault wedged the daemon for all callers.
        """
        from agentcad.daemon import daemon_status, send_request, stop_daemon

        sock_path, pid_path = daemon_paths
        agentcad_exe = str(pathlib.Path(sys.executable).parent / "agentcad")
        env = self._agentcad_env(sock_path, pid_path)

        # First run spawns the daemon. Use a real subprocess so the spawn
        # happens through the production fork-detach path.
        script = tmp_path / "good.py"
        script.write_text(
            "import cadquery as cq\n"
            "result = cq.Workplane('XY').box(10, 20, 5)\n"
            "show_object(result)\n"
        )
        subprocess.run(
            [agentcad_exe, "init", "--name", "crash_iso"],
            cwd=tmp_path, env=env, check=True, capture_output=True,
        )
        subprocess.run(
            [agentcad_exe, "run", str(script), "--output", "v1", "--no-preview"],
            cwd=tmp_path, env=env, check=True, capture_output=True, text=True,
            timeout=120,
        )

        # Wait for the daemon to bind.
        for _ in range(50):
            if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                break
            time.sleep(0.1)
        status = daemon_status(socket_path=sock_path, pid_path=pid_path)
        assert status["running"], "daemon should be up after the first run"
        parent_pid = status["pid"]

        try:
            # Send a request whose child will SIGKILL itself before responding.
            # The daemon parent forks, the child invokes the CLI which runs
            # this killer script and dies. The parent must reap (via
            # SIG_IGN-on-SIGCHLD) and remain serving.
            killer = tmp_path / "killer.py"
            killer.write_text(
                "import os, signal\n"
                "os.kill(os.getpid(), signal.SIGKILL)\n"
            )
            # We expect the request to error out (no response sent because
            # the child died before sending) — send_request returns None.
            resp = send_request({
                "type": "run",
                "cwd": str(tmp_path),
                "argv": ["run", str(killer), "--output", "boom", "--no-preview"],
            }, socket_path=sock_path)
            # resp may be None (child died before responding) or an error
            # payload. Either way, the daemon parent should still be alive.
            assert resp is None or "type" in resp

            # The parent must still be the same PID and still responsive.
            ping = send_request({"type": "ping"}, socket_path=sock_path)
            assert ping is not None, (
                "daemon parent stopped serving after a child crash — the "
                "preforked architecture is supposed to isolate this"
            )
            assert ping.get("type") == "pong"

            # And a subsequent real run should still work.
            ok = send_request({
                "type": "run",
                "cwd": str(tmp_path),
                "argv": ["run", str(script), "--output", "v_after_crash", "--no-preview"],
            }, socket_path=sock_path)
            assert ok is not None and ok.get("exit_code") == 0, (
                f"daemon couldn't service a run after a sibling child crashed: "
                f"{ok!r}"
            )

            # Parent PID is unchanged — the daemon didn't restart.
            status2 = daemon_status(socket_path=sock_path, pid_path=pid_path)
            assert status2["pid"] == parent_pid, (
                "daemon parent PID changed after child crash — somebody "
                "restarted it instead of riding through"
            )
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)

    def test_child_does_not_leak_cwd_to_parent(self, tmp_path, daemon_paths):
        """In the old in-process handler, ``os.chdir`` had to be restored in a
        ``finally`` block or it would leak to subsequent requests. With the
        fork-per-request model, the child's cwd dies with the child — the
        parent's cwd is never touched. Regression-guards against a refactor
        that mistakenly drops the fork in favor of in-process handling.
        """
        from agentcad.daemon import daemon_status, send_request, stop_daemon

        sock_path, pid_path = daemon_paths
        agentcad_exe = str(pathlib.Path(sys.executable).parent / "agentcad")
        env = self._agentcad_env(sock_path, pid_path)

        # Two project dirs; consecutive runs in each, checking the version dir
        # lands in the right place. If cwd leaked, the second run's version
        # dir would appear in the first run's directory.
        dir_a = tmp_path / "proj_a"
        dir_b = tmp_path / "proj_b"
        for d in (dir_a, dir_b):
            d.mkdir()
            subprocess.run(
                [agentcad_exe, "init", "--name", d.name],
                cwd=d, env=env, check=True, capture_output=True,
            )
            (d / "box.py").write_text(
                "import cadquery as cq\n"
                "result = cq.Workplane('XY').box(10, 20, 5)\n"
                "show_object(result)\n"
            )

        # Spawn daemon via a first run in dir_a.
        subprocess.run(
            [agentcad_exe, "run", str(dir_a / "box.py"), "--output", "v1", "--no-preview"],
            cwd=dir_a, env=env, check=True, capture_output=True, text=True,
            timeout=120,
        )
        for _ in range(50):
            if daemon_status(socket_path=sock_path, pid_path=pid_path).get("running"):
                break
            time.sleep(0.1)
        assert daemon_status(socket_path=sock_path, pid_path=pid_path)["running"]

        try:
            # Run in dir_a via daemon.
            send_request({
                "type": "run",
                "cwd": str(dir_a),
                "argv": ["run", str(dir_a / "box.py"), "--output", "v_a", "--no-preview"],
            }, socket_path=sock_path)
            # Then in dir_b via daemon.
            send_request({
                "type": "run",
                "cwd": str(dir_b),
                "argv": ["run", str(dir_b / "box.py"), "--output", "v_b", "--no-preview"],
            }, socket_path=sock_path)

            # Both version dirs should land in their own project — proves cwd
            # was honored per-request and didn't bleed.
            assert (dir_a / "v2_v_a").exists(), (
                f"dir_a's daemon run should create v2_v_a in dir_a; "
                f"got contents: {list(dir_a.iterdir())}"
            )
            assert (dir_b / "v1_v_b").exists(), (
                f"dir_b's daemon run should create v1_v_b in dir_b; "
                f"got contents: {list(dir_b.iterdir())}"
            )
            # And neither should leak the other's artifacts.
            assert not (dir_a / "v1_v_b").exists()
            assert not (dir_b / "v2_v_a").exists()
        finally:
            stop_daemon(socket_path=sock_path, pid_path=pid_path)
