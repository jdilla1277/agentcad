"""Layered geometry validation (M71).

One registry of named layers, one runner, one report shape. ``is_valid`` is
the verdict of the ``deliverable`` profile: true only when every gating
layer passed, false with ``first_failure`` named, ``None`` when a gating
layer could not finish. The kernel-only result that ``is_valid`` used to
carry lives in ``layers.brep_check``.

Layers (in order):

    file_parse       the file could be read as CAD (recorded by the loader)
    kernel_load      a non-empty shape came out of the kernel (loader)
    brep_check       BRepCheck_Analyzer finds no per-entity error
    shell_closure    every shell is closed; no loose faces; free edges by ID
    mesh_manifold    the tessellation, stitched by shared topology, is a
                     closed orientable manifold with no pinch vertices
    structure        solid/shell/face counts and loose members (reports)
    advisory         sliver faces and loose tolerances (reports)

The mesh layer is the slow one and runs in a bounded subprocess by default,
so a pathological part turns into ``status: timeout`` and ``is_valid: null``
instead of a hung command.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


MESH_TIMEOUT_ENV = "AGENTCAD_MESH_VALIDATION_TIMEOUT_S"
DEFAULT_MESH_TIMEOUT_S = 60.0

# Whole-report budget for validating a written file (run's delivered STEP,
# import's source). Files at or above VALIDATION_WORKER_MIN_BYTES validate in
# a fresh worker process so a stalled native check returns within budget as
# ``is_valid: null`` with every completed layer retained; smaller files
# validate in-process, where the whole report takes milliseconds. 0 disables.
VALIDATION_TIMEOUT_ENV = "AGENTCAD_VALIDATION_TIMEOUT_S"
DEFAULT_VALIDATION_TIMEOUT_S = 120.0
VALIDATION_WORKER_MIN_BYTES = 1_000_000

# Daemon requests run in forked children. A parallel OCCT algorithm in one of
# those children can deadlock if the daemon parent had already initialized
# TBB: fork preserves the pool state but not its worker threads. The daemon
# sets this private marker while invoking a command so in-process validation
# can keep its mesher sequential. Large shapes still use the fresh worker
# subprocess below, where parallel meshing is safe.
_DAEMON_CHILD_ENV = "_AGENTCAD_DAEMON_CHILD"

# Shapes at or below this many faces mesh in-process: the check takes tens of
# milliseconds there, while a worker subprocess costs about half a second of
# interpreter start-up on every run. Larger shapes use the bounded worker so
# a pathological part becomes a timeout instead of a hang.
MESH_INPROCESS_FACE_LIMIT = 500

# ``run`` judges the reloaded STEP; the in-memory source is validated only to
# feed the ``step_round_trip`` diagnostic. Above this many faces the kernel
# check and the tessellation are not repeated on the source: on a 2,000-face
# imported part each of them costs a minute or more, and the delivered STEP
# runs both anyway. The cheap layers (shell closure, structure, advisory)
# still run so the round trip can flag a lost body or an opened shell.
ROUND_TRIP_FULL_FACE_LIMIT = 500
_ROUND_TRIP_EXPENSIVE_LAYERS = ("brep_check", "mesh_manifold")
ROUND_TRIP_SKIP_REASON = (
    f"Not run on the in-memory source above {ROUND_TRIP_FULL_FACE_LIMIT} faces; "
    "the reloaded STEP runs this layer and its verdict gates."
)

# Tessellation deflection relative to part size, clamped. Matches the policy
# of the downstream gate this layer is measured against, so the parity corpus
# exercises the same regime.
DEFLECTION_RELATIVE = 0.001
DEFLECTION_MIN_MM = 0.005
DEFLECTION_MAX_MM = 0.5

# Advisory thresholds: flag fragile geometry, never gate on it.
ADVISORY_MIN_FACE_AREA_MM2 = 0.001
ADVISORY_MAX_TOLERANCE_MM = 0.1

_EVIDENCE_LIMIT = 20


@dataclass(frozen=True)
class Layer:
    name: str
    profile: str
    gates: bool
    description: str


LAYERS: tuple[Layer, ...] = (
    Layer("file_parse", "deliverable", True, "the file could be read as CAD"),
    Layer("kernel_load", "deliverable", True, "the kernel produced a non-empty shape"),
    Layer("brep_check", "deliverable", True, "no per-face, per-edge, or per-vertex kernel error"),
    Layer("shell_closure", "deliverable", True, "every shell is closed and no face is loose"),
    Layer("mesh_manifold", "deliverable", True,
          "the tessellation is a closed, orientable manifold with no pinch vertices"),
    Layer("structure", "deliverable", False, "solid, shell, and face structure"),
    Layer("advisory", "deliverable", False, "fragile geometry that still passes"),
)

_LAYERS_BY_NAME = {layer.name: layer for layer in LAYERS}
_LOADER_LAYERS = ("file_parse", "kernel_load")

# ``deliverable`` gates on every gating layer. ``kernel`` restores the
# pre-M71 contract (the kernel check alone) for intentional surfaces and
# sheet bodies; the skipped layers stay visible in the report.
PROFILES = ("deliverable", "kernel")
_KERNEL_PROFILE_SKIPS = ("shell_closure", "mesh_manifold")


def layer_by_name(name: str) -> Layer:
    return _LAYERS_BY_NAME[name]


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def validate_shape(
    topo_shape,
    *,
    profile: str = "deliverable",
    in_process: bool = False,
    mesh_parallel: bool | None = None,
    mesh_timeout_s: float | None = None,
    evidence_limit: int = _EVIDENCE_LIMIT,
    skip_layers: tuple[str, ...] = (),
    skip_reason: str | None = None,
    on_layer=None,
    shape_metrics: dict | None = None,
) -> dict:
    """Run every layer of ``profile`` on a loaded shape and return the report.

    ``on_layer(name, entry)`` is called as each layer entry is recorded, so a
    worker can persist partial evidence before a later layer stalls.
    ``shape_metrics`` is the caller's ``compute_metrics`` result for this
    same shape; the structure layer reuses its bounding box and volume for a
    single-solid shape instead of recomputing the tight box, which costs
    seconds on real imported parts.

    ``skip_layers`` names layers to leave unrun (status ``skipped``, with
    ``skip_reason`` as their message). A skipped gating layer leaves
    ``is_valid`` null: the report says nothing about that layer instead of
    implying it passed.
    """
    if profile not in PROFILES:
        raise ValueError(f"Unknown validation profile: {profile}")
    unknown_layers = set(skip_layers) - set(_LAYERS_BY_NAME)
    if unknown_layers:
        raise ValueError(f"Unknown validation layers: {sorted(unknown_layers)}")

    if mesh_parallel is None:
        mesh_parallel = os.environ.get(_DAEMON_CHILD_ENV) != "1"

    layers: dict[str, dict] = {name: {"status": "pass", "duration_ms": 0} for name in _LOADER_LAYERS}
    verdict_blocked_by: str | None = None
    undetermined: str | None = None
    skipped_gating: str | None = None

    for layer in LAYERS:
        if layer.name in _LOADER_LAYERS:
            continue
        if profile == "kernel" and layer.name in _KERNEL_PROFILE_SKIPS:
            layers[layer.name] = {"status": "skipped", "message": "Not run under the kernel profile."}
            continue
        if layer.name in skip_layers:
            layers[layer.name] = {"status": "skipped",
                                  "message": skip_reason or "Not run on this shape at the caller's request."}
            if layer.gates and skipped_gating is None:
                skipped_gating = layer.name
            continue
        if layer.gates and (verdict_blocked_by or undetermined):
            reason = verdict_blocked_by or undetermined
            layers[layer.name] = {"status": "skipped", "message": f"Not run after {reason} did not pass."}
            continue
        started = time.perf_counter()
        try:
            entry = _RUNNERS[layer.name](
                topo_shape,
                in_process=in_process,
                mesh_parallel=mesh_parallel,
                mesh_timeout_s=mesh_timeout_s,
                evidence_limit=evidence_limit,
                shape_metrics=shape_metrics,
            )
        except Exception as exc:  # a layer must never take the report down
            entry = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
        entry.setdefault("duration_ms", int(round((time.perf_counter() - started) * 1000)))
        layers[layer.name] = entry
        if on_layer is not None:
            on_layer(layer.name, entry)
        if layer.gates:
            if entry["status"] == "fail" and verdict_blocked_by is None:
                verdict_blocked_by = layer.name
            elif entry["status"] in ("timeout", "error") and undetermined is None:
                undetermined = layer.name

    if verdict_blocked_by is None and undetermined is None:
        undetermined = skipped_gating
    return _assemble(profile, layers, first_failure=verdict_blocked_by, undetermined=undetermined)


def round_trip_skip_layers(topo_shape) -> tuple[str, ...]:
    """Layers ``run`` leaves out when validating the in-memory source.

    Returns ``()`` for shapes at or below ``ROUND_TRIP_FULL_FACE_LIMIT`` faces,
    where every layer is cheap, and the expensive layers above it.
    """
    if _face_count(topo_shape) <= ROUND_TRIP_FULL_FACE_LIMIT:
        return ()
    return _ROUND_TRIP_EXPENSIVE_LAYERS


def load_failure_report(layer_name: str, message: str, *, profile: str = "deliverable") -> dict:
    """Report for a file that never became a shape (parse or kernel failure)."""
    layers: dict[str, dict] = {}
    for layer in LAYERS:
        if layer.name == layer_name:
            layers[layer.name] = {"status": "fail", "duration_ms": 0, "message": message}
        elif layer.name in _LOADER_LAYERS and layer.name < layer_name:
            layers[layer.name] = {"status": "pass", "duration_ms": 0}
        else:
            layers[layer.name] = {"status": "skipped", "message": f"Not run after {layer_name} failed."}
    if layer_name == "kernel_load":
        layers["file_parse"] = {"status": "pass", "duration_ms": 0}
    return _assemble(profile, layers, first_failure=layer_name, undetermined=None)


def _assemble(profile, layers, *, first_failure, undetermined) -> dict:
    if first_failure is not None:
        is_valid = False
    elif undetermined is not None:
        is_valid = None
    else:
        is_valid = True
    report = {
        "profile": profile,
        "is_valid": is_valid,
        "first_failure": first_failure,
        "undetermined_layer": undetermined,
        "layers": layers,
    }
    message, suggestion = _explain(report)
    report["message"] = message
    if suggestion:
        report["suggestion"] = suggestion
    from agentcad.validation_guidance import diagnostic_guidance, repair_guidance
    guidance = diagnostic_guidance(report)
    if guidance:
        report["guidance"] = guidance
        report["suggestion"] = " ".join(guidance["next_checks"])
    report["repairs"] = repair_guidance(report)
    return report


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


def _layer_brep_check(shape, *, evidence_limit=_EVIDENCE_LIMIT, **_) -> dict:
    from OCP.BRepCheck import BRepCheck_Analyzer
    from agentcad.metrics import extract_validity_errors
    from agentcad.native_io import suppress_native_output

    with suppress_native_output():
        analyzer = BRepCheck_Analyzer(shape)
        ok = analyzer.IsValid()
        errors = [] if ok else extract_validity_errors(analyzer, shape)
        entities = []
        if not ok:
            from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_WIRE
            from OCP.TopExp import TopExp_Explorer
            from agentcad.topo_ids import _indexed_map

            def statuses(entity):
                # IsValid(entity) is a cheap lookup; reading Status() through
                # the bindings costs milliseconds per entity, which on large
                # parts turned this evidence walk into minutes for one defect.
                if analyzer.IsValid(entity):
                    return set()
                result = analyzer.Result(entity)
                return {s.name for s in result.Status() if s.value != 0} if result else set()

            for kind, shape_type in (("face", TopAbs_FACE), ("edge", TopAbs_EDGE), ("vertex", TopAbs_VERTEX)):
                indexed = _indexed_map(shape, shape_type)
                for entity_id in range(1, indexed.Extent() + 1):
                    entity = indexed.FindKey(entity_id)
                    found = statuses(entity)
                    if kind == "face":
                        wires = TopExp_Explorer(entity, TopAbs_WIRE)
                        while wires.More():
                            found.update(statuses(wires.Current()))
                            wires.Next()
                    if found:
                        entities.append({"kind": kind, "id": entity_id, "errors": sorted(found)})
    return {"status": "pass" if ok else "fail", "errors": errors,
            "entities": entities[:evidence_limit], "entity_count": len(entities),
            "evidence_truncated": len(entities) > evidence_limit}


def _layer_shell_closure(shape, *, evidence_limit=_EVIDENCE_LIMIT, **_) -> dict:
    from OCP.BRepCheck import BRepCheck_NoError, BRepCheck_Shell
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import (
        TopTools_IndexedDataMapOfShapeListOfShape,
        TopTools_IndexedMapOfShape,
    )
    from agentcad.native_io import suppress_native_output

    with suppress_native_output():
        edge_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
        faces_in_shells = TopTools_IndexedMapOfShape()

        open_shells = []
        closed = 0
        exp = TopExp_Explorer(shape, TopAbs_SHELL)
        shell_index = 0
        while exp.More():
            shell_index += 1
            shell = TopoDS.Shell_s(exp.Current())
            TopExp.MapShapes_s(shell, TopAbs_FACE, faces_in_shells)
            if BRepCheck_Shell(shell).Closed() == BRepCheck_NoError:
                closed += 1
            else:
                open_shells.append((shell_index, shell))
            exp.Next()

        # Faces that belong to no shell at all (a bare face in a compound).
        all_faces = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_FACE, all_faces)
        loose_faces = [
            i for i in range(1, all_faces.Extent() + 1)
            if not faces_in_shells.Contains(all_faces.FindKey(i))
        ]

        free_edges = []
        seen = set()
        full_ancestry = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, full_ancestry)

        def collect_free_edges(container):
            ancestry = TopTools_IndexedDataMapOfShapeListOfShape()
            TopExp.MapShapesAndAncestors_s(container, TopAbs_EDGE, TopAbs_FACE, ancestry)
            for i in range(1, ancestry.Extent() + 1):
                edge = ancestry.FindKey(i)
                if ancestry.FindFromIndex(i).Extent() >= 2:
                    continue
                edge_id = edge_map.FindIndex(edge)
                if edge_id in seen:
                    continue
                seen.add(edge_id)
                record = _edge_record(TopoDS.Edge_s(edge), edge_id)
                record["face_ids"] = sorted({all_faces.FindIndex(face)
                    for face in full_ancestry.FindFromKey(edge)})
                free_edges.append(record)

        for _, shell in open_shells:
            collect_free_edges(shell)
        for face_id in loose_faces:
            collect_free_edges(all_faces.FindKey(face_id))

    ok = not open_shells and not loose_faces
    free_edges.sort(key=lambda e: e["id"])
    return {
        "status": "pass" if ok else "fail",
        "closed_shell_count": closed,
        "open_shell_count": len(open_shells),
        "loose_face_count": len(loose_faces),
        "loose_face_ids": loose_faces[:evidence_limit],
        "free_edge_count": len(free_edges),
        "free_edge_ids": [e["id"] for e in free_edges[:evidence_limit]],
        "free_edges": free_edges[:evidence_limit],
        "evidence_truncated": len(free_edges) > evidence_limit or len(loose_faces) > evidence_limit,
    }


def _layer_mesh_manifold(
    shape,
    *,
    in_process=False,
    mesh_parallel=True,
    mesh_timeout_s=None,
    evidence_limit=_EVIDENCE_LIMIT,
    **_,
) -> dict:
    deflection = deflection_for_shape(shape)
    if in_process or _face_count(shape) <= MESH_INPROCESS_FACE_LIMIT:
        entry = mesh_manifold_report(
            shape,
            deflection,
            evidence_limit=evidence_limit,
            parallel=mesh_parallel,
        )
        entry["worker"] = "in_process"
        return entry
    return bounded_mesh_manifold(shape, deflection, timeout_s=mesh_timeout_s, evidence_limit=evidence_limit)


def _face_count(shape) -> int:
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    faces = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, faces)
    return faces.Extent()


def _layer_structure(shape, *, evidence_limit=_EVIDENCE_LIMIT, shape_metrics=None, **_) -> dict:
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from agentcad.native_io import suppress_native_output

    def count(kind, container=shape):
        m = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(container, kind, m)
        return m, m.Extent()

    with suppress_native_output():
        solids, solid_count = count(TopAbs_SOLID)
        _, shell_count = count(TopAbs_SHELL)
        all_faces, face_count = count(TopAbs_FACE)
        _, edge_count = count(TopAbs_EDGE)
        faces_in_solids = TopTools_IndexedMapOfShape()
        for i in range(1, solid_count + 1):
            TopExp.MapShapes_s(solids.FindKey(i), TopAbs_FACE, faces_in_solids)
        loose = sum(
            1 for i in range(1, face_count + 1)
            if not faces_in_solids.Contains(all_faces.FindKey(i))
        )
    from agentcad.topo_ids import solid_entries
    if (
        solid_count == 1 and loose == 0 and shape_metrics
        and "bounding_box" in shape_metrics and "volume" in shape_metrics
    ):
        # One solid and nothing outside it: the solid's tight bounding box and
        # volume are the whole shape's, which the caller already computed.
        solids = [{"id": 1, "volume": shape_metrics["volume"],
                   "bbox": shape_metrics["bounding_box"]}]
    else:
        solids = solid_entries(shape, limit=evidence_limit)
    return {
        "status": "pass",
        "solid_count": solid_count,
        "shell_count": shell_count,
        "face_count": face_count,
        "edge_count": edge_count,
        "faces_outside_solids": loose,
        "solids": solids,
        "evidence_truncated": solid_count > evidence_limit,
    }


def _layer_advisory(shape, **_) -> dict:
    from OCP.BRep import BRep_Tool
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from agentcad.native_io import suppress_native_output

    min_area = math.inf
    max_tol = 0.0
    with suppress_native_output():
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            face = TopoDS.Face_s(exp.Current())
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            min_area = min(min_area, abs(props.Mass()))
            max_tol = max(max_tol, BRep_Tool.Tolerance_s(face))
            exp.Next()
        exp = TopExp_Explorer(shape, TopAbs_EDGE)
        while exp.More():
            max_tol = max(max_tol, BRep_Tool.Tolerance_s(TopoDS.Edge_s(exp.Current())))
            exp.Next()
        exp = TopExp_Explorer(shape, TopAbs_VERTEX)
        while exp.More():
            max_tol = max(max_tol, BRep_Tool.Tolerance_s(TopoDS.Vertex_s(exp.Current())))
            exp.Next()
    if min_area is math.inf:
        min_area = 0.0
    flags = []
    if min_area < ADVISORY_MIN_FACE_AREA_MM2:
        flags.append("sliver_face")
    if max_tol > ADVISORY_MAX_TOLERANCE_MM:
        flags.append("loose_tolerance")
    return {
        "status": "pass",
        "min_face_area_mm2": _round(min_area),
        "max_tolerance_mm": _round(max_tol, 6),
        "flags": flags,
    }


_RUNNERS = {
    "brep_check": _layer_brep_check,
    "shell_closure": _layer_shell_closure,
    "mesh_manifold": _layer_mesh_manifold,
    "structure": _layer_structure,
    "advisory": _layer_advisory,
}


# ---------------------------------------------------------------------------
# Mesh manifold check
# ---------------------------------------------------------------------------


def deflection_for_shape(shape) -> float:
    """Gate-compatible deflection: relative to the plain bounding-box diagonal.

    The downstream gate sizes its chord error from ``BRepBndLib.Add``, not
    the tight optimal box, and the mesher's success on marginal faces is
    deflection-sensitive, so the parity corpus needs the same number.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepTools import BRepTools

    BRepTools.Clean_s(shape)
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    if box.IsVoid():
        return DEFLECTION_MIN_MM
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    diagonal = math.dist((xmin, ymin, zmin), (xmax, ymax, zmax))
    return float(min(DEFLECTION_MAX_MM, max(DEFLECTION_MIN_MM, DEFLECTION_RELATIVE * diagonal)))


