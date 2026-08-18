import math

import cadquery as cq
import pytest
from OCP.BRep import BRep_Tool
from OCP.BRepGProp import BRepGProp
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec

from agentcad.helpers import (
    annular_boss,
    assemble,
    bbox_point,
    copy_shape,
    ellipse_wire,
    elliptical_sweep,
    involute_gear_profile,
    loft_sections,
    mirror_fuse,
    naca_wire,
    place_at,
    polygon_wire,
    raise_annulus,
    rotate,
    rounded_rect_wire,
    spline_wire,
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


def _volume(shape):
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


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

    def test_tapered_sweep_warns_on_kinked_spine(self):
        """Kinked spine — linear→parabolic→linear, the failure mode from
        the Andy Warhol Bridge friction test (#88). Each interior joint
        whose incoming/outgoing segments differ by >5° fires a warning."""
        import warnings as _w
        spine = [
            (0, 0, 0), (50, 0, 0),       # linear
            (75, 0, 5), (100, 0, 5),     # bumped up
            (150, 0, 0), (200, 0, 0),    # linear
        ]
        radii = [1.5] * 6
        with _w.catch_warnings(record=True) as captured:
            _w.simplefilter("always")
            tapered_sweep(spine, radii)
        kink_msgs = [str(w.message) for w in captured
                     if "tapered_sweep" in str(w.message)]
        # 4 interior points (i=1..4) all kink — exact count is implementation
        # detail; what matters is at least one warning fires with the
        # discriminating phrase + a degree value.
        assert len(kink_msgs) >= 1
        assert any("tangent discontinuity" in m for m in kink_msgs)
        assert any("°" in m for m in kink_msgs)

    def test_tapered_sweep_no_warning_on_smooth_spine(self):
        """A clearly-smooth spine (collinear + tiny perturbations) doesn't
        false-positive. Each per-point segment-to-segment angle stays
        well under the 5° threshold."""
        import warnings as _w
        # Nearly-straight spine: linear in X, tiny Z bow. Per-point angles
        # are around 1-2°, comfortably below the threshold.
        spine = [(t, 0, (t / 100) ** 2 * 5) for t in [0, 10, 20, 30, 40, 50]]
        radii = [1.5] * len(spine)
        with _w.catch_warnings(record=True) as captured:
            _w.simplefilter("always")
            tapered_sweep(spine, radii)
        kink_msgs = [str(w.message) for w in captured
                     if "tangent discontinuity" in str(w.message)]
        assert kink_msgs == [], f"expected 0 warnings on smooth spine, got: {kink_msgs}"


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


class TestCopyShape:
    def test_copy_has_independent_topology_and_same_geometry(self):
        source = cq.Workplane("XY").box(10, 20, 30).val().wrapped
        copied = copy_shape(source)

        assert not source.IsPartner(copied)
        assert _volume(copied) == pytest.approx(_volume(source))
        assert _bounding_box(copied) == pytest.approx(_bounding_box(source))

        source_face = TopExp_Explorer(source, TopAbs_FACE).Current()
        copied_face = TopExp_Explorer(copied, TopAbs_FACE).Current()
        assert not source_face.IsPartner(copied_face)

    def test_copy_accepts_wrapped_cadquery_shape(self):
        source = cq.Workplane("XY").box(10, 10, 10).val()
        copied = copy_shape(source)

        assert not source.wrapped.IsPartner(copied)
        assert _volume(copied) == pytest.approx(1000.0)

    def test_copy_rejects_non_shape(self):
        with pytest.raises(TypeError, match="TopoDS_Shape"):
            copy_shape("not a shape")

    def test_copy_rejects_null_shape(self):
        with pytest.raises(ValueError, match="null shape"):
            copy_shape(TopoDS_Shape())

    def test_copy_rebuilds_empty_compound_independently(self):
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound, TopoDS_Iterator

        source = TopoDS_Compound()
        BRep_Builder().MakeCompound(source)

        copied = copy_shape(source)

        assert not source.IsPartner(copied)
        assert not TopoDS_Iterator(copied).More()


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
        assert not box.IsPartner(moved)
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
        assert not box.IsPartner(rotated)
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

    def test_not_inflated_by_nurbs_poles(self):
        """A B-spline/NURBS body must report its trimmed extents, not the
        control-point poles. Under the old Add_s, the max corner of this
        radius-180 disk floated out past ~198 in X/Y, mis-seating place_at."""
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Vec
        from OCP.Geom import Geom_Circle
        from OCP.GeomConvert import GeomConvert
        from OCP.BRepBuilderAPI import (
            BRepBuilderAPI_MakeEdge,
            BRepBuilderAPI_MakeFace,
            BRepBuilderAPI_MakeWire,
        )
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism

        bspline = GeomConvert.CurveToBSplineCurve_s(
            Geom_Circle(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 180.0)
        )
        edge = BRepBuilderAPI_MakeEdge(bspline).Edge()
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        face = BRepBuilderAPI_MakeFace(wire, True).Face()
        solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, 134.0)).Shape()

        x_max, y_max, z_max = bbox_point(solid, "max", "max", "max")
        assert x_max == pytest.approx(180.0, abs=0.5)
        assert y_max == pytest.approx(180.0, abs=0.5)
        assert z_max == pytest.approx(134.0, abs=0.5)


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


