import json
import math
from pathlib import Path

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


def _bored_plate_step(directory: Path, name: str = "bored_plate.step") -> Path:
    plate = cq.Workplane("XY").box(160, 160, 10)
    bore = cq.Workplane("XY").circle(20).extrude(30).translate((0, 0, -15))
    part = plate.cut(bore)
    path = directory / name
    exporters.export(part, str(path))
    return path


def test_raise_annulus_edits_imported_step_without_boolean_fuse(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    step = _bored_plate_step(isolated_dir)
    imported = runner.invoke(cli, ["import", str(step), "--label", "part"])
    assert imported.exit_code == 0, imported.output

    inspected = runner.invoke(cli, ["inspect", "v1_part/output.step", "--ids"])
    assert inspected.exit_code == 0, inspected.output
    original = json.loads(inspected.stdout)
    original_volume = original["solids"][0]["volume"]

    (isolated_dir / "edit.py").write_text(
        """from build123d import *

base = load_step_shape("v1_part/output.step")
result = raise_annulus(
    base,
    center=(0, 0),
    inner_diameter=40,
    outer_diameter=80,
    height=7,
    z=5,
)
show_object(Compound(result))
"""
    )
    result = runner.invoke(cli, ["run", "edit.py", "--output", "raised_bore_land"])
    assert result.exit_code == 0, result.output

    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["metrics"]["is_valid"] is True
    assert parsed["metrics"]["bounding_box"]["z"][1] > 11.9

    expected_growth = math.pi * (40 ** 2 - 20 ** 2) * 7
    actual_growth = parsed["metrics"]["volume"] - original_volume
    assert abs(actual_growth - expected_growth) / expected_growth < 0.01
    assert (isolated_dir / "v2_raised_bore_land" / "output.step").exists()


def test_raise_annulus_raw_result_works_in_cadquery_runner(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    step = _bored_plate_step(isolated_dir)
    imported = runner.invoke(cli, ["import", str(step), "--label", "part"])
    assert imported.exit_code == 0, imported.output

    (isolated_dir / "edit_cq.py").write_text(
        """result = raise_annulus(
    "v1_part/output.step",
    center=(0, 0),
    inner_diameter=40,
    outer_diameter=80,
    height=7,
    z=5,
)
show_object(result)
"""
    )
    result = runner.invoke(
        cli,
        [
            "run",
            "edit_cq.py",
            "--runtime",
            "cadquery",
            "--output",
            "raised_bore_land_cq",
        ],
    )
    assert result.exit_code == 0, result.output

    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["metrics"]["is_valid"] is True
    assert parsed["metrics"]["bounding_box"]["z"][1] > 11.9
