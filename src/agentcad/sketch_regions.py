"""Sketch profiles: every enclosed region of a 2D sketch.

``sketch_regions(*items)`` takes closed shapes and curves drawn on one plane
(they may overlap or cross) and returns every enclosed region as its own
build123d ``Face``. The list position is the region number shown by
``show_regions`` on the region map, so ``regions[2]`` is the region labeled 2.

How it works: one oversized planar face is split by every input edge at
once. The pieces that touch the oversized frame are the outside; every other
piece is an enclosed region. Dangling or internal edges left inside a piece
are removed so each region is a clean face that extrudes to a valid solid.

build123d runtime only; everything CAD-related is imported lazily.
"""

from __future__ import annotations

import math
import warnings


# Fuzzy value for the splitter: lets tangent circles and touching sides meet
# exactly instead of leaving numerical slivers between them.
_FUZZY = 1e-7
# Regions smaller than this fraction of (sketch bbox diagonal)^2 are treated
# as numerical slivers and dropped by default (~0.0002 mm^2 for a 45 mm
# sketch). ``min_area=0`` keeps every region.
DEFAULT_MIN_AREA_FRACTION = 1e-7
# Two region centroids whose local Y differ by at most this fraction of the
# sketch extent belong to the same row of the ordering.
_ROW_TOLERANCE_FRACTION = 0.02
_SAMPLE_PARAMS = (0.0, 0.25, 0.5, 0.75, 1.0)


class SketchRegionError(ValueError):
    """Input that cannot be turned into sketch regions, with a fix hint."""


# --------------------------------------------------------------- input
def _explode(items, faces, edges):
    """Collect faces (for plane detection) and every edge from the input."""
    from build123d import BuildLine, BuildSketch, Shape

    from agentcad.runners.build123d import _as_build123d, _is_topods_shape

    for item in items:
        if isinstance(item, BuildSketch):
            item = item.sketch
        elif isinstance(item, BuildLine):
            item = item.line
        if isinstance(item, (list, tuple)) and not isinstance(item, Shape):
            _explode(item, faces, edges)
            continue
        if _is_topods_shape(item):
            item = _as_build123d(item)
        if not isinstance(item, Shape):
            raise SketchRegionError(
                f"sketch_regions() got {type(item).__name__}; pass 2D build123d "
                "shapes (Sketch, Face, Wire, Edge, Curve such as Line or "
                "Circle(...)) or lists of them."
            )
        if item.solids():
            raise SketchRegionError(
                f"sketch_regions() takes 2D geometry, but got a "
                f"{type(item).__name__} containing a solid. Pass a sketch, or "
                "one planar face of the solid, e.g. part.faces().sort_by(Axis.Z)[-1]."
            )
        faces.extend(item.faces())
        edges.extend(item.edges())
    return faces, edges


def _fmt_vec(vector):
    return "(" + ", ".join(f"{round(value, 3):g}" for value in vector) + ")"


def _resolve_plane_arg(plane):
    from build123d import Plane

    from agentcad.runners.build123d import _resolve_plane

    if plane is None or isinstance(plane, Plane):
        return plane
    return _resolve_plane(plane)


def _snap_to_named(origin, normal, *, keep_sign=True):
    """Return a named-plane-aligned Plane through ``origin`` if ``normal`` is
    parallel to XY/XZ/YZ, else None. ``keep_sign`` keeps the normal's
    direction (a face's real normal); otherwise the named plane's own
    normal is used (a fitted normal's sign is arbitrary)."""
    from build123d import Plane

    for named in (Plane.XY, Plane.XZ, Plane.YZ):
        dot = named.z_dir.dot(normal)
        if abs(abs(dot) - 1) < 1e-9:
            z_dir = named.z_dir if dot > 0 or not keep_sign else -named.z_dir
            offset = named.z_dir.dot(origin)
            return Plane(
                named.origin + named.z_dir * offset,
                x_dir=named.x_dir,
                z_dir=z_dir,
            )
    return None


def _face_plane(face):
    """Sketch plane of a planar face: a named plane when parallel to one,
    otherwise the face's own surface plane (origin = where it was drawn)."""
    from build123d import Plane, Vector
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    adaptor = BRepAdaptor_Surface(face.wrapped)
    if adaptor.GetType() != GeomAbs_Plane:
        raise SketchRegionError(
            "sketch_regions() needs planar input; a curved face was given."
        )
    normal = face.normal_at()
    snapped = _snap_to_named(face.center(), normal)
    if snapped is not None:
        return snapped
    surface = adaptor.Plane()
    origin = surface.Location()
    x_dir = surface.XAxis().Direction()
    return Plane(
        Vector(origin.X(), origin.Y(), origin.Z()),
        x_dir=Vector(x_dir.X(), x_dir.Y(), x_dir.Z()),
        z_dir=normal,
    )


