"""Exact nearest-part results with less unnecessary kernel work."""

import copy

import pytest
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Pnt

from agentcad.validation_guidance import part_connections


def box(x, y=0, z=0, size=2):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), size, size, size).Shape()


def diagnose(shapes, *, floating=()):
    raw = [{"topo_shape": s, "explicit_id": f"p{i}",
            "validation_options": {"floating": i in floating}}
           for i, s in enumerate(shapes)]
    public = [{"id": f"p{i}"} for i in range(len(shapes))]
    warnings = part_connections(raw, public)
    return raw, public, warnings


def exhaustive(shapes):
    """Reference the original all-pairs algorithm, including input-order ties."""
    distances = {}
    result = []
    for i, shape in enumerate(shapes):
        nearest = None
        for j, other in enumerate(shapes):
            if i == j:
                continue
            key = tuple(sorted((i, j)))
            if key not in distances:
                tool = BRepExtrema_DistShapeShape(shape, other)
                assert tool.IsDone()
                distances[key] = tool.Value()
            distance = distances[key]
            if nearest is None or distance < nearest[0]:
                nearest = distance, f"p{j}"
        result.append(nearest)
    return result


@pytest.mark.parametrize("shapes", [
    [box(0), box(100), box(2), box(102), box(300)],
    [box(0), box(1), box(2), box(2.05), box(200)],
    [box(-4), box(0), box(4), box(0, 4), box(0, -4)],
    [box(0), box(2.0099), box(4.02), box(6.0201)],
    [box(0), box(0), box(0), box(50)],
])
def test_matches_exhaustive_nearest_results(shapes):
    _, public, _ = diagnose(shapes, floating=(len(shapes) - 1,))
    for i, (part, expected) in enumerate(zip(public, exhaustive(shapes))):
        connection = part["connection"]
        distance, neighbor = expected
        assert connection["nearest_part_id"] == neighbor
        assert connection["distance_mm"] == round(distance, 6)
        assert connection["status"] == (
            "floating" if i == len(shapes) - 1 else
            "disconnected" if distance > 0.01 else "touching"
        )


def test_overlapping_bounds_do_not_establish_contact():
    tube = BRepAlgoAPI_Cut(BRepPrimAPI_MakeCylinder(10, 5).Shape(),
                          BRepPrimAPI_MakeCylinder(8, 5).Shape()).Shape()
    inner = BRepPrimAPI_MakeCylinder(3, 5).Shape()
    _, parts, warnings = diagnose([tube, inner])
    assert all(p["connection"]["status"] == "disconnected" for p in parts)
    assert all(p["connection"]["distance_mm"] == 5 for p in parts)
    assert len(warnings) == 2


def test_separated_repeated_parts_prune_exact_distance_calls(monkeypatch):
    import OCP.BRepExtrema

    shapes = [box(x) for x in range(0, 300, 10)]
    expected = exhaustive(shapes)
    calls = []

    def counted(a, b):
        calls.append((a, b))
        return BRepExtrema_DistShapeShape(a, b)

    monkeypatch.setattr(OCP.BRepExtrema, "BRepExtrema_DistShapeShape", counted)
    _, public, _ = diagnose(shapes)
    for part, (distance, neighbor) in zip(public, expected):
        assert part["connection"]["nearest_part_id"] == neighbor
        assert part["connection"]["distance_mm"] == round(distance, 6)
    # The old implementation does 435 exact comparisons for these 30 parts.
    assert len(calls) <= 2 * len(shapes)


def test_touching_ties_keep_first_neighbor_in_input_order(monkeypatch):
    import OCP.BRepExtrema

    calls = []

    def counted(a, b):
        calls.append((a, b))
        return BRepExtrema_DistShapeShape(a, b)

    monkeypatch.setattr(OCP.BRepExtrema, "BRepExtrema_DistShapeShape", counted)
    _, public, _ = diagnose([box(0)] * 12)
    assert [p["connection"]["nearest_part_id"] for p in public] == ["p1"] + ["p0"] * 11
    assert len(calls) < 20


def test_required_distance_failure_remains_unavailable(monkeypatch):
    import OCP.BRepExtrema

    class FailedDistance:
        def __init__(self, *args):
            pass

        def IsDone(self):
            return False

    monkeypatch.setattr(OCP.BRepExtrema, "BRepExtrema_DistShapeShape", FailedDistance)
    _, public, _ = diagnose([box(0), box(4)])
    assert all(p["connection"]["status"] == "unavailable" for p in public)


def test_missing_bounds_fall_back_to_exact_checks(monkeypatch):
    import agentcad.validation_guidance as guidance

    monkeypatch.setattr(guidance, "_connection_bounds", lambda shape: None)
    shapes = [box(0), box(4), box(2.05), box(100)]
    _, public, _ = diagnose(shapes)
    for part, (distance, neighbor) in zip(public, exhaustive(shapes)):
        assert part["connection"]["nearest_part_id"] == neighbor
        assert part["connection"]["distance_mm"] == round(distance, 6)


def test_rotated_parts_keep_exact_nearest_results():
    import math
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Ax1, gp_Dir, gp_Trsf

    rotation = gp_Trsf()
    rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), math.pi / 4)
    shapes = [BRepBuilderAPI_Transform(box(x), rotation, True).Shape() for x in (0, 4, 6, 30)]
    _, public, _ = diagnose(shapes)
    for part, (distance, neighbor) in zip(public, exhaustive(shapes)):
        assert part["connection"]["nearest_part_id"] == neighbor
        assert part["connection"]["distance_mm"] == round(distance, 6)


def test_unnamed_parts_are_neighbors_but_not_diagnosed():
    raw = [{"topo_shape": box(0), "explicit_id": "named"}, {"topo_shape": box(2)}]
    parts = [{"id": "named"}, {"id": "unnamed"}]
    part_connections(raw, parts)
    assert parts[0]["connection"]["nearest_part_id"] == "unnamed"
    assert "connection" not in parts[1]
    single = copy.deepcopy(parts[:1])
    part_connections(raw[:1], single)
    assert single[0]["connection"]["status"] == "not_applicable"
