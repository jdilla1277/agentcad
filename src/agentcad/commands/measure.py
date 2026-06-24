"""Dimensional measurements for STEP/BREP files."""

import json
import sys
from pathlib import Path

import click

from agentcad import file_detect
from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)


@click.command("measure")
@click.argument("file")
@click.option(
    "--features",
    "with_features",
    is_flag=True,
    help="Include full per-solid, per-face, and per-edge measurement lists. "
         "Default output stays compact with overall metrics and feature_summary.",
)
@click.option(
    "--no-daemon",
    is_flag=True,
    default=False,
    help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.",
)
def measure(file, with_features, no_daemon):
    """Measure dimensions and feature sizes in a STEP/BREP file."""
    argv = ["measure", file]
    if with_features:
        argv.append("--features")
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    file_path = Path(file)
    detection = file_detect.detect_file_type(file_path)
    category = detection["category"]

    if category == file_detect.MISSING:
        _emit({
            "command": "measure", "status": "error",
            "format_detected": None,
            "message": f"File '{file}' not found.",
            "suggestion": "Check the path and pass a STEP/STP/BREP file.",
        }, exit_code=1)
        return

    if category == file_detect.NOT_A_FILE:
        reason = detection.get("reason", "not_a_regular_file")
        _emit({
            "command": "measure", "status": "error",
            "format_detected": None,
            "message": f"Path '{file}' is not a regular file ({reason}).",
            "suggestion": "Pass a path to a STEP/STP/BREP file.",
        }, exit_code=1)
        return

    if category == file_detect.EMPTY:
        _emit({
            "command": "measure", "status": "empty",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": 0,
            "message": f"File '{file}' is empty.",
            "suggestion": "Re-export from your CAD tool; the file may be a failed write.",
        }, exit_code=1)
        return

    if category == file_detect.MALFORMED:
        _emit_malformed(file, detection)
        return

    if category == file_detect.UNKNOWN_FORMAT:
        _emit({
            "command": "measure", "status": "unknown_format",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"agentcad doesn't recognize extension '{detection.get('extension')}'."
            ),
            "suggestion": "Supported measurement formats: .step .stp .brep.",
        }, exit_code=0)
        return

    if category == file_detect.DISPLAY_FORMAT:
        fmt = detection["format"]
        _emit({
            "command": "measure", "status": "display_format",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"{fmt.upper()} is a display/export format, not source CAD geometry."
            ),
            "suggestion": "Measure the source STEP/STP/BREP file instead.",
        }, exit_code=0)
        return

    if category == file_detect.TIER2_RECOGNIZED:
        fmt = detection["format"]
        _emit({
            "command": "measure", "status": "recognized_deferred",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"Format '{fmt}' is recognized but not measurable in agentcad's v0 scope."
            ),
            "suggestion": "Convert to STEP/STP/BREP first, then run `agentcad measure`.",
        }, exit_code=0)
        return

    if category == file_detect.TIER1_MESH:
        _emit({
            "command": "measure", "status": "limited",
            "format_detected": detection["format"],
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "editable": False,
            "message": (
                "STL is a triangle mesh; agentcad measure reports B-rep dimensions "
                "from STEP/STP/BREP source geometry."
            ),
            "suggestion": "Re-export the model as STEP/STP/BREP for semantic measurements.",
        }, exit_code=0)
        return

    if category == file_detect.TIER0_BREP:
        _measure_tier0(
            str(file_path.absolute()),
            detection,
            with_features=with_features,
        )
        maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
        return

    _emit({
        "command": "measure", "status": "error",
        "format_detected": None,
        "message": f"Unhandled file category '{category}'.",
    }, exit_code=1)


def _emit(payload: dict, exit_code: int = 0) -> None:
    click.echo(json.dumps(payload))
    if exit_code != 0:
        sys.exit(exit_code)


def _emit_malformed(file: str, detection: dict) -> None:
    expected = detection.get("expected_format")
    actual = detection.get("format")
    if actual == "html":
        message = (
            f"Extension is .{expected} but content is HTML — likely a failed download "
            "(error page saved instead of the CAD file)."
        )
        suggestion = "Re-download the file; verify the source URL returns the CAD file, not HTML."
    else:
        message = (
            f"Extension claims '{expected}' but content looks like '{actual}'. "
            "File appears mislabeled or corrupted."
        )
        suggestion = f"Re-export as {expected} from your CAD tool, or rename to match the real format."
    _emit({
        "command": "measure", "status": "malformed",
        "format_detected": actual,
        "expected_format": expected,
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "message": message,
        "suggestion": suggestion,
    }, exit_code=1)


def _measure_tier0(file_path: str, detection: dict, *, with_features: bool) -> None:
    try:
        payload = measure_tier0_payload(
            file_path,
            detection,
            with_features=with_features,
        )
    except Exception as exc:
        _emit({
            "command": "measure", "status": "malformed",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"Recognized as {detection.get('format')} but the parser couldn't "
                f"measure it: {exc}. The file may be incomplete or corrupted."
            ),
            "suggestion": "Re-export from your CAD tool; the file may be incomplete or corrupted.",
        }, exit_code=1)
        return

    _emit(payload)


