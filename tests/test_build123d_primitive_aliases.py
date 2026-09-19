"""Issues #192/#197: generated constructor and placement compatibility.

Cylinder(diameter=10, h=20), Box(l=10, w=20, h=5) and friends normalize to
the native keywords; radius/diameter conflicts fail clearly; placement
keywords (center=, at=, centered=, axis=) are rejected with a copyable
translate / align / rotation example; native forms are untouched.

Unambiguous align strings and builder plane strings are normalized, while
coordinate tuples and invalid axis-like align strings receive targeted,
copyable placement guidance.
"""

import json
import math

import pytest
from click.testing import CliRunner

import build123d
from build123d import Align, BuildPart, BuildSketch, Mode, Plane
from build123d.objects_part import Cylinder as NativeCylinder

from agentcad.cli import cli
from agentcad.metrics import compute_metrics
from agentcad.runners import build123d as b3d_runner
from agentcad.runners.build123d_compat import (
    PrimitiveArgumentError,
    install_compat_primitives,
)


@pytest.fixture(scope="module")
def prims():
    """The compat classes, installed exactly as the runner installs them."""
    return install_compat_primitives(build123d)


def _volume(shape) -> float:
    return compute_metrics(shape.wrapped)["volume"]


def _bbox(shape):
    bb = shape.bounding_box()
    return (tuple(bb.min), tuple(bb.max))


# ---------------------------------------------------------------------------
# Accepted aliases
# ---------------------------------------------------------------------------

CYL_VOLUME = math.pi * 5**2 * 20


@pytest.mark.parametrize("kwargs", [
    dict(radius=5, height=20),
    dict(diameter=10, height=20),
    dict(dia=10, height=20),
    dict(d=10, height=20),
    dict(r=5, height=20),
    dict(d=10, h=20),
    dict(radius=5, h=20),
    dict(diameter=10, length=20),
    dict(diameter=10.0, h=20.0),
])
def test_cylinder_aliases_match_native(prims, kwargs):
    assert _volume(prims["Cylinder"](**kwargs)) == pytest.approx(CYL_VOLUME)


def test_cylinder_positional_plus_alias(prims):
    # Positional radius, alias height — no conflict.
    assert _volume(prims["Cylinder"](5, h=20)) == pytest.approx(CYL_VOLUME)


@pytest.mark.parametrize("args,kwargs", [
    ((), dict(l=10, w=20, h=5)),
    ((), dict(length=10, w=20, h=5)),
    ((10, 20), dict(h=5)),
    ((10,), dict(w=20, height=5)),
])
def test_box_aliases_match_native(prims, args, kwargs):
    assert _volume(prims["Box"](*args, **kwargs)) == pytest.approx(1000)


@pytest.mark.parametrize("kwargs", [dict(d=6), dict(diameter=6), dict(r=3), dict(dia=6)])
def test_circle_aliases(prims, kwargs):
    assert prims["Circle"](**kwargs).area == pytest.approx(math.pi * 9)


@pytest.mark.parametrize("kwargs", [dict(d=8), dict(diameter=8), dict(r=4)])
def test_sphere_aliases(prims, kwargs):
    expected = 4 / 3 * math.pi * 4**3
    assert _volume(prims["Sphere"](**kwargs)) == pytest.approx(expected, rel=1e-3)


def test_cone_height_alias(prims):
    cone = prims["Cone"](bottom_radius=5, top_radius=0, h=10)
    assert _volume(cone) == pytest.approx(math.pi * 25 * 10 / 3, rel=1e-3)


# ---------------------------------------------------------------------------
# Native build123d forms remain compatible
# ---------------------------------------------------------------------------

def test_native_forms_unchanged(prims):
    Cylinder = prims["Cylinder"]
    assert _volume(Cylinder(5, 20)) == pytest.approx(CYL_VOLUME)
    assert _volume(Cylinder(5, 20, 180)) == pytest.approx(CYL_VOLUME / 2)
    rotated = Cylinder(radius=5, height=20, rotation=(0, 90, 0))
    lo, hi = _bbox(rotated)
    assert hi[0] - lo[0] == pytest.approx(20)
    corner = Cylinder(5, 20, align=(Align.MIN, Align.MIN, Align.MIN))
    assert _bbox(corner)[0] == pytest.approx((0, 0, 0))


