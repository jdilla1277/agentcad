"""Per-feature ID extraction for `inspect --ids` and the pick helpers.

OCCT's ``TopExp.MapShapes_s`` builds an indexed map from a shape: each
feature gets a 1-based integer ID determined by topology-traversal
order. The ordering is **stable within a shape** but **not stable
across edits** — the same fillet operation on a slightly different
input can produce different IDs.

This module is the single source of truth for that mapping. Both
``inspect --ids`` (which publishes the IDs) and the pick helpers
(which look them up) go through the same ``_indexed_map`` calls so
the IDs they exchange are identical.

Public API:
    solid_entries(shape, limit=None) -> list[dict]   # id, volume, bbox
    face_entries(shape, limit=None)  -> list[dict]   # id, normal, area, centroid, bbox
    edge_entries(shape, limit=None)  -> list[dict]   # id, length, endpoints
    topology_counts(shape) -> dict                   # total solids/faces/edges
    pick_face_topods(shape, face_id) -> TopoDS_Face
    pick_edge_topods(shape, edge_id) -> TopoDS_Edge
"""
from typing import Any


def solid_entries(topo_shape, *, limit: int | None = None) -> list[dict]:
    """One dict per solid in `topo_shape`. IDs are 1-indexed."""
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopoDS import TopoDS

    entries = []
    for i, raw in enumerate(_iter_indexed_map(topo_shape, TopAbs_SOLID), start=1):
        if _limit_reached(entries, limit):
            break
        solid = TopoDS.Solid_s(raw)
        entries.append({
            "id": i,
            "volume": _round(_solid_volume(solid)),
            "bbox": _bbox(solid),
        })
    return entries


def face_entries(topo_shape, *, limit: int | None = None) -> list[dict]:
    """One dict per face in `topo_shape`. IDs are 1-indexed."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    entries = []
    for i, raw in enumerate(_iter_indexed_map(topo_shape, TopAbs_FACE), start=1):
        if _limit_reached(entries, limit):
            break
        face = TopoDS.Face_s(raw)
        area, centroid = _face_area_and_centroid(face)
        entries.append({
            "id": i,
            "area": _round(area),
            "centroid": _xyz(centroid),
            "normal": _face_normal(face),
            "bbox": _bbox(face),
        })
    return entries


_AXIS_VECS = (
    ("+x", (1.0, 0.0, 0.0)), ("-x", (-1.0, 0.0, 0.0)),
    ("+y", (0.0, 1.0, 0.0)), ("-y", (0.0, -1.0, 0.0)),
    ("+z", (0.0, 0.0, 1.0)), ("-z", (0.0, 0.0, -1.0)),
)


def _classify_axis(xyz_dict_or_tuple) -> str:
    """Return '+x'/'-x'/'+y'/'-y'/'+z'/'-z' if the direction is within ~8°
    of a coordinate axis, else 'other'. Threshold is cos(8°) ≈ 0.99 — tight
    enough that ambiguous diagonals don't get bucketed, loose enough that
    floating-point drift on supposedly-axis-aligned faces doesn't either."""
    if xyz_dict_or_tuple is None:
        return "other"
    if isinstance(xyz_dict_or_tuple, dict):
        v = (xyz_dict_or_tuple["x"], xyz_dict_or_tuple["y"], xyz_dict_or_tuple["z"])
    else:
        v = tuple(xyz_dict_or_tuple)
    for name, axis in _AXIS_VECS:
        if sum(a * b for a, b in zip(v, axis)) > 0.99:
            return name
    return "other"


