"""Tests for `split_by_plane` — Phase 4b edit helper.

Splits a shape with an infinite plane and returns both halves. Common
agent intents: "cut this in half for a cross-section", "trim off the top
above z=5", "separate the assembly along the parting plane".
"""
import json
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad.cli import cli


def _box_step(directory: Path, name: str = "box.step") -> Path:
    box = cq.Workplane("XY").box(20, 20, 20)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _init_and_import(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    step = _box_step(isolated_dir)
    result = runner.invoke(cli, ["import", str(step), "--label", "part"])
    assert result.exit_code == 0, result.output


def _run_edit(runner, isolated_dir, script_text: str, output: str):
    (isolated_dir / "edit.py").write_text(script_text)
    result = runner.invoke(cli, ["run", "edit.py", "--output", output])
    return result, (json.loads(result.stdout) if result.output else None)


# --- core split semantics --------------------------------------------------

class TestSplitByPlane:
    def test_split_xy_at_origin_returns_two_halves(self, runner, isolated_dir):
        """A 20x20x20 box centered at the origin cut by Plane.XY → two
        equal halves, each volume 4000."""
        _init_and_import(runner, isolated_dir)
        result, parsed = _run_edit(
            runner, isolated_dir,
            '''from build123d import *
base = load_step("v1_part/output.step")
below, above = split_by_plane(base, "XY")
# Stack them back so we can verify both pieces survived as a single artifact.
result = below + above
show_object(result)
''',
            "split_xy",
        )
        assert result.exit_code == 0, result.output
        assert parsed["status"] == "success"
        # Two halves stacked back together = original volume (4000+4000 = 8000).
        assert abs(parsed["metrics"]["volume"] - 8000.0) < 1e-3

    def test_split_returns_above_with_correct_volume(self, runner, isolated_dir):
        """Verify the 'above' half (along plane normal) has the expected
        volume on its own. For a 20x20x20 box centered at origin, splitting
        with Plane.XY gives a 20x20x10 above-half = 4000."""
        _init_and_import(runner, isolated_dir)
        result, parsed = _run_edit(
            runner, isolated_dir,
            '''from build123d import *
base = load_step("v1_part/output.step")
_below, above = split_by_plane(base, "XY")
show_object(above)
''',
            "above_only",
        )
        assert result.exit_code == 0, result.output
        assert abs(parsed["metrics"]["volume"] - 4000.0) < 1e-3
        # Above half should sit with its lowest z at the cut plane (z=0).
        assert abs(parsed["metrics"]["bounding_box"]["z"][0]) < 1e-3
        assert abs(parsed["metrics"]["bounding_box"]["z"][1] - 10.0) < 1e-3

    def test_split_returns_below_with_correct_bbox(self, runner, isolated_dir):
        """Verify the 'below' half has its top at the cut plane."""
        _init_and_import(runner, isolated_dir)
        result, parsed = _run_edit(
            runner, isolated_dir,
            '''from build123d import *
base = load_step("v1_part/output.step")
below, _above = split_by_plane(base, "XY")
show_object(below)
''',
            "below_only",
        )
        assert result.exit_code == 0, result.output
        assert abs(parsed["metrics"]["bounding_box"]["z"][1]) < 1e-3
        assert abs(parsed["metrics"]["bounding_box"]["z"][0] + 10.0) < 1e-3

    def test_split_with_b3d_plane_object(self, runner, isolated_dir):
        """Should accept a build123d Plane directly, not just string shorthand."""
        _init_and_import(runner, isolated_dir)
        result, parsed = _run_edit(
            runner, isolated_dir,
            '''from build123d import *
base = load_step("v1_part/output.step")
plane = Plane.XY.offset(5)
below, above = split_by_plane(base, plane)
show_object(below)
''',
            "offset_plane",
        )
        assert result.exit_code == 0, result.output
        # Box -10..10 in z, plane at z=5 → below half is -10..5 = 15 units thick.
        # Volume = 20*20*15 = 6000.
        assert abs(parsed["metrics"]["volume"] - 6000.0) < 1.0

    def test_split_xz_plane(self, runner, isolated_dir):
        """String shorthand should support XZ and YZ too, not just XY."""
        _init_and_import(runner, isolated_dir)
        result, parsed = _run_edit(
            runner, isolated_dir,
            '''from build123d import *
base = load_step("v1_part/output.step")
below, above = split_by_plane(base, "XZ")
show_object(above)
''',
            "split_xz",
        )
        assert result.exit_code == 0, result.output
        # Each half is 4000 (split along Y axis = 20*10*20).
        assert abs(parsed["metrics"]["volume"] - 4000.0) < 1.0


# --- error paths -----------------------------------------------------------

class TestSplitByPlaneErrors:
    def test_invalid_plane_string_raises_clean_error(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        result, parsed = _run_edit(
            runner, isolated_dir,
            '''from build123d import *
base = load_step("v1_part/output.step")
below, above = split_by_plane(base, "QQ")
show_object(below)
''',
            "should_fail",
        )
        assert result.exit_code != 0
        text = result.output.lower()
        # Error should mention the invalid string and list valid options.
        assert "qq" in text or "invalid" in text or "plane" in text
        assert "Traceback" not in result.output


# --- CQ-side validation ----------------------------------------------------

class TestSplitByPlaneInCadqueryScriptFails:
    def test_cq_script_using_split_by_plane_fails_with_kernel_neutral_hint(
        self, runner, isolated_dir
    ):
        _init_and_import(runner, isolated_dir)
        (isolated_dir / "edit.py").write_text(
            '''import cadquery as cq
from cadquery import importers
base = importers.importStep("v1_part/output.step")
below, above = split_by_plane(base, "XY")
show_object(below)
'''
        )
        result = runner.invoke(
            cli,
            [
                "run", "edit.py", "--output", "should_fail",
                "--runtime", "cadquery",
            ],
        )
        assert result.exit_code != 0
        text = result.output.lower()
        assert "split_by_plane" in text
        assert "build123d" in text or "importers" in text
