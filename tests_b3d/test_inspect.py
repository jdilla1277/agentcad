"""`agentcad inspect` against STEPs produced by the build123d runtime."""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


B3D_BOX = "from build123d import Box\nshow_object(Box(10, 10, 10))\n"

B3D_WITH_HOLE = """\
from build123d import Box, Cylinder
plate = Box(40, 30, 5)
hole = Cylinder(radius=4, height=20)
show_object(plate - hole)
"""


def _run(runner, isolated_dir, script):
    runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    (isolated_dir / "s.py").write_text(script)
    runner.invoke(cli, ["run", "s.py", "--output", "v", "--no-preview"])
    return isolated_dir / "v1_v" / "output.step"


def test_inspect_box_returns_valid(runner, isolated_dir):
    step = _run(runner, isolated_dir, B3D_BOX)
    r = runner.invoke(cli, ["inspect", str(step)])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
    assert parsed["is_valid"] is True
    assert parsed["solid_count"] == 1
    assert parsed["face_count"] == 6
    assert parsed["edge_count"] == 12


def test_inspect_plate_with_hole(runner, isolated_dir):
    step = _run(runner, isolated_dir, B3D_WITH_HOLE)
    r = runner.invoke(cli, ["inspect", str(step)])
    parsed = json.loads(r.stdout)
    assert parsed["is_valid"] is True
    # Box has 6 faces, cutting a through-hole adds 1 cylindrical face
    # and removes 2 spots from top + bottom (merging with the hole).
    # Exact count: 7 faces (6 orig - 2 hole spots + 2 hole endcaps merged,
    # plus 1 cylindrical wall). The exact number isn't the point —
    # it should just be > 6 and the shell should still be closed.
    assert parsed["face_count"] > 6
    assert parsed["shell_count"] == 1


def test_inspect_missing_step(runner, isolated_dir):
    r = runner.invoke(cli, ["inspect", "missing.step"])
    parsed = json.loads(r.stdout)
    assert r.exit_code == 1
    assert parsed["status"] == "error"
