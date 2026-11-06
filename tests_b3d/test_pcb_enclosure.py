"""Geometry checks for the PCB enclosure template against the example spec."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.gp import gp_Pnt

from agentcad.pcb_enclosure import BoardSpec, pcb_case, pcb_enclosure, pcb_enclosure_lid, pcb_lid
from agentcad.runners import build123d as b3d_runner


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "pcb_enclosure" / "board.json"
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
        "from agentcad.api import BoardSpec, pcb_case, pcb_lid\n"
        "spec = BoardSpec.from_json('board.json')\n"
        "show_object(pcb_case(spec))\n"
        "show_object(pcb_lid(spec))\n"
    )

    assert result.success, result.exception
    assert result.topo_shape is not None
    assert type(result.topo_shape).__name__ == "TopoDS_Compound"
    assert solid_count(result.topo_shape) == 2