# ── annular boss tests ────────────────────────────────────────


class TestAnnularBoss:
    def test_annular_boss_dimensions_and_volume_from_diameters(self):
        result = annular_boss(
            center=(-105, -30),
            inner_diameter=74,
            outer_diameter=134.2,
            height=10,
            z_min=5,
        )
        bb = _bounding_box(result)
        assert abs(bb[0] - (-105 - 67.1)) < 0.01
        assert abs(bb[3] - (-105 + 67.1)) < 0.01
        assert abs(bb[1] - (-30 - 67.1)) < 0.01
        assert abs(bb[4] - (-30 + 67.1)) < 0.01
        assert abs(bb[2] - 5) < 0.01
        assert abs(bb[5] - 15) < 0.01

        expected = math.pi * (67.1 ** 2 - 37 ** 2) * 10
        assert abs(_volume(result) - expected) / expected < 0.001

    def test_raise_annulus_returns_compound_with_source_by_default(self):
        base = cq.Workplane("XY").box(20, 20, 4).val().wrapped
        result = raise_annulus(
            base,
            center=(0, 0),
            inner_diameter=4,
            outer_diameter=10,
            height=3,
            z=2,
        )
        solids = TopExp_Explorer(result, TopAbs_SOLID)
        count = 0
        while solids.More():
            count += 1
            solids.Next()

        assert count == 2
        expected_added = math.pi * (5 ** 2 - 2 ** 2) * 3
        assert abs(_volume(result) - (20 * 20 * 4 + expected_added)) < 0.1

    def test_annular_boss_rejects_invalid_dimensions(self):
        with pytest.raises(ValueError, match="outer radius"):
            annular_boss(
                center=(0, 0),
                inner_diameter=10,
                outer_diameter=10,
                height=1,
            )

    def test_annular_boss_accepts_build123d_axis_z(self):
        from build123d import Axis

        result = annular_boss(
            center=(0, 0),
            inner_diameter=4,
            outer_diameter=10,
            height=2,
            z_min=1,
            axis=Axis.Z,
        )
        bb = _bounding_box(result)
        assert abs(bb[2] - 1) < 0.01
        assert abs(bb[5] - 3) < 0.01


# ── ellipse_wire tests ────────────────────────────────────────


class TestEllipseWire:
    def test_ellipse_wire_produces_wire(self):
        wire = ellipse_wire(10, 5)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)  # should not raise

    def test_ellipse_wire_at_center_and_normal(self):
        wire = ellipse_wire(10, 5, center=(20, 30, 40), normal=(0, 1, 0))
        bb = _bounding_box(wire)
        # Ellipse at (20,30,40) with normal=(0,1,0): lies in XZ plane at y=30
        assert abs(bb[1] - 30) < 0.01  # ymin
        assert abs(bb[4] - 30) < 0.01  # ymax

    def test_ellipse_wire_swaps_when_y_larger(self):
        """y_radius > x_radius should auto-swap and still produce valid wire."""
        wire = ellipse_wire(5, 10)
        assert not wire.IsNull()
        bb = _bounding_box(wire)
        # After swap, x extent should be 5*2=10, y extent should be 10*2=20
        x_span = bb[3] - bb[0]
        y_span = bb[4] - bb[1]
        assert abs(x_span - 10) < 0.1
        assert abs(y_span - 20) < 0.1

    def test_ellipse_wire_circle_when_equal(self):
        wire = ellipse_wire(10, 10)
        assert not wire.IsNull()
        bb = _bounding_box(wire)
        x_span = bb[3] - bb[0]
        y_span = bb[4] - bb[1]
        assert abs(x_span - 20) < 0.1
        assert abs(y_span - 20) < 0.1


