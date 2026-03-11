import math

import cadquery as cq
import pytest
from OCP.BRep import BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCP.Bnd import Bnd_Box
from OCP.TopAbs import TopAbs_FACE, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

from cadtool.helpers import (
    assemble,
    bbox_point,
    loft_sections,
    mirror_fuse,
    naca_wire,
    place_at,
    rotate,
    tapered_sweep,
    translate,
)


# ── Test utilities ──────────────────────────────────────────────


def _make_circle_wire(x, y, z, radius, normal=(0, 0, 1)):
    """Build a TopoDS_Wire circle at a 3D point."""
    center = gp_Pnt(x, y, z)
    axis = gp_Ax2(center, gp_Dir(*normal))
    circ = gp_Circ(axis, radius)
    edge = BRepBuilderAPI_MakeEdge(circ).Edge()
    wire = BRepBuilderAPI_MakeWire(edge).Wire()
    return wire


def _count_faces(shape):
    """Count faces using TopExp_Explorer."""
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    count = 0
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def _bounding_box(shape):
    """Return (xmin, ymin, zmin, xmax, ymax, zmax) via BRepBndLib."""
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    return box.Get()


# ── loft_sections tests ────────────────────────────────────────


class TestLoftSections:
    def test_loft_sections_produces_solid(self):
        sections = [
            _make_circle_wire(0, 0, 0, 10),
            _make_circle_wire(0, 0, 10, 8),
            _make_circle_wire(0, 0, 20, 5),
        ]
        result = loft_sections(sections)
        assert result.ShapeType() == TopoDS.Solid_s(result).ShapeType()

    def test_loft_sections_has_faces(self):
        sections = [
            _make_circle_wire(0, 0, 0, 10),
            _make_circle_wire(0, 0, 10, 8),
            _make_circle_wire(0, 0, 20, 5),
        ]
        result = loft_sections(sections)
        assert _count_faces(result) > 0

    def test_loft_sections_smooth_vs_ruled(self):
        sections = [
            _make_circle_wire(0, 0, 0, 10),
            _make_circle_wire(0, 0, 10, 3),
            _make_circle_wire(0, 0, 20, 10),
        ]
        smooth = loft_sections(sections, smooth=True)
        ruled = loft_sections(sections, smooth=False)
        # Different interpolation → different bounding boxes
        bb_smooth = _bounding_box(smooth)
        bb_ruled = _bounding_box(ruled)
        # At least one dimension should differ
        diffs = [abs(a - b) for a, b in zip(bb_smooth, bb_ruled)]
        assert max(diffs) > 0.01

    def test_loft_sections_two_sections_minimum(self):
        sections = [
            _make_circle_wire(0, 0, 0, 10),
            _make_circle_wire(0, 0, 15, 5),
        ]
        result = loft_sections(sections)
        assert result.ShapeType() == TopoDS.Solid_s(result).ShapeType()

    def test_loft_sections_single_section_error(self):
        sections = [_make_circle_wire(0, 0, 0, 10)]
        with pytest.raises(ValueError):
            loft_sections(sections)


# ── tapered_sweep tests ────────────────────────────────────────