def test_compat_class_is_a_native_subclass(prims):
    cyl = prims["Cylinder"](d=10, h=20)
    assert isinstance(cyl, NativeCylinder)
    assert type(cyl).__name__ == "Cylinder"
    # Installation is idempotent and patches the package namespace.
    assert build123d.Cylinder is prims["Cylinder"]
    assert install_compat_primitives(build123d)["Cylinder"] is prims["Cylinder"]


def test_builder_context_integration(prims):
    with BuildPart() as bp:
        prims["Box"](l=20, w=20, h=10)
        prims["Cylinder"](d=10, h=30, mode=Mode.SUBTRACT)
    assert _volume(bp.part) == pytest.approx(4000 - math.pi * 25 * 10)

    with BuildSketch(Plane.XY) as sk:
        prims["Circle"](d=6)
    assert sk.sketch.area == pytest.approx(math.pi * 9)


def test_unknown_keyword_still_raises_native_error(prims):
    with pytest.raises(TypeError, match="unexpected keyword argument 'bogus'"):
        prims["Cylinder"](radius=5, height=20, bogus=1)


# ---------------------------------------------------------------------------
# Conflicts and invalid values
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args,kwargs,fragment,example", [
    ((), dict(radius=5, diameter=10, height=20),
     "both 'diameter=' and 'radius='", "Cylinder(radius=5, height=20)"),
    ((), dict(d=10, diameter=12, h=20),
     "both set radius", "Cylinder(radius=6, height=20)"),
    ((5,), dict(diameter=10, height=20),
     "radius was already passed positionally", "Cylinder(5, height=20)"),
    ((5, 20), dict(h=20),
     "height was already passed positionally", "Cylinder(5, 20)"),
    ((), dict(radius=5, height=20, h=20),
     "both 'h=' and 'height='", "Cylinder(radius=5, height=20)"),
])
def test_cylinder_conflicts_fail_clearly(prims, args, kwargs, fragment, example):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](*args, **kwargs)
    assert fragment in str(exc.value)
    # The suggested fix is copyable: built from the values actually passed.
    assert example in str(exc.value)


def test_box_conflict(prims):
    with pytest.raises(PrimitiveArgumentError, match="both 'h=' and 'height='"):
        prims["Box"](10, 20, h=5, height=5)


@pytest.mark.parametrize("value", [0, -10, "10", True, None, float("nan"), float("inf")])
def test_invalid_alias_values(prims, value):
    with pytest.raises(PrimitiveArgumentError, match="must be a positive number"):
        prims["Cylinder"](diameter=value, height=20)


# ---------------------------------------------------------------------------
# Placement keywords: targeted, copyable guidance
# ---------------------------------------------------------------------------

def test_center_vector_guidance_uses_normalized_call(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](diameter=10, h=20, center=(1, 2, 3))
    msg = str(exc.value)
    assert "does not accept 'center='" in msg
    assert "Cylinder(radius=5, height=20).translate((1, 2, 3))" in msg
    assert "Pos(1, 2, 3) * Cylinder(radius=5, height=20)" in msg
    assert "with Locations((1, 2, 3))" in msg


