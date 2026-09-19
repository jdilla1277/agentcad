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
import re

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
# rotation tuples. Every literal repair is executed in the builder context
# that produced the error, and the resulting plane and signed extrusion
# direction are checked, not just the message text.
# ---------------------------------------------------------------------------

# signed axis -> named plane whose normal points that way
_SKETCH_PLANES = {
    "+X": "Plane.YZ", "-X": "Plane.ZY",
    "+Y": "Plane.ZX", "-Y": "Plane.XZ",
    "+Z": "Plane.XY", "-Z": "Plane.YX",
}
_AXIS_VALUES = [
    ("X", "+X"), ("-x", "-X"), ((0, 1, 0), "+Y"),
    ((0, -1, 0), "-Y"), ("Z", "+Z"), ([0, 0, -1], "-Z"),
]
_SKETCH_PRIMS = [
    ("Circle", (5,), "Circle(5)"),
    ("RegularPolygon", (5, 6), "RegularPolygon(5, 6)"),
    ("Rectangle", (10, 20), "Rectangle(10, 20)"),
]
_EXTRUDE = 3


def _axis_index(vector):
    components = [abs(vector.X), abs(vector.Y), abs(vector.Z)]
    index = components.index(max(components))
    sign = 1 if (vector.X, vector.Y, vector.Z)[index] > 0 else -1
    return index, sign


def _expected_extents(plane_name, class_name, args):
    """Global (lo, hi) per axis for the primitive drawn on ``plane_name``."""
    plane = getattr(Plane, plane_name.split(".")[1])
    local = getattr(build123d, class_name)(*args).bounding_box()
    expected = [None, None, None]
    for direction, lo, hi in (
        (plane.x_dir, local.min.X, local.max.X),
        (plane.y_dir, local.min.Y, local.max.Y),
    ):
        index, sign = _axis_index(direction)
        expected[index] = (lo, hi) if sign > 0 else (-hi, -lo)
    normal_index, _ = _axis_index(plane.z_dir)
    return expected, normal_index


def _assert_on_plane(topo_shape, axis, class_name, args, *, extruded):
    plane_name = _SKETCH_PLANES[axis]
    expected, normal_index = _expected_extents(plane_name, class_name, args)
    if extruded:
        expected[normal_index] = (
            (0, _EXTRUDE) if axis.startswith("+") else (-_EXTRUDE, 0)
        )
    else:
        expected[normal_index] = (0, 0)
    bb = build123d.Compound(topo_shape).bounding_box()
    lo, hi = tuple(bb.min), tuple(bb.max)
    for index in range(3):
        assert (lo[index], hi[index]) == pytest.approx(expected[index]), (
            f"axis {index} for {axis}: {(lo[index], hi[index])} != {expected[index]}"
        )


def _sketch_lines(msg):
    """The literal fragments an agent copies from an in-sketch repair."""
    header = re.search(r"`(with BuildSketch\(Plane\.\w+\):)`", msg)
    call = re.search(r"this call with ([A-Za-z]+\(.*?\))\. ", msg)
    assert header and call, msg
    return header.group(1), call.group(1)


def _run_in_sketch(header, *body):
    lines = "\n".join(f"        {line}" for line in body)
    return b3d_runner.execute(
        "with BuildPart() as part:\n"
        f"    {header}\n"
        f"{lines}\n"
        f"    extrude(amount={_EXTRUDE})\n"
        "show_object(part.part)"
    )


@pytest.mark.parametrize("class_name,args,call", _SKETCH_PRIMS)
@pytest.mark.parametrize("value,axis", _AXIS_VALUES)
def test_repair_inside_existing_sketch_changes_the_enclosing_plane(
    prims, class_name, args, call, value, axis,
):
    plane = _SKETCH_PLANES[axis]
    current = Plane.XZ if plane == "Plane.XY" else Plane.XY
    with pytest.raises(PrimitiveArgumentError) as exc:
        with prims["BuildPart"]():
            with prims["BuildSketch"](current):
                prims[class_name](*args, align=value)
    msg = str(exc.value)
    assert "names an axis, not an alignment" in msg
    assert msg.index("enclosing sketch") < msg.index("Align.MIN")
    assert "(currently Plane.XZ)" in msg if plane == "Plane.XY" else "(currently Plane.XY)" in msg
    assert "Do not nest" in msg
    assert f"with BuildSketch({plane}): {call}" not in msg, "must not nest"
    assert f"grows toward {axis}" in msg
    assert "rotation=(" not in msg

    header, literal = _sketch_lines(msg)
    assert header == f"with BuildSketch({plane}):"
    assert literal == call
    result = _run_in_sketch(header, literal)
    assert result.success, result.exception
    _assert_on_plane(result.topo_shape, axis, class_name, args, extruded=True)


