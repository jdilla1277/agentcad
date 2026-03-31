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
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Elips, gp_Pnt, gp_Trsf, gp_Vec


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


def involute_gear_profile(module, teeth, pressure_angle=20.0):
    """Generate a closed involute spur gear profile wire in the XY plane.

    Args:
        module: Gear module (pitch diameter / teeth). Controls tooth size.
        teeth: Number of teeth (minimum 6).
        pressure_angle: Pressure angle in degrees (default 20.0).

    Returns:
        TopoDS_Wire (closed) — full gear profile centered at origin in XY plane.
    """
    if teeth < 6:
        raise ValueError("teeth must be >= 6")

    m = float(module)
    z = int(teeth)
    alpha = math.radians(pressure_angle)

    # Derived radii
    r_pitch = m * z / 2.0
    r_base = r_pitch * math.cos(alpha)
    r_tip = r_pitch + m
    r_root = r_pitch - 1.25 * m

    # Involute curve: parametric from base circle
    # x(t) = r_base * (cos(t) + t*sin(t))
    # y(t) = r_base * (sin(t) - t*cos(t))
    # Find t_max where the involute reaches r_tip
    # r(t) = r_base * sqrt(1 + t^2), so t_max = sqrt((r_tip/r_base)^2 - 1)
    t_max = math.sqrt((r_tip / r_base) ** 2 - 1)

    n_inv = 20  # points per involute curve

    def involute_pts(t_max, n_pts):
        """Sample involute curve starting at base circle."""
        pts = []
        for i in range(n_pts + 1):
            t = t_max * i / n_pts
            x = r_base * (math.cos(t) + t * math.sin(t))
            y = r_base * (math.sin(t) - t * math.cos(t))
            pts.append((x, y))
        return pts

    # Generate one involute flank (right side of tooth)
    inv_pts = involute_pts(t_max, n_inv)

    # Angular tooth thickness at pitch circle
    # Tooth thickness at pitch = pi*m/2
    # Involute angle at pitch: inv(alpha) = tan(alpha) - alpha
    inv_alpha = math.tan(alpha) - alpha
    # Angular half-tooth thickness at pitch circle
    half_tooth_angle = math.pi / (2 * z) + inv_alpha

    # The involute starts at angle 0 on the base circle.
    # At the pitch circle, the involute point is at angle inv_alpha from base.
    # We need to rotate the involute so the tooth is centered.

    # Build right flank: rotate involute by +half_tooth_angle
    # Build left flank: mirror about tooth centerline (reflect y), rotate by -half_tooth_angle
    # Actually: mirror the involute (negate y), giving the left flank

    def rotate_pt(x, y, angle):
        c, s = math.cos(angle), math.sin(angle)
        return (c * x - s * y, s * x + c * y)

    builder = BRepBuilderAPI_MakeWire()
    tooth_angle = 2 * math.pi / z

    for i in range(z):
        base_angle = i * tooth_angle

        # Right involute flank (rotated by half_tooth_angle + base_angle)
        right_pts = []
        for px, py in inv_pts:
            rx, ry = rotate_pt(px, py, half_tooth_angle + base_angle)
            right_pts.append(gp_Pnt(rx, ry, 0))

        # Left involute flank (mirror y then rotate by -half_tooth_angle + base_angle)
        left_pts = []
        for px, py in inv_pts:
            mx, my = rotate_pt(px, -py, -half_tooth_angle + base_angle)
            left_pts.append(gp_Pnt(mx, my, 0))

        # Root arc: from end of previous left flank to start of this right flank
        # The root circle point at the start of right involute
        right_start = right_pts[0]
        left_start = left_pts[0]

        # If base circle > root circle, we need a radial line down to root,
        # then a root arc, then a radial line back up.
        # If base circle <= root circle, involute starts at/above root.
        if r_base > r_root:
            # Radial segment from root to base at right flank start angle
            right_angle = math.atan2(right_start.Y(), right_start.X())
            root_right = gp_Pnt(r_root * math.cos(right_angle), r_root * math.sin(right_angle), 0)

            # Previous tooth's left flank end (at root)
            prev_left_end_angle = math.atan2(left_start.Y(), left_start.X())
            # The previous tooth's left involute base point
            if i == 0:
                # For the first tooth, the previous is the last tooth
                prev_base_angle = (z - 1) * tooth_angle
                prev_left_pts_start = inv_pts[0]
                pmx, pmy = rotate_pt(prev_left_pts_start[0], -prev_left_pts_start[1], -half_tooth_angle + prev_base_angle)
                prev_left_base = gp_Pnt(pmx, pmy, 0)
            else:
                prev_base_angle = (i - 1) * tooth_angle
                prev_left_pts_start = inv_pts[0]
                pmx, pmy = rotate_pt(prev_left_pts_start[0], -prev_left_pts_start[1], -half_tooth_angle + prev_base_angle)
                prev_left_base = gp_Pnt(pmx, pmy, 0)

            prev_left_angle = math.atan2(prev_left_base.Y(), prev_left_base.X())
            root_prev_left = gp_Pnt(r_root * math.cos(prev_left_angle), r_root * math.sin(prev_left_angle), 0)

            # Radial line: previous left base → root
            edge_down = BRepBuilderAPI_MakeEdge(prev_left_base, root_prev_left).Edge()
            builder.Add(edge_down)

            # Root arc from prev_left_root to right_root
            mid_angle = (prev_left_angle + right_angle) / 2
            # Handle angle wrapping for first tooth
            if i == 0 and prev_left_angle > right_angle:
                mid_angle = (prev_left_angle + right_angle + 2 * math.pi) / 2
            root_mid = gp_Pnt(r_root * math.cos(mid_angle), r_root * math.sin(mid_angle), 0)
            arc = GC_MakeArcOfCircle(root_prev_left, root_mid, root_right)
            edge_root = BRepBuilderAPI_MakeEdge(arc.Value()).Edge()
            builder.Add(edge_root)

            # Radial line: root → right base
            edge_up = BRepBuilderAPI_MakeEdge(root_right, right_start).Edge()
            builder.Add(edge_up)
        else:
            # Base circle is inside root circle — involute starts above root
            # Just connect previous left to this right with a root arc
            if i == 0:
                prev_base_angle = (z - 1) * tooth_angle
            else:
                prev_base_angle = (i - 1) * tooth_angle
            prev_left_pts_start = inv_pts[0]
            pmx, pmy = rotate_pt(prev_left_pts_start[0], -prev_left_pts_start[1], -half_tooth_angle + prev_base_angle)
            prev_left_base = gp_Pnt(pmx, pmy, 0)

            mid_angle = (math.atan2(prev_left_base.Y(), prev_left_base.X()) + math.atan2(right_start.Y(), right_start.X())) / 2
            if i == 0 and math.atan2(prev_left_base.Y(), prev_left_base.X()) > math.atan2(right_start.Y(), right_start.X()):
                mid_angle = (math.atan2(prev_left_base.Y(), prev_left_base.X()) + math.atan2(right_start.Y(), right_start.X()) + 2 * math.pi) / 2
            root_mid = gp_Pnt(r_root * math.cos(mid_angle), r_root * math.sin(mid_angle), 0)
            arc = GC_MakeArcOfCircle(prev_left_base, root_mid, right_start)
            edge_root = BRepBuilderAPI_MakeEdge(arc.Value()).Edge()
            builder.Add(edge_root)

        # Right involute flank (spline, base→tip)
        arr_r = TColgp_Array1OfPnt(1, len(right_pts))
        for j, pt in enumerate(right_pts):
            arr_r.SetValue(j + 1, pt)
        curve_r = GeomAPI_PointsToBSpline(arr_r).Curve()
        edge_r = BRepBuilderAPI_MakeEdge(curve_r).Edge()
        builder.Add(edge_r)

        # Tip arc: from right tip to left tip
        right_tip = right_pts[-1]
        left_tip = left_pts[-1]
        tip_mid_angle = (math.atan2(right_tip.Y(), right_tip.X()) + math.atan2(left_tip.Y(), left_tip.X())) / 2
        tip_mid = gp_Pnt(r_tip * math.cos(tip_mid_angle), r_tip * math.sin(tip_mid_angle), 0)
        arc_tip = GC_MakeArcOfCircle(right_tip, tip_mid, left_tip)
        edge_tip = BRepBuilderAPI_MakeEdge(arc_tip.Value()).Edge()
        builder.Add(edge_tip)

        # Left involute flank (spline, tip→base — reversed)
        left_pts_rev = list(reversed(left_pts))
        arr_l = TColgp_Array1OfPnt(1, len(left_pts_rev))
        for j, pt in enumerate(left_pts_rev):
            arr_l.SetValue(j + 1, pt)
        curve_l = GeomAPI_PointsToBSpline(arr_l).Curve()
        edge_l = BRepBuilderAPI_MakeEdge(curve_l).Edge()
        builder.Add(edge_l)

    wire = builder.Wire()
    return wire


