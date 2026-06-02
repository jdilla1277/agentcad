"""Persistent worker daemon for agentcad.

Architecture: a long-lived parent process keeps CadQuery/OCP loaded in memory.
For each ``run`` request, the parent ``fork()``s a child that handles the
single request and exits. Operational requests (``ping``, ``shutdown``) are
answered by the parent directly.

Why preforked-per-request rather than handle-in-process:
  - per-request crash isolation: a segfault in OCP only kills that one child;
    the parent stays serving.
  - no module-state accumulation: ``sys.modules``, ``os.chdir``, env vars are
    born fresh in every child.
  - parallelism is free: parent can fork N children concurrently; CadQuery's
    reentrancy concerns live in the child where they belong.

Why no version-mismatch handshake:
  - The parent's in-memory ``agentcad`` is frozen at startup. After ``pip
    install --upgrade``, the user must run ``agentcad daemon restart`` to
    pick up the new code — same model as every other long-running process.
    ``daemon status`` reports the parent's version for diagnostics; nothing
    is gated on it.
"""

import hashlib
import json
import os
import signal
import socket
import struct
import subprocess
import sys
import time

import agentcad


def _venv_tag():
    """Short hash of the resolved ``sys.prefix`` so each venv gets its own daemon.

    A daemon's Python environment is fixed at the moment it's launched —
    requests it serves run inside *that* Python with *that* venv's
    site-packages, not the caller's. With a UID-only socket path, a daemon
    spawned from venv A would intercept requests from venv B and execute
    them in A's environment, leading to confusing failures when B has
    packages A doesn't (or vice versa).

    We hash ``sys.prefix`` rather than ``sys.executable`` because two venvs
    sharing the same underlying Python interpreter realpath to the *same*
    executable (the venv's python is a symlink/shim to /opt/homebrew/...).
    ``sys.prefix`` points to the venv root and is the actual unit of
    isolation — different venvs → different prefixes → different daemons,
    same venv (regardless of which symlink or shim invoked Python) → same
    prefix → same daemon.
    """
    canonical = os.path.realpath(sys.prefix)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def daemon_supported():
    """Whether this platform can host the preforked daemon.

    The daemon is built on POSIX-only primitives: ``os.fork`` (prefork the
    warm process), ``AF_UNIX`` sockets (the IPC transport), and ``os.getuid``
    (the per-user socket/pid path). Windows has none of these, so the daemon
    is disabled there and every command runs directly, equivalent to passing
    ``--no-daemon`` on each call.
    """
    return (
        hasattr(os, "fork")
        and hasattr(os, "getuid")
        and hasattr(socket, "AF_UNIX")
    )


def _default_socket_path():
    # Test/operator override — useful for pinning a daemon to a known path
    # in subprocess-based tests, or for running multiple daemons on a host
    # without venv-keyed isolation.
    override = os.environ.get("AGENTCAD_SOCKET_PATH")
    if override:
        return override
    return f"/tmp/agentcad-daemon-{os.getuid()}-{_venv_tag()}.sock"


