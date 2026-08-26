import json

from click.testing import CliRunner
from agentcad import __version__
from agentcad.cli import cli


def test_context_no_manifest_error(runner, isolated_dir):
    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "context"
    assert data["status"] == "error"


def test_context_empty_project(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "myproject"])
    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert data["project"] == "myproject"
    assert data["version_count"] == 0
    assert data["current"] is None
    assert data["recovery"] == {
        "status": "clean",
        "candidate_count": 0,
        "recoverable_count": 0,
        "candidates": [],
    }


def test_context_with_versions(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "proj"])
    # Write manifest with 2 versions
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    manifest["versions"] = [
        {"version": 1, "label": "box", "status": "success", "path": "v1_box/"},
        {"version": 2, "label": "cyl", "status": "success", "path": "v2_cyl/"},
    ]
    manifest["current"] = "cyl"
    (isolated_dir / "agentcad.json").write_text(json.dumps(manifest, indent=2))

    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["version_count"] == 2
    assert data["current"] == "cyl"


def test_context_includes_versions_summary(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "proj"])
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    manifest["versions"] = [
        {"version": 1, "label": "box", "status": "success", "path": "v1_box/"},
        {"version": 2, "label": "cyl", "status": "failed", "path": "v2_cyl_failed/"},
    ]
    manifest["current"] = "box"
    (isolated_dir / "agentcad.json").write_text(json.dumps(manifest, indent=2))

    result = runner.invoke(cli, ["context"])
    data = json.loads(result.stdout)
    versions = data["versions"]
    assert len(versions) == 2
    assert versions[0]["version"] == 1
    assert versions[0]["label"] == "box"
    assert versions[0]["status"] == "success"
    assert versions[1]["version"] == 2
    assert versions[1]["label"] == "cyl"
    assert versions[1]["status"] == "failed"


def test_context_versions_include_path(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "proj"])
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    manifest["versions"] = [
        {"version": 1, "label": "box", "status": "success", "path": "v1_box/"},
    ]
    manifest["current"] = "box"
    (isolated_dir / "agentcad.json").write_text(json.dumps(manifest, indent=2))

    result = runner.invoke(cli, ["context"])
    data = json.loads(result.stdout)
    assert data["versions"][0]["path"] == "v1_box/"


def test_context_includes_tool_version(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "proj"])
    result = runner.invoke(cli, ["context"])
    data = json.loads(result.stdout)
    assert "tool_version" in data
    assert data["tool_version"] == __version__


def test_context_reports_unregistered_core_step_as_recoverable(
    runner, isolated_dir
):
    import cadquery as cq
    from cadquery import exporters

    runner.invoke(cli, ["init", "--name", "proj"])
    orphan = isolated_dir / "v1_interrupted_box"
    orphan.mkdir()
    exporters.export(cq.Workplane("XY").box(10, 10, 10), str(orphan / "output.step"))
    manifest_path = isolated_dir / "agentcad.json"
    manifest_before = manifest_path.read_bytes()
    step_before = (orphan / "output.step").read_bytes()

    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    recovery = data["recovery"]
    assert recovery["status"] == "needed"
    assert recovery["candidate_count"] == 1
    assert recovery["recoverable_count"] == 1
    assert recovery["candidates"] == [{
        "version": 1,
        "label": "interrupted_box",
        "path": "v1_interrupted_box/",
        "step": "v1_interrupted_box/output.step",
        "issues": ["missing_manifest_entry", "missing_metadata"],
        "recoverable": True,
        "recovery_command": "agentcad recover v1_interrupted_box",
    }]
    assert manifest_path.read_bytes() == manifest_before
    assert (orphan / "output.step").read_bytes() == step_before
    assert not (orphan / "meta.json").exists()


def test_context_reports_interrupted_directory_without_core_step(
    runner, isolated_dir
):
    runner.invoke(cli, ["init", "--name", "proj"])
    interrupted = isolated_dir / "v1_never_exported"
    interrupted.mkdir()
    (interrupted / "script.py").write_text("raise RuntimeError('stopped')\n")

    result = runner.invoke(cli, ["context"])
    data = json.loads(result.stdout)
    candidate = data["recovery"]["candidates"][0]
    assert candidate["issues"] == [
        "missing_manifest_entry", "missing_metadata", "missing_core_step"
    ]
    assert candidate["recoverable"] is False
    assert "recovery_command" not in candidate
    assert interrupted.exists()


def test_context_reports_registered_version_with_missing_metadata(
    runner, isolated_dir
):
    import cadquery as cq
    from cadquery import exporters

    runner.invoke(cli, ["init", "--name", "proj"])
    version_dir = isolated_dir / "v1_box"
    version_dir.mkdir()
    exporters.export(
        cq.Workplane("XY").box(10, 10, 10),
        str(version_dir / "output.step"),
    )
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 1,
        "label": "box",
        "status": "success",
        "path": "v1_box/",
    }]
    manifest_path.write_text(json.dumps(manifest))

    result = runner.invoke(cli, ["context"])
    candidate = json.loads(result.stdout)["recovery"]["candidates"][0]
    assert candidate["issues"] == ["missing_metadata"]
    assert candidate["recoverable"] is True


def test_context_does_not_flag_recorded_failure_diagnostics_for_recovery(
    runner, isolated_dir
):
    runner.invoke(cli, ["init", "--name", "proj"])
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [
        {
            "version": 1,
            "label": "script_error",
            "status": "failed",
            "path": "v1_script_error_failed/",
        },
        {
            "version": 2,
            "label": "open_shell",
            "status": "invalid_geometry",
            "path": "v2_open_shell_invalid/",
        },
    ]
    manifest_path.write_text(json.dumps(manifest))
    for entry in manifest["versions"]:
        version_dir = isolated_dir / entry["path"].rstrip("/")
        version_dir.mkdir()
        (version_dir / "script.py").write_text("source preserved\n")
        (version_dir / "meta.json").write_text(json.dumps({
            "version": entry["version"],
            "label": entry["label"],
            "status": entry["status"],
        }))

    result = runner.invoke(cli, ["context"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["recovery"] == {
        "status": "clean",
        "candidate_count": 0,
        "recoverable_count": 0,
        "candidates": [],
    }
