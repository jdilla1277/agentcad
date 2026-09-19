"""Issue #194: edit helpers preserve the caller's abstraction level.

A ``load_step() -> translate -> safe_cut -> show_object`` pipeline must stay
a build123d object pipeline; raw ``load_step_shape()`` workflows must stay
raw. Synthetic primitives cover the type matrix; the comparison fixtures
(single solid and an overlapping multi-solid compound, exported from real
runs) cover the topology and container shapes agents actually import.
"""

from pathlib import Path

import pytest
from build123d import Box, Compound, Cylinder, Part, Solid, Wire
from build123d.topology import Shape as B3dShape
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shape, TopoDS_Solid

from agentcad.helpers import (
    bbox_point,
    copy_shape,
    mirror_fuse,
    place_at,
    raise_annulus,
    rotate,
    safe_cut,
    safe_fuse,
    safe_intersection,
    translate,
)
from agentcad.metrics import compute_metrics
from agentcad.runners import build123d as b3d_runner
from agentcad.step_io import load_cad_shape

FIXTURES = Path(__file__).parent / "fixtures" / "comparison"


def _topo(shape):
    return getattr(shape, "wrapped", shape)


def _tool():
    return Cylinder(radius=3, height=60)


_SHAPE_EDITS = [
    pytest.param(lambda s: translate(s, 5, 0, 0), id="translate"),
    pytest.param(lambda s: rotate(s, "Z", 45), id="rotate"),
    pytest.param(lambda s: place_at(s, (0, 0, 0), (5, 5, 5)), id="place_at"),
    pytest.param(lambda s: copy_shape(s), id="copy_shape"),
    pytest.param(lambda s: mirror_fuse(translate(s, 20, 0, 0), "YZ"), id="mirror_fuse"),
    pytest.param(lambda s: safe_cut(s, _tool()), id="safe_cut"),
    pytest.param(lambda s: safe_fuse(s, translate(_tool(), 0, 0, 20)), id="safe_fuse"),
    pytest.param(lambda s: safe_intersection(s, _tool()), id="safe_intersection"),
    pytest.param(
        lambda s: raise_annulus(
            s, center=(0, 0), inner_diameter=8, outer_diameter=12, height=2, z=15
        ),
        id="raise_annulus",
    ),
]


@pytest.mark.parametrize("edit", _SHAPE_EDITS)
def test_build123d_part_stays_build123d_part(edit):
    result = edit(Box(20, 20, 30))
    assert isinstance(result, Part), type(result)
    assert isinstance(result.wrapped, TopoDS_Compound)
    assert result.volume > 0
    assert result.volume == pytest.approx(compute_metrics(result.wrapped)["volume"])
    assert len(result.solids()) >= 1
    # The build123d algebra keeps working on the result.
    assert (result - Box(1, 1, 1)).volume > 0


@pytest.mark.parametrize("edit", _SHAPE_EDITS)
def test_build123d_solid_stays_build123d(edit):
    result = edit(Solid.make_box(20, 20, 30))
    assert isinstance(result, B3dShape), type(result)
    assert isinstance(result.wrapped, TopoDS_Shape)
    assert result.volume > 0


@pytest.mark.parametrize("edit", _SHAPE_EDITS)
def test_raw_topods_stays_raw(edit):
    result = edit(Solid.make_box(20, 20, 30).wrapped)
    assert isinstance(result, TopoDS_Shape)
    assert not isinstance(result, B3dShape)
    assert compute_metrics(result)["volume"] > 0


def test_single_solid_edits_unwrap_to_solid():
    """A bare Solid in gives a Solid back when the kernel wraps a single
    solid in a compound, so ``.fillet`` and friends still apply."""
    solid = Solid.make_box(20, 20, 30)
    cut = safe_cut(solid, _tool())
    assert isinstance(cut, Solid)
    assert isinstance(translate(solid, 1, 1, 1), Solid)


def test_wire_helpers_round_trip_wires():
    wire = Wire.make_circle(5)
    moved = translate(wire, 10, 0, 0)
    assert isinstance(moved, Wire)
    assert bbox_point(moved) == pytest.approx((10, 0, 0))


def test_bbox_point_accepts_wrapped_shapes():
    box = Box(10, 20, 30)
    assert bbox_point(box, "max", "max", "max") == pytest.approx((5, 10, 15))
    assert bbox_point(box.wrapped, "min", "min", "min") == pytest.approx((-5, -10, -15))
    with pytest.raises(TypeError, match="bbox_point"):
        bbox_point("not a shape")


def test_compound_of_raw_solid_is_the_documented_footgun():
    """Regression guard for the guidance change: wrapping a raw solid as
    ``Compound(raw)`` reports zero volume, which is why the helpers now hand
    back wrapped results instead of asking scripts to wrap them."""
    raw = translate(Solid.make_box(1, 2, 3).wrapped, 0, 0, 0)
    assert Compound(raw).volume == 0
    assert translate(Solid.make_box(1, 2, 3), 0, 0, 0).volume == pytest.approx(6)


