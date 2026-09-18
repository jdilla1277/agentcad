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
