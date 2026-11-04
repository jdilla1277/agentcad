"""Caller-supplied budgets for complete local inspection response delivery."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from functools import wraps
import hashlib
import json
import math
from pathlib import Path
import shlex

import click

from agentcad.inspection_report import compact_report, _estimated_tokens
from agentcad.inspection_snapshots import SnapshotStore, SnapshotResourceLimit
from agentcad.project import ProjectError, format_response, get_project

RESERVE = 32
OPTIONS = {
    "context_window": "--context-window", "response_budget": "--response-budget",
    "response_text_budget": "--response-text-budget", "image_budget": "--image-budget",
    "vision_profile": "--vision-profile",
}


def estimate(payload):
    """Match the JSON actually emitted by CLI and MCP, including spaces/escapes."""
    return math.ceil(len(json.dumps(payload).encode("utf-8")) / 3)


def policy_values(**values):
    if not any(value is not None for value in values.values()):
        return None
    for key in OPTIONS.keys() - {"vision_profile"}:
        value = values.get(key)
        if value is not None and (type(value) is not int or value < (0 if key == "image_budget" else 1)):
            raise ValueError(f"{key} must be {'nonnegative' if key == 'image_budget' else 'positive'} integer tokens.")
    profile = values.get("vision_profile")
    if isinstance(profile, str):
        try:
            profile = json.loads(profile)
        except ValueError as exc:
            raise ValueError("vision_profile must be JSON with name and tokens_per_image.") from exc
    if profile is not None:
        if (not isinstance(profile, dict) or set(profile) != {"name", "tokens_per_image"}
                or not isinstance(profile["name"], str) or not profile["name"].strip()
                or len(profile["name"]) > 80 or type(profile["tokens_per_image"]) is not int
                or profile["tokens_per_image"] < 1):
            raise ValueError("vision_profile needs only name (1..80 characters) and positive integer tokens_per_image for one 1024-square sheet.")
    return {**values, "vision_profile": profile}


def delivery_options(function):
    @click.option("--context-window", type=click.IntRange(min=1), default=None, help="Caller context tokens; derive a 5% combined response target unless --response-budget overrides it.")
    @click.option("--response-budget", type=click.IntRange(min=1), default=None, help="Combined estimated token limit for the complete JSON response and inline images.")
    @click.option("--response-text-budget", type=click.IntRange(min=1), default=None, help="Estimated token limit for complete JSON, including metadata/jobs. Defaults to the combined limit if known, otherwise 2000.")
    @click.option("--image-budget", type=click.IntRange(min=0), default=None, help="Estimated inline image-token limit. Without a vision profile, constrained images are references only.")
    @click.option("--vision-profile", default=None, help='Caller estimate as JSON: {"name":"host","tokens_per_image":1000} for each 1024-square sheet.')
    @wraps(function)
    def wrapped(*args, **kwargs):
        values = {key: kwargs.pop(key, None) for key in OPTIONS}
        try:
            policy = policy_values(**values)
        except ValueError as exc:
            raise ProjectError("invalid_delivery_budget", str(exc), "Use agentcad docs editing for inspection budget controls.") from exc
        if policy is None:
            return function(*args, **kwargs)
        ctx = click.get_current_context()
        if ctx.command.name == "inspect" and not kwargs.get("with_report"):
            raise click.UsageError("Caller delivery budgets require --report.")
        try:
            source = str(Path(kwargs["file"]).resolve())
        except (OSError, ValueError, RuntimeError) as exc:
            raise ProjectError("invalid_delivery_source", str(exc), "Use a readable local CAD path.") from exc
        ctx.meta["inspection_delivery_policy"] = policy
        ctx.meta["inspection_delivery_source"] = source
        ctx.meta["inspection_delivery_argument"] = kwargs["file"]
        return function(*args, **kwargs)
    return wrapped


def append_options(args, values):
    for key, option in OPTIONS.items():
        value = (values or {}).get(key)
        if value is not None:
            args.extend([option, json.dumps(value) if key == "vision_profile" and isinstance(value, dict) else str(value)])
    return args


def forward_options(args):
    return append_options(args, click.get_current_context().meta.get("inspection_delivery_policy"))


def _report(payload):
    if isinstance(payload.get("inspection"), dict):
        return payload["inspection"]
    if "media_summary" in payload and "inventory" in payload:
        return payload
    return None


def _canonical_action(action, layout, baseline, origin, source_argument):
    lexer = shlex.shlex(action, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        program, verb, operand = (lexer.get_token() for _ in range(3))
        if program != "agentcad" or verb not in {"inspect", "measure", "view"} or not operand:
            return action
        target = (Path(origin) / operand).resolve()
        if target not in {Path(baseline).resolve(), (Path(origin) / source_argument).resolve()}:
            return action
        # Capture the original suffix before reading flags, retaining quoted
        # arguments and human annotations exactly. Only the operand changes.
        suffix = action[lexer.instream.tell():].lstrip()
        has_build_root = False
        for token in lexer:
            if token == "—":
                break
            if token == "--build-dir" or token.startswith("--build-dir="):
                has_build_root = True
                break
    except (OSError, ValueError, RuntimeError):
        return action
    command = f"agentcad {verb} {shlex.quote(str(target))}"
    if not has_build_root:
        command += " --build-dir " + shlex.quote(str(layout.build_root))
    return command + (" " + suffix if suffix else "")


def _canonical(payload, layout, baseline, origin, source_argument):
    result = format_response(payload, replace(layout, source="command"))
    report = _report(result)
    if report:
        for key in ("manifest", "report", "bundle", "image_root"):
            if key in report["inventory"]:
                report["inventory"][key] = str((Path(origin) / report["inventory"][key]).resolve())
    # Known CAD commands remain executable from another caller directory.
    for container in (result, report):
        if not container or "next_actions" not in container:
            continue
        container["next_actions"] = [
            _canonical_action(action, layout, baseline, origin, source_argument)
            for action in container["next_actions"]
        ]
    return result


def save_snapshot(payload, layout, ctx):
    previous = ctx.meta.get("inspection_delivery_snapshot")
    if previous:
        return previous
    step = (payload.get("outputs") or {}).get("step") or ctx.meta["inspection_delivery_source"]
    baseline = str(Path(step).resolve())
    body = {
        "schema_version": 1, "project_root": str(layout.project_root), "build_root": str(layout.build_root),
        "baseline": baseline,
        "baseline_sha256": hashlib.sha256(Path(baseline).read_bytes()).hexdigest() if Path(baseline).is_file() else None,
        "exit_code": ctx.meta.get("command_exit_code", ctx.meta.get("import_exit_code", ctx.meta.get("inspection_exit_code", 0))),
        "response_path_base": str(Path.cwd()), "source_argument": ctx.meta["inspection_delivery_argument"],
        "payload": deepcopy(payload),
    }
    expected = (payload.get("geometry_manifest") or {}).get("baseline_sha256") or (_report(payload) or {}).get("baseline_sha256")
    if expected is not None and expected != body["baseline_sha256"]:
        raise ValueError("Baseline changed before response snapshot publication.")
    reference = SnapshotStore(layout).publish(body)
    reference["retrieve"] = shlex.join(["agentcad", "inspect", baseline, "--report", "--response-snapshot", reference["id"],
                                       "--build-dir", str(layout.build_root)])
    return reference


def read_snapshot(snapshot_id, file, layout):
    body, reference = SnapshotStore(layout).read(snapshot_id)
    if (body["project_root"] != str(layout.project_root) or body["build_root"] != str(layout.build_root)
            or body["baseline"] != str(Path(file).resolve())):
        raise ValueError("Response snapshot belongs to another baseline or project/build root.")
    if body["baseline_sha256"] is not None and hashlib.sha256(Path(file).read_bytes()).hexdigest() != body["baseline_sha256"]:
        raise ValueError("Baseline changed since this response snapshot; restore the original geometry.")
    ctx = click.get_current_context()
    reference["retrieve"] = shlex.join(["agentcad", "inspect", body["baseline"], "--report", "--response-snapshot", snapshot_id,
                                       "--build-dir", str(layout.build_root)])
    ctx.meta["inspection_delivery_snapshot"] = reference
    materialized = deepcopy(body["payload"])
    original_report = _report(materialized)
    original_budget = original_report["budget"]["text_tokens"] if original_report else None
    if original_report:
        original_report["budget"]["text_tokens"] = 2**63
    payload = _canonical(materialized, layout, body["baseline"], body["response_path_base"], body["source_argument"])
    report = _report(payload)
    if report:
        report["budget"].update(original_text_tokens=original_budget, snapshot_retrieval=True,
                                text_tokens=max(original_budget, _estimated_tokens(report) + 64))
        compact = compact_report(report)
        if report is payload:
            payload.update(compact)
        else:
            payload["inspection"] = compact
    payload["snapshot_command"] = payload["command"]
    payload["command"] = "inspect"
    payload["historical_response"] = True
    if "current_advanced" in payload:
        payload["recorded_current_advanced"] = payload["current_advanced"]
        payload["current_advanced"] = False
    payload["response_snapshot"] = reference
    return payload, body["exit_code"]


def _keep(data, keys):
    return {key: value for key, value in data.items() if key in keys}


def _summarize(payload):
    """Keep decisions and their evidence references ahead of optional detail."""
    if "validation" in payload:
        payload["validation"] = _keep(payload["validation"], {"profile", "is_valid", "first_failure", "undetermined_layer"})
    if "metrics" in payload:
        payload["metrics"] = _keep(payload["metrics"], {"dimensions", "volume", "surface_area", "face_count", "edge_count", "is_valid", "reliable"})
    if "outputs" in payload:
        payload["outputs"] = _keep(payload["outputs"], {"step", "geometry_manifest", "inspection_report", "inspection_bundle", "inspection_image"})
    if isinstance(payload.get("geometry_manifest"), dict):
        payload["geometry_manifest"] = _keep(payload["geometry_manifest"], {"manifest_id", "baseline_sha256", "version", "path", "counts", "sample_ids", "recognition"})
    for key in ("accepted_version_before", "accepted_version_after"):
        if isinstance(payload.get(key), dict):
            payload[key] = _keep(payload[key], {"version", "label", "path", "status"})
    change = payload.get("change")
    if isinstance(change, dict):
        payload["change"] = _keep(change, {"revision_id", "parent_revision_id", "status", "request", "receipt", "geometry", "validation_checks"})
        checks = change.get("validation_checks")
        if isinstance(checks, dict):
            payload["change"]["validation_checks"] = {**_keep(checks, {"lifecycle", "validation_state", "promotion_ready", "counts"}),
                                                         "checks_total": len(checks.get("checks", [])),
                                                         "blockers_total": len(checks.get("blockers", [])),
                                                         "warnings_total": len(checks.get("warnings", [])),
                                                         "details_at": change.get("receipt")}
    job = payload.get("inspection_job")
    if job:
        payload["inspection_job"] = _keep(job, {"id", "version", "status", "worker_running", "cancel_requested", "manifest_id", "analysis_revision", "recognition", "request", "checkpoints", "stages", "actions"})
        selected = "wait" if job["status"] in {"running", "queued"} else "resume" if job["status"] in {"failed", "stopped", "canceled", "resource_limit"} else "status"
        payload["inspection_job"]["actions"] = _keep(job.get("actions", {}), {selected})
        payload["inspection_job"]["stages"] = {name: row for name, row in job.get("stages", {}).items() if row["status"] != "complete"}
    for key in ("artifacts", "viewer_context", "viewer_glb", "viewer", "project_viewer", "preview", "diff", "comparison_phases", "created", "tool_version", "source", "scaffold", "hint", "inspection_image"):
        payload.pop(key, None)
    report = _report(payload)
    if report:
        report.pop("provenance", None)
    if "next_actions" in payload:
        payload["next_actions"] = payload["next_actions"][:1]
    if isinstance(payload.get("message"), str) and len(payload["message"]) > 240:
        payload["message"] = payload["message"][:240]
        payload["message_truncated"] = True
    if payload.get("warnings"):
        warnings = payload["warnings"]
        payload["warnings"] = [str(warnings[0])[:240]]
        payload["warnings_omitted"] = len(warnings) - 1


def _trim_report(payload):
    report = _report(payload)
    if not report:
        return False
    for group in ("features", "parts"):
        if report["samples"][group]:
            report["samples"][group].pop()
            report["budget"]["truncated"] = True
            return True
    if report["parts"]:
        report["parts"].pop()
        report["budget"]["truncated"] = True
        return True
    if "bbox" in report.get("model", {}):
        report["model"].pop("bbox")
        return True
    return False


def _minimum(payload):
    """Condense evidence references; keep acceptance and progress decisions."""
    for key in ("accepted_version_before", "accepted_version_after"):
        if isinstance(payload.get(key), dict):
            payload[key] = _keep(payload[key], {"version", "label", "status"})
    promotion = payload.get("promotion")
    if isinstance(promotion, dict):
        payload["promotion"] = {**_keep(promotion, {"kind", "outcome", "lifecycle", "request_key", "target_id", "validation_state", "reason_codes", "promoted_revision_before", "promoted_revision_after"}), "summarized": True}
    change = payload.get("change")
    if isinstance(change, dict):
        keys = {"revision_id", "status", "receipt", "validation_checks"}
        if not (payload.get("outputs") or {}).get("step"):
            keys.add("geometry")
        payload["change"] = _keep(change, keys)
    job = payload.get("inspection_job")
    if job:
        payload["inspection_job"] = _keep(job, {"id", "version", "manifest_id", "analysis_revision", "recognition", "status", "worker_running", "cancel_requested", "actions", "stages"})
    if isinstance(payload.get("idempotency"), dict):
        payload["idempotency"] = _keep(payload["idempotency"], {"key", "request_id", "state", "replayed"})
    if isinstance(payload.get("geometry_manifest"), dict):
        payload["geometry_manifest"] = _keep(payload["geometry_manifest"], {"manifest_id", "baseline_sha256", "path", "counts", "recognition"})
    if isinstance(payload.get("outputs"), dict):
        payload["outputs"] = _keep(payload["outputs"], {"step"})
    payload["response_snapshot"] = _keep(payload["response_snapshot"], {"id", "path", "retrieve", "retention"})
    for key in ("next_actions", "original_filename", "sha256", "import_request", "promotion_request_key", "build_root_source", "metrics", "validation"):
        payload.pop(key, None)
    report = _report(payload)
    if report:
        keep = {"report"}
        if not payload.get("geometry_manifest"):
            keep.add("manifest")
        if report["media"]:
            keep.add("image_root")
        report["inventory"] = _keep(report["inventory"], keep)
        report.pop("next_actions", None)
        report["budget"] = _keep(report["budget"], {"scope", "text_tokens", "max_images", "transport_reserve_tokens", "truncated", "media_omitted_by_text"})


def _set_media(payload, original, count):
    report = _report(payload)
    if report is None:
        return
    report["media"] = deepcopy(original[:count])
    prepared = report["media_summary"]["prepared"]
    prior_text = report["budget"].get("media_omitted_by_text", 0)
    prior_nontext = report["media_summary"]["omitted"] - prior_text
    report["media_summary"]["returned"] = count
    report["media_summary"]["omitted"] = prior_nontext + prepared - count
    report["budget"]["media_omitted_by_text"] = prepared - count
    # Use the existing report accounting/required-reference contract too.
    compact = compact_report(report)
    if report is payload:
        payload.update(compact)
    else:
        payload["inspection"] = compact


def constrain(payload, requested, reference, *, delivered=True):
    """Pure final projection; safe to repeat after transport adds fields."""
    result = deepcopy(payload)
    previous = result.pop("delivery", None)
    if _report(result) is result:
        # The legacy flat report owns its own estimate. Keep the new audit
        # outside that scope so two self-referential estimates cannot drift.
        outer = {key: result.pop(key) for key in (
            "_exit_code", "via", "project_root", "build_root", "build_root_source", "response_snapshot",
            "historical_response", "snapshot_command", "current_advanced", "recorded_current_advanced",
        ) if key in result}
        result = {"command": result["command"], "status": result["status"], "inspection": result, **outer}
    report = _report(result)
    original = deepcopy(report.get("media", [])) if report else []
    already_omitted = (previous or {}).get("media", {}).get("omitted_ids", [])
    profile = requested.get("vision_profile")
    combined = requested.get("response_budget")
    if combined is None and requested.get("context_window") is not None:
        combined = requested["context_window"] // 20
    default_text = combined if combined is not None else 2000
    text_limit = min(requested.get("response_text_budget") or default_text, combined if combined is not None else math.inf)
    image_limit = requested.get("image_budget")
    unknown_constrained = profile is None and (combined is not None or image_limit is not None)
    count = len(original) if delivered and not unknown_constrained else 0
    reason = "vision_profile_required" if unknown_constrained else "image_delivery_failed" if not delivered else "token_budget"
    if image_limit == 0 and delivered:
        reason = "image_budget_zero"
    if profile and image_limit is not None:
        count = min(count, image_limit // profile["tokens_per_image"])
    result["response_snapshot"] = reference
    result["delivery"] = {
        "scope": "complete_response", "requested": requested,
        "limits": {"text_tokens": text_limit, "image_tokens": image_limit, "combined_tokens": combined},
        "target_source": "explicit_response_budget" if requested.get("response_budget") is not None else "context_window_5_percent" if combined is not None else "text_fallback",
        "text_estimation": "ceil(emitted JSON UTF-8 bytes / 3)", "transport_reserve_tokens": RESERVE,
        "vision_estimation": "caller_supplied_fixed_1024_square" if profile else "unknown",
        "estimated_text_tokens": 0, "estimated_image_tokens": None, "estimated_combined_tokens": None,
        "budget_satisfied": True, "status": "complete", "truncated": bool((previous or {}).get("truncated")),
        "media": {"eligible": len(original) + len(already_omitted),
                  "prepared": report["media_summary"]["prepared"] if report else 0,
                  "returned": count, "omitted_ids": [], "omission_reason": None},
    }
    summarized = False
    minimized = False
    while True:
        _set_media(result, original, count)
        current = _report(result)
        count = len(current["media"]) if current else 0
        audit = result["delivery"]
        omitted = list(dict.fromkeys([*already_omitted, *[m["id"] for m in original[count:]]]))
        audit["media"].update(returned=count, omitted_ids=omitted, omission_reason=reason if omitted else None)
        audit["estimated_image_tokens"] = count * profile["tokens_per_image"] if profile else 0 if count == 0 else None
        audit["truncated"] = audit["truncated"] or bool(omitted)
        for _ in range(4):
            audit["estimated_text_tokens"] = estimate(result)
            audit["estimated_combined_tokens"] = audit["estimated_text_tokens"] + audit["estimated_image_tokens"] if audit["estimated_image_tokens"] is not None else None
        fits_text = audit["estimated_text_tokens"] + RESERVE <= text_limit
        if not fits_text:
            audit["truncated"] = True
            if not summarized:
                _summarize(result)
                summarized = True
                continue
            if _trim_report(result):
                continue
            if not minimized:
                _minimum(result)
                minimized = True
                continue
            if count:
                count -= 1
                continue
            current = _report(result)
            if current and "image_root" in current["inventory"]:
                current["inventory"].pop("image_root")
                continue
            # Required identity/status/evidence cannot fit. Be explicit rather
            # than reversing successful CAD or claiming the hard cap was met.
            audit.update(status="budget_unavailable", budget_satisfied=False)
            for _ in range(4):
                audit["estimated_text_tokens"] = estimate(result)
                audit["estimated_combined_tokens"] = audit["estimated_text_tokens"] if audit["estimated_image_tokens"] == 0 else None
            return result
        if combined is not None and audit["estimated_combined_tokens"] + RESERVE > combined:
            count -= 1
            audit["truncated"] = True
            continue
        return result


def apply_delivery(payload, layout, ctx):
    requested = ctx.meta.get("inspection_delivery_policy")
    if requested is None:
        return payload
    try:
        reference = (payload["response_snapshot"] if payload.get("delivery", {}).get("scope") == "complete_response"
                     and "response_snapshot" in payload else save_snapshot(payload, layout, ctx))
        return constrain(payload, requested, reference)
    except (OSError, ValueError, ProjectError, KeyError, TypeError) as exc:
        status = "resource_limit" if isinstance(exc, SnapshotResourceLimit) else "snapshot_unavailable"
        report = _report(payload)
        media = report.get("media", []) if report else []
        result = {**payload, "delivery": {"scope": "complete_response", "requested": requested,
                  "status": status, "budget_satisfied": False, "message": str(exc),
                  "estimated_text_tokens": 0, "estimated_image_tokens": 0,
                  "media": {"eligible": len(media), "returned": 0, "omitted_ids": [row["id"] for row in media],
                            "omission_reason": status}}}
        return finalize_mcp(result)


def finalize_mcp(payload, *, delivered=True):
    audit = payload.get("delivery", {})
    if audit.get("scope") == "complete_response" and "response_snapshot" in payload:
        return constrain(payload, audit["requested"], payload["response_snapshot"], delivered=delivered)
    if audit.get("status") in {"snapshot_unavailable", "resource_limit"}:
        payload = deepcopy(payload)
        for _ in range(4):
            payload["delivery"]["estimated_text_tokens"] = estimate(payload)
    return payload
