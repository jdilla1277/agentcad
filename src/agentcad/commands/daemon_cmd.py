import json

import click

from agentcad.daemon import (
    _default_pid_path,
    _default_socket_path,
    daemon_status,
    restart_daemon,
    start_daemon,
    stop_daemon,
)


def _socket_path():
    return _default_socket_path()


def _pid_path():
    return _default_pid_path()


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
    ``start``, but always force-kills if graceful shutdown fails.
    """
    result = restart_daemon(
        socket_path=_socket_path(),
        pid_path=_pid_path(),
    )
    ok = result.get("started", False)
    output = {"command": "daemon", "status": "success" if ok else "error", **result}
    click.echo(json.dumps(output))
