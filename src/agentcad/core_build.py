"""Shared contract for the geometry-producing part of run and import.

A core build consists of source execution/loading, aggregate and per-part
metrics where applicable, final geometry validation, and STEP export. Visual
artifacts and browser work happen only after this boundary.
"""


INVALID_GEOMETRY = "invalid_geometry"


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