def test_at_keyword_with_positional_dims(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Box"](10, 20, 5, at=(0, 0, 10))
    assert "Box(10, 20, 5).translate((0, 0, 10))" in str(exc.value)


def test_center_non_vector_falls_back_to_placeholder(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Box"](10, 20, 5, center="origin")
    assert ".translate((x, y, z))" in str(exc.value)


@pytest.mark.parametrize("keyword", ["centered", "center"])
def test_centered_false_points_to_align(prims, keyword):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Box"](10, 20, 5, **{keyword: False})
    assert "align=(Align.MIN, Align.MIN, Align.MIN)" in str(exc.value)


def test_centered_true_says_drop_it(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](radius=5, height=20, centered=True)
    assert "drop the keyword" in str(exc.value)


@pytest.mark.parametrize("keyword", ["axis", "direction"])
def test_axis_points_to_rotation(prims, keyword):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](d=10, h=20, **{keyword: "X"})
    assert "rotation=(0, 90, 0)" in str(exc.value)


def test_circle_center_guidance_is_2d(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Circle"](radius=3, center=(1, 2))
    msg = str(exc.value)
    assert "Pos(1, 2) * Circle(radius=3)" in msg
    assert "BuildSketch" in msg
    assert ".translate(" not in msg


# ---------------------------------------------------------------------------
# Constructor alignment and builder workplanes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("align", [
    "center", "CENTER", Align.CENTER,
])
def test_scalar_alignment_forms_are_centered(prims, align):
    lo, hi = _bbox(prims["Box"](10, 20, 30, align=align))
    assert lo == pytest.approx((-5, -10, -15))
    assert hi == pytest.approx((5, 10, 15))


def test_none_alignment_string_matches_native_enum(prims):
    string_bounds = _bbox(prims["Box"](10, 20, 30, align="none"))
    enum_bounds = _bbox(prims["Box"](10, 20, 30, align=Align.NONE))
    assert string_bounds[0] == pytest.approx(enum_bounds[0])
    assert string_bounds[1] == pytest.approx(enum_bounds[1])


@pytest.mark.parametrize("align", [
    ("min", "center", "MAX"),
    ["min", Align.CENTER, "max"],
    (Align.MIN, Align.CENTER, Align.MAX),
])
def test_per_axis_alignment_forms_are_normalized(prims, align):
    lo, hi = _bbox(prims["Box"](10, 20, 30, align=align))
    assert lo == pytest.approx((0, -10, -30))
    assert hi == pytest.approx((10, 10, 0))


def test_two_dimensional_alignment_is_normalized(prims):
    circle = prims["Circle"](3, align=("min", "max"))
    bb = circle.bounding_box()
    assert tuple(bb.min)[:2] == pytest.approx((0, -6))
    assert tuple(bb.max)[:2] == pytest.approx((6, 0))


def test_alignment_shortcuts_cover_other_native_primitives(prims):
    rectangle = prims["Rectangle"](10, 20, align=("min", "max"))
    rect_bounds = rectangle.bounding_box()
    assert tuple(rect_bounds.min)[:2] == pytest.approx((0, -20))
    assert tuple(rect_bounds.max)[:2] == pytest.approx((10, 0))

    torus = prims["Torus"](10, 2, align=("min", "center", "max"))
    torus_bounds = torus.bounding_box()
    assert tuple(torus_bounds.min) == pytest.approx((0, -12, -4))
    assert tuple(torus_bounds.max) == pytest.approx((24, 12, 0))


# ---------------------------------------------------------------------------
# Issue #218: sketch orientation guidance uses builder planes, not 3D
# rotation tuples. Every literal repair is executed and its resulting plane
# checked, not just the message text.
# ---------------------------------------------------------------------------

# axis -> (plane name, signed normal, global axis index of the normal,
#          global axis indices that receive the sketch's local (x, y)).
_SKETCH_AXES = {
    "X": ("Plane.YZ", "+X", 0, (1, 2)),
    "Y": ("Plane.XZ", "-Y", 1, (0, 2)),
    "Z": ("Plane.XY", "+Z", 2, (0, 1)),
}
_EXTRUDE = 3


def _native_footprint(class_name, args):
    """(x, y) extents of the primitive drawn on the default XY plane."""
    bb = getattr(build123d, class_name)(*args).bounding_box()
    return (bb.min.X, bb.max.X), (bb.min.Y, bb.max.Y)


def _assert_solid_on_plane(topo_shape, axis, class_name, args):
    """The extruded solid lies on the axis's plane and grows along its normal."""
    _, normal, normal_index, in_plane = _SKETCH_AXES[axis]
    bb = build123d.Compound(topo_shape).bounding_box()
    lo, hi = tuple(bb.min), tuple(bb.max)
    expected_normal = (
        (-_EXTRUDE, 0) if normal.startswith("-") else (0, _EXTRUDE)
    )
    assert (lo[normal_index], hi[normal_index]) == pytest.approx(expected_normal)
    (x_lo, x_hi), (y_lo, y_hi) = _native_footprint(class_name, args)
    assert (lo[in_plane[0]], hi[in_plane[0]]) == pytest.approx((x_lo, x_hi))
    assert (lo[in_plane[1]], hi[in_plane[1]]) == pytest.approx((y_lo, y_hi))