def _edges_plane(edges):
    """Fit a plane through edges only (no faces given)."""
    from build123d import Compound, Vector
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepLib import BRepLib_FindSurface

    found = BRepLib_FindSurface(Compound(edges).wrapped, 1e-6, True, True)
    if not found.Found():
        raise SketchRegionError(
            "sketch_regions() could not find one plane through the input "
            "edges: they are not coplanar, or they are a single straight line. "
            "Draw every shape on one plane, or pass plane=Plane.XY (or the "
            "plane you sketched on)."
        )
    surface = BRepAdaptor_Surface(
        BRepBuilderAPI_MakeFace(found.Surface(), 1e-6).Face()
    ).Plane()
    location = surface.Location()
    direction = surface.Axis().Direction()
    origin = Vector(location.X(), location.Y(), location.Z())
    normal = Vector(direction.X(), direction.Y(), direction.Z())
    snapped = _snap_to_named(origin, normal, keep_sign=False)
    if snapped is not None:
        return snapped
    from build123d import Plane

    x_dir = surface.XAxis().Direction()
    return Plane(origin, x_dir=Vector(x_dir.X(), x_dir.Y(), x_dir.Z()), z_dir=normal)


def _extent(edges):
    from build123d import Compound

    return max(Compound(edges).bounding_box().diagonal, 1e-9)


def _check_coplanar(edges, plane, extent):
    tolerance = max(1e-4, 1e-6 * extent)
    worst = 0.0
    for edge in edges:
        for t in _SAMPLE_PARAMS:
            worst = max(worst, abs(plane.to_local_coords(edge @ t).Z))
    if worst > tolerance:
        raise SketchRegionError(
            f"sketch_regions() input is not coplanar: an edge point lies "
            f"{worst:.3g} off the sketch plane (origin {_fmt_vec(plane.origin)}, "
            f"normal {_fmt_vec(plane.z_dir)}). Draw every shape on the same "
            "plane (e.g. Plane.XZ * Circle(5)), or pass plane= explicitly."
        )


def detect_plane(faces, edges, plane=None):
    """The sketch plane for the input, verified to contain every edge."""
    plane = _resolve_plane_arg(plane)
    if plane is None:
        plane = _face_plane(faces[0]) if faces else _edges_plane(edges)
    _check_coplanar(edges, plane, _extent(edges))
    return plane


# ----------------------------------------------------------- regions
def _split_regions(edges, plane):
    """Split one oversized face by every edge; keep pieces off the frame."""
    from build123d import Compound, Face, Plane, Vector
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Splitter
    from OCP.TopTools import TopTools_ListOfShape

    box = Compound(edges).bounding_box()
    size = box.diagonal + 1.0
    local_center = plane.to_local_coords(box.center())
    frame = Face.make_rect(
        4 * size,
        4 * size,
        Plane(
            plane.from_local_coords((local_center.X, local_center.Y)),
            x_dir=plane.x_dir,
            z_dir=plane.z_dir,
        ),
    )
    corners = [Vector(vertex) for vertex in frame.vertices()]
    corner_tolerance = 1e-6 * size

    splitter = BRepAlgoAPI_Splitter()
    arguments = TopTools_ListOfShape()
    arguments.Append(frame.wrapped)
    tools = TopTools_ListOfShape()
    for edge in edges:
        tools.Append(edge.wrapped)
    splitter.SetArguments(arguments)
    splitter.SetTools(tools)
    splitter.SetFuzzyValue(_FUZZY)
    splitter.SetNonDestructive(True)
    splitter.Build()
    if not splitter.IsDone():
        raise SketchRegionError(
            "sketch_regions() could not split the sketch into regions. "
            "Simplify overlapping curves or remove near-duplicate edges."
        )
    regions = []
    for face in Compound(splitter.Shape()).faces():
        if any(
            (Vector(vertex) - corner).length < corner_tolerance
            for vertex in face.vertices()
            for corner in corners
        ):
            continue  # owns a frame corner: the outside of the sketch
        regions.append(_clean_face(face))
    return regions


