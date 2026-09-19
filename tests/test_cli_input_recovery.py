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
    if payload.get("reason") == "invalid_build_root":
        assert "next_actions" not in payload
        assert payload["suggestion"]
    else:
        assert payload["next_actions"]
        assert all(shlex.split(action)[0] == "agentcad" for action in payload["next_actions"])
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
    assert payload["next_actions"] == ["agentcad docs artifacts"]


@pytest.mark.parametrize("formats", ["step", "fbx", ","])
@pytest.mark.parametrize("missing", [False, True])
def test_standalone_export_formats_rejected_before_routing(
    runner, project, forbid_startup, monkeypatch, formats, missing
):
    if not missing:
        (project / "output.step").write_text("unread until after argument validation")

    def no_recovery(*args, **kwargs):
        pytest.fail("Invalid format reached missing-path recovery")

    monkeypatch.setattr("agentcad.commands.export_cmd.missing_step_payload", no_recovery)
    result = runner.invoke(cli, ["export", "output.step", "--format", formats])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    expected = "No export formats" if formats == "," else "Unsupported format(s)"
    assert expected in payload["message"]
    assert payload["next_actions"] == ["agentcad export --help"]
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


@pytest.mark.parametrize("command", ["render", "export"])
@pytest.mark.parametrize("build_dir", [None, "build artifacts"])
def test_missing_step_action_executes_with_recorded_path_and_options(
    runner, project, command, build_dir
):
    selection = ["--build-dir", build_dir] if build_dir else []
    if build_dir:
        result = runner.invoke(cli, ["init", "--no-agent-setup", *selection])
        assert result.exit_code == 0, result.output
    result = runner.invoke(cli, [
        "run", "model.py", "--label", "first", "--no-preview", "--no-view",
        "--no-diff", "--no-daemon", *selection,
    ])
    assert result.exit_code == 0, result.output
    original = Path(json.loads(result.stdout)["outputs"]["step"]).resolve()
    # A conventional v1_first/output.step guess must fail this regression.
    recorded = original.with_name("actual geometry.step")
    original.rename(recorded)
    build_root = project / build_dir if build_dir else project
    meta_path = recorded.parent / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["outputs"]["step"] = recorded.relative_to(build_root).as_posix()
    meta_path.write_text(json.dumps(meta))
    manifest_before = (build_root / "agentcad.json").read_bytes()
    options = (
        ["--view=-45:30", "--zoom", "1.4", "--size", "320x240", "--msaa", "2",
         "--focus=-1,2,3", "--no-fit", "--name", "review angle", "--highlight", "validation"]
        if command == "render" else ["--format", "stl,obj"]
    )
    result = runner.invoke(cli, [command, "guessed.step", *options, "--no-daemon", *selection])
    assert result.exit_code == 1, result.output
    action = json.loads(result.stdout)["next_actions"][0]
    argv = shlex.split(action)
    assert argv[:3] == ["agentcad", command, str(recorded)]
    assert "--no-daemon" in argv
    if build_dir:
        assert argv[argv.index("--build-dir") + 1] == str(build_root)
    if command == "render":
        assert set(argv[3:]).issuperset({
            "--view=-45:30", "--zoom=1.4", "--size=320x240", "--msaa=2",
            "--focus=-1,2,3", "--no-fit", "--name=review angle", "--highlight=validation",
        })
    else:
        assert "--format=stl,obj" in argv
    retried = runner.invoke(cli, argv[1:])  # Follow literally; don't repair the hint.
    assert retried.exit_code == 0, retried.output
    payload = json.loads(retried.stdout)
    assert payload["status"] == "success"
    outputs = payload["renders"] if command == "render" else payload["outputs"]
    for output in outputs.values():
        assert Path(output).is_file()
        assert Path(output).resolve().is_relative_to(build_root)
    if command == "render":
        from PIL import Image
        with Image.open(next(iter(outputs.values()))) as rendered:
            assert rendered.size == (320, 240)
        assert payload["highlight"] == "validation"
    else:
        assert set(outputs) == {"stl", "obj"}
    assert (build_root / "agentcad.json").read_bytes() == manifest_before