def _assert_face_on_plane(topo_shape, axis, class_name, args):
    """A standalone relocated face is flat along the axis's normal."""
    _, _, normal_index, in_plane = _SKETCH_AXES[axis]
    bb = build123d.Compound(topo_shape).bounding_box()
    lo, hi = tuple(bb.min), tuple(bb.max)
    assert (lo[normal_index], hi[normal_index]) == pytest.approx((0, 0))
    (x_lo, x_hi), (y_lo, y_hi) = _native_footprint(class_name, args)
    assert (lo[in_plane[0]], hi[in_plane[0]]) == pytest.approx((x_lo, x_hi))
    assert (lo[in_plane[1]], hi[in_plane[1]]) == pytest.approx((y_lo, y_hi))


def _run_builder_repair(builder_line: str):
    """Execute the literal ``with BuildSketch(...): ...`` repair and extrude it."""
    return b3d_runner.execute(
        "with BuildPart() as part:\n"
        f"    {builder_line}\n"
        f"    extrude(amount={_EXTRUDE})\n"
        "show_object(part.part)"
    )


@pytest.mark.parametrize("class_name,args,call", [
    ("Circle", (5,), "Circle(5)"),
    ("RegularPolygon", (5, 6), "RegularPolygon(5, 6)"),
    ("Rectangle", (10, 20), "Rectangle(10, 20)"),
])
@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
def test_sketch_align_axis_repairs_land_on_the_right_plane(
    prims, class_name, args, call, axis,
):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims[class_name](*args, align=axis)
    msg = str(exc.value)
    plane, normal, _, _ = _SKETCH_AXES[axis]
    assert "Invalid align=" in msg
    assert "rotation=(" not in msg

    assert "names an axis, not an alignment" in msg
    assert msg.index("BuildSketch") < msg.index("Align.MIN"), "fix before enum list"
    builder_line = f"with BuildSketch({plane}): {call}"
    assert builder_line in msg
    if axis == "Z":
        assert "already has its normal along +Z" in msg
        standalone = call
        assert f"drop align=: {builder_line}." in msg
    else:
        standalone = f"{plane} * {call}"
        assert f"normal points along {normal}" in msg
    assert f"Outside a builder: {standalone}." in msg

    result = _run_builder_repair(builder_line)
    assert result.success, result.exception
    _assert_solid_on_plane(result.topo_shape, axis, class_name, args)

    result = b3d_runner.execute(f"show_object({standalone})")
    assert result.success, result.exception
    _assert_face_on_plane(result.topo_shape, axis, class_name, args)


@pytest.mark.parametrize("keyword", ["axis", "direction", "dir", "normal"])
@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
def test_sketch_orientation_keywords_get_axis_specific_planes(
    prims, keyword, axis,
):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Rectangle"](10, 20, **{keyword: axis})
    msg = str(exc.value)
    plane, normal, _, _ = _SKETCH_AXES[axis]
    assert f"Rectangle() does not accept '{keyword}='." in msg
    assert "rotation=(" not in msg
    other_planes = {p for p, _, _, _ in _SKETCH_AXES.values()} - {plane}

    builder_line = f"with BuildSketch({plane}): Rectangle(10, 20)"
    assert builder_line in msg
    if axis == "Z":
        assert f"drop {keyword}=: {builder_line}." in msg
        assert "Outside a builder: Rectangle(10, 20)." in msg
    else:
        assert f"grows toward {normal}" in msg
    if axis == "Y":
        assert "Plane.ZX for a +Y normal" in msg
        other_planes.discard("Plane.ZX")
    assert not any(other in msg for other in other_planes)

    result = _run_builder_repair(builder_line)
    assert result.success, result.exception
    _assert_solid_on_plane(result.topo_shape, axis, "Rectangle", (10, 20))


