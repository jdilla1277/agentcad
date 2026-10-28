"""Versioned records for shared CAD change validation and promotion.

These pure builders describe pinned inputs and evidence. They do not run CAD,
write history, authorize human reviews, or advance a project's accepted target.
Persistence and command adapters must validate records at their boundaries.

Schema 1 records
---------------
Every record has ``schema_version``, ``kind``, and ``content_sha256``. Builders
return detached JSON objects; callers must not mutate an issued record. The
digest covers its exact content, using sorted UTF-8 JSON with finite numbers.

* ``candidate`` pins source/geometry hashes and sizes, the model description,
  parent revision, exact policies per named target, and automatic/staged mode.
  Each target has its own explicit deliverable artifact set; accepting one
  target does not advertise another target's artifacts as verified output.
  ``model_fingerprint`` excludes storage paths and administrative metadata;
  geometry, placements, units, and other model inputs belong in ``model`` or
  ``inputs``, never in ``metadata``. Artifact paths are snapshot-relative.
  Aliases at one path must declare the same content hash and byte size.
* ``target_policy`` pins versioned check definitions/settings, completion and
  failure obligations, and required review IDs. A successor also identifies
  its previous policy, actor, rationale, and before/after requirements.
* ``check_result`` binds one pass/fail/pending/unavailable result, its observed
  value, evidence references, and follow-up actions to that exact scope.
* ``change_receipt`` retains all checks/warnings/blockers. Required failure
  wins over pending work, then unknown evidence. Human review readiness is
  separate from model validity. Missing required evidence never passes.
* ``promotion_request`` binds a caller-known key, project/target, exact input
  scope, selection generation, and expected accepted revision. Replay is not
  a new request; changed inputs require a new key.
* ``operation_status`` describes in-progress work without a terminal outcome.
  ``promotion_receipt`` describes one completed promoted/not_promoted outcome,
  the before/after accepted revision, evidence identity, and artifact references.
  Refusals may retain operation diagnostics with requested/observed selection
  and policy snapshots plus reconciliation actions, separate from check blockers.

These are record contracts, not new CLI/MCP commands. Builders trust supplied
measurement/review facts; they do not measure geometry or grant approvals.
Before actual promotion, adapters must load retained artifacts and policy,
verify their bytes, reevaluate current evidence/reviews, check the live working
selection, and commit target and outcome consistently. A digest is neither
durable storage nor authorization; terminal receipt construction is not a
promotion transaction. Existing ``run`` behavior is intentionally unchanged.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import PurePosixPath, PureWindowsPath

SCHEMA_VERSION = 1
_SCOPE_FIELDS = {"candidate", "target_id", "policy"}
_FIELDS = {
    "candidate": {
        "revision_id",
        "parent_revision_id",
        "inputs",
        "model",
        "target_policies",
        "deliverables",
        "promotion_mode",
        "model_fingerprint",
        "requested_change",
        "metadata",
    },
    "target_policy": {
        "target_id",
        "policy_id",
        "version",
        "checks",
        "required_reviews",
        "change",
    },
    "check_result": _SCOPE_FIELDS
    | {
        "check_id",
        "definition_version",
        "definition_sha256",
        "state",
        "settings",
        "observed",
        "evidence",
        "actions",
    },
    "change_receipt": _SCOPE_FIELDS
    | {
        "checks",
        "validation_state",
        "review_states",
        "promotion_ready",
        "blockers",
        "warnings",
        "candidate_retained",
        "promoted_revision_id",
        "artifacts",
    },
    "promotion_request": _SCOPE_FIELDS
    | {
        "project_id",
        "request_key",
        "selection_generation",
        "promotion_mode",
        "expected_promoted_revision_id",
    },
    "promotion_receipt": _SCOPE_FIELDS
    | {
        "project_id",
        "request_key",
        "request_sha256",
        "promotion_mode",
        "lifecycle",
        "outcome",
        "validation_state",
        "promoted_revision_before",
        "promoted_revision_after",
        "candidate_retained",
        "blockers",
        "warnings",
        "reason_codes",
        "operation_diagnostics",
        "check_receipt_sha256",
        "review_states",
        "verified_artifacts",
    },
    "operation_status": _SCOPE_FIELDS
    | {
        "project_id",
        "request_key",
        "request_sha256",
        "promotion_mode",
        "lifecycle",
        "outcome",
        "jobs",
    },
}
_CHECK_STATES = {"pass", "fail", "pending", "unavailable"}


class ContractError(ValueError):
    """A malformed or mismatched record, with a machine-readable reason."""

    def __init__(self, message: str, reason: str = "invalid_contract"):
        super().__init__(message)
        self.reason = reason


def _require(condition: bool, message: str, reason: str = "invalid_contract") -> None:
    if not condition:
        raise ContractError(message, reason)


def _text(value, field: str) -> None:
    _require(
        isinstance(value, str) and bool(value.strip()),
        f"{field} must be a non-empty string.",
    )


def _integer(value, field: str, minimum: int = 0) -> None:
    _require(
        type(value) is int and value >= minimum,
        f"{field} must be an integer >= {minimum}.",
    )


def _hash(value, field: str) -> None:
    _require(
        isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
        f"{field} must be a lowercase SHA-256 digest.",
    )


def _policy_reference(value) -> None:
    _require(
        isinstance(value, dict) and set(value) == {"policy_id", "version", "sha256"},
        "Invalid pinned policy reference.",
    )
    _text(value["policy_id"], "policy_id")
    _integer(value["version"], "policy version", 1)
    _hash(value["sha256"], "policy.sha256")


def _candidate_reference(value) -> None:
    _require(
        isinstance(value, dict)
        and set(value) == {"revision_id", "model_fingerprint", "content_sha256"},
        "Invalid pinned candidate reference.",
    )
    _text(value["revision_id"], "candidate.revision_id")
    for field in ("model_fingerprint", "content_sha256"):
        _hash(value[field], f"candidate.{field}")


def _operation_snapshot(value, *, observed: bool) -> None:
    _require(
        isinstance(value, dict)
        and set(value) == {"candidate", "policy", "selection_generation", "promoted_revision_id"},
        "Operation context needs candidate, policy, selection generation, and accepted revision.",
    )
    if value["candidate"] is not None or not observed:
        _candidate_reference(value["candidate"])
    if value["policy"] is not None or not observed:
        _policy_reference(value["policy"])
    _integer(value["selection_generation"], "operation selection_generation")
    if value["promoted_revision_id"] is not None:
        _text(value["promoted_revision_id"], "operation promoted_revision_id")


def _operation_diagnostic(value, receipt: dict) -> None:
    _require(
        isinstance(value, dict) and set(value) == {"reason", "requested", "observed", "actions"},
        "Operation diagnostics need a reason, requested/observed snapshots, and actions.",
    )
    _text(value["reason"], "operation diagnostic reason")
    _operation_snapshot(value["requested"], observed=False)
    _operation_snapshot(value["observed"], observed=True)
    actions = value["actions"]
    _require(
        isinstance(actions, list) and bool(actions),
        "Operation diagnostics need reconciliation actions.",
    )
    for action in actions:
        _require(isinstance(action, dict), "Operation actions must be objects.")
        _text(action.get("action"), "operation action")
    requested = value["requested"]
    _require(
        all(requested[key] == receipt[key] for key in ("candidate", "policy")),
        "Operation diagnostic identifies a different promotion request.",
        "binding_mismatch",
    )
    # The requested generation and accepted revision are not duplicated in the
    # receipt's top-level scope. Reconstruct the exact request to verify both.
    request_body = {
        "schema_version": SCHEMA_VERSION,
        "kind": "promotion_request",
        **{
            key: receipt[key]
            for key in (
                "candidate",
                "policy",
                "target_id",
                "project_id",
                "request_key",
                "promotion_mode",
            )
        },
        "selection_generation": requested["selection_generation"],
        "expected_promoted_revision_id": requested["promoted_revision_id"],
    }
    _require(
        _digest(request_body) == receipt["request_sha256"],
        "Requested conflict context does not match the original promotion request.",
        "binding_mismatch",
    )


def _json(value) -> str:
    """Canonical, finite JSON; reject Python's silently coerced object keys."""

    def inspect(item):
        if isinstance(item, dict):
            for key, child in item.items():
                _require(isinstance(key, str), "JSON object keys must be strings.")
                inspect(child)
        elif isinstance(item, list):
            for child in item:
                inspect(child)
        else:
            _require(
                item is None or type(item) in (str, bool, int, float),
                "Records must contain JSON values only.",
            )
            if type(item) is float:
                _require(math.isfinite(item), "JSON numbers must be finite.")

    inspect(value)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _seal(kind: str, **fields) -> dict:
    record = {"schema_version": SCHEMA_VERSION, "kind": kind, **fields}
    record["content_sha256"] = _digest(record)
    return validate_document(record, kind=kind)


