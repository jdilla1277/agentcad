"""Durable CAD promotion decisions, committed with the accepted version.

The project manifest is the authority: its accepted target and terminal receipt
are published by the same atomic replacement. Version metadata is written first
and may remain as an interrupted, unaccepted reservation. No mutable CAD source
is executed here. CLI staging, pending automatic intent and request-key controls
are adapters on this layer, not additional acceptance mechanisms.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path

from agentcad import change_contract as contract
from agentcad.change_store import (
    Attempt,
    ChangeStore,
    ChangeStoreError,
    _digest,
    _sealed,
)
from agentcad.project import ProjectError
from agentcad.versioning import (
    VersionReservation,
    _merge_version_entry,
    _version_lock,
    atomic_write_json,
)


def _key(request_key: str) -> str:
    if (
        not isinstance(request_key, str)
        or not request_key.strip()
        or len(request_key) > 200
    ):
        raise ChangeStoreError(
            "Use a nonempty promotion request key of at most 200 characters."
        )
    return hashlib.sha256(request_key.encode()).hexdigest()


def _legacy_current(manifest: dict) -> dict:
    label = manifest.get("current")
    entry = next(
        (
            v
            for v in reversed(manifest.get("versions", []))
            if v.get("label") == label and v.get("status") == "success"
        ),
        None,
    )
    return {"current": label, "version": copy.deepcopy(entry)}


class PromotionRefused(ProjectError):
    def __init__(self, receipt: dict):
        self.receipt = receipt
        super().__init__(
            "promotion_refused",
            "The candidate was retained but not accepted; inspect the promotion receipt.",
            "Inspect the working selection and accepted revision, then rerun the current script or import to create a successor.",
        )

    def payload(self, command: str) -> dict:
        return {
            "command": command,
            "status": "not_promoted",
            "message": str(self),
            "promotion": self.receipt,
            "current_advanced": False,
            "outputs": {"step": None},
            "suggestion": self.suggestion,
            "more_at": "agentcad docs check-spec",
        }


class PromotionStore:
    def __init__(self, build_root: str | Path):
        self.changes = ChangeStore(build_root)
        self.root = self.changes.root

    def _manifest(self) -> dict:
        # Apply the retention store's containment/symlink checks to state too.
        path = self.changes._path("agentcad.json")
        try:
            result = json.loads(path.read_text())
            if not isinstance(result, dict) or not isinstance(
                result.get("versions"), list
            ):
                raise ValueError("project history must contain versions")
            return result
        except (OSError, ValueError) as exc:
            raise ChangeStoreError(f"Cannot read promotion history: {exc}") from exc

    def _state(self, manifest: dict) -> dict:
        if "promotion_state" not in manifest:
            return _sealed(
                "promotion_state",
                project_id="p_" + uuid.uuid4().hex,
                targets={},
                operations={},
            )
        state = copy.deepcopy(manifest["promotion_state"])
        try:
            if (
                set(state)
                != {
                    "schema_version",
                    "kind",
                    "content_sha256",
                    "project_id",
                    "targets",
                    "operations",
                }
                or state["schema_version"] != 1
                or state["kind"] != "promotion_state"
                or state["content_sha256"]
                != _digest({k: v for k, v in state.items() if k != "content_sha256"})
                or not isinstance(state["project_id"], str)
                or not isinstance(state["targets"], dict)
                or not isinstance(state["operations"], dict)
            ):
                raise ValueError("promotion state integrity check failed")
            for key, operation in state["operations"].items():
                request, result = operation["request"], operation["result"]
                contract.validate_document(request, kind="promotion_request")
                contract.validate_document(result)
                if (
                    key != _key(request["request_key"])
                    or request["project_id"] != state["project_id"]
                    or result["kind"] not in ("operation_status", "promotion_receipt")
                    or result["request_sha256"] != request["content_sha256"]
                    or any(
                        result[field] != request[field]
                        for field in (
                            "candidate",
                            "policy",
                            "target_id",
                            "project_id",
                            "request_key",
                            "promotion_mode",
                        )
                    )
                ):
                    raise ValueError("operation is not bound to its original request")
            return state
        except (ValueError, TypeError, KeyError) as exc:
            raise ChangeStoreError(f"Cannot read promotion state: {exc}") from exc

    def _write(self, manifest: dict, state: dict) -> None:
        manifest["promotion_state"] = _sealed(
            "promotion_state",
            **{k: state[k] for k in ("project_id", "targets", "operations")},
        )
        atomic_write_json(self.root / "agentcad.json", manifest)

    def read(self, request_key: str) -> dict:
        operation = self.find(request_key)
        if operation is None:
            raise ChangeStoreError(
                "Unknown promotion request key; use the original build directory."
            )
        return operation

    def find(self, request_key: str) -> dict | None:
        state = self._state(self._manifest())
        return copy.deepcopy(state["operations"].get(_key(request_key)))

    def accepted(self, target_id: str) -> dict | None:
        return self._state(self._manifest())["targets"].get(target_id)

    def begin(
        self, revision_id: str, *, request_key: str, target_id: str = "cad"
    ) -> dict:
        """Persist a known-key request before an acceptance transaction starts.

        A repeated key returns its original selection and policy, even after
        later edits. It cannot bind another revision or target. The CAD adapter
        deliberately has no authority to accept robot packages.
        """
        key = _key(request_key)
        self.changes._path(".agentcad")
        with _version_lock(self.root):
            manifest = self._manifest()
            state = self._state(manifest)
            saved = self.changes.read(revision_id)
            candidate, policy = saved["candidate"], saved["policy"]
            if candidate is None:
                raise ChangeStoreError(
                    "Wait for the retained core snapshot before requesting promotion."
                )
            if target_id != "cad" or policy["target_id"] != target_id:
                raise ChangeStoreError(
                    "This adapter can promote only a candidate's pinned CAD target."
                )
            existing = state["operations"].get(key)
            if existing:
                retry = contract.make_promotion_request(
                    candidate,
                    policy,
                    project_id=state["project_id"],
                    request_key=request_key,
                    selection_generation=existing["request"]["selection_generation"],
                    expected_promoted_revision_id=existing["request"][
                        "expected_promoted_revision_id"
                    ],
                )
                contract.validate_replay(existing["request"], retry)
                return copy.deepcopy(existing["request"])
            accepted = state["targets"].get(target_id) or {}
            request = contract.make_promotion_request(
                candidate,
                policy,
                project_id=state["project_id"],
                request_key=request_key,
                selection_generation=saved["request"]["selection_generation"],
                expected_promoted_revision_id=accepted.get("revision_id"),
            )
            state["operations"][key] = {
                "request": request,
                "result": contract.make_operation_status(request, "pending"),
                "legacy_before": _legacy_current(manifest),
                "accepted_version": None,
            }
            self._write(manifest, state)
            return copy.deepcopy(request)

    def commit(
        self, request_key: str, reservation: VersionReservation, meta: dict, entry: dict
    ) -> dict:
        """Recheck pinned evidence/selection and atomically accept or refuse.

        A terminal key always returns its saved receipt. New evidence after a
        refusal needs a new key. Unknown or corrupted evidence raises without
        advancing current; the pending request remains recoverable.
        """
        key = _key(request_key)
        with _version_lock(self.root):
            manifest = self._manifest()
            state = self._state(manifest)
            if key not in state["operations"]:
                raise ChangeStoreError(
                    "Persist the promotion request before attempting acceptance."
                )
            operation = state["operations"][key]
            request = operation["request"]
            if operation["result"]["kind"] == "promotion_receipt":
                return copy.deepcopy(operation["result"])
            revision = request["candidate"]["revision_id"]
            saved = self.changes.read(revision)
            candidate, policy = saved["candidate"], saved["policy"]
            # Reconstruct the entire pinned request instead of trusting a
            # candidate ID or policy display name alone.
            contract.validate_replay(
                request,
                contract.make_promotion_request(
                    candidate,
                    policy,
                    project_id=state["project_id"],
                    request_key=request_key,
                    selection_generation=request["selection_generation"],
                    expected_promoted_revision_id=request[
                        "expected_promoted_revision_id"
                    ],
                ),
            )
            accepted = state["targets"].get(request["target_id"]) or {}
            before = accepted.get("revision_id")
            evidence = self.changes.read_checks(revision)["receipt"]
            checked = contract.make_change_receipt(
                candidate, policy, evidence["checks"], promoted_revision_id=before
            )
            selection = self.changes._state()
            reasons = []
            if (
                selection["revision_id"] != revision
                or selection["selection_generation"] != request["selection_generation"]
            ):
                reasons.append("selection_changed")
            if (
                before != request["expected_promoted_revision_id"]
                or _legacy_current(manifest) != operation["legacy_before"]
                or (
                    accepted
                    and accepted["version"] != _legacy_current(manifest)["version"]
                )
            ):
                reasons.append("accepted_target_changed")
            diagnostics = []
            if reasons:
                observed = (
                    self.changes.read(selection["revision_id"])
                    if selection["revision_id"]
                    else None
                )
                diagnostics = [
                    {
                        "reason": reason,
                        "observed": {
                            "candidate": (
                                observed["receipt"]["candidate"]
                                if observed and observed["receipt"]
                                else None
                            ),
                            "policy": (
                                observed["receipt"]["policy"]
                                if observed and observed["receipt"]
                                else None
                            ),
                            "selection_generation": selection["selection_generation"],
                            "promoted_revision_id": before,
                        },
                        "actions": [
                            {
                                "action": "reconcile",
                                "revision_id": revision,
                                "message": "Inspect the working and accepted revisions; use a new request key after reconciliation.",
                            }
                        ],
                    }
                    for reason in reasons
                ]
            promote = checked["promotion_ready"] and not reasons
            result = contract.make_promotion_receipt(
                request,
                checked,
                outcome="promoted" if promote else "not_promoted",
                promoted_revision_after=revision if promote else before,
                operation_diagnostics=diagnostics,
            )
            if promote:
                self._verify_materialized(reservation, meta, entry, saved)
                _merge_version_entry(manifest, reservation, entry, advance_current=True)
                # Metadata is not acceptance authority. If the process dies
                # before manifest replacement, this is only an orphan version.
                atomic_write_json(
                    reservation.path / "meta.json",
                    {
                        **meta,
                        "promotion_request_key": request_key,
                    },
                )
                operation["accepted_version"] = copy.deepcopy(entry)
                state["targets"][request["target_id"]] = {
                    "revision_id": revision,
                    "candidate": request["candidate"],
                    "policy": request["policy"],
                    "version": copy.deepcopy(entry),
                }
            operation["result"] = result
            self._write(manifest, state)
            meta["promotion"] = copy.deepcopy(result)
            return result

    def _verify_materialized(self, reservation, meta, entry, saved):
        if (
            reservation.path.parent.resolve() != self.root
            or reservation.path.name != reservation.dir_name
            or entry.get("version") != reservation.number
            or entry.get("label") != reservation.label
            or entry.get("path") != reservation.dir_name + "/"
            or entry.get("status") != "success"
            or meta.get("status") != "success"
            or meta.get("version") != reservation.number
            or meta.get("label") != reservation.label
            or meta.get("outputs", {}).get("step")
            != reservation.dir_name + "/output.step"
            or meta.get("change", {}).get("revision_id")
            != saved["request"]["revision_id"]
        ):
            raise ChangeStoreError(
                "Accepted version must match the reserved retained candidate."
            )
        attempt = Attempt(self.changes, saved["request"]["revision_id"])
        for artifact in saved["candidate"]["deliverables"]["cad"]:
            expected = attempt._verify(artifact).read_bytes()
            delivered = self.changes._path(reservation.dir_name + "/output.step")
            if delivered.read_bytes() != expected:
                raise ChangeStoreError(
                    "Delivered STEP bytes differ from the checked snapshot."
                )
        source_name = (
            "script.py"
            if saved["request"]["command"] == "run"
            else "source" + attempt.source_path.suffix
        )
        source = self.changes._path(reservation.dir_name + "/" + source_name)
        if source.read_bytes() != attempt.source_path.read_bytes():
            raise ChangeStoreError(
                "Delivered source differs from the retained source snapshot."
            )