def test_sketch_axis_lowercase_and_padded_values_are_recognised(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Circle"](5, axis=" x ")
    assert "with BuildSketch(Plane.YZ): Circle(5)" in str(exc.value)


def test_sketch_in_plane_rotation_is_preserved_not_conflicted(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Rectangle"](10, 20, align="X", rotation=30)
    msg = str(exc.value)
    assert "cannot both be preserved" not in msg
    standalone = "Plane.YZ * Rectangle(10, 20, rotation=30)"
    assert "with BuildSketch(Plane.YZ): Rectangle(10, 20, rotation=30)" in msg
    assert f"Outside a builder: {standalone}." in msg
    assert "in-plane angle" in msg

    result = b3d_runner.execute(f"show_object({standalone})")
    assert result.success, result.exception
    bounds = build123d.Compound(result.topo_shape).bounding_box()
    rotated = build123d.Rectangle(10, 20, rotation=30).bounding_box()
    assert tuple(bounds.size) == pytest.approx(
        (0, rotated.size.X, rotated.size.Y)
    )


def test_sketch_orientation_inside_builder_omits_standalone_form(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Circle"](5, direction="X", mode=Mode.SUBTRACT)
    msg = str(exc.value)
    assert "with BuildSketch(Plane.YZ): Circle(5, mode=Mode.SUBTRACT)" in msg
    assert "Outside a builder" not in msg
    assert "Plane.YZ * Circle" not in msg


@pytest.mark.parametrize("vector,axis", [
    ((1, 0, 0), "X"), ([0, 1, 0], "Y"), ((0, 0, 1), "Z"),
])
def test_unit_axis_vectors_are_read_as_axes(prims, vector, axis):
    plane = _SKETCH_AXES[axis][0]
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["RegularPolygon"](5, 6, axis=vector)
    assert f"with BuildSketch({plane}): RegularPolygon(5, 6)" in str(exc.value)

    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](5, 20, axis=vector)
    msg = str(exc.value)
    expected = {"X": "rotation=(0, 90, 0)", "Y": "rotation=(-90, 0, 0)"}
    if axis == "Z":
        assert "already points along +Z" in msg
    else:
        assert expected[axis] in msg


@pytest.mark.parametrize("value", [(1, 1, 0), "up", (0, -1, 0)])
def test_sketch_non_axis_orientation_lists_every_plane(prims, value):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Rectangle"](10, 20, axis=value)
    msg = str(exc.value)
    assert "Rectangle() does not accept 'axis='." in msg
    assert "must be" not in msg
    assert "Could not read an axis from axis=" in msg
    assert "with BuildSketch(Plane.YZ): Rectangle(10, 20) faces +X" in msg
    assert "Plane.XZ faces -Y" in msg
    assert "Plane.XY (the default) faces +Z" in msg
    assert "in-plane angle" in msg
    assert "rotation=(" not in msg


def test_sketch_non_axis_align_has_no_orientation_hint(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Circle"](5, align="middle")
    msg = str(exc.value)
    assert "Invalid align='middle'" in msg
    assert "BuildSketch" not in msg and "rotation" not in msg


@pytest.mark.parametrize("value", [(1, 2, 3), [1, 2, 3]])
def test_align_coordinates_point_to_translation(prims, value):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Box"](10, 20, 30, align=value)
    msg = str(exc.value)
    assert "align= controls which bounding-box side" in msg
    assert "Box(10, 20, 30).translate((1, 2, 3))" in msg
    assert "align=(Align.MIN, Align.CENTER, Align.MAX)" in msg


@pytest.mark.parametrize("value", ["X", "middle", ("min", "max")])
def test_invalid_align_has_supported_values(prims, value):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](5, 20, align=value)
    msg = str(exc.value)
    assert "Invalid align=" in msg
    assert "Align.MIN" in msg and "Align.CENTER" in msg and "Align.MAX" in msg
    if value == "X":
        assert "not an alignment value" in msg
        assert "rotation=(0, 90, 0)" in msg


@pytest.mark.parametrize("axis,correction,expected_size", [
    ("X", "Cylinder(5, 20, rotation=(0, 90, 0))", (20, 10, 10)),
    ("Y", "Cylinder(5, 20, rotation=(-90, 0, 0))", (10, 20, 10)),
    ("Z", "Cylinder(5, 20)", (10, 10, 20)),
])
def test_axis_like_align_has_literal_executable_correction(
    prims, axis, correction, expected_size,
):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](5, 20, align=axis)
    msg = str(exc.value)
    assert correction in msg
    if axis == "Z":
        assert "already points along +Z" in msg
    else:
        assert f"along +{axis}" in msg

    result = b3d_runner.execute(f"show_object({correction})")
    assert result.success, result.exception
    bounds = build123d.Compound(result.topo_shape).bounding_box()
    assert tuple(bounds.size) == pytest.approx(expected_size)


@pytest.mark.parametrize("keyword", ["align", "axis"])
def test_existing_rotation_and_axis_guess_report_executable_choices(prims, keyword):
    kwargs = {
        "rotation": (15, 0, 0),
        "mode": Mode.SUBTRACT,
        keyword: "X",
    }
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](2, 20, **kwargs)

    keep_rotation = (
        "Cylinder(2, 20, rotation=(15, 0, 0), mode=Mode.SUBTRACT)"
    )
    point_along_x = (
        "Cylinder(2, 20, rotation=(0, 90, 0), mode=Mode.SUBTRACT)"
    )
    msg = str(exc.value)
    assert "request two orientations" in msg
    assert "cannot both be preserved" in msg
    assert f"drop {keyword}=: {keep_rotation}" in msg
    assert f"point along +X instead, replace rotation=: {point_along_x}" in msg
    assert "rotate(" not in msg

    namespace = vars(build123d)
    with prims["BuildPart"]() as part:
        prims["Box"](60, 10, 10)
        tool = eval(point_along_x, namespace)
    assert tuple(tool.bounding_box().size) == pytest.approx((20, 4, 4))
    assert _volume(part.part) == pytest.approx(60 * 10 * 10 - math.pi * 2**2 * 20)


