"""Runtime precedence and recovery must work on the default install too."""
# collect-on-default-profile

import importlib
import json

import pytest

from agentcad.cli import cli
from agentcad.runners import dispatch


MIXED = "import cadquery as cq\nfrom build123d import Box\nshow_object(Box(3, 4, 5))\n"
B3D = "from build123d import Box\nshow_object(Box(3, 4, 5))\n"


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
@pytest.mark.parametrize("source", [MIXED, "show_object(cq.Workplane('XY'))\n", B3D, ""])
def test_project_selected_before_source_detection(runtime, source):
    assert dispatch.select_runtime(source, project_default=runtime) == (runtime, "project")


@pytest.mark.parametrize("runtime,project", [("build123d", "cadquery"), ("cadquery", "build123d")])
def test_command_beats_project_and_mixed_imports(runtime, project, monkeypatch):
    engine = object()
    monkeypatch.setattr(dispatch, "get_runner", lambda name: engine)
    assert dispatch.select_runtime(MIXED, runtime, project) == (runtime, "command")
    assert dispatch.resolve(MIXED, runtime, project) == (runtime, engine)


@pytest.mark.parametrize("source,expected", [
    (B3D, "build123d"),
    ("import cadquery as cq\n", "cadquery"),
    ("show_object(cq.Workplane('XY'))\n", "cadquery"),
    ("show_object(Box(1, 1, 1))\n", "build123d"),
    ('# import cadquery\nmessage = "cq.Workplane"\n', "build123d"),
])
def test_unpinned_detection(source, expected):
    assert dispatch.select_runtime(source) == (expected, "detection")


def test_unpinned_mixed_imports_remain_ambiguous():
    with pytest.raises(ValueError, match="runtime ambiguous"):
        dispatch.resolve(MIXED)


