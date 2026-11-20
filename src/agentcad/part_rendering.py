"""Recover saved part appearances on a reloaded STEP without guessing order."""
from __future__ import annotations

import math


def saved_render_parts(shape, parts):
    """Return a complete, unambiguous part/shape mapping, or None.

    Exporters can add compound wrappers or reorder children. Match the saved
    measurements against actual compound/solid members and require a disjoint
    partition of the loaded faces, covering loose edges/vertices too.
    Missing, changed or ambiguous metadata falls
    back to rendering the whole shape, never a partial model or guessed colors.
    No Boolean operations or cross-version geometry comparisons are performed.
    """
    if not isinstance(parts, list) or not parts or not all(isinstance(p, dict) for p in parts):
        return None
    from OCP.TopAbs import (
        TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX,
    )
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS_Iterator
    from OCP.TopTools import TopTools_IndexedMapOfShape

    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    if not faces.Extent():
        return None
    candidates = {}
    pending = [shape]
    while pending:
        member = pending.pop()
        member_faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(member, TopAbs_FACE, member_faces)
        indices = frozenset(faces.FindIndex(member_faces.FindKey(i)) for i in range(1, member_faces.Extent() + 1))
        if indices and indices not in candidates:
            candidates[indices] = (member, _measure(member, member_faces.Extent()))
        if member.ShapeType() in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
            children = TopoDS_Iterator(member)
            while children.More():
                pending.append(children.Value())
                children.Next()

    result, used = [], set()
    for part in parts:
        metrics = part.get("metrics")
        if not isinstance(metrics, dict):
            return None
        matches = [
            (indices, member) for indices, (member, measured) in candidates.items()
            if _matches(metrics, measured)
        ]
        if len(matches) != 1:
            return None
        indices, member = matches[0]
        if used.intersection(indices):
            return None
        used.update(indices)
        result.append({**part, "topo_shape": member})
    if used != set(range(1, faces.Extent() + 1)):
        return None
    # Face coverage alone would miss loose construction wires or vertices.
    # A parts payload replaces rendering the whole shape, so every entity
    # must remain visible even for mixed solid/surface/wire outputs.
    for kind in (TopAbs_EDGE, TopAbs_VERTEX):
        whole = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, kind, whole)
        covered = set()
        for part in result:
            members = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(part["topo_shape"], kind, members)
            covered.update(whole.FindIndex(members.FindKey(i)) for i in range(1, members.Extent() + 1))
        if covered != set(range(1, whole.Extent() + 1)):
            return None
    return result


def _measure(shape, face_count):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bounds)
    xmin, ymin, zmin, xmax, ymax, zmax = bounds.Get()
    volume, area = GProp_GProps(), GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, volume)
    BRepGProp.SurfaceProperties_s(shape, area)
    center = volume.CentreOfMass()
    edges = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    return {
        "bounding_box": {"x": [xmin, xmax], "y": [ymin, ymax], "z": [zmin, zmax]},
        "volume": volume.Mass(), "surface_area": area.Mass(),
        "center_of_mass": {"x": center.X(), "y": center.Y(), "z": center.Z()},
        "face_count": face_count, "edge_count": edges.Extent(),
    }


def _matches(saved, measured):
    def close(a, b, relative=1e-9):
        return (type(a) in (int, float) and math.isfinite(a)
                and math.isclose(a, b, rel_tol=relative, abs_tol=2e-4))
    try:
        return (
            saved["face_count"] == measured["face_count"]
            and saved["edge_count"] == measured["edge_count"]
            and all(close(saved["bounding_box"][axis][i], measured["bounding_box"][axis][i])
                    for axis in "xyz" for i in (0, 1))
            and all(close(saved["center_of_mass"][axis], measured["center_of_mass"][axis]) for axis in "xyz")
            and all(close(saved[key], measured[key], 1e-6) for key in ("volume", "surface_area"))
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return False
