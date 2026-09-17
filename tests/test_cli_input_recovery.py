"""Cheap input mistakes return recovery before daemon or CAD startup.

# collect-on-default-profile
"""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.cli import cli


@pytest.fixture
def project(runner, isolated_dir):
    result = runner.invoke(cli, ["init", "--no-agent-setup"])
    assert result.exit_code == 0, result.output
    (isolated_dir / "model.py").write_text("show_object(Box(10, 20, 5))\n")
    return isolated_dir


@pytest.fixture
def forbid_startup(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid input reached daemon routing/startup or CAD resolution")

    for command in ("run", "import_cmd", "render", "export_cmd"):
        monkeypatch.setattr(
            f"agentcad.commands.{command}.maybe_route_through_daemon", unexpected
        )
    monkeypatch.setattr("agentcad.daemon.spawn_daemon_via_fork", unexpected)
    monkeypatch.setattr("agentcad.runners.dispatch.resolve", unexpected)


@pytest.mark.parametrize(
    "args, expected",
    [
        (["model.py"], "--label names this version"),
        (["model.py", "--label", "v1", "--runtime", "python"], "This project uses build123d"),
        (["model.py", "--label", "v1", "--export", "step"], "outputs.step"),
        (["model.py", "--label", "v1", "--export", "fbx"], "Supported: stl, glb, obj"),
        (["model.py", "--label", "v1", "--export", ","], "No export formats"),
        (["missing.py", "--label", "v1"], "not found"),
        (["missing.step", "--label", "v1"], "not found"),
        (["build.sh", "--label", "v1"], "Python CAD script (.py)"),
        (["directory.py", "--label", "v1"], "is not a file"),
        (["binary.py", "--label", "v1"], "Cannot read Python script"),
        (["model.py", "--label", "output/model.step"], "outputs.step"),
        (["model.py", "--label", "v1", "--build-dir", "model.py"], "existing file"),
        (["model.py", "--label", "v1", "--build-dir", "model.py/nested"], "existing file"),
        (["model.py", "--label", "v1", "--build-dir", "missing"], "No AgentCAD manifest"),
    ],
)
def test_run_input_errors_are_local(runner, project, forbid_startup, args, expected):
    (project / "build.sh").write_text("#!/bin/sh\necho hello\n")
    (project / "directory.py").mkdir()
    (project / "binary.py").write_bytes(b"\xff\xfe")
    result = runner.invoke(cli, ["run", *args])
    assert result.exit_code != 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert expected in payload["message"] or expected in payload.get("suggestion", "")
    assert payload["next_actions"]
    assert payload["outputs"]["step"] is None
    assert payload["artifact_created"] is False
    if args[0] == "missing.step":
        assert payload["next_actions"] == ["agentcad import --help"]
    if "output/model.step" in args:
        recovery = shlex.split(payload["next_actions"][0])
        assert recovery[recovery.index("--label") + 1] == "first"
        assert "output/model.step" not in recovery
    assert json.loads((project / "agentcad.json").read_text())["versions"] == []
    assert not list(project.glob("v[0-9]*"))


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
@pytest.mark.parametrize("option_style", ["separate", "equals"])
def test_runtime_hint_uses_selected_build_root(
    runner, project, forbid_startup, runtime, option_style
):
    build = project / "other history"
    build.mkdir()
    (build / "agentcad.json").write_text(json.dumps({"runtime": runtime, "versions": []}))
    option = ["--build-dir", str(build)] if option_style == "separate" else [f"--build-dir={build}"]
    result = runner.invoke(cli, ["run", "model.py", "--label", "v1", "--runtime", "python", *option])
    payload = json.loads(result.stdout)
    assert f"This project uses {runtime} (--runtime {runtime})" in payload["message"]
    recovery = shlex.split(payload["next_actions"][0])
    assert "--runtime" not in recovery
    assert recovery[recovery.index("--build-dir") + 1] == str(build)


def test_runtime_hint_without_manifest_is_accurate(runner, isolated_dir, forbid_startup):
    payload = json.loads(runner.invoke(cli, ["run", "model.py", "--label", "v1", "--runtime", "python"]).stdout)
    assert "detect source syntax" in payload["message"]
    assert "default to build123d" in payload["message"]


@pytest.mark.parametrize("formats", ["step", "step,stl,fbx", ","])
def test_export_correction_keeps_other_options(runner, project, forbid_startup, formats):
    result = runner.invoke(cli, [
        "run", "model.py", "--label=-v 1", "--export", formats,
        "--render=-45:30", "--params", "width=4", "--no-preview", "--no-view",
        "--no-diff", "--validation-profile", "kernel", "--no-daemon",
    ])
    recovery = shlex.split(json.loads(result.stdout)["next_actions"][0])
    assert "--label=-v 1" in recovery
    assert "--render=-45:30" in recovery
    assert recovery[recovery.index("--params") + 1] == "width=4"
    for flag in ("--no-preview", "--no-view", "--no-diff", "--no-daemon"):
        assert flag in recovery
    assert recovery[recovery.index("--validation-profile") + 1] == "kernel"
    if "stl" in formats:
        assert recovery[recovery.index("--export") + 1] == "stl"
    else:
        assert "--export" not in recovery


def test_corrected_shell_script_command_builds_in_selected_directory(runner, project):
    result = runner.invoke(cli, ["init", "--build-dir", "build artifacts", "--no-agent-setup"])
    assert result.exit_code == 0
    (project / "build.sh").write_text("#!/bin/sh\n")
    result = runner.invoke(cli, [
        "run", "build.sh", "--label", "first build", "--build-dir", "build artifacts",
        "--no-preview", "--no-view", "--no-diff", "--no-daemon",
    ])
    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "invalid_script_type"
    recovery = runner.invoke(cli, shlex.split(payload["next_actions"][0])[1:])
    assert recovery.exit_code == 0, recovery.output
    outcome = json.loads(recovery.stdout)
    assert outcome["status"] == "success"
    assert Path(outcome["outputs"]["step"]).is_file()
    assert Path(outcome["outputs"]["step"]).is_relative_to(project / "build artifacts")


def test_missing_manifest_preserves_run_contract(runner, isolated_dir, forbid_startup):
    (isolated_dir / "model.py").write_text("show_object(Box(1, 2, 3))")
    result = runner.invoke(cli, ["run", "model.py", "--label", "first"])
    payload = json.loads(result.stdout)
    assert payload["next_actions"] == ["agentcad init"]
    assert payload["label"] == "first"
    assert payload["outputs"]["step"] is None
    assert payload["artifact_created"] is False


@pytest.mark.parametrize("command, options", [("render", ["--view", "iso"]), ("export", ["--format", "stl"])])
@pytest.mark.parametrize("path", ["missing.step", "directory.step"])
def test_missing_outputs_are_rejected_before_routing(runner, project, forbid_startup, command, options, path):
    (project / "directory.step").mkdir()
    result = runner.invoke(cli, [command, path, *options])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "outputs.step" in payload["message"]
    assert payload["next_actions"] == ["agentcad context"]


@pytest.mark.parametrize("formats", ["step", "fbx", ","])
def test_standalone_export_formats_rejected_before_routing(runner, project, forbid_startup, formats):
    (project / "output.step").write_text("unread until after argument validation")
    result = runner.invoke(cli, ["export", "output.step", "--format", formats])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["next_actions"]
    if formats == "step":
        assert "outputs.step" in payload["message"]


def test_invalid_inputs_do_not_import_cad_in_a_cold_process(project):
    (project / "build.sh").write_text("#!/bin/sh\n")
    script = """
import sys
from click.testing import CliRunner
from agentcad.cli import cli
for args in (
    ['run', 'model.py'],
    ['run', 'model.py', '--label', 'first', '--runtime', 'python'],
    ['run', 'build.sh', '--label', 'first'],
    ['run', 'missing.py', '--label', 'first'],
    ['run', 'model.py', '--label', 'first', '--export', 'step'],
    ['render', 'missing.step', '--view', 'iso'],
    ['export', 'missing.step', '--format', 'stl'],
):
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0, result.output
loaded = {name.split('.')[0] for name in sys.modules}
assert not loaded.intersection({'OCP', 'build123d', 'cadquery'}), loaded
"""
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=project, env=env,
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
