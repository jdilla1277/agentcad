"""Compare measured CAD features against an explicit JSON spec."""

from __future__ import annotations

import json
import math
import shlex
import sys
import time
from pathlib import Path

import click
from agentcad.project import project_options

from agentcad import file_detect
from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.commands.measure import measure_tier0_payload, validity_from_metrics
from agentcad.feature_checks import (
    check_positioned_cylinders,
    check_probe,
    normalize_box,
    validate_positions,
)


@click.command("check-spec")
@click.argument("file")
@click.argument("spec_file", required=False)
@click.option("--complete", "complete", is_flag=True, default=False,
              help="WIP, requires --experimental-changes: complete deferred checks and settle still-current automatic acceptance; stages stay unaccepted.")
@click.option(
    "--no-daemon",
    is_flag=True,
    default=False,
    help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.",
)
@project_options
def check_spec(file, spec_file, no_daemon, complete=False):
    """Check a STEP/BREP file against a JSON feature spec.

    SPEC_FILE example:

      {"features":[
        {"name":"bolt_holes","type":"cylinder","diameter_mm":6,"count":4},
        {"name":"mounts","type":"positioned_cylinders","axis":"z","diameter_mm":3,
         "positions_mm":[[3,3],[25.17,3]]},
        {"name":"usb","type":"keepout","box_mm":[10,-2,0,18,0,5]}
      ]}

    A failed spec still means the command ran successfully: status remains
    "success" and the comparison result is in passed/matched_features.
    With a retained revision ID, omit SPEC_FILE to read its pinned receipt;
    --complete performs deferred checks and settles saved automatic intent.
    Staged candidates still require explicit promotion. Reads are read-only.
    """
    if file.startswith("r_") and not Path(file).exists():
        from agentcad.change_store import ChangeStore
        from agentcad.project import get_project

        if spec_file:
            _emit({
                "command": "check-spec", "status": "error",
                "message": "A retained revision uses its pinned requirements; create a successor to change them.",
            }, exit_code=1)
            return
        store = ChangeStore(get_project().build_root)
        started_ns = time.time_ns()
        try:
            from agentcad.change_promotion import PromotionStore
            # Read the operation first: a committed acceptance then necessarily
            # has completed evidence. Pending operation + newer evidence is safe.
            automatic = PromotionStore(store.root).automatic_fields(file) if not complete else {}
            checked = store.complete_checks(file) if complete else {**store.read_checks(file), **automatic}
            receipt = checked["receipt"]
            saved = store.read(file)
            geometry = next((
                str(store.root / a["path"])
                for a in (saved["candidate"] or {}).get("inputs", []) if a["role"] == "geometry"
            ), None)
            warnings = []
            if complete:
                try:
                    from agentcad.working_viewer import prepare
                    prepare(store.root, file)
                except (OSError, ValueError, RuntimeError) as exc:
                    warnings.append(f"Checks saved, but candidate view could not update: {exc}")
            if checked.get("current_advanced"):
                accepted = checked["accepted_version_after"]
                try:
                    from agentcad.project_viewer import record_failure
                    record_failure(store.root, f"v{accepted['version']} · {accepted['label']}",
                                   "accepted_without_viewer", started_ns=started_ns)
                except (OSError, TimeoutError) as exc:
                    warnings.append(f"Accepted, but live viewer status could not update: {exc}")
            root_option = " --build-dir " + shlex.quote(str(store.root)) if get_project().configured else ""
            notes = ["Check results and the promotion decision are separate: passing checks accept only a still-active normal-run intent. Staged or superseded work stays unaccepted. Reads never change current."]
            actions = []
            if checked.get("current_advanced"):
                step = shlex.quote(get_project().response_path(store.root / checked["outputs"]["step"]))
                actions = [f"agentcad view {step}{root_option} — inspect the accepted model"]
            elif checked.get("promotion", {}).get("outcome") == "not_promoted":
                notes.append("The final promotion receipt preserves evidence at refusal time, which may differ from the latest check receipt. Repeating --complete cannot reopen that decision; reconcile the selected revisions before a new attempt.")
                actions = [f"agentcad context{root_option} — inspect the working and accepted revisions before a new attempt"]
            _emit({
                "command": "check-spec", "status": "success", **checked,
                "validation_state": receipt["validation_state"] if receipt else "pending",
                "passed": receipt["promotion_ready"] if receipt else False,
                "current_advanced": checked.get("current_advanced", False), "geometry": geometry,
                "notes": notes,
                **({"warnings": warnings} if warnings else {}),
                **({"next_actions": actions} if actions else {}),
                "more_at": "agentcad docs check-spec",
            })
        except Exception as exc:
            _emit({
                "command": "check-spec", "status": "error", "message": str(exc),
                "revision_id": file,
                "suggestion": "Use a returned revision ID and its selected build directory. Retry --complete to recover checks or an interrupted automatic acceptance; it never executes CAD again.",
            }, exit_code=1)
        return
    if complete or spec_file is None:
        _emit({
            "command": "check-spec", "status": "error",
            "message": "Use FILE SPEC_FILE, or a retained REVISION_ID with optional --complete.",
        }, exit_code=1)
        return
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
            precise_cylinders=_needs_precise_cylinders(spec),
            probe_boxes=_declared_probe_boxes(spec),
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
        "validity": validity_from_metrics(
            measurement["metrics"], measurement.get("validation")
        ),
        "metrics": measurement["metrics"],
        "next_actions": _next_actions(result["passed"], file_path, spec_path),
        "more_at": "agentcad docs check-spec",
    })
    _emit(result)
    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)