def _default_pid_path():
    override = os.environ.get("AGENTCAD_PID_PATH")
    if override:
        return override
    return f"/tmp/agentcad-daemon-{os.getuid()}-{_venv_tag()}.pid"


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
        # Snapshot agentcad's version at startup so ``ping``/``status`` can
        # report it for diagnostics. Nothing is gated on it.
        self._version = agentcad.__version__

    def handle_request(self, request):
        """Dispatch a request and return a response dict.

        Production runs ``run`` requests through ``serve()`` → fork, where the
        child invokes ``_handle_run`` and writes its response back over the
        socket. This synchronous path exists so tests can verify the run
        contract without spawning a real subprocess.
        """
        req_type = request.get("type")
        if req_type == "ping":
            return {"type": "pong", "version": self._version}
        if req_type == "shutdown":
            self._running = False
            return {"type": "ack"}
        if req_type == "run":
            return self._handle_run(request)
        return {"type": "error", "message": f"Unknown request type: {req_type}"}

    def _handle_run(self, request):
        """Execute a single ``agentcad`` CLI invocation and return the response.

        In production this only runs inside a forked child (see ``_run_in_child``);
        the child exits after sending the response so cwd/env mutations don't
        leak. The in-process path (tests) restores cwd and the recursion-guard
        env var explicitly.
        """
        from click.testing import CliRunner
        from agentcad.cli import cli

        cwd = request.get("cwd")
        argv = request.get("argv", [])
        original_cwd = os.getcwd()
        original_env = os.environ.get("AGENTCAD_DAEMON")
        os.environ["AGENTCAD_DAEMON"] = "1"
        try:
            if cwd:
                os.chdir(cwd)
            runner = CliRunner()
            result = runner.invoke(cli, argv)
            return {
                "type": "result",
                "exit_code": result.exit_code,
                # ``output`` is stdout-only so the client can JSON-parse it
                # cleanly and stamp ``via: daemon``. Click 8.3's
                # ``result.output`` merges stdout+stderr, which broke that
                # parse historically.
                "output": result.stdout,
                "stderr": result.stderr,
                # Stamp the daemon's in-memory version so the routing layer
                # can warn when it doesn't match what the client's on-disk
                # agentcad is — i.e. pip-upgrade happened, daemon is stale.
                "version": self._version,
            }
        finally:
            os.chdir(original_cwd)
            if original_env is None:
                os.environ.pop("AGENTCAD_DAEMON", None)
            else:
                os.environ["AGENTCAD_DAEMON"] = original_env

    def serve(self):
        """Run the accept-and-fork loop until ``shutdown`` arrives."""
        # SIGCHLD ignore makes the kernel auto-reap exited children — no
        # zombies, no explicit waitpid plumbing. Verified clean on macOS and
        # Linux by spike 02.
        try:
            signal.signal(signal.SIGCHLD, signal.SIG_IGN)
        except ValueError:
            # Tests run the server inside a thread; signal.signal() requires
            # main thread. Tests don't trigger real forks (request handler
            # is patched), so no zombies accumulate.
            pass

        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

        # Write the PID file BEFORE binding the socket. ``start_daemon``'s
        # readiness check watches for the socket to appear; if we bound it
        # first there's a window where the socket exists but the PID file
        # hasn't been written yet, and a follow-up ``daemon status`` reads
        # an empty PID and reports running=false even though the process is
        # healthy.
        with open(self._pid_path, "w") as f:
            f.write(str(os.getpid()))

        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self._socket_path)
        # Backlog 16 absorbs short bursts. Per-request work happens in forked
        # children, so the parent itself rarely blocks.
        server_sock.listen(16)
        server_sock.settimeout(1.0)  # periodic _running check

        self._running = True
        try:
            while self._running:
                try:
                    conn, _ = server_sock.accept()
                except socket.timeout:
                    continue
                try:
                    request = _read_one_message(conn)
                except Exception:
                    _safe_close(conn)
                    continue
                if request is None:
                    _safe_close(conn)
                    continue

                if request.get("type") == "run":
                    self._fork_and_run(request, conn, server_sock)
                    # Parent's conn is already closed inside _fork_and_run.
                else:
                    response = self.handle_request(request)
                    try:
                        conn.sendall(encode_message(response))
                    except Exception:
                        pass
                    _safe_close(conn)
        finally:
            server_sock.close()
            if os.path.exists(self._socket_path):
                os.unlink(self._socket_path)
            if os.path.exists(self._pid_path):
                os.unlink(self._pid_path)

    def _fork_and_run(self, request, conn, listen_sock):
        """Fork a child to handle one ``run`` request; parent returns immediately."""
        try:
            pid = os.fork()
        except OSError as e:
            try:
                conn.sendall(encode_message({
                    "type": "result",
                    "exit_code": 97,
                    "output": json.dumps({
                        "command": "run",
                        "status": "error",
                        "message": f"daemon fork failed: {e}",
                    }),
                    "version": self._version,
                }))
            except Exception:
                pass
            _safe_close(conn)
            return

        if pid == 0:
            # CHILD: close listening socket, run the command, exit.
            try:
                listen_sock.close()
                _run_in_child(self, request, conn)
            finally:
                try:
                    sys.stdout.flush()
                    sys.stderr.flush()
                except Exception:
                    pass
                os._exit(0)
        else:
            # PARENT: the child owns the conn from here on. SIGCHLD-ignore
            # will reap the child when it exits.
            _safe_close(conn)