@pytest.mark.parametrize("keyword", ["axis", "direction", "dir", "normal"])
@pytest.mark.parametrize("value,axis", [("Y", "+Y"), ((-1, 0, 0), "-X")])
def test_orientation_keywords_share_the_sketch_path(prims, keyword, value, axis):
    with pytest.raises(PrimitiveArgumentError) as exc:
        with prims["BuildPart"]():
            with prims["BuildSketch"]():
                prims["Rectangle"](10, 20, **{keyword: value})
    msg = str(exc.value)
    assert f"Rectangle() does not accept '{keyword}='." in msg
    header, literal = _sketch_lines(msg)
    assert header == f"with BuildSketch({_SKETCH_PLANES[axis]}):"
    assert literal == "Rectangle(10, 20)"
    result = _run_in_sketch(header, literal)
    assert result.success, result.exception
    _assert_on_plane(result.topo_shape, axis, "Rectangle", (10, 20), extruded=True)


def test_repair_when_sketch_is_already_on_the_requested_plane(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        with prims["BuildSketch"](Plane.YZ):
            prims["Circle"](5, axis="X")
    msg = str(exc.value)
    assert "already on Plane.YZ; drop axis= and keep: Circle(5)." in msg
    assert "BuildSketch(Plane" not in msg.split("keep:")[1]


@pytest.mark.parametrize("class_name,args,call", _SKETCH_PRIMS)
@pytest.mark.parametrize("value,axis", _AXIS_VALUES)
def test_repair_directly_inside_buildpart_adds_a_sketch(
    prims, class_name, args, call, value, axis,
):
    plane = _SKETCH_PLANES[axis]
    with pytest.raises(PrimitiveArgumentError) as exc:
        with prims["BuildPart"]():
            prims[class_name](*args, direction=value)
    msg = str(exc.value)
    literal = f"with BuildSketch({plane}): {call}"
    assert "need a BuildSketch inside the BuildPart" in msg
    assert f"drop direction= and use: {literal}." in msg
    result = b3d_runner.execute(
        "with BuildPart() as part:\n"
        f"    {literal}\n"
        f"    extrude(amount={_EXTRUDE})\n"
        "show_object(part.part)"
    )
    assert result.success, result.exception
    _assert_on_plane(result.topo_shape, axis, class_name, args, extruded=True)


@pytest.mark.parametrize("class_name,args,call", _SKETCH_PRIMS)
@pytest.mark.parametrize("value,axis", _AXIS_VALUES)
def test_repair_outside_any_builder_relocates_the_face(
    prims, class_name, args, call, value, axis,
):
    plane = _SKETCH_PLANES[axis]
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims[class_name](*args, align=value)
    msg = str(exc.value)
    literal = call if plane == "Plane.XY" else f"{plane} * {call}"
    assert f"drop align= and use: {literal}." in msg
    assert f"Inside a BuildPart, use with BuildSketch({plane}): {call}." in msg
    result = b3d_runner.execute(f"show_object({literal})")
    assert result.success, result.exception
    _assert_on_plane(result.topo_shape, axis, class_name, args, extruded=False)


def test_subtractive_repair_executes_beside_its_additive_geometry(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        with prims["BuildPart"]():
            with prims["BuildSketch"](Plane.XY):
                prims["Rectangle"](10, 10)
                prims["Circle"](2, direction=(1, 0, 0), mode=Mode.SUBTRACT)
    msg = str(exc.value)
    assert "Change the enclosing sketch's plane (currently Plane.XY)" in msg
    assert "same sketch as the additive geometry" in msg
    assert "Plane.YZ * Circle" not in msg
    header, literal = _sketch_lines(msg)
    assert header == "with BuildSketch(Plane.YZ):"
    assert literal == "Circle(2, mode=Mode.SUBTRACT)"

    result = _run_in_sketch(header, "Rectangle(10, 10)", literal)
    assert result.success, result.exception
    bounds = build123d.Compound(result.topo_shape).bounding_box()
    assert tuple(bounds.min) == pytest.approx((0, -5, -5))
    assert tuple(bounds.max) == pytest.approx((_EXTRUDE, 5, 5))
    assert _volume(build123d.Compound(result.topo_shape)) == pytest.approx(
        (100 - math.pi * 4) * _EXTRUDE
    )


def test_scalar_rotation_is_preserved_on_primitives_that_accept_it(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Rectangle"](10, 20, align="X", rotation=30)
    msg = str(exc.value)
    assert "cannot both be preserved" not in msg
    literal = "Plane.YZ * Rectangle(10, 20, rotation=30)"
    assert f"use: {literal}." in msg
    assert "stays an in-plane angle" in msg
    result = b3d_runner.execute(f"show_object({literal})")
    assert result.success, result.exception
    bounds = build123d.Compound(result.topo_shape).bounding_box()
    rotated = build123d.Rectangle(10, 20, rotation=30).bounding_box()
    assert tuple(bounds.size) == pytest.approx(
        (0, rotated.size.X, rotated.size.Y)
    )


@pytest.mark.parametrize("class_name,args,call,rotation,reason", [
    ("Circle", (5,), "Circle(5)", (0, 90, 0), "has no rotation= keyword"),
    ("Circle", (5,), "Circle(5)", 30, "has no rotation= keyword"),
    ("Rectangle", (10, 20), "Rectangle(10, 20)", (0, 90, 0),
     "single in-plane angle in degrees, not (0, 90, 0)"),
    ("RegularPolygon", (5, 6), "RegularPolygon(5, 6)", (0, 90, 0),
     "single in-plane angle in degrees, not (0, 90, 0)"),
])
def test_unusable_rotation_is_dropped_from_the_repair(
    prims, class_name, args, call, rotation, reason,
):
    with pytest.raises(PrimitiveArgumentError) as exc:
        with prims["BuildPart"]():
            prims[class_name](*args, axis="X", rotation=rotation)
    msg = str(exc.value)
    literal = f"with BuildSketch(Plane.YZ): {call}"
    assert f"use: {literal}." in msg
    assert reason in msg
    assert "rotation=" not in msg.split("use: ")[1].split(".")[0]
    result = b3d_runner.execute(
        "with BuildPart() as part:\n"
        f"    {literal}\n"
        f"    extrude(amount={_EXTRUDE})\n"
        "show_object(part.part)"
    )
    assert result.success, result.exception
    _assert_on_plane(result.topo_shape, "+X", class_name, args, extruded=True)


@pytest.mark.parametrize("value,axis,rotation", [
    ((0, -1, 0), "-Y", "rotation=(90, 0, 0)"),
    ((-1, 0, 0), "-X", "rotation=(0, -90, 0)"),
    ("-z", "-Z", "rotation=(180, 0, 0)"),
    ((0, 1, 0), "+Y", "rotation=(-90, 0, 0)"),
])
def test_signed_axes_orient_solids_too(prims, value, axis, rotation):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cone"](5, 0, 10, axis=value)
    msg = str(exc.value)
    literal = f"Cone(5, 0, 10, {rotation})"
    assert literal in msg
    assert f"points along {axis}" in msg
    result = b3d_runner.execute(f"show_object({literal})")
    assert result.success, result.exception
    com = build123d.Compound(result.topo_shape).center(build123d.CenterOf.MASS)
    index = "XYZ".index(axis[1])
    # A cone's mass centre sits a quarter height from the base, i.e. on the
    # opposite side of the origin from the apex.
    expected = [0.0, 0.0, 0.0]
    expected[index] = 2.5 if axis.startswith("+") else -2.5
    assert (com.X, com.Y, com.Z) == pytest.approx(
        [-v for v in expected], abs=1e-6
    )


def test_positive_z_vector_means_no_rotation(prims):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Cylinder"](5, 20, axis=(0, 0, 1))
    assert "already points along +Z" in str(exc.value)


@pytest.mark.parametrize("value", [(1, 1, 0), "up", (0, 2, 0), (0, 0, 0)])
def test_unreadable_orientation_lists_every_plane(prims, value):
    with pytest.raises(PrimitiveArgumentError) as exc:
        prims["Rectangle"](10, 20, axis=value)
    msg = str(exc.value)
    assert "Rectangle() does not accept 'axis='." in msg
    assert "must be" not in msg
    assert "Could not tell which axis axis= means" in msg
    assert "Plane.YZ faces +X (Plane.ZY -X)" in msg
    assert "Plane.ZX faces +Y (Plane.XZ -Y)" in msg
    assert "Plane.XY faces +Z (Plane.YX -Z)" in msg
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