def check_measurement_against_spec(measurement: dict, spec: dict) -> dict:
    from agentcad.validation_guidance import structure_checks, with_structure_checks
    report = measurement.get("validation")
    checks = structure_checks(report["layers"].get("structure", {}) if report else {}, spec)
    if report and checks:
        from agentcad.core_build import apply_validation
        report = with_structure_checks(report, checks)
        measurement["validation"] = report
        apply_validation(measurement["metrics"], report)
    measured_cylinders = measurement.get("cylindrical_features", [])
    matched_features = []
    missing_features = []
    total_abs_count_error = 0

    for feature in spec.get("features", []):
        feature_type = feature.get("type")
        if feature_type == "cylinder":
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
        elif feature_type == "positioned_cylinders":
            target = _positioned_cylinders_target_from_feature(feature, spec)
            cylinders = measurement.get("precise_cylinders")
            if cylinders is None:
                missing_features.append({
                    "name": target["name"],
                    "type": "positioned_cylinders",
                    "target": target,
                    "measured": None,
                    "reason": "precise_cylinder_evidence_missing",
                })
                total_abs_count_error += len(target["positions_mm"])
                continue

            try:
                passed, observed = check_positioned_cylinders(
                    cylinders,
                    axis=target["axis"],
                    diameter_mm=target["diameter_mm"],
                    diameter_tolerance_mm=target["diameter_tolerance_mm"],
                    positions_mm=target["positions_mm"],
                    position_tolerance_mm=target["position_tolerance_mm"],
                )
            except ValueError as exc:
                missing_features.append({
                    "name": target["name"],
                    "type": "positioned_cylinders",
                    "target": target,
                    "measured": None,
                    "reason": str(exc),
                })
                total_abs_count_error += len(target["positions_mm"])
                continue

            count_error = observed["measured_count"] - observed["expected_count"]
            total_abs_count_error += abs(count_error)
            matched_features.append({
                "name": target["name"],
                "type": "positioned_cylinders",
                "target": target,
                "measured": observed,
                "count_error": count_error,
                "max_position_error_mm": observed["max_position_error_mm"],
                "passed": passed,
            })
        elif feature_type in ("keepout", "material"):
            target = _probe_target_from_feature(feature, spec)
            rows = measurement.get("material_volumes")
            if rows is None:
                missing_features.append({
                    "name": target["name"],
                    "type": feature_type,
                    "target": target,
                    "measured": None,
                    "reason": "material_probe_evidence_missing",
                })
                continue

            threshold = (
                target["max_volume_mm3"]
                if feature_type == "keepout"
                else target["min_volume_mm3"]
            )
            try:
                passed, observed = check_probe(
                    rows,
                    kind=feature_type,
                    box_mm=target["box_mm"],
                    threshold_mm3=threshold,
                )
            except ValueError as exc:
                missing_features.append({
                    "name": target["name"],
                    "type": feature_type,
                    "target": target,
                    "measured": None,
                    "reason": str(exc),
                })
                continue
            matched_features.append({
                "name": target["name"],
                "type": feature_type,
                "target": target,
                "measured": observed,
                "volume_margin_mm3": _round_float(
                    threshold - observed["volume_mm3"]
                    if feature_type == "keepout"
                    else observed["volume_mm3"] - threshold
                ),
                "passed": passed,
            })
        else:
            missing_features.append({
                "name": feature.get("name"),
                "type": feature_type,
                "target": None,
                "measured": None,
                "reason": "unsupported_feature_type",
            })

    passed = (not missing_features and all(f["passed"] for f in matched_features)
              and all(c["passed"] is True for c in checks))
    return {
        "passed": passed,
        **({"structure_checks": checks} if checks else {}),
        **({"validation": report, "is_valid": report["is_valid"]} if report else {}),
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
                "\"diameter_mm\":12,\"count\":6}, "
                "{\"name\":\"mounts\",\"type\":\"positioned_cylinders\",\"axis\":\"z\","
                "\"diameter_mm\":3,\"positions_mm\":[[3,3],[25.17,3]]}, or "
                "{\"name\":\"usb_keepout\",\"type\":\"keepout\","
                "\"box_mm\":[10.275,-2.3,0,17.895,-0.3,4.65]}."
            ),
        }, exit_code=1)
        return None
    return data


