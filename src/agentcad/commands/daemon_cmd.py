import json
import platform

import click

from agentcad.daemon import (
    _default_pid_path,
    _default_socket_path,
    daemon_status,
    daemon_supported,
    restart_daemon,
    start_daemon,
    stop_daemon,
)


def _socket_path():
    return _default_socket_path()


def _pid_path():
    return _default_pid_path()


def _emit_unsupported(subcommand: str) -> None:
    """Emit a clean JSON response when the daemon can't run on this platform.

    On Windows the daemon is disabled entirely (no ``fork``/``AF_UNIX``/
    ``getuid``). Without this guard, every ``agentcad daemon *`` subcommand
    crashes with ``AttributeError`` inside ``_default_socket_path()`` — the
    exact crash ``daemon_supported()`` was added to prevent on the run path.
    Mirroring it here keeps the diagnostic subcommands quiet and actionable
    on Windows instead of blowing up the agent."""
    click.echo(json.dumps({
        "command": "daemon",
        "subcommand": subcommand,
        "status": "unsupported",
        "platform": platform.system(),
        "message": (
            "The agentcad daemon requires POSIX primitives (os.fork, AF_UNIX, "
            "os.getuid) and is disabled on this platform. Commands still run "
            "directly — behavior is correct, but each invocation pays the "
            "full OCP import cost instead of routing through a warm worker. "
            "Native Windows daemon support is not implemented yet."
        ),
    }))


@click.group(hidden=True)
def daemon():
    """Manage the agentcad background daemon (auto-managed; commands here are
    diagnostic).

    The daemon is spawned automatically on the first ``agentcad run`` in a
    venv and reused for subsequent runs — agents don't need to start or
    stop it. The commands in this group are kept for diagnostics
    (``status``) and operator cleanup (``stop``); ``start`` and ``restart``
    are mostly redundant since auto-spawn handles the common path."""


@daemon.command()
def start():
    """Start the daemon worker."""
    if not daemon_supported():
        _emit_unsupported("start")
        return
    result = start_daemon(
        socket_path=_socket_path(),
        pid_path=_pid_path(),
    )
    ok = result.get("started", False) or "already running" in result.get("message", "").lower()
    output = {"command": "daemon", "status": "success" if ok else "error", **result}
    click.echo(json.dumps(output))


@daemon.command()
def stop():
    """Stop the daemon worker."""
    if not daemon_supported():
        _emit_unsupported("stop")
        return
    result = stop_daemon(
        socket_path=_socket_path(),
        pid_path=_pid_path(),
    )
    ok = result.get("stopped", False)
    output = {"command": "daemon", "status": "success" if ok else "error", **result}
    click.echo(json.dumps(output))


@daemon.command()
def status():
    """Check daemon status."""
    if not daemon_supported():
        _emit_unsupported("status")
        return
    result = daemon_status(
        socket_path=_socket_path(),
        pid_path=_pid_path(),
    )
    output = {"command": "daemon", "status": "success", **result}
    click.echo(json.dumps(output))


@daemon.command()
def restart():
    """Stop the daemon (if running) and start a fresh one.

    Single-command recovery path for stale-version daemons after ``pip
    install --upgrade agentcad``. Equivalent to ``stop`` followed by
    ``start``. Force-stop is attempted only after daemon identity is verified.
    """
    if not daemon_supported():
        _emit_unsupported("restart")
        return
    result = restart_daemon(
        socket_path=_socket_path(),
        pid_path=_pid_path(),
    )
    ok = result.get("started", False)
    output = {"command": "daemon", "status": "success" if ok else "error", **result}
    click.echo(json.dumps(output))
