"""Read-only history and saved-evidence comparisons for retained changes."""

from __future__ import annotations

import math

from agentcad.change_promotion import PromotionStore, _current_target, _legacy_current
from agentcad.change_store import Attempt, ChangeStore, ChangeStoreError, _REVISION
from agentcad.project import ProjectError


class ChangeHistoryError(ProjectError):
    def __init__(self, reason, message):
        super().__init__(
            reason,
            message,
            "Use changes list to find a retained revision ID in the same build directory; see agentcad docs changes.",
        )


def _policy_ref(policy):
    if not policy:
        return None
    return {
        "policy_id": policy["policy_id"],
        "version": policy["version"],
        "sha256": policy["content_sha256"],
    }


def _delta(before, after):
    numeric = all(
        type(value) in (int, float) and math.isfinite(value)
        for value in (before, after)
    )
    return {
        "baseline": before,
        "candidate": after,
        "delta": after - before if numeric else None,
    }


class ChangeHistory:
    def __init__(self, build_root):
        self.store = ChangeStore(build_root)
        self.promotions = PromotionStore(build_root)

    def _selection(self):
        manifest = self.promotions._manifest()
        state = self.promotions._state(manifest)
        accepted = _current_target(manifest, state, "cad")
        working = self.store._state()
        return {
            "working_revision_id": working["revision_id"],
            "selection_generation": working["selection_generation"],
            "accepted_revision_id": accepted["revision_id"] if accepted else None,
            "accepted_version": _legacy_current(manifest)["version"],
        }, state

    def _resolve(self, reference, selection):
        if reference in ("working", "accepted"):
            revision = selection[reference + "_revision_id"]
            if not revision:
                raise ChangeHistoryError(
                    (
                        "accepted_change_unavailable"
                        if reference == "accepted"
                        else "working_change_unavailable"
                    ),
                    f"No retained {reference} revision is available. Older accepted numbered versions may have no retained change ID; use an explicit retained ID or diff saved CAD files.",
                )
            return revision
        if not isinstance(reference, str) or not _REVISION.fullmatch(reference):
            raise ChangeHistoryError(
                "invalid_revision_reference",
                "Use a returned r_ revision ID, working, or accepted. Labels and file paths are not retained revision identities.",
            )
        return reference

    def _read(self, revision):
        try:
            saved = self.store.read(revision)
            checked = self.store.read_checks(revision)
            core = (
                Attempt(self.store, revision)._read("core.json", "attempt_core")[
                    "response"
                ]
                if saved["candidate"]
                else {}
            )
            return saved, checked, core
        except (OSError, ValueError) as exc:
            raise ChangeHistoryError(
                "change_evidence_unavailable",
                f"Cannot verify retained change {revision}: {exc}",
            ) from exc

    def _summary(self, saved, checked, selection):
        request, receipt = saved["request"], checked["receipt"]
        return {
            "revision_id": request["revision_id"],
            "parent_revision_id": request["parent_revision_id"],
            "selection_generation": request["selection_generation"],
            "started_ns": request["started_ns"],
            "label": request["label"],
            "command": request["command"],
            "promotion_mode": request["promotion_mode"],
            "lifecycle": saved["status"],
            "result_status": (
                saved["outcome"]["response"].get("status") if saved["outcome"] else None
            ),
            "core_available": saved["candidate"] is not None,
            "geometry_available": bool(
                (saved["candidate"] or {}).get("deliverables", {}).get("cad")
            ),
            "validation_state": receipt["validation_state"] if receipt else "pending",
            "check_lifecycle": checked["lifecycle"],
            "blocker_count": len(receipt["blockers"]) if receipt else 0,
            "warning_count": len(receipt["warnings"]) if receipt else 0,
            "is_working": request["revision_id"] == selection["working_revision_id"],
            "is_accepted": request["revision_id"] == selection["accepted_revision_id"],
            "integrity": "verified",
        }

    def _artifact(self, artifact):
        return (
            {**artifact, "path": str(self.store.root / artifact["path"])}
            if artifact
            else None
        )

    def _checks(self, saved, checked):
        receipt = checked["receipt"]
        specs = {
            row["check_id"]: row for row in (saved["policy"] or {}).get("checks", [])
        }
        rows = []
        for result in (receipt or {}).get("checks", []):
            spec = specs[result["check_id"]]
            rows.append(
                {
                    **{
                        key: result[key]
                        for key in (
                            "check_id",
                            "state",
                            "definition_version",
                            "definition_sha256",
                            "settings",
                            "observed",
                            "evidence",
                            "actions",
                        )
                    },
                    "require_completion": spec["require_completion"],
                    "blocks_on_failure": spec["blocks_on_failure"],
                }
            )
        return {
            "lifecycle": checked["lifecycle"],
            "receipt": checked["receipt_path"],
            "validation_state": receipt["validation_state"] if receipt else "pending",
            "promotion_ready": receipt["promotion_ready"] if receipt else False,
            "results": rows,
            "blockers": (receipt or {}).get("blockers", []),
            "warnings": (receipt or {}).get("warnings", []),
        }

    def _show(self, revision, selection, state):
        saved, checked, core = self._read(revision)
        request, outcome = saved["request"], saved["outcome"]
        attempt = Attempt(self.store, revision)
        geometry = (saved["candidate"] or {}).get("deliverables", {}).get("cad", [])
        response = outcome["response"] if outcome else {}
        diagnostics = {
            key: str(response.get(key) or "")[:2000]
            for key in ("message", "error", "script_output")
        }
        diagnostics.update(
            path=str(attempt.directory / "outcome.json") if outcome else None,
            truncated=any(
                len(str(response.get(key) or "")) > 2000 for key in diagnostics
            ),
        )
        static = (
            response.get("checks", [])
            if response.get("status") == "validation_error"
            else []
        )
        diagnostics.update(
            static_checks=[
                {
                    key: str(check.get(key) or "")[:2000]
                    for key in ("check", "severity", "message")
                }
                for check in static[:20]
            ],
            static_check_count=len(static),
            static_checks_omitted=max(0, len(static) - 20),
        )
        diagnostics["truncated"] = (
            diagnostics["truncated"]
            or len(static) > 20
            or any(
                len(str(check.get(key) or "")) > 2000
                for check in static
                for key in ("check", "severity", "message")
            )
        )
        artifacts = (outcome or {}).get("artifacts", [])
        promotions = [
            {
                key: value
                for key, value in self.promotions._response_fields(op).items()
                if key != "current_advanced"
            }
            for op in state["operations"].values()
            if op["request"]["candidate"]["revision_id"] == revision
        ]
        return {
            "change": self._summary(saved, checked, selection),
            "selection": selection,
            "source": self._artifact(request["source"]),
            "geometry": self._artifact(geometry[0]) if geometry else None,
            "model": request["model"],
            "policy": saved["policy"] or request.get("check_policy"),
            "checks": self._checks(saved, checked),
            "metrics": core.get("metrics"),
            "parts": core.get("parts", []),
            "groups": core.get("groups", []),
            "params": core.get("params"),
            "diagnostics": diagnostics,
            "records": {
                "request": str(attempt.directory / "request.json"),
                "core": (
                    str(attempt.directory / "core.json") if saved["candidate"] else None
                ),
                "candidate": (
                    str(attempt.directory / "candidate.json")
                    if saved["candidate"]
                    else None
                ),
                "policy": (
                    str(attempt.directory / "policy.json") if saved["policy"] else None
                ),
                "original_receipt": (
                    str(attempt.directory / "receipt.json")
                    if saved["receipt"]
                    else None
                ),
                "outcome": diagnostics["path"],
                "promotion_history": str(self.store.root / "agentcad.json"),
            },
            "artifacts": [self._artifact(artifact) for artifact in artifacts[:50]],
            "artifact_count": len(artifacts),
            "artifacts_omitted": max(0, len(artifacts) - 50),
            "unavailable_artifacts": (outcome or {}).get("unavailable_artifacts", []),
            "promotions": promotions[:20],
            "promotion_count": len(promotions),
            "promotions_omitted": max(0, len(promotions) - 20),
        }

    def show(self, reference):
        selection, state = self._selection()
        return self._show(self._resolve(reference, selection), selection, state)

    def list(self, *, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ChangeHistoryError(
                "invalid_history_limit",
                "History limit must be an integer from 1 through 100.",
            )
        selection, _ = self._selection()
        directory = self.store._path(".agentcad/changes/attempts")
        index = []
        for path in directory.iterdir() if directory.exists() else []:
            if not _REVISION.fullmatch(path.name) or not path.is_dir():
                continue
            try:
                request = Attempt(self.store, path.name)._read(
                    "request.json", "attempt_request"
                )
                generation = request["selection_generation"]
                if (
                    request["revision_id"] != path.name
                    or type(generation) is not int
                    or generation < 1
                ):
                    raise ChangeStoreError(
                        "Invalid request identity or selection generation."
                    )
                error = None
            except (OSError, ValueError, KeyError) as exc:
                generation, error = -1, str(exc)
            index.append((generation, path.name, error))
        index.sort(reverse=True, key=lambda item: (item[0], item[1]))
        if before is not None:
            position = next(
                (i for i, item in enumerate(index) if item[1] == before), None
            )
            if position is None:
                raise ChangeHistoryError(
                    "invalid_history_cursor",
                    "Use next_before from a history page in this build directory.",
                )
            remaining = index[position + 1 :]
        else:
            remaining = index
        changes = []
        for _, revision, error in remaining[:limit]:
            try:
                if error:
                    raise ChangeHistoryError("change_evidence_unavailable", error)
                saved, checked, _ = self._read(revision)
                row = self._summary(saved, checked, selection)
            except ChangeHistoryError as exc:
                row = {
                    "revision_id": revision,
                    "integrity": "unavailable",
                    "message": str(exc),
                    "is_working": revision == selection["working_revision_id"],
                    "is_accepted": revision == selection["accepted_revision_id"],
                }
            changes.append(row)
        return {
            "selection": selection,
            "changes": changes,
            "count": len(changes),
            "total": len(index),
            "limit": limit,
            "next_before": (
                changes[-1]["revision_id"] if len(remaining) > limit else None
            ),
        }

    def compare(self, baseline, revision):
        selection, state = self._selection()
        base = self._show(self._resolve(baseline, selection), selection, state)
        candidate = self._show(self._resolve(revision, selection), selection, state)
        before, after = base["metrics"] or {}, candidate["metrics"] or {}
        metrics = {
            "dimensions": {
                axis: _delta(
                    before.get("dimensions", {}).get(axis),
                    after.get("dimensions", {}).get(axis),
                )
                for axis in ("x", "y", "z")
            },
            **{
                key: _delta(before.get(key), after.get(key))
                for key in ("volume", "surface_area")
            },
            "units": {"dimensions": "mm", "volume": "mm^3", "surface_area": "mm^2"},
            "baseline_reliable": before.get("reliable"),
            "candidate_reliable": after.get("reliable"),
        }
        old_specs = {
            row["check_id"]: row for row in (base["policy"] or {}).get("checks", [])
        }
        new_specs = {
            row["check_id"]: row
            for row in (candidate["policy"] or {}).get("checks", [])
        }
        old_checks = {row["check_id"]: row for row in base["checks"]["results"]}
        new_checks = {row["check_id"]: row for row in candidate["checks"]["results"]}
        geometry = {
            "baseline": base["geometry"],
            "candidate": candidate["geometry"],
            "status": (
                "not_run"
                if base["geometry"] and candidate["geometry"]
                else "unavailable"
            ),
            "same_bytes": (
                base["geometry"]["sha256"] == candidate["geometry"]["sha256"]
                if base["geometry"] and candidate["geometry"]
                else None
            ),
            "message": "Stored measurements and check verdicts are evidence summaries, not an occupied-space comparison. Missing candidate geometry is never replaced by the accepted model.",
        }
        return {
            "selection": selection,
            "baseline": base["change"],
            "candidate": candidate["change"],
            "source": {
                "baseline": base["source"],
                "candidate": candidate["source"],
                "same_bytes": base["source"]["sha256"] == candidate["source"]["sha256"],
            },
            "model": {
                "baseline": base["model"],
                "candidate": candidate["model"],
                "changed": base["model"] != candidate["model"],
            },
            "params": {
                "baseline": base["params"],
                "candidate": candidate["params"],
                "changed": base["params"] != candidate["params"],
            },
            "metrics": metrics,
            "geometry_comparison": geometry,
            "policy": {
                "baseline": _policy_ref(base["policy"]),
                "candidate": _policy_ref(candidate["policy"]),
                "changed": _policy_ref(base["policy"])
                != _policy_ref(candidate["policy"]),
                "change": (candidate["policy"] or {}).get("change"),
                "added_checks": sorted(new_specs.keys() - old_specs.keys()),
                "removed_checks": sorted(old_specs.keys() - new_specs.keys()),
                "changed_checks": sorted(
                    key
                    for key in old_specs.keys() & new_specs.keys()
                    if old_specs[key] != new_specs[key]
                ),
            },
            "checks": [
                {
                    "check_id": key,
                    "baseline": old_checks.get(key),
                    "candidate": new_checks.get(key),
                    "definition_changed": old_specs.get(key) != new_specs.get(key),
                }
                for key in sorted(old_specs.keys() | new_specs.keys())
            ],
        }
