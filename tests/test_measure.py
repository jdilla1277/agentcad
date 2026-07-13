import json
import math
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad.cli import cli


_REAL_WORLD_FIXTURES = Path(__file__).parent / "fixtures" / "real_world"


def _box_step(directory, name="box.step"):
    box = cq.Workplane("XY").box(10, 20, 5)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _cylinder_step(directory, name="cylinder.step"):
    cyl = cq.Workplane("XY").circle(5).extrude(20)
    path = directory / name
    exporters.export(cyl, str(path))
    return path


def _plate_with_two_holes_step(directory, name="two_holes.step"):
    plate = cq.Workplane("XY").box(50, 20, 5)
    cutters = (
        cq.Workplane("XY")
        .pushPoints([(-10, 0), (10, 0)])
        .circle(3)
        .extrude(10)
    )
    part = plate.cut(cutters)
    path = directory / name
    exporters.export(part, str(path))
    return path


def _many_boxes_step(directory, name="many_boxes.step", count=20):
    solids = [
        cq.Workplane("XY").box(1, 1, 1).translate((i * 3, 0, 0)).val()
        for i in range(count)
    ]
    compound = cq.Compound.makeCompound(solids)
    path = directory / name
    exporters.export(compound, str(path))
    return path


def _surface_step(directory, name="surface.step"):
    from cadquery.occ_impl.shapes import Face, Shell

    face = Face.makePlane(length=10, width=10)
    shell = Shell.makeShell([face])
    path = directory / name
    exporters.export(cq.Workplane("XY").newObject([shell]), str(path))
    return path