def summary_entries(topo_shape, *, id_limit: int | None = None) -> dict:
    """Cluster faces and edges into semantic groups for compact reporting.

    Replaces the per-feature ID/area/normal/etc. payload with a much smaller
    summary that names *what's there* without listing every face. Address
    the M60 Phase 4 friction (#131): a 118-face Pump Manifold's `--ids`
    output is 70 KB, blowing the agent's context budget; the summary
    reduces that to a few hundred bytes while preserving the key signals
    (counts, sizes, axes). IDs are emitted per cluster and can be capped for
    large parts.

    Cluster shape:
      face_clusters: [
        {kind: "planar", axis: "+z", count, area_mean/min/max, ids: [...]},
        {kind: "cylindrical", axis: "+z", radius: 4.0, diameter: 8.0, count, ids: [...]},
        {kind: "other", surface_type: "BSplineSurface", count, ids: [...]},
      ]
      edge_clusters: [
        {kind: "circle", radius: 4.0, diameter: 8.0, axis: "+z", count, ids: [...]},
        {kind: "line", axis: "+x", count, length_mean, ids: [...]},
        {kind: "other", curve_type: "BSplineCurve", count, ids: [...]},
      ]
    """
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.GeomAbs import (
        GeomAbs_BSplineCurve, GeomAbs_BSplineSurface,
        GeomAbs_BezierCurve, GeomAbs_BezierSurface,
        GeomAbs_Circle, GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Ellipse,
        GeomAbs_Hyperbola, GeomAbs_Line, GeomAbs_OffsetCurve,
        GeomAbs_OffsetSurface, GeomAbs_OtherCurve, GeomAbs_OtherSurface,
        GeomAbs_Parabola, GeomAbs_Plane, GeomAbs_Sphere,
        GeomAbs_SurfaceOfExtrusion, GeomAbs_SurfaceOfRevolution,
        GeomAbs_Torus,
    )
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopoDS import TopoDS

    surface_type_names = {
        GeomAbs_Plane: "Plane", GeomAbs_Cylinder: "Cylinder",
        GeomAbs_Cone: "Cone", GeomAbs_Sphere: "Sphere", GeomAbs_Torus: "Torus",
        GeomAbs_BezierSurface: "BezierSurface", GeomAbs_BSplineSurface: "BSplineSurface",
        GeomAbs_SurfaceOfRevolution: "SurfaceOfRevolution",
        GeomAbs_SurfaceOfExtrusion: "SurfaceOfExtrusion",
        GeomAbs_OffsetSurface: "OffsetSurface", GeomAbs_OtherSurface: "OtherSurface",
    }
    curve_type_names = {
        GeomAbs_Line: "Line", GeomAbs_Circle: "Circle", GeomAbs_Ellipse: "Ellipse",
        GeomAbs_Hyperbola: "Hyperbola", GeomAbs_Parabola: "Parabola",
        GeomAbs_BezierCurve: "BezierCurve", GeomAbs_BSplineCurve: "BSplineCurve",
        GeomAbs_OffsetCurve: "OffsetCurve", GeomAbs_OtherCurve: "OtherCurve",
    }

    # --- faces ------------------------------------------------------------
    # Bucket key: (kind, axis-or-direction, radius-bin-or-None).
    # Cluster body accumulates ids + areas (for planar mean/min/max).
    face_buckets: dict[tuple, dict] = {}
    for face_id, raw in enumerate(_iter_indexed_map(topo_shape, TopAbs_FACE), start=1):
        face = TopoDS.Face_s(raw)
        try:
            adaptor = BRepAdaptor_Surface(face)
            kind = adaptor.GetType()
        except Exception:
            kind = GeomAbs_OtherSurface
        area, _ = _face_area_and_centroid(face)

        if kind == GeomAbs_Plane:
            axis = _classify_axis(_face_normal(face))
            key = ("planar", axis, None)
            entry = face_buckets.setdefault(key, {
                "kind": "planar", "axis": axis, "ids": [], "_areas": [],
            })
            entry["ids"].append(face_id)
            entry["_areas"].append(area)
        elif kind == GeomAbs_Cylinder:
            try:
                cyl = adaptor.Cylinder()
                d = cyl.Axis().Direction()
                axis = _classify_axis((d.X(), d.Y(), d.Z()))
                radius = round(cyl.Radius(), 2)
            except Exception:
                axis, radius = "other", None
            key = ("cylindrical", axis, radius)
            entry = face_buckets.setdefault(key, {
                "kind": "cylindrical", "axis": axis, "radius": radius,
                "diameter": _round(radius * 2) if radius is not None else None,
                "ids": [],
            })
            entry["ids"].append(face_id)
        else:
            type_name = surface_type_names.get(kind, f"unknown_{int(kind)}")
            key = ("other", type_name, None)
            entry = face_buckets.setdefault(key, {
                "kind": "other", "surface_type": type_name, "ids": [],
            })
            entry["ids"].append(face_id)

    face_clusters = []
    for entry in face_buckets.values():
        if "_areas" in entry:
            areas = entry.pop("_areas")
            entry["count"] = len(areas)
            entry["area_mean"] = _round(sum(areas) / len(areas))
            entry["area_min"] = _round(min(areas))
            entry["area_max"] = _round(max(areas))
        else:
            entry["count"] = len(entry["ids"])
        face_clusters.append(entry)
    # Sort by count descending so the dominant features come first.
    face_clusters.sort(key=lambda e: -e["count"])

    # --- edges ------------------------------------------------------------
    edge_buckets: dict[tuple, dict] = {}
    for edge_id, raw in enumerate(_iter_indexed_map(topo_shape, TopAbs_EDGE), start=1):
        edge = TopoDS.Edge_s(raw)
        try:
            adaptor = BRepAdaptor_Curve(edge)
            kind = adaptor.GetType()
        except Exception:
            kind = GeomAbs_OtherCurve
        length, _, _ = _edge_geometry(edge)

        if kind == GeomAbs_Line:
            try:
                d = adaptor.Line().Direction()
                axis = _classify_axis((d.X(), d.Y(), d.Z()))
            except Exception:
                axis = "other"
            key = ("line", axis, None)
            entry = edge_buckets.setdefault(key, {
                "kind": "line", "axis": axis, "ids": [], "_lengths": [],
            })
            entry["ids"].append(edge_id)
            entry["_lengths"].append(length)
        elif kind == GeomAbs_Circle:
            try:
                circ = adaptor.Circle()
                d = circ.Axis().Direction()
                axis = _classify_axis((d.X(), d.Y(), d.Z()))
                radius = round(circ.Radius(), 2)
            except Exception:
                axis, radius = "other", None
            key = ("circle", axis, radius)
            entry = edge_buckets.setdefault(key, {
                "kind": "circle", "axis": axis, "radius": radius,
                "diameter": _round(radius * 2) if radius is not None else None,
                "ids": [],
            })
            entry["ids"].append(edge_id)
        else:
            type_name = curve_type_names.get(kind, f"unknown_{int(kind)}")
            key = ("other", type_name, None)
            entry = edge_buckets.setdefault(key, {
                "kind": "other", "curve_type": type_name, "ids": [],
            })
            entry["ids"].append(edge_id)

    edge_clusters = []
    for entry in edge_buckets.values():
        if "_lengths" in entry:
            lengths = entry.pop("_lengths")
            entry["count"] = len(lengths)
            entry["length_mean"] = _round(sum(lengths) / len(lengths))
        else:
            entry["count"] = len(entry["ids"])
        edge_clusters.append(entry)
    edge_clusters.sort(key=lambda e: -e["count"])

    summary_id_truncation = _truncate_cluster_ids(
        face_clusters, edge_clusters, id_limit
    )
    return {
        "face_count": sum(e["count"] for e in face_clusters),
        "edge_count": sum(e["count"] for e in edge_clusters),
        "face_clusters": face_clusters,
        "edge_clusters": edge_clusters,
        **({"id_truncation": summary_id_truncation} if summary_id_truncation else {}),
    }


