"""Evidence, explicit structure expectations, and advisory part separation."""

from __future__ import annotations

import copy


def structure_options(options):
    """Validate author intent at capture time, before expensive export work."""
    options = options or {}
    result = {}
    for key in ("expect_solids", "expect_shells"):
        if key in options:
            value = options[key]
            if type(value) is not int or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
            result[key] = value
    if "floating" in options:
        if type(options["floating"]) is not bool:
            raise ValueError("floating must be a boolean")
        result["floating"] = options["floating"]
    return result


def structure_checks(layer, expectations, *, part_id=None):
    checks = []
    for key, field in (("expect_solids", "solid_count"), ("expect_shells", "shell_count")):
        if key not in expectations:
            continue
        actual = layer.get(field)
        checks.append({
            "expectation": key, "expected": expectations[key], "actual": actual,
            "passed": actual == expectations[key] if actual is not None else None,
            **({"part_id": part_id} if part_id is not None else {}),
        })
    return checks


def with_structure_checks(report, checks):
    """Apply explicit intent without masking an earlier geometry failure."""
    if not checks:
        return report
    from agentcad.validation import _assemble

    layers = copy.deepcopy(report["layers"])
    entry = layers["structure"]
    entry["expectations"] = checks
    entry["gates"] = True
    failed = any(c["passed"] is False for c in checks)
    unknown = any(c["passed"] is None for c in checks)
    entry["status"] = "fail" if failed else "error" if unknown else "pass"
    first = report["first_failure"] or ("structure" if failed else None)
    undetermined = report.get("undetermined_layer") or ("structure" if unknown else None)
    return _assemble(report["profile"], layers, first_failure=first, undetermined=undetermined)


def diagnostic_guidance(report):
    """Separate observed failures from causes that require inspection."""
    first = report.get("first_failure")
    unknown = "The cause of this failure has not been determined."
    checks = []
    if first == "shell_closure":
        unknown = ("We have not determined whether intended faces are missing, existing faces "
                   "are disconnected, or loose faces are unwanted. Closure alone does not "
                   "establish which repair applies.")
        checks = [
            "Inspect the listed free edges and loose faces with inspect FILE --ids or "
            "view FILE --validation; compare them with the intended surfaces in the source model.",
            "Check whether each intended face exists. If faces exist on both sides of a gap, "
            "check that their boundaries coincide before considering sewing. "
            "Sewing cannot replace a missing face.",
        ]
    elif first == "mesh_manifold":
        kind = report["layers"][first].get("defect", {}).get("kind")
        if kind == "triangle_ceiling":
            unknown = "The triangle budget was exceeded; this does not establish defective faces."
            checks = ["Inspect model complexity and tiny features against the reported triangle budget "
                      "before deciding whether simplification is acceptable."]
        elif kind in ("non_manifold_edge", "pinch_vertex"):
            unknown = ("The local surface connection failed, but we have not determined whether "
                       "the design calls for separate bodies, a joined body, or a different profile.")
            checks = [
                "Inspect the reported edge or vertex and adjacent faces with inspect FILE --ids "
                "or view FILE --validation; trace them to the source operation.",
                "Confirm whether these regions should be separate closed bodies or one connected "
                "body, and check for self-crossing profiles before choosing a repair.",
            ]
        else:
            unknown = "The mesh check failed; the underlying modeling or tessellation cause is unverified."
            checks = ["Inspect the reported mesh defect and any listed faces in the source model. "
                      "Check shared boundaries, face orientation, and tessellation settings "
                      "before changing geometry."]
    elif first == "structure":
        unknown = "Counts alone do not tell us which bodies are unwanted or should be joined."
        checks = ["Compare the expected and actual counts, per-solid volumes and bounds, and any "
                  "part IDs with the intended assembly. Confirm which bodies should exist "
                  "before fusing, deleting, or changing an expectation."]
    elif first == "brep_check":
        unknown = "The kernel identified inconsistent entities, but the source operation responsible is unverified."
        checks = ["Inspect the reported error classes and entity IDs. Trace those entities to the "
                  "source operations and validate intermediate results to locate the first failing operation."]
    else:
        return None
    return {"finding": report["message"], "unknown": unknown, "next_checks": checks}