def _clean_face(face):
    """Remove dangling/internal edges left inside a split piece and rebuild."""
    from build123d import Edge, Face, Wire
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_EXTERNAL, TopAbs_INTERNAL
    from OCP.TopExp import TopExp_Explorer

    occurrences = []
    explorer = TopExp_Explorer(face.wrapped, TopAbs_EDGE)
    while explorer.More():
        occurrences.append(explorer.Current())
        explorer.Next()
    seen = {}
    internal = False
    for raw in occurrences:
        edge = Edge(raw)
        seen[edge] = seen.get(edge, 0) + 1
        if raw.Orientation() in (TopAbs_INTERNAL, TopAbs_EXTERNAL):
            internal = True
    # An edge used twice in one face is a slit (a dangling line end).
    dangling = {edge for edge, count in seen.items() if count > 1}
    if not internal and not dangling:
        return face
    keep = []
    for raw in occurrences:
        edge = Edge(raw)
        if raw.Orientation() in (TopAbs_INTERNAL, TopAbs_EXTERNAL) or edge in dangling:
            continue
        if not any(edge.is_same(kept) for kept in keep):
            keep.append(edge)
    wires = sorted(
        Wire.combine(keep),
        key=lambda wire: Face(wire).area if wire.is_closed else 0,
        reverse=True,
    )
    return Face(wires[0], wires[1:]) if len(wires) > 1 else Face(wires[0])


def _orient(face, plane):
    from build123d import Face

    if face.normal_at().dot(plane.z_dir) < 0:
        return Face(face.wrapped.Reversed())
    return face


def _order(regions, plane, extent):
    """Rows top to bottom (local +Y first), then left to right; ties by
    larger area, so a ring comes before the disk inside it."""
    if not regions:
        return regions
    row_tolerance = _ROW_TOLERANCE_FRACTION * extent + 1e-12
    info = []
    for region in regions:
        center = plane.to_local_coords(region.center())
        info.append((region, center.X, center.Y, region.area))
    info.sort(key=lambda item: -item[2])
    rows, row = [], [info[0]]
    for item in info[1:]:
        if abs(item[2] - row[0][2]) <= row_tolerance:
            row.append(item)
        else:
            rows.append(row)
            row = [item]
    rows.append(row)
    ordered = []
    for row in rows:
        row.sort(key=lambda item: (round(item[1] / row_tolerance), -item[3]))
        ordered.extend(item[0] for item in row)
    return ordered


def sketch_regions(*items, plane=None, min_area=None):
    """Every enclosed region of a 2D sketch, numbered like the region map.

    Args:
        *items: closed shapes and curves on one plane — Sketch/Face (outer
            and inner boundaries), Wire, Edge, Curve (Line, Polyline, arcs,
            splines), or lists of them. They may overlap or cross; open
            curves split the regions they cross but enclose nothing alone.
        plane: sketch plane (Plane, or "XY"/"XZ"/"YZ"). Default: the first
            face's plane, else a plane fitted through the edges (snapped to
            XY/XZ/YZ when parallel to one).
        min_area: drop regions smaller than this area. Default:
            1e-7 x (sketch bbox diagonal)^2, which only removes numerical
            slivers. Pass 0 to keep every region.

    Returns:
        A ShapeList of Faces with normals along the plane's z_dir, ordered
        in rows top to bottom, then left to right. ``regions[i]`` is the
        region labeled ``i`` by ``show_regions(regions)``. Extrude any
        subset: ``extrude([regions[0], regions[2]], amount=5)``.
        Index numbers shift when sketch parameters change, so parametric
        scripts should select by position: ``regions.at((x, y))`` is the
        region containing that plane-local point, and
        ``regions.merged(a, b)`` fuses chosen regions into one Face (one
        solid after extrude/revolve). Both survive slicing
        (``regions[1:]``); filter_by/sort_by/``+`` return plain lists.

    Caveat: shapes merged before the call (BuildSketch in the default ADD
    mode, or ``a + b``) have already lost their inner boundaries. Pass the
    shapes separately (algebra objects or Mode.PRIVATE); lines can still
    split a merged sketch.
    """
    faces, edges = _explode(items, [], [])
    if not edges:
        raise SketchRegionError(
            "sketch_regions() got no geometry. Pass closed shapes such as "
            "Rectangle(40, 20), Circle(6), or Polyline(..., close=True), plus "
            "optional lines that split them."
        )
    sketch_plane = detect_plane(faces, edges, plane)
    extent = _extent(edges)
    regions = [_orient(face, sketch_plane) for face in _split_regions(edges, sketch_plane)]

    threshold = (
        DEFAULT_MIN_AREA_FRACTION * extent * extent
        if min_area is None else float(min_area)
    )
    kept = [region for region in regions if region.area > threshold]
    dropped = len(regions) - len(kept)
    if not kept:
        if regions:
            raise SketchRegionError(
                f"sketch_regions() found only regions smaller than "
                f"min_area={threshold:.3g}; pass a smaller min_area."
            )
        raise SketchRegionError(
            "sketch_regions() found no closed region: open curves alone "
            "enclose nothing. Add a closed shape (Rectangle, Circle, "
            "Polyline(..., close=True)) or close the outline so the lines "
            "meet end to end."
        )
    if dropped and min_area is None:
        warnings.warn(
            f"sketch_regions() dropped {dropped} sliver region(s) smaller than "
            f"{threshold:.3g}; pass min_area=0 to keep them.",
            UserWarning,
            stacklevel=2,
        )
    return make_region_list(
        _order(kept, sketch_plane, _local_extent(edges, sketch_plane)), sketch_plane
    )


