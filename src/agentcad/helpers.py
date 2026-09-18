# agentcad.helpers — Organic geometry primitives for agent scripts

from io import BytesIO
import math
import sys
from numbers import Real
import warnings
from pathlib import Path

from OCP.Bnd import Bnd_Box
from OCP.BOPAlgo import (
    BOPAlgo_BOP,
    BOPAlgo_CellsBuilder,
    BOPAlgo_COMMON,
    BOPAlgo_CUT,
    BOPAlgo_FUSE,
)
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRep import BRep_Builder
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_Copy,
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.GProp import GProp_GProps
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TopAbs import (
    TopAbs_COMPOUND,
    TopAbs_COMPSOLID,
    TopAbs_EDGE,
    TopAbs_FACE,
    TopAbs_SHELL,
    TopAbs_SOLID,
    TopAbs_VERTEX,
    TopAbs_WIRE,
)
from OCP.TopExp import TopExp_Explorer
from OCP.TopTools import TopTools_ListOfShape
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Iterator, TopoDS_Shape
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


_TAPERED_SWEEP_KINK_THRESHOLD_DEG = 5.0


def tapered_sweep(spine, radii):
    """Loft circular sections along a spine with varying radii.

    Args:
        spine: List of (x, y, z) tuples defining the sweep path (minimum 2).
        radii: List of floats, one radius per spine point.

    Returns:
        TopoDS_Solid

    Warnings (via :mod:`warnings`):
        Emits a ``UserWarning`` for each interior spine point whose incoming
        and outgoing segments differ by more than 5° — a tangent
        discontinuity ("kink"). At a kink the cross-section orientation can
        flip, producing self-intersecting / inside-out geometry that passes
        ``is_valid`` because each individual face is sound. Fix by sampling
        the spine more densely across the joint so consecutive segments
        align.
    """
    if len(spine) != len(radii):
        raise ValueError("spine and radii must have the same length")
    if len(spine) < 2:
        raise ValueError("tapered_sweep requires at least 2 spine points")

    pts = [gp_Pnt(*p) for p in spine]
    n = len(pts)

    # Detect kinks: at any interior point where the incoming and outgoing
    # segment directions differ by more than the threshold, the per-point
    # tangent (centered difference, below) bisects two unrelated directions
    # and the swept circle ends up tilted off-axis. Surface as a warning
    # rather than an error so the caller can ship if they know what they're
    # doing.
    threshold_rad = math.radians(_TAPERED_SWEEP_KINK_THRESHOLD_DEG)
    for i in range(1, n - 1):
        incoming = gp_Vec(pts[i - 1], pts[i])
        outgoing = gp_Vec(pts[i], pts[i + 1])
        if incoming.Magnitude() == 0 or outgoing.Magnitude() == 0:
            continue
        angle_rad = incoming.Angle(outgoing)
        if angle_rad > threshold_rad:
            warnings.warn(
                f"tapered_sweep: spine has tangent discontinuity at point "
                f"i={i} (~{math.degrees(angle_rad):.1f}° kink) — "
                f"cross-sections may flip, producing distorted geometry "
                f"even though is_valid may stay true. Sample the spine "
                f"more densely across the joint to align consecutive "
                f"segment directions.",
            )

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


# ---------------------------------------------------------------------------
# Abstraction-level preservation (issue #194)
#
# Edit pipelines mix three shape representations: raw OCCT ``TopoDS_Shape``
# values, build123d wrappers (``Part``, ``Solid``, ...), and CadQuery
# wrappers (``cq.Shape``, ``cq.Workplane``). Every shape-in/shape-out helper
# in this module computes on the raw topology and then hands the result back
# at the caller's level: a build123d input gets a build123d result, a CadQuery
# input gets a CadQuery result, and a raw input stays raw. That keeps
# ``load_step() -> translate -> safe_cut -> show_object`` one object model
# instead of silently switching to raw OCCT halfway through the script.
# ---------------------------------------------------------------------------


def _unwrap_shape(shape):
    """Return the raw ``TopoDS_Shape`` behind a raw, build123d, or CadQuery value.

    Returns ``None`` when ``shape`` carries no OCCT topology so callers can
    raise their own context-specific error.
    """
    if isinstance(shape, TopoDS_Shape):
        return shape
    topo = getattr(shape, "wrapped", None)
    if isinstance(topo, TopoDS_Shape):
        return topo
    val = getattr(shape, "val", None)  # CadQuery Workplane
    if callable(val):
        try:
            topo = getattr(val(), "wrapped", None)
        except Exception:
            topo = None
        if isinstance(topo, TopoDS_Shape):
            return topo
    return None


