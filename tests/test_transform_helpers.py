"""Transform calling conventions and independent geometry on the default runtime."""

from pathlib import Path

import pytest
from build123d import Box, Compound, Vector
from build123d.topology import Shape as B3dShape
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape

from agentcad.helpers import bbox_point, place_at, rotate, translate
from agentcad.metrics import compute_metrics
from agentcad.runners import build123d as b3d_runner
from agentcad.step_io import load_cad_shape


@pytest.fixture(params=["wrapped", "raw"])
def shape(request):
    box = Box(10, 20, 30)
    return box if request.param == "wrapped" else box.wrapped


def _topo(shape):
    return getattr(shape, "wrapped", shape)


def _assert_independent(source, result):
    raw = _topo(source)
    # Issue #194: the result stays at the caller's abstraction level.
    if isinstance(source, TopoDS_Shape):
        assert isinstance(result, TopoDS_Shape)
    else:
        assert isinstance(result, B3dShape)
        assert isinstance(result.wrapped, TopoDS_Shape)
    result = _topo(result)
    assert not raw.IsPartner(result)
    original_face = TopExp_Explorer(raw, TopAbs_FACE).Current()
    result_face = TopExp_Explorer(result, TopAbs_FACE).Current()
    assert not original_face.IsPartner(result_face)


@pytest.mark.parametrize("offset", [
    (50, -40, 30), ((50, -40, 30),), ([50, -40, 30],),
    (Vector(50, -40, 30),),
])
def test_translation_forms_move_without_mutating_source(shape, offset):
    result = translate(shape, *offset)
    assert bbox_point(result) == pytest.approx((50, -40, 30))
    assert bbox_point(shape) == pytest.approx((0, 0, 0))
    assert compute_metrics(_topo(result))["volume"] == pytest.approx(6000)
    _assert_independent(shape, result)


def test_existing_keyword_translation(shape):
    assert bbox_point(translate(shape=shape, x=10, y=20, z=30)) == pytest.approx((10, 20, 30))
    assert bbox_point(translate(shape, 10, y=20, z=30)) == pytest.approx((10, 20, 30))


@pytest.mark.parametrize("offset", [(0, 0, 0), ((0, 0, 0),), (Vector(0, 0, 0),)])
def test_zero_translation_copies_topology(shape, offset):
    result = translate(shape, *offset)
    assert bbox_point(result) == pytest.approx((0, 0, 0))
    _assert_independent(shape, result)


@pytest.mark.parametrize("axis,expected_size", [
    ("X", (10, 30, 20)), ("Y", (30, 20, 10)), ("Z", (20, 10, 30)),
])
def test_rotation_preserves_source_and_volume(shape, axis, expected_size):
    result = rotate(shape=shape, axis=axis, angle_deg=90)
    assert tuple(Compound(_topo(result)).bounding_box().size) == pytest.approx(expected_size)
    assert tuple(Compound(_topo(shape)).bounding_box().size) == pytest.approx((10, 20, 30))
    assert compute_metrics(_topo(result))["volume"] == pytest.approx(6000)
    _assert_independent(shape, result)


def test_zero_rotation_copies_topology(shape):
    _assert_independent(shape, rotate(shape, "Z", 0))


def test_bbox_point_and_place_at_accept_raw_or_wrapped_shapes(shape):
    source_min = bbox_point(shape, "min", "min", "min")
    result = place_at(shape, from_pt=source_min, to_pt=(0, 0, 0))
    assert bbox_point(result, "min", "min", "min") == pytest.approx((0, 0, 0))
    assert bbox_point(shape) == pytest.approx((0, 0, 0))
    assert compute_metrics(_topo(result))["volume"] == pytest.approx(6000)
    _assert_independent(shape, result)


def test_place_at_accepts_vector_points(shape):
    result = place_at(shape, Vector(0, 0, 0), Vector(10, 20, 30))
    assert bbox_point(result) == pytest.approx((10, 20, 30))