def _local_extent(edges, plane):
    xs, ys = [], []
    for edge in edges:
        for t in _SAMPLE_PARAMS:
            local = plane.to_local_coords(edge @ t)
            xs.append(local.X)
            ys.append(local.Y)
    return max(max(xs) - min(xs), max(ys) - min(ys), 1e-9)


# ------------------------------------------------- region list + picking
_REGION_LIST_CLASS = None


def _region_list_class():
    """The ShapeList subclass sketch_regions returns (built lazily so this
    module imports without build123d)."""
    global _REGION_LIST_CLASS
    if _REGION_LIST_CLASS is not None:
        return _REGION_LIST_CLASS
    from build123d import Face, ShapeList

    class SketchRegions(ShapeList):
        """Faces from sketch_regions(); index i = label i on the region map.

        Extra over ShapeList: ``plane`` (the sketch plane), ``at(point)``
        and ``merged(...)``. Slices keep all three.
        """

        plane = None

        def __getitem__(self, key):
            if isinstance(key, slice):
                return make_region_list(list(self).__getitem__(key), self.plane)
            return list(self).__getitem__(key)

        def at(self, *points):
            """The region (Face) containing each point.

            A point is plane-local ``(x, y)`` — the coordinates of the map's
            axis ticks and of ``center``/``interior`` in the JSON — or a
            global ``(x, y, z)``/Vector on the sketch plane. One point
            returns a Face, several return a list in the same order.
            Raises ValueError naming the nearest region when a point lies
            in no region or on a boundary between regions.
            """
            return region_at(self, *points, plane=self.plane)

        def merged(self, *keys):
            """One Face fused from the chosen regions (indexes or Faces).

            Extruding or revolving the merged face gives one solid; the
            separate regions can give several face-sharing solids instead.
            """
            faces = []
            for key in _flatten(keys):
                if isinstance(key, bool) or not isinstance(key, (int, Face)):
                    raise SketchRegionError(
                        f"merged() takes region indexes or Faces, got "
                        f"{type(key).__name__}. Example: regions.merged(0, 2) or "
                        "regions.merged(regions.at((5, 0)), regions.at((-5, 0)))."
                    )
                if isinstance(key, int):
                    if not -len(self) <= key < len(self):
                        raise SketchRegionError(
                            f"merged() index {key} is out of range: this list has "
                            f"{len(self)} regions (0-{len(self) - 1})."
                        )
                    key = list(self)[key]
                faces.append(key)
            return merge_regions(faces, plane=self.plane, _names=self)

    SketchRegions.__module__ = __name__
    SketchRegions.__qualname__ = "SketchRegions"
    globals()["SketchRegions"] = SketchRegions  # importable/picklable by name
    _REGION_LIST_CLASS = SketchRegions
    return SketchRegions


def make_region_list(faces, plane):
    """A SketchRegions list (ShapeList + plane/at/merged) of ``faces``."""
    result = _region_list_class()(faces)
    # show_regions reads the sketch plane from here to orient the map.
    result.plane = plane
    return result


def _flatten(items):
    from build123d import Shape

    out = []
    for item in items:
        if isinstance(item, (list, tuple)) and not isinstance(item, Shape):
            out.extend(_flatten(item))
        else:
            out.append(item)
    return out


def _region_faces(regions):
    from build123d import Face

    from agentcad.runners.build123d import _as_build123d, _is_topods_shape

    faces = []
    for position, item in enumerate(_flatten([regions])):
        if _is_topods_shape(item):
            item = _as_build123d(item)
        if not isinstance(item, Face):
            raise SketchRegionError(
                f"item {position} is a {type(item).__name__}, not a Face; pass "
                "the list returned by sketch_regions(...)."
            )
        faces.append(item)
    return faces


def _faces_plane(faces, plane):
    plane = _resolve_plane_arg(plane)
    if plane is not None:
        return plane
    edges = [edge for face in faces for edge in face.edges()]
    return detect_plane(faces, edges, None)


def _faces_extent(faces):
    from build123d import Compound

    return max(Compound(list(faces)).bounding_box().diagonal, 1e-9)


