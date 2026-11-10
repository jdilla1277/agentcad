"""Position-aware checks against an independent build123d plate fixture."""

from __future__ import annotations

import json

import pytest
from build123d import Box, Cylinder, Pos, export_step

from agentcad.cli import cli


def _plate_step(directory):
    plate = Box(50, 20, 5)
    plate -= Pos(-10, 0, 0) * Cylinder(3, 10)
    plate -= Pos(10, 0, 0) * Cylinder(3, 10)
    path = directory / "plate.step"
    export_step(plate, str(path))
    return path


def test_positioned_cylinder_and_probe_checks_on_a_plate(runner, isolated_dir):
    step = _plate_step(isolated_dir)
    spec = isolated_dir / "spec.json"
    spec.write_text(json.dumps({
        "expect_solids": 1,
        "features": [
            {
                "name": "mounts",
                "type": "positioned_cylinders",
                "axis": "z",
                "diameter_mm": 6,
                "positions_mm": [[-10, 0], [10, 0]],
                "position_tolerance_mm": 0.05,
            },
            {
                "name": "mount_void",
                "type": "keepout",
                "box_mm": [-11, -1, -1, -9, 1, 1],
                "max_volume_mm3": 0,
            },
            {
                "name": "plate_material",
                "type": "material",
                "box_mm": [0, 0, 0, 1, 1, 1],
                "min_volume_mm3": 0.5,
            },
        ],
    }))

    result = runner.invoke(
        cli, ["check-spec", str(step), str(spec), "--no-daemon"]
    )

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["passed"] is True
    by_name = {row["name"]: row for row in parsed["matched_features"]}
    assert by_name["mounts"]["type"] == "positioned_cylinders"
    assert by_name["mounts"]["measured"]["measured_count"] == 2
    assert by_name["mount_void"]["measured"]["volume_mm3"] == 0
    assert by_name["plate_material"]["measured"]["volume_mm3"] == pytest.approx(1)
