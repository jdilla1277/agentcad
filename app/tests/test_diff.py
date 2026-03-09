import json

from click.testing import CliRunner
from cadtool.cli import cli


def _setup_two_versions(isolated_dir):
    """Create a manifest with two versions and their meta.json files."""
    # Create version directories
    v1_dir = isolated_dir / "v1_box"
    v1_dir.mkdir()
    v1_meta = {
        "version": 1,
        "label": "box",
        "status": "success",
        "created": "2025-01-01T00:00:00+00:00",
        "script": "v1_box/script.py",
        "outputs": {"step": "v1_box/output.step"},
    }
    (v1_dir / "meta.json").write_text(json.dumps(v1_meta, indent=2))

    v2_dir = isolated_dir / "v2_cyl"
    v2_dir.mkdir()
    v2_meta = {
        "version": 2,
        "label": "cyl",
        "status": "success",
        "created": "2025-01-02T00:00:00+00:00",
        "script": "v2_cyl/script.py",
        "outputs": {"step": "v2_cyl/output.step", "stl": "v2_cyl/output.stl"},
        "renders": {"iso": "v2_cyl/renders/iso.png"},
    }
    (v2_dir / "meta.json").write_text(json.dumps(v2_meta, indent=2))

    # Create manifest
    manifest = {
        "name": "proj",
        "version": "0.1.0",
        "created": "2025-01-01T00:00:00+00:00",
        "current": "cyl",
        "versions": [
            {"version": 1, "label": "box", "status": "success", "path": "v1_box/"},
            {"version": 2, "label": "cyl", "status": "success", "path": "v2_cyl/"},
        ],
    }
    (isolated_dir / "cadtool.json").write_text(json.dumps(manifest, indent=2))


def test_diff_no_manifest_error(runner, isolated_dir):
    result = runner.invoke(cli, ["diff", "1", "2"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["command"] == "diff"
    assert data["status"] == "error"


def test_diff_by_version_number(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "success"
    assert data["v1"]["version"] == 1
    assert data["v2"]["version"] == 2


def test_diff_by_label(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "box", "cyl"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["v1"]["label"] == "box"
    assert data["v2"]["label"] == "cyl"


def test_diff_mixed_number_and_label(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "cyl"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["v1"]["version"] == 1
    assert data["v2"]["label"] == "cyl"


def test_diff_shows_label_change(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.output)
    assert data["changes"]["label"] == {"from": "box", "to": "cyl"}


def test_diff_shows_status_change(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    # Modify v2 to be failed
    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["status"] = "failed"
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.output)
    assert data["changes"]["status"] == {"from": "success", "to": "failed"}


def test_diff_shows_output_changes(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.output)
    outputs = data["changes"]["outputs"]
    assert "stl" in outputs["added"]
    assert "step" in outputs["unchanged"]


def test_diff_shows_render_changes(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.output)
    renders = data["changes"]["renders"]
    assert "iso" in renders["added"]


def test_diff_same_version_no_changes(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "1"])
    data = json.loads(result.output)
    assert data["changes"]["label"] is None
    assert data["changes"]["status"] is None
    assert data["changes"]["outputs"]["added"] == []
    assert data["changes"]["outputs"]["removed"] == []


def test_diff_unknown_version_error(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "99"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"
    assert "99" in data["message"]


def test_diff_shows_metric_changes(runner, isolated_dir):
    """Diff shows metric differences when metrics are present."""
    _setup_two_versions(isolated_dir)
    # Add metrics to v1 and v2 with different values
    v1_meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    v1_meta["metrics"] = {"volume": 1000.0, "face_count": 6}
    (isolated_dir / "v1_box" / "meta.json").write_text(json.dumps(v1_meta))

    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["metrics"] = {"volume": 1570.8, "face_count": 3}
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.output)
    assert "metrics" in data["changes"]
    m = data["changes"]["metrics"]
    assert m["volume"] == {"from": 1000.0, "to": 1570.8}
    assert m["face_count"] == {"from": 6, "to": 3}


def test_diff_metrics_same_values_are_none(runner, isolated_dir):
    """Metrics with same values show None."""
    _setup_two_versions(isolated_dir)
    metrics = {"volume": 1000.0, "face_count": 6}
    for d in ["v1_box", "v2_cyl"]:
        meta = json.loads((isolated_dir / d / "meta.json").read_text())
        meta["metrics"] = metrics
        (isolated_dir / d / "meta.json").write_text(json.dumps(meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.output)
    m = data["changes"]["metrics"]
    assert m["volume"] is None
    assert m["face_count"] is None


def test_diff_no_metrics_graceful(runner, isolated_dir):
    """Diff works when neither version has metrics (pre-M14 versions)."""
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.output)
    # Should still succeed, metrics changes empty or absent
    assert data["status"] == "success"


def test_diff_unknown_label_error(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "box", "missing"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"
    assert "missing" in data["message"]
