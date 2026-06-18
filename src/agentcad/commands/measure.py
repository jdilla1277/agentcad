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
            str(file_path.resolve()),
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
        from agentcad.metrics import compute_metrics
        from agentcad.step_io import load_cad_shape
        from agentcad import topo_ids

        topo_shape = load_cad_shape(file_path)
        metrics = compute_metrics(topo_shape)
        feature_summary = topo_ids.summary_entries(topo_shape)
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

    payload = {
        "command": "measure",
        "status": "success",
        "file": file_path,
        "format_detected": detection.get("format"),
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "metrics": metrics,
        "feature_summary": feature_summary,
        "next_actions": [
            "read metrics.dimensions and feature_summary — use the included "
            "measurement fields before switching to inspect",
            f"agentcad inspect {file_path} --ids — get feature IDs for targeted edits",
        ],
        "more_at": "agentcad docs measure",
    }

    if with_features:
        payload["features"] = {
            "solids": topo_ids.solid_entries(topo_shape),
            "faces": topo_ids.face_entries(topo_shape),
            "edges": topo_ids.edge_entries(topo_shape),
        }

    _emit(payload)