def _index_names(faces, names):
    """Map face -> 'regions[i]' text using ``names`` (the source list)."""
    lookup = {}
    for index, face in enumerate(names if names is not None else faces):
        lookup.setdefault(id(face), index)
    return lookup


def _as_point(point, plane, extent):
    """(global Vector, plane-local (x, y)) for a 2- or 3-value point."""
    from build123d import Vector

    try:
        if isinstance(point, Vector):
            values = (point.X, point.Y, point.Z)
        else:
            values = tuple(float(v) for v in point)
    except (TypeError, ValueError):
        values = ()
    if len(values) == 2:
        local = values
        return plane.from_local_coords(values), local
    if len(values) == 3:
        world = Vector(*values)
        local = plane.to_local_coords(world)
        off = abs(local.Z)
        if off > max(1e-4, 1e-6 * extent):
            raise SketchRegionError(
                f"regions.at() point {_fmt_vec(values)} is {off:.3g} off the sketch "
                f"plane (origin {_fmt_vec(plane.origin)}, normal "
                f"{_fmt_vec(plane.z_dir)}). Pass plane-local (x, y) as on the map, "
                "or a global point on the plane."
            )
        return world, (local.X, local.Y)
    raise SketchRegionError(
        f"regions.at() takes points as (x, y) plane-local or (x, y, z) global, "
        f"got {point!r}. Example: regions.at((0, 5)) or regions.at((0, 5), (12, -3))."
    )


def _classify(face, world, tolerance):
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.gp import gp_Pnt
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON

    state = BRepClass_FaceClassifier(
        face.wrapped, gp_Pnt(world.X, world.Y, world.Z), tolerance
    ).State()
    if state == TopAbs_IN:
        return "in"
    if state == TopAbs_ON:
        return "on"
    return "out"


def region_at(regions, *points, plane=None):
    """Face of ``regions`` containing each point; see SketchRegions.at."""
    if not points:
        raise SketchRegionError(
            "regions.at() needs a point, e.g. regions.at((0, 5)) (plane-local x, y)."
        )
    faces = list(regions)
    if not faces:
        raise SketchRegionError("regions.at() was called on an empty region list.")
    faces = _region_faces(faces)
    plane = _faces_plane(faces, plane if plane is not None else getattr(regions, "plane", None))
    extent = _faces_extent(faces)
    tolerance = max(1e-7, 1e-6 * extent)
    found = []
    for point in points:
        world, local = _as_point(point, plane, extent)
        states = [_classify(face, world, tolerance) for face in faces]
        inside = [i for i, state in enumerate(states) if state == "in"]
        on = [i for i, state in enumerate(states) if state == "on"]
        where = _fmt_vec(local)
        if on:
            listed = " and ".join(f"regions[{i}]" for i in on)
            hints = ", ".join(
                f"{_fmt_vec(interior_point(faces[i], plane))} for regions[{i}]"
                for i in on[:3]
            )
            raise SketchRegionError(
                f"regions.at({where}) is on the boundary of {listed}, so it picks "
                f"no single region. Move the point inside one region, e.g. "
                f"{hints} (each region's interior point)."
            )
        if len(inside) > 1:
            listed = " and ".join(f"regions[{i}]" for i in inside)
            raise SketchRegionError(
                f"regions.at({where}) lies in {listed}: those faces overlap. Pass "
                "the list from one sketch_regions(...) call, whose regions never overlap."
            )
        if not inside:
            distances = [face.distance_to(world) for face in faces]
            nearest = min(range(len(faces)), key=distances.__getitem__)
            raise SketchRegionError(
                f"regions.at({where}) lies in no region (plane-local x, y). Nearest "
                f"is regions[{nearest}], {distances[nearest]:.3g} away; its interior "
                f"point is {_fmt_vec(interior_point(faces[nearest], plane))}. Check "
                "the point against the map's axis ticks or items[].interior."
            )
        index = inside[0]
        for previous_point, previous in found:
            if previous == index:
                raise SketchRegionError(
                    f"regions.at() points {_fmt_vec(previous_point)} and {where} both "
                    f"lie in regions[{index}]; pick one point per region (use "
                    "items[].interior from the map JSON)."
                )
        found.append((local, index))
    picked = [faces[index] for _point, index in found]
    if len(picked) == 1:
        return picked[0]
    return make_region_list(picked, plane)