def measure_tier0_payload(
    file_path: str,
    detection: dict,
    *,
    with_features: bool = False,
) -> dict:
    from agentcad.metrics import compute_metrics
    from agentcad.step_io import load_cad_shape
    from agentcad import topo_ids

    topo_shape = load_cad_shape(file_path, format_hint=detection.get("format"))
    metrics = compute_metrics(topo_shape)
    feature_summary = topo_ids.summary_entries(topo_shape)
    cylindrical_features = _cylindrical_features(topo_shape)

    payload = {
        "command": "measure",
        "status": "success",
        "file": file_path,
        "format_detected": detection.get("format"),
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "metrics": metrics,
        "validity": {"is_valid": metrics["is_valid"]},
        "cylindrical_features": cylindrical_features,
        "feature_summary": feature_summary,
        "next_actions": [
            "read metrics.dimensions and feature_summary — use the included "
            "measurement fields before switching to inspect",
            f"agentcad inspect {file_path} --ids — get feature IDs for targeted edits",
        ],
        "more_at": "agentcad docs measure",
    }

    # Flag high-risk edit inputs (invalid/large topology) so the agent gets
    # workflow guidance before committing to fragile load_step() + boolean
    # edits. measure doesn't compute free_edge_count (expensive), so risk is
    # classified on face/edge counts + validity only.
    from agentcad.edit_risk import classify_edit_risk
    risk = classify_edit_risk(
        face_count=metrics["face_count"],
        edge_count=metrics["edge_count"],
        is_valid=metrics["is_valid"],
        validity_errors=metrics.get("validity_errors"),
    )
    if risk:
        payload.update(risk)
        # The default next_actions point at inspect --ids for targeted edits;
        # on a high-risk input that contradicts the recommended_workflow.
        # Keep the next step coherent with the risk guidance.
        if risk["edit_risk"] == "high":
            payload["next_actions"] = [
                "read metrics + validity — this input is high edit-risk",
                "follow recommended_workflow before attempting any edit; do "
                "not start with load_step() + boolean/fillet",
            ]

    if with_features:
        payload["features"] = {
            "solids": topo_ids.solid_entries(topo_shape),
            "faces": topo_ids.face_entries(topo_shape),
            "edges": topo_ids.edge_entries(topo_shape),
        }

    return payload


def _cylindrical_features(topo_shape, *, precision: float = 0.1) -> list[dict]:
    """Return neutral cylindrical feature buckets.

    Split cylindrical faces on the same physical hole/boss are deduped by
    diameter, canonical axis direction, and the axis location's perpendicular
    offset from the origin. Slice 1 deliberately reports neutral cylinders
    instead of claiming hole vs boss.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    unique: dict[
        tuple[float, tuple[float, float, float], tuple[float, float, float]],
        dict,
    ] = {}

    explorer = TopExp_Explorer(topo_shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        try:
            adaptor = BRepAdaptor_Surface(face)
            if adaptor.GetType() == GeomAbs_Cylinder:
                cylinder = adaptor.Cylinder()
                axis = cylinder.Axis()
                direction = axis.Direction()
                canonical_direction = _canonical_direction(direction, precision)
                axis_point = _canonical_axis_point(
                    axis.Location(), direction, precision
                )
                diameter = _round_to(cylinder.Radius() * 2.0, precision)
                key = (diameter, canonical_direction, axis_point)
                unique.setdefault(key, {
                    "diameter_mm": diameter,
                    "axis": _axis_label(canonical_direction),
                    "direction": _tuple_xyz(canonical_direction),
                    "center": _tuple_xyz(axis_point),
                })
        except Exception:
            pass
        explorer.Next()

    buckets: dict[tuple[float, str, tuple[float, float, float]], dict] = {}
    for item in unique.values():
        direction_tuple = _dict_tuple(item["direction"])
        key = (item["diameter_mm"], item["axis"], direction_tuple)
        bucket = buckets.setdefault(key, {
            "diameter_mm": item["diameter_mm"],
            "count": 0,
            "axis": item["axis"],
            "representative_centers": [],
        })
        if item["axis"] == "other":
            bucket["direction"] = item["direction"]
        bucket["count"] += 1
        bucket["representative_centers"].append(item["center"])

    result = list(buckets.values())
    for bucket in result:
        bucket["representative_centers"].sort(
            key=lambda p: (p["x"], p["y"], p["z"])
        )
    result.sort(key=lambda b: (b["diameter_mm"], b["axis"]))
    return result


def _round_to(value: float, precision: float) -> float:
    return round(round(float(value) / precision) * precision, 6)


def _canonical_direction(direction, precision: float) -> tuple[float, float, float]:
    coords = [float(direction.X()), float(direction.Y()), float(direction.Z())]
    for value in coords:
        if abs(value) > 1e-9:
            if value < 0:
                coords = [-v for v in coords]
            break
    return tuple(_round_to(v, precision) for v in coords)


def _canonical_axis_point(location, direction, precision: float) -> tuple[float, float, float]:
    p = [float(location.X()), float(location.Y()), float(location.Z())]
    d = [float(direction.X()), float(direction.Y()), float(direction.Z())]
    dot = sum(a * b for a, b in zip(p, d))
    perp = [p[i] - dot * d[i] for i in range(3)]
    return tuple(_round_to(v, precision) for v in perp)


def _axis_label(direction: tuple[float, float, float]) -> str:
    axes = (
        ("+x", (1.0, 0.0, 0.0)),
        ("+y", (0.0, 1.0, 0.0)),
        ("+z", (0.0, 0.0, 1.0)),
    )
    for label, axis in axes:
        if sum(a * b for a, b in zip(direction, axis)) > 0.99:
            return label
    return "other"


def _tuple_xyz(values: tuple[float, float, float]) -> dict:
    return {"x": values[0], "y": values[1], "z": values[2]}


def _dict_tuple(values: dict) -> tuple[float, float, float]:
    return (values["x"], values["y"], values["z"])
