"""Artifact location is a project contract, independent of invocation cwd."""

import json
from pathlib import Path

import pytest

from agentcad.cli import cli


def invoke(runner, *args):
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, (result.output, result.exception)
    return json.loads(result.stdout)


def test_default_layout_unchanged(runner, isolated_dir):
    invoke(runner, "init", "--no-agent-setup")
    assert (isolated_dir / "agentcad.json").is_file()
    assert not (isolated_dir / "agentcad.toml").exists()


def test_help_does_not_log_before_build_override_is_selected(runner, isolated_dir):
    build = isolated_dir / "build"
    invoke(runner, "init", "--build-dir", str(build), "--no-agent-setup")
    result = runner.invoke(cli, ["run", "--build-dir", str(build), "--help"])
    assert result.exit_code == 0
    assert not (isolated_dir / ".agentcad").exists()


def test_config_discovery_from_subdirectory(runner, isolated_dir):
    from agentcad.project import resolve_project

    (isolated_dir / "agentcad.toml").write_text('build_dir = "./build"\n')
    invoke(runner, "init", "--no-agent-setup")
    child = isolated_dir / "sources"
    child.mkdir()
    layout = resolve_project(start=child)
    assert layout.project_root == isolated_dir
    assert layout.build_root == isolated_dir / "build"
    assert not (isolated_dir / "agentcad.json").exists()
    assert layout.manifest_path.is_file()


def test_override_initializes_separate_history_without_editing_config(
    runner, isolated_dir
):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    other = isolated_dir.parent / (isolated_dir.name + "-external")
    data = invoke(runner, "init", "--build-dir", str(other), "--no-agent-setup")
    assert data["build_root"] == str(other)
    assert data["build_root_source"] == "command"
    assert (isolated_dir / "agentcad.toml").read_text() == 'build_dir = "build"\n'
    assert (other / "agentcad.json").is_file()
    assert invoke(runner, "context")["build_root"] == str(isolated_dir / "build")