def edge_entries(topo_shape, *, limit: int | None = None) -> list[dict]:
    """One dict per edge in `topo_shape`. IDs are 1-indexed.
    For closed edges (circles, splines), the two endpoints coincide."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS

    entries = []
    for i, raw in enumerate(_iter_indexed_map(topo_shape, TopAbs_EDGE), start=1):
        if _limit_reached(entries, limit):
            break
        edge = TopoDS.Edge_s(raw)
        length, p_first, p_last = _edge_geometry(edge)
        entries.append({
            "id": i,
            "length": _round(length),
            "endpoints": [_xyz(p_first), _xyz(p_last)],
        })
    return entries


def topology_counts(topo_shape) -> dict:
    """Return total indexed feature counts without materializing entries."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID

    return {
        "solids": _indexed_map(topo_shape, TopAbs_SOLID).Extent(),
        "faces": _indexed_map(topo_shape, TopAbs_FACE).Extent(),
        "edges": _indexed_map(topo_shape, TopAbs_EDGE).Extent(),
    }


def pick_face_topods(topo_shape, face_id: int):
    """Look up a TopoDS_Face by ID. Raises ValueError if out of range."""
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    face_map = _indexed_map(topo_shape, TopAbs_FACE)
    if face_id < 1 or face_id > face_map.Extent():
        raise ValueError(
            f"face id {face_id} out of range (shape has {face_map.Extent()} faces; "
            "IDs are 1-indexed). Run `agentcad inspect <file> --ids` to see "
            "current face IDs."
        )
    return TopoDS.Face_s(face_map.FindKey(face_id))