def _read_one_message(conn):
    """Block until one length-prefixed JSON message arrives on ``conn``.

    Returns the decoded dict or None on EOF. Raises on socket error.
    """
    buf = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            return None
        buf += chunk
        request, _ = decode_message(buf)
        if request is not None:
            return request


def _safe_close(sock):
    try:
        sock.close()
    except Exception:
        pass


def _run_in_child(server, request, conn):
    """In a forked child: handle one request and write the response back.

    The child has its own copy of cwd/env, so any state mutation here dies
    with the child. We don't need the cwd/env-restore plumbing that the
    in-process ``_handle_run`` path uses.
    """
    try:
        response = server._handle_run(request)
    except BaseException as e:
        response = {
            "type": "result",
            "exit_code": 98,
            "output": json.dumps({
                "command": "run",
                "status": "error",
                "message": f"runner crashed: {type(e).__name__}: {e}",
            }),
            "stderr": "",
            "version": server._version,
        }
    try:
        conn.sendall(encode_message(response))
    except Exception:
        # Client may have disconnected — child has done its work and will
        # exit. Nothing useful to log here without contaminating stderr.
        pass


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
        return _read_one_message(sock)
    except (ConnectionError, OSError):
        return None
    finally:
        _safe_close(sock)


# ---------- Lifecycle ----------

def _pid_alive(pid):
    """Check if a process with the given PID is alive (zombie-aware).

    Plain ``os.kill(pid, 0)`` reports zombies as alive — the process record
    exists in the kernel until reaped by the parent. In production this is
    a non-issue (the daemon detaches via ``start_new_session=True``, so it's
    not anyone's child by the time ``stop_daemon`` runs). But callers in
    tests own the subprocess they spawn, and unreaped zombies look
    indistinguishable from a still-running daemon. So: when ``pid`` happens
    to be one of our own children, opportunistically reap it before reporting.
    """
    try:
        result_pid, _ = os.waitpid(pid, os.WNOHANG)
        if result_pid == pid:
            return False
    except (ChildProcessError, OSError):
        pass
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _read_pid(pid_path):
    """Return the integer PID stored at ``pid_path``, or None if missing/garbage."""
    if not os.path.exists(pid_path):
        return None
    try:
        return int(open(pid_path).read().strip())
    except (ValueError, OSError):
        return None


