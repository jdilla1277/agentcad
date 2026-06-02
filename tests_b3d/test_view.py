"""`agentcad view` against GLBs/STEPs from the build123d runtime.

`view` produces an HTML viewer embedding the model. The file produced
must exist, be non-empty, and mention the source file. The autouse
`_no_browser_launch` fixture in conftest stubs the real browser open.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


B3D_BOX = "from build123d import Box\nshow_object(Box(10, 10, 10))\n"


def _setup(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    (isolated_dir / "s.py").write_text(B3D_BOX)
    runner.invoke(cli, ["run", "s.py", "--output", "v", "--export", "glb",
                        "--no-preview"])
    return isolated_dir / "v1_v"


def test_view_b3d_step(runner, isolated_dir):
    """`view` on a build123d-produced STEP auto-converts to GLB and
    writes a viewer HTML."""
    version_dir = _setup(runner, isolated_dir)
    step = version_dir / "output.step"
    r = runner.invoke(cli, ["view", str(step)])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"


def test_view_b3d_glb(runner, isolated_dir):
    version_dir = _setup(runner, isolated_dir)
    glb = version_dir / "output.glb"
    r = runner.invoke(cli, ["view", str(glb)])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
