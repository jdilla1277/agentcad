"""Durable specification evidence; geometry validity stays independent.

Pure receipt/aggregation functions are also used by the trusted cloud worker.
Local receipts are indexed beside the artifact (or in its registered project)
so reading the same file from another working directory finds the same checks.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

_SPEC_REASONS = {"spec_check_failed", "spec_check_stale", "spec_check_incomplete", "spec_evidence_unavailable"}


def spec_identity(spec, spec_file=None):
    """Filenames name local policies; hosted callers use one default or an ID."""
    if isinstance(spec, dict) and spec.get("spec_id"):
        return "spec:" + spec["spec_id"]
    return str(Path(spec_file).resolve()) if spec_file else "spec:requirements"


def make_check(artifact_sha256, spec_sha256, spec, result, *, spec_file=None, identity=None, status=None):
    return {
        "status": status or ("pass" if result.get("passed") is True else "fail" if result.get("passed") is False else "pending"),
        "artifact_sha256": artifact_sha256, "spec_sha256": spec_sha256,
        "spec_file": str(Path(spec_file).resolve()) if spec_file else None,
        "identity": identity or spec_identity(spec, spec_file),
        "checked_at": datetime.now(timezone.utc).isoformat(), "spec": deepcopy(spec),
        "evidence": {key: deepcopy(result[key]) for key in
                     ("passed", "matched_features", "missing_features", "structure_checks", "unsupported_requirements", "total_abs_count_error") if key in result},
    }


def aggregate_checks(checks, artifact_sha256):
    from agentcad.core_build import file_sha256
    current = []
    for saved in checks:
        check = deepcopy(saved)
        stale = []
        if check.get("status") == "unavailable":
            current.append(check)
            continue
        if check.get("artifact_sha256") != artifact_sha256:
            stale.append("artifact_changed")
        if check.get("spec_file"):
            try:
                if file_sha256(check["spec_file"]) != check.get("spec_sha256"):
                    stale.append("spec_changed")
            except OSError:
                stale.append("spec_unavailable")
        if stale:
            check["checked_status"] = check.get("status")
            check["status"] = "stale"
            check["stale_reasons"] = stale
        current.append(check)
    status = next((s for s in ("fail", "stale", "pending", "unavailable")
                   if any(c.get("status") == s for c in current)), "pass" if current else "unchecked")
    return {"status": status, "artifact_sha256": artifact_sha256, "checks": current}


def combine_readiness(geometry, intent):
    result = deepcopy(geometry)
    # Older projections may already include an intent blocker. Never erase a
    # geometry/source-change blocker when recomputing the current intent state.
    reasons = [r for r in result.get("blocking_reasons", []) if r not in _SPEC_REASONS]
    reason = {"fail": "spec_check_failed", "stale": "spec_check_stale", "pending": "spec_check_incomplete",
              "unavailable": "spec_evidence_unavailable"}.get((intent or {}).get("status"))
    if reason:
        reasons.append(reason)
    result["blocking_reasons"] = reasons
    result["ready"] = not reasons and geometry.get("ready") is True
    return result


def intent_message(intent):
    message = {
        "fail": "Specification check failed; not ready for delivery. Repair the missing or mismatched requirements and recheck the same specification.",
        "stale": "Specification evidence is stale; not ready for delivery. Recheck the current geometry against the current specification.",
        "pending": "Declared specification checks are incomplete; not ready for delivery. Complete the checks before delivery.",
        "unavailable": "Saved specification evidence is unavailable; not ready for delivery. Restore the saved evidence, then recheck every affected specification.",
        "pass": "Declared specification checks passed. Geometry validity and other task requirements remain separate.",
    }.get(intent["status"], "No specification has been checked.")
    if intent["status"] == "pending" and any(c.get("evidence", {}).get("unsupported_requirements") for c in intent.get("checks", [])):
        message += " Review unsupported_requirements: express supported measurable checks where possible; repeating unsupported checks cannot complete them."
    return message


def _location(step):
    step = Path(step).resolve()
    meta = None
    root = step.parent
    if step.name == "output.step":
        for parent in step.parents:
            manifest = parent / "agentcad.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text())
                if any((parent / v["path"] / "output.step").resolve() == step for v in data.get("versions", [])):
                    root, meta = parent, step.with_name("meta.json")
                    break
            except (OSError, ValueError, KeyError, TypeError):
                continue
    key = hashlib.sha256(str(step).encode()).hexdigest()
    record = root / ".agentcad/intent" / (key + ".json")
    fallback = step.parent / ".agentcad/intent" / (key + ".json")
    return root, meta, record, fallback


def _read_local_checks(step):
    root, meta, record, fallback = _location(step)
    for path in dict.fromkeys((record, fallback)):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
            if data["schema_version"] != 1 or not isinstance(data["checks"], list):
                raise ValueError("Invalid evidence record")
            if any(c.get("status") not in {"pass", "fail", "pending", "unavailable"} for c in data["checks"]):
                raise ValueError("Invalid evidence state")
            return data["checks"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            # Registered metadata mirrors the full policy list. Retain each
            # identity, but require each policy to be rechecked before recovery.
            # Without a mirror we cannot know which requirements were lost.
            if meta and meta.is_file():
                try:
                    mirrored = json.loads(meta.read_text())["intent_validation"]["checks"]
                    if mirrored and all(isinstance(c, dict) and c.get("identity") for c in mirrored):
                        return [{**c, "status": "unavailable", "declaration_id": None} for c in mirrored]
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    pass
            return [{"status": "unavailable", "identity": "unavailable"}]
    if meta and meta.is_file():
        try:
            checks = json.loads(meta.read_text()).get("intent_validation", {}).get("checks", [])
            for check in checks:
                if check.get("status") == "stale":
                    check["status"] = check.pop("checked_status", "pending")
                    check.pop("stale_reasons", None)
            return checks
        except (OSError, ValueError, TypeError, AttributeError):
            return [{"status": "unavailable", "identity": "unavailable"}]
    return []


def _read_checks(step):
    checks = _read_local_checks(step)
    step = Path(step).resolve()
    if (step.parent.name == "geometry" and len(step.parents) > 5
            and step.parents[2].name == "attempts" and step.parents[3].name == "changes"
            and step.parents[4].name == ".agentcad" and (step.parents[1] / "request.json").is_file()):
        identity = "retained:" + step.parents[1].name
        try:
            from agentcad.change_store import ChangeStore
            store = ChangeStore(step.parents[5])
            saved = store.read(step.parents[1].name)
            if saved["request"].get("check_policy"):
                receipt = store.read_checks(step.parents[1].name)["receipt"]
                artifact = next(a for a in saved["candidate"]["inputs"] if a["role"] == "geometry")
                pinned = {"identity": identity, "status": {"valid": "pass", "invalid": "fail", "pending": "pending"}.get(receipt["validation_state"], "unavailable"),
                          "artifact_sha256": artifact["sha256"], "spec_sha256": saved["policy"]["content_sha256"],
                          "spec_file": None, "spec": saved["policy"], "evidence": {"checks": receipt["checks"]}}
                checks = [c for c in checks if c.get("identity") != identity] + [pinned]
        except (OSError, ValueError, KeyError, StopIteration):
            checks = [c for c in checks if c.get("identity") != identity] + [{"identity": identity, "status": "unavailable"}]
    return checks


def project_intent(payload, step, *, checks=None):
    checks = _read_checks(step) if checks is None else checks
    if not checks:
        return payload
    from agentcad.core_build import file_delivery_readiness, file_sha256
    geometry = deepcopy(payload.get("geometry_readiness") or payload.get("delivery_readiness")
                        or file_delivery_readiness(payload.get("validation") or {}, step))
    try:
        actual_sha = file_sha256(step)
    except OSError:
        actual_sha = None
    if actual_sha != geometry.get("artifact_sha256"):
        geometry["ready"] = False
        reasons = geometry.setdefault("blocking_reasons", [])
        if "geometry_evidence_stale" not in reasons:
            reasons.append("geometry_evidence_stale")
    intent = aggregate_checks(checks, actual_sha)
    payload["geometry_readiness"] = geometry
    payload["intent_validation"] = intent
    payload["delivery_readiness"] = combine_readiness(geometry, intent)
    if intent["status"] == "pass":
        from agentcad.core_build import readiness_message
        payload["message"] = intent_message(intent) + " " + readiness_message(geometry)
    else:
        payload["message"] = intent_message(intent)
        if payload.get("command") == "check-spec" and payload.get("passed") is True:
            payload["message"] = "This specification passed, but other declared checks block delivery. " + payload["message"]
    return payload


def save_check(step, check, geometry=None, *, declaration=False):
    from agentcad.versioning import _file_lock, atomic_write_json
    step = Path(step).resolve()
    root, meta_path, record, _ = _location(step)
    with _file_lock(root / ".agentcad/intent-write.lock"):
        checks = _read_checks(step)
        previous = next((c for c in checks if c.get("identity") == check["identity"]), None)
        if not declaration and (previous is None or not check.get("declaration_id")
                                or previous.get("declaration_id") != check["declaration_id"]):
            # Only the current declaration may settle this policy. A late
            # passing checker cannot erase newer failed or pending requirements.
            return checks
        checks = [c for c in checks if c.get("identity") != check["identity"]]
        checks.append(check)
        atomic_write_json(record, {"schema_version": 1, "checks": checks})
        if meta_path and meta_path.is_file():
            metadata = json.loads(meta_path.read_text())
            if geometry is not None:
                metadata["geometry_readiness"] = deepcopy(geometry)
            if "delivery_readiness" in metadata or geometry is not None:
                if "delivery_readiness" not in metadata:
                    metadata["delivery_readiness"] = deepcopy(geometry)
                project_intent(metadata, step, checks=checks)
                atomic_write_json(meta_path, metadata)
    return checks


def persist_metadata(meta_path, metadata):
    """Do not let optional post-processing overwrite a concurrent check."""
    from agentcad.versioning import _file_lock, atomic_write_json
    step = Path(meta_path).with_name("output.step")
    root, _, record, fallback = _location(step)
    with _file_lock(root / ".agentcad/intent-write.lock"):
        if (record.exists() or fallback.exists() or metadata.get("intent_validation")) and metadata.get("delivery_readiness"):
            project_intent(metadata, step)
        atomic_write_json(meta_path, metadata)
