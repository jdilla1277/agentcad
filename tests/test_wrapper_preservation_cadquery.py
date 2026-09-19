"""Issue #194 on the CadQuery runtime: helpers keep cq wrappers as cq wrappers.

Skipped automatically when the optional ``cadquery`` extra is not installed
(see the root conftest).
"""

import cadquery as cq
import pytest
from OCP.TopoDS import TopoDS_Shape

from agentcad.helpers import (
    assemble,
    bbox_point,
    copy_shape,
    mirror_fuse,
    rotate,
    safe_cut,
    safe_fuse,
    translate,
)
from agentcad.runners import cadquery as cq_runner


def _box():
    return cq.Workplane("XY").box(10, 20, 30)


@pytest.mark.parametrize("edit", [
    pytest.param(lambda s: translate(s, 5, 0, 0), id="translate"),
    pytest.param(lambda s: rotate(s, "Z", 30), id="rotate"),
    pytest.param(lambda s: copy_shape(s), id="copy_shape"),
    pytest.param(lambda s: mirror_fuse(translate(s, 20, 0, 0), "YZ"), id="mirror_fuse"),
    pytest.param(lambda s: safe_cut(s, cq.Workplane("XY").cylinder(60, 3).val()), id="safe_cut"),
    pytest.param(lambda s: safe_fuse(s, cq.Workplane("XY").sphere(4).translate((0, 0, 15)).val()), id="safe_fuse"),
])
def test_cadquery_shape_stays_cadquery_shape(edit):
    result = edit(_box().val())
    assert isinstance(result, cq.Shape), type(result)
    assert result.Volume() > 0


def test_workplane_in_gives_workplane_out():
    moved = translate(_box(), 50, 0, 0)
    assert isinstance(moved, cq.Workplane)
    assert bbox_point(moved) == pytest.approx((50, 0, 0))
    assert isinstance(translate(_box().val().wrapped, 1, 1, 1), TopoDS_Shape)


def test_assemble_accepts_mixed_representations():
    shape = _box().val()
    result = assemble(shape, _box().translate((50, 0, 0)), shape.wrapped)
    assert isinstance(result, cq.Workplane)
    assert result.val().Volume() == pytest.approx(3 * 6000)


def test_cadquery_runner_pipeline_keeps_cq_objects():
    result = cq_runner.execute(
        """
part = cq.Workplane('XY').box(10, 20, 5).val()
moved = translate(part, 50, 0, 0)
trimmed = safe_cut(moved, cq.Workplane('XY').cylinder(20, 2).translate((50, 0, 0)).val())
assert isinstance(trimmed, cq.Shape), type(trimmed)
show_object(cq.Workplane('XY').newObject([trimmed]))
"""
    )
    assert result.success, result.exception


# ---------------------------------------------------------------------------
# Multi-object Workplanes: every stack object survives, plane is preserved.
# ---------------------------------------------------------------------------

def _two_boxes():
    """Two 1 mm^3 boxes on one Workplane stack (combine=False keeps both)."""
    return cq.Workplane("XZ").pushPoints([(0, 0), (5, 0)]).box(1, 1, 1, combine=False)


def _total_volume(workplane):
    return sum(shape.Volume() for shape in workplane.vals())


def _assert_plane_preserved(source, result):
    assert result.plane.origin.toTuple() == pytest.approx(source.plane.origin.toTuple())
    assert result.plane.zDir.toTuple() == pytest.approx(source.plane.zDir.toTuple())
    assert result.plane.xDir.toTuple() == pytest.approx(source.plane.xDir.toTuple())


@pytest.mark.parametrize("edit", [
    pytest.param(lambda wp: translate(wp, 1, 0, 0), id="translate"),
    pytest.param(lambda wp: rotate(wp, "Z", 90), id="rotate"),
    pytest.param(lambda wp: copy_shape(wp), id="copy_shape"),
])
def test_multi_object_workplane_transforms_keep_every_object(edit):
    source = _two_boxes()
    assert len(source.vals()) == 2

    result = edit(source)

    assert isinstance(result, cq.Workplane)
    assert len(result.vals()) == 2
    assert _total_volume(result) == pytest.approx(2.0)
    assert _total_volume(source) == pytest.approx(2.0)
    _assert_plane_preserved(source, result)
    # The result chains from the original workplane instead of a fresh one.
    assert result.parent is source


def test_multi_object_workplane_translate_moves_both_objects():
    source = _two_boxes()
    result = translate(source, 1, 0, 0)
    centers = sorted(round(v.Center().x, 6) for v in result.vals())
    assert centers == pytest.approx([1.0, 6.0])


def test_multi_object_workplane_boolean_uses_every_object():
    source = _two_boxes()
    # Cut three quarters off the second box (x 4.75..5.5 of 4.5..5.5); the
    # first box must survive untouched.
    tool = cq.Workplane("XY").box(1, 2, 2).translate((5.25, 0, 0))

    trimmed = safe_cut(source, tool)

    assert isinstance(trimmed, cq.Workplane)
    assert len(trimmed.vals()) == 2
    assert sorted(v.Volume() for v in trimmed.vals()) == pytest.approx([0.25, 1.0])
    _assert_plane_preserved(source, trimmed)

    # Fusing a bridge between the boxes yields one solid; the stack reports
    # the pieces that actually exist rather than the input count.
    bridge = cq.Workplane("XY").box(4, 0.5, 0.5).translate((2.5, 0, 0))
    merged = safe_fuse(source, bridge)
    assert isinstance(merged, cq.Workplane)
    assert len(merged.vals()) == 1
    assert _total_volume(merged) == pytest.approx(3.0)


def test_multi_object_workplane_bbox_covers_every_object():
    assert bbox_point(_two_boxes(), "max", "max", "max") == pytest.approx((5.5, 0.5, 0.5))


def test_empty_workplane_is_rejected_with_a_clear_error():
    with pytest.raises(TypeError, match="Use translate"):
        translate(cq.Workplane("XY"), 1, 0, 0)


def test_cadquery_runner_multi_object_pipeline_end_to_end():
    result = cq_runner.execute(
        """
base = cq.Workplane('XY').pushPoints([(0, 0), (20, 0)]).box(10, 10, 10, combine=False)
moved = translate(base, 0, 0, 5)
assert len(moved.vals()) == 2
trimmed = safe_cut(moved, cq.Workplane('XY').cylinder(40, 2).translate((20, 0, 0)))
assert isinstance(trimmed, cq.Workplane) and len(trimmed.vals()) == 2
show_object(trimmed)
"""
    )
    assert result.success, result.exception
