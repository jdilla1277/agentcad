import json

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


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


def _write_spec(directory, payload, name="spec.json"):
    path = directory / name
    path.write_text(json.dumps(payload))
    return path


class TestCheckSpecCommand:
    def test_check_spec_passes_matching_cylinder_spec(self, runner, isolated_dir):
        step = _plate_with_two_holes_step(isolated_dir)
        spec = _write_spec(isolated_dir, {
            "features": [
                {
                    "name": "mounting_holes",
                    "type": "cylinder",
                    "diameter_mm": 6,
                    "count": 2,
                    "axis": "+z",
                }
            ]
        })

        result = runner.invoke(cli, ["check-spec", str(step), str(spec)])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "check-spec"
        assert parsed["status"] == "success"
        assert parsed["passed"] is True
        assert parsed["missing_features"] == []
        assert parsed["total_abs_count_error"] == 0
        assert parsed["matched_features"][0]["passed"] is True
        assert parsed["matched_features"][0]["measured"] == {
            "diameter_mm": 6.0,
            "count": 2,
            "axis": "+z",
        }

    def test_check_spec_reports_count_error_without_cli_failure(
        self, runner, isolated_dir
    ):
        step = _plate_with_two_holes_step(isolated_dir)
        spec = _write_spec(isolated_dir, {
            "features": [
                {
                    "name": "mounting_holes",
                    "type": "cylinder",
                    "diameter_mm": 6,
                    "count": 3,
                }
            ]
        })

        result = runner.invoke(cli, ["check-spec", str(step), str(spec)])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["passed"] is False
        assert parsed["missing_features"] == []
        assert parsed["total_abs_count_error"] == 1
        feature = parsed["matched_features"][0]
        assert feature["name"] == "mounting_holes"
        assert feature["count_error"] == -1
        assert feature["passed"] is False

    def test_check_spec_reports_missing_feature_instead_of_nearest_match(
        self, runner, isolated_dir
    ):
        step = _plate_with_two_holes_step(isolated_dir)
        spec = _write_spec(isolated_dir, {
            "features": [
                {
                    "name": "eight_mm_holes",
                    "type": "cylinder",
                    "diameter_mm": 8,
                    "count": 2,
                }
            ]
        })

        result = runner.invoke(cli, ["check-spec", str(step), str(spec)])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["passed"] is False
        assert parsed["matched_features"] == []
        assert parsed["total_abs_count_error"] == 2
        missing = parsed["missing_features"][0]
        assert missing["name"] == "eight_mm_holes"
        assert missing["reason"] == "no_matching_cylinder_bucket"
        assert missing["target"]["diameter_mm"] == 8.0

    def test_check_spec_supports_per_feature_tolerances(self, runner, isolated_dir):
        step = _plate_with_two_holes_step(isolated_dir)
        spec = _write_spec(isolated_dir, {
            "features": [
                {
                    "name": "loose_holes",
                    "type": "cylinder",
                    "diameter_mm": 6.05,
                    "diameter_tolerance_mm": 0.1,
                    "count": 3,
                    "count_tolerance": 1,
                }
            ]
        })

        result = runner.invoke(cli, ["check-spec", str(step), str(spec)])

        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["passed"] is True
        feature = parsed["matched_features"][0]
        assert feature["diameter_error_mm"] == -0.05
        assert feature["count_error"] == -1
        assert feature["passed"] is True

    def test_check_spec_rejects_invalid_spec_schema(self, runner, isolated_dir):
        step = _plate_with_two_holes_step(isolated_dir)
        spec = _write_spec(isolated_dir, {"features": [{"type": "box"}]})

        result = runner.invoke(cli, ["check-spec", str(step), str(spec)])

        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "check-spec"
        assert parsed["status"] == "invalid_spec"
        assert any("name" in error for error in parsed["errors"])
        assert any("type" in error for error in parsed["errors"])

    def test_check_spec_missing_spec_error(self, runner, isolated_dir):
        step = _plate_with_two_holes_step(isolated_dir)

        result = runner.invoke(cli, ["check-spec", str(step), "missing.json"])

        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "check-spec"
        assert parsed["status"] == "error"
        assert "Spec file" in parsed["message"]

    def test_check_spec_missing_cad_file_error(self, runner, isolated_dir):
        spec = _write_spec(isolated_dir, {
            "features": [
                {"name": "holes", "type": "cylinder", "diameter_mm": 6, "count": 2}
            ]
        })

        result = runner.invoke(cli, ["check-spec", "missing.step", str(spec)])

        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "check-spec"
        assert parsed["status"] == "error"
        assert "not found" in parsed["message"]

    def test_check_spec_unsupported_file_error(self, runner, isolated_dir):
        stl = isolated_dir / "mesh.stl"
        stl.write_text("solid mesh\nendsolid mesh\n")
        spec = _write_spec(isolated_dir, {
            "features": [
                {"name": "holes", "type": "cylinder", "diameter_mm": 6, "count": 2}
            ]
        })

        result = runner.invoke(cli, ["check-spec", str(stl), str(spec)])

        assert result.exit_code == 1
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "check-spec"
        assert parsed["status"] == "unsupported_format"
        assert "STEP/STP/BREP" in parsed["message"]