class _UnionFind:
    __slots__ = ("parent",)

    def __init__(self):
        self.parent: dict = {}

    def find(self, key):
        parent = self.parent
        root = key
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(key, key) != root:
            parent[key], key = root, parent[key]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


DEFLECTION_LADDER = (1, 4, 16, 32)
MAX_TRIANGLES = 1_000_000


def mesh_manifold_report(
    shape,
    deflection_mm: float,
    *,
    evidence_limit: int = _EVIDENCE_LIMIT,
    parallel: bool = True,
) -> dict:
    """Tessellate, stitch by shared topology, and check the closed-manifold rules.

    Vertices are merged only where the B-rep says they are the same entity:
    along shared edges through each face's polygon-on-triangulation, and at
    shared vertices. No coordinate tolerance is involved, so a defect in the
    topology becomes a defect in the mesh instead of being welded shut.

    The kernel mesher is fragile on marginal faces at any single deflection,
    so the check walks the same ladder the downstream gate uses: the
    requested deflection, then 4, 16, and 32 times finer. The first rung that
    yields a clean manifold passes and is recorded as ``ladder_divisor``; if
    none does, the finest rung's defect is reported. A rung whose mesh would
    exceed the triangle ceiling stops the ladder with that as the verdict.
    """
    last = None
    for divisor in DEFLECTION_LADDER:
        entry = _mesh_manifold_once(
            shape,
            deflection_mm / divisor,
            evidence_limit=evidence_limit,
            parallel=parallel,
        )
        entry["ladder_divisor"] = divisor
        entry["requested_deflection_mm"] = _round(deflection_mm, 6)
        if entry["status"] == "pass":
            return entry
        last = entry
        if entry.get("defect", {}).get("kind") == "triangle_ceiling":
            break
    return last