@pytest.mark.parametrize("from_pt,to_pt", [
    ((0, 0), (1, 2, 3)),
    ((0, 0, 0), "origin"),
    ((0, 0, 0), (1, 2, float("inf"))),
])
def test_invalid_place_at_points_have_exact_signature(shape, from_pt, to_pt):
    with pytest.raises((TypeError, ValueError), match=r"Use place_at\(shape, from_pt="):
        place_at(shape, from_pt=from_pt, to_pt=to_pt)


@pytest.mark.parametrize("args", [
    (), (1,), (1, 2), ((1, 2),), ((1, 2, 3, 4),),
    ((1, 2, 3), 4, 5), (Vector(1, 2, 3), 4), ((1, 2, 3), None, None),
    ("123",), ({1, 2, 3},), ({"x": 1, "y": 2, "z": 3},),
    (1, 2, "three"), (True, 2, 3), (float("nan"), 2, 3),
    ((1, float("inf"), 3),), (1, 2, 3, 4),
])
def test_invalid_translation_has_correction(shape, args):
    with pytest.raises((TypeError, ValueError), match=r"Use translate\(shape, x, y, z\)"):
        translate(shape, *args)


@pytest.mark.parametrize("args", [
    (), ("Z",), ("W", 90), ([0, 0, 1], 90), (None, 90),
    ("Z", "90"), ("Z", float("inf")), ("Z", True), ("Z", 90, 1),
])
def test_invalid_rotation_identifies_all_required_arguments(shape, args):
    with pytest.raises((TypeError, ValueError), match=r"Use rotate\(shape, axis, angle_deg\)"):
        rotate(shape, *args)


@pytest.mark.parametrize("function,args", [
    (translate, ()), (translate, (1, 2, 3)), (translate, ((1, 2, 3),)),
    (translate, (TopoDS_Shape(), 1, 2, 3)),
    (rotate, ()), (rotate, ("Z", 90)), (rotate, (None, "Z", 90)),
])
def test_missing_or_invalid_shape_has_correction(function, args):
    with pytest.raises((TypeError, ValueError), match=f"Use {function.__name__}"):
        function(*args)


@pytest.mark.parametrize("expression", [
    "translate(box, 10, 20, 30)", "translate(box, (10, 20, 30))",
    "translate(box, Vector(10, 20, 30))", "rotate(box, 'Z', 90)",
])
def test_injected_helpers_execute_end_to_end(expression):
    result = b3d_runner.execute(f"box = Box(10, 20, 30)\nshow_object(Compound({expression}))\n")
    assert result.success, result.exception
    assert compute_metrics(result.topo_shape)["volume"] == pytest.approx(6000)


@pytest.mark.parametrize("expression,correction", [
    ("translate(1, 2, 3)(Box(10, 20, 30))", "Use translate(shape, x, y, z)"),
    ("rotate(Box(10, 20, 30), 'Z')", "shape, axis, and angle_deg are required"),
    ("rotate(Box(10, 20, 30), axis='Z', angle=90)", "Use rotate(shape, axis, angle_deg)"),
])
def test_runner_surfaces_transform_corrections(expression, correction):
    result = b3d_runner.execute(expression)
    assert not result.success
    assert correction in result.exception


def test_imported_compound_repeated_transforms_are_independent():
    path = Path(__file__).parent / "fixtures" / "comparison" / "overlapping_compound.step"
    source = load_cad_shape(path)
    original_center = bbox_point(source)
    original_metrics = compute_metrics(source)
    first = translate(source, (100, 0, 0))
    second = translate(source, Vector(100, 0, 0))
    rotated = rotate(first, "Z", 90)
    placed = place_at(source, from_pt=original_center, to_pt=(0, 0, 0))

    for result in (first, second, rotated, placed):
        _assert_independent(source, result)
        metrics = compute_metrics(result)
        assert metrics["volume"] == pytest.approx(original_metrics["volume"])
        assert len(Compound(result).solids()) == len(Compound(source).solids())
        assert metrics["is_valid"]
    _assert_independent(first, second)
    _assert_independent(first, rotated)
    assert bbox_point(source) == pytest.approx(original_center)
    assert bbox_point(first) == pytest.approx((original_center[0] + 100, *original_center[1:]))
    assert bbox_point(second) == pytest.approx(bbox_point(first))
    assert bbox_point(placed) == pytest.approx((0, 0, 0))
