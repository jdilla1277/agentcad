"""Shared daemon-routing for heavy commands.

The daemon was originally wired only into ``agentcad run``. This module
extracts the routing pattern so any heavy command (``render``, ``export``,
``inspect``, ``import``, ``diff``) can opt in and benefit from the warm
OCP state. Closes #177.

Server side: ``daemon._run_in_child`` is runtime-agnostic — it just invokes
the Click CLI with whatever argv arrives. We don't need a new daemon request
type per command; each client just sends its own argv with the appropriate
subcommand name at argv[0].

Public API:

``maybe_route_through_daemon(argv, no_daemon=False)``
    Try sending this argv to a running daemon. If routed successfully,
    prints the response on stdout and calls ``sys.exit()``. If the
    daemon is unreachable / disabled, returns so the caller proceeds
    with direct execution. Stamps ``"via": "daemon"`` onto the JSON response.

``maybe_spawn_daemon_for_next_run(no_daemon=False)``
    Fork off the warm process as the daemon after a successful direct
    execution. Subsequent commands in the same venv route through the
    forked daemon at ~0.1s instead of paying the cold OCP cost again.

Tests monkeypatch ``agentcad.daemon.send_request`` to simulate a
running daemon — see ``test_run.py::test_run_via_daemon_field_in_output``
for the canonical pattern.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click

import agentcad
from agentcad import daemon as _daemon


def _socket_path() -> str:
    return _daemon._default_socket_path()


def _pid_path() -> str:
    return _daemon._default_pid_path()


def _route_through_daemon(argv: list[str]):
    """Send an argv-shaped request to the daemon. Returns the raw response
    dict, or ``None`` if the daemon couldn't be reached. argv[0] is the
    Click subcommand name (``run``, ``render``, etc.); the daemon-side
    handler invokes the CLI with this argv directly."""
    return _daemon.send_request(
        {"type": "run", "cwd": str(Path.cwd()), "argv": argv},
        socket_path=_socket_path(),
    )


def maybe_route_through_daemon(argv: list[str], no_daemon: bool = False) -> None:
    """Try routing through the daemon; sys.exit on success.

    Returns ``None`` if direct execution should proceed:
      * the platform can't host a daemon (Windows: no ``fork``/``AF_UNIX``/
        ``getuid``; see ``daemon.daemon_supported``),
      * caller passed ``--no-daemon``,
      * we're already inside a daemon (``AGENTCAD_DAEMON`` env set —
        would recurse),
      * the daemon is unreachable.

    On successful routing, prints the (possibly augmented) output on
    stdout and calls ``sys.exit(exit_code)`` — caller never returns.

    Pip-upgrade hint: a daemon started before ``pip install --upgrade
    agentcad`` keeps the old code in memory. We don't auto-restart it
    (that would re-introduce the version-handshake machinery the redesign
    deleted), but we do emit a one-line stderr warning when the daemon's
    in-memory version differs from the client's on-disk install — so the
    silent "via:daemon stamped on stale code" trap is at least visible.
    """
    if not _daemon.daemon_supported() or os.environ.get("AGENTCAD_DAEMON") or no_daemon:
        return None

    result = _route_through_daemon(argv)
    if result is None:
        return None

    daemon_version = result.get("version")
    if daemon_version and daemon_version != agentcad.__version__:
        click.echo(
            f"warning: daemon is running agentcad v{daemon_version}; "
            f"installed agentcad is v{agentcad.__version__}. "
            f"Run `agentcad daemon restart` to refresh.",
            err=True,
        )

    output_str = result.get("output", "")
    stderr_str = result.get("stderr", "")
    try:
        data = json.loads(output_str)
        data["via"] = "daemon"
        output_str = json.dumps(data)
    except (json.JSONDecodeError, TypeError):
        # Daemon returned non-JSON (shouldn't happen, but be defensive).
        pass
    # Replay child's stderr first (heartbeats, progress messages), then stdout
    # with the via-stamp applied.
    if stderr_str:
        click.echo(stderr_str, nl=False, err=True)
    if output_str:
        click.echo(output_str, nl=False)
    sys.exit(result.get("exit_code", 0))


def maybe_spawn_daemon_for_next_run(no_daemon: bool = False) -> None:
    """Fork off the warm process as the daemon after a successful direct
    execution. Idempotent — silently no-ops if a daemon for this venv
    is already running, if we're inside a daemon-routed request, or if the
    platform can't host a daemon."""
    if no_daemon or not _daemon.daemon_supported():
        return
    _daemon.spawn_daemon_via_fork(
        socket_path=_socket_path(),
        pid_path=_pid_path(),
    )