def _artifact(item: dict) -> None:
    _require(isinstance(item, dict), "Each input artifact must be an object.")
    for field in ("name", "role", "path"):
        _text(item.get(field), f"artifact.{field}")
    _require(
        item["role"] in {"source", "geometry", "collision_geometry", "input"},
        "Unknown input artifact role.",
    )
    path = item["path"]
    _require(
        not PurePosixPath(path).is_absolute()
        and not PureWindowsPath(path).drive
        and "\\" not in path
        and all(p not in ("", ".", "..") for p in path.split("/")),
        "Artifact paths must be relative snapshot paths without traversal.",
    )
    _hash(item.get("sha256"), "artifact.sha256")
    _integer(item.get("size_bytes"), "artifact.size_bytes")


def _model_fingerprint(model: dict, inputs: list) -> str:
    # Storage locators and administrative revision/review metadata are not
    # model identity. Keep role/name because they associate bytes with inputs.
    identities = [{k: a[k] for k in ("name", "role", "sha256", "size_bytes")} for a in inputs]
    identities.sort(key=lambda a: (a["role"], a["name"]))
    return _digest({"schema_version": SCHEMA_VERSION, "model": model, "inputs": identities})


def validate_document(record: dict, *, kind: str | None = None) -> dict:
    """Check schema and integrity and return a detached JSON copy.

    Digests detect changed records, not malicious authors. Immutable storage,
    artifact byte verification, and operation authorization are separate duties.
    """
    _require(isinstance(record, dict), "A change record must be a JSON object.")
    _require(
        type(record.get("schema_version")) is int and record["schema_version"] == SCHEMA_VERSION,
        "Unsupported change-record schema version.",
        "unsupported_schema",
    )
    _require(
        isinstance(record.get("kind"), str) and record["kind"] in _FIELDS,
        "Unknown change-record kind.",
    )
    _require(kind is None or record["kind"] == kind, f"Expected a {kind} record.")
    expected_fields = _FIELDS[record["kind"]] | {
        "schema_version",
        "kind",
        "content_sha256",
    }
    _require(
        set(record) == expected_fields,
        "Record fields do not match its schema; use the documented fields and schema version.",
    )
    body = {k: v for k, v in record.items() if k != "content_sha256"}
    _require(
        record.get("content_sha256") == _digest(body),
        "Record integrity check failed; use the original snapshot.",
        "integrity_mismatch",
    )
    if record["kind"] not in ("candidate", "target_policy"):
        _text(record["target_id"], "target_id")
        _candidate_reference(record["candidate"])
        _policy_reference(record["policy"])
    if "promotion_mode" in record:
        _require(
            record["promotion_mode"] in ("automatic", "staged"),
            "promotion_mode must be automatic or staged.",
        )
    for field in ("project_id", "request_key"):
        if field in record:
            _text(record[field], field)
    for field in ("request_sha256", "check_receipt_sha256"):
        if field in record:
            _hash(record[field], field)
    for field in ("blockers", "warnings", "reason_codes", "verified_artifacts"):
        if field in record:
            _require(isinstance(record[field], list), f"{field} must be a list.")
    if "validation_state" in record:
        _require(
            record["validation_state"] in ("valid", "invalid", "pending", "unknown"),
            "Unknown validation state.",
        )
    if "review_states" in record:
        _require(
            isinstance(record["review_states"], dict),
            "review_states must be an object.",
        )
    for field in (
        "promoted_revision_id",
        "expected_promoted_revision_id",
        "promoted_revision_before",
        "promoted_revision_after",
    ):
        if field in record and record[field] is not None:
            _text(record[field], field)
    if record["kind"] == "candidate":
        _text(record.get("revision_id"), "revision_id")
        parent = record.get("parent_revision_id")
        if parent is not None:
            _text(parent, "parent_revision_id")
            _require(parent != record["revision_id"], "A revision cannot be its own parent.")
        _require(
            record.get("promotion_mode") in ("automatic", "staged"),
            "promotion_mode must be automatic or staged.",
        )
        _require(isinstance(record.get("model"), dict), "model must be a JSON object.")
        _require(isinstance(record["metadata"], dict), "metadata must be a JSON object.")
        _require(
            isinstance(record["requested_change"], str),
            "requested_change must be a string.",
        )
        _require(
            isinstance(record["target_policies"], dict) and bool(record["target_policies"]),
            "Candidate must pin named target policies.",
        )
        for target_id, ref in record["target_policies"].items():
            _text(target_id, "target_id")
            _policy_reference(ref)
        _require(
            isinstance(record.get("inputs"), list),
            "inputs must be a list of pinned artifacts.",
        )
        path_identities = {}
        for item in record["inputs"]:
            _artifact(item)
            identity = (item["sha256"], item["size_bytes"])
            _require(
                path_identities.setdefault(item["path"], identity) == identity,
                f"Snapshot path {item['path']!r} declares conflicting content hashes or sizes.",
                "artifact_path_conflict",
            )
        deliverables = record["deliverables"]
        _require(
            isinstance(deliverables, dict) and set(deliverables) == set(record["target_policies"]),
            "Declare deliverables for each pinned target.",
        )
        for artifacts in deliverables.values():
            _require(
                isinstance(artifacts, list),
                "Target deliverables must be lists of pinned artifacts.",
            )
            for item in artifacts:
                _artifact(item)
                _require(
                    item in record["inputs"],
                    "Deliverables must reference exact artifacts available in the snapshot.",
                )
        names = [(a["role"], a["name"]) for a in record["inputs"]]
        _require(len(names) == len(set(names)), "Input role/name pairs must be unique.")
        _require(
            record.get("model_fingerprint")
            == _model_fingerprint(record["model"], record["inputs"]),
            "Model fingerprint does not match its pinned inputs.",
            "integrity_mismatch",
        )
    elif record["kind"] == "target_policy":
        for field in ("target_id", "policy_id"):
            _text(record.get(field), field)
        _integer(record.get("version"), "policy version", 1)
        _require(
            isinstance(record.get("checks"), list) and bool(record["checks"]),
            "A policy needs explicit check definitions.",
        )
        ids = []
        for spec in record["checks"]:
            _require(isinstance(spec, dict), "Each check definition must be an object.")
            _text(spec.get("check_id"), "check_id")
            ids.append(spec["check_id"])
            _integer(spec.get("definition_version"), "definition_version", 1)
            _require(
                isinstance(spec.get("settings"), dict),
                "Check settings must be an object.",
            )
            for flag in ("require_completion", "blocks_on_failure"):
                _require(type(spec.get(flag)) is bool, f"{flag} must be a boolean.")
            _require(
                not spec["blocks_on_failure"] or spec["require_completion"],
                "Blocking checks must require completion.",
            )
        _require(len(ids) == len(set(ids)), "Policy check IDs must be unique.")
        _require(
            isinstance(record.get("required_reviews"), list),
            "required_reviews must be a list.",
        )
        for review_id in record["required_reviews"]:
            _text(review_id, "review ID")
        _require(
            len(record["required_reviews"]) == len(set(record["required_reviews"])),
            "Required review IDs must be unique.",
        )
        change = record["change"]
        if record["version"] == 1:
            _require(change is None, "An initial policy has no predecessor.")
        else:
            _require(
                isinstance(change, dict)
                and set(change)
                == {
                    "previous_policy",
                    "actor",
                    "rationale",
                    "requirements_diff",
                },
                "A successor policy needs its predecessor, actor, rationale, and requirement diff.",
            )
            previous = change["previous_policy"]
            _policy_reference(previous)
            _require(
                previous["policy_id"] == record["policy_id"]
                and previous["version"] + 1 == record["version"],
                "A successor must follow the preceding version of the same policy.",
            )
            _text(change["actor"], "policy change actor")
            _text(change["rationale"], "policy change rationale")
            diff = change["requirements_diff"]
            _require(
                isinstance(diff, dict) and set(diff) == {"checks", "required_reviews"},
                "Invalid requirement diff.",
            )
            for key in ("checks", "required_reviews"):
                _require(
                    isinstance(diff[key], dict)
                    and set(diff[key]) == {"before", "after"}
                    and isinstance(diff[key]["before"], list)
                    and diff[key]["after"] == record[key],
                    "Requirement diff must identify the actual before/after requirements.",
                )
    elif record["kind"] == "check_result":
        _require(
            isinstance(record["state"], str) and record["state"] in _CHECK_STATES,
            "Unknown check state.",
        )
        _text(record["check_id"], "check_id")
        _integer(record["definition_version"], "definition_version", 1)
        _hash(record["definition_sha256"], "definition_sha256")
        _require(isinstance(record["settings"], dict), "Check settings must be an object.")
        _require(
            isinstance(record["evidence"], list) and isinstance(record["actions"], list),
            "Check evidence and actions must be lists.",
        )
        _require(
            all(isinstance(item, dict) for item in record["evidence"] + record["actions"]),
            "Evidence and action references must be objects.",
        )
    elif record["kind"] == "change_receipt":
        _require(
            record["validation_state"] in ("valid", "invalid", "pending", "unknown"),
            "Unknown validation state.",
        )
        _require(
            type(record["promotion_ready"]) is bool,
            "promotion_ready must be a boolean.",
        )
        _require(
            isinstance(record["blockers"], list) and isinstance(record["warnings"], list),
            "Blockers and warnings must be lists.",
        )
        _require(
            record["promotion_ready"] == (not record["blockers"]),
            "Promotion readiness disagrees with its blockers.",
        )
        _require(
            not record["promotion_ready"] or record["validation_state"] == "valid",
            "An unsatisfied model cannot be ready for promotion.",
        )
        _require(
            isinstance(record["checks"], list) and isinstance(record["review_states"], dict),
            "Invalid check or review evidence.",
        )
        for checked in record["checks"]:
            validate_document(checked, kind="check_result")
            _require(
                all(record[k] == checked[k] for k in _SCOPE_FIELDS),
                "Nested check evidence has a different scope.",
                "binding_mismatch",
            )
        _require(
            record["candidate_retained"] is True,
            "Attempts remain retained regardless of validation.",
        )
        _require(isinstance(record["artifacts"], list), "artifacts must be a list.")
        for item in record["artifacts"]:
            _artifact(item)
    elif record["kind"] == "promotion_request":
        for field in ("project_id", "request_key", "target_id"):
            _text(record.get(field), field)
        _integer(record.get("selection_generation"), "selection_generation")
    elif record["kind"] == "operation_status":
        _require(
            record.get("lifecycle") in ("pending", "running"),
            "In-progress lifecycle must be pending or running.",
        )
        _require(
            record.get("outcome") is None,
            "An in-progress operation has no terminal outcome.",
        )
        _require(isinstance(record["jobs"], list), "jobs must be a list.")
    elif record["kind"] == "promotion_receipt":
        _require(
            isinstance(record["operation_diagnostics"], list),
            "operation_diagnostics must be a list.",
        )
        for diagnostic in record["operation_diagnostics"]:
            _operation_diagnostic(diagnostic, record)
        for reason in record["reason_codes"]:
            _text(reason, "operation reason code")
        _require(
            record.get("outcome") in ("promoted", "not_promoted"),
            "Unknown terminal promotion outcome.",
        )
        _require(
            record["lifecycle"] == "completed",
            "A terminal receipt must have completed lifecycle.",
        )
        _require(record["candidate_retained"] is True, "Promotion must retain the candidate.")
        for item in record["verified_artifacts"]:
            _artifact(item)
        if record["outcome"] == "promoted":
            _require(
                not record["operation_diagnostics"],
                "A successful promotion cannot contain operation failure diagnostics.",
            )
            _require(
                bool(record["verified_artifacts"]),
                "A promoted target must identify its deliverable artifacts.",
            )
            _require(
                record["validation_state"] == "valid" and not record["blockers"],
                "A promoted receipt cannot contain unsatisfied requirements.",
            )
            _require(
                record["promoted_revision_after"] == record["candidate"]["revision_id"],
                "Promotion must identify its exact candidate.",
            )
        else:
            _require(
                record["promoted_revision_after"] == record["promoted_revision_before"],
                "A refused attempt cannot advance the accepted target.",
            )
            _require(
                bool(record["blockers"] or record["reason_codes"]),
                "A refusal must explain its blockers or reason.",
            )
    return json.loads(_json(record))


