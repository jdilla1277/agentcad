"""Compare measured CAD features against an explicit JSON spec."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from agentcad import file_detect
from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.commands.measure import measure_tier0_payload, validity_from_metrics


@click.command("check-spec")
@click.argument("file")
@click.argument("spec_file")
@click.option(
    "--no-daemon",
    is_flag=True,
    default=False,
    help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.",
)
def check_spec(file, spec_file, no_daemon):
    """Check a STEP/BREP file against a JSON feature spec.

    SPEC_FILE example:

      {"features":[{"name":"bolt_holes","type":"cylinder","diameter_mm":6,"count":4}]}

    A failed spec still means the command ran successfully: status remains
    "success" and the comparison result is in passed/matched_features.
    """
    argv = ["check-spec", file, spec_file]
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    spec_path = Path(spec_file)
    spec = _load_spec(spec_path)
    if spec is None:
        return

    file_path = Path(file)
    detection = file_detect.detect_file_type(file_path)
    category = detection["category"]

    if category == file_detect.MISSING:
        _emit({
            "command": "check-spec",
            "status": "error",
            "format_detected": None,
            "message": f"File '{file}' not found.",
            "suggestion": "Check the path and pass a STEP/STP/BREP file.",
        }, exit_code=1)
        return

    if category == file_detect.NOT_A_FILE:
        reason = detection.get("reason", "not_a_regular_file")
        _emit({
            "command": "check-spec",
            "status": "error",
            "format_detected": None,
            "message": f"Path '{file}' is not a regular file ({reason}).",
            "suggestion": "Pass a path to a STEP/STP/BREP file.",
        }, exit_code=1)
        return

    if category == file_detect.EMPTY:
        _emit({
            "command": "check-spec",
            "status": "empty",
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

    if category != file_detect.TIER0_BREP:
        _emit({
            "command": "check-spec",
            "status": "unsupported_format",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": "check-spec currently supports STEP/STP/BREP source geometry only.",
            "suggestion": "Convert to STEP/STP/BREP first, then run `agentcad check-spec`.",
        }, exit_code=1)
        return

    try:
        measurement = measure_tier0_payload(
            str(file_path.resolve()),
            detection,
            with_features=False,
        )
    except Exception as exc:
        _emit({
            "command": "check-spec",
            "status": "malformed",
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

    result = check_measurement_against_spec(measurement, spec)
    result.update({
        "command": "check-spec",
        "status": "success",
        "file": str(file_path.resolve()),
        "spec_file": str(spec_path.resolve()),
        "format_detected": detection.get("format"),
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "validity": validity_from_metrics(measurement["metrics"]),
        "metrics": measurement["metrics"],
        "next_actions": _next_actions(result["passed"], file_path, spec_path),
        "more_at": "agentcad docs check-spec",
    })
    _emit(result)
    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)


def check_measurement_against_spec(measurement: dict, spec: dict) -> dict:
    measured_cylinders = measurement.get("cylindrical_features", [])
    matched_features = []
    missing_features = []
    total_abs_count_error = 0

    for feature in spec["features"]:
        target = _target_from_feature(feature, spec)
        match = _find_cylinder_bucket(measured_cylinders, target)
        if match is None:
            missing = {
                "name": target["name"],
                "type": "cylinder",
                "target": target,
                "measured": None,
                "reason": "no_matching_cylinder_bucket",
            }
            missing_features.append(missing)
            total_abs_count_error += target["count"]
            continue

        count_error = int(match["count"]) - target["count"]
        diameter_error = _round_float(float(match["diameter_mm"]) - target["diameter_mm"])
        passed = abs(count_error) <= target["count_tolerance"]
        matched_features.append({
            "name": target["name"],
            "type": "cylinder",
            "target": target,
            "measured": {
                "diameter_mm": match["diameter_mm"],
                "count": match["count"],
                "axis": match["axis"],
            },
            "diameter_error_mm": diameter_error,
            "count_error": count_error,
            "passed": passed,
        })
        total_abs_count_error += abs(count_error)

    passed = not missing_features and all(f["passed"] for f in matched_features)
    return {
        "passed": passed,
        "matched_features": matched_features,
        "missing_features": missing_features,
        "total_abs_count_error": total_abs_count_error,
    }


def _load_spec(path: Path) -> dict | None:
    if not path.exists():
        _emit({
            "command": "check-spec",
            "status": "error",
            "message": f"Spec file '{path}' not found.",
            "suggestion": "Pass a JSON file with a top-level features array.",
        }, exit_code=1)
        return None

    if not path.is_file():
        _emit({
            "command": "check-spec",
            "status": "error",
            "message": f"Spec path '{path}' is not a regular file.",
            "suggestion": "Pass a JSON file with a top-level features array.",
        }, exit_code=1)
        return None

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        _emit({
            "command": "check-spec",
            "status": "invalid_spec",
            "message": f"Spec file '{path}' is not valid JSON: {exc.msg}.",
            "suggestion": "Fix the JSON syntax and retry.",
        }, exit_code=1)
        return None

    errors = _validate_spec(data)
    if errors:
        _emit({
            "command": "check-spec",
            "status": "invalid_spec",
            "message": "Spec file does not match the supported check-spec schema.",
            "errors": errors,
            "suggestion": (
                "Use features like "
                "{\"name\":\"bolt_holes\",\"type\":\"cylinder\","
                "\"diameter_mm\":12,\"count\":6}."
            ),
        }, exit_code=1)
        return None
    return data


def _validate_spec(data) -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return ["spec must be a JSON object"]

    features = data.get("features")
    if not isinstance(features, list):
        errors.append("features must be an array")
        return errors

    if not features:
        errors.append("features must contain at least one feature")
        return errors

    for idx, feature in enumerate(features):
        prefix = f"features[{idx}]"
        if not isinstance(feature, dict):
            errors.append(f"{prefix} must be an object")
            continue

        if not isinstance(feature.get("name"), str) or not feature["name"].strip():
            errors.append(f"{prefix}.name must be a non-empty string")

        if feature.get("type") != "cylinder":
            errors.append(f"{prefix}.type must be 'cylinder'")

        if not _is_number(feature.get("diameter_mm")) or feature["diameter_mm"] <= 0:
            errors.append(f"{prefix}.diameter_mm must be a positive number")

        if not _is_int(feature.get("count")) or feature["count"] < 0:
            errors.append(f"{prefix}.count must be a non-negative integer")

        axis = feature.get("axis")
        if axis is not None and axis not in {"+x", "+y", "+z", "other"}:
            errors.append(f"{prefix}.axis must be one of +x, +y, +z, other")

        diameter_tolerance = feature.get(
            "diameter_tolerance_mm",
            data.get("diameter_tolerance_mm", 0.1),
        )
        if not _is_number(diameter_tolerance) or diameter_tolerance < 0:
            errors.append(
                f"{prefix}.diameter_tolerance_mm must be a non-negative number"
            )

        count_tolerance = feature.get(
            "count_tolerance",
            data.get("count_tolerance", 0),
        )
        if not _is_int(count_tolerance) or count_tolerance < 0:
            errors.append(f"{prefix}.count_tolerance must be a non-negative integer")

    return errors


def _target_from_feature(feature: dict, spec: dict) -> dict:
    return {
        "name": feature["name"],
        "diameter_mm": float(feature["diameter_mm"]),
        "count": int(feature["count"]),
        "axis": feature.get("axis"),
        "diameter_tolerance_mm": float(
            feature.get("diameter_tolerance_mm", spec.get("diameter_tolerance_mm", 0.1))
        ),
        "count_tolerance": int(
            feature.get("count_tolerance", spec.get("count_tolerance", 0))
        ),
    }


def _find_cylinder_bucket(measured_cylinders: list[dict], target: dict) -> dict | None:
    candidates = []
    for cylinder in measured_cylinders:
        diameter_error = abs(float(cylinder["diameter_mm"]) - target["diameter_mm"])
        if diameter_error > target["diameter_tolerance_mm"]:
            continue
        if target["axis"] is not None and cylinder.get("axis") != target["axis"]:
            continue
        count_error = abs(int(cylinder["count"]) - target["count"])
        candidates.append((diameter_error, count_error, cylinder))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _next_actions(passed: bool, file_path: Path, spec_path: Path) -> list[str]:
    if passed:
        return [
            "record this check-spec result with the completed model",
            f"agentcad measure {file_path} — inspect the measured inventory if you need details",
        ]
    return [
        "revise the CAD so each missing or failed feature matches the spec",
        f"agentcad check-spec {file_path} {spec_path} — rerun after revision",
    ]


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
        "command": "check-spec",
        "status": "malformed",
        "format_detected": actual,
        "expected_format": expected,
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "message": message,
        "suggestion": suggestion,
    }, exit_code=1)


def _emit(payload: dict, exit_code: int = 0) -> None:
    click.echo(json.dumps(payload))
    if exit_code != 0:
        sys.exit(exit_code)


def _round_float(value: float) -> float:
    return round(float(value), 6)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
