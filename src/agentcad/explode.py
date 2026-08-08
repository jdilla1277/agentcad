"""Exploded-view geometry: split an assembly and move parts apart.

Parts are displaced radially: each part moves along the vector from the
assembly's bounding-box center to its own bounding-box center, scaled by an
explode factor. Factor 1.0 ("100%") doubles each part's distance from the
assembly center; 0.0 leaves the assembly assembled.
"""

_MIN_DIRECTION = 1e-9
MAX_FACTOR = 5.0


def parse_explode_factor(value):
    """Parse an explode amount: '50%', '100%', '0.5', or 1.5 → float factor.

    Raises ValueError with an agent-actionable message on bad input.
    """
    if isinstance(value, (int, float)):
        factor = float(value)
    else:
        raw = str(value).strip()
        try:
            if raw.endswith("%"):
                factor = float(raw[:-1]) / 100.0
            else:
                factor = float(raw)
        except ValueError:
            raise ValueError(
                f"Invalid explode factor '{value}'. Use a percentage like '50%' "
                "or a scale factor like 0.5 (100% doubles part separation)."
            )
    if factor < 0:
        raise ValueError(
            f"Explode factor must be >= 0, got {factor}. Use 0 for assembled, "
            "1.0 (100%) for a standard exploded view."
        )
    if factor > MAX_FACTOR:
        raise ValueError(
            f"Explode factor {factor} exceeds the maximum of {MAX_FACTOR} "
            f"({int(MAX_FACTOR * 100)}%). Very large factors push parts out of frame."
        )
    return factor


def shape_bbox(topo_shape):
    """Tight bounding box of a shape as ((xmin, ymin, zmin), (xmax, ymax, zmax))."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepTools import BRepTools

    # Same sequence as compute_metrics: Clean_s drops cached triangulation so
    # AddOptimal_s measures the exact trimmed geometry, not NURBS poles.
    BRepTools.Clean_s(topo_shape)
    bbox = Bnd_Box()
    BRepBndLib.AddOptimal_s(topo_shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def shape_center(topo_shape):
    """Bounding-box center of a shape as (x, y, z)."""
    (xmin, ymin, zmin), (xmax, ymax, zmax) = shape_bbox(topo_shape)
    return ((xmin + xmax) / 2, (ymin + ymax) / 2, (zmin + zmax) / 2)


def split_solids(topo_shape):
    """Return the individual solids inside a shape (empty list if none)."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    solids = []
    explorer = TopExp_Explorer(topo_shape, TopAbs_SOLID)
    while explorer.More():
        solids.append(TopoDS.Solid_s(explorer.Current()))
        explorer.Next()
    return solids


def _center_from_metrics(part_meta):
    bbox = (part_meta.get("metrics") or {}).get("bounding_box")
    if not bbox:
        return None
    try:
        return tuple((float(bbox[axis][0]) + float(bbox[axis][1])) / 2 for axis in "xyz")
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def _solid_matches_part(solid_center, part_meta):
    """True if a solid's center lies inside the part's recorded bounding box
    (inflated slightly to absorb rounding in meta.json)."""
    bbox = (part_meta.get("metrics") or {}).get("bounding_box")
    if not bbox:
        return False
    try:
        for axis, value in zip("xyz", solid_center):
            lo, hi = float(bbox[axis][0]), float(bbox[axis][1])
            slack = max((hi - lo) * 0.02, 1e-3)
            if value < lo - slack or value > hi + slack:
                return False
    except (KeyError, TypeError, ValueError, IndexError):
        return False
    return True


def group_solids_by_part(solids, parts_meta):
    """Assign STEP solids back to the parts recorded in meta.json.

    A part may contain several solids. Each solid goes to the part whose
    recorded bounding box contains the solid's center; ties break by nearest
    part center. Returns a list aligned with parts_meta:
      [{"part": part_meta, "solids": [...]}, ...]
    or None when no assignment is possible (e.g. missing per-part metrics).
    """
    if not parts_meta:
        return None
    centers = [_center_from_metrics(p) for p in parts_meta]
    if any(c is None for c in centers):
        return None

    groups = [{"part": p, "solids": []} for p in parts_meta]
    for solid in solids:
        solid_center = shape_center(solid)
        candidates = [
            idx for idx, part in enumerate(parts_meta)
            if _solid_matches_part(solid_center, part)
        ]
        if not candidates:
            candidates = list(range(len(parts_meta)))
        best = min(
            candidates,
            key=lambda idx: sum(
                (a - b) ** 2 for a, b in zip(solid_center, centers[idx])
            ),
        )
        groups[best]["solids"].append(solid)
    return [g for g in groups if g["solids"]]


def explode_offsets(part_centers, assembly_center, factor):
    """Radial displacement per part: (center - assembly_center) * factor.

    Returns a list of (dx, dy, dz) aligned with part_centers. Parts whose
    center coincides with the assembly center get a zero offset — the caller
    should surface that as a warning (concentric parts don't separate under
    radial explode).
    """
    offsets = []
    for center in part_centers:
        direction = tuple(c - a for c, a in zip(center, assembly_center))
        if sum(d * d for d in direction) < _MIN_DIRECTION:
            offsets.append((0.0, 0.0, 0.0))
        else:
            offsets.append(tuple(d * factor for d in direction))
    return offsets


def translate_shape(topo_shape, offset):
    """Translate a shape by (dx, dy, dz), returning a new shape."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec

    if all(abs(v) < _MIN_DIRECTION for v in offset):
        return topo_shape
    trsf = gp_Trsf()
    trsf.SetTranslation(gp_Vec(*offset))
    return BRepBuilderAPI_Transform(topo_shape, trsf, True).Shape()


def make_compound(shapes):
    """Wrap shapes in a single compound."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound
