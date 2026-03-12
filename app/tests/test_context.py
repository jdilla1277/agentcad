import json

from click.testing import CliRunner
from cadtool.cli import cli


def test_context_no_manifest_error(runner, isolated_dir):
    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["command"] == "context"
    assert data["status"] == "error"


def test_context_empty_project(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "myproject"])
    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "success"
    assert data["project"] == "myproject"
    assert data["version_count"] == 0
    assert data["current"] is None


def test_context_with_versions(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "proj"])
    # Write manifest with 2 versions
    manifest = json.loads((isolated_dir / "cadtool.json").read_text())
    manifest["versions"] = [
        {"version": 1, "label": "box", "status": "success", "path": "v1_box/"},
        {"version": 2, "label": "cyl", "status": "success", "path": "v2_cyl/"},
    ]
    manifest["current"] = "cyl"
    (isolated_dir / "cadtool.json").write_text(json.dumps(manifest, indent=2))

    result = runner.invoke(cli, ["context"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["version_count"] == 2
    assert data["current"] == "cyl"


def test_context_includes_versions_summary(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "proj"])
    manifest = json.loads((isolated_dir / "cadtool.json").read_text())
    manifest["versions"] = [
        {"version": 1, "label": "box", "status": "success", "path": "v1_box/"},
        {"version": 2, "label": "cyl", "status": "failed", "path": "v2_cyl_failed/"},
    ]
    manifest["current"] = "box"
    (isolated_dir / "cadtool.json").write_text(json.dumps(manifest, indent=2))

    result = runner.invoke(cli, ["context"])
    data = json.loads(result.output)
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
    manifest = json.loads((isolated_dir / "cadtool.json").read_text())
    manifest["versions"] = [
        {"version": 1, "label": "box", "status": "success", "path": "v1_box/"},
    ]
    manifest["current"] = "box"
    (isolated_dir / "cadtool.json").write_text(json.dumps(manifest, indent=2))

    result = runner.invoke(cli, ["context"])
    data = json.loads(result.output)
    assert data["versions"][0]["path"] == "v1_box/"


def test_context_includes_tool_version(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "proj"])
    result = runner.invoke(cli, ["context"])
    data = json.loads(result.output)
    assert "tool_version" in data
    assert data["tool_version"] == "0.1.0"
