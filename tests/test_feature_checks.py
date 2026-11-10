"""Focused contract tests for shared geometric feature probes."""

from __future__ import annotations

import pytest

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from agentcad.feature_checks import (
    box_key,
    check_positioned_cylinders,
    check_probe,
    extract_cylinders,
    material_volumes,
    validate_positions,
)


def test_positioned_cylinders_accepts_a_solid_pin():
    pin = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(5, 7, 0), gp_Dir(0, 0, 1)), 1.5, 10
    ).Shape()

    passed, observed = check_positioned_cylinders(
        extract_cylinders(pin),
        axis="z",
        diameter_mm=3.0,
        positions_mm=[[5, 7]],
        position_tolerance_mm=0.01,
    )

    assert passed is True
    assert observed["matches"][0]["measured_mm"] == [5.0, 7.0]
    assert observed["extra_cylinders"] == []


def test_positioned_cylinders_bounds_measured_candidates():
    cylinders = [
        {
            "diameter_mm": 3.0,
            "direction": [0, 0, 1],
            "center_mm": [float(index), 0, 0],
            "face_indices": [index + 1],
        }
        for index in range(201)
    ]

    with pytest.raises(ValueError, match="more than 200 measured cylinders"):
        check_positioned_cylinders(
            cylinders,
            axis="z",
            diameter_mm=3.0,
            positions_mm=[[0, 0]],
        )


def test_validate_positions_uses_the_shared_200_position_limit():
    positions = [[float(index), 0.0] for index in range(200)]
    assert len(validate_positions(positions)) == 200

    with pytest.raises(ValueError, match="at most 200 positions"):
        validate_positions([*positions, [200.0, 0.0]])


def test_box_key_preserves_distinct_probe_boxes():
    smaller = [0, 0, 0, 100, 100, 1.0000001]
    larger = [0, 0, 0, 100, 100, 1.0000004]

    assert box_key(smaller) != box_key(larger)


def test_probe_boxes_keep_separate_volumes_in_both_orders():
    shape = BRepPrimAPI_MakeBox(
        gp_Pnt(0, 0, 0), gp_Pnt(100, 100, 10)
    ).Shape()
    smaller = [0, 0, 0, 100, 100, 1.0000001]
    larger = [0, 0, 0, 100, 100, 1.0000004]

    for boxes in ([smaller, larger], [larger, smaller]):
        measured = {
            tuple(row["box_mm"]): row["volume_mm3"]
            for row in material_volumes(shape, boxes)
        }
        assert measured[tuple(smaller)] == pytest.approx(10000.001, abs=1e-5)
        assert measured[tuple(larger)] == pytest.approx(10000.004, abs=1e-5)

    # Measuring the larger probe first must not satisfy the smaller probe.
    rows = material_volumes(shape, [larger, smaller])
    passed, observed = check_probe(
        rows,
        kind="material",
        box_mm=smaller,
        threshold_mm3=10000.002,
    )
    assert passed is False
    assert observed["volume_mm3"] == pytest.approx(10000.001, abs=1e-5)

    passed, observed = check_probe(
        rows,
        kind="material",
        box_mm=larger,
        threshold_mm3=10000.002,
    )
    assert passed is True
    assert observed["volume_mm3"] == pytest.approx(10000.004, abs=1e-5)
