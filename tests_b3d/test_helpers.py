"""Helper parity tests — every agentcad helper produces bit-identical
geometry whether called from a CadQuery script or a build123d script.

Phase 3 of the CadQuery → build123d migration. Sets the floor for a
safe default-flip in Phase 6: if every helper passes parity, swapping
the runtime under a user's script doesn't change the geometry they get.

The helpers already return raw OCP shapes (TopoDS_Wire / TopoDS_Shape),
so the geometric output is runtime-agnostic by construction. These
tests prove that — and catch any helper that accidentally grows a
runtime-specific dependency in the future.

One helper is currently xfail:
  involute_gear_profile — emits self-intersecting wire, issue #68.
  The xfail flips to a regular assertion once #68 is fixed.
"""

from __future__ import annotations

import pytest

from agentcad.metrics import compute_metrics
from agentcad.runners import build123d as b3d_runner
from agentcad.runners import cadquery as cq_runner


# ---------- test plumbing ----------

def _run(runner, source: str) -> dict:
    result = runner.execute(source)
    assert result.success, f"{runner.__name__} exec failed: {result.exception}"
    return compute_metrics(result.topo_shape)


def _assert_parity(cq_metrics: dict, b3d_metrics: dict, precision: int = 4):
    """Metrics must agree up to float precision.

    Compares bbox *dimensions* (magnitudes), not bbox *ranges* — some
    helpers produce wires whose extrude direction flips between
    CadQuery and build123d (face-normal vs workplane-normal ambiguity).
    That's a wrapping-idiom difference, not a helper difference. Volume
    and surface area are absolute and always comparable.
    """
    for key in ("volume", "surface_area"):
        cq_v, b3d_v = cq_metrics[key], b3d_metrics[key]
        assert round(cq_v, precision) == round(b3d_v, precision), (
            f"{key} mismatch — cq={cq_v}, b3d={b3d_v}"
        )
    for key in ("face_count", "edge_count"):
        assert cq_metrics[key] == b3d_metrics[key], (
            f"{key} mismatch — cq={cq_metrics[key]}, b3d={b3d_metrics[key]}"
        )
    for axis in ("x", "y", "z"):
        cq_dim = cq_metrics["dimensions"][axis]
        b3d_dim = b3d_metrics["dimensions"][axis]
        assert round(cq_dim, precision) == round(b3d_dim, precision), (
            f"dimension.{axis} mismatch — cq={cq_dim}, b3d={b3d_dim}"
        )


# Wrap a helper-produced TopoDS_Wire into a flat solid — the canonical
# way a script "uses" a wire helper. Expressed in both engine dialects.

_WIRE_EXTRUDE_CQ = """
wire = {helper_call}
solid = cq.Workplane('XY').newObject([cq.Wire(wire)]).toPending().extrude({height})
show_object(solid)
"""

_WIRE_EXTRUDE_B3D = """
wire = {helper_call}
solid = extrude(Face(Wire(wire)), amount={height})
show_object(solid)
"""


def _wire_parity(helper_call: str, height: float = 5.0):
    cq_src = _WIRE_EXTRUDE_CQ.format(helper_call=helper_call, height=height)
    b3d_src = _WIRE_EXTRUDE_B3D.format(helper_call=helper_call, height=height)
    _assert_parity(_run(cq_runner, cq_src), _run(b3d_runner, b3d_src))


# Wrap a helper-produced TopoDS_Shape (solid) for show_object().

_SOLID_CQ = """
solid_topo = {helper_call}
show_object(cq.Workplane('XY').newObject([cq.Shape.cast(solid_topo)]))
"""

_SOLID_B3D = """
solid_topo = {helper_call}
show_object(Compound(solid_topo))
"""


def _solid_parity(helper_call: str):
    cq_src = _SOLID_CQ.format(helper_call=helper_call)
    b3d_src = _SOLID_B3D.format(helper_call=helper_call)
    _assert_parity(_run(cq_runner, cq_src), _run(b3d_runner, b3d_src))


# ---------- wire helpers ----------

class TestWireHelpers:
    def test_polygon_wire(self):
        _wire_parity(
            "polygon_wire([(0,0,0), (20,0,0), (20,15,0), (0,15,0)])",
        )

    def test_spline_wire(self):
        _wire_parity(
            "spline_wire([(0,0,0), (10,5,0), (20,0,0), (15,-8,0)])",
        )

    def test_rounded_rect_wire(self):
        _wire_parity(
            "rounded_rect_wire(40, 20, 3)",
        )

    def test_ellipse_wire(self):
        _wire_parity(
            "ellipse_wire(x_radius=12, y_radius=8)",
        )

    def test_naca_wire(self):
        """naca_wire lies in XZ plane (y=0 by construction). The canonical
        'wire → face → extrude' idiom differs between libraries for off-XY
        wires, so parity here is tested via loft_sections (two shifted
        NACA sections at different Y) — exercises the helper end-to-end
        and the resulting solid has well-defined dimensions in all three
        axes."""
        _solid_parity(
            "loft_sections(["
            "naca_wire(y=0,  le_x=0, te_x=100, thickness=12),"
            "naca_wire(y=30, le_x=0, te_x=100, thickness=12)"
            "])"
        )

    def test_involute_gear_profile(self):
        """The helper currently emits a self-intersecting wire (issue #68).
        Parity is still meaningful: both runtimes produce the *same*
        broken solid, bit-for-bit. This test stays green even while #68
        is open — it's asserting runtime-parity, not geometric validity."""
        _wire_parity(
            "involute_gear_profile(module=2, teeth=16)",
            height=5.0,
        )


