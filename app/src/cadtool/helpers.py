# cadtool.helpers — Organic geometry primitives for agent scripts

import math

import warnings

from OCP.Bnd import Bnd_Box
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.gp import gp_Ax1, gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec


def loft_sections(sections, smooth=True):
    """Loft through a list of TopoDS_Wire sections to produce a solid.

    Args:
        sections: List of TopoDS_Wire (minimum 2).
        smooth: If True, smooth interpolation; if False, ruled (linear).

    Returns:
        TopoDS_Solid
    """
    if len(sections) < 2:
        raise ValueError("loft_sections requires at least 2 sections")

    loft = BRepOffsetAPI_ThruSections(True, not smooth)
    for wire in sections:
        loft.AddWire(TopoDS.Wire_s(wire))
    loft.Build()

    if not loft.IsDone():
        raise ValueError("Loft operation failed")

    return loft.Shape()


def tapered_sweep(spine, radii):
    """Loft circular sections along a spine with varying radii.

    Args:
        spine: List of (x, y, z) tuples defining the sweep path (minimum 2).
        radii: List of floats, one radius per spine point.

    Returns:
        TopoDS_Solid
    """
    if len(spine) != len(radii):
        raise ValueError("spine and radii must have the same length")
    if len(spine) < 2:
        raise ValueError("tapered_sweep requires at least 2 spine points")

    pts = [gp_Pnt(*p) for p in spine]
    n = len(pts)
    wires = []

    for i in range(n):
        # Compute local tangent from adjacent points
        if i == 0:
            tangent = gp_Vec(pts[0], pts[1])
        elif i == n - 1:
            tangent = gp_Vec(pts[n - 2], pts[n - 1])
        else:
            tangent = gp_Vec(pts[i - 1], pts[i + 1])

        tangent.Normalize()
        direction = gp_Dir(tangent)
        axis = gp_Ax2(pts[i], direction)
        circ = gp_Circ(axis, radii[i])
        edge = BRepBuilderAPI_MakeEdge(circ).Edge()
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        wires.append(wire)

    return loft_sections(wires)


def naca_wire(y, le_x, te_x, thickness, profile="0012"):
    """Generate a NACA 4-digit airfoil wire at a given Y position.

    Args:
        y: Y position of the airfoil cross-section.
        le_x: X coordinate of the leading edge.
        te_x: X coordinate of the trailing edge.
        thickness: Max thickness as percentage of chord (e.g. 12 for 12%).
        profile: NACA 4-digit designation (e.g. "0012").

    Returns:
        TopoDS_Wire (closed)
    """
    chord = te_x - le_x
    t = thickness / 100.0
    n_pts = 40

    # NACA 4-digit symmetric thickness distribution (closed TE variant)
    def half_thickness(xc):
        return (t / 0.2) * chord * (
            0.2969 * math.sqrt(xc)
            - 0.1260 * xc
            - 0.3516 * xc ** 2
            + 0.2843 * xc ** 3
            - 0.1036 * xc ** 4
        )

    # Generate upper and lower surface points (LE → TE)
    upper_pts = []
    lower_pts = []
    for i in range(n_pts + 1):
        # Cosine spacing for better LE resolution
        beta = math.pi * i / n_pts
        xc = 0.5 * (1.0 - math.cos(beta))  # 0 → 1
        ht = half_thickness(xc)
        x = le_x + xc * chord
        upper_pts.append(gp_Pnt(x, y, ht))
        lower_pts.append(gp_Pnt(x, y, -ht))

    # Force LE and TE to exactly z=0 for clean closure
    le_pt = gp_Pnt(le_x, y, 0.0)
    te_pt = gp_Pnt(te_x, y, 0.0)
    upper_pts[0] = le_pt
    upper_pts[-1] = te_pt
    lower_pts[0] = le_pt
    lower_pts[-1] = te_pt

    # Build upper spline (LE to TE)
    upper_arr = TColgp_Array1OfPnt(1, len(upper_pts))
    for i, pt in enumerate(upper_pts):
        upper_arr.SetValue(i + 1, pt)
    upper_spline = GeomAPI_PointsToBSpline(upper_arr).Curve()
    upper_edge = BRepBuilderAPI_MakeEdge(upper_spline).Edge()

    # Build lower spline (TE to LE) — reversed so the wire closes
    lower_pts.reverse()
    lower_arr = TColgp_Array1OfPnt(1, len(lower_pts))
    for i, pt in enumerate(lower_pts):
        lower_arr.SetValue(i + 1, pt)
    lower_spline = GeomAPI_PointsToBSpline(lower_arr).Curve()
    lower_edge = BRepBuilderAPI_MakeEdge(lower_spline).Edge()

    # Combine into a closed wire
    builder = BRepBuilderAPI_MakeWire()
    builder.Add(upper_edge)
    builder.Add(lower_edge)
    wire = builder.Wire()
    return wire