def _mesh_manifold_once(
    shape,
    deflection_mm: float,
    *,
    evidence_limit: int = _EVIDENCE_LIMIT,
    parallel: bool = True,
    _debug: dict | None = None,
) -> dict:
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_VERTEX
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from agentcad.native_io import suppress_native_output

    with suppress_native_output():
        BRepTools.Clean_s(shape)
        BRepMesh_IncrementalMesh(shape, deflection_mm, False, 0.5, parallel)

        face_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
        edge_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
        vertex_map = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_VERTEX, vertex_map)

        coords: dict = {}
        triangles: list = []
        edge_polygons: dict = {}
        vertex_nodes: dict = {}
        degenerate_nodes: list = []
        missing_faces: list = []

        for fi in range(1, face_map.Extent() + 1):
            face = TopoDS.Face_s(face_map.FindKey(fi))
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is None or tri.NbTriangles() == 0:
                missing_faces.append(fi)
                continue
            trsf = loc.Transformation()
            for k in range(1, tri.NbNodes() + 1):
                p = tri.Node(k).Transformed(trsf)
                coords[(fi, k)] = (p.X(), p.Y(), p.Z())
            flip = face.Orientation() == TopAbs_REVERSED
            for t in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(t).Get()
                if flip:
                    b, c = c, b
                triangles.append(((fi, a), (fi, b), (fi, c), fi))
            exp = TopExp_Explorer(face, TopAbs_EDGE)
            while exp.More():
                edge = TopoDS.Edge_s(exp.Current())
                poly = BRep_Tool.PolygonOnTriangulation_s(edge, tri, loc)
                if poly is not None and poly.NbNodes() > 0:
                    nodes = [(fi, poly.Node(i)) for i in range(1, poly.NbNodes() + 1)]
                    if BRep_Tool.Degenerated_s(edge):
                        # A degenerate edge is one point (cone apex, sphere
                        # pole); every node along it is that vertex.
                        degenerate_nodes.append(nodes)
                    edge_polygons.setdefault(edge_map.FindIndex(edge), []).append(nodes)
                    # Polygon nodes follow the edge's own parametrization, so
                    # pair them with the un-oriented first/last vertex.
                    v_first = vertex_map.FindIndex(TopExp.FirstVertex_s(edge, False))
                    v_last = vertex_map.FindIndex(TopExp.LastVertex_s(edge, False))
                    vertex_nodes.setdefault(v_first, []).append(nodes[0])
                    vertex_nodes.setdefault(v_last, []).append(nodes[-1])
                exp.Next()

    if len(triangles) > MAX_TRIANGLES:
        return {
            "status": "fail",
            "deflection_mm": _round(deflection_mm, 6),
            "triangle_count": len(triangles),
            "defect": {"kind": "triangle_ceiling", "count": len(triangles), "ceiling": MAX_TRIANGLES},
        }
    if missing_faces:
        return {
            "status": "fail",
            "deflection_mm": _round(deflection_mm, 6),
            "defect": {
                "kind": "missing_triangulation",
                "face_ids": missing_faces[:evidence_limit],
                "count": len(missing_faces),
            },
        }

    uf = _UnionFind()

    def dist2(a, b):
        return sum((x - y) ** 2 for x, y in zip(coords[a], coords[b]))

    for polys in edge_polygons.values():
        base = polys[0]
        for other in polys[1:]:
            if len(other) == len(base):
                # Pick the alignment with the smaller total distance. Comparing
                # endpoints alone is ambiguous on closed edges, whose first
                # and last nodes coincide.
                forward = sum(dist2(a, b) for a, b in zip(base, other))
                backward = sum(dist2(a, b) for a, b in zip(base, reversed(other)))
                if backward < forward:
                    other = list(reversed(other))
                for a, b in zip(base, other):
                    uf.union(a, b)
            else:
                # Discretization mismatch across faces: stitch each node to
                # its nearest counterpart on the base polygon.
                for b in other:
                    nearest = min(base, key=lambda a: dist2(a, b))
                    uf.union(nearest, b)
    for nodes in vertex_nodes.values():
        first = nodes[0]
        for other in nodes[1:]:
            uf.union(first, other)
    for nodes in degenerate_nodes:
        first = nodes[0]
        for other in nodes[1:]:
            uf.union(first, other)

    root_of: dict = {}

    def root(key):
        r = root_of.get(key)
        if r is None:
            r = uf.find(key)
            root_of[key] = r
        return r

    global_tris: list = []
    by_set: dict = {}
    for a, b, c, fi in triangles:
        ra, rb, rc = root(a), root(b), root(c)
        if ra == rb or rb == rc or ra == rc:
            continue
        index = len(global_tris)
        global_tris.append((ra, rb, rc, fi))
        by_set.setdefault(frozenset((ra, rb, rc)), []).append(index)

    # Opposite-winding duplicate pairs carry no surface; drop both.
    dropped = set()
    for indices in by_set.values():
        if len(indices) == 2:
            (a1, b1, c1, _), (a2, b2, c2, _) = global_tris[indices[0]], global_tris[indices[1]]
            cyc1 = {(a1, b1), (b1, c1), (c1, a1)}
            cyc2 = {(a2, b2), (b2, c2), (c2, a2)}
            if not (cyc1 & cyc2):
                dropped.update(indices)

    edge_uses: dict = {}
    for index, (a, b, c, fi) in enumerate(global_tris):
        if index in dropped:
            continue
        for u, v in ((a, b), (b, c), (c, a)):
            key = (u, v) if u < v else (v, u)
            edge_uses.setdefault(key, []).append((index, u < v))

    if _debug is not None:
        _debug.update(uf=uf, coords=coords, edge_polygons=edge_polygons, global_tris=global_tris,
                      edge_uses=edge_uses, dropped=dropped, vertex_nodes=vertex_nodes)
    live_count = len(global_tris) - len(dropped)
    vertex_count = len({r for t in global_tris for r in t[:3]})
    base = {
        "deflection_mm": _round(deflection_mm, 6),
        "triangle_count": live_count,
        "vertex_count": vertex_count,
    }
    if live_count == 0:
        return {**base, "status": "fail",
                "defect": {"kind": "empty_mesh", "count": len(triangles)}}

    def edge_defect(kind, key, uses):
        u, v = key
        mid = tuple((p + q) / 2 for p, q in zip(coords[u], coords[v]))
        # A mesh edge inside a face has no B-rep edge ID. Publish an empty
        # list there instead of guessing the nearest unrelated model edge.
        ids = sorted(mesh_edge_ids.get(frozenset((u, v)), set()))
        return {
            "kind": kind,
            "location": {"x": _round(mid[0]), "y": _round(mid[1]), "z": _round(mid[2])},
            "triangle_count": len(uses),
            "face_ids": sorted({global_tris[i][3] for i, _ in uses}),
            "edge_ids": ids,
            "endpoints": [dict(zip(("x", "y", "z"), map(_round, coords[p]))) for p in (u, v)],
        }

    mesh_edge_ids = {}
    for edge_id, polygons in edge_polygons.items():
        for nodes in polygons:
            for a, b in zip(nodes, nodes[1:]):
                mesh_edge_ids.setdefault(frozenset((root(a), root(b))), set()).add(edge_id)

    def failed_edges(kind, failures):
        defects = [edge_defect(kind, key, uses) for key, uses in failures[:evidence_limit]]
        first = edge_defect(kind, *failures[0])
        first["count"] = len(failures)
        return {**base, "status": "fail", "defect": first, "defects": defects,
                "evidence_truncated": len(failures) > evidence_limit}

    non_manifold = [(k, u) for k, u in edge_uses.items() if len(u) > 2]
    if non_manifold:
        return failed_edges("non_manifold_edge", non_manifold)

    open_edges = [(k, u) for k, u in edge_uses.items() if len(u) == 1]
    if open_edges:
        return failed_edges("open_edge", open_edges)

    inconsistent = [(k, u) for k, u in edge_uses.items() if len(u) == 2 and u[0][1] == u[1][1]]
    if inconsistent:
        return failed_edges("inconsistent_winding", inconsistent)

    pinch = _find_pinch_vertex(global_tris, dropped, edge_uses)
    if pinch is not None:
        vertex, faces = pinch
        p = coords[vertex]
        return {
            **base,
            "status": "fail",
            "defect": {
                "kind": "pinch_vertex",
                "location": {"x": _round(p[0]), "y": _round(p[1]), "z": _round(p[2])},
                "face_ids": sorted(faces),
                "vertex_ids": sorted(vid for vid, nodes in vertex_nodes.items()
                                     if any(root(node) == vertex for node in nodes)),
            },
        }

    return {**base, "status": "pass"}