class TestTaperedSweep:
    def test_tapered_sweep_produces_solid(self):
        spine = [(0, 0, 0), (0, 0, 5), (0, 0, 10), (0, 0, 15), (0, 0, 20)]
        radii = [5, 7, 10, 7, 5]
        result = tapered_sweep(spine, radii)
        assert result.ShapeType() == TopoDS.Solid_s(result).ShapeType()

    def test_tapered_sweep_banana_shape(self):
        spine = [(0, 0, 0), (35, 0, 20), (70, 0, 28), (105, 0, 22), (140, 0, 5)]
        radii = [0.5, 7, 11, 10, 6]
        result = tapered_sweep(spine, radii)
        bb = _bounding_box(result)
        # xmin, ymin, zmin, xmax, ymax, zmax
        x_span = bb[3] - bb[0]
        assert x_span > 100  # banana is long

    def test_tapered_sweep_straight_spine(self):
        spine = [(0, 0, 0), (10, 0, 0), (20, 0, 0)]
        radii = [3, 5, 3]
        result = tapered_sweep(spine, radii)
        assert _count_faces(result) > 0

    def test_tapered_sweep_mismatched_lengths_error(self):
        spine = [(0, 0, 0), (0, 0, 10)]
        radii = [5, 7, 3]
        with pytest.raises(ValueError):
            tapered_sweep(spine, radii)

    def test_tapered_sweep_too_few_points_error(self):
        spine = [(0, 0, 0)]
        radii = [5]
        with pytest.raises(ValueError):
            tapered_sweep(spine, radii)


# ── naca_wire tests ────────────────────────────────────────────


class TestNacaWire:
    def test_naca_wire_produces_wire(self):
        wire = naca_wire(y=0, le_x=0, te_x=100, thickness=12)
        from OCP.TopoDS import TopoDS
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)  # should not raise

    def test_naca_wire_is_closed(self):
        wire = naca_wire(y=0, le_x=0, te_x=100, thickness=12)
        assert wire.Closed()

    def test_naca_wire_at_position(self):
        wire = naca_wire(y=5.0, le_x=0, te_x=100, thickness=12)
        explorer = TopExp_Explorer(wire, TopAbs_VERTEX)
        while explorer.More():
            vtx = TopoDS.Vertex_s(explorer.Current())
            pt = BRep_Tool.Pnt_s(vtx)
            assert abs(pt.Y() - 5.0) < 0.01
            explorer.Next()

    def test_naca_wire_symmetric_profile(self):
        wire = naca_wire(y=0, le_x=0, te_x=100, thickness=12, profile="0012")
        bb = _bounding_box(wire)
        # zmin and zmax should be roughly symmetric about z=0
        assert abs(bb[2] + bb[5]) < 0.5  # zmin ≈ -zmax

    def test_naca_wire_different_profiles_differ(self):
        w1 = naca_wire(y=0, le_x=0, te_x=100, thickness=12, profile="0012")
        w2 = naca_wire(y=0, le_x=0, te_x=100, thickness=24, profile="0024")
        bb1 = _bounding_box(w1)
        bb2 = _bounding_box(w2)
        # Different thickness → different Z extents
        z_span_1 = bb1[5] - bb1[2]
        z_span_2 = bb2[5] - bb2[2]
        assert abs(z_span_1 - z_span_2) > 1.0


# ── mirror_fuse tests ──────────────────────────────────────────


class TestMirrorFuse:
    def _make_half_box(self):
        """A box spanning x=[0,10], y=[0,10], z=[0,10]."""
        import cadquery as cq
        box = cq.Workplane("XY").box(10, 10, 10, centered=False)
        return box.val().wrapped

    def test_mirror_fuse_xz_plane(self):
        shape = self._make_half_box()
        result = mirror_fuse(shape, plane="XZ")
        bb = _bounding_box(result)
        # Original y=[0,10], mirrored → y=[-10,10]
        assert bb[1] < -9.0  # ymin
        assert bb[4] > 9.0   # ymax

    def test_mirror_fuse_produces_solid(self):
        shape = self._make_half_box()
        result = mirror_fuse(shape, plane="XZ")
        assert result.ShapeType() == TopoDS.Solid_s(result).ShapeType()

    def test_mirror_fuse_yz_plane(self):
        shape = self._make_half_box()
        result = mirror_fuse(shape, plane="YZ")
        bb = _bounding_box(result)
        assert bb[0] < -9.0  # xmin
        assert bb[3] > 9.0   # xmax

    def test_mirror_fuse_xy_plane(self):
        shape = self._make_half_box()
        result = mirror_fuse(shape, plane="XY")
        bb = _bounding_box(result)
        assert bb[2] < -9.0  # zmin
        assert bb[5] > 9.0   # zmax

    def test_mirror_fuse_invalid_plane_error(self):
        shape = self._make_half_box()
        with pytest.raises(ValueError):
            mirror_fuse(shape, plane="ABC")