def ellipse_wire(x_radius, y_radius, center=(0, 0, 0), normal=(0, 0, 1)):
    """Create an elliptical wire.

    Args:
        x_radius: Radius along the local X axis.
        y_radius: Radius along the local Y axis.
        center: (x, y, z) center point.
        normal: (x, y, z) normal vector.

    Returns:
        TopoDS_Wire
    """
    if x_radius <= 0 or y_radius <= 0:
        raise ValueError("Both radii must be positive")

    ax = gp_Ax2(gp_Pnt(*center), gp_Dir(*normal))

    # gp_Elips requires major >= minor; swap and rotate if needed
    if x_radius >= y_radius:
        elips = gp_Elips(ax, float(x_radius), float(y_radius))
    else:
        # Rotate the X-axis 90° so the visual orientation stays correct
        ax.Rotate(gp_Ax1(gp_Pnt(*center), gp_Dir(*normal)), math.pi / 2)
        elips = gp_Elips(ax, float(y_radius), float(x_radius))

    edge = BRepBuilderAPI_MakeEdge(elips).Edge()
    return BRepBuilderAPI_MakeWire(edge).Wire()


def spline_wire(points, closed=True):
    """Create a smooth spline wire through 3D points.

    Args:
        points: List of (x, y, z) tuples (minimum 3).
        closed: If True, close the wire.

    Returns:
        TopoDS_Wire
    """
    if len(points) < 3:
        raise ValueError("spline_wire requires at least 3 points")

    pts = [gp_Pnt(*p) for p in points]
    if closed:
        pts.append(pts[0])

    arr = TColgp_Array1OfPnt(1, len(pts))
    for i, p in enumerate(pts):
        arr.SetValue(i + 1, p)

    curve = GeomAPI_PointsToBSpline(arr).Curve()
    edge = BRepBuilderAPI_MakeEdge(curve).Edge()
    return BRepBuilderAPI_MakeWire(edge).Wire()