def _unlink_quiet(path):
    """``os.unlink(path)`` that swallows the not-found case."""
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def daemon_status(socket_path=None, pid_path=None):
    """Check if the daemon is running. Returns dict with running, pid, version,
    and (when relevant) a ``stale`` flag + hint when the daemon's in-memory
    code is older than the installed agentcad on disk.

    Requires successful ping, not just an alive PID. The pid file by itself
    can lie — a leftover from a dead daemon could point at an unrelated
    alive process (e.g. an ``agentcad render`` from another shell, or PID
    recycling after the original daemon was SIGKILLed), which would
    otherwise falsely report ``running:true`` and block
    ``spawn_daemon_via_fork`` from starting a fresh daemon. Issue #190.

    Stale state (PID alive but socket unbound, or socket file present but
    no listener answering ping) is cleaned up so the next spawn attempt
    has a clean slate.

    The staleness check on the daemon's reported version is the only way
    to surface ``pip install --upgrade agentcad`` to a running daemon,
    which would otherwise silently keep serving the old code until the
    user happened to restart. Nothing is gated on it — agents see the
    hint via ``agentcad daemon status`` and can choose to
    ``agentcad daemon restart``.
    """
    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()

    pid = _read_pid(pid_path)
    if pid is None:
        return {"running": False}

    if not _pid_alive(pid):
        # Stale PID — process died. Clean up files.
        _unlink_quiet(pid_path)
        _unlink_quiet(socket_path)
        return {"running": False}

    if not os.path.exists(socket_path):
        # PID alive but socket never bound (daemon died before bind,
        # or fork failed mid-startup). Clean up pid file so spawn can retry.
        _unlink_quiet(pid_path)
        return {"running": False}

    # Probe the listener. Ping bypasses any state the daemon might be in;
    # no response means this PID isn't actually a daemon (or the daemon
    # is wedged) — clean up so spawn_daemon_via_fork can take over.
    resp = send_request({"type": "ping"}, socket_path=socket_path)
    if not resp or resp.get("type") != "pong":
        _unlink_quiet(pid_path)
        _unlink_quiet(socket_path)
        return {"running": False}

    result = {"running": True, "pid": pid}
    version = resp.get("version")
    if version is not None:
        result["version"] = version
        # Compare to the currently-installed agentcad. ``agentcad.__version__``
        # reflects what *this* (client) process loaded; if the daemon's
        # in-memory version differs, the daemon is running stale code from
        # before the last pip upgrade.
        on_disk = agentcad.__version__
        if version != on_disk:
            result["stale"] = True
            result["installed_version"] = on_disk
            result["hint"] = (
                f"Daemon is running v{version}; installed agentcad is "
                f"v{on_disk}. Run 'agentcad daemon restart' to load the "
                f"new code."
            )
    return result


def _force_kill(pid, pid_path, socket_path, sigterm_grace_s=2.0):
    """Send SIGTERM, then SIGKILL if needed; clean up socket + PID files.

    Returns the method that ultimately stopped the process, one of
    ``"sigterm"`` or ``"sigkill"``. Used as the fallback when graceful
    shutdown is unavailable (no socket, no ack, daemon ignored shutdown).
    """
    method = None
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
            method = "sigterm"
        except (ProcessLookupError, PermissionError):
            pass
        deadline = time.time() + sigterm_grace_s
        while time.time() < deadline:
            if not _pid_alive(pid):
                break
            time.sleep(0.05)

        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
                method = "sigkill"
            except (ProcessLookupError, PermissionError):
                pass
            for _ in range(20):
                if not _pid_alive(pid):
                    break
                time.sleep(0.05)

    _unlink_quiet(socket_path)
    _unlink_quiet(pid_path)
    return method


def stop_daemon(socket_path=None, pid_path=None):
    """Send shutdown to the daemon. Always wins or reports why it couldn't.

    Strategy:
      1. If no daemon is running, return a clear "not running" message.
      2. Try graceful shutdown over the socket. If we get an ack and the
         process exits cleanly, report ``method="graceful"``.
      3. Otherwise escalate to SIGTERM, then SIGKILL. Report
         ``method="sigterm"`` or ``method="sigkill"``.
    """
    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()

    pid = _read_pid(pid_path)
    if pid is None or not _pid_alive(pid):
        # Nothing to stop. Clean up any leftover files for completeness.
        _unlink_quiet(socket_path)
        _unlink_quiet(pid_path)
        return {"stopped": False, "message": "Daemon not running"}

    # Try graceful shutdown first. Wait for both the socket AND PID file to
    # be unlinked: ``serve()``'s finally block removes the socket first and
    # the PID file second, so a check that only watches the socket can
    # return prematurely while the PID file is still on disk — that races
    # with a follow-up ``start_daemon`` call.
    if os.path.exists(socket_path):
        resp = send_request({"type": "shutdown"}, socket_path=socket_path)
        if resp is not None and resp.get("type") == "ack":
            for _ in range(50):
                if not os.path.exists(socket_path) and not os.path.exists(pid_path):
                    return {"stopped": True, "method": "graceful"}
                time.sleep(0.1)
            # Ack but files lingered — fall through to force.

    method = _force_kill(pid, pid_path, socket_path)
    if _pid_alive(pid):
        return {
            "stopped": False,
            "message": (
                f"Daemon process (pid {pid}) did not exit after SIGKILL. "
                f"Investigate manually with `ps -p {pid}`."
            ),
        }
    return {"stopped": True, "method": method or "force"}


