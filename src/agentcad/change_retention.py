"""Retained attempts and declared-check adapters for local commands."""

from __future__ import annotations

import shutil
import json
import shlex
from functools import wraps
from pathlib import Path

import click

from agentcad.change_store import ChangeStore, ChangeStoreError
from agentcad.project import ProjectError, get_project


def change_options(function):
    """Share requirement options without duplicating command callback signatures."""

    @click.option(
        "--checks",
        "checks_file",
        type=click.Path(dir_okay=False),
        default=None,
        help="JSON dimension/preservation requirements; omitted on repairs, inherits the selected policy.",
    )
    @click.option(
        "--defer-checks",
        is_flag=True,
        default=False,
        help="Retain pending checks; complete later with check-spec REVISION --complete.",
    )
    @wraps(function)
    def wrapped(*args, checks_file=None, defer_checks=False, **kwargs):
        ctx = click.get_current_context()
        if kwargs.get("dry_run") and (checks_file or defer_checks):
            raise ProjectError(
                "checks_need_retention",
                "Declared checks need a retained attempt, not --dry-run.",
                "Remove --dry-run to retain and evaluate the requirements.",
            )
        if checks_file:
            try:
                ctx.meta["change_requirements"] = json.loads(Path(checks_file).read_text())
                if not isinstance(ctx.meta["change_requirements"], dict):
                    raise ValueError("The requirements file must contain a JSON object.")
            except (OSError, ValueError) as exc:
                raise ProjectError(
                    "invalid_checks",
                    f"Cannot read checks JSON: {exc}",
                    "Fix the checks file and retry.",
                ) from exc
        ctx.meta["change_checks_file"] = checks_file
        ctx.meta["change_defer_checks"] = defer_checks
        return function(*args, **kwargs)

    return wrapped


def forward_check_options(argv):
    ctx = click.get_current_context()
    if ctx.meta.get("change_checks_file"):
        argv.extend(["--checks", ctx.meta["change_checks_file"]])
    if ctx.meta.get("change_defer_checks"):
        argv.append("--defer-checks")


def assess_change(payload, geometry):
    """Gate the existing acceptance path; refused geometry remains diagnostic."""
    attempt = current_attempt()
    if attempt is None:
        return None
    request = attempt._read("request.json", "attempt_request")
    if not request.get("check_policy"):
        return None
    prepare_change(payload, geometry=geometry)
    reference = attempt.reference()
    checked = reference["validation_checks"]
    if checked["promotion_ready"]:
        return None
    state = checked["validation_state"]
    status = {"invalid": "checks_failed", "pending": "checks_pending"}.get(
        state, "checks_unavailable"
    )
    action = f"agentcad check-spec {attempt.revision_id}" + (
        " --complete" if state == "pending" else ""
    )
    if get_project().configured:
        action += " --build-dir " + shlex.quote(str(get_project().build_root))
    return {
        **payload,
        "status": status,
        "label": request["label"],
        "outputs": {"step": None},
        "current_advanced": False,
        "message": "The candidate is retained, but required checks are not satisfied; the accepted model is unchanged.",
        "suggestion": action,
        "more_at": "agentcad docs check-spec",
        "change": reference,
    }


def current_attempt():
    ctx = click.get_current_context(silent=True)
    return ctx.meta.get("change_attempt") if ctx is not None else None


def begin_change(*, command: str, label: str, source: bytes, source_name: str, model: dict):
    ctx = click.get_current_context(silent=True)
    if ctx is None or ctx.meta.get("project_dry_run") or ctx.meta.get("viewer_dry_run"):
        return None
    if current_attempt() is not None:
        return current_attempt()
    layout = get_project()
    if not layout.manifest_path.is_file():
        return None
    try:
        manifest = layout.read_manifest()
        attempt = ChangeStore(layout.build_root).begin(
            command=command,
            label=label,
            source=source,
            source_name=source_name,
            model=model,
            legacy_baseline=manifest.get("current"),
            requirements=ctx.meta.get("change_requirements"),
            defer_checks=ctx.meta.get("change_defer_checks", False),
        )
    except Exception as exc:
        ctx.meta["change_start_error"] = str(exc)
        if isinstance(exc, ValueError) and not isinstance(exc, ChangeStoreError):
            raise ProjectError(
                "invalid_checks",
                str(exc),
                "Fix the requirements JSON; policy changes need actor and rationale. See agentcad docs check-spec.",
            ) from exc
        raise ProjectError(
            "change_retention_failed",
            f"Could not retain the change before execution: {exc}",
            "Check the selected build directory and retry; no new CAD result was accepted.",
        ) from exc
    ctx.meta["change_attempt"] = attempt
    ctx.meta["change_command"] = command
    request = ctx.meta.get("import_request")
    if request is not None:
        request.bind_attempt(attempt)
    return attempt


