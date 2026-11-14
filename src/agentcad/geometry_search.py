"""Shared local CLI/MCP exact search, immutable pages and bounded delivery."""

import base64
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shlex

from agentcad.experimental_geometry_search import FLAG, MARKER
from agentcad.geometry_manifest import _digest, read_manifest
from agentcad.geometry_query import InvalidQuery, parse_query
from agentcad.geometry_search_index import INDEX_REVISION, evaluate
from agentcad.geometry_search_store import SearchStore, SearchUnavailable
from agentcad.inspection_delivery import estimate, RESERVE
from agentcad.project import format_response, get_project, derived_dir


class SearchBudgetError(ValueError):
    reason = "geometry_search_budget_too_small"


def _action(step, **arguments):
    layout = get_project()
    args = {"file": str(step), "cwd": str(layout.project_root), "build_dir": str(layout.build_root), MARKER: True, **arguments}
    cli = ["agentcad", "inspect", str(step), FLAG, "--build-dir", str(layout.build_root)]
    for key, value in arguments.items():
        option = "--" + key.replace("_", "-")
        if type(value) is bool:
            if value:
                cli.append(option)
        else:
            cli.extend([option, json.dumps(value) if isinstance(value, dict) else str(value)])
    return {"cli": shlex.join(cli), "mcp": {"tool": "inspect", "arguments": args}}


def _cursor(snapshot_id, offset):
    body = f"{snapshot_id}:{offset}"
    return body + ":" + hashlib.sha256(body.encode()).hexdigest()[:16]


def _parse_cursor(cursor):
    if not isinstance(cursor, str) or len(cursor) > 160:
        raise InvalidQuery("Use the returned pagination cursor without modification.")
    match = re.fullmatch(r"(search_[0-9a-f]{64}):(0|[1-9][0-9]{0,9}):[0-9a-f]{16}", cursor)
    if match is None or _cursor(match[1], int(match[2])) != cursor:
        raise InvalidQuery("Use the returned pagination cursor without modification.")
    return match[1], int(match[2])


def _compact(entry):
    result = {k: deepcopy(v) for k, v in entry.items() if v is not None and k not in {
        "coordinate_frame", "units", "bbox", "part_ids", "occurrence_ids", "selector_status", "name", "members"}}
    if len(entry.get("part_ids", [])) > 1:
        result["part_ids"] = entry["part_ids"][:10]
        if len(entry["part_ids"]) > 10:
            result["parts"] = {"returned": 10, "total": len(entry["part_ids"]), "truncated": True}
    if entry.get("occurrence_id") is None and entry.get("occurrence_ids"):
        result["occurrence_ids"] = entry["occurrence_ids"][:10]
        if len(entry["occurrence_ids"]) > 10:
            result["occurrences"] = {"returned": 10, "total": len(entry["occurrence_ids"]), "truncated": True}
    if "members" in entry:
        result["member_counts"] = {kind: len(ids) for kind, ids in entry["members"].items()}
    if entry.get("selector") is None:
        result["selector_status"] = entry["selector_status"]
    return result


def _prepare_media(step, results, count, index):
    if not count or not results:
        return [], []
    from agentcad.geometry_images import geometry_image
    from agentcad.inspection_cache import cache_lease

    media, failures = [], []
    output = derived_dir("inspect", Path(step))
    session = {"context": index.context}
    # One lease prevents this bundle's earlier images being evicted while
    # later ones are prepared. It also gives all returned references a lease.
    with cache_lease(output) as cache:
        for entry in results[:count]:
            try:
                grouped = "distinct_occurrence_count" in entry
                target_id = entry["occurrence_ids"][0] if grouped else entry["id"]
                image = geometry_image(step, output, object_id=target_id, _session=session)
                media.append({"id": entry["id"], "target_id": target_id,
                              "scope": "representative_matched_occurrence" if grouped else "selected_target",
                              "png": image["png"], "metadata": image["metadata"],
                              "sha256": image["png_sha256"]})
            except Exception as exc:
                failures.append({"id": entry["id"], "message": str(exc)})
        if media:
            retention = cache.reference()
            for item in media:
                item["retention"] = retention
    return media, failures