def _is_build123d_shape(obj):
    # Subclasses may live outside the build123d package (the runner's
    # compat primitives such as Box are agentcad classes), so test the
    # class hierarchy rather than the defining module.
    try:
        from build123d.topology import Shape
    except ImportError:
        return False
    return isinstance(obj, Shape)


def _cadquery_kind(obj):
    """Return ``"shape"``, ``"workplane"``, or ``None`` for a CadQuery value."""
    # A CadQuery object can only exist once cadquery has been imported, so
    # consult sys.modules instead of paying for an import on the default
    # build123d-only profile.
    cq = sys.modules.get("cadquery")
    if cq is None:
        return None
    if isinstance(obj, cq.Workplane):
        return "workplane"
    if isinstance(obj, cq.Shape):
        return "shape"
    return None


def _top_level_children(shape):
    children = []
    iterator = TopoDS_Iterator(shape)
    while iterator.More():
        children.append(iterator.Value())
        iterator.Next()
    return children


def _wrap_build123d(topo, template=None):
    """Wrap a raw ``TopoDS_Shape`` in the build123d class matching its topology.

    ``template`` is the caller's original input. When it is a compound-family
    wrapper (``Part``/``Compound``, which is what ``load_step`` and the
    primitives such as ``Box`` are) the result comes back as a ``Part``
    wrapping a compound, so ``.volume`` and ``.solids()`` stay measurable.
    Wrapping a bare ``TopoDS_Solid`` directly in ``Part(...)`` or
    ``Compound(...)`` yields a shape that reports zero volume and iterates
    over shells, which is the failure this helper exists to prevent.
    """
    from build123d import Compound, Edge, Face, Part, Shell, Solid, Vertex, Wire
    from build123d.topology import downcast

    shape = downcast(topo)
    kind = shape.ShapeType()
    compound_family = isinstance(template, Compound)
    if kind == TopAbs_COMPOUND:
        solids = _solid_members(shape)
        children = _top_level_children(shape)
        if (
            not compound_family
            and len(children) == 1
            and children[0].ShapeType() == TopAbs_SOLID
        ):
            return Solid(downcast(children[0]))
        return Part(shape) if solids else Compound(shape)
    if kind == TopAbs_SOLID:
        if compound_family:
            return Part(_compound_topods(shape))
        return Solid(shape)
    if kind == TopAbs_COMPSOLID:
        return Part(_compound_topods(shape))
    simple = {
        TopAbs_SHELL: Shell,
        TopAbs_FACE: Face,
        TopAbs_WIRE: Wire,
        TopAbs_EDGE: Edge,
        TopAbs_VERTEX: Vertex,
    }
    return simple[kind](shape)


def _rewrap_like(topo, template):
    """Return raw ``topo`` at ``template``'s abstraction level.

    Raw or unknown templates (including ``None`` and file paths) return the
    raw shape unchanged; build123d templates return build123d wrappers;
    CadQuery ``Shape`` templates return ``cq.Shape`` and ``Workplane``
    templates return a ``Workplane`` holding the result.
    """
    if template is None or isinstance(template, TopoDS_Shape):
        return topo
    if _is_build123d_shape(template):
        return _wrap_build123d(topo, template)
    kind = _cadquery_kind(template)
    if kind is not None:
        import cadquery as cq

        wrapped = cq.Shape.cast(topo)
        if kind == "workplane":
            return cq.Workplane("XY").newObject([wrapped])
        return wrapped
    return topo


def _require_shape(shape, label):
    topo = _unwrap_shape(shape)
    if topo is None:
        raise TypeError(
            f"{label} must be a TopoDS_Shape or a build123d/CadQuery shape "
            f"with wrapped topology, got {type(shape).__name__}"
        )
    if topo.IsNull():
        raise ValueError(f"{label} is a null shape")
    return topo


