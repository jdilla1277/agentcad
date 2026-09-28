"""Transform calling conventions and independent geometry on the default runtime."""

from pathlib import Path

import pytest
from build123d import Box, Compound, Vector
from build123d.topology import Shape as B3dShape
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape

from agentcad.commands.run import _execution_error_guidance
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


def test_delta_keyword_translation(shape):
    result = translate(shape, dx=50, dy=-40, dz=30)
    assert bbox_point(result) == pytest.approx((50, -40, 30))
    assert bbox_point(shape) == pytest.approx((0, 0, 0))
    _assert_independent(shape, result)
    assert bbox_point(translate(shape=shape, dz=3, dx=1, dy=2)) == pytest.approx((1, 2, 3))


@pytest.mark.parametrize("args,kwargs", [
    ((), {"x": 1, "dy": 2, "dz": 3}),
    ((1, 2, 3), {"dx": 1}),
    (((1, 2, 3),), {"dx": 1, "dy": 2, "dz": 3}),
    ((), {"x": 1, "y": 2, "z": 3, "dz": 3}),
])
def test_mixing_coordinate_and_delta_keywords_is_ambiguous(shape, args, kwargs):
    with pytest.raises(TypeError, match=r"Ambiguous translation: got both x/y/z .* pass one set"):
        translate(shape, *args, **kwargs)


@pytest.mark.parametrize("args,kwargs,missing,required", [
    ((), {"dx": 1}, "dy, dz", "dx, dy, and dz"),
    ((), {"dx": 1, "dz": 3}, "dy", "dx, dy, and dz"),
    ((), {"dz": 3}, "dx, dy", "dx, dy, and dz"),
    ((), {"x": 1}, "y, z", "x, y, and z"),
    ((1, 2), {}, "z", "x, y, and z"),
    ((), {"x": 1, "z": 3}, "y", "x, y, and z"),
    ((), {}, "x, y, z", "x, y, and z"),
])
def test_partial_coordinates_name_every_required_coordinate(
    shape, args, kwargs, missing, required,
):
    with pytest.raises(TypeError) as excinfo:
        translate(shape, *args, **kwargs)
    message = str(excinfo.value)
    assert f"Missing translation coordinate(s) {missing};" in message
    assert f"{required} are all required" in message
    assert "Use translate(shape, (x, y, z))" in message


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
    with pytest.raises((TypeError, ValueError), match=r"Use translate\(shape, \(x, y, z\)\)"):
        translate(shape, *args)


@pytest.mark.parametrize("args", [
    (), ("Z",), ("W", 90), ([0, 0, 1], 90), (None, 90),
    ("Z", "90"), ("Z", float("inf")), ("Z", True), ("Z", 90, 1),
])
def test_invalid_rotation_identifies_all_required_arguments(shape, args):
    with pytest.raises((TypeError, ValueError), match=r"Use rotate\(shape, axis, angle_deg\)"):
        rotate(shape, *args)


@pytest.mark.parametrize("args", [
    (1, 2, 3), (1.5,), ((1, 2, 3),), ([1, 2, 3],), (Vector(1, 2, 3),),
])
def test_offset_in_shape_position_gets_canonical_repair(args):
    with pytest.raises(TypeError) as excinfo:
        translate(*args)
    message = str(excinfo.value)
    assert "takes the shape first" in message
    assert message.endswith("Use translate(shape, (x, y, z)).")


def test_list_of_shapes_is_not_mistaken_for_an_offset():
    with pytest.raises(TypeError, match="The first argument must be a TopoDS_Shape"):
        translate([Box(1, 1, 1)], 1, 2, 3)


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
    "translate(box, Vector(10, 20, 30))", "translate(box, dx=10, dy=20, dz=30)",
    "rotate(box, 'Z', 90)",
])
def test_injected_helpers_execute_end_to_end(expression):
    result = b3d_runner.execute(f"box = Box(10, 20, 30)\nshow_object(Compound({expression}))\n")
    assert result.success, result.exception
    assert compute_metrics(result.topo_shape)["volume"] == pytest.approx(6000)


@pytest.mark.parametrize("expression,correction", [
    ("translate(1, 2, 3)(Box(10, 20, 30))", "Use translate(shape, (x, y, z))"),
    ("translate(Box(10, 20, 30), dx=1, dy=2)", "dx, dy, and dz are all required"),
    ("translate(Box(10, 20, 30), x=1, dy=2, dz=3)", "Ambiguous translation"),
    ("rotate(Box(10, 20, 30), 'Z')", "shape, axis, and angle_deg are required"),
    ("rotate(Box(10, 20, 30), axis='Z', angle=90)", "Use rotate(shape, axis, angle_deg)"),
])
def test_runner_surfaces_transform_corrections(expression, correction):
    result = b3d_runner.execute(expression)
    assert not result.success
    assert correction in result.exception


