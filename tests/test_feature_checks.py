"""Focused contract tests for shared geometric feature probes."""

from __future__ import annotations

import pytest

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

from agentcad.feature_checks import (
    MIN_PROBE_EXTENT_MM,
    box_key,
    check_positioned_cylinders,
    check_probe,
    extract_cylinders,
    material_volumes,
    validate_positions,
    validate_solid_occupancy,
)


def _box(xmin=0, ymin=0, zmin=0, xmax=10, ymax=10, zmax=10):
    return BRepPrimAPI_MakeBox(
        gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)
    ).Shape()


def _compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


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


def test_extract_cylinders_propagates_face_inspection_failures(monkeypatch):
    def fail(face):
        raise RuntimeError("injected surface inspection failure")

    monkeypatch.setattr("OCP.BRepAdaptor.BRepAdaptor_Surface", fail)

    with pytest.raises(RuntimeError, match="injected surface inspection failure"):
        extract_cylinders(_box())


def test_probe_rejects_a_reversed_solid_instead_of_measuring_its_complement():
    reversed_box = _box().Reversed()
    inside = [1, 1, 1, 2, 2, 2]
    outside = [20, 20, 20, 21, 21, 21]

    with pytest.raises(ValueError, match="positive finite signed volume"):
        validate_solid_occupancy(reversed_box)

    rows = material_volumes(reversed_box, [inside, outside])
    assert len(rows) == 2
    for row in rows:
        assert "error" in row
        assert "positive finite signed volume" in row["error"]
        assert "volume_mm3" not in row
        with pytest.raises(ValueError, match="Material probe failed for box"):
            check_probe(
                rows,
                kind="keepout",
                box_mm=row["box_mm"],
                threshold_mm3=0,
            )


def test_probe_rejects_mixed_solid_orientations_with_positive_total_volume():
    normal = _box(0, 0, 0, 20, 10, 10)
    reversed_box = _box(0, 20, 0, 10, 30, 10).Reversed()
    mixed = _compound(normal, reversed_box)

    with pytest.raises(ValueError, match="solid 2"):
        validate_solid_occupancy(mixed)

    rows = material_volumes(mixed, [[1, 1, 1, 2, 2, 2], [1, 21, 1, 2, 22, 2]])
    assert all("error" in row for row in rows)
    assert all("solid 2" in row["error"] for row in rows)


def test_thin_probe_is_an_error_row_and_other_boxes_still_measure():
    cube = _box()
    thin = [0, 0, 0, 1, 1, MIN_PROBE_EXTENT_MM / 10]
    solid = [0, 0, 0, 1, 1, 1]

    rows = material_volumes(cube, [thin, solid])

    assert "error" in rows[0]
    assert "minimum required by OCCT" in rows[0]["error"]
    assert rows[1]["volume_mm3"] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="Material probe failed for box"):
        check_probe(rows, kind="keepout", box_mm=thin, threshold_mm3=0)
    passed, _ = check_probe(rows, kind="material", box_mm=solid, threshold_mm3=0.5)
    assert passed is True


def test_positioned_cylinder_evidence_reports_the_declared_axis():
    pin = BRepPrimAPI_MakeCylinder(
        gp_Ax2(gp_Pnt(5, 7, 0), gp_Dir(0, 0, 1)), 1.5, 10
    ).Shape()

    _, observed = check_positioned_cylinders(
        extract_cylinders(pin),
        axis="z",
        diameter_mm=3.0,
        positions_mm=[[5, 7]],
        position_tolerance_mm=0.01,
    )

    assert observed["axis"] == "z"