def mirror_fuse(shape, plane="XZ"):
    """Mirror a shape about a coordinate plane and fuse with the original.

    Args:
        shape: TopoDS_Shape to mirror.
        plane: "XZ", "YZ", or "XY".

    Returns:
        TopoDS_Shape (fused solid, or compound as fallback)
    """
    planes = {
        "XZ": gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)),  # normal = Y
        "YZ": gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)),  # normal = X
        "XY": gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)),  # normal = Z
    }

    if plane not in planes:
        raise ValueError(
            f"Invalid plane '{plane}'. Must be one of: {', '.join(sorted(planes))}"
        )

    trsf = gp_Trsf()
    trsf.SetMirror(planes[plane])
    mirrored = BRepBuilderAPI_Transform(shape, trsf, True).Shape()

    try:
        fuse = BRepAlgoAPI_Fuse(shape, mirrored)
        if fuse.IsDone():
            result = fuse.Shape()
            # Unwrap compound if it contains a single solid
            explorer = TopExp_Explorer(result, TopAbs_SOLID)
            if explorer.More():
                solid = TopoDS.Solid_s(explorer.Current())
                explorer.Next()
                if not explorer.More():
                    return solid
            return result
    except Exception:
        pass

    warnings.warn("Boolean fuse failed, returning compound instead")
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    builder.Add(compound, shape)
    builder.Add(compound, mirrored)
    return compound


def translate(shape, x, y, z):
    """Translate a shape by (x, y, z).

    Args:
        shape: TopoDS_Shape to translate.
        x, y, z: Translation distances.

    Returns:
        TopoDS_Shape at the new position.
    """
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(x, y, z))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def rotate(shape, axis, angle_deg):
    """Rotate a shape around a coordinate axis through the origin.

    Follows right-hand rule: positive angle = counterclockwise when
    looking from positive axis toward origin.
    E.g. positive Y rotation moves +Z toward +X.

    Args:
        shape: TopoDS_Shape to rotate.
        axis: "X", "Y", or "Z".
        angle_deg: Rotation angle in degrees.

    Returns:
        TopoDS_Shape at the new orientation.
    """
    axes = {
        "X": gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)),
        "Y": gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)),
        "Z": gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)),
    }
    if axis not in axes:
        raise ValueError(f"axis must be 'X', 'Y', or 'Z', got '{axis}'")
    trsf = gp_Trsf()
    trsf.SetRotation(axes[axis], math.radians(angle_deg))
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def bbox_point(shape, x="center", y="center", z="center"):
    """Query a point on a shape's bounding box.

    Each axis takes "min", "center", or "max".

    Args:
        shape: TopoDS_Shape.
        x, y, z: One of "min", "center", "max".

    Returns:
        Tuple (x, y, z) of floats.
    """
    valid = ("min", "center", "max")
    for name, val in [("x", x), ("y", y), ("z", z)]:
        if val not in valid:
            raise ValueError(
                f"Invalid value '{val}' for {name}. Must be one of: {', '.join(valid)}"
            )

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()

    def _pick(lo, hi, spec):
        if spec == "min":
            return lo
        elif spec == "max":
            return hi
        return (lo + hi) / 2.0

    return (_pick(xmin, xmax, x), _pick(ymin, ymax, y), _pick(zmin, zmax, z))


def place_at(shape, from_pt, to_pt):
    """Translate shape so from_pt moves to to_pt.

    Args:
        shape: TopoDS_Shape.
        from_pt: (x, y, z) source point.
        to_pt: (x, y, z) target point.

    Returns:
        TopoDS_Shape at the new position.
    """
    return translate(
        shape,
        to_pt[0] - from_pt[0],
        to_pt[1] - from_pt[1],
        to_pt[2] - from_pt[2],
    )


def assemble(*shapes):
    """Combine TopoDS_Shape objects into a compound ready for show_object().

    Eliminates the cq.Shape.cast / makeCompound / newObject ceremony.

    Args:
        shapes: One or more TopoDS_Shape objects.

    Returns:
        cq.Workplane containing the compound.
    """
    import cadquery as cq

    wrapped = [cq.Shape.cast(s) for s in shapes]
    compound = cq.Compound.makeCompound(wrapped)
    return cq.Workplane("XY").newObject([compound])