def repair_guidance(report):
    """Possible repairs, not diagnoses: no applicability precondition is tested here."""
    first = report.get("first_failure")
    if first is None:
        return []
    entry = report["layers"][first]
    repairs = []

    def add(kind, changes_intent, why, how, precondition):
        item = dict(kind=kind, changes_intent=changes_intent, why=why, how=how,
                    precondition=precondition, applicability="unverified",
                    evidence={"layer": first, "message": report["message"]})
        repairs.append(item)

    if first == "shell_closure":
        add("rebuild_closed_profile", True,
            "A missing intended face or incomplete profile is one possible cause of an open boundary.",
            "Close the intended profile before extruding, or construct the missing face "
            "with Face(closed_wire) and rebuild the shell. Validate the exported STEP again.",
            "Inspection confirms an intended face is missing or its source profile is incomplete.")
        add("sew_coincident_faces", False,
            "Coincident face boundaries may have separate topology.",
            "Use Shell(existing_faces) to sew existing faces, then Solid(shell) only if closed. "
            "Revalidate; do not increase tolerance or move faces to force closure.",
            "Only when all intended faces already exist and their boundaries coincide; "
            "sewing must not add a face or change the occupied geometry.")
        if entry.get("loose_face_count"):
            add("remove_loose_faces", True, "Faces outside any shell may be unwanted export leftovers.",
                "Keep only intended bodies in the exported Compound; revalidate the exported STEP.",
                "The listed loose faces are confirmed unwanted, not intended surfaces that need rebuilding.")
    elif first == "mesh_manifold":
        kind = entry.get("defect", {}).get("kind")
        if kind in ("non_manifold_edge", "pinch_vertex"):
            add("separate_body_topology", False,
                "Independent bodies may share an edge or point in one non-manifold shell.",
                "Rebuild the original closed bodies separately and use show_assembly([a, b]). "
                "Check the exported STEP's verdict.",
                "The intended result is separate closed bodies with unchanged occupied geometry; "
                "this does not repair a self-crossing profile or satisfy expect_solids: 1.")
            add("overlap_and_fuse", True,
                "A single body needs a connection with positive volume.",
                "Extend the intended connection to overlap the other body, then fuse using "
                "a.fuse(b). Recheck dimensions and expect_solids; do not choose an overlap blindly.",
                "The design requires a single connected body, and the connection dimensions "
                "permit adding material. The reported defect is at that intended connection.")
        elif kind != "triangle_ceiling":
            add("rebuild_failed_faces", True,
                f"The exported surface has a {kind or 'mesh'} defect.",
                "Rebuild the listed faces from the intended closed profiles. Re-export and "
                "validate the STEP before exporting a mesh.",
                "Inspection identifies a defective source face or profile responsible for the mesh failure.")
    elif first == "structure":
        add("restore_expected_structure", True,
            "The declared body or shell count does not match the output.",
            "Inspect the per-solid volumes and bounds. Fuse intended overlapping bodies with "
            "a.fuse(b), or remove unintended bodies. Change the expectation only if the "
            "design intentionally requires a different count.",
            "The intended body arrangement and the specific source of the count mismatch are confirmed.")
    elif first == "brep_check":
        add("rebuild_invalid_geometry", True, "The kernel reports inconsistent geometry.",
            "Rebuild the failing operation from a closed, non-self-crossing profile; "
            "validate after each Boolean or fillet.",
            "Intermediate validation identifies the source operation responsible for the reported entities.")
    return repairs


