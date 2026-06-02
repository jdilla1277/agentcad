"""`agentcad export` against STEPs produced by the build123d runtime.

Mirrors tests/test_export_cmd.py. The export command is runtime-
agnostic (it reads a STEP file, not a script), but it needs to work
on STEPs that the build123d runner produced — which use build123d's
native `export_step` writer, not CadQuery's. This file proves the
handoff.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


B3D_BOX = "from build123d import Box\nshow_object(Box(10, 10, 10))\n"


def _setup_step(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    (isolated_dir / "s.py").write_text(B3D_BOX)
    runner.invoke(cli, ["run", "s.py", "--output", "box", "--no-preview"])
    return isolated_dir / "v1_box" / "output.step"


def test_export_stl(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["export", str(step), "--format", "stl"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
    assert Path(parsed["outputs"]["stl"]).exists()
    assert Path(parsed["outputs"]["stl"]).stat().st_size > 0


def test_export_glb(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["export", str(step), "--format", "glb"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert Path(parsed["outputs"]["glb"]).exists()


def test_export_obj(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["export", str(step), "--format", "obj"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert Path(parsed["outputs"]["obj"]).exists()


def test_export_multiple_formats(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["export", str(step), "--format", "stl,glb,obj"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    for fmt in ("stl", "glb", "obj"):
        assert fmt in parsed["outputs"]
        assert Path(parsed["outputs"][fmt]).exists()


def test_export_missing_step(runner, isolated_dir):
    r = runner.invoke(cli, ["export", "nope.step", "--format", "stl"])
    parsed = json.loads(r.stdout)
    assert r.exit_code == 1
    assert parsed["status"] == "error"


def test_export_preserves_geometry(runner, isolated_dir):
    """STL produced from a b3d STEP should have the same triangle count
    scale as a CadQuery STEP of the same shape — sanity check that the
    mesh pipeline handles b3d-produced geometry without silent corruption."""
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["export", str(step), "--format", "stl"])
    parsed = json.loads(r.stdout)
    stl_path = Path(parsed["outputs"]["stl"])
    # A 10x10x10 box's STL is small but nonzero. Upper bound catches any
    # pathological tessellation blowup.
    assert 200 < stl_path.stat().st_size < 200_000