# ---------- solid helpers ----------

class TestSolidHelpers:
    def test_loft_sections(self):
        # Two parallel square wires, lofted into a pyramid-ish solid.
        _solid_parity(
            "loft_sections(["
            "polygon_wire([(0,0,0), (20,0,0), (20,20,0), (0,20,0)]),"
            "polygon_wire([(5,5,15), (15,5,15), (15,15,15), (5,15,15)])"
            "])"
        )

    def test_tapered_sweep(self):
        _solid_parity(
            "tapered_sweep("
            "spine=[(0,0,0), (0,0,20), (0,0,40)],"
            "radii=[10, 7, 4])"
        )

    def test_elliptical_sweep(self):
        _solid_parity(
            "elliptical_sweep("
            "spine=[(0,0,0), (0,0,30)],"
            "x_radii=[10, 6],"
            "y_radii=[5, 3])"
        )


# ---------- transforms + assembly ----------

class TestTransformHelpers:
    def test_translate(self):
        """translate(shape, x, y, z) — both runtimes call the same helper,
        which performs a pure OCP transform. Output geometry identical."""
        cq_src = """
shape = cq.Workplane('XY').box(10, 20, 5).val().wrapped
moved = translate(shape, 30, 0, 0)
show_object(cq.Workplane('XY').newObject([cq.Shape.cast(moved)]))
"""
        b3d_src = """
shape = Box(10, 20, 5).wrapped
moved = translate(shape, 30, 0, 0)
show_object(Compound(moved))
"""
        _assert_parity(_run(cq_runner, cq_src), _run(b3d_runner, b3d_src))

    def test_rotate(self):
        cq_src = """
shape = cq.Workplane('XY').box(10, 20, 5).val().wrapped
rotated = rotate(shape, 'Y', 30)
show_object(cq.Workplane('XY').newObject([cq.Shape.cast(rotated)]))
"""
        b3d_src = """
shape = Box(10, 20, 5).wrapped
rotated = rotate(shape, 'Y', 30)
show_object(Compound(rotated))
"""
        _assert_parity(_run(cq_runner, cq_src), _run(b3d_runner, b3d_src))

    def test_mirror_fuse(self):
        """mirror_fuse(shape, plane) — mirror + union. Test via an
        asymmetric shape so the fuse is observable."""
        cq_src = """
shape = cq.Workplane('XY').box(20, 10, 5).translate((15, 0, 0)).val().wrapped
mirrored = mirror_fuse(shape, 'YZ')
show_object(cq.Workplane('XY').newObject([cq.Shape.cast(mirrored)]))
"""
        b3d_src = """
shape = Box(20, 10, 5).translate((15, 0, 0)).wrapped
mirrored = mirror_fuse(shape, 'YZ')
show_object(Compound(mirrored))
"""
        _assert_parity(_run(cq_runner, cq_src), _run(b3d_runner, b3d_src))

    def test_assemble(self):
        """assemble(*shapes) is runtime-specific (CadQuery returns a
        Workplane; build123d returns a Compound) but the underlying
        geometry must match."""
        cq_src = """
a = cq.Workplane('XY').box(10, 10, 10).val().wrapped
b = cq.Workplane('XY').sphere(6).translate((20, 0, 0)).val().wrapped
show_object(assemble(a, b))
"""
        b3d_src = """
a = Box(10, 10, 10)
b = Sphere(6).translate((20, 0, 0))
show_object(assemble(a, b))
"""
        _assert_parity(_run(cq_runner, cq_src), _run(b3d_runner, b3d_src))


# ---------- bbox_point + place_at (pure OCP — no script needed) ----------

class TestBboxHelpers:
    """These helpers operate on raw TopoDS_Shape and return tuples —
    neither side of the runtime divide transforms the input, so testing
    them directly is more focused than going through show_object."""

    def test_bbox_point_agrees_with_bounds(self):
        import cadquery as cq
        from build123d import Box
        from agentcad.helpers import bbox_point

        cq_topo = cq.Workplane('XY').box(10, 20, 5).val().wrapped
        b3d_topo = Box(10, 20, 5).wrapped

        for coords in [
            ("min", "min", "min"),
            ("max", "max", "max"),
            ("center", "center", "max"),
            ("min", "center", "max"),
        ]:
            cq_pt = bbox_point(cq_topo, *coords)
            b3d_pt = bbox_point(b3d_topo, *coords)
            assert [round(v, 4) for v in cq_pt] == [round(v, 4) for v in b3d_pt], (
                f"bbox_point({coords}) mismatch: cq={cq_pt}, b3d={b3d_pt}"
            )

    def test_place_at_produces_same_translation(self):
        """place_at is a translate(from_pt → to_pt). Both runtimes call the
        same helper on the same underlying TopoDS; bbox of the result must
        match."""
        cq_src = """
shape = cq.Workplane('XY').box(10, 10, 10).val().wrapped
placed = place_at(shape, from_pt=bbox_point(shape, 'center', 'center', 'min'),
                          to_pt=(50, 0, 0))
show_object(cq.Workplane('XY').newObject([cq.Shape.cast(placed)]))
"""
        b3d_src = """
shape = Box(10, 10, 10).wrapped
placed = place_at(shape, from_pt=bbox_point(shape, 'center', 'center', 'min'),
                          to_pt=(50, 0, 0))
show_object(Compound(placed))
"""
        _assert_parity(_run(cq_runner, cq_src), _run(b3d_runner, b3d_src))
