"""Tests for `cut_pocket` and `boss` — Phase 4c feature ops.

These are the first edit helpers to add NEW features to a part (rather
than modifying existing ones like fillet/chamfer/shell). Both pick a
face by ID, position a 2D sketch profile on the face's plane, and
extrude into or out of the solid.
"""
import json
import math
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad.cli import cli


def _box_step(directory: Path, name: str = "box.step") -> Path:
    box = cq.Workplane("XY").box(20, 20, 10)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _init_and_import(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    step = _box_step(isolated_dir)
    result = runner.invoke(cli, ["import", str(step), "--label", "part"])
    assert result.exit_code == 0, result.output


def _inspected_ids(runner, version_dir: str) -> dict:
    step = f"{version_dir}/output.step"
    result = runner.invoke(cli, ["inspect", step, "--ids"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _run_edit(runner, isolated_dir, script_text: str, output: str):
    (isolated_dir / "edit.py").write_text(script_text)
    result = runner.invoke(cli, ["run", "edit.py", "--output", output])
    return result, (json.loads(result.stdout) if result.output else None)


def _top_face_id(ids: dict) -> int:
    """Return the ID of the face with the highest centroid.z."""
    return max(ids["faces"], key=lambda f: f["centroid"]["z"])["id"]


# --- cut_pocket ------------------------------------------------------------

class TestCutPocket:
    def test_circular_pocket_into_top_face(self, runner, isolated_dir):
        """Cut a 3mm-radius, 4mm-deep circular pocket into the top face of a
        20x20x10 box. Volume should drop by ~π*9*4 = ~113.1mm³."""
        _init_and_import(runner, isolated_dir)
        ids = _inspected_ids(runner, "v1_part")
        face_id = _top_face_id(ids)
        original_vol = ids["solids"][0]["volume"]

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
profile = Sketch() + Circle(radius=3)
result = cut_pocket(base, face_id={face_id}, profile=profile, depth=4)
show_object(result)
''',
            "pocketed",
        )
        assert result.exit_code == 0, result.output
        assert parsed["status"] == "success"
        assert parsed["metrics"]["is_valid"] is True
        expected_drop = math.pi * 9 * 4  # π·r²·h ≈ 113.097
        actual_drop = original_vol - parsed["metrics"]["volume"]
        # 1% tolerance to account for tessellation / rounding.
        assert abs(actual_drop - expected_drop) / expected_drop < 0.01, \
            f"expected ~{expected_drop:.2f} drop, got {actual_drop:.2f}"

    def test_rectangular_pocket(self, runner, isolated_dir):
        """Non-circular profile too — a 4x6 rectangular pocket, 3mm deep."""
        _init_and_import(runner, isolated_dir)
        ids = _inspected_ids(runner, "v1_part")
        face_id = _top_face_id(ids)
        original_vol = ids["solids"][0]["volume"]

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
profile = Sketch() + Rectangle(4, 6)
result = cut_pocket(base, face_id={face_id}, profile=profile, depth=3)
show_object(result)
''',
            "rect_pocket",
        )
        assert result.exit_code == 0, result.output
        expected_drop = 4 * 6 * 3  # 72
        actual_drop = original_vol - parsed["metrics"]["volume"]
        assert abs(actual_drop - expected_drop) < 0.5


# --- boss ------------------------------------------------------------------

class TestBoss:
    def test_circular_boss_on_top_face(self, runner, isolated_dir):
        """Add a 3mm-radius, 4mm-tall cylindrical boss on the top face.
        Volume should grow by ~π*9*4 = ~113.1mm³."""
        _init_and_import(runner, isolated_dir)
        ids = _inspected_ids(runner, "v1_part")
        face_id = _top_face_id(ids)
        original_vol = ids["solids"][0]["volume"]

        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
profile = Sketch() + Circle(radius=3)
result = boss(base, face_id={face_id}, profile=profile, height=4)
show_object(result)
''',
            "bossed",
        )
        assert result.exit_code == 0, result.output
        assert parsed["status"] == "success"
        assert parsed["metrics"]["is_valid"] is True
        expected_growth = math.pi * 9 * 4
        actual_growth = parsed["metrics"]["volume"] - original_vol
        assert abs(actual_growth - expected_growth) / expected_growth < 0.01, \
            f"expected ~{expected_growth:.2f} growth, got {actual_growth:.2f}"
        # Boss should poke up above the original top face (z=5 → z=9).
        assert parsed["metrics"]["bounding_box"]["z"][1] > 5.0


# --- error paths -----------------------------------------------------------

class TestFeatureOpErrors:
    def test_cut_pocket_negative_depth_raises_clean_error(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        ids = _inspected_ids(runner, "v1_part")
        face_id = _top_face_id(ids)
        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
profile = Sketch() + Circle(radius=3)
result = cut_pocket(base, face_id={face_id}, profile=profile, depth=-1)
show_object(result)
''',
            "should_fail",
        )
        assert result.exit_code != 0
        text = result.output.lower()
        assert "depth" in text and ("positive" in text or "negative" in text or ">" in text)
        assert "Traceback" not in result.output

    def test_boss_zero_height_raises_clean_error(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        ids = _inspected_ids(runner, "v1_part")
        face_id = _top_face_id(ids)
        result, parsed = _run_edit(
            runner, isolated_dir,
            f'''from build123d import *
base = load_step("v1_part/output.step")
profile = Sketch() + Circle(radius=3)
result = boss(base, face_id={face_id}, profile=profile, height=0)
show_object(result)
''',
            "should_fail",
        )
        assert result.exit_code != 0
        assert "height" in result.output.lower()


# --- CQ-side validation ----------------------------------------------------

class TestFeatureOpsInCadqueryScriptFails:
    @pytest.mark.parametrize("helper", ["cut_pocket", "boss"])
    def test_cq_script_using_helper_fails_with_kernel_neutral_hint(
        self, runner, isolated_dir, helper
    ):
        _init_and_import(runner, isolated_dir)
        (isolated_dir / "edit.py").write_text(
            f"""import cadquery as cq
from cadquery import importers
base = importers.importStep("v1_part/output.step")
result = {helper}(base, face_id=1, profile=None, depth=1)
show_object(result)
"""
        )
        result = runner.invoke(cli, ["run", "edit.py", "--output", "should_fail"])
        assert result.exit_code != 0
        text = result.output.lower()
        assert helper in text
        assert "build123d" in text or "importers" in text