def _find_pinch_vertex(global_tris, dropped, edge_uses):
    """A vertex whose incident triangles form more than one fan."""
    incident: dict = {}
    for index, (a, b, c, _) in enumerate(global_tris):
        if index in dropped:
            continue
        for v in (a, b, c):
            incident.setdefault(v, []).append(index)
    for vertex, tri_indices in incident.items():
        if len(tri_indices) < 4:
            continue
        uf = _UnionFind()
        for index in tri_indices:
            a, b, c, _ = global_tris[index]
            for u, w in ((a, b), (b, c), (c, a)):
                if vertex not in (u, w):
                    continue
                key = (u, w) if u < w else (w, u)
                for other, _ in edge_uses.get(key, ()):
                    uf.union(index, other)
        roots = {uf.find(index) for index in tri_indices}
        if len(roots) > 1:
            return vertex, {global_tris[i][3] for i in tri_indices}
    return None


def _validation_timeout_seconds(explicit: float | None) -> float | None:
    if explicit is not None:
        return None if explicit <= 0 else float(explicit)
    raw = os.environ.get(VALIDATION_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_VALIDATION_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_VALIDATION_TIMEOUT_S
    if not math.isfinite(value) or value < 0:
        return DEFAULT_VALIDATION_TIMEOUT_S
    return None if value == 0 else value


def validate_file(path, *, profile: str = "deliverable", evidence_limit: int = _EVIDENCE_LIMIT) -> dict:
    """Load a CAD file and validate it in this process; parse failures become a report."""
    from agentcad.native_io import suppress_native_output
    from agentcad.step_io import load_cad_shape

    started = time.perf_counter()
    try:
        with suppress_native_output():
            shape = load_cad_shape(path)
    except Exception as exc:
        report = load_failure_report("file_parse", f"{type(exc).__name__}: {exc}", profile=profile)
        report["timings"] = {"reload_ms": int(round((time.perf_counter() - started) * 1000))}
        return report
    reload_ms = int(round((time.perf_counter() - started) * 1000))
    started = time.perf_counter()
    report = validate_shape(shape, profile=profile, evidence_limit=evidence_limit)
    report["timings"] = {
        "reload_ms": reload_ms,
        "delivered_validation_ms": int(round((time.perf_counter() - started) * 1000)),
    }
    return report


WORKER_WAIT_HEARTBEAT_S = 5.0


def _run_worker(argv, budget_s, on_wait=None):
    """Run a worker under a budget; return (returncode, stderr_tail, timed_out).

    ``on_wait(elapsed_s)`` is called every ``WORKER_WAIT_HEARTBEAT_S`` while
    the worker is still running so the caller can show progress instead of
    silence.
    """
    started = time.perf_counter()
    process = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        text=True, env=_worker_env(),
    )
    next_beat = WORKER_WAIT_HEARTBEAT_S
    try:
        while True:
            remaining = budget_s - (time.perf_counter() - started)
            if remaining <= 0:
                process.kill()
                _, stderr = process.communicate()
                return process.returncode, (stderr or "").strip()[-400:], True
            try:
                _, stderr = process.communicate(timeout=min(0.25, max(remaining, 0.01)))
                return process.returncode, (stderr or "").strip()[-400:], False
            except subprocess.TimeoutExpired:
                elapsed = time.perf_counter() - started
                if on_wait is not None and elapsed >= next_beat:
                    on_wait(elapsed)
                    next_beat += WORKER_WAIT_HEARTBEAT_S
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def bounded_validate_file(
    path,
    *,
    profile: str = "deliverable",
    timeout_s: float | None = None,
    evidence_limit: int = _EVIDENCE_LIMIT,
    on_wait=None,
) -> dict:
    """Validate a written CAD file within a wall-clock budget.

    ``on_wait(elapsed_s)`` is called periodically while the worker runs.

    Small files (below ``VALIDATION_WORKER_MIN_BYTES``) and a disabled budget
    run in-process. Otherwise a worker process validates the file and streams
    each finished layer back; when the budget expires the worker is killed and
    the report keeps every finished layer, marks the running layer
    ``timeout``, and leaves the rest ``skipped``. A gating failure proven
    before the timeout still yields ``is_valid: false``; otherwise the verdict
    is null with ``undetermined_layer`` naming the stalled layer.
    """
    path = Path(path)
    budget = _validation_timeout_seconds(timeout_s)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if budget is None or size < VALIDATION_WORKER_MIN_BYTES:
        report = validate_file(path, profile=profile, evidence_limit=evidence_limit)
        report["worker"] = "in_process"
        return report

    with tempfile.TemporaryDirectory(prefix="agentcad-validate-") as temp:
        result = Path(temp) / "layers.jsonl"
        argv = [
            sys.executable, "-m", "agentcad.validation_worker",
            str(path), str(result), profile, str(evidence_limit),
        ]
        started = time.perf_counter()
        try:
            returncode, stderr_tail, timed_out = _run_worker(argv, budget, on_wait)
        except Exception as exc:
            return _worker_failure_report(profile, f"Could not start the validation worker: {exc}")
        elapsed_ms = int(round((time.perf_counter() - started) * 1000))
        lines = []
        if result.exists():
            for raw in result.read_text().splitlines():
                try:
                    lines.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    final = next((line["report"] for line in lines if "report" in line), None)
    if final is not None and not timed_out:
        final["worker"] = "subprocess"
        final["budget_s"] = budget
        return final
    partial = {line["layer"]: line["entry"] for line in lines if "layer" in line}
    if timed_out:
        return _assemble_partial(
            profile, partial, budget_s=budget, elapsed_ms=elapsed_ms,
            reason="timeout", detail=None,
        )
    detail = stderr_tail or f"exit status {returncode}"
    return _assemble_partial(
        profile, partial, budget_s=budget, elapsed_ms=elapsed_ms,
        reason="error", detail=f"Validation worker failed: {detail}",
    )