def pick_edge_topods(topo_shape, edge_id: int):
    """Look up a TopoDS_Edge by ID. Raises ValueError if out of range."""
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopoDS import TopoDS

    edge_map = _indexed_map(topo_shape, TopAbs_EDGE)
    if edge_id < 1 or edge_id > edge_map.Extent():
        raise ValueError(
            f"edge id {edge_id} out of range (shape has {edge_map.Extent()} edges; "
            "IDs are 1-indexed). Run `agentcad inspect <file> --ids` to see "
            "current edge IDs."
        )
    return TopoDS.Edge_s(edge_map.FindKey(edge_id))


# --- internals --------------------------------------------------------------

def _limit_reached(entries: list, limit: int | None) -> bool:
    return limit is not None and len(entries) >= limit


def _truncate_cluster_ids(
    face_clusters: list[dict], edge_clusters: list[dict], id_limit: int | None
) -> dict | None:
    if id_limit is None:
        return None

    fields = []
    for path, clusters in (
        ("face_clusters", face_clusters),
        ("edge_clusters", edge_clusters),
    ):
        truncated_clusters = 0
        omitted = 0
        for cluster in clusters:
            ids = cluster.get("ids", [])
            total = len(ids)
            if total <= id_limit:
                continue
            cluster["ids"] = ids[:id_limit]
            cluster["ids_total"] = total
            cluster["ids_omitted"] = total - id_limit
            cluster["ids_truncated"] = True
            truncated_clusters += 1
            omitted += total - id_limit
        if truncated_clusters:
            fields.append({
                "path": path,
                "limit_per_cluster": id_limit,
                "truncated_clusters": truncated_clusters,
                "omitted_ids": omitted,
            })

    if not fields:
        return None
    return {"limited": True, "fields": fields}


def _indexed_map(topo_shape, topabs_kind):
    """Build OCCT's IndexedMapOfShape — same call inspect and pick share."""
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(topo_shape, topabs_kind, m)
    return m


def _iter_indexed_map(topo_shape, topabs_kind):
    """Yield raw TopoDS_Shape entries from the indexed map, in ID order."""
    m = _indexed_map(topo_shape, topabs_kind)
    for i in range(1, m.Extent() + 1):
        yield m.FindKey(i)


def _solid_volume(solid) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, props)
    return props.Mass()


def _face_area_and_centroid(face):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return props.Mass(), props.CentreOfMass()


def _face_normal(face):
    """Surface normal at the face's UV midpoint, accounting for orientation.
    Returns a {x, y, z} dict, or None if the surface has no defined normal
    at that parameter (rare — happens for degenerate or singular surfaces)."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepTools import BRepTools
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.TopAbs import TopAbs_REVERSED

    try:
        umin, umax, vmin, vmax = BRepTools.UVBounds_s(face)
        u = (umin + umax) / 2
        v = (vmin + vmax) / 2
        adaptor = BRepAdaptor_Surface(face)
        slprops = GeomLProp_SLProps(adaptor.Surface().Surface(), u, v, 1, 1e-6)
        if not slprops.IsNormalDefined():
            return None
        n = slprops.Normal()
        # Account for face orientation
        if face.Orientation() == TopAbs_REVERSED:
            n.Reverse()
        return {"x": _round(n.X()), "y": _round(n.Y()), "z": _round(n.Z())}
    except Exception:
        return None


def _edge_geometry(edge):
    """Return (length, first_point, last_point) for an edge."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    length = props.Mass()

    curve = BRepAdaptor_Curve(edge)
    p_first = curve.Value(curve.FirstParameter())
    p_last = curve.Value(curve.LastParameter())
    return length, p_first, p_last


def _bbox(shape) -> dict:
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return {
        "x": [_round(xmin), _round(xmax)],
        "y": [_round(ymin), _round(ymax)],
        "z": [_round(zmin), _round(zmax)],
    }


def _xyz(pnt) -> dict:
    return {"x": _round(pnt.X()), "y": _round(pnt.Y()), "z": _round(pnt.Z())}


def _round(v: float) -> float:
    """Round floats to 4 decimal places to keep JSON output reasonable.
    4dp is sub-micron precision in mm — fine for agent decision-making."""
    return round(float(v), 4)