# ── spline_wire tests ────────────────────────────────────────


class TestSplineWire:
    def test_spline_wire_closed(self):
        # Circle-like points in XY plane
        pts = [(10, 0, 0), (0, 10, 0), (-10, 0, 0), (0, -10, 0)]
        wire = spline_wire(pts, closed=True)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)  # should not raise

    def test_spline_wire_open(self):
        pts = [(0, 0, 0), (5, 10, 0), (10, 0, 0)]
        wire = spline_wire(pts, closed=False)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)

    def test_spline_wire_too_few_points(self):
        with pytest.raises(ValueError):
            spline_wire([(0, 0, 0), (1, 1, 1)])

    def test_spline_wire_near_duplicate_points(self):
        """Near-duplicate points at segment boundaries should be collapsed."""
        pts = [
            (10, 0, 0),
            (0, 10, 0),
            (0, 10, 1e-8),  # near-duplicate of previous
            (-10, 0, 0),
            (0, -10, 0),
        ]
        wire = spline_wire(pts, closed=True)
        assert not wire.IsNull()

    def test_spline_wire_all_duplicates_raises(self):
        """If dedup leaves fewer than 3 points, should still raise."""
        pts = [(1, 0, 0), (1, 0, 1e-9), (1, 0, 2e-9)]
        with pytest.raises(ValueError):
            spline_wire(pts)


# ── polygon_wire tests ───────────────────────────────────────


class TestPolygonWire:
    def test_polygon_wire_closed_triangle(self):
        pts = [(0, 0, 0), (10, 0, 0), (5, 10, 0)]
        wire = polygon_wire(pts, closed=True)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)

    def test_polygon_wire_open(self):
        pts = [(0, 0, 0), (10, 0, 0), (10, 10, 0)]
        wire = polygon_wire(pts, closed=False)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)

    def test_polygon_wire_too_few_points(self):
        with pytest.raises(ValueError):
            polygon_wire([(0, 0, 0), (1, 1, 1)])

    def test_polygon_wire_near_duplicate_points(self):
        """Near-duplicate consecutive points should be collapsed."""
        pts = [
            (0, 0, 0),
            (10, 0, 0),
            (10, 0, 1e-8),  # near-duplicate of previous
            (5, 10, 0),
        ]
        wire = polygon_wire(pts, closed=True)
        assert not wire.IsNull()

    def test_polygon_wire_all_duplicates_raises(self):
        """If dedup leaves fewer than 3 points, should still raise."""
        pts = [(5, 5, 0), (5, 5, 1e-9), (5, 5, 2e-9)]
        with pytest.raises(ValueError):
            polygon_wire(pts)


# ── rounded_rect_wire tests ──────────────────────────────────


class TestRoundedRectWire:
    def test_rounded_rect_wire_produces_wire(self):
        wire = rounded_rect_wire(20, 10, 2)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)

    def test_rounded_rect_wire_zero_fillet(self):
        wire = rounded_rect_wire(20, 10, 0)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)

    def test_rounded_rect_wire_at_center_and_normal(self):
        wire = rounded_rect_wire(20, 10, 2, center=(50, 50, 0), normal=(0, 0, 1))
        bb = _bounding_box(wire)
        x_center = (bb[0] + bb[3]) / 2
        y_center = (bb[1] + bb[4]) / 2
        assert abs(x_center - 50) < 0.1
        assert abs(y_center - 50) < 0.1


# ── elliptical_sweep tests ───────────────────────────────────