# ── translate tests ───────────────────────────────────────────


class TestTranslate:
    def test_translate_moves_bounding_box(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        moved = translate(box, 50, 0, 0)
        bb = _bounding_box(moved)
        assert bb[0] > 40  # xmin moved from -5 to 45

    def test_translate_preserves_volume(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        moved = translate(box, 50, 100, 200)
        from OCP.GProp import GProp_GProps
        from OCP.BRepGProp import BRepGProp
        props_orig, props_moved = GProp_GProps(), GProp_GProps()
        BRepGProp.VolumeProperties_s(box, props_orig)
        BRepGProp.VolumeProperties_s(moved, props_moved)
        assert abs(props_orig.Mass() - props_moved.Mass()) < 0.01

    def test_translate_zero_is_identity(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        moved = translate(box, 0, 0, 0)
        bb_orig = _bounding_box(box)
        bb_moved = _bounding_box(moved)
        for a, b in zip(bb_orig, bb_moved):
            assert abs(a - b) < 0.01


# ── rotate tests ──────────────────────────────────────────────


class TestRotate:
    def test_rotate_z_90_swaps_dims(self):
        box = cq.Workplane("XY").box(10, 20, 5).val().wrapped
        rotated = rotate(box, "Z", 90)
        bb = _bounding_box(rotated)
        x_extent = bb[3] - bb[0]
        y_extent = bb[4] - bb[1]
        assert abs(x_extent - 20) < 0.1  # was Y=20, now X≈20
        assert abs(y_extent - 10) < 0.1  # was X=10, now Y≈10

    def test_rotate_preserves_volume(self):
        box = cq.Workplane("XY").box(10, 20, 5).val().wrapped
        rotated = rotate(box, "X", 45)
        from OCP.GProp import GProp_GProps
        from OCP.BRepGProp import BRepGProp
        props_orig, props_rotated = GProp_GProps(), GProp_GProps()
        BRepGProp.VolumeProperties_s(box, props_orig)
        BRepGProp.VolumeProperties_s(rotated, props_rotated)
        assert abs(props_orig.Mass() - props_rotated.Mass()) < 0.01

    def test_rotate_invalid_axis_raises(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        with pytest.raises(ValueError, match="must be"):
            rotate(box, "W", 90)


# ── bbox_point tests ─────────────────────────────────────────


class TestBboxPoint:
    def test_min_center_max_on_centered_box(self):
        """A 10x20x30 box centered at origin: min=-5,-10,-15 max=5,10,15."""
        box = cq.Workplane("XY").box(10, 20, 30).val().wrapped
        pt_min = bbox_point(box, "min", "min", "min")
        assert abs(pt_min[0] - (-5)) < 0.01
        assert abs(pt_min[1] - (-10)) < 0.01
        assert abs(pt_min[2] - (-15)) < 0.01

    def test_max_corner(self):
        box = cq.Workplane("XY").box(10, 20, 30).val().wrapped
        pt_max = bbox_point(box, "max", "max", "max")
        assert abs(pt_max[0] - 5) < 0.01
        assert abs(pt_max[1] - 10) < 0.01
        assert abs(pt_max[2] - 15) < 0.01

    def test_center_is_midpoint(self):
        box = cq.Workplane("XY").box(10, 20, 30).val().wrapped
        pt = bbox_point(box, "center", "center", "center")
        assert abs(pt[0]) < 0.01
        assert abs(pt[1]) < 0.01
        assert abs(pt[2]) < 0.01

    def test_mixed_axes(self):
        box = cq.Workplane("XY").box(10, 20, 30).val().wrapped
        pt = bbox_point(box, "min", "max", "center")
        assert abs(pt[0] - (-5)) < 0.01
        assert abs(pt[1] - 10) < 0.01
        assert abs(pt[2]) < 0.01

    def test_non_origin_shape(self):
        """A box translated away from origin."""
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        moved = translate(box, 100, 200, 300)
        pt = bbox_point(moved, "min", "min", "min")
        assert abs(pt[0] - 95) < 0.01
        assert abs(pt[1] - 195) < 0.01
        assert abs(pt[2] - 295) < 0.01

    def test_invalid_axis_value_raises(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        with pytest.raises(ValueError, match="bad"):
            bbox_point(box, "bad", "center", "center")

    def test_returns_tuple_of_three(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        pt = bbox_point(box, "center", "center", "center")
        assert isinstance(pt, tuple)
        assert len(pt) == 3


# ── place_at tests ───────────────────────────────────────────


class TestPlaceAt:
    def test_moves_shape_correctly(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        # Move bottom-center to (50, 50, 0)
        placed = place_at(
            box,
            from_pt=bbox_point(box, "center", "center", "min"),
            to_pt=(50, 50, 0),
        )
        bb = _bounding_box(placed)
        # New bbox: x=[45,55], y=[45,55], z=[0,10]
        assert abs(bb[0] - 45) < 0.01
        assert abs(bb[5] - 10) < 0.01

    def test_preserves_volume(self):
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        placed = place_at(box, from_pt=(0, 0, 0), to_pt=(100, 200, 300))
        props_orig, props_placed = GProp_GProps(), GProp_GProps()
        BRepGProp.VolumeProperties_s(box, props_orig)
        BRepGProp.VolumeProperties_s(placed, props_placed)
        assert abs(props_orig.Mass() - props_placed.Mass()) < 0.01

    def test_combines_with_bbox_point(self):
        """The canonical use case: snap leg bottom to base top-corner."""
        base = cq.Workplane("XY").box(100, 100, 10).val().wrapped
        leg = cq.Workplane("XY").box(5, 5, 80).val().wrapped
        placed_leg = place_at(
            leg,
            from_pt=bbox_point(leg, "center", "center", "min"),
            to_pt=bbox_point(base, "max", "max", "max"),
        )
        bb = _bounding_box(placed_leg)
        # Leg top should be at z = 5 (base top) + 80 (leg height) = 85
        assert abs(bb[5] - 85) < 0.1
        # Leg center x,y should be at 50,50 (base max corner)
        x_center = (bb[0] + bb[3]) / 2
        y_center = (bb[1] + bb[4]) / 2
        assert abs(x_center - 50) < 0.1
        assert abs(y_center - 50) < 0.1


# ── assemble tests ───────────────────────────────────────────


class TestAssemble:
    def test_single_shape(self):
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        result = assemble(box)
        # Should be a CQ Workplane usable with show_object
        assert hasattr(result, "val")

    def test_multiple_shapes(self):
        box1 = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        box2 = translate(cq.Workplane("XY").box(5, 5, 20).val().wrapped, 20, 0, 0)
        result = assemble(box1, box2)
        assert hasattr(result, "val")

    def test_returns_valid_compound(self):
        box1 = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        box2 = translate(cq.Workplane("XY").box(5, 5, 5).val().wrapped, 30, 0, 0)
        result = assemble(box1, box2)
        compound = result.val()
        # Should be a compound containing 2 solids
        from OCP.TopAbs import TopAbs_SOLID
        from OCP.TopExp import TopExp_Explorer
        explorer = TopExp_Explorer(compound.wrapped, TopAbs_SOLID)
        count = 0
        while explorer.More():
            count += 1
            explorer.Next()
        assert count == 2

    def test_show_object_compatible(self):
        """assemble() output can be passed to show_object via CQGI."""
        box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
        result = assemble(box)
        # show_object expects a CQ object — val() should not raise
        val = result.val()
        assert val is not None
