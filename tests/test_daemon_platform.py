"""Platform-capability guards for the preforked daemon.

The daemon relies on POSIX-only primitives — ``os.fork``, ``AF_UNIX``
sockets, ``os.getuid`` (per-user socket path), ``os.setsid``. Windows has
none of these, so every heavy command used to crash with ``AttributeError:
module 'os' has no attribute 'getuid'`` *before the script ran* — the only
workaround was ``--no-daemon`` on every call (reported via feedback,
2026-05-30). These tests lock in that the daemon silently disables itself
on platforms that can't support it, so commands fall back to direct
execution instead of crashing.
"""

import json
import os
import socket

import pytest

from agentcad import daemon
from agentcad.commands import _daemon_routing as routing


def test_daemon_supported_matches_host_capabilities():
    expected = (
        hasattr(os, "fork")
        and hasattr(os, "getuid")
        and hasattr(socket, "AF_UNIX")
    )
    assert daemon.daemon_supported() is expected


def test_daemon_supported_false_without_getuid(monkeypatch):
    # Simulate Windows: no os.getuid.
    monkeypatch.delattr(os, "getuid", raising=False)
    assert daemon.daemon_supported() is False


def test_daemon_supported_false_without_fork(monkeypatch):
    monkeypatch.delattr(os, "fork", raising=False)
    assert daemon.daemon_supported() is False


def test_daemon_supported_false_without_af_unix(monkeypatch):
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    assert daemon.daemon_supported() is False


def test_route_noops_when_unsupported(monkeypatch):
    """maybe_route_through_daemon must not touch the socket path on an
    unsupported platform, even with no_daemon=False and AGENTCAD_DAEMON unset."""
    monkeypatch.setattr(daemon, "daemon_supported", lambda: False)
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    called = []
    monkeypatch.setattr(routing, "_route_through_daemon",
                        lambda argv: called.append(argv))

    assert routing.maybe_route_through_daemon(["run", "x.py"], no_daemon=False) is None
    assert called == [], "should not attempt to reach the daemon when unsupported"


def test_spawn_noops_when_unsupported(monkeypatch):
    monkeypatch.setattr(daemon, "daemon_supported", lambda: False)
    called = []
    monkeypatch.setattr(daemon, "spawn_daemon_via_fork",
                        lambda **kw: called.append(kw))

    routing.maybe_spawn_daemon_for_next_run(no_daemon=False)
    assert called == [], "should not fork a daemon when unsupported"


def test_windows_simulation_no_attributeerror(monkeypatch):
    """End-to-end regression for the reported crash: with the POSIX
    primitives removed (as on Windows), both daemon entry points must
    no-op rather than raise AttributeError on os.getuid/os.fork."""
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.delattr(os, "fork", raising=False)
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)

    # Neither call should raise — this is exactly the path `agentcad run`
    # takes before/after executing a script.
    assert routing.maybe_route_through_daemon(["run", "x.py"], no_daemon=False) is None
    routing.maybe_spawn_daemon_for_next_run(no_daemon=False)


# --- `agentcad daemon` subcommand guards (Windows-clean diagnostics) ---


@pytest.mark.parametrize("subcommand", ["start", "stop", "status", "restart"])
def test_daemon_subcommand_emits_unsupported_json_on_windows(
    runner, monkeypatch, subcommand
):
    """`agentcad daemon {start,stop,status,restart}` must emit a clean
    status='unsupported' JSON instead of crashing with AttributeError.

    The routing-level guard only covers `agentcad run` and friends;
    daemon_cmd.py's diagnostic subcommands have their own entry into
    _default_socket_path(). Without this mirror, a Windows user trying
    to diagnose with `agentcad daemon status` would hit the exact crash
    that motivated daemon_supported() in the first place.
    """
    from agentcad.cli import cli

    # Simulate Windows by removing the POSIX primitives daemon_supported()
    # probes for. monkeypatch restores them at test teardown.
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.delattr(os, "fork", raising=False)
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)

    result = runner.invoke(cli, ["daemon", subcommand])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "daemon"
    assert parsed["subcommand"] == subcommand
    assert parsed["status"] == "unsupported"
    assert "POSIX" in parsed["message"]
    assert "Native Windows daemon support is not implemented yet" in parsed["message"]
