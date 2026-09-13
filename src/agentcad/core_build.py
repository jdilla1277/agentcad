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


def apply_validation(metrics: dict, report: dict) -> dict:
    """Fold a layered validation report into a metrics dict.

    ``metrics.is_valid`` becomes the validator's verdict (true, false, or null
    when a gating layer could not finish). The kernel check's own result
    stays in ``report.layers.brep_check``. ``metrics.reliable`` is false when
    the shape has no solid or an open shell, because volume and surface area
    are not physical quantities then.
    """
    layers = report["layers"]
    metrics["is_valid"] = report["is_valid"]
    errors = layers.get("brep_check", {}).get("errors") or []
    if errors:
        metrics["validity_errors"] = errors
    else:
        metrics.pop("validity_errors", None)
    structure = layers.get("structure", {})
    closure = layers.get("shell_closure", {})
    metrics["reliable"] = bool(
        structure.get("solid_count", 0) >= 1 and closure.get("status") != "fail"
    )
    return metrics


def validated_metrics(topo_shape, *, profile: str = "deliverable") -> tuple[dict, dict]:
    """Metrics plus the layered validation report for an in-memory shape.

    Commands that operate on a file (import, measure, inspect, recover) use
    this directly: the loaded shape is the artifact. ``run`` validates the
    STEP it is about to deliver instead; see ``validate_delivered_step``.
    """
    from agentcad.metrics import compute_metrics
    from agentcad.validation import validate_shape

    # The report's kernel layer runs on this same shape; do not run it twice.
    metrics = compute_metrics(topo_shape, check_validity=False)
    report = validate_shape(topo_shape, profile=profile)
    return apply_validation(metrics, report), report


def validate_delivered_step(step_path, *, profile: str = "deliverable") -> dict:
    """Validate a STEP file the way every downstream consumer will see it.

    STEP export changes topology: two bodies fused along a shared edge are
    one non-manifold edge in memory but two clean bodies in the file. The
    verdict must describe the artifact that ships, so ``run`` writes the STEP
    first and validates the reloaded shape.
    """
    from agentcad.native_io import suppress_native_output
    from agentcad.step_io import load_cad_shape
    from agentcad.validation import load_failure_report, validate_shape

    try:
        with suppress_native_output():
            shape = load_cad_shape(step_path)
    except Exception as exc:
        return load_failure_report("file_parse", f"{type(exc).__name__}: {exc}", profile=profile)
    return validate_shape(shape, profile=profile)


def reliability_warning(metrics: dict) -> str | None:
    if metrics.get("reliable", True):
        return None
    return (
        "volume, surface_area, and center_of_mass are not physical quantities "
        "here: the shape has no closed solid (reliable: false)."
    )


def validation_warning(report: dict) -> str | None:
    """A warning line for a verdict that could not be reached."""
    if report.get("is_valid") is not None:
        return None
    layer = report.get("undetermined_layer") or "a validation layer"
    return (
        f"Validation could not finish: {layer.replace('_', ' ')} did not complete, so "
        "is_valid is null. The version was saved; rerun with a larger "
        "AGENTCAD_MESH_VALIDATION_TIMEOUT_S or inspect the file to get a verdict."
    )


def invalid_geometry_payload(
    command: str, metrics: dict, validation: dict | None = None
) -> dict | None:
    """Return the shared non-success response for a non-deliverable final shape.

    Only a definite ``is_valid: false`` takes this path. A null verdict (a
    layer timed out) is reported as a warning by the caller instead, so a
    slow part is never discarded as broken.
    """
    if metrics.get("is_valid") is not False:
        return None

    if validation is not None:
        message = (
            "The final CAD geometry is not deliverable and was not saved as a "
            f"successful version. {validation.get('message', '')}"
        ).strip()
        suggestion = validation.get("suggestion") or (
            "Repair the source so it produces a closed, valid solid before retrying."
        )
    else:
        errors = metrics.get("validity_errors") or []
        error_detail = f" Checks: {', '.join(errors)}." if errors else ""
        message = (
            "The final CAD geometry is invalid and was not saved as a "
            f"successful version.{error_detail}"
        )
        suggestion = (
            "Repair the reported validity errors in the source geometry before "
            "retrying."
            if errors
            else
            "Repair the source so it produces a closed, valid solid before retrying."
        )
    payload = {
        "command": command,
        "status": INVALID_GEOMETRY,
        "message": message,
        "suggestion": suggestion,
        "metrics": metrics,
        "version_recorded": False,
        "current_advanced": False,
    }
    if validation is not None:
        payload["validation"] = validation
        payload["first_failure"] = validation.get("first_failure")
        payload["validation_profile"] = validation.get("profile")
    return payload
