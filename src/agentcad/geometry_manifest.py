"""Immutable geometry inventories and selectors bound to normalized STEP bytes."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import shlex
from copy import deepcopy
from pathlib import Path

from agentcad.versioning import atomic_write_json

MANIFEST_NAME = "geometry_manifest.json"
SCHEMA_VERSION = 1


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _kernel_version() -> str:
    return importlib.metadata.version("cadquery-ocp")


def _inventory(shape) -> dict:
    from agentcad.topo_ids import solid_entries, face_entries, edge_entries

    return {"solid": solid_entries(shape), "face": face_entries(shape), "edge": edge_entries(shape)}


def create_manifest(step_path: Path, version: int, *, source: dict) -> dict:
    """Issue IDs from a reloaded normalized document, never source traversal."""
    from agentcad.step_assembly import read_step_assembly

    step_path = Path(step_path)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("Geometry version must be a positive integer.")
    destination = step_path.with_name(MANIFEST_NAME)
    if destination.exists():
        raise ValueError("Geometry manifest already exists; issued identities cannot be overwritten.")
    document = read_step_assembly(step_path)
    inventory = _inventory(document.shape)
    entities = {}
    for kind, entries in inventory.items():
        for entry in entries:
            object_id = f"{kind}_v{version}_{entry['id']}"
            entities[object_id] = {
                **entry, "id": object_id, "kind": kind,
                "index": entry["id"], "units": "mm",
                "coordinate_frame": "assembly", "occurrence_ids": [],
            }
    from agentcad.topo_ids import _bbox, _solid_volume
    definitions = {}
    for i, definition in enumerate(document.definitions, 1):
        object_id = f"part_v{version}_{i}"
        definitions[definition.source_id] = object_id
        entities[object_id] = {
            "id": object_id, "kind": "part", "definition_kind": definition.kind,
            "document_label": definition.source_id, "name": definition.name,
            "name_status": definition.name_status,
            "metadata_origin": source["assembly_metadata"],
            "bbox": _bbox(definition.shape), "volume": _solid_volume(definition.shape),
            "units": "mm", "coordinate_frame": "definition_local",
            "occurrence_ids": [],
        }
    occurrences = {o.source_id: f"occurrence_v{version}_{i}" for i, o in enumerate(document.occurrences, 1)}
    for occurrence in document.occurrences:
        object_id = occurrences[occurrence.source_id]
        members = {
            "solid": [f"solid_v{version}_{i}" for i in occurrence.solid_ids],
            "face": [f"face_v{version}_{i}" for i in occurrence.face_ids],
            "edge": [f"edge_v{version}_{i}" for i in occurrence.edge_ids],
        }
        entities[object_id] = {
            "id": object_id, "kind": "occurrence",
            "part_id": definitions[occurrence.definition_id],
            "parent_id": occurrences.get(occurrence.parent_id),
            "document_path": occurrence.source_id,
            "name": occurrence.name, "name_status": occurrence.name_status,
            "placement": occurrence.placement, "units": "mm",
            "coordinate_frame": "assembly",
            "mapping_status": "baseline_exact" if occurrence.mapping_status == "source_exact" else occurrence.mapping_status,
            "members": members, "metadata_origin": source["assembly_metadata"],
            "children": [],
        }
        entities[definitions[occurrence.definition_id]]["occurrence_ids"].append(object_id)
        for ids in members.values():
            for member in ids:
                entities[member]["occurrence_ids"].append(object_id)
    for occurrence_id in occurrences.values():
        parent = entities[occurrence_id]["parent_id"]
        if parent:
            entities[parent]["children"].append(occurrence_id)
    payload = {
        "schema_version": SCHEMA_VERSION, "version": version,
        "baseline": {"file": step_path.name, "sha256": hashlib.sha256(step_path.read_bytes()).hexdigest()},
        "kernel_version": _kernel_version(), "source": source,
        "hierarchy_status": document.hierarchy_status,
        "recognition": {"status": "not_available"},
        "counts": {kind + "s": len(inventory[kind]) for kind in ("solid", "face", "edge")},
        "entities": entities,
    }
    payload["counts"].update({
        "part_definitions": sum(d.kind == "part" for d in document.definitions),
        "assembly_definitions": sum(d.kind == "assembly" for d in document.definitions),
        "part_occurrences": sum(o.mapping_status != "assembly" for o in document.occurrences),
        "assembly_occurrences": sum(o.mapping_status == "assembly" for o in document.occurrences),
    })
    payload["manifest_id"] = _digest(payload)
    try:
        atomic_write_json(destination, payload, overwrite=False)
    except FileExistsError as exc:
        raise ValueError("Geometry manifest already exists; issued identities cannot be overwritten.") from exc
    return payload


def read_manifest(step_path: str | Path) -> dict:
    """Validate both saved identity data and the exact baseline it addresses."""
    step_path = Path(step_path)
    path = step_path.with_name(MANIFEST_NAME)
    try:
        payload = json.loads(path.read_text())
        if payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported geometry manifest schema; re-import the source.")
        manifest_id = payload.pop("manifest_id")
        if _digest(payload) != manifest_id:
            raise ValueError("Geometry manifest integrity check failed; restore the original manifest.")
        payload["manifest_id"] = manifest_id
        baseline = payload["baseline"]
        if baseline["file"] != step_path.name or hashlib.sha256(step_path.read_bytes()).hexdigest() != baseline["sha256"]:
            raise ValueError("Baseline does not match its geometry manifest; use the original version or re-import.")
        if payload["kernel_version"] != _kernel_version():
            raise ValueError("Geometry manifest uses another CAD kernel version; re-import before using its selectors.")
        return payload
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read geometry manifest for {step_path.name}: {exc}. Import the geometry to issue IDs.") from exc


def record(payload: dict, object_id: str) -> dict:
    match = re.fullmatch(r"(part|occurrence|solid|face|edge)_v([1-9]\d*)_([1-9]\d*)", object_id)
    if match is None:
        raise ValueError("Use a typed ID from this version's geometry manifest, such as face_v1_1.")
    if int(match[2]) != payload["version"]:
        raise ValueError(f"ID {object_id} belongs to another version; this baseline is v{payload['version']}.")
    if object_id not in payload["entities"]:
        raise ValueError(f"Unknown geometry ID {object_id}; inspect this baseline's geometry manifest.")
    return deepcopy(payload["entities"][object_id])


def summary(step_path: str | Path, payload: dict) -> dict:
    roots = sorted(
        (e["id"] for e in payload["entities"].values() if e["kind"] == "occurrence" and e["parent_id"] is None),
        key=lambda value: int(value.rsplit("_", 1)[1]),
    )
    sample_ids = {
        kind: next((e["id"] for e in payload["entities"].values() if e["kind"] == kind), None)
        for kind in ("face", "edge")
    }
    return {
        "path": str(Path(step_path).with_name(MANIFEST_NAME)),
        "manifest_id": payload["manifest_id"], "version": payload["version"],
        "baseline_sha256": payload["baseline"]["sha256"], "counts": payload["counts"],
        "recognition": payload["recognition"],
        "root_occurrence_ids": roots[:10], "root_occurrences_total": len(roots),
        "sample_ids": sample_ids,
        "lookup_example": f"agentcad inspect {shlex.quote(str(step_path))} --id {roots[0] if roots else sample_ids['face']}",
        "image_example": f"agentcad inspect {shlex.quote(str(step_path))} --image",
    }


class BaselineGeometry:
    """Selection context for one verified baseline, independent of later edits.

    Selectors always return geometry from this context's original baseline.
    Use ``shape`` as the starting Part, then re-inspect any newly saved version.
    """

    def __init__(self, step_path: str | Path):
        from agentcad.step_assembly import read_step_assembly
        from agentcad.topo_ids import _indexed_map
        from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_SOLID

        self._manifest = read_manifest(step_path)
        document = read_step_assembly(step_path)
        # A kernel/parser change must never silently reassign an issued ID.
        expected = {kind: [] for kind in ("solid", "face", "edge")}
        for entry in self._manifest["entities"].values():
            if entry["kind"] in expected:
                expected[entry["kind"]].append({k: v for k, v in entry.items() if k not in {"kind", "index", "units", "coordinate_frame", "occurrence_ids"}} | {"id": entry["index"]})
        for entries in expected.values():
            entries.sort(key=lambda e: e["id"])
        if _inventory(document.shape) != expected:
            raise ValueError("Reloaded geometry no longer matches the issued ID mapping; re-import before selecting.")
        self._shape = _flat_part(document.shape).wrapped
        if _inventory(self._shape) != expected:
            raise ValueError("Build123d baseline container changed the issued ID mapping; re-import before selecting.")
        self._maps = {
            "face": _indexed_map(self._shape, TopAbs_FACE),
            "edge": _indexed_map(self._shape, TopAbs_EDGE),
            "solid": _indexed_map(self._shape, TopAbs_SOLID),
        }

    @property
    def manifest(self) -> dict:
        return deepcopy(self._manifest)

    @property
    def shape(self):
        from build123d import Part

        # Give callers a distinct container value so moving their Part cannot
        # move this context's original baseline and invalidate cached maps.
        return Part(self._shape.Located(self._shape.Location()))

    def record(self, object_id: str) -> dict:
        return record(self._manifest, object_id)

    def pick_face(self, object_id: str):
        from build123d import Face
        from OCP.TopoDS import TopoDS

        return Face(TopoDS.Face_s(self._pick(object_id, "face")))

    def pick_edge(self, object_id: str):
        from build123d import Edge
        from OCP.TopoDS import TopoDS

        return Edge(TopoDS.Edge_s(self._pick(object_id, "edge")))

    def image_shape(self, object_id: str):
        """Raw image target in assembly coordinates, using issued memberships.

        A part definition shows all its placed occurrences. An occurrence
        shows only that instance and its descendants. This is a visualization
        helper; shared topology does not promise an exclusive edit selector.
        """
        from OCP.BRep import BRep_Builder
        from OCP.TopoDS import TopoDS_Compound
        from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
        from agentcad.topo_ids import _indexed_map

        entry = self.record(object_id)
        if entry["kind"] in self._maps:
            return self._maps[entry["kind"]].FindKey(entry["index"])
        pending = [object_id] if entry["kind"] == "occurrence" else list(entry["occurrence_ids"])
        members = {kind: set() for kind in self._maps}
        visited = set()
        while pending:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            occurrence = self._manifest["entities"][current]
            pending.extend(occurrence["children"])
            for kind, values in occurrence["members"].items():
                members[kind].update(self._manifest["entities"][value]["index"] for value in values)
        target = TopoDS_Compound()
        builder = BRep_Builder()
        builder.MakeCompound(target)
        for index in sorted(members["solid"]):
            builder.Add(target, self._maps["solid"].FindKey(index))
        # Keep loose faces and wires too, without drawing solid boundaries twice.
        for kind, topology in (("face", TopAbs_FACE), ("edge", TopAbs_EDGE)):
            covered = _indexed_map(target, topology)
            for index in sorted(members[kind]):
                raw = self._maps[kind].FindKey(index)
                if not covered.Contains(raw):
                    builder.Add(target, raw)
        return target

    def _pick(self, object_id: str, kind: str):
        entry = self.record(object_id)
        if entry["kind"] != kind:
            raise ValueError(f"Expected a {kind} ID, got {object_id}.")
        if len(entry["occurrence_ids"]) > 1:
            raise ValueError("This topology is shared by multiple occurrences; an exclusive instance selection is unavailable.")
        return self._maps[kind].FindKey(entry["index"])


def load_geometry(step_path: str | Path) -> BaselineGeometry:
    """Load an imported baseline and its checked, typed-ID selection context."""
    from agentcad.api import _record_loaded_file

    _record_loaded_file(str(step_path))
    return BaselineGeometry(step_path)


def _flat_part(shape):
    """Keep every leaf shape while removing containers that hide Part.volume."""
    from build123d import Compound, Part
    from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_COMPSOLID
    from OCP.TopoDS import TopoDS_Iterator

    def leaves(raw):
        if raw.ShapeType() in (TopAbs_COMPOUND, TopAbs_COMPSOLID):
            children = TopoDS_Iterator(raw)
            while children.More():
                yield from leaves(children.Value())
                children.Next()
        else:
            yield Compound.cast(raw)

    return Part(Compound(list(leaves(shape))).wrapped)