# Default cold-start timeout. Generous enough to cover OCP's cold import on a
# fresh install where the kernel page cache hasn't seen the .so files yet —
# observed at 30+s on a freshly pip-installed venv.
_DEFAULT_START_TIMEOUT_S = 90.0


def start_daemon(socket_path=None, pid_path=None, timeout_s=_DEFAULT_START_TIMEOUT_S):
    """Start the daemon as a subprocess. Returns dict with started, pid.

    If a daemon is already running, refuses with "already running". Pip-upgrade
    recovery is via ``agentcad daemon restart``, not via auto-replace inside
    start.

    Readiness is determined by the socket file appearing on disk, never by
    "the subprocess hasn't exited yet." On a cold install OCP can take 30+s
    to import; the previous code would time out, see the process still alive,
    and optimistically report ``started:true`` even though the daemon hadn't
    bound the socket or written its PID file.
    """
    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()

    status = daemon_status(socket_path=socket_path, pid_path=pid_path)
    if status.get("running"):
        return {
            "started": False,
            "message": "Daemon already running",
            "pid": status["pid"],
        }

    # Defensive cleanup — daemon_status should have done this, but a previous
    # crash could have left files behind.
    _unlink_quiet(socket_path)
    _unlink_quiet(pid_path)

    proc = subprocess.Popen(
        [sys.executable, "-m", "agentcad.daemon",
         "--socket", socket_path, "--pid", pid_path],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    socket_ready = False
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if os.path.exists(socket_path):
            socket_ready = True
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)

    if socket_ready:
        return {"started": True, "pid": proc.pid}

    # Either the subprocess crashed or the timeout expired with no socket.
    crashed = proc.poll() is not None
    if not crashed:
        try:
            os.kill(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

    stderr = ""
    try:
        stderr = proc.stderr.read().decode("utf-8", errors="replace").strip()
    except Exception:
        pass

    if crashed:
        msg = "Daemon failed to start"
        if stderr:
            last_line = stderr.strip().splitlines()[-1]
            msg = f"Daemon failed to start: {last_line}"
    else:
        msg = (
            f"Daemon did not bind its socket within {timeout_s:.0f}s — "
            "OCP/CadQuery cold import may be unusually slow on this machine. "
            "Try `agentcad daemon start` again (the import should be cached "
            "now), or increase the timeout."
        )
        if stderr:
            msg += f" Subprocess stderr: {stderr.splitlines()[-1]}"

    _unlink_quiet(socket_path)
    _unlink_quiet(pid_path)
    return {"started": False, "message": msg}


def restart_daemon(socket_path=None, pid_path=None):
    """Stop the daemon (if running) and start a fresh one.

    Recommended path after ``pip install --upgrade agentcad`` — the running
    daemon has the old code loaded in memory and won't see the upgrade until
    its process is replaced.
    """
    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()
    stop_result = stop_daemon(socket_path=socket_path, pid_path=pid_path)
    start_result = start_daemon(socket_path=socket_path, pid_path=pid_path)
    return {
        "stopped": stop_result.get("stopped", False),
        **start_result,
    }


def spawn_daemon_via_fork(socket_path=None, pid_path=None):
    """Spawn a daemon by forking off the calling process — no extra cold OCP.

    Hooked from ``agentcad run`` after the script's runtime has imported OCP.
    The fork inherits OCP in memory (copy-on-write), so the daemon "boots"
    for free off the same import the parent already paid for. Subsequent
    ``agentcad run`` invocations in this venv find the socket and route
    through the daemon at ~0.1s instead of paying the warm OCP cost again.

    No-op when:
      * a daemon is already running for this venv (per-venv socket path),
      * we're inside a daemon-routed request (``AGENTCAD_DAEMON`` env set),
      * fork fails for any reason — the caller's run is unaffected.

    Uses the standard daemon-detach pattern: double-fork + ``setsid`` +
    close-and-redirect stdio. The grandchild (the daemon) is reparented to
    init, has no controlling terminal, and inherits no parent file
    descriptors that could keep pipes open or leak fds.

    Returns a dict with at least ``spawned: bool`` for diagnostics. The
    parent never blocks waiting for the daemon to finish binding — the
    grandchild does that on its own time, and the next ``agentcad run`` is
    the one that benefits.
    """
    if os.environ.get("AGENTCAD_DAEMON"):
        return {"spawned": False, "reason": "inside_daemon"}

    if socket_path is None:
        socket_path = _default_socket_path()
    if pid_path is None:
        pid_path = _default_pid_path()

    status = daemon_status(socket_path=socket_path, pid_path=pid_path)
    if status.get("running"):
        return {"spawned": False, "reason": "already_running",
                "pid": status["pid"]}

    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    try:
        first_pid = os.fork()
    except OSError:
        return {"spawned": False, "reason": "fork_failed"}

    if first_pid != 0:
        try:
            os.waitpid(first_pid, 0)
        except ChildProcessError:
            pass
        return {"spawned": True}

    # First child: detach from session then double-fork so we're reparented
    # to init and the original parent doesn't have to wait on us.
    try:
        os.setsid()
    except OSError:
        os._exit(1)

    try:
        second_pid = os.fork()
    except OSError:
        os._exit(1)

    if second_pid != 0:
        os._exit(0)

    # Grandchild: this is the daemon. Close every inherited fd and redirect
    # stdio to /dev/null so we don't keep parent-side pipes alive.
    #
    # macOS's ``sysconf("SC_OPEN_MAX")`` returns 2^63-1 ("unlimited"), so
    # iterating it directly would spin forever. Use the actual soft
    # RLIMIT_NOFILE instead, capped to something sane.
    import resource
    try:
        soft_limit, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (ValueError, OSError):
        soft_limit = 1024
    max_fd = min(int(soft_limit), 65536)
    os.closerange(3, max_fd)

    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    # Optional debug log for grandchild stderr (set
    # AGENTCAD_DAEMON_STDERR_LOG=/path/to/log) so a forked daemon's crashes
    # don't disappear into /dev/null. Production default is /dev/null.
    err_log = os.environ.get("AGENTCAD_DAEMON_STDERR_LOG")
    if err_log:
        try:
            err_fd = os.open(err_log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            os.dup2(err_fd, 2)
            os.close(err_fd)
        except OSError:
            os.dup2(devnull, 2)
    else:
        os.dup2(devnull, 2)
    if devnull > 2:
        os.close(devnull)

    try:
        server = DaemonServer(socket_path=socket_path, pid_path=pid_path)
        server.serve()
    except BaseException:
        pass
    finally:
        os._exit(0)


# ---------- __main__ ----------

def _main():
    """Entry point for daemon subprocess."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default=None)
    parser.add_argument("--pid", default=None)
    args = parser.parse_args()

    # Eager imports — warm up all expensive modules in the parent so each
    # forked child inherits OCP in memory via copy-on-write.
    import cadquery  # noqa: F401
    from cadquery import cqgi, exporters  # noqa: F401
    from agentcad import helpers, metrics, render, export  # noqa: F401
    try:
        import build123d  # noqa: F401
    except ImportError:
        pass

    server = DaemonServer(
        socket_path=args.socket,
        pid_path=args.pid,
    )
    server.serve()


if __name__ == "__main__":
    _main()