@pytest.mark.parametrize("runtime,other", [("build123d", "cadquery"), ("cadquery", "build123d")])
def test_resolve_mixed_project_is_mismatch_before_engine_load(runtime, other, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Conflicting source must be rejected before importing an engine")

    monkeypatch.setattr(dispatch, "get_runner", unexpected)
    with pytest.raises(ValueError, match=f"project uses {runtime}") as error:
        dispatch.resolve(MIXED, project_default=runtime)
    assert "ambiguous" not in str(error.value)
    assert f"Remove the {other} imports and API usage" in str(error.value)
    assert f"docs preamble --runtime {runtime}" in str(error.value)


@pytest.mark.parametrize("project,expected_source", [("build123d", "project"), ("cadquery", "project"), (None, "detection")])
def test_conflict_cli_is_structured_and_has_no_side_effects(
    runner, isolated_dir, monkeypatch, project, expected_source
):
    manifest = {"name": "test", "versions": []}
    if project:
        manifest["runtime"] = project
    path = isolated_dir / "agentcad.json"
    path.write_text(json.dumps(manifest))
    original = path.read_bytes()
    (isolated_dir / "mixed.py").write_text(MIXED)
    run_module = importlib.import_module("agentcad.commands.run")

    def unexpected(*args, **kwargs):
        pytest.fail("Conflict must be rejected before engine import or daemon contact/startup")

    monkeypatch.setattr(dispatch, "get_runner", unexpected)
    monkeypatch.setattr(run_module, "maybe_route_through_daemon", unexpected)
    monkeypatch.setattr(run_module, "maybe_spawn_daemon_for_next_run", unexpected)
    result = runner.invoke(cli, ["run", "mixed.py", "--label", "edit"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["runtime_source"] == expected_source
    assert data["artifact_created"] is False
    if project:
        assert data["runtime"] == project
        assert "runtime mismatch" in data["message"]
        assert "ambiguous" not in data["message"]
    else:
        assert "runtime" not in data
        assert "runtime ambiguous" in data["message"]
    assert path.read_bytes() == original
    assert not list(isolated_dir.glob("v*_*"))


@pytest.mark.parametrize("mode,expected_source", [
    ("project", "project"), ("override", "command"),
    ("detected", "detection"), ("fallback", "detection"),
])
def test_run_reports_and_records_runtime_source(runner, isolated_dir, mode, expected_source):
    init = runner.invoke(cli, ["init", "--runtime", "build123d"])
    assert init.exit_code == 0, init.output
    path = isolated_dir / "agentcad.json"
    manifest = json.loads(path.read_text())
    if mode in {"detected", "fallback"}:
        manifest.pop("runtime")
    elif mode == "override":
        manifest["runtime"] = "cadquery"
    path.write_text(json.dumps(manifest))
    source = B3D if mode != "fallback" else "show_object(Box(3, 4, 5))\n"
    (isolated_dir / "model.py").write_text(source)
    args = ["run", "model.py", "--label", "edit", "--no-preview", "--no-diff", "--no-view", "--no-daemon"]
    if mode == "override":
        args += ["--runtime", "build123d"]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["runtime"] == "build123d"
    assert data["runtime_source"] == expected_source
    assert data["metrics"]["volume"] == pytest.approx(60)
    meta = json.loads((isolated_dir / "v1_edit" / "meta.json").read_text())
    assert meta["runtime_source"] == expected_source
    assert json.loads(path.read_text()).get("runtime") == manifest.get("runtime")


@pytest.mark.parametrize("source,status,recorded", [
    ("show_object(Box(1, 1,\n", "validation_error", False),
    ("raise RuntimeError('broken edit')\nshow_object(Box(1, 1, 1))\n", "failed", True),
])
def test_errors_keep_runtime_source(runner, isolated_dir, source, status, recorded):
    runner.invoke(cli, ["init", "--runtime", "build123d"])
    (isolated_dir / "model.py").write_text(source)
    result = runner.invoke(cli, ["run", "model.py", "--label", "edit", "--no-daemon"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["status"] == status
    assert data["runtime"] == "build123d"
    assert data["runtime_source"] == "project"
    if recorded:
        meta = json.loads((isolated_dir / "v1_edit_failed" / "meta.json").read_text())
        assert meta["runtime_source"] == "project"


def test_fixing_stray_import_builds_first_version(runner, isolated_dir):
    runner.invoke(cli, ["init", "--runtime", "build123d"])
    script = isolated_dir / "model.py"
    script.write_text(MIXED)
    args = ["run", "model.py", "--label", "edit", "--no-preview", "--no-diff", "--no-view", "--no-daemon"]
    rejected = runner.invoke(cli, args)
    assert rejected.exit_code == 1
    assert "Remove the cadquery imports" in json.loads(rejected.stdout)["message"]
    script.write_text(B3D)
    repaired = runner.invoke(cli, args)
    assert repaired.exit_code == 0, repaired.output
    data = json.loads(repaired.stdout)
    assert data["version"] == 1
    assert data["runtime_source"] == "project"
    assert (isolated_dir / data["outputs"]["step"]).exists()


@pytest.mark.parametrize("override", [False, True])
def test_selected_build_root_pin_applies_from_subdirectory(
    runner, isolated_dir, monkeypatch, override
):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    init = runner.invoke(cli, ["init", "--runtime", "build123d", "--no-agent-setup"])
    assert init.exit_code == 0, init.output
    source_dir = isolated_dir / "src"
    source_dir.mkdir()
    (source_dir / "model.py").write_text(B3D)
    # Even a different pin in an explicitly selected build root must win.
    args = ["run", "model.py", "--dry-run", "--no-daemon"]
    if override:
        alternate = runner.invoke(cli, ["init", "--build-dir", "alternate", "--no-agent-setup"])
        assert alternate.exit_code == 0, alternate.output
        path = isolated_dir / "alternate" / "agentcad.json"
        manifest = json.loads(path.read_text())
        manifest["runtime"] = "cadquery"
        path.write_text(json.dumps(manifest))
        args += ["--build-dir", "alternate"]
    monkeypatch.chdir(source_dir)
    result = runner.invoke(cli, args)
    data = json.loads(result.stdout)
    assert data["runtime_source"] == "project"
    if override:
        assert result.exit_code == 1, result.output
        assert data["runtime"] == "cadquery"
        assert "runtime mismatch" in data["message"]
    else:
        assert result.exit_code == 0, result.output
        assert data["runtime"] == "build123d"


def test_command_override_of_mixed_script_reaches_selected_runner(
    runner, isolated_dir, monkeypatch
):
    from agentcad.runners import ExecutionResult

    runner.invoke(cli, ["init", "--runtime", "build123d"])
    (isolated_dir / "model.py").write_text(MIXED)
    called = []

    class Engine:
        def validate(self, source):
            called.append(source)
            return []

        def execute(self, source, params):
            # Stop before geometry work; even on a build123d-only install,
            # prove mixed source reaches the explicitly selected runner.
            return ExecutionResult(status="validation_error", exception="reached engine")

    monkeypatch.setattr(dispatch, "get_runner", lambda name: Engine())
    result = runner.invoke(cli, ["run", "model.py", "--dry-run", "--runtime", "build123d", "--no-daemon"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["message"] == "reached engine"
    assert data["runtime"] == "build123d"
    assert data["runtime_source"] == "command"
    assert called == [MIXED]
