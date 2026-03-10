import json

import click

from cadtool.daemon import (
    _default_pid_path,
    _default_socket_path,
    daemon_status,
    start_daemon,
    stop_daemon,
)


def _socket_path():
    return _default_socket_path()


def _pid_path():
    return _default_pid_path()


@click.group()
def daemon():
    """Manage the cadtool background daemon."""


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
