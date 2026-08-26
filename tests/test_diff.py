import json

import cadquery as cq
from cadquery import exporters
from click.testing import CliRunner
from agentcad.cli import cli


def _write_box_step(path, size):
    box = cq.Workplane("XY").box(size, size, size)
    exporters.export(box, str(path))
    return path


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
    (isolated_dir / "agentcad.json").write_text(json.dumps(manifest, indent=2))


def test_diff_no_manifest_error(runner, isolated_dir):
    result = runner.invoke(cli, ["diff", "1", "2"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "diff"
    assert data["status"] == "error"


def test_diff_accepts_standalone_step_paths_without_manifest(runner, isolated_dir):
    input_step = _write_box_step(isolated_dir / "input.step", 10)
    output_step = _write_box_step(isolated_dir / "output.step", 20)

    result = runner.invoke(cli, ["diff", str(input_step), str(output_step)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert data["v1"]["file"] == "input.step"
    assert data["v2"]["file"] == "output.step"
    assert data["changes"]["metrics"]["volume"] == {"from": 1000.0, "to": 8000.0}
    assert data["changes"]["metrics"]["dimensions"] == {
        "from": {"x": 10.0, "y": 10.0, "z": 10.0},
        "to": {"x": 20.0, "y": 20.0, "z": 20.0},
    }
    comparison = data["comparison_3d"]
    assert comparison["method"] == "source_frame_boolean_volume"
    assert comparison["alignment"] == {
        "mode": "source_frame",
        "transform_applied": False,
    }
    assert comparison["volumes"]["shared"] == 1000.0
    assert comparison["volumes"]["reference_only"] == 0.0
    assert comparison["volumes"]["candidate_only"] == 7000.0
    assert comparison["ratios"]["reference_coverage"] == 1.0
    assert comparison["ratios"]["candidate_coverage"] == 0.125

    phases = data["comparison_phases"]
    assert phases["source_loading"]["status"] == "success"
    assert phases["exact_3d_comparison"]["status"] == "success"
    for name in (
        "comparison_rendering",
        "projection_comparison",
        "difference_artifact_export",
        "viewer_generation",
    ):
        assert phases[name]["status"] == "skipped"
        assert "duration_ms" not in phases[name]


def test_diff_disambiguates_repeated_standalone_filenames(runner, isolated_dir):
    baseline_dir = isolated_dir / "baseline"
    candidate_dir = isolated_dir / "candidate"
    baseline_dir.mkdir()
    candidate_dir.mkdir()
    baseline = _write_box_step(baseline_dir / "output.step", 10)
    candidate = _write_box_step(candidate_dir / "output.step", 20)

    result = runner.invoke(cli, ["diff", str(baseline), str(candidate)])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["v1"]["label"] == "baseline/output.step"
    assert data["v2"]["label"] == "candidate/output.step"


def test_diff_standalone_step_path_missing_file_error(runner, isolated_dir):
    input_step = _write_box_step(isolated_dir / "input.step", 10)

    result = runner.invoke(cli, ["diff", str(input_step), "missing.step"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["command"] == "diff"
    assert data["status"] == "error"
    assert data["message"] == "File 'missing.step' not found."


def test_diff_exact_exception_is_attributed_without_losing_metric_changes(
    runner, isolated_dir, monkeypatch
):
    input_step = _write_box_step(isolated_dir / "input.step", 10)
    output_step = _write_box_step(isolated_dir / "output.step", 20)

    def fail_exact(*_args, **_kwargs):
        raise RuntimeError("injected explicit exact failure")

    monkeypatch.setattr(
        "agentcad.solid_compare.bounded_compare_solid_volumes", fail_exact
    )
    monkeypatch.setenv("AGENTCAD_APPROX_RESOLUTION_MM", "1")
    result = runner.invoke(cli, [
        "diff", str(input_step), str(output_step), "--no-daemon",
    ])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert data["changes"]["metrics"]["volume"] == {
        "from": 1000.0,
        "to": 8000.0,
    }
    assert data["comparison_3d"]["method"] == "approximate_voxel_volume"
    assert data["comparison_3d"]["status"] == "success"
    assert data["comparison_3d"]["exact_attempt"]["status"] == "unavailable"
    phase = data["comparison_phases"]["exact_3d_comparison"]
    assert phase["status"] == "failed"
    assert "injected explicit exact failure" in phase["message"]
    assert data["comparison_phases"]["approximate_3d_comparison"]["status"] == "success"


def test_diff_exact_timeout_is_structured(
    runner, isolated_dir, monkeypatch
):
    input_step = _write_box_step(isolated_dir / "input.step", 10)
    output_step = _write_box_step(isolated_dir / "output.step", 20)

    def exact_timeout(*_args, **_kwargs):
        from agentcad.solid_compare import SolidComparison

        return SolidComparison({
            "method": "source_frame_boolean_volume",
            "status": "timeout",
            "timeout_s": 0.05,
            "reason": {
                "code": "exact_comparison_timeout",
                "message": "Exact comparison timed out.",
            },
        })

    monkeypatch.setattr(
        "agentcad.solid_compare.bounded_compare_solid_volumes",
        exact_timeout,
    )
    monkeypatch.setenv("AGENTCAD_APPROX_RESOLUTION_MM", "1")
    result = runner.invoke(cli, [
        "diff", str(input_step), str(output_step), "--no-daemon",
    ])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert data["comparison_3d"]["status"] == "success"
    assert data["comparison_3d"]["method"] == "approximate_voxel_volume"
    assert data["comparison_phases"]["exact_3d_comparison"]["status"] == "timeout"
    assert data["comparison_phases"]["approximate_3d_comparison"]["status"] == "success"
    exact_attempt = data["comparison_3d"]["exact_attempt"]
    assert exact_attempt["status"] == "timeout"
    suggestion = exact_attempt["suggestion"]
    assert str(input_step) in suggestion
    assert str(output_step) in suggestion
    assert "AGENTCAD_DIFF_TIMEOUT_S=60" in suggestion


def test_diff_both_3d_methods_timeout_have_copyable_retries(
    runner, isolated_dir, monkeypatch
):
    from agentcad.solid_compare import SolidComparison

    input_step = _write_box_step(isolated_dir / "input.step", 10)
    output_step = _write_box_step(isolated_dir / "output.step", 20)

    monkeypatch.setattr(
        "agentcad.solid_compare.bounded_compare_solid_volumes",
        lambda *_args, **_kwargs: SolidComparison({
            "method": "source_frame_boolean_volume",
            "status": "timeout",
            "timeout_s": 1,
            "reason": {"code": "exact_comparison_timeout", "message": "timeout"},
        }),
    )
    monkeypatch.setattr(
        "agentcad.solid_compare.bounded_approximate_solid_volumes",
        lambda *_args, **_kwargs: SolidComparison({
            "method": "approximate_voxel_volume",
            "status": "timeout",
            "timeout_s": 2,
            "reason": {
                "code": "approximate_comparison_timeout",
                "message": "timeout",
            },
        }),
    )

    result = runner.invoke(cli, [
        "diff", str(input_step), str(output_step), "--no-daemon",
    ])

    data = json.loads(result.stdout)["comparison_3d"]
    assert data["status"] == "timeout"
    assert "AGENTCAD_DIFF_TIMEOUT_S=60" in data["suggestion"]
    approximate = data["approximate_attempt"]
    assert "AGENTCAD_APPROX_DIFF_TIMEOUT_S=60" in approximate["suggestion"]
    assert str(input_step) in approximate["suggestion"]
    assert str(output_step) in approximate["suggestion"]


def test_diff_by_version_number(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert data["v1"]["version"] == 1
    assert data["v2"]["version"] == 2


def test_diff_by_label(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "box", "cyl"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["v1"]["label"] == "box"
    assert data["v2"]["label"] == "cyl"


def test_diff_mixed_number_and_label(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "cyl"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["v1"]["version"] == 1
    assert data["v2"]["label"] == "cyl"


def test_diff_shows_label_change(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    assert data["changes"]["label"] == {"from": "box", "to": "cyl"}


def test_diff_shows_status_change(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    # Modify v2 to be failed
    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["status"] = "failed"
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    assert data["changes"]["status"] == {"from": "success", "to": "failed"}


def test_diff_shows_output_changes(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    outputs = data["changes"]["outputs"]
    assert "stl" in outputs["added"]
    assert "step" in outputs["unchanged"]


def test_diff_shows_render_changes(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    renders = data["changes"]["renders"]
    assert "iso" in renders["added"]


def test_diff_same_version_no_changes(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "1"])
    data = json.loads(result.stdout)
    assert data["changes"]["label"] is None
    assert data["changes"]["status"] is None
    assert data["changes"]["outputs"]["added"] == []
    assert data["changes"]["outputs"]["removed"] == []


def test_diff_unknown_version_error(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "99"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
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
    data = json.loads(result.stdout)
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
    data = json.loads(result.stdout)
    m = data["changes"]["metrics"]
    assert m["volume"] is None
    assert m["face_count"] is None


def test_diff_no_metrics_graceful(runner, isolated_dir):
    """Diff works when neither version has metrics (pre-M14 versions)."""
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    # Should still succeed, metrics changes empty or absent
    assert data["status"] == "success"


def test_diff_unknown_label_error(runner, isolated_dir):
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "box", "missing"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert "missing" in data["message"]


# --- M21: Params in diff ---


def test_diff_shows_param_changes(runner, isolated_dir):
    """Diff shows parameter differences when params are present."""
    _setup_two_versions(isolated_dir)
    v1_meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    v1_meta["params"] = {"length": 50.0}
    (isolated_dir / "v1_box" / "meta.json").write_text(json.dumps(v1_meta))

    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["params"] = {"length": 100.0}
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    assert "params" in data["changes"]
    assert data["changes"]["params"]["length"] == {"from": 50.0, "to": 100.0}


# --- M33: Parts in diff ---


def test_diff_shows_parts_name_changes(runner, isolated_dir):
    """Diff shows added/unchanged part names between versions."""
    _setup_two_versions(isolated_dir)
    v1_meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    v1_meta["parts"] = [
        {"name": "deck", "color": "gray", "metrics": {"volume": 100.0}},
    ]
    (isolated_dir / "v1_box" / "meta.json").write_text(json.dumps(v1_meta))

    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["parts"] = [
        {"name": "deck", "color": "gray", "metrics": {"volume": 100.0}},
        {"name": "pin", "color": "blue", "metrics": {"volume": 50.0}},
    ]
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    assert "parts" in data["changes"]
    names = data["changes"]["parts"]["names"]
    assert "pin" in names["added"]
    assert "deck" in names["unchanged"]
    assert names["removed"] == []
    assert data["changes"]["parts"]["ids"] == names


def test_diff_shows_per_part_metric_changes(runner, isolated_dir):
    """Diff shows metric changes for shared parts."""
    _setup_two_versions(isolated_dir)
    v1_meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    v1_meta["parts"] = [
        {"name": "deck", "color": "gray", "metrics": {"volume": 100.0, "face_count": 6}},
    ]
    (isolated_dir / "v1_box" / "meta.json").write_text(json.dumps(v1_meta))

    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["parts"] = [
        {"name": "deck", "color": "gray", "metrics": {"volume": 200.0, "face_count": 6}},
    ]
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    deck = data["changes"]["parts"]["deck"]
    assert deck["volume"] == {"from": 100.0, "to": 200.0}
    assert deck["face_count"] is None  # same value
    assert deck["name"] is None


def test_diff_no_parts_graceful(runner, isolated_dir):
    """Diff works when neither version has parts."""
    _setup_two_versions(isolated_dir)
    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    assert data["status"] == "success"
    assert "parts" not in data["changes"]


def test_diff_parts_color_change(runner, isolated_dir):
    """Diff shows color change for a shared part."""
    _setup_two_versions(isolated_dir)
    v1_meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    v1_meta["parts"] = [
        {"name": "deck", "color": "gray", "metrics": {"volume": 100.0}},
    ]
    (isolated_dir / "v1_box" / "meta.json").write_text(json.dumps(v1_meta))

    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["parts"] = [
        {"name": "deck", "color": "blue", "metrics": {"volume": 100.0}},
    ]
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    deck = data["changes"]["parts"]["deck"]
    assert deck["color"] == {"from": "gray", "to": "blue"}


def test_diff_parts_match_by_new_string_id_across_rename(runner, isolated_dir):
    """New parts schema keys by id, so display-name edits do not orphan diffs."""
    _setup_two_versions(isolated_dir)
    v1_meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    v1_meta["parts"] = [
        {
            "id": "wheel_left",
            "id_source": "explicit",
            "name": "Left wheel",
            "color": "gray",
            "metrics": {"volume": 100.0},
        },
    ]
    (isolated_dir / "v1_box" / "meta.json").write_text(json.dumps(v1_meta))

    v2_meta = json.loads((isolated_dir / "v2_cyl" / "meta.json").read_text())
    v2_meta["parts"] = [
        {
            "id": "wheel_left",
            "id_source": "explicit",
            "name": "Front left wheel",
            "color": "blue",
            "metrics": {"volume": 100.0},
        },
    ]
    (isolated_dir / "v2_cyl" / "meta.json").write_text(json.dumps(v2_meta))

    result = runner.invoke(cli, ["diff", "1", "2"])
    data = json.loads(result.stdout)
    names = data["changes"]["parts"]["names"]
    assert names["unchanged"] == ["wheel_left"]
    assert data["changes"]["parts"]["ids"]["unchanged"] == ["wheel_left"]
    assert data["changes"]["parts"]["wheel_left"]["name"] == {
        "from": "Left wheel",
        "to": "Front left wheel",
    }
    assert data["changes"]["parts"]["wheel_left"]["color"] == {
        "from": "gray",
        "to": "blue",
    }


# --- Daemon routing (#177) ---

def test_diff_routes_through_daemon_when_available(runner, isolated_dir, monkeypatch):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    daemon_output = json.dumps({
        "command": "diff", "status": "success",
        "v1": "first", "v2": "second", "changes": {},
    })
    monkeypatch.setattr(
        "agentcad.daemon.send_request",
        lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
    )
    result = runner.invoke(cli, ["diff", "1", "2"])
    assert result.exit_code == 0, result.stdout
    parsed = json.loads(result.stdout)
    assert parsed["via"] == "daemon"
    assert parsed["command"] == "diff"


def test_diff_no_daemon_flag_skips_routing(runner, isolated_dir, monkeypatch):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    calls: list[tuple] = []
    def _track(*a, **kw):
        calls.append((a, kw))
        return None
    monkeypatch.setattr("agentcad.daemon.send_request", _track)
    # No manifest exists, but with --no-daemon we should bypass routing
    # entirely and hit direct execution's missing-manifest error.
    result = runner.invoke(cli, ["diff", "1", "2", "--no-daemon"])
    assert calls == [], f"send_request was called despite --no-daemon: {calls}"
