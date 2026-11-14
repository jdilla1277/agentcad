"""Geometry checks for the PCB enclosure template against the example spec."""

from __future__ import annotations

import json
from pathlib import Path
from runpy import run_path
import shutil

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Pnt

from agentcad.runners import build123d as b3d_runner

RECIPE = Path(__file__).resolve().parents[1]
helpers = run_path(str(RECIPE / "enclosure.py"))
BoardSpec = helpers["BoardSpec"]
pcb_case = helpers["pcb_case"]
pcb_lid = helpers["pcb_lid"]
pcb_enclosure = helpers["pcb_enclosure"]
pcb_enclosure_lid = helpers["pcb_enclosure_lid"]


EXAMPLE = RECIPE / "board.json"
RIM = 10.6
LID_TOP = 12.6
LID_BOTTOM = 9.4


@pytest.fixture(scope="module")
def spec():
    return BoardSpec.from_json(EXAMPLE)


@pytest.fixture(scope="module")
def case(spec):
    return pcb_case(spec)


@pytest.fixture(scope="module")
def lid(spec):
    return pcb_lid(spec)


def material(shape, box):
    """Volume of ``shape`` inside ``[xmin, ymin, zmin, xmax, ymax, zmax]``."""
    xmin, ymin, zmin, xmax, ymax, zmax = box
    tool = BRepPrimAPI_MakeBox(
        gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)
    ).Shape()
    common = BRepAlgoAPI_Common(shape.wrapped, tool)
    common.Build()
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(common.Shape(), props)
    return props.Mass()


def solid_count(shape):
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def test_case_is_one_valid_solid_with_expected_envelope(case):
    assert case.is_valid
    assert case.volume > 0
    assert len(case.solids()) == 1

    bounds = case.bounding_box()
    assert bounds.min.X == pytest.approx(-2.3, abs=1e-6)
    assert bounds.min.Y == pytest.approx(-2.3, abs=1e-6)
    assert bounds.min.Z == pytest.approx(-4.6, abs=1e-6)
    assert bounds.max.X == pytest.approx(30.47, abs=1e-6)
    assert bounds.max.Y == pytest.approx(53.61, abs=1e-6)
    assert bounds.max.Z == pytest.approx(RIM, abs=1e-6)
    assert bounds.size.X == pytest.approx(32.77, abs=1e-6)
    assert bounds.size.Y == pytest.approx(55.91, abs=1e-6)
    assert bounds.size.Z == pytest.approx(15.2, abs=1e-6)


def test_lid_is_one_valid_solid_with_expected_envelope(lid):
    assert lid.is_valid
    assert lid.volume > 0
    assert len(lid.solids()) == 1

    bounds = lid.bounding_box()
    assert bounds.min.X == pytest.approx(-2.3, abs=1e-6)
    assert bounds.min.Y == pytest.approx(-2.3, abs=1e-6)
    assert bounds.min.Z == pytest.approx(LID_BOTTOM, abs=1e-6)
    assert bounds.max.Z == pytest.approx(LID_TOP, abs=1e-6)
    assert bounds.size.X == pytest.approx(32.77, abs=1e-6)
    assert bounds.size.Y == pytest.approx(55.91, abs=1e-6)
    assert bounds.size.Z == pytest.approx(3.2, abs=1e-6)


def test_aliases_build_the_same_shapes(spec, case, lid):
    assert pcb_enclosure(spec).volume == pytest.approx(case.volume, abs=1e-6)
    assert pcb_enclosure_lid(spec).volume == pytest.approx(lid.volume, abs=1e-6)


def test_usb_opening_is_clear_and_the_surrounding_wall_is_not(case):
    assert material(case, (10.3, -2.2, 0.1, 17.85, -0.4, 4.5)) == pytest.approx(
        0.0, abs=1e-6
    )
    assert material(case, (10.3, -2.2, 4.75, 17.85, -0.4, 5.5)) > 5.0
    assert material(case, (0.0, -2.3, 6.0, 5.0, -0.3, 9.0)) > 10.0


def test_side_button_opening_is_clear_and_the_surrounding_wall_is_not(case):
    assert material(case, (28.6, 23.0, 0.7, 30.2, 28.5, 2.9)) == pytest.approx(
        0.0, abs=1e-6
    )
    assert material(case, (28.6, 15.0, 0.7, 30.2, 20.0, 2.9)) > 5.0
    assert material(case, (28.6, 23.0, 3.2, 30.2, 28.5, 4.2)) > 1.0