class TestMeasureCommand:
    def test_measure_returns_json_metrics_and_feature_summary(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "measure"
        assert parsed["status"] == "success"
        assert parsed["metrics"]["dimensions"]["x"] == 10.0
        assert parsed["metrics"]["dimensions"]["y"] == 20.0
        assert parsed["metrics"]["dimensions"]["z"] == 5.0
        assert parsed["validity"]["is_valid"] is True
        assert "cylindrical_features" in parsed
        assert parsed["feature_summary"]["face_count"] == 6
        assert parsed["feature_summary"]["edge_count"] == 12
        assert "features" not in parsed

    @pytest.mark.parametrize(
        "flags", [[], ["--features"], ["--cylinders-only"]]
    )
    def test_surface_only_input_explains_zero_volume(
        self, runner, isolated_dir, flags
    ):
        step = _surface_step(isolated_dir)
        result = runner.invoke(
            cli, ["measure", str(step), "--no-daemon", *flags]
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert parsed["metrics"]["volume"] == 0.0
        notes = " ".join(parsed.get("notes", [])).lower()
        assert "no solid body" in notes
        assert "zero volume" in notes
        assert "loft" in notes
        next_actions = " ".join(parsed["next_actions"]).lower()
        assert "loft" in next_actions
        assert "targeted edits" not in next_actions

    def test_rounded_zero_volume_solid_does_not_get_surface_note(
        self, runner, isolated_dir
    ):
        """Use topology, not rounded volume, to distinguish tiny solids from
        surface-only inputs."""
        tiny = cq.Workplane("XY").box(0.01, 0.01, 0.01)
        path = isolated_dir / "tiny.step"
        exporters.export(tiny, str(path))

        result = runner.invoke(cli, ["measure", str(path), "--no-daemon"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert parsed["metrics"]["volume"] == 0.0
        notes = " ".join(parsed.get("notes", [])).lower()
        assert "no solid body" not in notes

    def test_measure_summary_reports_circle_and_cylinder_diameter(
        self, runner, isolated_dir
    ):
        step = _cylinder_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        assert result.exit_code == 0, result.output
        summary = json.loads(result.stdout)["feature_summary"]
        cyl_faces = [
            c for c in summary["face_clusters"] if c["kind"] == "cylindrical"
        ]
        circle_edges = [
            c for c in summary["edge_clusters"] if c["kind"] == "circle"
        ]
        assert any(c["radius"] == 5.0 and c["diameter"] == 10.0 for c in cyl_faces)
        assert any(c["radius"] == 5.0 and c["diameter"] == 10.0 for c in circle_edges)

    def test_measure_reports_deduped_cylindrical_feature_inventory(
        self, runner, isolated_dir
    ):
        step = _plate_with_two_holes_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        features = parsed["cylindrical_features"]
        hole_bucket = next(
            f for f in features
            if f["diameter_mm"] == 6.0 and f["axis"] == "+z"
        )
        assert hole_bucket["count"] == 2
        assert hole_bucket["representative_centers"] == [
            {"x": -10.0, "y": 0.0, "z": 0.0},
            {"x": 10.0, "y": 0.0, "z": 0.0},
        ]

    def test_measure_reports_cadgenbench_101_known_diameter_buckets(self, runner):
        step = _REAL_WORLD_FIXTURES / "cadgenbench_101_agentcad.step"
        result = runner.invoke(cli, ["measure", str(step)])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        buckets = {
            f["diameter_mm"]: f
            for f in parsed["cylindrical_features"]
        }

        assert parsed["validity"]["is_valid"] is True
        assert parsed["metrics"]["dimensions"] == {"x": 220.0, "y": 120.0, "z": 45.0}
        assert buckets[12.0]["count"] == 10
        assert buckets[12.0]["axis"] == "+z"
        assert buckets[36.0]["count"] == 4
        assert buckets[36.0]["axis"] == "+z"
        assert buckets[50.0]["count"] == 1
        assert buckets[50.0]["axis"] == "+z"

    def test_measure_features_flag_adds_full_feature_lists(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step), "--features"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        features = parsed["features"]
        assert len(features["solids"]) == 1
        assert len(features["faces"]) == 6
        assert len(features["edges"]) == 12
        assert "area" in features["faces"][0]
        assert "length" in features["edges"][0]

    def test_measure_features_default_is_bounded(self, runner, isolated_dir):
        step = _many_boxes_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step), "--features"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert len(parsed["features"]["faces"]) == 100
        assert len(parsed["features"]["edges"]) == 100
        fields = {f["path"]: f for f in parsed["truncation"]["fields"]}
        assert fields["features.faces"]["total"] == 120
        assert fields["features.edges"]["total"] == 240
        assert any("--no-limit" in c for c in parsed["truncation"]["recommended_commands"])

    def test_measure_features_no_limit_restores_complete_lists(
        self, runner, isolated_dir
    ):
        step = _many_boxes_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step), "--features", "--no-limit"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert len(parsed["features"]["faces"]) == parsed["metrics"]["face_count"]
        assert len(parsed["features"]["edges"]) == parsed["metrics"]["edge_count"]
        assert "truncation" not in parsed

    def test_measure_summary_truncation_recommends_summary_sized_followup(
        self, runner, isolated_dir
    ):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step), "--limit", "1"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        recommended = " ".join(parsed["truncation"]["recommended_commands"])
        assert "measure" in recommended
        assert "--limit 500" in recommended
        assert "--no-limit" in recommended
        assert "--features --no-limit" not in recommended

    def test_measure_cylinders_only_filters_diameter_and_axis(
        self, runner, isolated_dir
    ):
        step = _plate_with_two_holes_step(isolated_dir)
        result = runner.invoke(cli, [
            "measure", str(step),
            "--cylinders-only", "--diameter", "6", "--axis", "+z",
        ])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert "feature_summary" not in parsed
        assert "features" not in parsed
        assert parsed["query"]["cylinders_only"] is True
        assert parsed["cylindrical_features"] == [{
            "diameter_mm": 6.0,
            "count": 2,
            "axis": "+z",
            "representative_centers": [
                {"x": -10.0, "y": 0.0, "z": 0.0},
                {"x": 10.0, "y": 0.0, "z": 0.0},
            ],
        }]

    def test_measure_missing_file_error(self, runner, isolated_dir):
        result = runner.invoke(cli, ["measure", "missing.step"])
        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "measure"
        assert parsed["status"] == "error"

    def test_measure_returns_no_artifacts(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        parsed = json.loads(result.stdout)
        assert "outputs" not in parsed
        assert "renders" not in parsed

    def test_measure_next_actions_do_not_redirect_measurements_to_inspect(
        self, runner, isolated_dir
    ):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step)])
        parsed = json.loads(result.stdout)
        joined = " ".join(parsed["next_actions"])
        assert "feature_summary" in joined
        assert "inspect" in joined
        assert "--summary" not in joined

    def test_measure_loads_step_symlink_to_extensionless_target(
        self, runner, isolated_dir
    ):
        step = _box_step(isolated_dir, "source.step")
        blob = isolated_dir / "661210dec702"
        link = isolated_dir / "input.step"
        step.rename(blob)
        link.symlink_to(blob.name)

        result = runner.invoke(cli, ["measure", str(link), "--no-daemon"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["file"].endswith("input.step")
        assert parsed["format_detected"] == "step"
        assert parsed["metrics"]["dimensions"] == {"x": 10.0, "y": 20.0, "z": 5.0}


class TestMeasureEditRisk:
    """Edit-risk classification (issue #32)."""

    def test_clean_part_has_no_edit_risk(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["measure", str(step), "--no-daemon"])
        parsed = json.loads(result.stdout)
        assert "edit_risk" not in parsed

    def test_large_part_flagged_with_guidance(self, runner, isolated_dir):
        solids = [
            cq.Workplane("XY").transformed(offset=(i * 20, 0, 0)).box(10, 10, 10).val()
            for i in range(200)
        ]
        compound = cq.Compound.makeCompound(solids)
        wp = cq.Workplane("XY").newObject([compound])
        step = isolated_dir / "big.step"
        exporters.export(wp, str(step))

        result = runner.invoke(cli, ["measure", str(step), "--no-daemon"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["edit_risk"] == "medium"
        assert any("large topology" in r for r in parsed["risk_reasons"])
        assert parsed["recommended_workflow"]

    def test_invalid_large_input_flagged_high_end_to_end(self, runner, isolated_dir):
        from tests.test_inspect import _invalid_large_brep

        brep = _invalid_large_brep(isolated_dir)
        result = runner.invoke(cli, ["measure", str(brep), "--no-daemon"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["edit_risk"] == "high"
        assert any("invalid topology" in r for r in parsed["risk_reasons"])
        if parsed["metrics"]["surface_area"] == 0.0:
            assert math.copysign(1.0, parsed["metrics"]["surface_area"]) == 1.0
        # next_actions stays coherent with the risk guidance
        joined = " ".join(parsed["next_actions"]).lower()
        assert "recommended_workflow" in joined

    def test_high_risk_brep_symlink_to_extensionless_target_loads(
        self, runner, isolated_dir
    ):
        from tests.test_inspect import _invalid_large_brep

        brep = _invalid_large_brep(isolated_dir, "source.brep")
        blob = isolated_dir / "661210dec702"
        link = isolated_dir / "input.brep"
        brep.rename(blob)
        link.symlink_to(blob.name)

        result = runner.invoke(cli, ["measure", str(link), "--no-daemon"])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["file"].endswith("input.brep")
        assert parsed["format_detected"] == "brep"
        assert parsed["edit_risk"] == "high"


class TestMeasureDaemonRouting:
    def test_measure_routes_through_daemon_when_available(
        self, runner, isolated_dir, monkeypatch
    ):
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        daemon_output = json.dumps({
            "command": "measure",
            "status": "success",
            "metrics": {"dimensions": {"x": 10, "y": 20, "z": 5}},
            "feature_summary": {"face_count": 6, "edge_count": 12},
        })
        monkeypatch.setattr(
            "agentcad.daemon.send_request",
            lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
        )
        result = runner.invoke(cli, ["measure", "any.step"])
        assert result.exit_code == 0, result.stdout
        parsed = json.loads(result.stdout)
        assert parsed["via"] == "daemon"
        assert parsed["command"] == "measure"

    def test_measure_no_daemon_flag_skips_routing(
        self, runner, isolated_dir, monkeypatch
    ):
        step = _box_step(isolated_dir)
        monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
        calls = []

        def _track(*a, **kw):
            calls.append((a, kw))
            return None

        monkeypatch.setattr("agentcad.daemon.send_request", _track)
        result = runner.invoke(cli, ["measure", str(step), "--no-daemon"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert "via" not in parsed
        assert calls == []