def test_numeric_align_repair_preserves_and_executes_native_arguments(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](
            radius=2,
            height=12,
            rotation=(0, 90, 0),
            mode=Mode.SUBTRACT,
            align=(20, 0, 0),
        )
    repair = (
        "with Locations((20, 0, 0)): "
        "Cylinder(radius=2, height=12, rotation=(0, 90, 0), "
        "mode=Mode.SUBTRACT)"
    )
    assert f"Inside a builder, use exactly: {repair}" in str(exc.value)

    result = b3d_runner.execute(
        "with BuildPart() as part:\n"
        "    Box(60, 20, 20)\n"
        f"    {repair}\n"
        "show_object(part.part)"
    )
    assert result.success, result.exception
    expected_volume = 60 * 20 * 20 - math.pi * 2**2 * 12
    assert compute_metrics(result.topo_shape)["volume"] == pytest.approx(
        expected_volume
    )


@pytest.mark.parametrize("builder_name", ["BuildPart", "BuildSketch", "BuildLine"])
def test_builder_plane_strings_are_normalized(prims, builder_name):
    builder = prims[builder_name]("xy")
    assert builder.workplanes[0] == Plane.XY


def test_wrapped_builders_still_nest(prims):
    """The wrapper must not break parent/child linking between builders.

    build123d links nested builders by comparing the Python frame that
    created each one; the wrapper's extra __init__ frame used to hide the
    parent, so BuildSketch-in-BuildPart never transferred its faces.
    """
    with prims["BuildPart"]() as part:
        with prims["BuildSketch"]("XZ"):
            prims["Rectangle"](10, 20)
        build123d.extrude(amount=3)
    bounds = part.part.bounding_box()
    assert tuple(bounds.min) == pytest.approx((-5, -3, -10))
    assert tuple(bounds.max) == pytest.approx((5, 0, 10))

    with prims["BuildSketch"]() as sketch:
        with prims["BuildLine"]():
            build123d.Polyline((0, 0), (10, 0), (10, 5), close=True)
        build123d.make_face()
    assert sketch.sketch.area == pytest.approx(25)


