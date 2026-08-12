"""Shared contract for the geometry-producing part of run and import.

A core build consists of source execution/loading, aggregate and per-part
metrics where applicable, final geometry validation, and STEP export. Visual
artifacts and browser work happen only after this boundary.
"""

from copy import deepcopy
from pathlib import Path

from agentcad.versioning import atomic_write_json


INVALID_GEOMETRY = "invalid_geometry"


class ArtifactLifecycle:
    """Persist post-processing state without changing core build success."""

    def __init__(self, meta_path: Path, meta: dict):
        self.meta_path = Path(meta_path)
        self.meta = meta

    def persist(self) -> None:
        atomic_write_json(self.meta_path, self.meta)

    def set_artifact(
        self,
        name: str,
        status: str,
        *,
        message: str | None = None,
    ) -> None:
        entry = self.meta.setdefault("artifacts", {}).setdefault(name, {})
        entry["status"] = status
        if message:
            entry["message"] = message
        else:
            entry.pop("message", None)
        self.persist()

    def finish_pending(self, *, message: str) -> None:
        for entry in self.meta.get("artifacts", {}).values():
            if entry.get("status") == "pending":
                entry["status"] = "skipped"
                entry["message"] = message
        self.persist()

    def add_warning(self, warning: str) -> None:
        warnings = self.meta.setdefault("warnings", [])
        if warning not in warnings:
            warnings.append(warning)
        self.persist()

    def response(self) -> dict:
        return deepcopy(self.meta)


def invalid_geometry_payload(command: str, metrics: dict) -> dict | None:
    """Return the shared non-success response for an invalid final shape."""
    if metrics.get("is_valid") is not False:
        return None

    errors = metrics.get("validity_errors") or []
    error_detail = f" Checks: {', '.join(errors)}." if errors else ""
    suggestion = (
        "Repair the reported validity errors in the source geometry before "
        "retrying."
        if errors
        else
        "Repair the source so it produces a closed, valid solid before retrying."
    )
    return {
        "command": command,
        "status": INVALID_GEOMETRY,
        "message": (
            "The final CAD geometry is invalid and was not saved as a "
            f"successful version.{error_detail}"
        ),
        "suggestion": suggestion,
        "metrics": metrics,
        "version_recorded": False,
        "current_advanced": False,
    }