def _retained_media(data, ids):
    from agentcad.inspection_cache import cache_lease
    media, failures = [], []
    for item in data.get("media", []):
        if item["id"] not in ids:
            continue
        try:
            path = Path(item["png"])
            with cache_lease(path.parent.parent) as cache:
                cache.validate([path, Path(item["metadata"])], item["retention"])
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("Image hash changed.")
            media.append(deepcopy(item))
        except (OSError, ValueError) as exc:
            failures.append({"id": item["id"], "status": "unavailable", "message": str(exc)})
    failures.extend(f for f in data.get("media_failures", []) if f["id"] in ids)
    return media, failures


def _limits(settings):
    policy = settings.get("policy") or {}
    combined = policy.get("response_budget")
    if combined is None and policy.get("context_window") is not None:
        combined = max(1, policy["context_window"] // 20)
    text = min(settings["text_budget"], policy.get("response_text_budget") or combined or settings["text_budget"],
               combined if combined is not None else math.inf)
    return policy, combined, text


def search(step, *, query=None, snapshot_id=None, cursor=None, object_id=None,
           page_size=10, images=10, text_budget=2000, policy=None, full_response=False):
    layout = get_project()
    step = Path(step).resolve()
    offset = 0
    if cursor is not None:
        cursor_snapshot, offset = _parse_cursor(cursor)
        if snapshot_id is not None and snapshot_id != cursor_snapshot:
            raise InvalidQuery("cursor and search_snapshot refer to different queries.")
        snapshot_id = cursor_snapshot
    if query is not None and (snapshot_id is not None or object_id is not None):
        raise InvalidQuery("Use query for a new search, or a returned snapshot/cursor for retained results.")
    normalized = parse_query(query) if query is not None else None
    if normalized is None and snapshot_id is None:
        raise InvalidQuery("Use --query JSON, --search-snapshot ID or a returned --cursor.")
    store = SearchStore(layout)
    saved = read_manifest(step)
    if normalized is not None:
        results, coverage, failures, index = evaluate(step, saved, normalized)
        # Verify that long-running evaluation still addresses the same bytes.
        if read_manifest(step)["manifest_id"] != saved["manifest_id"]:
            raise SearchUnavailable("Baseline changed while filtering; restore the original bytes.")
        settings = {"page_size": page_size, "images": images, "text_budget": text_budget,
                    "policy": policy or {}, "full_response": full_response}
        requested, combined, _ = _limits(settings)
        profile = requested.get("vision_profile")
        allowed_images = min(images, page_size)
        if profile is None and (combined is not None or requested.get("image_budget") is not None):
            allowed_images = 0
        if profile and requested.get("image_budget") is not None:
            allowed_images = min(allowed_images, requested["image_budget"] // profile["tokens_per_image"])
        # Preserve text first, leaving a conservative reservation before CAD
        # rendering. Final delivery enforces the exact serialized allowance.
        if profile and combined is not None:
            allowed_images = min(allowed_images, max(0, combined - min(text_budget, combined)) // profile["tokens_per_image"])
        try:
            media, media_failures = _prepare_media(step, results, allowed_images, index)
        except (OSError, ValueError) as exc:
            media = []
            media_failures = [{"id": r["id"], "message": str(exc)} for r in results[:allowed_images]]
        data, reference = store.publish({"baseline": str(step), "manifest_id": saved["manifest_id"],
            "baseline_sha256": saved["baseline"]["sha256"], "version": saved["version"],
            "analysis_revision": _digest({"manifest": saved["manifest_id"], "index": INDEX_REVISION}),
            "index_revision": INDEX_REVISION, "query": normalized, "settings": settings,
            "results": results, "filter_coverage": coverage, "failures": failures,
            "media": media, "media_failures": media_failures})
        snapshot_id = reference["id"]
    else:
        data, reference, _ = store.read(snapshot_id)
        if (data["baseline"] != str(step) or data["manifest_id"] != saved["manifest_id"]
                or data["baseline_sha256"] != saved["baseline"]["sha256"]):
            raise SearchUnavailable("Search snapshot belongs to another baseline or version; use its original file/build root.")
        for field, value, default in (("page_size", page_size, 10), ("images", images, 10), ("text_budget", text_budget, 2000)):
            if value != default and value != data["settings"][field]:
                raise InvalidQuery("Retained pages use their saved delivery settings. Start a new query to change them.")
        if policy and policy != data["settings"]["policy"] or full_response and not data["settings"]["full_response"]:
            raise InvalidQuery("Retained pages use their saved delivery budgets. Start a new query to change them.")
    results = data["results"]
    if offset and offset >= len(results):
        raise InvalidQuery("Cursor offset is outside this retained result set.")
    if object_id is not None:
        if cursor is not None:
            raise InvalidQuery("search_id cannot be combined with cursor.")
        selected = next((i for i, entry in enumerate(results) if entry["id"] == object_id), None)
        if selected is None:
            raise InvalidQuery("search_id is not a match in this retained query.")
        offset = selected
    settings = data["settings"]
    page_size = 1 if object_id is not None else settings["page_size"]
    count = min(page_size, len(results) - offset)
    policy, combined, text_limit = _limits(settings)
    profile = policy.get("vision_profile")
    media, image_failures = _retained_media(data, {r["id"] for r in results[offset:offset + count]})
    requested_media = len(media)
    unavailable_media = {f["id"] for f in image_failures}
    artifact_action = _action(step, artifact=snapshot_id)
    payload = {"command": "inspect", "status": "success", "operation": "geometry_search",
        MARKER: {"enabled": True, "status": "work_in_progress"},
        "version": data["version"], "manifest_id": data["manifest_id"], "baseline_sha256": data["baseline_sha256"],
        "analysis_revision": data["analysis_revision"], "index_revision": data["index_revision"],
        "applied_query": data["query"], "result_mode": data["query"]["result_mode"],
        "coordinate_frame": "definition_local" if data["query"]["result_mode"] == "definitions" else "assembly", "units": "mm",
        "ranking_mode": "not_applicable", "ranking_complete": None,
        "comparison_counts": None, "shape_comparison_calls": 0,
        "recognition": {"status": "not_available", "recognized_feature_count": None},
        "filter_coverage": data["filter_coverage"], "match_count": len(results),
        "match_count_complete": data["filter_coverage"]["status"] == "complete",
        "search_snapshot": {"id": reference["id"], "retention": reference["retention"]},
        "candidate_manifest": {"id": snapshot_id, "sha256": reference["sha256"], "retrieve": artifact_action},
        "limitations": ["Raw geometry only; no named-feature recognition or shape-similarity ranking.",
                         "Bounds and dimensions use axis-aligned boxes; location is the box center.",
                         "Only the initial page prepares images; saved image bytes are never regenerated by page retrieval."],
        "media": media, "image_failures": image_failures,
        "delivery": {"scope": "geometry_search", "limits": {"text_tokens": None if settings["full_response"] else text_limit, "combined_tokens": combined,
                     "image_tokens": policy.get("image_budget"), "max_images": settings["images"], "image_size": [1024, 1024]},
                     "text_estimation": "ceil(emitted JSON UTF-8 bytes / 3)", "transport_reserve_tokens": RESERVE,
                     "vision_estimation": "caller_supplied_fixed_1024_square" if profile else "unknown",
                     "estimated_text_tokens": 0, "estimated_image_tokens": None,
                     "budget_satisfied": True}}
    if not results:
        payload["suggestions"] = ["Check the selected baseline, units, tolerances and placement filters. Filters were not relaxed."]
    if data["failures"]:
        payload["limitations"].append("Some candidates could not be evaluated; failure IDs/reasons are in the candidate manifest.")
    # Reserve room for project metadata and MCP's exit code before emission.
    while True:
        payload["results"] = [_compact(e) for e in results[offset:offset + count]]
        ids = {r["id"] for r in payload["results"]}
        payload["media"] = [m for m in media if m["id"] in ids]
        payload["image_failures"] = [{**f, "message": f["message"][:160]} for f in image_failures if f["id"] in ids]
        next_offset = offset + count
        payload["page"] = {"offset": offset, "requested": page_size, "returned": count,
                           "has_more": object_id is None and next_offset < len(results), "next": None}
        if payload["page"]["has_more"]:
            payload["page"]["next"] = _action(step, cursor=_cursor(snapshot_id, next_offset))
        audit = payload["delivery"]
        audit["estimated_image_tokens"] = len(payload["media"]) * profile["tokens_per_image"] if profile else None if payload["media"] else 0
        audit["images_returned"] = len(payload["media"])
        audit["images_omitted"] = requested_media - len(payload["media"])
        audit["image_omission_reason"] = ("retained_image_unavailable" if unavailable_media else
            "text_or_combined_budget" if requested_media > len(payload["media"]) else
            "caller_disabled" if settings["images"] == 0 else
            "vision_profile_required" if profile is None and (combined is not None or policy.get("image_budget") is not None) else
            "image_budget_zero" if policy.get("image_budget") == 0 else None)
        for _ in range(4):
            audit["estimated_text_tokens"] = estimate({**format_response(payload, layout), "_exit_code": 0})
        text = audit["estimated_text_tokens"] + RESERVE
        image_tokens = audit["estimated_image_tokens"] or 0
        if settings["full_response"] or text <= text_limit and (combined is None or text + image_tokens <= combined):
            return payload
        if media:
            media.pop()
        elif count > 1:
            count -= 1
        else:
            raise SearchBudgetError(f"Required search evidence exceeds the {text_limit}-token delivery limit. Start a new query with a larger --text-budget/--response-text-budget. Retained manifest: {snapshot_id}.")


def read_search_artifact(step, snapshot_id, *, offset, length, expected_sha256, expected_expires_at):
    """Bounded complete-candidate retrieval; no native CAD work or regeneration."""
    from agentcad.inspection_artifacts import _manifest, _regular

    step = Path(step).absolute()
    _regular(step, step.parent)
    saved, _ = _manifest(step)
    data, reference, raw = SearchStore(get_project()).read(snapshot_id)
    if data["baseline"] != str(step) or data["manifest_id"] != saved["manifest_id"]:
        raise SearchUnavailable("Search artifact belongs to another baseline; use the original file/build root.")
    digest, retention = reference["sha256"], reference["retention"]
    if expected_sha256 is not None and expected_sha256 != digest:
        raise SearchUnavailable("Search artifact hash changed; no replacement evidence was returned.")
    if expected_expires_at is not None and expected_expires_at != retention["expires_at"]:
        raise SearchUnavailable("Search artifact expiry changed; restart the read without combining old and new chunks.")
    if offset and expected_expires_at is None:
        raise SearchUnavailable("Continuation requires artifact_expires_at; copy next.cli/next.mcp.")
    if offset >= len(raw):
        raise SearchUnavailable("artifact_offset must be smaller than the artifact byte count.")
    chunk = raw[offset:offset + length]
    complete = offset + len(chunk) == len(raw)
    return {"command": "inspect", "status": "success", "manifest_id": saved["manifest_id"],
            MARKER: {"enabled": True, "status": "work_in_progress"},
            "artifact": {"reference": snapshot_id, "sha256": digest, "total_bytes": len(raw),
                         "mime_type": "application/json", "encoding": "base64", "offset": offset,
                         "returned_bytes": len(chunk), "data": base64.b64encode(chunk).decode("ascii"),
                         "complete": complete, "retention": retention},
            "next": None if complete else _action(step, artifact=snapshot_id, artifact_sha256=digest,
                artifact_offset=offset + len(chunk), artifact_bytes=length, artifact_expires_at=retention["expires_at"])}