def polygon_wire(points, closed=True):
    """Create a wire from straight line segments between points.

    Args:
        points: List of (x, y, z) tuples (minimum 3).
        closed: If True, close the wire.

    Returns:
        TopoDS_Wire
    """
    if len(points) < 3:
        raise ValueError("polygon_wire requires at least 3 points")

    pts = [gp_Pnt(*p) for p in points]
    builder = BRepBuilderAPI_MakeWire()
    for i in range(len(pts) - 1):
        edge = BRepBuilderAPI_MakeEdge(pts[i], pts[i + 1]).Edge()
        builder.Add(edge)
    if closed:
        edge = BRepBuilderAPI_MakeEdge(pts[-1], pts[0]).Edge()
        builder.Add(edge)
    return builder.Wire()


def rounded_rect_wire(width, height, fillet_radius, center=(0, 0, 0), normal=(0, 0, 1)):
    """Create a rectangle wire with rounded corners.

    Args:
        width: Rectangle width.
        height: Rectangle height.
        fillet_radius: Corner fillet radius (0 for sharp corners).
        center: (x, y, z) center point.
        normal: (x, y, z) normal vector.

    Returns:
        TopoDS_Wire
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    if fillet_radius < 0:
        raise ValueError("fillet_radius must be non-negative")
    if fillet_radius > min(width, height) / 2:
        raise ValueError("fillet_radius must be <= min(width, height) / 2")

    hw = width / 2.0
    hh = height / 2.0
    r = float(fillet_radius)

    # Build in local XY, then transform to center/normal
    # Corner order: bottom-right, top-right, top-left, bottom-left
    # Each corner has a fillet start and end point
    builder = BRepBuilderAPI_MakeWire()

    if r == 0:
        # Simple rectangle — 4 line edges
        corners = [
            gp_Pnt(hw, -hh, 0), gp_Pnt(hw, hh, 0),
            gp_Pnt(-hw, hh, 0), gp_Pnt(-hw, -hh, 0),
        ]
        for i in range(4):
            edge = BRepBuilderAPI_MakeEdge(corners[i], corners[(i + 1) % 4]).Edge()
            builder.Add(edge)
    else:
        # 8 points where fillets meet straight edges, plus 4 arc centers
        # Right side: x = hw, fillet from (hw, -hh+r) to (hw, hh-r)
        # Top side: y = hh, fillet from (hw-r, hh) to (-hw+r, hh)
        # Left side: x = -hw, fillet from (-hw, hh-r) to (-hw, -hh+r)
        # Bottom side: y = -hh, fillet from (-hw+r, -hh) to (hw-r, -hh)

        # Fillet endpoints (going clockwise from bottom-right)
        p = [
            gp_Pnt(hw, -hh + r, 0),   # 0: right side bottom
            gp_Pnt(hw, hh - r, 0),    # 1: right side top
            gp_Pnt(hw - r, hh, 0),    # 2: top side right
            gp_Pnt(-hw + r, hh, 0),   # 3: top side left
            gp_Pnt(-hw, hh - r, 0),   # 4: left side top
            gp_Pnt(-hw, -hh + r, 0),  # 5: left side bottom
            gp_Pnt(-hw + r, -hh, 0),  # 6: bottom side left
            gp_Pnt(hw - r, -hh, 0),   # 7: bottom side right
        ]

        # Arc midpoints (on the circle at 45° from the straight edges)
        arc_mid = [
            gp_Pnt(hw - r + r * math.cos(math.pi / 4), hh - r + r * math.sin(math.pi / 4), 0),     # top-right
            gp_Pnt(-hw + r - r * math.cos(math.pi / 4), hh - r + r * math.sin(math.pi / 4), 0),    # top-left
            gp_Pnt(-hw + r - r * math.cos(math.pi / 4), -hh + r - r * math.sin(math.pi / 4), 0),   # bottom-left
            gp_Pnt(hw - r + r * math.cos(math.pi / 4), -hh + r - r * math.sin(math.pi / 4), 0),    # bottom-right
        ]

        # Build: right line, TR arc, top line, TL arc, left line, BL arc, bottom line, BR arc
        edges = [
            BRepBuilderAPI_MakeEdge(p[0], p[1]).Edge(),                                    # right
            BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p[1], arc_mid[0], p[2]).Value()).Edge(),  # TR arc
            BRepBuilderAPI_MakeEdge(p[2], p[3]).Edge(),                                    # top
            BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p[3], arc_mid[1], p[4]).Value()).Edge(),  # TL arc
            BRepBuilderAPI_MakeEdge(p[4], p[5]).Edge(),                                    # left
            BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p[5], arc_mid[2], p[6]).Value()).Edge(),  # BL arc
            BRepBuilderAPI_MakeEdge(p[6], p[7]).Edge(),                                    # bottom
            BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(p[7], arc_mid[3], p[0]).Value()).Edge(),  # BR arc
        ]
        for e in edges:
            builder.Add(e)

    wire = builder.Wire()

    # Transform to target center and normal
    cx, cy, cz = center
    if (cx, cy, cz) != (0, 0, 0) or tuple(normal) != (0, 0, 1):
        ax_target = gp_Ax3(gp_Pnt(*center), gp_Dir(*normal))
        ax_origin = gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1))
        trsf = gp_Trsf()
        trsf.SetTransformation(ax_target, ax_origin)
        wire = BRepBuilderAPI_Transform(wire, trsf, True).Shape()
        wire = TopoDS.Wire_s(wire)

    return wire


def elliptical_sweep(spine, x_radii, y_radii):
    """Loft elliptical sections along a spine with varying radii.

    Args:
        spine: List of (x, y, z) tuples defining the sweep path (minimum 2).
        x_radii: List of floats, one X radius per spine point.
        y_radii: List of floats, one Y radius per spine point.

    Returns:
        TopoDS_Solid
    """
    if len(spine) != len(x_radii) or len(spine) != len(y_radii):
        raise ValueError("spine, x_radii, and y_radii must have the same length")
    if len(spine) < 2:
        raise ValueError("elliptical_sweep requires at least 2 spine points")

    pts = [gp_Pnt(*p) for p in spine]
    n = len(pts)
    wires = []

    for i in range(n):
        # Compute local tangent from adjacent points (same as tapered_sweep)
        if i == 0:
            tangent = gp_Vec(pts[0], pts[1])
        elif i == n - 1:
            tangent = gp_Vec(pts[n - 2], pts[n - 1])
        else:
            tangent = gp_Vec(pts[i - 1], pts[i + 1])

        tangent.Normalize()
        direction = gp_Dir(tangent)
        ax = gp_Ax2(pts[i], direction)

        xr = float(x_radii[i])
        yr = float(y_radii[i])

        # gp_Elips requires major >= minor; swap and rotate if needed
        if xr >= yr:
            elips = gp_Elips(ax, xr, yr)
        else:
            ax.Rotate(gp_Ax1(pts[i], direction), math.pi / 2)
            elips = gp_Elips(ax, yr, xr)

        edge = BRepBuilderAPI_MakeEdge(elips).Edge()
        wire = BRepBuilderAPI_MakeWire(edge).Wire()
        wires.append(wire)

    return loft_sections(wires)