def _policy_ref(policy: dict) -> dict:
    return {
        "policy_id": policy["policy_id"],
        "version": policy["version"],
        "sha256": policy["content_sha256"],
    }


def _candidate_ref(candidate: dict) -> dict:
    return {k: candidate[k] for k in ("revision_id", "model_fingerprint", "content_sha256")}


def _scope(candidate: dict, policy: dict) -> dict:
    validate_document(candidate, kind="candidate")
    validate_document(policy, kind="target_policy")
    ref = _policy_ref(policy)
    _require(
        candidate["target_policies"].get(policy["target_id"]) == ref,
        "Candidate does not pin this target's exact policy; create a successor.",
        "binding_mismatch",
    )
    return {
        "candidate": _candidate_ref(candidate),
        "target_id": policy["target_id"],
        "policy": ref,
    }


def make_policy(
    *,
    target_id: str,
    policy_id: str,
    version: int,
    checks: list,
    required_reviews: list | None = None,
    previous_policy: dict | None = None,
    actor: str | None = None,
    rationale: str | None = None,
) -> dict:
    reviews = required_reviews if required_reviews is not None else []
    change = None
    if previous_policy is not None:
        previous = validate_document(previous_policy, kind="target_policy")
        _require(
            previous["target_id"] == target_id,
            "A successor policy cannot switch deliverable targets.",
        )
        change = {
            "previous_policy": _policy_ref(previous),
            "actor": actor,
            "rationale": rationale,
            "requirements_diff": {
                "checks": {"before": previous["checks"], "after": checks},
                "required_reviews": {
                    "before": previous["required_reviews"],
                    "after": reviews,
                },
            },
        }
    return _seal(
        "target_policy",
        target_id=target_id,
        policy_id=policy_id,
        version=version,
        checks=checks,
        required_reviews=reviews,
        change=change,
    )