def _worker_failure_report(profile: str, message: str) -> dict:
    layers = {name: {"status": "error", "message": message} if name == "kernel_load"
              else {"status": "skipped", "message": "Not run after the validation worker failed."}
              for name in _LAYERS_BY_NAME}
    layers["file_parse"] = {"status": "pass", "duration_ms": 0}
    report = _assemble(profile, layers, first_failure=None, undetermined="kernel_load")
    report["worker"] = "subprocess"
    return report


def _assemble_partial(profile, partial, *, budget_s, elapsed_ms, reason, detail):
    """Build a report from the layers a worker finished before it stopped."""
    layers: dict[str, dict] = {}
    stalled = None
    first_failure = None
    for layer in LAYERS:
        if layer.name in partial:
            layers[layer.name] = partial[layer.name]
            if layer.gates and first_failure is None and partial[layer.name].get("status") == "fail":
                first_failure = layer.name
            continue
        if profile == "kernel" and layer.name in _KERNEL_PROFILE_SKIPS:
            layers[layer.name] = {"status": "skipped", "message": "Not run under the kernel profile."}
            continue
        if first_failure is not None and layer.gates:
            layers[layer.name] = {"status": "skipped", "message": f"Not run after {first_failure} did not pass."}
            continue
        if stalled is None:
            stalled = layer.name
            if reason == "timeout":
                layers[layer.name] = {
                    "status": "timeout", "budget_s": budget_s, "duration_ms": elapsed_ms,
                    "message": f"Validation exceeded its {budget_s:g}s budget during {layer.name}.",
                }
            else:
                layers[layer.name] = {"status": "error", "message": detail}
        else:
            layers[layer.name] = {"status": "skipped", "message": f"Not reached after {stalled} did not complete."}
    report = _assemble(
        profile, layers, first_failure=first_failure,
        undetermined=None if first_failure is not None else stalled,
    )
    report["worker"] = "subprocess"
    report["budget_s"] = budget_s
    if reason == "timeout":
        report["timed_out_layer"] = stalled
    return report


