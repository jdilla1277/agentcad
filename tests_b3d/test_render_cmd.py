"""`agentcad render` against STEPs produced by the build123d runtime.

The render pipeline is OCP-based and runtime-agnostic — it reads
a STEP file and rasterizes via VTK. This file verifies that a STEP
written by build123d's export path is readable by the render code.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


B3D_BOX = "from build123d import Box\nshow_object(Box(20, 30, 10))\n"


def _setup_step(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "p", "--runtime", "build123d"])
    (isolated_dir / "s.py").write_text(B3D_BOX)
    runner.invoke(cli, ["run", "s.py", "--output", "box", "--no-preview"])
    return isolated_dir / "v1_box" / "output.step"


def test_render_iso(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "iso"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
    out = Path(parsed["renders"]["iso"])
    assert out.exists() and out.stat().st_size > 0


def test_render_all_named_views(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "all"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    # 'all' expands to front/right/top/iso per the docs
    for view in ("front", "right", "top", "iso"):
        assert view in parsed["renders"]


def test_render_custom_angle(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "45,30"])
    assert r.exit_code == 0, r.output
    parsed = json.loads(r.stdout)
    # Custom angles are keyed by the integer pair
    assert any("45" in k and "30" in k for k in parsed["renders"])


def test_render_zoom(runner, isolated_dir):
    step = _setup_step(runner, isolated_dir)
    r = runner.invoke(cli, ["render", str(step), "--view", "iso", "--zoom", "1.5"])
    assert r.exit_code == 0, r.output


def test_render_missing_step(runner, isolated_dir):
    r = runner.invoke(cli, ["render", "absent.step", "--view", "iso"])
    parsed = json.loads(r.stdout)
    assert r.exit_code == 1
    assert parsed["status"] == "error"
