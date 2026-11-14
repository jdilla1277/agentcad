"""Exact predicates on verified baseline geometry; no similarity ranking."""

from copy import deepcopy
import math

from agentcad.geometry_manifest import BaselineGeometry, record
from agentcad.geometry_query import InvalidQuery, in_range, referenced_ids

INDEX_REVISION = "geometry-search-index-v1"


def xyz(point):
    return [point.X(), point.Y(), point.Z()]


def precise_bounds(shape):
    """Preserve geometric precision for plane predicates, without rounding."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, False, False)
    low_x, low_y, low_z, high_x, high_y, high_z = box.Get()
    return {"x": [low_x, high_x], "y": [low_y, high_y], "z": [low_z, high_z]}


def parallel(a, b, degrees, *, either=True):
    dot = sum(x * y for x, y in zip(a, b))
    cross = (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])
    angle = math.degrees(math.atan2(math.hypot(*cross), abs(dot) if either else dot))
    return angle <= degrees + 1e-12


def coaxial(left, right, tolerance, angle):
    if left is None or right is None or not parallel(left["direction"], right["direction"], angle):
        return False
    delta = [a - b for a, b in zip(left["origin"], right["origin"])]
    d = right["direction"]
    along = sum(a * b for a, b in zip(delta, d))
    return math.hypot(*(a - along * b for a, b in zip(delta, d))) <= tolerance


class ExactIndex:
    def __init__(self, step, saved):
        self.step, self.saved = step, saved
        self.context = None
        self.shapes, self.descriptors, self.analytics = {}, {}, {}

    def shape(self, object_id):
        if object_id not in self.shapes:
            if self.context is None:
                self.context = BaselineGeometry(self.step)
                if self.context.manifest_id != self.saved["manifest_id"]:
                    raise ValueError("Baseline changed before search; retry with its original bytes.")
            self.shapes[object_id] = self.context.image_shape(object_id)
        return self.shapes[object_id]

    def analytic(self, object_id):
        if object_id in self.analytics:
            return self.analytics[object_id]
        from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
        from OCP.BRep import BRep_Tool
        from OCP.TopoDS import TopoDS
        from OCP import GeomAbs

        entry = self.saved["entities"][object_id]
        result = {"axis": None, "orientation": None, "radius": None, "diameter": None}
        kind = entry["kind"]
        if kind == "face":
            adaptor = BRepAdaptor_Surface(TopoDS.Face_s(self.shape(object_id)))
            mapping = {"Plane": "plane", "Cylinder": "cylinder", "Cone": "cone", "Sphere": "sphere", "Torus": "torus",
                       "BezierSurface": "bezier", "BSplineSurface": "bspline", "SurfaceOfRevolution": "revolution",
                       "SurfaceOfExtrusion": "extrusion", "OffsetSurface": "offset", "OtherSurface": "other"}
            result["surface_type"] = next((name for code, name in mapping.items() if adaptor.GetType() == getattr(GeomAbs, "GeomAbs_" + code)), "other")
            if result["surface_type"] == "plane":
                from OCP.TopAbs import TopAbs_REVERSED
                normal = adaptor.Plane().Axis().Direction()
                if self.shape(object_id).Orientation() == TopAbs_REVERSED:
                    normal.Reverse()
                result["orientation"] = xyz(normal)
            if result["surface_type"] in {"cylinder", "cone", "torus", "sphere"}:
                analytic = getattr(adaptor, result["surface_type"].capitalize())()
                if result["surface_type"] != "sphere":
                    axis = analytic.Axis()
                    result["axis"] = {"origin": xyz(axis.Location()), "direction": xyz(axis.Direction())}
                    result["orientation"] = result["axis"]["direction"]
                if result["surface_type"] in {"cylinder", "sphere"}:
                    result["radius"] = analytic.Radius()
        elif kind == "edge":
            edge = TopoDS.Edge_s(self.shape(object_id))
            if BRep_Tool.Degenerated_s(edge):
                result["curve_type"] = "degenerate"
            else:
                adaptor = BRepAdaptor_Curve(edge)
                mapping = {"Line": "line", "Circle": "circle", "Ellipse": "ellipse", "Hyperbola": "hyperbola", "Parabola": "parabola",
                           "BezierCurve": "bezier", "BSplineCurve": "bspline", "OffsetCurve": "offset", "OtherCurve": "other"}
                result["curve_type"] = next((name for code, name in mapping.items() if adaptor.GetType() == getattr(GeomAbs, "GeomAbs_" + code)), "other")
                if result["curve_type"] == "line":
                    line = adaptor.Line()
                    result["axis"] = {"origin": xyz(line.Location()), "direction": xyz(line.Direction())}
                elif result["curve_type"] in {"circle", "ellipse"}:
                    analytic = adaptor.Circle() if result["curve_type"] == "circle" else adaptor.Ellipse()
                    axis = analytic.Axis()
                    result["axis"] = {"origin": xyz(axis.Location()), "direction": xyz(axis.Direction())}
                    if result["curve_type"] == "circle":
                        result["radius"] = analytic.Radius()
                result["orientation"] = result["axis"]["direction"] if result["axis"] else None
        result["diameter"] = 2 * result["radius"] if result["radius"] is not None else None
        self.analytics[object_id] = result
        return result

    def describe(self, object_id):
        if object_id in self.descriptors:
            return self.descriptors[object_id]
        from agentcad.topo_ids import _bbox

        entry = self.saved["entities"][object_id]
        kind = entry["kind"]
        # Parts search their individual placements. Definition grouping happens
        # only after filtering, never against a union of distant placements.
        bbox = entry.get("bbox") or _bbox(self.shape(object_id))
        occurrences = ([object_id] if kind == "occurrence" else
                       [i for i in entry.get("occurrence_ids", [])
                        if self.saved["entities"][i]["mapping_status"] != "assembly"])
        parts = sorted({self.saved["entities"][i]["part_id"] for i in occurrences})
        result = {"id": object_id, "kind": kind, "part_ids": parts,
                  "part_id": parts[0] if len(parts) == 1 else None,
                  "occurrence_ids": occurrences, "occurrence_id": occurrences[0] if len(occurrences) == 1 else None,
                  "coordinate_frame": "assembly", "units": "mm", "bbox": bbox,
                  "dimensions": {a: round(bbox[a][1] - bbox[a][0], 6) for a in "xyz"},
                  "location": [round(sum(bbox[a]) / 2, 6) for a in "xyz"]}
        for field in ("area", "length", "volume", "placement", "name", "members"):
            if field in entry:
                result[field] = entry[field]
        if kind == "occurrence":
            definition = self.saved["entities"][entry["part_id"]]
            result["volume"] = definition.get("volume")
            result["placement"] = entry["placement"]
        if kind in {"face", "edge"}:
            result.update(self.analytic(object_id))
        if kind in {"face", "edge"} and len(occurrences) <= 1:
            result["selector"] = f"geometry.pick_{kind}({object_id!r})"
        else:
            result["selector"] = None
        result["selector_status"] = "available" if result["selector"] else "native_selection_required"
        self.descriptors[object_id] = result
        return result

    def matches(self, object_id, where):
        for field, spec in where.items():
            if field == "all":
                matches = all(self.matches(object_id, node) for node in spec)
            elif field == "any":
                matches = any(self.matches(object_id, node) for node in spec)
            elif field == "not":
                matches = not self.matches(object_id, spec)
            elif field in {"id", "part_id", "occurrence_id"}:
                entry = self.saved["entities"][object_id]
                if field == "id":
                    ids = [object_id]
                elif field == "occurrence_id":
                    # A selected assembly occurrence includes its descendants.
                    ids = [object_id] if entry["kind"] == "occurrence" else entry.get("occurrence_ids", [])
                    ids = self._ancestors(ids)
                else:
                    occs = [entry["id"]] if entry["kind"] == "occurrence" else entry.get("occurrence_ids", [])
                    ids = [self.saved["entities"][i]["part_id"] for i in self._ancestors(occs)]
                matches = bool(set(ids) & set(spec))
            elif field == "relation":
                matches = self._relation(object_id, spec)
            elif field == "plane":
                matches = self._plane(object_id, spec)
            else:
                data = self.describe(object_id)
                if field in {"area", "length", "volume", "radius", "diameter"}:
                    matches = in_range(data.get(field), spec)
                elif field == "dimensions":
                    matches = all(in_range(data["dimensions"][a], v) for a, v in spec.items())
                elif field in {"surface_type", "curve_type"}:
                    matches = data.get(field) in spec
                elif field == "orientation":
                    matches = data.get("orientation") is not None and parallel(data["orientation"], spec["direction"], spec["angle_tolerance_deg"], either=spec["sense"] == "either")
                elif field == "axis":
                    matches = coaxial(data.get("axis"), spec, spec["tolerance"], spec["angle_tolerance_deg"])
                elif field == "bounds":
                    b, t = data["bbox"], spec["tolerance"]
                    if spec["relation"] == "contained":
                        matches = all(b[a][0] >= spec["min"][i] - t and b[a][1] <= spec["max"][i] + t for i, a in enumerate("xyz"))
                    else:
                        matches = all(b[a][1] >= spec["min"][i] - t and b[a][0] <= spec["max"][i] + t for i, a in enumerate("xyz"))
                elif field == "location":
                    matches = math.dist(data["location"], spec["origin"]) <= spec["tolerance"]
                elif field == "placement":
                    matrix = data.get("placement")
                    matches = matrix is not None and math.dist([r[3] for r in matrix], spec["origin"]) <= spec["tolerance"]
                    if matches and "rotation" in spec:
                        matches = all(abs(matrix[i][j] - spec["rotation"][i][j]) <= 1e-9 for i in range(3) for j in range(3))
                else:
                    raise ValueError(f"Unsupported filter {field}.")
            if not matches:
                return False
        return True

    def _ancestors(self, ids):
        result = set(ids)
        for object_id in ids:
            parent = self.saved["entities"][object_id].get("parent_id")
            visited = {object_id}
            while parent:
                if parent in visited:
                    raise ValueError("Occurrence hierarchy contains a cycle.")
                visited.add(parent)
                result.add(parent)
                parent = self.saved["entities"][parent].get("parent_id")
        return sorted(result)

    def _relation(self, object_id, spec):
        target = spec["to"]
        if spec["type"] in {"coaxial", "shared_axis"}:
            return coaxial(self.analytic(object_id).get("axis"), self.analytic(target).get("axis"), spec["tolerance"], spec["angle_tolerance_deg"])
        if spec["type"] == "distance":
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape
            operation = BRepExtrema_DistShapeShape(self.shape(object_id), self.shape(target))
            if not operation.IsDone():
                raise ValueError("Minimum-distance evaluation did not complete.")
            return in_range(operation.Value(), spec["distance"])
        if object_id == target:
            return False
        from OCP.TopAbs import TopAbs_VERTEX, TopAbs_EDGE, TopAbs_FACE
        from agentcad.topo_ids import _indexed_map
        kind = self.saved["entities"][object_id]["kind"]
        target_kind = self.saved["entities"][target]["kind"]
        dimension = min({"edge": 1, "face": 2}.get(kind, 3), {"edge": 1, "face": 2}.get(target_kind, 3))
        topology = {1: TopAbs_VERTEX, 2: TopAbs_EDGE, 3: TopAbs_FACE}[dimension]
        left, right = _indexed_map(self.shape(object_id), topology), _indexed_map(self.shape(target), topology)
        return any(right.Contains(left.FindKey(i)) for i in range(1, left.Extent() + 1))

    def _plane(self, object_id, spec):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform, BRepBuilderAPI_MakeFace
        from OCP.gp import gp_Trsf, gp_Ax3, gp_Dir, gp_Pnt, gp_Pln

        frame = gp_Ax3(gp_Pnt(*spec["origin"]), gp_Dir(*spec["normal"]))
        transform = gp_Trsf()
        transform.SetTransformation(frame)
        projected = BRepBuilderAPI_Transform(self.shape(object_id), transform, True).Shape()
        bounds = precise_bounds(projected)
        z = bounds["z"]
        tolerance = spec["tolerance"]
        if spec["relation"] == "coplanar":
            return max(abs(v) for v in z) <= tolerance
        # A bounding box straddling a plane is insufficient for disconnected
        # geometry. Minimum distance to a covering plane checks the real shape.
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        extent = max(1, *(abs(v) for a in "xy" for v in bounds[a])) + tolerance + 1
        plane = BRepBuilderAPI_MakeFace(gp_Pln(frame), -extent, extent, -extent, extent).Face()
        operation = BRepExtrema_DistShapeShape(self.shape(object_id), plane)
        if not operation.IsDone():
            raise ValueError("Plane intersection evaluation did not complete.")
        return operation.Value() <= tolerance


def evaluate(step, saved, query):
    for object_id in referenced_ids(query["where"]):
        try:
            record(saved, object_id)
        except ValueError as exc:
            raise InvalidQuery(str(exc)) from exc
    kind = query["kind"]
    # part means independently placed leaf parts, with occurrence IDs as the
    # public result identities. Assembly occurrences can be queried explicitly.
    eligible = sorted(e["id"] for e in saved["entities"].values()
                      if (e["kind"] == "occurrence" and e["mapping_status"] != "assembly"
                          if kind == "part" else e["kind"] == kind))
    index = ExactIndex(step, saved)
    results, failures = [], []
    for object_id in eligible:
        try:
            if index.matches(object_id, query["where"]):
                results.append(deepcopy(index.describe(object_id)))
        except Exception as exc:
            failures.append({"id": object_id, "message": f"{type(exc).__name__}: {exc}"})
    matched_occurrences = len(results) if kind in {"part", "occurrence"} else None
    if query["result_mode"] == "definitions":
        grouped = {}
        for entry in results:
            part_id = entry["part_id"]
            if part_id not in grouped:
                definition = saved["entities"][part_id]
                grouped[part_id] = {"id": part_id, "kind": "part", "part_id": part_id, "part_ids": [part_id],
                                    "occurrence_ids": [], "occurrence_id": None, "coordinate_frame": "definition_local", "units": "mm",
                                    "bbox": definition["bbox"], "dimensions": {a: round(definition["bbox"][a][1] - definition["bbox"][a][0], 6) for a in "xyz"},
                                    "location": [round(sum(definition["bbox"][a]) / 2, 6) for a in "xyz"],
                                    "volume": definition.get("volume"), "selector": None, "selector_status": "native_selection_required"}
            grouped[part_id]["occurrence_ids"].append(entry["id"])
        results = sorted(grouped.values(), key=lambda e: e["id"])
        for entry in results:
            entry["distinct_occurrence_count"] = len(entry["occurrence_ids"])
            members = {kind: set() for kind in ("solid", "face", "edge")}
            pending = list(entry["occurrence_ids"])
            visited = set()
            while pending:
                occurrence_id = pending.pop()
                if occurrence_id in visited:
                    continue
                visited.add(occurrence_id)
                occurrence = saved["entities"][occurrence_id]
                pending.extend(occurrence["children"])
                for kind, ids in occurrence["members"].items():
                    members[kind].update(ids)
            entry["members"] = {kind: sorted(ids) for kind, ids in members.items()}
    coverage = {"status": "partial" if failures else "complete", "eligible": len(eligible),
                "evaluated": len(eligible) - len(failures), "failed": len(failures), "pending": 0,
                "matched": len(results), "matched_occurrences": matched_occurrences}
    return results, coverage, failures, index