@pytest.mark.parametrize("broken", ["missing", "invalid", "no_step", "deleted", "failed", "outside"])
def test_missing_step_does_not_guess_when_current_metadata_is_unusable(
    runner, project, forbid_startup, broken
):
    version = project / "v1_first"
    version.mkdir()
    step = version / "output.step"
    step.write_text("must not be opened as CAD")
    manifest = json.loads((project / "agentcad.json").read_text())
    manifest.update(current="first", versions=[{
        "version": 1, "label": "first", "status": "success", "path": "v1_first/",
    }])
    (project / "agentcad.json").write_text(json.dumps(manifest))
    meta = {"status": "success", "outputs": {"step": "v1_first/output.step"}}
    if broken == "no_step":
        meta["outputs"].pop("step")
    elif broken == "deleted":
        step.unlink()
    elif broken == "failed":
        meta["status"] = "failed"
    elif broken == "outside":
        meta["outputs"]["step"] = "../outside.step"
    if broken != "missing":
        (version / "meta.json").write_text("not JSON" if broken == "invalid" else json.dumps(meta))
    result = runner.invoke(cli, ["export", "missing.step", "--format", "stl"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert "No existing outputs.step" in payload["message"]
    assert payload["next_actions"] == ["agentcad docs artifacts"]


def test_project_error_keeps_prose_out_of_next_actions():
    from agentcad.project import ProjectError
    payload = ProjectError("unfixable", "Bad path", "Choose a writable directory.").payload("run")
    assert payload["suggestion"] == "Choose a writable directory."
    assert "next_actions" not in payload


def test_missing_build_manifest_action_can_be_executed(runner, project):
    args = ["run", "model.py", "--label", "first", "--build-dir", "new history",
            "--no-preview", "--no-diff", "--no-view", "--no-daemon"]
    failed = runner.invoke(cli, args)
    payload = json.loads(failed.stdout)
    assert payload["reason"] == "build_root_not_initialized"
    action = payload["next_actions"][0]
    initialized = runner.invoke(cli, shlex.split(action)[1:])
    assert initialized.exit_code == 0, initialized.output
    retried = runner.invoke(cli, args)
    assert retried.exit_code == 0, retried.output
    assert Path(json.loads(retried.stdout)["outputs"]["step"]).is_file()


def test_missing_step_retry_uses_current_success_not_latest_attempt(
    runner, project, forbid_startup
):
    entries = []
    for number, label, status in [(1, "first", "success"), (2, "newer", "success"), (3, "first", "failed")]:
        directory = project / f"v{number}_{label}"
        directory.mkdir()
        step = directory / "recorded.step"
        step.write_text("metadata lookup must not parse CAD")
        (directory / "meta.json").write_text(json.dumps({
            "status": status, "outputs": {"step": step.relative_to(project).as_posix()},
        }))
        entries.append({"version": number, "label": label, "status": status, "path": directory.name})
    manifest = json.loads((project / "agentcad.json").read_text())
    manifest.update(current="first", versions=entries)
    (project / "agentcad.json").write_text(json.dumps(manifest))
    result = runner.invoke(cli, ["export", "missing.step", "--format", "stl", "--no-daemon"])
    assert result.exit_code == 1
    action = json.loads(result.stdout)["next_actions"][0]
    assert shlex.split(action)[2] == str(project / "v1_first/recorded.step")


@pytest.mark.parametrize("command, options", [
    ("render", ["--view", "iso"]), ("export", ["--format", "stl"]),
])
@pytest.mark.parametrize("reference", ["direct", "file_symlink", "directory_symlink"])
def test_missing_step_rejects_cross_version_metadata(
    runner, project, forbid_startup, command, options, reference
):
    first = project / "v1_first"
    newer = project / "v2_newer"
    first.mkdir()
    newer.mkdir()
    other_step = newer / "newer.step"
    other_step.write_text("must not render or export this other version")
    # Even a conventional local file is not a fallback for corrupted metadata.
    (first / "output.step").write_text("must not guess this file either")
    if reference == "direct":
        recorded = other_step
    else:
        link = first / "linked"
        try:
            if reference == "file_symlink":
                link.symlink_to(other_step)
                recorded = link
            else:
                link.symlink_to(newer, target_is_directory=True)
                recorded = link / "newer.step"
        except OSError as exc:
            pytest.skip(f"Symlinks unavailable: {exc}")
    manifest = json.loads((project / "agentcad.json").read_text())
    manifest.update(current="first", versions=[
        {"version": 1, "label": "first", "status": "success", "path": "v1_first/"},
        {"version": 2, "label": "newer", "status": "success", "path": "v2_newer/"},
    ])
    (project / "agentcad.json").write_text(json.dumps(manifest))
    (first / "meta.json").write_text(json.dumps({
        "status": "success", "outputs": {"step": recorded.relative_to(project).as_posix()},
    }))
    result = runner.invoke(cli, [command, "missing.step", *options])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert "No existing outputs.step" in payload["message"]
    assert payload["next_actions"] == ["agentcad docs artifacts"]
    followed = runner.invoke(cli, shlex.split(payload["next_actions"][0])[1:])
    assert followed.exit_code == 0, followed.output
