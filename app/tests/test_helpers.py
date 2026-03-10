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

from cadtool.helpers import loft_sections, mirror_fuse, naca_wire, rotate, tapered_sweep, translate


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