def part_connections(raw_parts, public_parts, *, tolerance_mm=0.01):
    """Nearest physical distance, with a small explicit contact tolerance.

    Only named/explicitly identified parts are diagnosed. No connection is
    inferred from names, groups, or a bounding-box overlap.
    """
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    warnings = []
    distances = {}
    for i, (raw, public) in enumerate(zip(raw_parts, public_parts)):
        if not raw.get("name") and raw.get("explicit_id") is None:
            continue
        floating = raw.get("validation_options", {}).get("floating", False)
        nearest = None
        unavailable = False
        for j, other in enumerate(raw_parts):
            if i == j:
                continue
            key = tuple(sorted((i, j)))
            if key not in distances:
                try:
                    tool = BRepExtrema_DistShapeShape(raw["topo_shape"], other["topo_shape"])
                    distances[key] = tool.Value() if tool.IsDone() else None
                except Exception:
                    distances[key] = None
            distance = distances[key]
            if distance is None:
                unavailable = True
                continue
            if nearest is None or distance < nearest[0]:
                nearest = (distance, public_parts[j]["id"])
        if nearest is None or unavailable:
            public["connection"] = {"status": "unavailable" if unavailable else "not_applicable",
                                    "floating": floating, "tolerance_mm": tolerance_mm}
            continue
        distance, neighbor = nearest
        disconnected = distance > tolerance_mm
        public["connection"] = {
            "status": "floating" if floating else "disconnected" if disconnected else "touching",
            "floating": floating, "nearest_part_id": neighbor,
            "distance_mm": round(distance, 6), "tolerance_mm": tolerance_mm,
        }
        if disconnected and not floating:
            warnings.append(f"Part {public['id']!r} touches no other part: nearest part "
                            f"{neighbor!r} is {distance:.4g} mm away. If intentional, "
                            "set options={'floating': True} on its show_object call.")
    return warnings


def validation_markers(shape, report):
    """Polylines in CAD coordinates shared by the PNG and browser highlights."""
    from agentcad.topo_ids import pick_edge_topods, pick_face_topods, _indexed_map, _xyz, vertex_entries
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    markers = []
    closure = report["layers"].get("shell_closure", {})
    edge_ids = set(closure.get("free_edge_ids", []))
    mesh = report["layers"].get("mesh_manifold", {})
    defects = mesh.get("defects", [mesh["defect"]] if mesh.get("defect") else [])
    face_ids = set(closure.get("loose_face_ids", []))
    vertex_ids = set()
    for entity in report["layers"].get("brep_check", {}).get("entities", []):
        if entity["kind"] == "face":
            face_ids.add(entity["id"])
        elif entity["kind"] == "edge":
            edge_ids.add(entity["id"])
        elif entity["kind"] == "vertex":
            vertex_ids.add(entity["id"])
    if vertex_ids:
        for vertex in vertex_entries(shape):
            if vertex["id"] in vertex_ids:
                markers.append({"kind": "point", "points": [vertex["location"]], "vertex_ids": [vertex["id"]]})
    for defect in defects:
        edge_ids.update(defect.get("edge_ids", []))
        if not defect.get("location") and not defect.get("endpoints"):
            face_ids.update(defect.get("face_ids", []))
    edge_map = _indexed_map(shape, TopAbs_EDGE)
    for face_id in sorted(face_ids):
        exp = TopExp_Explorer(pick_face_topods(shape, face_id), TopAbs_EDGE)
        while exp.More():
            edge_ids.add(edge_map.FindIndex(exp.Current()))
            exp.Next()
    for edge_id in sorted(edge_ids):
        curve = BRepAdaptor_Curve(pick_edge_topods(shape, edge_id))
        start, end = curve.FirstParameter(), curve.LastParameter()
        points = [_xyz(curve.Value(start + (end - start) * i / 64)) for i in range(65)]
        markers.append({"kind": "edge", "edge_id": edge_id, "points": points})
    for defect in defects:
        if defect.get("endpoints"):
            markers.append({"kind": "mesh_edge", "points": defect["endpoints"],
                            "face_ids": defect.get("face_ids", [])})
        elif defect.get("location"):
            markers.append({"kind": "point", "points": [defect["location"]],
                            "vertex_ids": defect.get("vertex_ids", []),
                            "face_ids": defect.get("face_ids", [])})
    return markers