def make_candidate(
    *,
    revision_id: str,
    inputs: list,
    model: dict,
    policies: list,
    deliverables: dict | None = None,
    parent_revision_id: str | None = None,
    promotion_mode: str = "automatic",
    requested_change: str = "",
    metadata: dict | None = None,
) -> dict:
    _require(isinstance(inputs, list), "inputs must be a list.")
    for item in inputs:
        _artifact(item)
    refs = {}
    for policy in policies:
        validate_document(policy, kind="target_policy")
        _require(policy["target_id"] not in refs, "Candidate target IDs must be unique.")
        refs[policy["target_id"]] = _policy_ref(policy)
    _require(bool(refs), "Candidate must pin at least one target policy.")
    if deliverables is None:
        _require(len(refs) == 1, "Multiple targets require explicit target-specific deliverables.")
        deliverables = {next(iter(refs)): [item for item in inputs if item["role"] == "geometry"]}
    return _seal(
        "candidate",
        revision_id=revision_id,
        parent_revision_id=parent_revision_id,
        inputs=inputs,
        model=model,
        target_policies=refs,
        deliverables=deliverables,
        promotion_mode=promotion_mode,
        model_fingerprint=_model_fingerprint(model, inputs),
        requested_change=requested_change,
        metadata=metadata if metadata is not None else {},
    )