class TestEllipticalSweep:
    def test_elliptical_sweep_produces_solid(self):
        spine = [(0, 0, 0), (0, 0, 5), (0, 0, 10)]
        x_radii = [5, 7, 5]
        y_radii = [3, 4, 3]
        result = elliptical_sweep(spine, x_radii, y_radii)
        assert result.ShapeType() == TopoDS.Solid_s(result).ShapeType()

    def test_elliptical_sweep_matches_tapered_when_equal(self):
        """When x == y at all points, should approximate tapered_sweep volume."""
        spine = [(0, 0, 0), (0, 0, 5), (0, 0, 10), (0, 0, 15), (0, 0, 20)]
        radii = [5, 7, 10, 7, 5]
        tapered = tapered_sweep(spine, radii)
        elliptical = elliptical_sweep(spine, radii, radii)
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        props_t, props_e = GProp_GProps(), GProp_GProps()
        BRepGProp.VolumeProperties_s(tapered, props_t)
        BRepGProp.VolumeProperties_s(elliptical, props_e)
        # Should be within 1% of each other
        assert abs(props_t.Mass() - props_e.Mass()) / props_t.Mass() < 0.01

    def test_elliptical_sweep_elliptical_bbox(self):
        """x_radii > y_radii should produce wider-than-deep bounding box."""
        spine = [(0, 0, 0), (0, 0, 10)]
        x_radii = [10, 10]
        y_radii = [3, 3]
        result = elliptical_sweep(spine, x_radii, y_radii)
        bb = _bounding_box(result)
        x_span = bb[3] - bb[0]
        y_span = bb[4] - bb[1]
        assert x_span > y_span * 2

    def test_elliptical_sweep_mismatched_lengths(self):
        with pytest.raises(ValueError):
            elliptical_sweep([(0, 0, 0), (0, 0, 10)], [5, 5], [3])

    def test_elliptical_sweep_minimum_two_points(self):
        with pytest.raises(ValueError):
            elliptical_sweep([(0, 0, 0)], [5], [3])


# ── involute_gear_profile tests ─────────────────────────────


class TestInvoluteGearProfile:
    def test_produces_wire(self):
        wire = involute_gear_profile(module=2, teeth=20)
        assert not wire.IsNull()
        TopoDS.Wire_s(wire)  # downcast should not raise

    def test_is_closed(self):
        wire = involute_gear_profile(module=2, teeth=20)
        assert wire.Closed()

    def test_module_scaling(self):
        small = involute_gear_profile(module=1, teeth=20)
        large = involute_gear_profile(module=3, teeth=20)
        bb_s = _bounding_box(small)
        bb_l = _bounding_box(large)
        assert (bb_l[3] - bb_l[0]) > (bb_s[3] - bb_s[0])

    def test_tooth_count_affects_size(self):
        few = involute_gear_profile(module=2, teeth=10)
        many = involute_gear_profile(module=2, teeth=30)
        bb_f = _bounding_box(few)
        bb_m = _bounding_box(many)
        assert (bb_m[3] - bb_m[0]) > (bb_f[3] - bb_f[0])

    def test_pressure_angle_varies_profile(self):
        w1 = involute_gear_profile(module=2, teeth=20, pressure_angle=14.5)
        w2 = involute_gear_profile(module=2, teeth=20, pressure_angle=25.0)
        bb1 = _bounding_box(w1)
        bb2 = _bounding_box(w2)
        assert bb1 != bb2

    def test_bbox_matches_outer_diameter(self):
        m, z = 2, 20
        wire = involute_gear_profile(module=m, teeth=z)
        bb = _bounding_box(wire)
        expected_od = m * z + 2 * m  # pitch + 2*addendum
        actual_span = bb[3] - bb[0]
        assert abs(actual_span - expected_od) / expected_od < 0.05

    def test_minimum_teeth_error(self):
        with pytest.raises(ValueError, match="teeth must be >= 6"):
            involute_gear_profile(module=2, teeth=4)

    def test_can_extrude_to_solid(self):
        """Usability: wire should be extrudable into a gear solid."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        wire = involute_gear_profile(module=2, teeth=20)
        face = BRepBuilderAPI_MakeFace(wire).Face()
        solid = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, 10)).Shape()
        assert not solid.IsNull()
