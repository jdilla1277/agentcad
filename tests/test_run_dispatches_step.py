"""`agentcad run` dispatches CAD files to the import path (Phase 2 slice 2b).

The footgun this closes: an agent reading agentcad's JSON output sees
that `agentcad run` is the everything-command, then runs `agentcad run
widget.step` and gets a confusing parse error. Now that just works —
suffix dispatch routes to import.
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


class TestRunDispatchesStep:
    def test_run_step_file_dispatches_to_import(self, runner, isolated_dir):
        """`agentcad run widget.step --output rev_a` produces the same
        result as `agentcad import widget.step --label rev_a`."""
        runner.invoke(cli, ["init"])
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["run", str(step), "--output", "rev_a"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        # Response identifies as an import — honest about what happened.
        assert parsed["command"] == "import"
        assert parsed["status"] == "success"
        assert parsed["label"] == "rev_a"
        assert (isolated_dir / "v1_rev_a" / "output.step").exists()
        assert (isolated_dir / "v1_rev_a" / "source.step").exists()

    def test_run_brep_file_dispatches_to_import(self, runner, isolated_dir):
        """BREP files dispatch too — anything in Tier 0."""
        from OCP.BRepTools import BRepTools
        plate = cq.Workplane("XY").box(20, 20, 5)
        brep_path = isolated_dir / "thing.brep"
        BRepTools.Write_s(plate.val().wrapped, str(brep_path))

        runner.invoke(cli, ["init"])
        result = runner.invoke(cli, ["run", str(brep_path), "--output", "stub"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        assert parsed["command"] == "import"
        assert (isolated_dir / "v1_stub" / "source.brep").exists()

    def test_run_python_script_unchanged(self, runner, isolated_dir):
        """No regression: a real Python script still runs as a build, not
        as an import."""
        runner.invoke(cli, ["init"])
        script = isolated_dir / "build.py"
        script.write_text(
            "from build123d import *\n"
            "show_object(Box(10, 20, 5))\n"
        )
        result = runner.invoke(cli, ["run", str(script), "--output", "build_v1"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        # Real script goes through run, not import.
        assert parsed["command"] == "run"
        assert parsed["status"] == "success"

    def test_run_stl_returns_polite_limited(self, runner, isolated_dir):
        """STL is Tier 1 — same polite-no response as `agentcad import`."""
        runner.invoke(cli, ["init"])
        stl = isolated_dir / "part.stl"
        stl.write_text(
            "solid x\nfacet normal 0 0 1\nouter loop\n"
            "vertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\n"
            "endloop\nendfacet\nendsolid x\n"
        )
        result = runner.invoke(cli, ["run", str(stl), "--output", "ignored"])
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "limited"
        assert parsed["format_detected"] == "stl"

    def test_run_step_in_empty_dir_points_at_import_init(
        self, runner, isolated_dir
    ):
        """No manifest yet. Error must teach the agent how to recover —
        `run` doesn't have --init, but `import --init` does."""
        # No `agentcad init` first.
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["run", str(step), "--output", "rev_a"])
        assert result.exit_code != 0
        parsed = json.loads(result.stdout)
        # Suggestion must point the agent at the working recovery path.
        text = (parsed.get("message", "") + parsed.get("suggestion", "")).lower()
        assert "import" in text and "--init" in text

    def test_run_step_response_includes_scaffold(self, runner, isolated_dir):
        """Dispatched run benefits from 2a's scaffold writing — agent
        invokes `agentcad run widget.step` and gets a v1 + edit.py."""
        runner.invoke(cli, ["init"])
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["run", str(step), "--output", "rev_a"])
        parsed = json.loads(result.stdout)
        assert parsed.get("scaffold") == "edit.py"
        assert (isolated_dir / "edit.py").exists()
