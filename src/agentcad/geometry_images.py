"""Lazy four-view images whose targets are the issued baseline geometry IDs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile

from agentcad.geometry_manifest import BaselineGeometry, _digest, read_manifest, record
from agentcad.versioning import atomic_write_json

IMAGE_REVISION = "geometry-image-v3"
IMAGE_SIZE = 1024
IMAGE_VIEWS = ("iso", "top", "front", "right")


def _response(result, png, metadata):
    # Camera evidence stays available on disk without filling the agent's
    # response with four copies of every projected label.
    return {**{key: value for key, value in result.items() if key != "projections"},
            "png": str(png), "metadata": str(metadata)}


def _anchor(shape):
    """Find an actual target point, even for an annular or concave face."""
    from agentcad.topo_ids import _bbox
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    bbox = _bbox(shape)
    center = [sum(bbox[axis]) / 2 for axis in ("x", "y", "z")]
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*center)).Vertex()
    distance = BRepExtrema_DistShapeShape(vertex, shape)
    if not distance.IsDone() or distance.NbSolution() < 1:
        raise ValueError("Cannot locate a point on the requested image target.")
    point = distance.PointOnShape2(1)
    return [point.X(), point.Y(), point.Z()]


def _bounds(shape):
    from agentcad.topo_ids import _bbox

    bbox = _bbox(shape)
    return {"bbox": bbox, "dimensions": {
        axis: round(bbox[axis][1] - bbox[axis][0], 6) for axis in ("x", "y", "z")
    }}


def _overall_labels(saved):
    from agentcad.inspection_samples import select_samples

    plan = select_samples(saved)
    return [entry["id"] for entry in plan["parts"] + plan["features"]]


def geometry_image(step_path, output_root, *, object_id=None, isolate=False, _session=None):
    """Verify the baseline on every call; cache only a checked derived image."""
    step_path = Path(step_path)
    saved = read_manifest(step_path)
    if object_id is not None:
        record(saved, object_id)
    if isolate and object_id is None:
        raise ValueError("--isolate requires --id so the isolated target is explicit.")
    mode = "isolated" if isolate else "highlighted" if object_id else "overall"
    key = {
        "manifest_id": saved["manifest_id"], "baseline_sha256": saved["baseline"]["sha256"],
        "renderer": IMAGE_REVISION, "object_id": object_id, "mode": mode,
        "size": [IMAGE_SIZE, IMAGE_SIZE], "views": list(IMAGE_VIEWS),
    }
    directory = Path(output_root) / "inspection"
    png = directory / f"{object_id or 'overall'}_{mode}_{_digest(key)[:16]}.png"
    metadata = png.with_suffix(".json")
    try:
        cached = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            raise ValueError("Image metadata is not an object.")
        checksum = cached.pop("metadata_sha256")
        if (cached["request"] == key and _digest(cached) == checksum
                and hashlib.sha256(png.read_bytes()).hexdigest() == cached["png_sha256"]):
            return _response(cached, png, metadata)
    except (OSError, ValueError, KeyError, TypeError):
        pass

    context = (_session or {}).get("context")
    if context is None or context.manifest_id != saved["manifest_id"]:
        context = BaselineGeometry(step_path)
        if _session is not None:
            _session["context"] = context
    overall = context.shape.wrapped
    target = context.image_shape(object_id) if object_id else None
    frame = target if isolate else overall
    ids = [object_id] if object_id else _overall_labels(saved)
    annotations = [{"id": value, "point": _anchor(context.image_shape(value))} for value in ids]
    bounds = _bounds(frame)
    part_scope = "all placed occurrences" if object_id and record(saved, object_id)["kind"] == "part" else None
    result = {
        "request": key, "coordinate_frame": "assembly", "units": "mm",
        "model": _bounds(overall), "target": _bounds(target) if target is not None else None,
        "frame": bounds, "annotations": annotations,
        "labels": {"returned": len(ids), "inventory_total": len(saved["entities"]),
                   "scope": "selected_target" if object_id else "sample",
                   "complete": bool(object_id) or len(ids) == len(saved["entities"])},
        "highlight_style": "orange target shown through gray model" if target is not None and not isolate else None,
        "part_scope": part_scope,
    }
    from agentcad.render import render_geometry_sheet
    from agentcad.native_io import silence_native_stdout

    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".render-", dir=directory) as temp:
        temporary = Path(temp) / "sheet.png"
        with silence_native_stdout():
            result["projections"] = render_geometry_sheet(
                frame, temporary, annotations=annotations, target=None if isolate else target,
                dimensions=bounds["dimensions"],
                title=(f"{object_id} | ALL PLACED OCCURRENCES" if part_scope else object_id) or f"MODEL v{saved['version']}",
            )
        result["png_sha256"] = hashlib.sha256(temporary.read_bytes()).hexdigest()
        # Reject an input altered while rendering instead of publishing it as
        # an image of the original baseline.
        if read_manifest(step_path)["manifest_id"] != saved["manifest_id"]:
            raise ValueError("Baseline changed during image rendering; retry with the original version.")
        os.replace(temporary, png)
        atomic_write_json(metadata, {**result, "metadata_sha256": _digest(result)})
    return _response(result, png, metadata)