def merge_regions(*faces, plane=None, _names=None):
    """Fuse chosen regions into one Face whose shared edges are removed.

    Extruding or revolving this one face gives one solid; passing the
    regions separately can give several solids that share faces. Raises
    ValueError when the regions do not share boundary edges (they touch
    only at points, or not at all) and so cannot become one face.
    """
    from build123d import Compound, Face
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.TopTools import TopTools_ListOfShape

    faces = _region_faces(faces)
    if not faces:
        raise SketchRegionError(
            "merge_regions() needs regions, e.g. regions.merged(0, 2) or "
            "merge_regions(regions.at((5, 0)), regions.at((-5, 0)))."
        )
    plane = _faces_plane(faces, plane)
    unique = []
    for face in faces:
        if not any(face.is_same(kept) for kept in unique):
            unique.append(face)
    if len(unique) == 1:
        return _orient(unique[0], plane)
    fuse = BRepAlgoAPI_Fuse()
    arguments = TopTools_ListOfShape()
    arguments.Append(unique[0].wrapped)
    tools = TopTools_ListOfShape()
    for face in unique[1:]:
        tools.Append(face.wrapped)
    fuse.SetArguments(arguments)
    fuse.SetTools(tools)
    fuse.SetFuzzyValue(_FUZZY)
    fuse.Build()
    if not fuse.IsDone():
        raise SketchRegionError("merge_regions() could not fuse the chosen regions.")
    unify = ShapeUpgrade_UnifySameDomain(fuse.Shape(), True, True, True)
    unify.Build()
    pieces = Compound(unify.Shape()).faces()
    if len(pieces) != 1:
        names = _index_names(unique, _names)
        listed = ", ".join(
            f"regions[{names[id(face)]}]" if id(face) in names else f"face {k}"
            for k, face in enumerate(unique)
        )
        raise SketchRegionError(
            f"merge_regions() of {listed} gives {len(pieces)} separate faces: some "
            "regions share no boundary edge with the rest (they touch only at "
            "points or not at all). Add a region that connects them, or extrude "
            "them as separate bodies with show_assembly([...])."
        )
    return _orient(Face(pieces[0].wrapped), plane)


def _local_xy(points, plane):
    """(N, 2) plane-local coordinates of (N, 3) global points."""
    import numpy as np

    origin = np.array(tuple(plane.origin))
    axes = np.array([tuple(plane.x_dir), tuple(plane.y_dir)])
    return (np.asarray(points, dtype=float) - origin) @ axes.T