def _spec(policy: dict, check_id: str) -> dict:
    match = next((s for s in policy["checks"] if s["check_id"] == check_id), None)
    _require(match is not None, f"Check {check_id!r} is not defined by the pinned policy.")
    return match


def make_check_result(
    candidate: dict,
    policy: dict,
    check_id: str,
    state: str,
    *,
    observed=None,
    evidence: list | None = None,
    actions: list | None = None,
) -> dict:
    scope = _scope(candidate, policy)
    spec = _spec(policy, check_id)
    return _seal(
        "check_result",
        **scope,
        check_id=check_id,
        definition_version=spec["definition_version"],
        definition_sha256=_digest(spec),
        state=state,
        settings=spec["settings"],
        observed=observed,
        evidence=evidence if evidence is not None else [],
        actions=actions if actions is not None else [],
    )


def make_change_receipt(
    candidate: dict,
    policy: dict,
    checks: list,
    *,
    review_states: dict | None = None,
    promoted_revision_id: str | None = None,
) -> dict:
    scope = _scope(candidate, policy)
    _require(isinstance(checks, list), "checks must be a list of result records.")
    found = {}
    for result in checks:
        validate_document(result, kind="check_result")
        _require(
            all(result.get(k) == v for k, v in scope.items()),
            "Check evidence belongs to another candidate, target, or policy.",
            "binding_mismatch",
        )
        check_id = result["check_id"]
        _require(
            check_id not in found,
            "One receipt cannot silently choose between duplicate check results.",
        )
        spec = _spec(policy, check_id)
        _require(
            result["definition_sha256"] == _digest(spec)
            and result["definition_version"] == spec["definition_version"]
            and result["settings"] == spec["settings"],
            "Check definition/settings changed; rerun for the pinned policy.",
            "binding_mismatch",
        )
        found[check_id] = result
    blockers, warnings, states = [], [], []
    complete = []
    for spec in policy["checks"]:
        check_id = spec["check_id"]
        result = found.get(check_id) or make_check_result(
            candidate, policy, check_id, "unavailable"
        )
        complete.append(result)
        state = result["state"]
        if spec["blocks_on_failure"] and state == "fail":
            states.append("invalid")
            blockers.append(_blocker(result, "required_check_failed"))
        elif spec["require_completion"] and state in ("pending", "unavailable"):
            states.append("pending" if state == "pending" else "unknown")
            blockers.append(
                _blocker(
                    result,
                    (
                        "required_check_pending"
                        if state == "pending"
                        else "required_evidence_unavailable"
                    ),
                )
            )
        elif not spec["blocks_on_failure"] and state != "pass":
            warnings.append(result)
    if not candidate["deliverables"][policy["target_id"]]:
        states.append("unknown")
        blockers.append({"reason": "required_artifacts_missing", "target_id": policy["target_id"]})
    verdict = next((s for s in ("invalid", "pending", "unknown") if s in states), "valid")
    reviews = review_states if review_states is not None else {}
    _require(isinstance(reviews, dict), "review_states must be an object.")
    for review_id in policy["required_reviews"]:
        state = reviews.get(review_id, "missing")
        _require(
            state in ("accepted", "missing", "stale", "needs_change"),
            "Unknown required-review state.",
        )
        if state != "accepted":
            blockers.append(
                {
                    "review_id": review_id,
                    "state": state,
                    "reason": f"required_review_{state}",
                }
            )
    return _seal(
        "change_receipt",
        **scope,
        checks=complete,
        validation_state=verdict,
        review_states=reviews,
        promotion_ready=not blockers,
        blockers=blockers,
        warnings=warnings,
        candidate_retained=True,
        promoted_revision_id=promoted_revision_id,
        artifacts=candidate["deliverables"][policy["target_id"]],
    )