def test_runner_nested_builders_extrude():
    result = b3d_runner.execute(
        "with BuildPart() as part:\n"
        "    with BuildSketch(Plane.YZ):\n"
        "        Circle(5)\n"
        "    extrude(amount=3)\n"
        "show_object(part.part)"
    )
    assert result.success, result.exception
    bounds = build123d.Compound(result.topo_shape).bounding_box()
    assert tuple(bounds.min) == pytest.approx((0, -5, -5))
    assert tuple(bounds.max) == pytest.approx((3, 5, 5))


def test_invalid_builder_plane_string_has_copyable_guidance(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["BuildPart"]("horizontal")
    msg = str(exc.value)
    assert "WorkplaneList" not in msg
    assert "with BuildPart(Plane.XY): ..." in msg
    assert "Plane.XZ" in msg and "Plane.YZ" in msg


# ---------------------------------------------------------------------------
# End to end through the runner and the CLI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("script", [
    "show_object(Cylinder(d=10, h=20))",
    "show_object(Cylinder(diameter=10, height=20))",
    "from build123d import *\nshow_object(Cylinder(diameter=10, h=20))",
    "import build123d as bd\nshow_object(bd.Cylinder(diameter=10, h=20))",
    "from build123d import Cylinder\nshow_object(Cylinder(d=10, h=20))",
])
def test_runner_accepts_aliases(script):
    result = b3d_runner.execute(script)
    assert result.success, result.exception
    assert compute_metrics(result.topo_shape)["volume"] == pytest.approx(CYL_VOLUME)


def test_runner_surfaces_placement_guidance():
    result = b3d_runner.execute(
        "show_object(Box(10, 20, 5, center=(0, 0, 10)))"
    )
    assert not result.success
    assert "Box(10, 20, 5).translate((0, 0, 10))" in result.exception


def test_runner_normalizes_alignment_and_builder_plane_strings():
    result = b3d_runner.execute(
        "from build123d import *\n"
        "with BuildPart('XY') as part:\n"
        "    Box(10, 20, 5, align=('min', 'center', 'max'))\n"
        "show_object(part.part)"
    )
    assert result.success, result.exception
    bounds = build123d.Compound(result.topo_shape).bounding_box()
    assert tuple(bounds.min) == pytest.approx((0, -10, -5))
    assert tuple(bounds.max) == pytest.approx((10, 10, 0))


def test_runner_surfaces_sketch_plane_guidance():
    result = b3d_runner.execute('show_object(Circle(5, align="X"))')
    assert not result.success
    assert "with BuildSketch(Plane.YZ): Circle(5)" in result.exception
    assert "rotation=(" not in result.exception


def test_runner_surfaces_numeric_align_correction():
    result = b3d_runner.execute("show_object(Box(10, 20, 5, align=(1, 2, 3)))")
    assert not result.success
    assert "Box(10, 20, 5).translate((1, 2, 3))" in result.exception


def test_runner_surfaces_conflict():
    result = b3d_runner.execute("show_object(Cylinder(radius=5, d=10, height=20))")
    assert not result.success
    assert "both 'd=' and 'radius='" in result.exception


def test_preamble_docs_mention_aliases():
    result = CliRunner().invoke(cli, ["docs", "preamble", "--runtime", "build123d"])
    assert result.exit_code == 0, result.output
    assert "Cylinder(diameter=10, height=20)" in result.output


def test_preamble_docs_state_real_extrude_normals():
    """Plane.XZ faces -Y in build123d; the docs must not promise +Y."""
    result = CliRunner().invoke(cli, ["docs", "preamble", "--runtime", "build123d"])
    assert result.exit_code == 0, result.output
    content = json.loads(result.output)["content"]
    assert "Plane.XZ sketch → extrude towards -Y" in content
    assert "Plane.XZ sketch → extrude towards +Y" not in content
    assert ".translate((x, y, z))" in result.output
    assert "align=('min', 'center', 'max')" in result.output
    assert "align=(10, 0, 5) is" in result.output
    assert "with BuildPart('XY') as part:" in result.output
    assert "place_at(shape, from_pt=" in result.output