def _worker_env() -> dict:
    """Environment for worker processes: make this agentcad importable.

    The parent may run from a source checkout with a relative PYTHONPATH or
    from a directory the child will not share; the child must import the
    same package regardless of its working directory.
    """
    import agentcad

    package_root = str(Path(agentcad.__file__).resolve().parent.parent)
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    parts = [package_root] + [p for p in existing.split(os.pathsep) if p and p != package_root]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _mesh_timeout_seconds(explicit: float | None) -> float | None:
    if explicit is not None:
        return None if explicit <= 0 else float(explicit)
    raw = os.environ.get(MESH_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_MESH_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MESH_TIMEOUT_S
    if not math.isfinite(value) or value < 0:
        return DEFAULT_MESH_TIMEOUT_S
    return None if value == 0 else value


def bounded_mesh_manifold(shape, deflection_mm: float, *, timeout_s=None, evidence_limit=_EVIDENCE_LIMIT) -> dict:
    """Run the mesh layer in a subprocess with a wall-clock budget."""
    from OCP.BRepTools import BRepTools
    from agentcad.native_io import silence_native_stdout

    budget = _mesh_timeout_seconds(timeout_s)
    with tempfile.TemporaryDirectory(prefix="agentcad-mesh-") as temp:
        brep = Path(temp) / "shape.brep"
        result = Path(temp) / "result.json"
        with silence_native_stdout():
            written = BRepTools.Write_s(shape, str(brep))
        if not written:
            return {"status": "error", "worker": "subprocess",
                    "message": "Could not serialize the shape for the mesh worker."}
        argv = [
            sys.executable, "-m", "agentcad.mesh_validation_worker",
            str(brep), str(result), str(deflection_mm), str(evidence_limit),
        ]
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, timeout=budget, check=False, env=_worker_env(),
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "worker": "subprocess",
                "budget_s": budget,
                "deflection_mm": _round(deflection_mm, 6),
                "duration_ms": int(round((time.perf_counter() - started) * 1000)),
                "message": f"Mesh validation exceeded its {budget:g}s budget.",
            }
        except Exception as exc:
            return {"status": "error", "worker": "subprocess",
                    "message": f"Could not start the mesh worker: {exc}"}
        if completed.returncode != 0 or not result.exists():
            detail = (completed.stderr or "").strip()[-400:]
            return {"status": "error", "worker": "subprocess",
                    "message": f"Mesh worker failed: {detail or completed.returncode}"}
        try:
            entry = json.loads(result.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            return {"status": "error", "worker": "subprocess",
                    "message": f"Mesh worker returned an invalid result: {exc}"}
    entry["worker"] = "subprocess"
    entry["budget_s"] = budget
    return entry


# ---------------------------------------------------------------------------
# Explanations
# ---------------------------------------------------------------------------


def _explain(report: dict) -> tuple[str, str | None]:
    layers = report["layers"]
    first = report["first_failure"]
    if first is None and report["undetermined_layer"] is not None:
        name = report["undetermined_layer"]
        entry = layers[name]
        detail = entry.get("message") or entry["status"]
        return (
            f"Validation could not finish: {name.replace('_', ' ')} ended with {entry['status']} ({detail}). "
            "The shape was not classified as invalid.",
            "Rerun with a larger budget, or inspect the earlier layers, which did pass.",
        )
    if first is None:
        if report.get("profile") == "kernel":
            return ("The shape passed the kernel consistency check; shell closure and "
                    "mesh checks were not run under the kernel profile.", None)
        return ("The shape passed every deliverable validation layer.", None)
    entry = layers[first]
    if first == "structure":
        failures = [c for c in entry.get("expectations", []) if c["passed"] is False]
        detail = "; ".join(
            (f"part {c['part_id']}: " if 'part_id' in c else '') +
            f"{c['expectation']} expected {c['expected']}, got {c['actual']}" for c in failures)
        return (f"Structure expectation failed: {detail}.",
                "Inspect the per-solid volumes and bounds, then restore the intended body count.")
    if first == "file_parse":
        return (f"The file could not be read as CAD: {entry.get('message', '')}".strip(),
                "Re-export from the source tool. Do not repair CAD by editing the file text.")
    if first == "kernel_load":
        return (f"The kernel produced no usable shape: {entry.get('message', '')}".strip(),
                "Re-export the model; the file parsed but contains no geometry.")
    if first == "brep_check":
        errors = ", ".join(entry.get("errors") or []) or "unspecified kernel error"
        return (f"The kernel consistency check failed: {errors}.",
                "Repair the reported entities in the source geometry before retrying.")
    if first == "shell_closure":
        parts = []
        if entry.get("open_shell_count"):
            parts.append(f"{entry['open_shell_count']} open shell(s)")
        if entry.get("loose_face_count"):
            parts.append(f"{entry['loose_face_count']} loose face(s)")
        ids = entry.get("free_edge_ids") or []
        edge_text = f"; free edges {ids}" if ids else ""
        return (
            f"The shape is not watertight: {' and '.join(parts)} with {entry.get('free_edge_count', 0)} free edge(s){edge_text}.",
            "Close the profile before extruding, add the missing face, or remove the loose face. "
            "The listed free edges include their endpoints so you can see where the gap is.",
        )
    if first == "mesh_manifold":
        defect = entry.get("defect") or {}
        kind = defect.get("kind", "mesh defect")
        loc = defect.get("location")
        where = (f" near ({loc['x']}, {loc['y']}, {loc['z']})" if loc else "")
        faces = defect.get("face_ids") or []
        face_text = f" on faces {faces}" if faces else ""
        if kind == "non_manifold_edge":
            return (
                f"The tessellated surface has an edge shared by {defect.get('triangle_count')} triangles{where}{face_text}; "
                "a printer or grader cannot tell inside from outside there.",
                "Overlap the bodies by at least 0.01 mm before fusing, keep them as separate "
                "bodies with show_assembly([a, b]), or rebuild the profile without the self-crossing.",
            )
        if kind == "open_edge":
            return (
                f"The tessellated surface has {defect.get('count')} open edge(s){where}{face_text} although the B-rep shells report closed.",
                "Check the faces listed for gaps or tolerance mismatches at their shared edges; re-export or sew the shell.",
            )
        if kind == "pinch_vertex":
            return (
                f"Two surface sheets meet at a single point{where}{face_text}.",
                "Separate the bodies at that vertex or overlap them before fusing.",
            )
        if kind == "triangle_ceiling":
            return (f"The tessellation needs more than {defect.get('ceiling')} triangles at the gate deflection.",
                    "Simplify the model or its tiny features; downstream meshers apply the same ceiling.")
        if kind == "empty_mesh":
            return ("The tessellation produced no surface triangles.",
                    "The shape has no area to mesh; check that it contains real faces.")
        if kind == "missing_triangulation":
            return (
                f"{defect.get('count')} face(s) could not be tessellated: {defect.get('face_ids')}.",
                "Rebuild or simplify the listed faces; a downstream mesher will fail on them too.",
            )
        return (f"The tessellated surface failed the manifold check ({kind}){where}.", None)
    return (f"Validation failed at {first}.", None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _edge_record(edge, edge_id: int) -> dict:
    from agentcad.topo_ids import _edge_geometry, _xyz

    length, p_first, p_last = _edge_geometry(edge)
    return {"id": edge_id, "length": _round(length), "endpoints": [_xyz(p_first), _xyz(p_last)]}


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)