def test_standoff_bosses_have_material_and_pilot_holes_are_clear(case):
    assert material(case, (1.5, 1.5, -2.0, 4.5, 4.5, -0.2)) == pytest.approx(
        10.25885559298, abs=1e-6
    )
    assert material(case, (2.8, 2.8, -2.0, 3.2, 3.2, -0.2)) == pytest.approx(
        0.0, abs=1e-6
    )


def test_lid_antenna_window_and_screw_holes_are_clear(lid):
    assert material(lid, (10.0, 30.0, 11.1, 12.0, 32.0, 12.3)) == pytest.approx(
        0.0, abs=1e-6
    )
    assert material(lid, (2.7, 2.7, 11.1, 3.3, 3.3, 12.3)) == pytest.approx(
        0.0, abs=1e-6
    )
    assert material(lid, (0.0, 10.0, 11.1, 5.0, 15.0, 12.3)) > 20.0


def test_keepouts_remove_lid_material(spec):
    data = spec.to_dict()
    data["lid_windows"] = []
    data["keepouts"] = [
        {"name": "lid_keepout", "box_mm": [10.0, 30.0, 9.0, 12.0, 32.0, 13.0]}
    ]
    lid = pcb_lid(BoardSpec.from_dict(data))

    assert material(lid, (10.2, 30.2, 11.0, 11.8, 31.8, 12.2)) == pytest.approx(
        0.0, abs=1e-6
    )


def test_keepouts_remove_case_material(spec):
    data = spec.to_dict()
    data["keepouts"] = [
        {"name": "boss_keepout", "box_mm": [1.5, 1.5, -2.5, 4.5, 4.5, -0.2]}
    ]
    case = pcb_case(BoardSpec.from_dict(data))

    assert material(case, (1.5, 1.5, -2.0, 4.5, 4.5, -0.2)) == pytest.approx(
        0.0, abs=1e-6
    )


def test_lid_none_refuses_to_build_a_lid(spec):
    data = spec.to_dict()
    data["case"]["lid"] = "none"
    with pytest.raises(ValueError, match="case.lid to 'none'"):
        pcb_lid(BoardSpec.from_dict(data))


def test_cutout_near_rim_uses_board_underside_coordinates():
    spec = BoardSpec({
        "board": {"width_mm": 20, "height_mm": 30, "thickness_mm": 1.6},
        "heights": {"top_max_mm": 4, "lid_clearance_mm": 0.5},
        "cutouts": [{
            "edge": "bottom", "center_mm": 10, "width_mm": 6,
            "z_min_mm": 4, "z_max_mm": 5,
        }],
    })
    tray = pcb_case(spec)
    assert material(tray, (8, -2.2, 4.1, 12, -0.4, 4.9)) == pytest.approx(0, abs=1e-6)
    assert material(tray, (8, -2.2, 5.1, 12, -0.4, 5.9)) > 1


@pytest.mark.parametrize("edge", ["bottom", "top", "left", "right"])
@pytest.mark.parametrize("lip_clearance,lip_thickness", [(0.2, 1.2), (0.8, 2.4)])
def test_connector_opening_clears_the_assembled_wall_and_lip(edge, lip_clearance, lip_thickness):
    from build123d import Compound

    center = 10 if edge in {"bottom", "top"} else 15
    spec = BoardSpec({
        "board": {"width_mm": 20, "height_mm": 30, "thickness_mm": 1.6},
        "heights": {"top_max_mm": 4, "lid_clearance_mm": 0.5},
        "case": {
            "lid_lip_clearance_mm": lip_clearance,
            "lid_lip_thickness_mm": lip_thickness,
        },
        "cutouts": [{
            "edge": edge, "center_mm": center, "width_mm": 6,
            "z_min_mm": 5, "z_max_mm": 6,
        }],
    })
    tray = pcb_case(spec)
    lid = pcb_lid(spec)
    assembly = Compound(children=[tray, lid])
    outer = -0.3 - 2
    inner = -0.3 + lip_clearance + lip_thickness
    passage = {
        "bottom": [7, outer, 5, 13, inner, 6],
        "top": [7, 30 - inner, 5, 13, 30 - outer, 6],
        "left": [outer, 12, 5, inner, 18, 6],
        "right": [20 - inner, 12, 5, 20 - outer, 18, 6],
    }[edge]
    assert assembly.is_valid
    assert len(assembly.solids()) == 2
    assert material(assembly, passage) == pytest.approx(0, abs=1e-6)

    # The wall and lip immediately above the declared opening remain intact.
    above = [*passage]
    above[2], above[5] = 6.02, 6.08
    assert material(tray, above) > 0.1
    assert material(lid, above) > 0.1


