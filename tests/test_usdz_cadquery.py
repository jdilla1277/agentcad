"""USDZ export from the CadQuery compatibility runtime (needs both extras)."""

import importlib.util
import json
from pathlib import Path

import pytest

from agentcad.cli import cli

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("pxr") is None, reason='needs pip install "agentcad[usdz]"'
)

SCRIPT = (
    "import cadquery as cq\n"
    'plate = cq.Workplane("XY").box(40, 20, 4).faces(">Z").workplane().hole(6)\n'
    'cap = cq.Workplane("XY").box(4, 4, 8).translate((35, 0, 0))\n'
    'show_object(plate, name="plate", options={"color": "orange"})\n'
    'show_object(cap, name="cap")\n'
)


def test_cadquery_run_and_export_write_valid_usdz(runner, isolated_dir):
    assert runner.invoke(cli, ["init", "--runtime", "cadquery"]).exit_code == 0
    (isolated_dir / "part.py").write_text(SCRIPT)
    result = runner.invoke(cli, ["run", "part.py", "--label", "cq", "--export", "usdz",
                                 "--no-preview", "--no-view", "--no-diff", "--no-daemon"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["mesh_validation"]["usdz"]["is_valid"] is True, data["mesh_validation"]
    assert data["mesh_validation"]["usdz"]["layers"]["file_parse"]["usdz"]["mesh_prim_count"] == 2

    step = isolated_dir / "v1_cq" / "output.step"
    exported = runner.invoke(cli, ["export", str(step), "--format", "usdz", "--no-daemon"])
    assert exported.exit_code == 0, exported.output
    report = json.loads(exported.stdout)["mesh_validation"]["usdz"]
    assert report["is_valid"] is True
    assert Path(report["checked_file"]).name == "output.usdz"
