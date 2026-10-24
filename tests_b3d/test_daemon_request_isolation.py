"""A daemon auto-spawned from project A must serve project B as B.

Heavy commands fork the warm process into the daemon *while they are still
running* (``maybe_spawn_daemon_for_next_run``). Whatever per-invocation state
the spawning command holds at that moment is copied into the daemon forever.
Every later request — from any project — must still be handled with its own
project's output formatting and its own client environment.

These tests stay deterministic: instead of forking, the spawn hook calls the
daemon's request handler in-process at the exact moment the real fork would
happen, so the handler sees the same inherited state a forked daemon would.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from agentcad import cli as cli_mod
from agentcad import daemon as daemon_mod
from agentcad.cli import cli
from agentcad.daemon import DaemonServer

B3D_BOX = "from build123d import Box\nshow_object(Box(20, 30, 10))\n"
RUN_FLAGS = ["--no-view", "--no-diff", "--no-preview"]


def _init_project(runner, path, monkeypatch):
    path.mkdir()
    monkeypatch.chdir(path)
    result = runner.invoke(cli, ["init", "--name", path.name, "--no-agent-setup"])
    assert result.exit_code == 0, result.output
    (path / "model.py").write_text(B3D_BOX)


def _direct_run(runner, path, label, monkeypatch):
    monkeypatch.chdir(path)
    result = runner.invoke(cli, ["run", "model.py", "--label", label, *RUN_FLAGS])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _assert_belongs_to(path_value, project, other, field):
    assert str(other) not in path_value, (
        f"{field} for project {project.name} points into project {other.name}: "
        f"{path_value}"
    )
    path = Path(path_value)
    resolved = path if path.is_absolute() else project / path
    assert resolved.is_relative_to(project), (
        f"{field} is outside project {project.name}: {path_value}"
    )
    assert resolved.is_file(), f"{field} does not exist: {resolved}"


@pytest.fixture
def two_projects(runner, tmp_path, monkeypatch):
    a = tmp_path / "project_a"
    b = tmp_path / "project_b"
    _init_project(runner, a, monkeypatch)
    _init_project(runner, b, monkeypatch)
    monkeypatch.chdir(a)
    return a, b


def _spawn_hook_serving(request, record):
    """Replace the fork with one in-process daemon request.

    The hook fires inside the spawning command, exactly where the real
    ``spawn_daemon_via_fork`` would copy the process into the daemon.
    """

    def fake_spawn(socket_path=None, pid_path=None):
        if "busy" in record:
            return {"spawned": False, "reason": "already_running"}
        record["busy"] = True
        # The spawning command's own output state, which a real fork copies.
        spawning_output = cli_mod._INVOCATION_OUTPUT.get()
        record["spawning_output"] = spawning_output
        captured_before = list(spawning_output.captured)
        record["response"] = DaemonServer().handle_request(request)
        record["spawning_output_gained"] = spawning_output.captured[
            len(captured_before):
        ]
        return {"spawned": True}

    return fake_spawn


def test_daemon_spawned_by_run_reports_other_projects_paths_in_their_project(
    runner, two_projects, monkeypatch
):
    a, b = two_projects
    record = {}
    monkeypatch.setattr(
        daemon_mod,
        "spawn_daemon_via_fork",
        _spawn_hook_serving(
            {
                "type": "run",
                "cwd": str(b),
                "argv": ["run", "model.py", "--label", "bee", *RUN_FLAGS],
            },
            record,
        ),
    )

    result = runner.invoke(cli, ["run", "model.py", "--label", "ay", *RUN_FLAGS])

    assert result.exit_code == 0, result.output
    a_output = json.loads(result.stdout)
    _assert_belongs_to(a_output["outputs"]["step"], a, b, "A outputs.step")

    response = record["response"]
    assert response["exit_code"] == 0, response
    b_output = json.loads(response["output"])
    assert b_output["status"] == "success", b_output
    _assert_belongs_to(b_output["outputs"]["step"], b, a, "B outputs.step")
    for name, value in b_output["outputs"].items():
        if isinstance(value, str) and value.startswith(("v", "/")):
            _assert_belongs_to(value, b, a, f"B outputs.{name}")
    assert record["spawning_output_gained"] == []


def test_daemon_spawned_by_render_reports_other_projects_renders_in_their_project(
    runner, two_projects, monkeypatch
):
    a, b = two_projects
    monkeypatch.setattr(
        daemon_mod, "spawn_daemon_via_fork", lambda **_: {"spawned": False}
    )
    _direct_run(runner, a, "ay", monkeypatch)
    _direct_run(runner, b, "bee", monkeypatch)
    monkeypatch.chdir(a)

    record = {}
    monkeypatch.setattr(
        daemon_mod,
        "spawn_daemon_via_fork",
        _spawn_hook_serving(
            {
                "type": "run",
                "cwd": str(b),
                "argv": ["render", "v1_bee/output.step", "--view", "iso"],
            },
            record,
        ),
    )

    result = runner.invoke(cli, ["render", "v1_ay/output.step", "--view", "iso"])

    assert result.exit_code == 0, result.output
    _assert_belongs_to(json.loads(result.stdout)["renders"]["iso"], a, b, "A renders.iso")

    response = record["response"]
    assert response["exit_code"] == 0, response
    b_output = json.loads(response["output"])
    assert b_output["status"] == "success", b_output
    _assert_belongs_to(b_output["renders"]["iso"], b, a, "B renders.iso")


ENV_PROBE = (
    "import os\n"
    "from pathlib import Path\n"
    "from build123d import Box\n"
    "seen = {k: os.environ.get(k) for k in "
    "('AGENTCAD_VIEWER_HOME', 'AGENTCAD_DIFF_TIMEOUT_S', 'AGENTCAD_PROBE_EMPTY', "
    "'AGENTCAD_DAEMON_FUTURE_SETTING', 'AGENTCAD_SOCKET_PATH')}\n"
    "Path('seen_env.json').write_text(__import__('json').dumps(seen))\n"
    "show_object(Box(1, 1, 1))\n"
)


def test_daemon_request_uses_client_agentcad_environment(
    runner, tmp_path, monkeypatch
):
    """The daemon keeps the environment of whoever spawned it; a request must
    run with the *client's* AGENTCAD_* settings instead."""
    b = tmp_path / "project_b"
    _init_project(runner, b, monkeypatch)
    (b / "model.py").write_text(ENV_PROBE)
    client_home = tmp_path / "b-viewer-home"

    daemon_env = {
        "AGENTCAD_VIEWER_HOME": str(tmp_path / "a-viewer-home"),
        "AGENTCAD_DIFF_TIMEOUT_S": "7",
        "AGENTCAD_PROBE_EMPTY": "daemon",
        # Daemon-side settings: the daemon's values win, whatever the client has.
        "AGENTCAD_DAEMON_FUTURE_SETTING": "daemon",
        "AGENTCAD_SOCKET_PATH": "/daemon.sock",
    }
    sent = {}

    def fake_send_request(msg, socket_path=None, **_kwargs):
        sent["msg"] = msg
        # Simulate the daemon process: its own environment came from the
        # project that spawned it, not from this client.
        client_env = os.environ.copy()
        os.environ.update(daemon_env)
        try:
            return DaemonServer().handle_request(msg)
        finally:
            os.environ.clear()
            os.environ.update(client_env)

    monkeypatch.setattr(daemon_mod, "send_request", fake_send_request)
    monkeypatch.setattr(daemon_mod, "daemon_supported", lambda: True)
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    monkeypatch.delenv("AGENTCAD_DIFF_TIMEOUT_S", raising=False)
    monkeypatch.setenv("AGENTCAD_VIEWER_HOME", str(client_home))
    monkeypatch.setenv("AGENTCAD_PROBE_EMPTY", "")
    monkeypatch.setenv("AGENTCAD_DAEMON_FUTURE_SETTING", "client")
    monkeypatch.setenv("AGENTCAD_SOCKET_PATH", str(tmp_path / "client.sock"))

    result = runner.invoke(cli, ["run", "model.py", "--label", "probe", *RUN_FLAGS])

    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output.get("via") == "daemon", output
    seen = json.loads((b / "seen_env.json").read_text())
    assert seen == {
        "AGENTCAD_VIEWER_HOME": str(client_home),
        "AGENTCAD_DIFF_TIMEOUT_S": None,
        "AGENTCAD_PROBE_EMPTY": "",
        "AGENTCAD_DAEMON_FUTURE_SETTING": "daemon",
        "AGENTCAD_SOCKET_PATH": "/daemon.sock",
    }, f"daemon request ran with the wrong environment: {seen}"
    # Daemon-process plumbing stays out of the request.
    assert set(sent["msg"]["env"]) == {"AGENTCAD_VIEWER_HOME", "AGENTCAD_PROBE_EMPTY"}
    assert os.environ["AGENTCAD_VIEWER_HOME"] == str(client_home)