@pytest.mark.parametrize("builder", [pcb_case, pcb_lid])
def test_corner_radius_boundary_is_checked_before_building(builder):
    data = {"board": {"width_mm": 20, "height_mm": 30, "corner_radius_mm": 9.9}}
    part = builder(data)
    assert part.is_valid
    assert len(part.solids()) == 1
    assert part.volume > 0

    data["board"]["corner_radius_mm"] = 10
    with pytest.raises(ValueError, match="corner_radius_mm must be less than half"):
        builder(data)


@pytest.mark.parametrize("lip_depth", [1.2, 3.0])
def test_screw_passage_clears_the_entire_lip(lip_depth):
    from build123d import Align, Cylinder, Pos

    spec = BoardSpec({
        "board": {"width_mm": 20, "height_mm": 30, "thickness_mm": 1.6},
        "mounting_holes": [{"x_mm": 2, "y_mm": 2, "diameter_mm": 3}],
        "heights": {"top_max_mm": 4, "lid_clearance_mm": 0.5},
        "case": {"lid_lip_mm": lip_depth},
    })
    lid = pcb_lid(spec)
    rim = 6.1
    screw_path = Pos(2, 2, rim - lip_depth) * Cylinder(
        spec.case["screw_clearance_mm"] / 2,
        lip_depth + spec.case["lid_thickness_mm"],
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    assert (lid & screw_path).volume == pytest.approx(0, abs=1e-6)


def test_copied_recipe_builds_and_checks_its_export(tmp_path, monkeypatch):
    from click.testing import CliRunner
    from agentcad.cli import cli

    copied = tmp_path / "my-board"
    shutil.copytree(RECIPE, copied, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "build"))
    monkeypatch.chdir(copied)
    monkeypatch.setenv("AGENTCAD_DAEMON", "1")
    runner = CliRunner()
    initialized = runner.invoke(cli, ["init", "--runtime", "build123d", "--no-agent-setup"])
    assert initialized.exit_code == 0, initialized.output
    result = runner.invoke(cli, [
        "run", "example.py", "--label", "recipe", "--no-preview", "--no-diff",
        "--no-view", "--no-daemon",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    exported = Path(payload["outputs"]["step"])
    assert exported.is_relative_to(copied / "build")
    checked = runner.invoke(cli, ["check-spec", str(exported), "checks.json", "--no-daemon"])
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.stdout)["passed"] is True


def test_template_runs_through_the_public_script_api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "board.json").write_text(json.dumps({
        "board": {"width_mm": 20, "height_mm": 30},
        "mounting_holes": [{"x_mm": 2, "y_mm": 2, "diameter_mm": 3}],
        "cutouts": [
            {
                "name": "usb",
                "edge": "bottom",
                "center_mm": 10,
                "width_mm": 6,
                "height_mm": 3,
            }
        ],
        "lid_windows": [
            {"name": "window", "x_mm": 4, "y_mm": 10, "width_mm": 10, "height_mm": 8}
        ],
    }), encoding="utf-8")

    result = b3d_runner.execute(
        f"from runpy import run_path\nhelpers = run_path({str(RECIPE / 'enclosure.py')!r})\n"
        "BoardSpec = helpers['BoardSpec']\npcb_case = helpers['pcb_case']\npcb_lid = helpers['pcb_lid']\n"
        "spec = BoardSpec.from_json('board.json')\n"
        "show_object(pcb_case(spec))\n"
        "show_object(pcb_lid(spec))\n"
    )

    assert result.success, result.exception
    assert result.topo_shape is not None
    assert type(result.topo_shape).__name__ == "TopoDS_Compound"
    assert solid_count(result.topo_shape) == 2
