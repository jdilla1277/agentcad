"""Occupied volume survives authored compound and STEP measurement workflows."""
# collect-on-default-profile
import importlib.util
import json
import math
from pathlib import Path

import pytest

from agentcad.cli import cli


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
def test_embedded_annular_boss_counts_only_added_material(runner, isolated_dir, runtime):
    if runtime == "cadquery" and importlib.util.find_spec("cadquery") is None:
        pytest.skip("optional CadQuery extra is absent")
    if runtime == "cadquery":
        source = '''import cadquery as cq
base = cq.Workplane("XY").box(40,30,6).edges("|Z").fillet(2).faces(">Z").workplane().hole(8)
show_object(raise_annulus(base, center=(0,0), inner_diameter=8, outer_diameter=16, height=6, z=1))
'''
    else:
        source = '''from build123d import *
base = Box(40,30,6)
base = fillet(base.edges().filter_by(Axis.Z), 2) - Cylinder(4,20)
show_object(raise_annulus(base, center=(0,0), inner_diameter=8, outer_diameter=16, height=6, z=1))
'''
    (isolated_dir / "bracket.py").write_text(source)
    assert runner.invoke(cli, ["init", "--runtime", runtime, "--no-agent-setup"]).exit_code == 0
    response = runner.invoke(cli, ["run", "bracket.py", "--label", "boss", "--no-preview", "--no-view", "--no-diff", "--no-daemon"])
    assert response.exit_code == 0, response.output
    data = json.loads(response.stdout)
    # Filleted plate minus its through-hole, plus only the 4 mm of exposed ring.
    expected = (40 * 30 - (4 - math.pi) * 2**2 - math.pi * 4**2) * 6 + math.pi * (8**2 - 4**2) * 4
    assert data["metrics"]["volume"] == pytest.approx(expected, abs=0.001)
    assert data["metrics"]["reliable"] is True
    assert data["metrics"]["volume_measurement"]["basis"] == "occupied_space"
    step = Path(data["outputs"]["step"])
    before = step.read_bytes()
    measured = runner.invoke(cli, ["measure", str(step), "--no-daemon"])
    assert measured.exit_code == 0, measured.output
    measured_data = json.loads(measured.stdout)
    assert measured_data["metrics"]["volume"] == pytest.approx(expected, abs=0.001)
    assert step.read_bytes() == before
    from agentcad.step_io import load_cad_shape
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    solids = TopExp_Explorer(load_cad_shape(step), TopAbs_SOLID)
    count = 0
    while solids.More():
        count += 1
        solids.Next()
    assert count == 2  # Measuring occupied space never fuses the delivered parts.