def _blocker(result: dict, reason: str) -> dict:
    return {
        **{
            k: v for k, v in result.items() if k not in {"schema_version", "kind", "content_sha256"}
        },
        "check_result_sha256": result["content_sha256"],
        "reason": reason,
    }


def make_promotion_request(
    candidate: dict,
    policy: dict,
    *,
    project_id: str,
    request_key: str,
    selection_generation: int,
    expected_promoted_revision_id: str | None = None,
) -> dict:
    return _seal(
        "promotion_request",
        **_scope(candidate, policy),
        project_id=project_id,
        request_key=request_key,
        selection_generation=selection_generation,
        promotion_mode=candidate["promotion_mode"],
        expected_promoted_revision_id=expected_promoted_revision_id,
    )


def validate_replay(original: dict, retry: dict) -> dict:
    """Validate a known-key replay; a store must look up and return its outcome."""
    validate_document(original, kind="promotion_request")
    validate_document(retry, kind="promotion_request")
    _require(
        all(original[k] == retry[k] for k in ("project_id", "target_id", "request_key")),
        "Requests do not share a project, target, and request key.",
        "request_scope_mismatch",
    )
    _require(
        original == retry,
        "Request key already binds different inputs; use a new key for a new attempt.",
        "request_key_conflict",
    )
    return validate_document(original)


