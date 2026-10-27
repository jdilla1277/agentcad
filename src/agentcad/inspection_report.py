"""A bounded inspection summary and retrievable, version-bound sample bundle."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import shlex

from agentcad.geometry_manifest import BaselineGeometry, MANIFEST_NAME, _digest, read_manifest
from agentcad.inspection_samples import dimensions, select_samples
from agentcad.versioning import atomic_write_json

REPORT_REVISION = "inspection-report-v1"
DEFAULT_TEXT_BUDGET = 2000
MAX_IMAGES = 9


class ReportBudgetError(ValueError):
    pass


def _quality(meta):
    validation = meta.get("validation", {})
    if not isinstance(validation, dict):
        validation = {}
    metrics = meta.get("metrics") or {}
    layers = validation.get("layers") or {}
    return {
        "status": "available" if validation else "not_available",
        "profile": validation.get("profile"), "is_valid": validation.get("is_valid"),
        "reliable": metrics.get("reliable") if isinstance(metrics, dict) else None,
        "layers": {name: layer.get("status") for name, layer in layers.items() if isinstance(layer, dict)} if isinstance(layers, dict) else {},
    }


def _read_meta(step):
    try:
        data = json.loads(step.with_name("meta.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _covers_model(saved, part_id):
    occurrences = set(saved["entities"][part_id]["occurrence_ids"])
    return all(
        e["kind"] not in {"solid", "face", "edge"} or occurrences.intersection(e["occurrence_ids"])
        for e in saved["entities"].values()
    )


def _analysis(step, output_root, saved, meta, session):
    source_hash = saved["source"].get("sha256")
    if (meta.get("version") != saved["version"] or not source_hash
            or meta.get("sha256") != source_hash):
        meta = {}
    quality = _quality(meta)
    inputs = {"revision": REPORT_REVISION, "manifest_id": saved["manifest_id"], "quality": quality}
    revision = _digest(inputs)
    path = output_root / "inspection" / f"report_{revision[:16]}.json"
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        checksum = cached.pop("sha256")
        if cached["analysis_revision"] == revision and _digest(cached) == checksum:
            return cached, path
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    plan = select_samples(saved)
    context = BaselineGeometry(step)
    session["context"] = context
    from agentcad.geometry_images import _bounds

    parts = [{
        "id": e["id"], "name": e["name"], "name_status": e["name_status"],
        "definition_dimensions": dimensions(e["bbox"]), "definition_frame": "definition_local",
        "occurrence_count": len(e["occurrence_ids"]),
    } for e in saved["entities"].values() if e["kind"] == "part" and e["definition_kind"] == "part"]
    samples = {"parts": [], "features": []}
    for group in samples:
        for selected in plan[group]:
            entity = saved["entities"][selected["id"]]
            samples[group].append({
                **selected, "kind": entity["kind"],
                **_bounds(context.image_shape(entity["id"])),
                **{key: entity[key] for key in ("area", "length") if key in entity},
                "coordinate_frame": "assembly", "units": "mm",
            })
    reused = parts[0]["id"] if len(parts) == 1 and _covers_model(saved, parts[0]["id"]) else None
    for values in samples.values():
        for sample in values:
            sample["image_id"] = "overall" if sample["id"] == reused else sample["id"]
            sample["image_mode"] = "overall_reuse" if sample["id"] == reused else "isolated"
    data = {
        "analysis_revision": revision, "revision": REPORT_REVISION,
        "manifest_id": saved["manifest_id"], "version": saved["version"],
        "baseline_sha256": saved["baseline"]["sha256"],
        "status": "complete", "quality": quality, "provenance": saved["source"],
        "units": "mm", "coordinate_frame": "assembly", "model": _bounds(context.shape.wrapped),
        "counts": saved["counts"], "recognition": {"status": "not_available", "recognized_feature_count": None},
        "parts": parts, "samples": samples, "overall_reused_for": reused,
    }
    # A report is derived; creating it must not change the durable ID registry.
    if read_manifest(step)["manifest_id"] != saved["manifest_id"]:
        raise ValueError("Baseline changed while preparing the inspection report.")
    atomic_write_json(path, {**data, "sha256": _digest(data)})
    return data, path


def _media_order(data):
    result = [{"id": "overall", "role": "overall", "target_id": None, "isolate": False}]
    for i in range(max(len(data["samples"]["parts"]), len(data["samples"]["features"]))):
        for group in ("parts", "features"):
            values = data["samples"][group]
            if i >= len(values):
                continue
            selected = values[i]
            if selected["id"] == data["overall_reused_for"]:
                continue
            result.append({"id": selected["id"], "role": group, "target_id": selected["id"], "isolate": True})
    return result


def _estimated_tokens(payload):
    # An explicit estimate, not a model-independent tokenizer or a 5% promise.
    return math.ceil(len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) / 3)


def inspection_report(step_path, output_root, *, images=True, max_images=MAX_IMAGES,
                      text_budget=DEFAULT_TEXT_BUDGET, meta=None, response_path=str, response_extra=None):
    """Reuse analysis and prepare a bounded deterministic gallery.

    Budget applies to this report object. The legacy import envelope is a
    separate compatibility surface; inspect --report returns the bounded form.
    """
    if not 0 <= max_images <= MAX_IMAGES:
        raise ValueError("max_images must be between 0 and 9.")
    if text_budget < 1:
        raise ValueError("text_budget must be positive.")
    step, output_root = Path(step_path), Path(output_root)
    saved = read_manifest(step)
    session = {}
    data, path = _analysis(step, output_root, saved, meta if meta is not None else _read_meta(step), session)
    from agentcad.geometry_images import geometry_image

    media, omitted = [], []
    allowed = max_images if images else 0
    for planned in _media_order(data):
        if len(media) >= allowed:
            omitted.append({"id": planned["id"], "reason": "image_cap" if images else "images_disabled"})
            continue
        try:
            sheet = geometry_image(step, output_root, object_id=planned["target_id"],
                                   isolate=planned["isolate"], _session=session)
            media.append({
                "id": planned["id"], "role": planned["role"],
                "png": response_path(Path(sheet["png"])), "sha256": sheet["png_sha256"],
            })
        except Exception as exc:
            omitted.append({"id": planned["id"], "reason": "image_failed", "message": str(exc)[:240]})
    # Publish the full delivery catalogue separately so budget truncation never
    # hides which samples were chosen or how to retrieve their images.
    reused = data["overall_reused_for"] if any(row["id"] == "overall" for row in media) else None
    catalogue = {"analysis_revision": data["analysis_revision"], "media": media, "omitted": omitted,
                 "overall_reused_for": reused}
    catalogue_id = _digest(catalogue)
    catalogue_path = output_root / "inspection" / f"bundle_{catalogue_id[:16]}.json"
    atomic_write_json(catalogue_path, catalogue)
    manifest_path = response_path(step.with_name(MANIFEST_NAME))
    report_path = response_path(path)
    command = f"agentcad inspect {shlex.quote(response_path(step))}"
    payload = {
        "command": "inspect", "status": "success", "analysis_revision": data["analysis_revision"],
        "manifest_id": data["manifest_id"], "version": data["version"], "baseline_sha256": data["baseline_sha256"],
        "quality": data["quality"], "units": "mm", "coordinate_frame": "assembly",
        "model": data["model"], "counts": data["counts"], "recognition": data["recognition"],
        "provenance": {**data["provenance"], "filename": str(data["provenance"].get("filename", ""))[:80]},
        "inventory": {"manifest": manifest_path, "report": report_path,
                      "bundle": response_path(catalogue_path), "parts_total": len(data["parts"]),
                      "image_root": response_path(output_root / "inspection")},
        "parts": deepcopy(data["parts"][:10]), "samples": deepcopy(data["samples"]),
        "sample_totals": {group: len(values) for group, values in data["samples"].items()},
        "media": [{**row, "png": Path(row["png"]).name} for row in media],
        "media_path_basis": "inventory.image_root",
        "media_summary": {"prepared": len(media), "returned": len(media), "omitted": len(omitted),
                                         "failed": sum(row["reason"] == "image_failed" for row in omitted),
                                         "reused_overall_for": reused},
        "next_actions": [f"{command} --id ID --image", f"{command} --report --no-images"],
        "budget": {"scope": "inspection_report", "text_tokens": text_budget, "max_images": allowed,
                   "image_size": [1024, 1024], "image_tokens": "unknown", "context_window": "unknown",
                   "text_estimation": "ceil(compact UTF-8 JSON bytes / 3)", "estimated_text_tokens": 0,
                   "transport_reserve_tokens": 10,
                   "truncated": len(data["parts"]) > 10, "parts_returned": 0, "media_references_returned": len(media)},
    }
    payload.update(response_extra or {})
    if len(str(data["provenance"].get("filename", ""))) > 80:
        payload["provenance"]["filename_truncated"] = True
        payload["budget"]["truncated"] = True
    for part in payload["parts"]:
        if part["name"]:
            if len(part["name"]) > 80:
                part["name_truncated"] = True
                payload["budget"]["truncated"] = True
            part["name"] = part["name"][:80]
    # Preserve baseline/revision, status, counts, artifact references and CTAs.
    # Remove optional tail detail in a declared order when a caller lowers text.
    while True:
        payload["budget"]["parts_returned"] = len(payload["parts"])
        payload["budget"]["media_references_returned"] = len(payload["media"])
        payload["budget"]["samples_returned"] = {group: len(values) for group, values in payload["samples"].items()}
        payload["media_summary"]["returned"] = len(payload["media"])
        payload["media_summary"]["omitted"] = len(omitted) + len(media) - len(payload["media"])
        payload["media_summary"]["state"] = (
            "partial_failure" if payload["media_summary"]["failed"] else "disabled" if not images
            else "bounded" if payload["media_summary"]["omitted"] else "complete"
        )
        payload["budget"]["media_omitted_by_text"] = len(media) - len(payload["media"])
        payload["budget"]["estimated_text_tokens"] = _estimated_tokens(payload)
        payload["budget"]["estimated_text_tokens"] = _estimated_tokens(payload)
        if _estimated_tokens(payload) <= text_budget - 10:
            break
        payload["budget"]["truncated"] = True
        samples = payload["samples"]
        if any("reason" in row for values in samples.values() for row in values):
            for values in samples.values():
                for row in values:
                    row.pop("reason", None)
        elif any("bbox" in row for values in samples.values() for row in values):
            for values in samples.values():
                for row in values:
                    row.pop("bbox", None)  # Dimensions stay inline; bounds stay in the full report.
        elif len(payload["parts"]) > 4:
            payload["parts"].pop()
        elif samples["features"]:
            samples["features"].pop()
        elif samples["parts"]:
            samples["parts"].pop()
        elif payload["media"]:
            payload["media"].pop()
        elif payload["parts"]:
            payload["parts"].pop()
        else:
            raise ReportBudgetError("Text budget is too small for required identity, status and retrieval references; increase --text-budget.")
    payload["budget"]["estimated_text_tokens"] = _estimated_tokens(payload)
    return payload, {"report": path, "bundle": catalogue_path, "media": media, "omitted": omitted,
                     "analysis": data}
