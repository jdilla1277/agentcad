"""Issue #192: build123d primitives accept common generated constructor forms.

Cylinder(diameter=10, h=20), Box(l=10, w=20, h=5) and friends normalize to
the native keywords; radius/diameter conflicts fail clearly; placement
keywords (center=, at=, centered=, axis=) are rejected with a copyable
translate / align / rotation example; native forms are untouched.
"""

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


def test_runner_surfaces_conflict():
    result = b3d_runner.execute("show_object(Cylinder(radius=5, d=10, height=20))")
    assert not result.success
    assert "both 'd=' and 'radius='" in result.exception


def test_preamble_docs_mention_aliases():
    result = CliRunner().invoke(cli, ["docs", "preamble", "--runtime", "build123d"])
    assert result.exit_code == 0, result.output
    assert "Cylinder(diameter=10, height=20)" in result.output
    assert ".translate((x, y, z))" in result.output
