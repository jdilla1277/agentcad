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
from datetime import datetime, timezone
from pathlib import Path

from agentcad import change_contract as contract
from agentcad.change_store import (
    Attempt,
    ChangeStore,
    ChangeStoreError,
    _digest,
    _sealed,
    _write_bytes_once,
)
from agentcad.project import ProjectError
from agentcad.versioning import (
    VersionReservation,
    _merge_version_entry,
    _version_lock,
    _file_lock,
    _reserve_version_unlocked,
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


def _current_target(manifest: dict, state: dict, target_id: str) -> dict | None:
    target = state["targets"].get(target_id)
    if (
        target_id == "cad"
        and target
        and target["version"] != _legacy_current(manifest)["version"]
    ):
        # A legacy writer/recover can deliberately select another accepted
        # model. It has no known retained ID, just like a pre-retention history.
        # Requests already in flight still retain their original guard.
        return None
    return target


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
        manifest = self._manifest()
        return _current_target(manifest, self._state(manifest), target_id)

    def response_fields(self, request_key: str) -> dict:
        """Describe the original decision, including on an import replay."""
        operation = self.find(request_key)
        if operation is None:
            return {}
        return self._response_fields(operation)

    @staticmethod
    def _response_fields(operation: dict) -> dict:
        return {
            "promotion": operation["result"],
            "current_advanced": operation["result"].get("outcome") == "promoted",
            "accepted_version_before": operation["legacy_before"]["version"],
            "accepted_version_after": (
                operation["accepted_version"]
                if operation["result"]["kind"] == "promotion_receipt"
                else operation["legacy_before"]["version"]
            ),
        }

    def automatic_seed(self, revision_id: str) -> dict:
        """Pin the predecessor before CAD execution, under the version lock."""
        manifest = self._manifest()
        state = self._state(manifest)
        if "promotion_state" not in manifest:
            self._write(manifest, state)
        accepted = _current_target(manifest, state, "cad") or {}
        return {
            "request_key": "auto:" + revision_id,
            "project_id": state["project_id"],
            "expected_promoted_revision_id": accepted.get("revision_id"),
            "legacy_before": _legacy_current(manifest),
        }

    def ensure_automatic(self, revision_id: str) -> None:
        """Recover enrollment even if execution stopped just after core sealing.

        Only new normal deferred requests carry a seed. Older retained attempts
        and staged experiments cannot acquire automatic intent retroactively.
        """
        if (
            not Attempt(self.changes, revision_id)
            ._read("request.json", "attempt_request")
            .get("automatic_promotion")
        ):
            return
        saved = self.changes.read(revision_id)
        if (
            saved["request"].get("automatic_promotion")
            and saved["receipt"]
            and any(
                row["reason"] == "required_check_pending"
                for row in saved["receipt"]["blockers"]
            )
        ):
            self.begin(revision_id, request_key="auto:" + revision_id, automatic=True)

    def automatic_fields(self, revision_id: str, *, original: bool = False) -> dict:
        """Read the live operation separately from immutable execution results."""
        key = "auto:" + revision_id
        manifest = self._manifest()
        state = self._state(manifest)
        operation = state["operations"].get(_key(key))
        if not operation or not operation.get("automatic_intent"):
            return {}
        if original:
            # A deferred execution response describes the original pending
            # handoff, even if another caller already completed its checks.
            operation = copy.deepcopy(operation)
            operation["result"] = self._automatic_pending(operation["request"])
        accepted = (
            operation["accepted_version"]
            if operation["result"].get("outcome") == "promoted"
            else None
        )
        plan = operation.get("materialization")
        now = _current_target(manifest, state, "cad")
        return {
            **self._response_fields(operation),
            "current_advanced": False,
            "request_key": key,
            "accepted_revision_now": now["revision_id"] if now else None,
            "accepted_version_now": _legacy_current(manifest)["version"],
            "working_revision_now": self.changes.working_revision_id,
            "outputs": (
                (
                    dict(plan["meta"]["outputs"])
                    if plan
                    else {"step": accepted["path"] + "output.step"}
                )
                if accepted
                else {"step": None}
            ),
        }

    @staticmethod
    def _automatic_pending(request: dict) -> dict:
        return contract.make_operation_status(
            request,
            "pending",
            jobs=[
                {
                    "kind": "declared_checks",
                    "revision_id": request["candidate"]["revision_id"],
                    "action": "complete_checks",
                }
            ],
        )

    def complete_automatic(self, revision_id: str) -> dict:
        self.ensure_automatic(revision_id)
        operation = self.find("auto:" + revision_id)
        if not operation or not operation.get("automatic_intent"):
            return {}
        result = self.promote(revision_id, request_key="auto:" + revision_id)
        return {
            **self.automatic_fields(revision_id),
            "current_advanced": not result["replayed"]
            and result["promotion"].get("outcome") == "promoted",
            "replayed": result["replayed"],
        }

    def _cancel_superseded(self, manifest: dict, state: dict) -> bool:
        """Cancel under the selection writer's root lock; never revive on return."""
        selection = self.changes._state()
        changed = False
        for operation in state["operations"].values():
            request = operation["request"]
            revision = request["candidate"]["revision_id"]
            if (
                not operation.get("automatic_intent")
                or operation["result"]["kind"] == "promotion_receipt"
            ):
                continue
            if (
                selection["revision_id"] == revision
                and selection["selection_generation"] == request["selection_generation"]
            ):
                continue
            saved = self.changes.read(revision)
            before = (_current_target(manifest, state, request["target_id"]) or {}).get(
                "revision_id"
            )
            checked = contract.make_change_receipt(
                saved["candidate"],
                saved["policy"],
                self.changes.read_checks(revision)["receipt"]["checks"],
                promoted_revision_id=before,
            )
            operation["result"] = contract.make_promotion_receipt(
                request,
                checked,
                outcome="not_promoted",
                promoted_revision_after=before,
                operation_diagnostics=[
                    {
                        "reason": "selection_changed",
                        "observed": {
                            "candidate": None,
                            "policy": None,
                            "selection_generation": selection["selection_generation"],
                            "promoted_revision_id": before,
                        },
                        "actions": [
                            {
                                "action": "reconcile",
                                "revision_id": revision,
                                "message": "Automatic acceptance was cancelled by a newer selection. Inspect the revisions and use a new promotion key for a deliberate attempt.",
                            }
                        ],
                    }
                ],
            )
            operation["accepted_version"] = _legacy_current(manifest)["version"]
            changed = True
        return changed

    def cancel_superseded(self) -> None:
        """Called by selection writers while they already own the version lock."""
        manifest = self._manifest()
        state = self._state(manifest)
        if self._cancel_superseded(manifest, state):
            self._write(manifest, state)

    def begin(
        self,
        revision_id: str,
        *,
        request_key: str,
        target_id: str = "cad",
        automatic: bool = False,
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
            accepted = _current_target(manifest, state, target_id) or {}
            seed = saved["request"].get("automatic_promotion") if automatic else None
            if automatic and (
                not seed
                or seed["project_id"] != state["project_id"]
                or seed["request_key"] != request_key
                or candidate["promotion_mode"] != "automatic"
            ):
                raise ChangeStoreError(
                    "Automatic intent must match its original normal-run request."
                )
            request = contract.make_promotion_request(
                candidate,
                policy,
                project_id=state["project_id"],
                request_key=request_key,
                selection_generation=saved["request"]["selection_generation"],
                expected_promoted_revision_id=(
                    seed["expected_promoted_revision_id"]
                    if seed
                    else accepted.get("revision_id")
                ),
            )
            state["operations"][key] = {
                "request": request,
                "result": (
                    self._automatic_pending(request)
                    if automatic
                    else contract.make_operation_status(request, "pending")
                ),
                "legacy_before": (
                    seed["legacy_before"] if seed else _legacy_current(manifest)
                ),
                "accepted_version": None,
                **({"automatic_intent": True} if automatic else {}),
            }
            self._cancel_superseded(manifest, state)
            self._write(manifest, state)
            return copy.deepcopy(request)

    def commit(
        self,
        request_key: str,
        reservation: VersionReservation | None,
        meta: dict,
        entry: dict,
    ) -> dict:
        """Recheck pinned evidence/selection and atomically accept or refuse.

        A terminal key always returns its saved receipt. New evidence after a
        refusal needs a new key. Unreadable or corrupted inputs raise without
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
            accepted = _current_target(manifest, state, request["target_id"]) or {}
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
            # Pending is an operation state, not a terminal refusal. Failures,
            # unavailable requirements, and supersession still settle forever.
            if (
                operation.get("automatic_intent")
                and not reasons
                and checked["blockers"]
                and all(
                    blocker["reason"] == "required_check_pending"
                    for blocker in checked["blockers"]
                )
            ):
                return copy.deepcopy(operation["result"])
            result = contract.make_promotion_receipt(
                request,
                checked,
                outcome="promoted" if promote else "not_promoted",
                promoted_revision_after=revision if promote else before,
                operation_diagnostics=diagnostics,
            )
            if promote:
                if reservation is None:
                    reservation, meta, entry = self._materialize(
                        manifest, state, operation, saved
                    )
                self._verify_materialized(reservation, meta, entry, saved)
                _merge_version_entry(manifest, reservation, entry, advance_current=True)
                # Metadata is not acceptance authority. If the process dies
                # before manifest replacement, this is only an orphan version.
                # Keep the recovery binding in the caller's metadata too:
                # optional artifact updates persist this same object later.
                meta["promotion_request_key"] = request_key
                atomic_write_json(reservation.path / "meta.json", meta)
                state["targets"][request["target_id"]] = {
                    "revision_id": revision,
                    "candidate": request["candidate"],
                    "policy": request["policy"],
                    "version": copy.deepcopy(entry),
                }
            operation["result"] = result
            operation["accepted_version"] = _legacy_current(manifest)["version"]
            self._write(manifest, state)
            meta["promotion"] = copy.deepcopy(result)
            return result

    def promote(
        self, revision_id: str, *, request_key: str, target_id: str = "cad"
    ) -> dict:
        """Accept retained bytes, resuming the same reservation after interruption."""
        key = _key(request_key)
        lock = self.changes._path(".agentcad/promotions/" + key + ".lock")
        with _file_lock(lock):
            known = self.find(request_key)
            if known and (
                known["request"]["candidate"]["revision_id"] != revision_id
                or known["request"]["target_id"] != target_id
            ):
                raise ProjectError(
                    "promotion_request_conflict",
                    "This key already identifies a different revision or target.",
                    "Use the original revision/target to replay, or a new key for another promotion.",
                )
            if known and known["result"]["kind"] == "promotion_receipt":
                return {"promotion": known["result"], "replayed": True}
            self.begin(revision_id, request_key=request_key, target_id=target_id)
            result = self.commit(request_key, None, {}, {})
            return {"promotion": result, "replayed": False}

    def _materialize(self, manifest, state, operation, saved):
        """Persist a reservation before copying bytes; never execute source CAD.

        Only local verified file I/O runs under the root lock. Kernel work and
        optional rendering do not run in this transaction.
        """
        attempt = Attempt(self.changes, saved["request"]["revision_id"])
        plan = operation.get("materialization")
        if plan is None:
            reservation = _reserve_version_unlocked(
                self.root, manifest, saved["request"]["label"]
            )
            prefix = reservation.dir_name + "/"
            source_name = (
                "script.py"
                if saved["request"]["command"] == "run"
                else "source" + attempt.source_path.suffix
            )
            from agentcad import __version__

            core = attempt._read("core.json", "attempt_core")["response"]
            meta = {
                "command": "promote",
                "status": "success",
                "version": reservation.number,
                "label": reservation.label,
                "source": "script" if source_name == "script.py" else "import",
                "runtime": saved["request"]["model"].get("runtime"),
                "tool_version": __version__,
                "created": datetime.now(timezone.utc).isoformat(),
                "core": {"status": "success"},
                "change": {"revision_id": attempt.revision_id},
                "metrics": core.get("metrics"),
                "validation": core.get("validation"),
                "validation_profile": saved["request"]["model"].get(
                    "validation_profile", "deliverable"
                ),
                "outputs": {
                    "step": prefix + "output.step",
                    "script" if source_name == "script.py" else "source": prefix
                    + source_name,
                },
                "promotion_request_key": operation["request"]["request_key"],
            }
            # Use only model metadata sealed before staging returned; optional
            # viewer/render artifacts and mutable working source are not inputs.
            for field in ("parts", "groups", "params"):
                if field in core:
                    meta[field] = copy.deepcopy(core[field])
            entry = {
                "version": reservation.number,
                "label": reservation.label,
                "status": "success",
                "source": meta["source"],
                "path": prefix,
            }
            plan = {
                "number": reservation.number,
                "label": reservation.label,
                "dir_name": reservation.dir_name,
                "meta": meta,
                "entry": entry,
            }
            operation["materialization"] = plan
            # Interrupted requests must resume this directory, not reserve a
            # second one. The accepted pointer and terminal result stay put.
            self._write(manifest, state)
        path = self.changes._path(plan["dir_name"])
        if path.parent != self.root or not path.is_dir():
            raise ChangeStoreError(
                "The promotion's reserved directory is missing or invalid; restore it before retrying this key."
            )
        reservation = VersionReservation(
            plan["number"], plan["label"], plan["dir_name"], path
        )
        meta, entry = copy.deepcopy(plan["meta"]), copy.deepcopy(plan["entry"])
        # Mark unaccepted files before exposing output.step to legacy recovery.
        atomic_write_json(
            path / "meta.json",
            {**meta, "status": "pending_promotion", "current_advanced": False},
        )
        source_name = (
            "script.py"
            if saved["request"]["command"] == "run"
            else "source" + attempt.source_path.suffix
        )
        _write_bytes_once(
            self.changes._path(plan["dir_name"] + "/" + source_name),
            attempt.source_path.read_bytes(),
        )
        (artifact,) = saved["candidate"]["deliverables"]["cad"]
        _write_bytes_once(
            self.changes._path(plan["dir_name"] + "/output.step"),
            attempt._verify(artifact).read_bytes(),
        )
        return reservation, meta, entry

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