# ---------------------------------------------------------------------------
# Imported STEP pipelines through the real runner
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", ["single_solid.step", "overlapping_compound.step"])
def test_load_step_edit_pipeline_stays_build123d(fixture):
    path = FIXTURES / fixture
    source_volume = compute_metrics(load_cad_shape(path))["volume"]
    center = bbox_point(load_cad_shape(path))
    result = b3d_runner.execute(
        f"""
base = load_step({str(path)!r})
assert isinstance(base, Part)
moved = translate(base, 100, 0, 0)
assert isinstance(moved, Part), type(moved)
turned = rotate(moved, 'Z', 90)
assert isinstance(turned, Part), type(turned)
cutter = Cylinder(radius=2, height=500).translate(({center[1]!r} * -1, 100 + {center[0]!r}, 0))
trimmed = safe_cut(turned, cutter)
assert isinstance(trimmed, Part), type(trimmed)
assert trimmed.volume > 0
assert len(trimmed.faces()) > 0
assert trimmed.wrapped is not None
# Native build123d algebra keeps working on the helper result.
final = trimmed - Box(1, 1, 1).translate((500, 500, 500))
show_object(final)
"""
    )
    assert result.success, result.exception
    metrics = compute_metrics(result.topo_shape)
    assert 0 < metrics["volume"] < source_volume
    assert metrics["is_valid"]


def test_runner_compat_primitives_stay_build123d():
    """The runner swaps in agentcad subclasses of Box/Cylinder (issue #192);
    those must be recognised as build123d shapes too."""
    result = b3d_runner.execute(
        """
moved = translate(Box(10, 10, 10), 20, 0, 0)
assert isinstance(moved, Part), type(moved)
cut = safe_cut(Cylinder(radius=5, height=10), Box(2, 2, 20))
assert isinstance(cut, Part), type(cut)
show_object(moved + cut)
"""
    )
    assert result.success, result.exception


def test_load_step_shape_raw_pipeline_still_works():
    path = FIXTURES / "single_solid.step"
    result = b3d_runner.execute(
        f"""
raw = load_step_shape({str(path)!r})
moved = translate(raw, 10, 0, 0)
assert not hasattr(moved, 'wrapped')
trimmed = safe_cut(moved, Cylinder(radius=2, height=500).translate((10, 0, 0)).wrapped)
assert not hasattr(trimmed, 'wrapped')
show_object(trimmed)
"""
    )
    assert result.success, result.exception
    assert compute_metrics(result.topo_shape)["volume"] > 0


def test_mixed_raw_and_wrapped_inputs_in_one_boolean():
    path = FIXTURES / "single_solid.step"
    result = b3d_runner.execute(
        f"""
base = load_step({str(path)!r})
raw_tool = load_step_shape({str(path)!r})
shared = safe_intersection(base, translate(raw_tool, 1, 0, 0))
assert isinstance(shared, Part), type(shared)
merged = safe_fuse(raw_tool, base)
assert not hasattr(merged, 'wrapped')
show_object(shared)
"""
    )
    assert result.success, result.exception


def test_show_assembly_and_assemble_accept_raw_shapes():
    path = FIXTURES / "single_solid.step"
    result = b3d_runner.execute(
        f"""
raw = load_step_shape({str(path)!r})
show_assembly([raw, translate(raw, 100, 0, 0)], name="pair")
show_object(assemble(raw, Box(1, 1, 1).translate((300, 0, 0))), name="assembled")
show_assembly(raw, name="single_raw")
"""
    )
    assert result.success, result.exception
    assert result.output_type == "assembly"
    volumes = [compute_metrics(part["topo_shape"])["volume"] for part in result.parts]
    assert volumes[0] == pytest.approx(2 * volumes[2])
    assert volumes[1] == pytest.approx(volumes[2] + 1)


def test_id_edit_helpers_accept_raw_shapes():
    path = FIXTURES / "single_solid.step"
    result = b3d_runner.execute(
        f"""
raw = load_step_shape({str(path)!r})
filleted = fillet_edges(raw, 1, 0.5)
assert hasattr(filleted, "wrapped"), type(filleted)
below, above = split_by_plane(raw, 'XY')
profile = Sketch() + Circle(radius=1)
pocketed = cut_pocket(raw, 1, profile, 1)
show_object(filleted)
"""
    )
    assert result.success, result.exception


def test_runner_warns_when_script_wraps_raw_solid_in_compound():
    """The script's own `.volume` reads 0 while the run JSON reports the real
    volume; the runner names the mismatch so the agent stops guessing."""
    path = FIXTURES / "single_solid.step"
    result = b3d_runner.execute(
        f"""
raw = translate(load_step_shape({str(path)!r}), 1, 0, 0)
wrapped = Part(raw)
assert wrapped.volume == 0
show_object(wrapped)
"""
    )
    assert result.success, result.exception
    assert compute_metrics(result.topo_shape)["volume"] == pytest.approx(8000)
    assert any("wrapping a bare solid" in w for w in result.warnings), result.warnings

    clean = b3d_runner.execute(
        f"""
base = translate(load_step({str(path)!r}), 1, 0, 0)
assert base.volume > 0
show_object(base)
"""
    )
    assert clean.success, clean.exception
    assert not any("bare solid" in w for w in clean.warnings)


def test_shell_faces_raw_input_returns_measurable_compound_backed_part():
    """MakeThickSolid yields a bare TopoDS_Solid; shell_faces must not hand
    that back inside Part(...) (volume 0, iterates shells)."""
    result = b3d_runner.execute(
        """
from OCP.TopAbs import TopAbs_COMPOUND
for source in (Box(10, 20, 30).wrapped, Box(10, 20, 30)):
    shelled = shell_faces(source, 1, -1)
    assert isinstance(shelled, Part), type(shelled)
    assert shelled.wrapped.ShapeType() == TopAbs_COMPOUND
    assert shelled.volume > 0, shelled.volume
    assert len(shelled.solids()) == 1
    assert [type(child).__name__ for child in shelled] == ['Solid']
    assert abs(shelled.volume - shelled.solids()[0].volume) < 1e-6
show_object(shelled)
"""
    )
    assert result.success, result.exception
    assert 0 < compute_metrics(result.topo_shape)["volume"] < 6000
    assert not any("bare solid" in w for w in result.warnings)
