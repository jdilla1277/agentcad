"""Board-spec validation and normalization for the PCB enclosure template.

Geometry tests live in ``test_geometry.py`` so this module
stays fast and importable without the CAD kernel.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from runpy import run_path

import pytest

BoardSpec = run_path(str(Path(__file__).resolve().parents[1] / "enclosure.py"))["BoardSpec"]


def base_data():
    return {
        "name": "test-board",
        "board": {
            "width_mm": 20.0,
            "height_mm": 30.0,
            "thickness_mm": 1.6,
            "corner_radius_mm": 1.0,
        },
        "mounting_holes": [
            {"x_mm": 2.0, "y_mm": 2.0, "diameter_mm": 3.0},
        ],
        "heights": {
            "standoff_mm": 3.0,
            "bottom_max_mm": 1.5,
            "top_max_mm": 4.0,
            "lid_clearance_mm": 0.5,
        },
    }


def spec_with(**changes):
    data = copy.deepcopy(base_data())
    data.update(changes)
    return BoardSpec.from_dict(data)


def test_defaults_are_filled_in_and_derived_values_are_explicit():
    spec = BoardSpec.from_dict(base_data())

    assert spec.board == {
        "width_mm": 20.0,
        "height_mm": 30.0,
        "thickness_mm": 1.6,
        "corner_radius_mm": 1.0,
    }
    assert spec.mounting_holes == [
        {
            "x_mm": 2.0,
            "y_mm": 2.0,
            "diameter_mm": 3.0,
            "boss_diameter_mm": 6.0,
            "pilot_diameter_mm": 2.05,
        }
    ]
    assert spec.case["wall_mm"] == 2.0
    assert spec.case["floor_mm"] == 1.6
    assert spec.case["clearance_mm"] == 0.3
    assert spec.case["screw"] == "M2.5"
    assert spec.case["screw_clearance_mm"] == 2.9
    assert spec.case["screw_pilot_mm"] == 2.05
    assert spec.case["lid"] == "screw"
    assert spec.case["lid_thickness_mm"] == 2.0
    assert spec.case["lid_lip_mm"] == 1.2
    assert spec.case["lid_lip_thickness_mm"] == 1.2
    assert spec.case["lid_lip_clearance_mm"] == 0.2
    assert spec.cutouts == []
    assert spec.lid_windows == []
    assert spec.keepouts == []


def test_short_key_aliases_are_normalized():
    spec = BoardSpec.from_dict(
        {
            "board": {"width": 20, "height": 30},
            "mounting_holes": [{"x": 2, "y": 3, "diameter": 2.5}],
            "cutouts": [
                {
                    "name": "usb",
                    "edge": "bottom",
                    "center": 10,
                    "width": 6,
                    "z_max": 4.0,
                }
            ],
            "lid_windows": [
                {"name": "display", "x": 4, "y": 5, "width": 10, "height": 8}
            ],
            "keepouts": [
                {"name": "antenna", "x": 2, "y": 20, "width": 10, "height": 6}
            ],
            "heights": {"standoff": 4, "top_max": 5},
            "case": {"wall": 2.5, "lid": "screw"},
        }
    )

    assert spec.board["width_mm"] == 20.0
    assert spec.board["thickness_mm"] == 1.6
    assert spec.mounting_holes[0]["diameter_mm"] == 2.5
    assert spec.cutouts[0]["z_min_mm"] == 0.0
    assert spec.cutouts[0]["z_max_mm"] == 4.0
    assert spec.keepouts[0]["box_mm"] == (2.0, 20.0, 0.0, 12.0, 26.0, 6.0)
    assert spec.heights["standoff_mm"] == 4.0
    assert spec.heights["top_max_mm"] == 5.0
    assert spec.case["wall_mm"] == 2.5


def test_to_dict_is_a_deep_copy():
    spec = BoardSpec.from_dict(base_data())
    data = spec.to_dict()
    data["board"]["width_mm"] = 99

    assert spec.board["width_mm"] == 20.0


def test_from_json_reads_and_validates_a_file(tmp_path):
    path = tmp_path / "board.json"
    path.write_text(json.dumps(base_data()), encoding="utf-8")

    spec = BoardSpec.from_json(path)

    assert spec.board["height_mm"] == 30.0
    assert spec.case["screw"] == "M2.5"


def test_from_json_reports_missing_and_malformed_files(tmp_path):
    with pytest.raises(ValueError, match="board spec not found"):
        BoardSpec.from_json(tmp_path / "missing.json")

    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        BoardSpec.from_json(path)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"unknown": True}, "unknown top-level"),
        ({"board": {"width_mm": 0, "height_mm": 30}}, "width_mm"),
        (
            {"board": {"width_mm": 20, "height_mm": 30, "corner_radius_mm": 10}},
            "corner_radius_mm must be less than half",
        ),
        (
            {"board": {"width_mm": 20, "height_mm": 30, "corner_radius_mm": 11}},
            "corner_radius",
        ),
        ({"heights": {"standoff_mm": 1.0, "bottom_max_mm": 2.0}}, "standoff"),
        (
            {"mounting_holes": [{"x_mm": 25, "y_mm": 2, "diameter_mm": 3}]},
            "inside the board outline",
        ),
        (
            {
                "mounting_holes": [
                    {
                        "x_mm": 2,
                        "y_mm": 2,
                        "diameter_mm": 3,
                        "boss_diameter_mm": 2.0,
                    }
                ]
            },
            "must exceed",
        ),
        (
            {
                "cutouts": [
                    {
                        "edge": "bottom",
                        "center_mm": 1,
                        "width_mm": 4,
                        "height_mm": 3,
                    }
                ]
            },
            "inside the board edge",
        ),
        (
            {
                "cutouts": [
                    {
                        "edge": "right",
                        "center_mm": 15,
                        "width_mm": 4,
                        "z_max_mm": 20,
                    }
                ]
            },
            "cavity above the board",
        ),
        (
            {
                "lid_windows": [
                    {"x_mm": 18, "y_mm": 2, "width_mm": 4, "height_mm": 3}
                ]
            },
            "inside the board outline",
        ),
        (
            {"keepouts": [{"box_mm": [5, 5, 0, 4, 6, 2]}]},
            "greater than minimums",
        ),
        ({"case": {"screw": "M9"}}, "screw must be one of"),
        ({"case": {"lid": "snap"}}, "lid must be"),
        ({"case": {"lid_lip_mm": 10}}, "cannot exceed"),
        (
            {"case": {"lid_lip_thickness_mm": 20}},
            "too large for the cavity",
        ),
        (
            {
                "cutouts": [
                    {
                        "edge": "bottom",
                        "center_mm": 10,
                        "width_mm": 4,
                    }
                ]
            },
            "needs height_mm or z_max_mm",
        ),
    ],
)
def test_invalid_specs_report_actionable_errors(changes, message):
    data = copy.deepcopy(base_data())

    def merge(target, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    merge(data, changes)
    with pytest.raises(ValueError, match=message):
        BoardSpec.from_dict(data)


def test_mounting_hole_names_and_explicit_case_values_are_preserved():
    spec = spec_with(
        mounting_holes=[
            {
                "x_mm": 2,
                "y_mm": 2,
                "diameter_mm": 3,
                "boss_diameter_mm": 7,
                "pilot_diameter_mm": 2.2,
            }
        ],
        case={
            "wall_mm": 2.5,
            "floor_mm": 1.8,
            "clearance_mm": 0.4,
            "screw": "M3",
            "screw_clearance_mm": 3.6,
            "screw_pilot_mm": 2.7,
        },
    )

    hole = spec.mounting_holes[0]
    assert hole["boss_diameter_mm"] == 7.0
    assert hole["pilot_diameter_mm"] == 2.2
    assert spec.case["screw_clearance_mm"] == 3.6
    assert spec.case["screw_pilot_mm"] == 2.7
    assert spec.case["screw"] == "M3"


def test_keepout_boxes_allow_the_floor_below_the_board():
    spec = spec_with(
        keepouts=[{"name": "under-board", "box_mm": [-2, -2, -5, 2, 2, -1]}]
    )

    assert spec.keepouts[0]["box_mm"] == (-2.0, -2.0, -5.0, 2.0, 2.0, -1.0)


def test_lid_none_is_accepted_by_the_spec():
    spec = spec_with(case={"lid": "none"})
    assert spec.case["lid"] == "none"
