"""Tests for `pick_face` / `pick_edge` — Phase 3 build123d preamble helpers.

The agent's perspective: they ran `agentcad inspect part.step --ids`,
spotted face_id=5 as the one to fillet, and now write an edit script
that calls `pick_face(base, 5)` to get the b3d Face — no fragile
selector queries, no guessing.
"""
import json
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad.cli import cli


def _bracket_step(directory: Path, name: str = "bracket.step") -> Path:
    plate = cq.Workplane("XY").box(40, 30, 5)
    hole = cq.Workplane("XY").circle(5).extrude(10).translate((10, 0, 0))
    part = plate.cut(hole).edges(">Z").fillet(1)
    path = directory / name
    exporters.export(part, str(path))
    return path


def _init_and_import(runner, isolated_dir, label="bracket"):
    runner.invoke(cli, ["init"])
    step = _bracket_step(isolated_dir)
    result = runner.invoke(cli, ["import", str(step), "--label", label])
    assert result.exit_code == 0, result.output


# --- pick_face / pick_edge in b3d scripts ----------------------------------

class TestPickFaceInBuild123d:
    def test_pick_face_returns_b3d_face(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        script.write_text(
            "from build123d import *\n"
            "base = load_step('v1_bracket/output.step')\n"
            "f = pick_face(base, 1)\n"
            # f must be a build123d Face — show it as a Sketch-projected solid
            "assert isinstance(f, Face), f'expected Face, got {type(f).__name__}'\n"
            "show_object(base)\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "picked"])
        assert result.exit_code == 0, result.output

    def test_pick_face_can_be_used_in_fillet(self, runner, isolated_dir):
        """The whole point: pick a face by ID and apply a real edit op."""
        _init_and_import(runner, isolated_dir)
        # Inspect with IDs to see what we have.
        inspect_result = runner.invoke(
            cli, ["inspect", "v1_bracket/output.step", "--ids"]
        )
        parsed = json.loads(inspect_result.stdout)
        # Pick the largest face by area (likely a planar face from the box)
        largest = max(parsed["faces"], key=lambda f: f["area"])
        face_id = largest["id"]

        script = isolated_dir / "edit.py"
        script.write_text(
            "from build123d import *\n"
            f"base = load_step('v1_bracket/output.step')\n"
            f"f = pick_face(base, {face_id})\n"
            "edges = f.edges()\n"
            "result = base.fillet(0.3, edges)\n"
            "show_object(result)\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "filleted"])
        assert result.exit_code == 0, result.output

    def test_pick_face_out_of_range_raises_clean_error(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        script.write_text(
            "from build123d import *\n"
            "base = load_step('v1_bracket/output.step')\n"
            "f = pick_face(base, 9999)\n"
            "show_object(base)\n"
        )
        result = runner.invoke(
            cli,
            ["run", str(script), "--output", "should_fail"],
        )
        # Failure is OK; what matters is no traceback in the JSON output.
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        # Error message must reference what's wrong.
        text = (parsed.get("error", "") + parsed.get("message", "")).lower()
        assert "id" in text or "range" in text or "9999" in text


class TestPickEdgeInBuild123d:
    def test_pick_edge_returns_b3d_edge(self, runner, isolated_dir):
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        script.write_text(
            "from build123d import *\n"
            "base = load_step('v1_bracket/output.step')\n"
            "e = pick_edge(base, 1)\n"
            "assert isinstance(e, Edge), f'expected Edge, got {type(e).__name__}'\n"
            "show_object(base)\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "edge_picked"])
        assert result.exit_code == 0, result.output


# --- pick helpers are b3d-only ---------------------------------------------

class TestPickHelpersInCadqueryScriptFails:
    def test_cq_script_using_pick_face_fails_with_kernel_neutral_hint(
        self, runner, isolated_dir
    ):
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        script.write_text(
            "import cadquery as cq\n"
            "from cadquery import importers\n"
            "base = importers.importStep('v1_bracket/output.step')\n"
            "f = pick_face(base, 1)\n"
            "show_object(base)\n"
        )
        result = runner.invoke(
            cli,
            [
                "run", str(script), "--output", "should_fail",
                "--runtime", "cadquery",
            ],
        )
        assert result.exit_code != 0
        text = result.output.lower()
        assert "pick_face" in text
        # Same kernel-neutral-fallback hint pattern as load_step.
        assert "build123d" in text or "importers" in text