# Transform forms generated in the September 12 CADGenBench run (#189, #203).
# Supported forms must move the part; foreign forms must fail with a
# correction that names a working call. Nothing may silently succeed with
# the wrong geometry.
_BENCHMARK_SUPPORTED = [
    "translate(part, (x, y, z))",
    "translate(part, x, y, z)",
    "translate(part, Vector(x, y, z))",
    "translate(part, dx=x, dy=y, dz=z)",
]
_BENCHMARK_FOREIGN = [
    ("translate(x, y, z)(part)", "Use translate(shape, (x, y, z))"),
    ("translate((x, y, z))", "Use translate(shape, (x, y, z))"),
    ("translate(part, dx=x, dy=y)", "Use translate(shape, (x, y, z))"),
    ("translate(part, x=x, dy=y, dz=z)", "Use translate(shape, (x, y, z))"),
    ("rotate(part, 'Z')", "Use rotate(shape, axis, angle_deg)"),
    ("Translate((x, y, z))", "moved = translate(shape, (x, y, z))"),
    ("part.translated((x, y, z))", "moved = translate(shape, (x, y, z))"),
    ("part.translate(x, y, z)", "moved = shape.translate((x, y, z))"),
]
_BENCHMARK_PREFIX = "x, y, z = 10, 20, 30\npart = Box(10, 20, 30)\n"


@pytest.mark.parametrize("expression", _BENCHMARK_SUPPORTED)
def test_benchmark_supported_translate_forms_replay(expression):
    result = b3d_runner.execute(
        f"{_BENCHMARK_PREFIX}show_object(Compound({expression}))\n"
    )
    assert result.success, result.exception
    assert bbox_point(result.topo_shape) == pytest.approx((10, 20, 30))


@pytest.mark.parametrize("expression,correction", _BENCHMARK_FOREIGN)
def test_benchmark_foreign_translate_forms_replay(expression, correction):
    # Agents may read only the suggestion field, so every correction must
    # appear there, not just inside the error text.
    source = f"{_BENCHMARK_PREFIX}show_object(Compound({expression}))\n"
    result = b3d_runner.execute(source)
    assert not result.success
    guidance = _execution_error_guidance(result.exception, "build123d", source)
    assert correction in guidance["suggestion"]
    assert guidance["more_at"] == "agentcad docs helpers"


@pytest.mark.parametrize("source", [
    # User helper with its own translate(vector) method (PR #223 review).
    "class Helper:\n"
    "    def translate(self, vector):\n"
    "        return vector\n"
    "Helper().translate(1, 2, 3)\n"
    "show_object(Box(1, 1, 1))\n",
    # Plain value, not a CAD shape.
    "value = 'text'\n"
    "value.translated((1, 2, 3))\n"
    "show_object(Box(1, 1, 1))\n",
    # User class that shadows a build123d name.
    "class Box:\n"
    "    pass\n"
    "Box().translated((1, 2, 3))\n"
    "show_object(Sphere(1))\n",
])
def test_translate_method_guidance_requires_a_cad_receiver(source):
    result = b3d_runner.execute(source)
    assert not result.success
    assert _execution_error_guidance(result.exception, "build123d", source) == {}


def test_translate_method_guidance_matches_build123d_subclasses():
    for name in ("Box", "Part", "Solid", "Compound", "Shape", "Cylinder"):
        message = f"AttributeError: '{name}' object has no attribute 'translated'"
        guidance = _execution_error_guidance(message, "build123d", "")
        assert "moved = translate(shape, (x, y, z))" in guidance["suggestion"], name
    for name in ("Vector", "Location", "Helper", "str"):
        message = f"AttributeError: '{name}' object has no attribute 'translated'"
        assert _execution_error_guidance(message, "build123d", "") == {}, name


def test_helper_suggestion_requires_agentcad_correction_text():
    for message in (
        "ValueError: Use translate(part) before exporting",
        "ValueError: Use rotate(shape, 'Z') here",
    ):
        assert _execution_error_guidance(message, "build123d", "") == {}


def test_translate_method_guidance_ignores_unrelated_errors():
    for message in (
        "TypeError: Shape.rotate() takes 3 positional arguments but 4 were given",
        "AttributeError: 'Box' object has no attribute 'translate_by'",
        "NameError: name 'Translation' is not defined",
        "ValueError: Use the force",
    ):
        assert _execution_error_guidance(message, "build123d", "") == {}


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
