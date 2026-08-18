"""Tests for `load_step` / `load_step_shape` — Phase 2 b3d preamble helpers.

These run *inside* a build123d script (the agent's perspective). The b3d
runner injects them into script globals; CQ scripts don't see them
(b3d-only contract).
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


# --- load_step in b3d scripts -----------------------------------------------

class TestLoadStepInBuild123dScript:
    def test_load_step_returns_b3d_part(self, runner, isolated_dir):
        """Agent writes `base = load_step(...)` and shows it. Should produce
        a valid v2 with the imported geometry."""
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        script.write_text(
            "from build123d import *\n"
            "base = load_step('v1_bracket/output.step')\n"
            "show_object(base)\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "loaded"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["runtime"] == "build123d"
        # Volume should approximately match the imported bracket.
        assert parsed["metrics"]["volume"] > 0

    def test_load_step_supports_b3d_operators(self, runner, isolated_dir):
        """Confirms the returned object behaves like a build123d Shape —
        the whole point of returning a Part type, not a raw TopoDS_Shape."""
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        # Use b3d's algebraic API on the loaded part.
        script.write_text(
            "from build123d import *\n"
            "base = load_step('v1_bracket/output.step')\n"
            "tab = Box(10, 10, 5).move(Location((25, 0, 0)))\n"
            "result = base + tab\n"
            "show_object(result)\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "with_tab"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        # Volume should be roughly bracket + tab, larger than bracket alone.
        assert parsed["metrics"]["volume"] > 5000

    def test_load_step_shape_returns_raw_topods(self, runner, isolated_dir):
        """`load_step_shape` skips the .val().wrapped dance — gives raw
        TopoDS_Shape suitable for raw-shape helpers (mirror_fuse etc.)."""
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        script.write_text(
            "from build123d import *\n"
            "raw = load_step_shape('v1_bracket/output.step')\n"
            # mirror_fuse is a raw-shape helper from agentcad.helpers
            "mirrored = mirror_fuse(raw, plane='XZ')\n"
            "show_object(Part(mirrored))\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "mirrored"])
        assert result.exit_code == 0, result.output

    def test_imported_shape_copy_and_rotate_are_independent(
        self, runner, isolated_dir
    ):
        """The pre-injected import-pattern helpers must break shared topology
        before a transform, while leaving the source available unchanged."""
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        script.write_text(
            "from build123d import *\n"
            "raw = load_step_shape('v1_bracket/output.step')\n"
            "copied = copy_shape(raw)\n"
            "rotated = rotate(raw, 'Z', 17)\n"
            "assert not raw.IsPartner(copied)\n"
            "assert not raw.IsPartner(rotated)\n"
            "show_object(Part(rotated))\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "rotated"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert parsed["metrics"]["is_valid"] is True


# --- load_step is b3d-only --------------------------------------------------

class TestLoadStepInCadqueryScriptFails:
    def test_cq_script_using_load_step_fails_with_kernel_neutral_hint(
        self, runner, isolated_dir
    ):
        """Per the b3d-only edit-helpers contract: a CQ script calling
        load_step gets a clean error pointing at the kernel-neutral fallback,
        not a bare NameError."""
        _init_and_import(runner, isolated_dir)
        script = isolated_dir / "edit.py"
        # Explicit `import cadquery as cq` routes to the CQ runner.
        script.write_text(
            "import cadquery as cq\n"
            "base = load_step('v1_bracket/output.step')\n"
            "show_object(base)\n"
        )
        result = runner.invoke(
            cli,
            [
                "run", str(script), "--output", "should_fail",
                "--runtime", "cadquery",
            ],
        )
        # Validation or execution error — either way, not silent success.
        assert result.exit_code != 0
        # Error message must point at the kernel-neutral path.
        text = result.output.lower()
        assert "load_step" in text
        assert "importers.importstep" in text or "build123d" in text
