"""Persistent worker daemon for agentcad.

Keeps CadQuery/OCP loaded in memory so subsequent `agentcad run` invocations
skip the 3-5s cold import. IPC via Unix domain socket with length-prefixed JSON.
"""

import json
import os
import socket
import struct
import subprocess
import sys
import time


def _default_socket_path():
    return f"/tmp/agentcad-daemon-{os.getuid()}.sock"


def _default_pid_path():
    return f"/tmp/agentcad-daemon-{os.getuid()}.pid"


# ---------- Protocol ----------

def encode_message(msg):
    """Encode a dict as length-prefixed JSON (4-byte big-endian uint32 + UTF-8)."""
    payload = json.dumps(msg).encode("utf-8")
    return struct.pack("!I", len(payload)) + payload


def decode_message(data):
    """Decode a length-prefixed JSON message from bytes.

    Returns (message_dict, remaining_bytes) or (None, data) if incomplete.
    """
    if len(data) < 4:
        return None, data
    length = struct.unpack("!I", data[:4])[0]
    if len(data) < 4 + length:
        return None, data
    payload = data[4:4 + length]
    msg = json.loads(payload.decode("utf-8"))
    return msg, data[4 + length:]


# ---------- Server ----------

class DaemonServer:
    def __init__(self, socket_path=None, pid_path=None):
        self._socket_path = socket_path or _default_socket_path()
        self._pid_path = pid_path or _default_pid_path()
        self._running = False

    def handle_request(self, request):
        """Dispatch a request and return a response dict."""
        req_type = request.get("type")
        if req_type == "ping":
            return {"type": "pong"}
        elif req_type == "shutdown":
            self._running = False
            return {"type": "ack"}
        elif req_type == "run":
            return self._handle_run(request)
        else:
            return {"type": "error", "message": f"Unknown request type: {req_type}"}

    def _handle_run(self, request):
        """Execute a agentcad run command via CliRunner."""
        from click.testing import CliRunner
        from agentcad.cli import cli

        cwd = request.get("cwd")
        argv = request.get("argv", [])
        original_cwd = os.getcwd()
        # Set env var to prevent recursive daemon routing
        os.environ["AGENTCAD_DAEMON"] = "1"
        try:
            if cwd:
                os.chdir(cwd)
            runner = CliRunner()
            result = runner.invoke(cli, argv)
            return {
                "type": "result",
                "exit_code": result.exit_code,
                "output": result.output,
            }
        finally:
            os.chdir(original_cwd)
            os.environ.pop("AGENTCAD_DAEMON", None)

    def serve(self):
        """Start the server loop, listening on the Unix domain socket."""
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self._socket_path)
        server_sock.listen(1)
        server_sock.settimeout(1.0)  # Allow periodic check of _running

        # Write PID file
        with open(self._pid_path, "w") as f:
            f.write(str(os.getpid()))

        self._running = True
        try:
            while self._running:
                try:
                    conn, _ = server_sock.accept()
                except socket.timeout:
                    continue
                try:
                    buf = b""
                    while True:
                        chunk = conn.recv(65536)
                        if not chunk:
                            break
                        buf += chunk
                        request, _ = decode_message(buf)
                        if request is not None:
                            response = self.handle_request(request)
                            conn.sendall(encode_message(response))
                            break
                finally:
                    conn.close()
        finally:
            server_sock.close()
            if os.path.exists(self._socket_path):
                os.unlink(self._socket_path)
            if os.path.exists(self._pid_path):
                os.unlink(self._pid_path)


# ---------- Client ----------

def send_request(msg, socket_path=None):
    """Send a request to the daemon and return the response, or None if unavailable."""
    if socket_path is None:
        socket_path = _default_socket_path()

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(30)
        sock.connect(socket_path)
    except (FileNotFoundError, ConnectionRefusedError, OSError):
        return None

    try:
        sock.sendall(encode_message(msg))
        # Read response
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            response, _ = decode_message(buf)
            if response is not None:
                return response
        return None
    except (ConnectionError, OSError):
        return None
    finally:
        sock.close()


# ---------- Lifecycle ----------

def _pid_alive(pid):
    """Check if a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def daemon_status(socket_path=None, pid_path=None):
    """Check if the daemon is running. Returns dict with running, pid."""
    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()

    if not os.path.exists(pid_path):
        return {"running": False}

    try:
        pid = int(open(pid_path).read().strip())
    except (ValueError, OSError):
        return {"running": False}

    if not _pid_alive(pid):
        # Stale PID — clean up
        for p in (pid_path, socket_path):
            if os.path.exists(p):
                os.unlink(p)
        return {"running": False}

    return {"running": True, "pid": pid}


def stop_daemon(socket_path=None, pid_path=None):
    """Send shutdown to the daemon. Returns dict with stopped status."""
    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()

    resp = send_request({"type": "shutdown"}, socket_path=socket_path)
    if resp is not None and resp.get("type") == "ack":
        # Wait for socket to disappear
        for _ in range(50):
            if not os.path.exists(socket_path):
                break
            time.sleep(0.1)
        return {"stopped": True}
    return {"stopped": False}


def start_daemon(socket_path=None, pid_path=None):
    """Start the daemon as a subprocess. Returns dict with started, pid."""
    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()

    # Check if already running
    status = daemon_status(socket_path=socket_path, pid_path=pid_path)
    if status["running"]:
        return {"started": False, "message": "Daemon already running",
                "pid": status["pid"]}

    # Clean up stale files
    for p in (socket_path, pid_path):
        if os.path.exists(p):
            os.unlink(p)

    # Launch daemon subprocess
    proc = subprocess.Popen(
        [sys.executable, "-m", "agentcad.daemon",
         "--socket", socket_path, "--pid", pid_path],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # Wait for socket to appear (up to 30s — cold CadQuery/OCP import can take 15-20s)
    for _ in range(300):
        if os.path.exists(socket_path):
            break
        # If process already exited, no point waiting
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    else:
        return {"started": True, "pid": proc.pid}

    # If we broke out of the loop, check if it was success or failure
    if os.path.exists(socket_path):
        return {"started": True, "pid": proc.pid}

    # Process crashed — capture stderr for diagnostics
    stderr = ""
    try:
        stderr = proc.stderr.read().decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    msg = "Daemon failed to start"
    if stderr:
        # Take the last line as the most relevant error
        last_line = stderr.strip().splitlines()[-1]
        msg = f"Daemon failed to start: {last_line}"
    return {"started": False, "message": msg}


# ---------- __main__ ----------

def _main():
    """Entry point for daemon subprocess."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=None)
    parser.add_argument("--pid", default=None)
    args = parser.parse_args()

    # Eager imports — warm up all expensive modules
    import cadquery  # noqa: F401
    from cadquery import cqgi, exporters  # noqa: F401
    from agentcad import helpers, metrics, render, export  # noqa: F401

    server = DaemonServer(
        socket_path=args.socket,
        pid_path=args.pid,
    )
    server.serve()


if __name__ == "__main__":
    _main()