def edge_points(edge, deflection):
    """Points along ``edge`` with chord error <= ``deflection``."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_QuasiUniformDeflection

    curve = BRepAdaptor_Curve(edge.wrapped)
    sampler = GCPnts_QuasiUniformDeflection(curve, deflection)
    if sampler.IsDone() and sampler.NbPoints() >= 2:
        points = []
        for i in range(1, sampler.NbPoints() + 1):
            p = sampler.Value(i)
            points.append((p.X(), p.Y(), p.Z()))
        return points
    return [tuple(edge @ t) for t in (0.0, 0.25, 0.5, 0.75, 1.0)]


def _boundary_segments(face, plane):
    """(N, 2, 2) plane-local boundary segments of a planar face."""
    import numpy as np

    deflection = max(face.bounding_box().diagonal, 1e-9) / 2000
    segments = []
    for edge in face.edges():
        local = _local_xy(edge_points(edge, deflection), plane)
        segments.append(np.stack([local[:-1], local[1:]], axis=1))
    return np.concatenate(segments, axis=0)


def _segment_distance(points, segments):
    """Distance from each (M, 2) point to the nearest of (N, 2, 2) segments."""
    import numpy as np

    a = segments[None, :, 0, :]
    d = segments[None, :, 1, :] - a
    p = points[:, None, :]
    length2 = np.maximum((d * d).sum(-1), 1e-300)
    t = np.clip(((p - a) * d).sum(-1) / length2, 0.0, 1.0)
    closest = a + t[..., None] * d
    return np.sqrt(((p - closest) ** 2).sum(-1)).min(axis=1)


def _inside(points, segments):
    """Even-odd point-in-polygon test of (M, 2) points against the boundary."""
    import numpy as np

    x = points[:, 0:1]
    y = points[:, 1:2]
    x0, y0 = segments[None, :, 0, 0], segments[None, :, 0, 1]
    x1, y1 = segments[None, :, 1, 0], segments[None, :, 1, 1]
    crossing = (y0 <= y) != (y1 <= y)
    t = (y - y0) / np.where(crossing, y1 - y0, 1.0)
    hits = crossing & (x0 + t * (x1 - x0) > x)
    return hits.sum(axis=1) % 2 == 1


def _signed_distance(points, segments, chunk=1_000_000):
    """Distance to the boundary, negative outside, for (M, 2) points."""
    import numpy as np

    points = np.asarray(points, dtype=float).reshape(-1, 2)
    step = max(1, chunk // max(len(segments), 1))
    out = []
    for start in range(0, len(points), step):
        block = points[start:start + step]
        distance = _segment_distance(block, segments)
        out.append(np.where(_inside(block, segments), distance, -distance))
    return np.concatenate(out) if out else np.zeros(0)


# Pole-of-inaccessibility search: stop once the best point is within this
# share of the true inscribed radius, and keep at most this many cells alive
# per level (rings have a whole circle of equally deep points).
_POLE_PRECISION = 0.002
_POLE_MAX_CELLS = 512


def _pole_candidates(segments, seeds):
    """Points deep inside the boundary, best first, by branch and bound.

    A polylabel-style search for the center of the largest inscribed
    circle: square cells cover the boundary's box; a cell can only hold a
    point deeper than its center's distance plus its half-diagonal, so
    cells that cannot beat the best point so far are dropped and the rest
    split into four, level by level. Returns (points, distances), deepest
    first; distances are negative for points outside.
    """
    import numpy as np

    xmin, ymin = segments[..., 0].min(), segments[..., 1].min()
    xmax, ymax = segments[..., 0].max(), segments[..., 1].max()
    width, height = xmax - xmin, ymax - ymin
    extent = max(width, height, 1e-12)
    # Square cells as wide as the short side, at most 64 along the long one.
    size = max(min(width, height), extent / 64)
    nx = max(1, int(np.ceil(width / size)))
    ny = max(1, int(np.ceil(height / size)))
    gx = xmin + (np.arange(nx) + 0.5) * size + (width - nx * size) / 2
    gy = ymin + (np.arange(ny) + 0.5) * size + (height - ny * size) / 2
    cells = np.stack(np.meshgrid(gx, gy), axis=-1).reshape(-1, 2)
    half = size / 2

    pool_points = [np.asarray(seeds, dtype=float).reshape(-1, 2)]
    pool_scores = [_signed_distance(pool_points[0], segments)]
    best = float(pool_scores[0].max()) if len(pool_scores[0]) else -np.inf
    offsets = np.array([[-1, -1], [1, -1], [-1, 1], [1, 1]], dtype=float)
    floor = extent * 1e-9
    while len(cells):
        scores = _signed_distance(cells, segments)
        pool_points.append(cells)
        pool_scores.append(scores)
        best = max(best, float(scores.max()))
        precision = max(_POLE_PRECISION * best, floor)
        potential = scores + half * np.sqrt(2)
        keep = potential > best + precision
        if not keep.any() or half < floor:
            break
        cells, potential = cells[keep], potential[keep]
        if len(cells) > _POLE_MAX_CELLS:
            order = np.argsort(-potential, kind="stable")[:_POLE_MAX_CELLS]
            cells = cells[np.sort(order)]
        half /= 2
        cells = (cells[:, None, :] + offsets[None] * half).reshape(-1, 2)
    points = np.concatenate(pool_points)
    scores = np.concatenate(pool_scores)
    order = np.argsort(-scores, kind="stable")
    return points[order], scores[order]


def interior_point(face, plane):
    """A plane-local (x, y) point guaranteed inside ``face``, as far from its
    edges as practical: the center of the largest inscribed circle, to
    about 0.2% of its radius.

    Unlike the area centroid it never falls outside a U-shaped or ring
    region. Searched on a fine polygon of the outer and hole boundaries,
    then verified with the kernel's point-in-face test.
    """
    import numpy as np

    segments = _boundary_segments(face, plane)
    center = plane.to_local_coords(face.center())
    fallback = (float(center.X), float(center.Y))
    if len(segments) == 0:
        return fallback
    points, scores = _pole_candidates(segments, [fallback])
    extent = max(np.ptp(segments[..., 0]), np.ptp(segments[..., 1]), 1e-12)
    tolerance = max(1e-7, 1e-7 * extent)
    # The polygon is within a hair of the true boundary, so the deepest
    # candidate is inside; the kernel check guards odd cases (tangencies,
    # self-touching boundaries), trying the next deepest few if needed.
    tried = []
    for (x, y), score in zip(points, scores):
        if score <= 0 or len(tried) >= 16:
            break
        if any(math.hypot(x - tx, y - ty) < score / 2 for tx, ty in tried):
            continue
        tried.append((x, y))
        world = plane.from_local_coords((float(x), float(y)))
        if _classify(face, world, tolerance) == "in":
            return (float(x), float(y))
    return fallback


# ------------------------------------------------------ show_regions input
def coerce_region_faces(regions):
    """Faces and sketch plane for ``show_regions``; index = list position."""
    from build123d import Face, Shape

    from agentcad.runners.build123d import _as_build123d, _is_topods_shape

    if isinstance(regions, Face):
        items = [regions]
    elif isinstance(regions, Shape) or _is_topods_shape(regions):
        raise TypeError(
            f"show_regions() expects the list returned by sketch_regions(...), "
            f"got a {type(regions).__name__}. Call regions = sketch_regions(...) "
            "first, then show_regions(regions)."
        )
    else:
        try:
            items = list(regions)
        except TypeError as exc:
            raise TypeError(
                "show_regions() expects the list returned by sketch_regions(...), "
                f"got {type(regions).__name__}."
            ) from exc
    if not items:
        raise ValueError(
            "show_regions() received no regions; sketch_regions(...) returned "
            "an empty list or the list was filtered to nothing."
        )
    faces = []
    for position, item in enumerate(items):
        if _is_topods_shape(item):
            item = _as_build123d(item)
        if not isinstance(item, Face):
            raise TypeError(
                f"show_regions() item {position} is a {type(item).__name__}, not a "
                "Face. Pass the list from sketch_regions(...) (or a list of planar "
                "faces)."
            )
        faces.append(item)
    plane = getattr(regions, "plane", None)
    try:
        edges = [edge for face in faces for edge in face.edges()]
        plane = detect_plane(faces, edges, plane)
    except SketchRegionError as exc:
        raise ValueError(str(exc).replace("sketch_regions()", "show_regions()")) from exc
    return faces, plane


def describe_regions(faces, plane):
    """JSON-ready facts per region, in list order."""
    items = []
    for index, face in enumerate(faces):
        center = face.center()
        local = plane.to_local_coords(center)
        inside = interior_point(face, plane)
        inside_global = plane.from_local_coords(inside)
        items.append({
            "index": index,
            "area": _round(face.area),
            "center": [_round(local.X), _round(local.Y)],
            "center_global": [_round(center.X), _round(center.Y), _round(center.Z)],
            "interior": [_round(inside[0]), _round(inside[1])],
            "interior_global": [
                _round(inside_global.X), _round(inside_global.Y), _round(inside_global.Z)
            ],
            "holes": len(face.inner_wires()),
        })
    return items


def local_bounds(faces, plane):
    """(xmin, ymin, xmax, ymax) of ``faces`` in plane-local coordinates."""
    import numpy as np

    deflection = _faces_extent(faces) / 4000
    points = [
        point for face in faces for edge in face.edges()
        for point in edge_points(edge, deflection)
    ]
    local = _local_xy(points, plane)
    return (
        float(local[:, 0].min()), float(local[:, 1].min()),
        float(local[:, 0].max()), float(local[:, 1].max()),
    )


def parse_window(window, faces, plane):
    """Validate show_regions(window=((xmin, ymin), (xmax, ymax))).

    Returns (xmin, ymin, xmax, ymax) floats, or None for no window.
    """
    if window is None:
        return None
    example = "window=((-5, -5), (5, 5))"
    try:
        (x0, y0), (x1, y1) = window
        x0, y0, x1, y1 = (float(v) for v in (x0, y0, x1, y1))
    except (TypeError, ValueError):
        raise ValueError(
            f"show_regions() window must be ((xmin, ymin), (xmax, ymax)) in "
            f"plane-local units (the map's axis ticks), got {window!r}; e.g. "
            f"{example}."
        ) from None
    if not (x0 < x1 and y0 < y1):
        raise ValueError(
            f"show_regions() window {window!r} is empty: it needs xmin < xmax and "
            f"ymin < ymax, as ((xmin, ymin), (xmax, ymax)); e.g. {example}."
        )
    bx0, by0, bx1, by1 = local_bounds(faces, plane)
    if x1 < bx0 or x0 > bx1 or y1 < by0 or y0 > by1:
        raise ValueError(
            f"show_regions() window {window!r} contains no region: the sketch "
            f"spans x {_round(bx0, 3):g} to {_round(bx1, 3):g}, y {_round(by0, 3):g} "
            f"to {_round(by1, 3):g} (plane-local)."
        )
    return (x0, y0, x1, y1)


def plane_info(plane):
    return {
        "origin": [_round(value) for value in plane.origin],
        "x_dir": [_round(value) for value in plane.x_dir],
        "y_dir": [_round(value) for value in plane.y_dir],
        "z_dir": [_round(value) for value in plane.z_dir],
    }


def _round(value, digits=4):
    rounded = round(float(value), digits)
    return 0.0 if rounded == 0 else rounded


__all__ = [
    "SketchRegionError",
    "sketch_regions",
    "merge_regions",
    "region_at",
    "interior_point",
    "make_region_list",
    "parse_window",
    "coerce_region_faces",
    "describe_regions",
    "plane_info",
]