@pytest.mark.parametrize("contents", ["build_dir = 4", 'build_dir = ""', "bad [toml"])
def test_invalid_config_returns_json_without_state(runner, isolated_dir, contents):
    (isolated_dir / "agentcad.toml").write_text(contents)
    result = runner.invoke(cli, ["init", "--no-agent-setup"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["status"] == "error"
    assert not (isolated_dir / "agentcad.json").exists()
    assert not (isolated_dir / ".agentcad").exists()


def test_missing_selected_manifest_does_not_fall_back(runner, isolated_dir):
    invoke(runner, "init", "--no-agent-setup")
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    result = runner.invoke(cli, ["context"])
    data = json.loads(result.stdout)
    assert result.exit_code == 1
    assert data["reason"] == "build_root_not_initialized"
    assert "--build-dir" in data["suggestion"]


def test_artifact_containment(isolated_dir):
    from agentcad.project import ProjectError, resolve_project

    layout = resolve_project()
    with pytest.raises(ProjectError):
        layout.artifact_path("../escaped/output.step")
    (isolated_dir / "escape").symlink_to(isolated_dir.parent, target_is_directory=True)
    with pytest.raises(ProjectError):
        layout.artifact_path("escape/output.step")


SCRIPTS = {
    "build123d": 'show_object(Box(40, 30, 6) - Cylinder(3, 12), name="plate", id="plate")\n',
    "cadquery": 'show_object(cq.Workplane("XY").box(40,30,6).faces(">Z").workplane().hole(6), name="plate")\n',
}


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
def test_real_build_and_readers_from_nested_directory(
    runner, isolated_dir, monkeypatch, runtime
):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--runtime", runtime, "--no-agent-setup")
    source = isolated_dir / "source"
    source.mkdir()
    (source / "model.py").write_text(SCRIPTS[runtime])
    monkeypatch.chdir(source)
    first = invoke(
        runner,
        "run",
        "model.py",
        "--label",
        "first",
        "--no-daemon",
        "--no-view",
        "--no-diff",
        "--no-preview",
    )
    step = Path(first["outputs"]["step"])
    assert step == isolated_dir / "build/v1_first/output.step"
    assert step.is_file()
    assert first["runtime"] == runtime
    meta = json.loads((step.parent / "meta.json").read_text())
    assert meta["outputs"]["step"] == "v1_first/output.step"
    second = invoke(
        runner,
        "run",
        "model.py",
        "--label",
        "second",
        "--no-daemon",
        "--no-view",
        "--no-diff",
        "--no-preview",
    )
    assert second["version"] == 2
    assert invoke(runner, "context")["version_count"] == 2
    assert (
        invoke(runner, "parts", "list", "current")["outputs"]["step"]
        == second["outputs"]["step"]
    )
    assert (
        invoke(runner, "diff", "first", "second", "--no-daemon")["status"] == "success"
    )
    export = invoke(runner, "export", str(step), "--format", "stl", "--no-daemon")
    assert Path(export["outputs"]["stl"]).is_file()
    assert not (source / ".agentcad").exists()
    assert not (isolated_dir / ".agentcad").exists()
    assert (isolated_dir / "build/.agentcad/session.jsonl").is_file()


def test_import_scaffold_and_recovery_use_external_root(
    runner, isolated_dir, real_world_step
):
    import shlex
    import shutil

    shutil.copy2(real_world_step, isolated_dir / "source.step")
    build = isolated_dir.parent / (isolated_dir.name + "-build")
    data = invoke(
        runner,
        "import",
        "source.step",
        "--init",
        "--build-dir",
        str(build),
        "--label",
        "baseline",
        "--no-view",
        "--no-diff",
        "--no-daemon",
    )
    assert Path(data["viewer"]).is_file()
    assert (
        str(build / "v1_baseline/output.step") in (isolated_dir / "edit.py").read_text()
    )
    edited = invoke(
        runner,
        "run",
        "edit.py",
        "--build-dir",
        str(build),
        "--label",
        "edit",
        "--no-view",
        "--no-diff",
        "--no-preview",
        "--no-daemon",
    )
    assert edited["version"] == 2
    (build / "v2_edit/meta.json").unlink()
    context = invoke(runner, "context", "--build-dir", str(build))
    command = context["recovery"]["candidates"][0]["recovery_command"]
    assert "--build-dir" in command
    recovered = invoke(runner, *shlex.split(command)[1:])
    assert recovered["recovered"] is True
    assert (build / "v2_edit/meta.json").is_file()


def test_unwritable_root_fails_before_execution(runner, isolated_dir, monkeypatch):
    invoke(runner, "init", "--no-agent-setup")
    (isolated_dir / "model.py").write_text(SCRIPTS["build123d"])

    def denied(*args, **kwargs):
        raise PermissionError("test permission denied")

    monkeypatch.setattr("agentcad.project.os.replace", denied)
    result = runner.invoke(cli, ["run", "model.py", "--label", "bad", "--no-daemon"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["reason"] == "build_root_unwritable"
    assert data["artifact_created"] is False
    assert not list(isolated_dir.glob("v*"))


def test_failed_dry_run_does_not_record_diagnostics(runner, isolated_dir):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    (isolated_dir / "model.py").write_text(
        'show_object(Box(10,10,10))\nraise RuntimeError("expected failure")\n'
    )
    build = isolated_dir / "build"
    before = {p: p.read_bytes() for p in build.rglob("*") if p.is_file()}
    result = runner.invoke(
        cli, ["run", "model.py", "--label", "dry", "--dry-run", "--no-daemon"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["artifact_created"] is False
    assert before == {p: p.read_bytes() for p in build.rglob("*") if p.is_file()}


@pytest.mark.parametrize("source_marker", [False, True])
def test_discover_source_from_external_generated_directory(
    runner, isolated_dir, monkeypatch, source_marker
):
    if source_marker:
        (isolated_dir / "agentcad.toml").write_text("")
    build = isolated_dir.parent / (isolated_dir.name + "-external")
    invoke(runner, "init", "--build-dir", str(build), "--no-agent-setup")
    child = build / "v1_example"
    child.mkdir()
    monkeypatch.chdir(child)
    result = invoke(runner, "context")
    assert result["project_root"] == str(isolated_dir)
    assert result["build_root"] == str(build)
    assert result["build_root_source"] == "manifest"
    result = invoke(runner, "init", "--build-dir", "other", "--no-agent-setup")
    assert result["build_root"] == str(isolated_dir / "other")


def test_existing_file_is_not_a_build_root(runner, isolated_dir):
    target = isolated_dir / "not-a-directory"
    target.write_text("keep me")
    result = runner.invoke(
        cli, ["init", "--build-dir", str(target), "--no-agent-setup"]
    )
    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason"] == "invalid_build_root"
    assert target.read_text() == "keep me"


def test_mcp_requests_do_not_leak_project_roots(runner, isolated_dir, monkeypatch):
    from agentcad.mcp import server

    roots = [isolated_dir / name for name in ("project-a", "project-b")]
    for root in roots:
        root.mkdir()
        monkeypatch.chdir(root)
        (root / "agentcad.toml").write_text('build_dir = "build"\n')
        invoke(runner, "init", "--no-agent-setup")
    for root in [*roots, roots[0]]:
        result = server.context(str(root))
        assert result["build_root"] == str(root / "build")
        assert result["project_root"] == str(root)
        assert result["_exit_code"] == 0
    refused = server.context(str(roots[1]), build_dir=str(roots[0] / "build"))
    assert refused["reason"] == "invalid_manifest"


def test_configured_full_review_workflow(runner, isolated_dir):
    from urllib.parse import unquote, urlparse

    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    model = isolated_dir / "model.py"
    model.write_text(
        SCRIPTS["build123d"]
        + 'show_object(Box(4,4,4).translate((30,0,0)), id="spacer", name="Spacer")\n'
    )
    first = invoke(
        runner, "run", "model.py", "--label", "first", "--no-view", "--no-daemon"
    )
    second = invoke(
        runner, "run", "model.py", "--label", "second", "--no-view", "--no-daemon"
    )
    assert Path(second["preview"]).is_file()
    assert Path(second["viewer"]).is_file()
    review = invoke(
        runner, "parts", "view", "current", "--isolate", "plate", "--no-open"
    )
    assert Path(review["review_viewer"]).is_file()
    rendered = invoke(
        runner, "render", first["outputs"]["step"], "--view", "iso", "--no-daemon"
    )
    assert Path(rendered["renders"]["iso"]).is_file()
    diff = invoke(runner, "diff", "first", "second", "--visual", "--no-daemon")
    assert Path(unquote(urlparse(diff["visual"]["url"]).path)).is_relative_to(
        isolated_dir / "build"
    )
    for key in ("png", "overlay_png", "volume_glb", "volume_png"):
        if diff["visual"].get(key):
            assert Path(diff["visual"][key]).is_absolute()
            assert Path(diff["visual"][key]).is_file()
    assert not list(isolated_dir.glob("*.png"))
    assert not list(isolated_dir.glob("*.glb"))
    assert not list(isolated_dir.glob("*.html"))


def test_legacy_two_file_view_accepts_relative_paths(runner, isolated_dir):
    import cadquery as cq

    cq.exporters.export(
        cq.Workplane("XY").box(10, 20, 5), str(isolated_dir / "source.step")
    )
    invoke(runner, "export", "source.step", "--format", "glb", "--no-daemon")
    result = invoke(runner, "view", "source.glb", "source.glb")
    assert result["status"] == "success"
    assert result["url"].startswith("file://")


def test_dry_run_does_not_write_to_build(runner, isolated_dir):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    (isolated_dir / "model.py").write_text(SCRIPTS["build123d"])
    before = {
        p: p.read_bytes() for p in (isolated_dir / "build").rglob("*") if p.is_file()
    }
    data = invoke(
        runner, "run", "model.py", "--label", "dry", "--dry-run", "--no-daemon"
    )
    assert data["outputs"]["step"] is None
    assert before == {
        p: p.read_bytes() for p in (isolated_dir / "build").rglob("*") if p.is_file()
    }


def test_concurrent_reservations_in_configured_root(runner, isolated_dir):
    from concurrent.futures import ThreadPoolExecutor
    from agentcad.project import resolve_project
    from agentcad.versioning import reserve_version, commit_version

    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    layout = resolve_project()

    def build(index):
        r = reserve_version(layout.build_root, f"part-{index}")
        commit_version(
            r,
            {"version": r.number},
            {"version": r.number, "path": r.dir_name + "/"},
            advance_current=True,
        )
        return r.number

    with ThreadPoolExecutor(max_workers=6) as workers:
        numbers = list(workers.map(build, range(12)))
    assert sorted(numbers) == list(range(1, 13))
    assert len(layout.read_manifest()["versions"]) == 12


def test_daemon_forwards_override(runner, isolated_dir, monkeypatch):
    from agentcad.commands import _daemon_routing as routing

    build = isolated_dir / "output"
    invoke(runner, "init", "--build-dir", str(build), "--no-agent-setup")
    seen = []
    monkeypatch.delenv("AGENTCAD_DAEMON")
    monkeypatch.setattr(routing._daemon, "daemon_supported", lambda: True)
    monkeypatch.setattr(
        routing,
        "_route_through_daemon",
        lambda argv: (
            seen.append(argv)
            or {"output": '{"command":"run","status":"success"}', "exit_code": 0}
        ),
    )
    invoke(runner, "run", "model.py", "--label", "first", "--build-dir", str(build))
    assert seen[0][-2:] == ["--build-dir", str(build)]


def test_derived_exports_and_view_do_not_write_beside_source(runner, isolated_dir):
    import cadquery as cq

    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    cq.exporters.export(
        cq.Workplane("XY").box(10, 20, 5), str(isolated_dir / "source.step")
    )
    output = invoke(runner, "export", "source.step", "--format", "glb", "--no-daemon")
    assert Path(output["outputs"]["glb"]).is_relative_to(isolated_dir / "build/derived")
    viewer = invoke(runner, "view", "source.step")
    assert Path(viewer["model"]).is_relative_to(isolated_dir / "build/derived")
    assert not (isolated_dir / "source.glb").exists()
    assert not (isolated_dir / "source_viewer.html").exists()


def test_mcp_build_dir_parameter(runner, isolated_dir):
    from agentcad.mcp import server

    build = isolated_dir / "build"
    invoke(
        runner,
        "init",
        "--build-dir",
        str(build),
        "--runtime",
        "cadquery",
        "--no-agent-setup",
    )
    result = server.context(str(isolated_dir), build_dir=str(build))
    assert result["build_root"] == str(build)
    assert result["_exit_code"] == 0
    docs = server.docs("quickstart", cwd=str(isolated_dir), build_dir=str(build))
    assert docs["runtime"] == "cadquery"
    assert docs["build_root"] == str(build)


def test_daemon_handler_uses_override_and_restores_cwd(runner, isolated_dir):
    from agentcad.daemon import DaemonServer

    build = isolated_dir / "artifacts"
    invoke(runner, "init", "--build-dir", str(build), "--no-agent-setup")
    source = isolated_dir / "source"
    source.mkdir()
    (source / "model.py").write_text(SCRIPTS["build123d"])
    # A source marker makes relative overrides independent of the request cwd.
    (isolated_dir / "agentcad.toml").write_text('build_dir = "unused"\n')
    response = DaemonServer().handle_request(
        {
            "type": "run",
            "cwd": str(source),
            "argv": [
                "run",
                "model.py",
                "--label",
                "daemon",
                "--build-dir",
                "artifacts",
                "--no-view",
                "--no-diff",
                "--no-preview",
            ],
        }
    )
    assert response["exit_code"] == 0, response
    output = json.loads(response["output"])
    assert Path(output["outputs"]["step"]) == build / "v1_daemon/output.step"
    assert Path(output["outputs"]["step"]).is_file()
    assert Path.cwd() == isolated_dir
    assert not (source / "artifacts").exists()
    assert not (isolated_dir / "unused").exists()


def test_layout_is_frozen_before_script_changes_configuration(runner, isolated_dir):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    (isolated_dir / "model.py").write_text(
        "from pathlib import Path\n"
        'Path("agentcad.toml").write_text(\'build_dir = "changed"\\n\')\n'
        + SCRIPTS["build123d"]
    )
    output = invoke(
        runner,
        "run",
        "model.py",
        "--label",
        "first",
        "--no-daemon",
        "--no-view",
        "--no-preview",
        "--no-diff",
    )
    assert output["build_root"] == str(isolated_dir / "build")
    assert Path(output["outputs"]["step"]).is_file()
    assert not (isolated_dir / "changed").exists()


def test_failure_and_invalid_import_keep_diagnostics_inside_build(runner, isolated_dir):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "build"\n')
    invoke(runner, "init", "--no-agent-setup")
    (isolated_dir / "model.py").write_text(SCRIPTS["build123d"])
    invoke(
        runner,
        "run",
        "model.py",
        "--label",
        "good",
        "--no-daemon",
        "--no-view",
        "--no-preview",
        "--no-diff",
    )
    (isolated_dir / "model.py").write_text(
        'show_object(Box(10,10,10))\nraise RuntimeError("expected failure")\n'
    )
    failed = runner.invoke(cli, ["run", "model.py", "--label", "bad", "--no-daemon"])
    assert failed.exit_code == 1
    failed_data = json.loads(failed.stdout)
    assert failed_data["status"] == "failed"
    assert Path(failed_data["path"]) == isolated_dir / "build/v2_bad_failed"
    invalid = Path(__file__).parent / "fixtures/validation/bowtie_prism_invalid.brep"
    result = runner.invoke(
        cli, ["import", str(invalid), "--label", "invalid", "--no-daemon", "--no-view"]
    )
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "invalid_geometry"
    assert data["outputs"].get("step") is None
    assert Path(data["outputs"]["source"]).is_file()
    context = invoke(runner, "context")
    assert context["current"] == "good"
    assert context["version_count"] == 3
    assert not list(isolated_dir.glob("v*"))


def test_nested_project_marker_wins_over_parent(runner, isolated_dir, monkeypatch):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "parent-build"\n')
    invoke(runner, "init", "--no-agent-setup")
    child = isolated_dir / "child"
    child.mkdir()
    (child / "agentcad.toml").write_text('build_dir = "child-build"\n')
    monkeypatch.chdir(child)
    output = invoke(runner, "init", "--no-agent-setup")
    assert output["project_root"] == str(child)
    assert output["build_root"] == str(child / "child-build")


def test_help_and_installed_guidance_explain_build_roots(runner, isolated_dir):
    (isolated_dir / "AGENTS.md").write_text("Existing project instructions\n")
    (isolated_dir / "CLAUDE.md").write_text("Existing project instructions\n")
    invoke(runner, "init")
    assert "--build-dir" in runner.invoke(cli, ["run", "--help"]).stdout
    for guide in ("AGENTS.md", "CLAUDE.md", ".claude/skills/agentcad/SKILL.md"):
        contents = (isolated_dir / guide).read_text()
        assert "agentcad.toml" in contents
        assert "agentcad docs artifacts" in contents
    for section in ("quickstart", "commands", "editing", "schema"):
        assert "docs artifacts" in invoke(runner, "docs", section)["content"]