def test_client_request_env_keeps_daemon_settings_daemon_side():
    env = {
        "AGENTCAD_VIEWER_HOME": "/v",
        "AGENTCAD_NO_LOG": "",
        "AGENTCAD_DAEMON": "1",
        "AGENTCAD_DAEMON_STDERR_LOG": "/log",
        "AGENTCAD_DAEMON_SOME_FUTURE_SETTING": "x",
        "AGENTCAD_SOCKET_PATH": "/s.sock",
        "AGENTCAD_PID_PATH": "/s.pid",
        "_AGENTCAD_DAEMON_CHILD": "1",
        "HOME": "/home/someone",
    }
    assert daemon_mod.client_request_env(env) == {
        "AGENTCAD_VIEWER_HOME": "/v",
        "AGENTCAD_NO_LOG": "",
    }


def test_daemon_routed_command_is_session_logged_once_with_client_argv(
    runner, tmp_path, monkeypatch
):
    """The daemon's ``sys.argv`` is whatever started it (a forked daemon keeps
    the spawning command's argv). Only the client logs a routed command."""
    b = tmp_path / "project_b"
    _init_project(runner, b, monkeypatch)
    client_args = ["model.py", "--label", "bee", *RUN_FLAGS]
    spawning_argv = ["agentcad", "run", "model.py", "--label", "ay", "--no-view"]

    def fake_send_request(msg, socket_path=None, **_kwargs):
        client_argv = sys.argv
        sys.argv = spawning_argv
        try:
            return DaemonServer().handle_request(msg)
        finally:
            sys.argv = client_argv

    monkeypatch.setattr(daemon_mod, "send_request", fake_send_request)
    monkeypatch.setattr(daemon_mod, "daemon_supported", lambda: True)
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    monkeypatch.delenv("AGENTCAD_NO_LOG", raising=False)
    monkeypatch.setattr(sys, "argv", ["agentcad", "run", *client_args])

    result = runner.invoke(cli, ["run", *client_args])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout).get("via") == "daemon"
    log = b / ".agentcad" / "session.jsonl"
    runs = [
        entry for entry in map(json.loads, log.read_text().splitlines())
        if entry["command"] == "run"
    ]
    assert [entry["args"]["argv"] for entry in runs] == [client_args], runs
    assert runs[0]["result"]["via"] == "daemon"