def _declared_probe_boxes(spec: dict) -> list:
    return [
        feature["box_mm"]
        for feature in spec.get("features", [])
        if feature.get("type") in ("keepout", "material")
    ]


def _needs_precise_cylinders(spec: dict) -> bool:
    return any(
        feature.get("type") == "positioned_cylinders"
        for feature in spec.get("features", [])
    )


def _validate_spec(data) -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return ["spec must be a JSON object"]

    for key in ("expect_solids", "expect_shells"):
        if key in data and (type(data[key]) is not int or data[key] < 0):
            errors.append(f"{key} must be a non-negative integer")
    for key in (
        "diameter_tolerance_mm",
        "position_tolerance_mm",
        "keepout_max_volume_mm3",
    ):
        if key in data and (not _is_number(data[key]) or data[key] < 0):
            errors.append(f"{key} must be a non-negative number")
    has_structure = any(key in data for key in ("expect_solids", "expect_shells"))
    features = data.get("features", [] if has_structure else None)
    if not isinstance(features, list):
        errors.append("features must be an array")
        return errors

    if not features and not has_structure:
        errors.append("features must contain at least one feature")
        return errors

    for idx, feature in enumerate(features):
        prefix = f"features[{idx}]"
        if not isinstance(feature, dict):
            errors.append(f"{prefix} must be an object")
            continue

        if not isinstance(feature.get("name"), str) or not feature["name"].strip():
            errors.append(f"{prefix}.name must be a non-empty string")

        feature_type = feature.get("type")
        if not isinstance(feature_type, str) or feature_type not in {
            "cylinder",
            "positioned_cylinders",
            "keepout",
            "material",
        }:
            errors.append(
                f"{prefix}.type must be one of cylinder, positioned_cylinders, "
                "keepout, material"
            )
            continue

        if feature_type == "cylinder":
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
        elif feature_type == "positioned_cylinders":
            axis = feature.get("axis")
            if axis not in {"x", "y", "z", "+x", "+y", "+z"}:
                errors.append(f"{prefix}.axis must be one of x, y, z")
            if not _is_number(feature.get("diameter_mm")) or feature["diameter_mm"] <= 0:
                errors.append(f"{prefix}.diameter_mm must be a positive number")

            try:
                validate_positions(
                    feature.get("positions_mm"),
                    f"{prefix}.positions_mm",
                )
            except ValueError as exc:
                errors.append(str(exc))

            diameter_tolerance = feature.get(
                "diameter_tolerance_mm",
                data.get("diameter_tolerance_mm", 0.1),
            )
            if not _is_number(diameter_tolerance) or diameter_tolerance < 0:
                errors.append(
                    f"{prefix}.diameter_tolerance_mm must be a non-negative number"
                )

            position_tolerance = feature.get(
                "position_tolerance_mm",
                data.get("position_tolerance_mm", 0.1),
            )
            if not _is_number(position_tolerance) or position_tolerance < 0:
                errors.append(
                    f"{prefix}.position_tolerance_mm must be a non-negative number"
                )
        else:
            try:
                normalize_box(feature.get("box_mm"))
            except ValueError as exc:
                errors.append(f"{prefix}.box_mm is invalid: {exc}")
            if feature_type == "keepout":
                maximum = feature.get(
                    "max_volume_mm3",
                    data.get("keepout_max_volume_mm3", 0.0),
                )
                if not _is_number(maximum) or maximum < 0:
                    errors.append(
                        f"{prefix}.max_volume_mm3 must be a non-negative number"
                    )
            else:
                minimum = feature.get("min_volume_mm3")
                if not _is_number(minimum) or minimum <= 0:
                    errors.append(
                        f"{prefix}.min_volume_mm3 must be a positive number"
                    )

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


def _positioned_cylinders_target_from_feature(feature: dict, spec: dict) -> dict:
    return {
        "name": feature["name"],
        "axis": feature["axis"],
        "diameter_mm": float(feature["diameter_mm"]),
        "positions_mm": validate_positions(
            feature["positions_mm"], "positions_mm"
        ),
        "diameter_tolerance_mm": float(
            feature.get("diameter_tolerance_mm", spec.get("diameter_tolerance_mm", 0.1))
        ),
        "position_tolerance_mm": float(
            feature.get(
                "position_tolerance_mm", spec.get("position_tolerance_mm", 0.1)
            )
        ),
    }


def _probe_target_from_feature(feature: dict, spec: dict) -> dict:
    target = {
        "name": feature["name"],
        "kind": feature["type"],
        "box_mm": list(normalize_box(feature["box_mm"])),
    }
    if feature["type"] == "keepout":
        target["max_volume_mm3"] = float(
            feature.get(
                "max_volume_mm3", spec.get("keepout_max_volume_mm3", 0.0)
            )
        )
    else:
        target["min_volume_mm3"] = float(feature["min_volume_mm3"])
    return target


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
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