def make_operation_status(request: dict, lifecycle: str, *, jobs: list | None = None) -> dict:
    request = validate_document(request, kind="promotion_request")
    return _seal(
        "operation_status",
        request_key=request["request_key"],
        request_sha256=request["content_sha256"],
        project_id=request["project_id"],
        candidate=request["candidate"],
        target_id=request["target_id"],
        policy=request["policy"],
        promotion_mode=request["promotion_mode"],
        lifecycle=lifecycle,
        outcome=None,
        jobs=jobs if jobs is not None else [],
    )


def make_promotion_receipt(
    request: dict,
    checked: dict,
    *,
    outcome: str,
    promoted_revision_after: str | None,
    reason_codes: list | None = None,
    operation_diagnostics: list | None = None,
) -> dict:
    """Describe a terminal outcome without performing the target transition.

    Each operation diagnostic supplies ``reason``, ``observed``, and nonempty
    ``actions``. The observed snapshot contains candidate/policy references
    (or None when absent), selection_generation, and promoted_revision_id.
    Requested context is copied from the pinned request, never supplied by the
    caller. Diagnostics explain operation refusals, not failed model checks.
    """
    request = validate_document(request, kind="promotion_request")
    checked = validate_document(checked, kind="change_receipt")
    provided_diagnostics = operation_diagnostics if operation_diagnostics is not None else []
    _require(isinstance(provided_diagnostics, list), "operation_diagnostics must be a list.")
    diagnostics = []
    for diagnostic in provided_diagnostics:
        _require(
            isinstance(diagnostic, dict) and set(diagnostic) == {"reason", "observed", "actions"},
            "Provide an operation reason, observed snapshot, and actions; requested context comes from the request.",
        )
        _text(diagnostic["reason"], "operation diagnostic reason")
        diagnostics.append(
            {
                **diagnostic,
                "requested": {
                    "candidate": request["candidate"],
                    "policy": request["policy"],
                    "selection_generation": request["selection_generation"],
                    "promoted_revision_id": request["expected_promoted_revision_id"],
                },
            }
        )
    codes = reason_codes if reason_codes is not None else []
    _require(isinstance(codes, list), "reason_codes must be a list.")
    for reason in codes:
        _text(reason, "operation reason code")
    codes = list(dict.fromkeys(codes + [diagnostic["reason"] for diagnostic in diagnostics]))
    _require(
        all(request[k] == checked[k] for k in ("candidate", "target_id", "policy")),
        "Promotion request and checked evidence identify different inputs.",
        "binding_mismatch",
    )
    before = checked["promoted_revision_id"]
    if outcome == "promoted":
        _require(
            bool(checked["artifacts"]),
            "No deliverable artifacts are available for this target.",
            "required_artifacts_missing",
        )
        _require(
            checked["promotion_ready"],
            "Required evidence or reviews are unsatisfied.",
            "promotion_not_ready",
        )
        _require(
            request["expected_promoted_revision_id"] == before,
            "Accepted target changed; reconcile before another promotion.",
            "promotion_precondition_failed",
        )
        _require(
            promoted_revision_after == request["candidate"]["revision_id"],
            "Promotion must name the exact requested candidate.",
        )
    else:
        _require(
            promoted_revision_after == before,
            "A refused attempt cannot advance the accepted target.",
        )
        _require(
            bool(checked["blockers"] or codes),
            "A refusal must explain its blockers or reason.",
        )
    return _seal(
        "promotion_receipt",
        project_id=request["project_id"],
        request_key=request["request_key"],
        request_sha256=request["content_sha256"],
        candidate=request["candidate"],
        target_id=request["target_id"],
        policy=request["policy"],
        promotion_mode=request["promotion_mode"],
        lifecycle="completed",
        outcome=outcome,
        validation_state=checked["validation_state"],
        promoted_revision_before=before,
        promoted_revision_after=promoted_revision_after,
        candidate_retained=True,
        blockers=checked["blockers"],
        warnings=checked["warnings"],
        reason_codes=codes,
        operation_diagnostics=diagnostics,
        check_receipt_sha256=checked["content_sha256"],
        review_states=checked["review_states"],
        verified_artifacts=checked["artifacts"] if outcome == "promoted" else [],
    )