def test_daemon_usage_error_does_not_reach_spawning_commands_output(
    runner, two_projects, monkeypatch
):
    """A client newer than the daemon can send an option the daemon doesn't
    know. agentcad reports that after the command's own output state is gone,
    so it must use the request's fresh context. Without it, project B's error
    went through the spawning command's formatter and named project A as its
    project root."""
    a, b = two_projects
    record = {}
    monkeypatch.setattr(
        daemon_mod,
        "spawn_daemon_via_fork",
        _spawn_hook_serving(
            {
                "type": "run",
                "cwd": str(b),
                "argv": ["run", "model.py", "--label", "bee", "--from-a-newer-client"],
            },
            record,
        ),
    )

    result = runner.invoke(cli, ["run", "model.py", "--label", "ay", *RUN_FLAGS])

    assert result.exit_code == 0, result.output
    response = record["response"]
    assert response["exit_code"] != 0
    assert json.loads(response["output"])["error_kind"] == "unknown_option"
    assert str(a) not in response["output"], response["output"]
    assert record["spawning_output_gained"] == [], (
        "the daemon request's output went to the spawning command: "
        f"{record['spawning_output_gained']}"
    )


def test_daemon_request_survives_deleted_spawning_directory(
    runner, tmp_path, monkeypatch
):
    """A daemon spawned from a temp project keeps serving after it is deleted."""
    b = tmp_path / "project_b"
    _init_project(runner, b, monkeypatch)
    gone = tmp_path / "deleted_project_a"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()

    response = DaemonServer().handle_request(
        {
            "type": "run",
            "cwd": str(b),
            "argv": ["run", "model.py", "--label", "bee", *RUN_FLAGS],
        }
    )

    assert response["exit_code"] == 0, response
    assert json.loads(response["output"])["status"] == "success"