def mirror_fuse(shape, plane="XZ"):
    """Mirror a shape about a coordinate plane and fuse with the original.

    Args:
        shape: Raw TopoDS_Shape or build123d/CadQuery shape to mirror.
        plane: "XZ", "YZ", or "XY".

    Returns:
        The fused solid (or a compound as fallback) at the same abstraction
        level as ``shape``: raw in, raw out; build123d in, build123d out.
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

    topo = _require_shape(shape, "mirror_fuse shape")
    trsf = gp_Trsf()
    trsf.SetMirror(planes[plane])
    mirrored = BRepBuilderAPI_Transform(topo, trsf, True).Shape()

    try:
        fuse = BRepAlgoAPI_Fuse(topo, mirrored)
        if fuse.IsDone():
            result = fuse.Shape()
            # Unwrap compound if it contains a single solid
            explorer = TopExp_Explorer(result, TopAbs_SOLID)
            if explorer.More():
                solid = TopoDS.Solid_s(explorer.Current())
                explorer.Next()
                if not explorer.More():
                    return _rewrap_like(solid, shape)
            return _rewrap_like(result, shape)
    except Exception:
        pass

    warnings.warn("Boolean fuse failed, returning compound instead")
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    builder.Add(compound, topo)
    builder.Add(compound, mirrored)
    return _rewrap_like(compound, shape)


def copy_shape(shape):
    """Return a geometrically independent copy of an OCCT shape.

    Imported features must not share OCCT's internal topology record before
    one copy is transformed and compared with another. A shallow Python copy
    or a location-only transform can leave that record shared and make later
    Boolean results incorrect without raising an error.

    ``shape`` may be a raw ``TopoDS_Shape`` or an object with a ``wrapped``
    TopoDS shape (such as a build123d or CadQuery shape). The copy comes back
    at the same abstraction level as the input: a raw shape returns a raw
    ``TopoDS_Shape``, a build123d ``Part`` returns a build123d ``Part``.
    Geometry is copied; cached triangulation is intentionally not copied.
    """
    topo = _unwrap_shape(shape)
    if topo is None:
        raise TypeError(
            "copy_shape expects a TopoDS_Shape or an object with a wrapped "
            f"TopoDS_Shape, got {type(shape).__name__}"
        )
    if topo.IsNull():
        raise ValueError("copy_shape cannot copy a null shape")

    copier = BRepBuilderAPI_Copy(topo, True, False)
    if not copier.IsDone():
        raise ValueError("copy_shape could not create an independent geometry copy")
    copied = copier.Shape()
    if copied.IsNull():
        raise ValueError("copy_shape did not produce independent geometry")
    if topo.IsPartner(copied):
        # OCCT reuses the singleton topology record for an empty compound.
        # Rebuild that container explicitly; it has no child geometry to copy.
        children = TopoDS_Iterator(topo)
        if topo.ShapeType() == TopAbs_COMPOUND and not children.More():
            empty = TopoDS_Compound()
            BRep_Builder().MakeCompound(empty)
            return _rewrap_like(empty, shape)
        raise ValueError("copy_shape did not produce independent geometry")
    return _rewrap_like(copied, shape)


_SAFE_BOOLEAN_TOLERANCE_MM = 1e-7


def _shape_volume(shape):
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def _contains_solid(shape):
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    return explorer.More()


def _solid_members(shape):
    members = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        members.append(explorer.Current())
        explorer.Next()
    return members


def _is_empty_compound(shape):
    if shape.ShapeType() != TopAbs_COMPOUND:
        return False
    return not TopoDS_Iterator(shape).More()


def _canonical_occupied_shape(
    shape, label, tolerance=_SAFE_BOOLEAN_TOLERANCE_MM
):
    """Return geometry and volume with overlapping members counted once."""
    solids = _solid_members(shape)
    if len(solids) <= 1:
        return shape, _shape_volume(shape)

    partition = BOPAlgo_CellsBuilder()
    partition.SetNonDestructive(True)
    partition.SetFuzzyValue(tolerance)
    for solid in solids:
        partition.AddArgument(copy_shape(solid))
    try:
        partition.Perform()
    except Exception as exc:
        raise ValueError(
            f"{label} occupied-volume validation failed in the CAD kernel: {exc}"
        ) from exc
    if partition.HasErrors():
        details = "; ".join(_boolean_messages(partition, "DumpErrors"))
        suffix = f": {details}" if details else ""
        raise ValueError(f"{label} occupied-volume validation failed{suffix}")
    partition.RemoveAllFromResult()
    partition.AddAllToResult()
    if partition.HasErrors():
        details = "; ".join(_boolean_messages(partition, "DumpErrors"))
        suffix = f": {details}" if details else ""
        raise ValueError(f"{label} occupied-volume extraction failed{suffix}")
    result = partition.Shape()
    if result.IsNull() or not BRepCheck_Analyzer(result).IsValid():
        raise ValueError(f"{label} occupied-volume validation returned invalid geometry")
    result = copy_shape(result)
    return result, _shape_volume(result)


def _physical_volume(shape, label, tolerance=_SAFE_BOOLEAN_TOLERANCE_MM):
    """Measure occupied volume, counting overlapping compound members once."""
    return _canonical_occupied_shape(shape, label, tolerance)[1]


def _boolean_input(shape, label, tolerance):
    topo = _unwrap_shape(shape)
    if topo is None:
        raise TypeError(
            f"{label} must be a TopoDS_Shape or an object with a wrapped "
            f"TopoDS_Shape, got {type(shape).__name__}"
        )
    if topo.IsNull():
        raise ValueError(f"{label} is a null shape")
    if not BRepCheck_Analyzer(topo).IsValid():
        raise ValueError(f"{label} is invalid; repair it before a safe Boolean")
    occupied_shape, volume = _canonical_occupied_shape(topo, label, tolerance)
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError(
            f"{label} must contain valid positive-volume solid geometry; "
            f"measured volume was {volume}"
        )
    return occupied_shape, volume


def _boolean_tolerance(value):
    try:
        tolerance = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"tolerance must be a finite non-negative number, got {value}"
        ) from exc
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError(
            f"tolerance must be a finite non-negative number, got {value}"
        )
    return tolerance


def _shape_list(shapes):
    result = TopTools_ListOfShape()
    for shape in shapes:
        result.Append(shape)
    return result


def _boolean_messages(operation, method_name):
    output = BytesIO()
    getattr(operation, method_name)(output)
    return [
        line.strip()
        for line in output.getvalue().decode("utf-8", errors="replace").splitlines()
        if line.strip()
    ]


def _volume_slack(*volumes):
    """Allow tiny kernel noise while still rejecting physically wrong output."""
    scale = max((abs(volume) for volume in volumes), default=0.0)
    return max(1e-6, scale * 1e-7)


def _run_safe_boolean(name, operation_kind, argument, tools, tolerance):
    operation = BOPAlgo_BOP()
    operation.SetArguments(_shape_list([copy_shape(argument)]))
    operation.SetTools(_shape_list([copy_shape(tool) for tool in tools]))
    operation.SetOperation(operation_kind)
    operation.SetNonDestructive(True)
    operation.SetFuzzyValue(tolerance)
    try:
        operation.Perform()
    except Exception as exc:
        raise ValueError(f"{name} failed in the CAD kernel: {exc}") from exc
    if operation.HasErrors():
        details = "; ".join(_boolean_messages(operation, "DumpErrors"))
        suffix = f": {details}" if details else ""
        raise ValueError(f"{name} failed in the CAD kernel{suffix}")

    result = operation.Shape()
    if result.IsNull():
        raise ValueError(f"{name} returned a null result")
    if not BRepCheck_Analyzer(result).IsValid():
        raise ValueError(f"{name} returned invalid geometry; no result was accepted")
    if not _contains_solid(result) and not _is_empty_compound(result):
        raise ValueError(
            f"{name} returned invalid geometry: output was not a solid; "
            "no result was accepted"
        )

    warning_messages = _boolean_messages(operation, "DumpWarnings")
    if warning_messages:
        warnings.warn(
            f"{name} completed with CAD-kernel warnings: "
            + "; ".join(warning_messages),
            UserWarning,
        )
    return copy_shape(result)


def _safe_result_volume(result, name, tolerance):
    raw_volume = _shape_volume(result)
    physical_volume = _physical_volume(result, f"{name} result", tolerance)
    slack = _volume_slack(raw_volume, physical_volume)
    if abs(raw_volume - physical_volume) > slack:
        raise ValueError(
            f"{name} returned overlapping solid members whose aggregate volume "
            "double-counts occupied space; no result was accepted"
        )
    return physical_volume


def safe_cut(source, *tools, tolerance=_SAFE_BOOLEAN_TOLERANCE_MM):
    """Subtract one or more tools without accepting an impossible result.

    Inputs are independently copied, all tools are applied in one
    non-destructive kernel operation, and the result must be valid with a
    volume no greater than the source. The result comes back at the same
    abstraction level as ``source`` (raw, build123d, or CadQuery).
    """
    if not tools:
        raise ValueError("safe_cut requires at least one cutting tool")
    tolerance = _boolean_tolerance(tolerance)
    template = source
    source, source_volume = _boolean_input(source, "safe_cut source", tolerance)
    checked_tools = [
        _boolean_input(tool, f"safe_cut tool {index}", tolerance)[0]
        for index, tool in enumerate(tools, start=1)
    ]
    result = _run_safe_boolean(
        "safe_cut", BOPAlgo_CUT, source, checked_tools, tolerance
    )
    result_volume = _safe_result_volume(result, "safe_cut", tolerance)
    slack = _volume_slack(source_volume, result_volume)
    if not math.isfinite(result_volume) or result_volume < 0:
        raise ValueError(
            f"safe_cut returned an invalid volume ({result_volume} mm^3); "
            "no result was accepted"
        )
    if result_volume > source_volume + slack:
        raise ValueError(
            "safe_cut rejected an impossible result: subtraction increased "
            f"volume from {source_volume} to {result_volume} mm^3"
        )
    return _rewrap_like(result, template)


def safe_intersection(left, right, *, tolerance=_SAFE_BOOLEAN_TOLERANCE_MM):
    """Intersect two solids and reject invalid or oversized output.

    The result comes back at the same abstraction level as ``left``.
    """
    tolerance = _boolean_tolerance(tolerance)
    template = left
    left, left_volume = _boolean_input(
        left, "safe_intersection left input", tolerance
    )
    right, right_volume = _boolean_input(
        right, "safe_intersection right input", tolerance
    )
    result = _run_safe_boolean(
        "safe_intersection", BOPAlgo_COMMON, left, [right], tolerance
    )
    result_volume = _safe_result_volume(result, "safe_intersection", tolerance)
    slack = _volume_slack(left_volume, right_volume, result_volume)
    if not math.isfinite(result_volume) or result_volume < 0:
        raise ValueError(
            f"safe_intersection returned an invalid volume ({result_volume} mm^3); "
            "no result was accepted"
        )
    maximum = min(left_volume, right_volume)
    if result_volume > maximum + slack:
        raise ValueError(
            "safe_intersection rejected an impossible result: intersection "
            f"volume {result_volume} mm^3 exceeds input volume {maximum} mm^3"
        )
    return _rewrap_like(result, template)


def safe_fuse(source, *tools, tolerance=_SAFE_BOOLEAN_TOLERANCE_MM):
    """Fuse a source and one or more tools in one validated operation.

    The result must be valid and its volume must remain between the largest
    input and the sum of all inputs. Disjoint inputs may produce a valid
    multi-solid compound. The result comes back at the same abstraction
    level as ``source`` (raw, build123d, or CadQuery).
    """
    if not tools:
        raise ValueError("safe_fuse requires at least one tool")
    tolerance = _boolean_tolerance(tolerance)
    template = source
    source, source_volume = _boolean_input(source, "safe_fuse source", tolerance)
    checked_tools = []
    input_volumes = [source_volume]
    for index, tool in enumerate(tools, start=1):
        checked, volume = _boolean_input(
            tool, f"safe_fuse tool {index}", tolerance
        )
        checked_tools.append(checked)
        input_volumes.append(volume)

    result = _run_safe_boolean(
        "safe_fuse", BOPAlgo_FUSE, source, checked_tools, tolerance
    )
    result_volume = _safe_result_volume(result, "safe_fuse", tolerance)
    slack = _volume_slack(*input_volumes, result_volume)
    minimum = max(input_volumes)
    maximum = sum(input_volumes)
    if not math.isfinite(result_volume) or result_volume < 0:
        raise ValueError(
            f"safe_fuse returned an invalid volume ({result_volume} mm^3); "
            "no result was accepted"
        )
    if result_volume < minimum - slack or result_volume > maximum + slack:
        raise ValueError(
            "safe_fuse rejected an impossible result: union volume "
            f"{result_volume} mm^3 is outside the physical range "
            f"{minimum}..{maximum} mm^3"
        )
    return _rewrap_like(result, template)


_TRANSFORM_MISSING = object()
_TRANSLATE_USAGE = (
    "Use translate(shape, x, y, z), translate(shape, (x, y, z)), "
    "or translate(shape, Vector(x, y, z))."
)
_ROTATE_USAGE = (
    "Use rotate(shape, axis, angle_deg), e.g. rotate(shape, 'Z', 90); "
    "shape, axis, and angle_deg are required."
)


def _transform_shape(shape, usage):
    topo = _unwrap_shape(shape)
    if topo is None:
        raise TypeError(
            "The first argument must be a TopoDS_Shape or a build123d/CadQuery "
            f"shape with wrapped topology. {usage}"
        )
    if topo.IsNull():
        raise ValueError(f"Cannot transform a null shape. {usage}")
    return topo


def _transform_number(value, name, usage):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a finite number. {usage}")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{name} must be a finite number. {usage}") from None
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number. {usage}")
    return result


def translate(
    shape=_TRANSFORM_MISSING, x=_TRANSFORM_MISSING,
    y=_TRANSFORM_MISSING, z=_TRANSFORM_MISSING, *extra, **kwargs,
):
    """Translate a shape by (x, y, z).

    Args:
        shape: Raw TopoDS_Shape or build123d/CadQuery shape to translate.
        x, y, z: Translation distances, or pass a single three-coordinate
            tuple/list or Vector as x (omitting y and z).

    Returns:
        An independently copied shape at the new position, at the same
        abstraction level as the input: raw in, raw out; build123d ``Part``
        in, build123d ``Part`` out; CadQuery shape in, CadQuery shape out.
    """
    if extra or kwargs:
        raise TypeError(f"Unexpected translation arguments. {_TRANSLATE_USAGE}")
    topo = _transform_shape(shape, _TRANSLATE_USAGE)
    vector = getattr(x, "wrapped", x)
    if isinstance(x, (tuple, list)) or isinstance(vector, gp_Vec):
        if y is not _TRANSFORM_MISSING or z is not _TRANSFORM_MISSING:
            raise TypeError(
                f"Pass either one vector or three coordinates. {_TRANSLATE_USAGE}"
            )
        coordinates = (
            (vector.X(), vector.Y(), vector.Z())
            if isinstance(vector, gp_Vec) else x
        )
        if len(coordinates) != 3:
            raise ValueError(
                f"Translation requires exactly three coordinates. {_TRANSLATE_USAGE}"
            )
    else:
        coordinates = (x, y, z)
    x, y, z = (
        _transform_number(value, name, _TRANSLATE_USAGE)
        for name, value in zip(("x", "y", "z"), coordinates)
    )
    independent = copy_shape(topo)
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(x, y, z))
    moved = BRepBuilderAPI_Transform(independent, trsf, False).Shape()
    return _rewrap_like(moved, shape)


def rotate(shape=None, axis=None, angle_deg=None, *extra, **kwargs):
    """Rotate a shape around a coordinate axis through the origin.

    Follows right-hand rule: positive angle = counterclockwise when
    looking from positive axis toward origin.
    E.g. positive Y rotation moves +Z toward +X.

    Args:
        shape: Raw TopoDS_Shape or build123d/CadQuery shape to rotate.
        axis: "X", "Y", or "Z".
        angle_deg: Rotation angle in degrees.

    Returns:
        An independently copied shape at the new orientation, at the same
        abstraction level as the input (raw, build123d, or CadQuery).
    """
    if extra or kwargs:
        raise TypeError(f"Unexpected rotation arguments. {_ROTATE_USAGE}")
    topo = _transform_shape(shape, _ROTATE_USAGE)
    if axis is None or angle_deg is None:
        raise TypeError(f"Missing rotation arguments. {_ROTATE_USAGE}")
    axes = {
        "X": gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)),
        "Y": gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)),
        "Z": gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)),
    }
    if not isinstance(axis, str) or axis not in axes:
        raise ValueError(f"axis must be 'X', 'Y', or 'Z', got {axis!r}. {_ROTATE_USAGE}")
    angle_deg = _transform_number(angle_deg, "angle_deg", _ROTATE_USAGE)
    independent = copy_shape(topo)
    trsf = gp_Trsf()
    trsf.SetRotation(axes[axis], math.radians(angle_deg))
    rotated = BRepBuilderAPI_Transform(independent, trsf, False).Shape()
    return _rewrap_like(rotated, shape)


def bbox_point(shape, x="center", y="center", z="center"):
    """Query a point on a shape's bounding box.

    Each axis takes "min", "center", or "max".

    Args:
        shape: Raw TopoDS_Shape or build123d/CadQuery shape.
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
    shape = _require_shape(shape, "bbox_point shape")

    # AddOptimal_s, not Add_s — Add_s reads B-spline/NURBS bounds off the
    # control-point poles, which sit outside the trimmed geometry. For a
    # placement helper that's a real footgun: bbox_point(shape, x="max")
    # would return a point floating in space beyond the actual body, so
    # place_at / assemble would mis-seat NURBS parts. AddOptimal_s gives a
    # tight box on the same basis as `agentcad measure`. Clean_s first to
    # drop cached triangulation (matches metrics.compute_metrics).
    BRepTools.Clean_s(shape)
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box)
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
        shape: Raw TopoDS_Shape or build123d/CadQuery shape.
        from_pt: (x, y, z) source point.
        to_pt: (x, y, z) target point.

    Returns:
        The moved shape at the same abstraction level as the input.
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

    This is the CadQuery-runtime flavor: it returns a ``cq.Workplane`` and
    needs the ``agentcad[cadquery]`` extra. build123d scripts get a
    build123d-native ``assemble`` injected by their runner instead, so this
    function is only reached from CadQuery-routed scripts.

    Args:
        shapes: One or more raw TopoDS_Shape, cq.Shape, or cq.Workplane values.

    Returns:
        cq.Workplane containing the compound.
    """
    try:
        import cadquery as cq
    except ImportError as exc:
        from agentcad.runners.dispatch import MISSING_CADQUERY_MESSAGE
        raise RuntimeError(MISSING_CADQUERY_MESSAGE) from exc

    wrapped = [
        cq.Shape.cast(_require_shape(s, f"assemble shape {index}"))
        for index, s in enumerate(shapes, start=1)
    ]
    compound = cq.Compound.makeCompound(wrapped)
    return cq.Workplane("XY").newObject([compound])


def annular_boss(
    center,
    inner_radius=None,
    outer_radius=None,
    height=None,
    *,
    inner_diameter=None,
    outer_diameter=None,
    z=0.0,
    z_min=None,
    axis="Z",
):
    """Create a raised annular land as a raw ``TopoDS_Shape``.

    Args:
        center: ``(x, y)`` or ``(x, y, z)`` center of the annulus.
        inner_radius / outer_radius: Radii of the bore and outside land.
        height: Boss height, extruded from ``z`` toward +Z.
        inner_diameter / outer_diameter: Diameter alternatives to radii.
        z: Base Z when ``center`` is 2D.
        z_min: Alias for ``z`` matching STEP-edit measurement terminology.
        axis: Currently only ``"Z"`` is supported.

    Returns:
        TopoDS_Shape annular solid suitable for raw-shape assembly/export.
    """
    axis_name = _coerce_axis_name(axis)
    if axis_name != "Z":
        raise ValueError("annular_boss currently supports axis='Z' only")

    inner = _coerce_radius(
        radius=inner_radius,
        diameter=inner_diameter,
        radius_name="inner_radius",
        diameter_name="inner_diameter",
    )
    outer = _coerce_radius(
        radius=outer_radius,
        diameter=outer_diameter,
        radius_name="outer_radius",
        diameter_name="outer_diameter",
    )
    if outer <= inner:
        raise ValueError(
            f"outer radius must be greater than inner radius, got {outer} <= {inner}"
        )
    if height is None or height <= 0:
        raise ValueError(f"height must be positive, got {height}")

    if z_min is not None:
        z = z_min
    cx, cy, cz = _coerce_annulus_center(center, z)

    # Outer cylinder minus a coaxial inner cylinder, built straight on OCP so
    # the STEP-edit path works on the default build123d-only installation.
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder

    axis = gp_Ax2(gp_Pnt(cx, cy, cz), gp_Dir(0, 0, 1))
    outer_cyl = BRepPrimAPI_MakeCylinder(axis, float(outer), float(height)).Shape()
    inner_cyl = BRepPrimAPI_MakeCylinder(axis, float(inner), float(height)).Shape()
    cut = BRepAlgoAPI_Cut(outer_cyl, inner_cyl)
    if not cut.IsDone():
        raise RuntimeError("annular_boss: boolean cut of the bore failed")
    result = cut.Shape()
    # BRepAlgoAPI returns a compound around the single resulting solid;
    # hand back the bare solid like the previous CadQuery-based extrude did.
    if result.ShapeType() == TopAbs_COMPOUND:
        explorer = TopExp_Explorer(result, TopAbs_SOLID)
        if explorer.More():
            return explorer.Current()
    return result


def raise_annulus(
    source=None,
    *,
    center,
    inner_diameter=None,
    outer_diameter=None,
    height,
    z=0.0,
    inner_radius=None,
    outer_radius=None,
    axis="Z",
    fuse=False,
):
    """Add an annular raised land to an imported shape.

    By default this returns a compound of ``source`` plus the annular boss,
    so STEP-edit scripts can preserve fragile imported topology without a
    successful boolean fuse. Set ``fuse=True`` to try a boolean union; if the
    fuse fails, the helper warns and returns the same compound fallback.

    ``source`` may be a STEP/STP/BREP path, a raw TopoDS_Shape, or a wrapped
    build123d/CadQuery shape. Passing ``source=None`` returns only the annular
    boss shape. A path or raw source returns a raw shape; a wrapped source
    returns a wrapped result of the same kind (for example, a build123d
    ``Part`` in gives a build123d ``Part`` out).
    """
    land = annular_boss(
        center=center,
        inner_radius=inner_radius,
        outer_radius=outer_radius,
        inner_diameter=inner_diameter,
        outer_diameter=outer_diameter,
        height=height,
        z=z,
        axis=axis,
    )
    if source is None:
        return land

    base = _coerce_topods_shape(source)
    template = None if isinstance(source, (str, Path)) else source
    if not fuse:
        return _rewrap_like(_compound_topods(base, land), template)

    try:
        fused = BRepAlgoAPI_Fuse(base, land)
        if fused.IsDone():
            return _rewrap_like(fused.Shape(), template)
    except Exception:
        pass

    warnings.warn("raise_annulus boolean fuse failed, returning compound instead")
    return _rewrap_like(_compound_topods(base, land), template)


def _compound_topods(*shapes):
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, _coerce_topods_shape(shape))
    return compound


def _coerce_topods_shape(shape):
    if isinstance(shape, (str, Path)):
        from agentcad.step_io import load_cad_shape
        return load_cad_shape(shape)
    if hasattr(shape, "wrapped"):
        return shape.wrapped
    if hasattr(shape, "val"):
        return shape.val().wrapped
    return shape


def _coerce_radius(*, radius, diameter, radius_name, diameter_name):
    if radius is None and diameter is None:
        raise ValueError(f"pass {radius_name} or {diameter_name}")
    if radius is not None and diameter is not None:
        raise ValueError(f"pass only one of {radius_name} or {diameter_name}")
    divisor = 1.0 if radius is not None else 2.0
    value = float(radius if radius is not None else diameter) / divisor
    if value <= 0:
        label = radius_name if radius is not None else diameter_name
        original = radius if radius is not None else diameter
        raise ValueError(f"{label} must be positive, got {original}")
    return value


def _coerce_annulus_center(center, z):
    values = tuple(center)
    if len(values) == 2:
        return (float(values[0]), float(values[1]), float(z))
    if len(values) == 3:
        return (float(values[0]), float(values[1]), float(values[2]))
    raise ValueError("center must be a 2D or 3D point")


def _coerce_axis_name(axis):
    if not isinstance(axis, str) and hasattr(axis, "direction"):
        coords = _coerce_vector3(axis.direction)
        if coords and _is_positive_z_direction(coords):
            return "Z"
    name = getattr(axis, "name", axis)
    return str(name).strip().upper()


def _coerce_vector3(vector):
    attrs = []
    for attr in ("X", "Y", "Z"):
        if not hasattr(vector, attr):
            break
        value = getattr(vector, attr)
        attrs.append(value() if callable(value) else value)
    if len(attrs) == 3:
        return tuple(float(v) for v in attrs)
    try:
        values = tuple(vector)
    except TypeError:
        return None
    if len(values) != 3:
        return None
    return tuple(float(v) for v in values)


def _is_positive_z_direction(coords, tol=1e-9):
    x, y, z = coords
    return abs(x) <= tol and abs(y) <= tol and z > 0


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


def _dedup_points(points, tol=1e-6):
    """Remove consecutive near-duplicate points within tolerance."""
    if not points:
        return points
    result = [points[0]]
    for p in points[1:]:
        prev = result[-1]
        dist = sum((a - b) ** 2 for a, b in zip(p, prev)) ** 0.5
        if dist > tol:
            result.append(p)
    return result


def spline_wire(points, closed=True):
    """Create a smooth spline wire through 3D points.

    Near-duplicate consecutive points (within 1e-6mm) are automatically
    collapsed to prevent OCC ``Knots interval values too close`` errors.

    Args:
        points: List of (x, y, z) tuples (minimum 3 after dedup).
        closed: If True, close the wire.

    Returns:
        TopoDS_Wire
    """
    points = _dedup_points(points)
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

    Near-duplicate consecutive points (within 1e-6mm) are automatically
    collapsed to prevent zero-length edges.

    Args:
        points: List of (x, y, z) tuples (minimum 3 after dedup).
        closed: If True, close the wire.

    Returns:
        TopoDS_Wire
    """
    points = _dedup_points(points)
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