def begin_run_change():
    ctx = click.get_current_context(silent=True)
    if ctx is None or "change_run_source" not in ctx.meta or ctx.meta.get("change_start_error"):
        return None
    source, name = ctx.meta["change_run_source"]
    return begin_change(
        command="run",
        label=ctx.meta.get("run_label") or "unlabeled",
        source=source,
        source_name=name,
        model={
            "runtime": ctx.meta.get("run_runtime"),
            "params": ctx.params.get("params"),
            "validation_profile": ctx.params.get("validation_profile", "deliverable"),
        },
    )


def copy_run_source(original: Path, destination: Path) -> None:
    attempt = current_attempt()
    shutil.copy2(attempt.source_path if attempt else original, destination)


def capture_geometry(path: Path) -> None:
    attempt = current_attempt()
    if attempt is not None and path.is_file():
        attempt.capture_geometry(path)


def prepare_change(payload: dict, *, geometry: Path | None = None) -> None:
    attempt = current_attempt()
    if attempt is not None:
        try:
            # Version metadata keeps a stable core reference, not a transient
            # operation status or a yet-unwritten terminal diagnostics path.
            reference = attempt.prepare(payload, geometry=geometry)
            payload["change"] = {
                k: v for k, v in reference.items() if k not in ("status", "diagnostics")
            }
        except Exception as exc:
            raise ProjectError(
                "change_retention_failed",
                f"Could not retain core CAD evidence: {exc}",
                "Inspect the retained request and fix the build directory before retrying; this core result was not accepted.",
            ) from exc


def attach_change(payload: dict) -> dict:
    """Complete once, before response formatting; shared by CLI and local MCP."""
    ctx = click.get_current_context(silent=True)
    if (
        ctx is None
        or payload.get("command") not in ("run", "import")
        or payload.get("via") == "daemon"
        or ctx.meta.get("import_replay")
        or ctx.meta.get("import_request_closed")
    ):
        return payload
    if ctx.meta.get("change_start_error"):
        return {
            **payload,
            "change": {"status": "unavailable", "message": ctx.meta["change_start_error"]},
        }
    attempt = current_attempt()
    if attempt is None and payload["command"] == "run":
        try:
            attempt = begin_run_change()
        except ProjectError as exc:
            return {**payload, "change": {"status": "unavailable", "message": str(exc)}}
    if attempt is None:
        return payload
    try:
        # A prepared core may already carry the reference; do not retain a
        # recursive or stale response reference as the operation's diagnostics.
        response = {k: v for k, v in payload.items() if k != "change"}
        prepared = attempt.prepare(response)
        if prepared.get("geometry") and not (response.get("outputs") or {}).get("step"):
            if isinstance(response.get("message"), str):
                response["message"] = response["message"].replace(
                    "No STEP was created.", "No accepted STEP was created."
                )
            response["notes"] = [
                *response.get("notes", []),
                "change.geometry is a diagnostic snapshot, not an accepted STEP deliverable.",
            ]
        payload = response
        reference = attempt.finish(response)
    except Exception as exc:
        reference = {
            "revision_id": attempt.revision_id,
            "status": "incomplete",
            "request": str(attempt.directory / "request.json"),
            "message": str(exc),
        }
        payload = {
            **payload,
            "warnings": [
                *payload.get("warnings", []),
                "The command result is unchanged, but change retention is incomplete; inspect change.request before relying on retained evidence.",
            ],
        }
    return {**payload, "change": reference}
