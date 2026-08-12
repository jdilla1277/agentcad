"""Explicit reconciliation for interrupted version directories."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad.manifest import load_manifest
from agentcad.native_io import silence_native_stdout
from agentcad.recovery import parse_version_dir_name


def _emit(payload: dict, *, exit_code: int = 0) -> None:
    click.echo(json.dumps(payload))
    if exit_code:
        sys.exit(exit_code)


def _existing_metadata(meta_path: Path) -> tuple[dict | None, bool]:
    if not meta_path.exists():
        return None, False
    try:
        payload = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, True
    return (payload, False) if isinstance(payload, dict) else (None, True)


def _infer_source(version_dir: Path) -> str:
    if (version_dir / "script.py").is_file():
        return "script"
    if any(version_dir.glob("source.*")):
        return "import"
    return "recovered"


@click.command("recover")
@click.argument("version_dir")
@click.option(
    "--make-current",
    is_flag=True,
    help="Explicitly make the recovered successful version current.",
)
def recover(version_dir: str, make_current: bool) -> None:
    """Validate and register an interrupted VERSION_DIR without deleting it."""
    manifest = load_manifest(command="recover")
    requested = Path(version_dir)
    parsed = parse_version_dir_name(requested.name)
    if requested.is_absolute() or requested.parent != Path(".") or parsed is None:
        _emit({
            "command": "recover",
            "status": "error",
            "reason": "not_a_version_directory",
            "message": (
                "Pass a direct project version directory such as v3_edit."
            ),
            "recovery_performed": False,
        }, exit_code=1)
        return

    project_dir = Path.cwd()
    path = project_dir / requested.name
    if not path.is_dir() or path.is_symlink():
        _emit({
            "command": "recover",
            "status": "error",
            "reason": (
                "version_directory_symlink"
                if path.is_symlink()
                else "version_directory_missing"
            ),
            "message": (
                f"Version directory '{requested.name}' must be a real direct "
                "project directory, not a symlink."
                if path.is_symlink()
                else f"Version directory '{requested.name}' does not exist."
            ),
            "recovery_performed": False,
        }, exit_code=1)
        return

    number, inferred_label = parsed
    relative_path = f"{path.name}/"
    matching_entry = next((
        entry for entry in manifest.get("versions", [])
        if str(entry.get("path", "")).rstrip("/") == path.name
    ), None)
    conflicting_entry = next((
        entry for entry in manifest.get("versions", [])
        if entry.get("version") == number
        and str(entry.get("path", "")).rstrip("/") != path.name
    ), None)
    if conflicting_entry is not None:
        _emit({
            "command": "recover",
            "status": "conflict",
            "reason": "version_number_already_registered",
            "version": number,
            "path": relative_path,
            "registered_path": conflicting_entry.get("path"),
            "message": (
                f"Version {number} already points to "
                f"{conflicting_entry.get('path')}; no files were changed."
            ),
            "recovery_performed": False,
        }, exit_code=1)
        return

    if matching_entry and matching_entry.get("version") != number:
        _emit({
            "command": "recover",
            "status": "conflict",
            "reason": "manifest_version_mismatch",
            "path": relative_path,
            "directory_version": number,
            "manifest_version": matching_entry.get("version"),
            "message": (
                "The directory and manifest disagree on the version number; "
                "no files were changed."
            ),
            "recovery_performed": False,
        }, exit_code=1)
        return

    duplicate_paths = sorted(
        child.name
        for child in project_dir.iterdir()
        if child.is_dir()
        and child.name != path.name
        and (parse_version_dir_name(child.name) or (None,))[0] == number
    )
    if duplicate_paths:
        _emit({
            "command": "recover",
            "status": "conflict",
            "reason": "duplicate_version_directories",
            "version": number,
            "paths": [path.name, *duplicate_paths],
            "message": "Multiple directories claim this version number; no files were changed.",
            "recovery_performed": False,
        }, exit_code=1)
        return

    meta_path = path / "meta.json"
    existing_meta, corrupt_metadata = _existing_metadata(meta_path)
    if corrupt_metadata:
        _emit({
            "command": "recover",
            "status": "unrecoverable",
            "reason": "corrupt_metadata",
            "path": relative_path,
            "message": "meta.json is corrupt; it was preserved and no history was changed.",
            "recovery_performed": False,
        }, exit_code=1)
        return

    if existing_meta and existing_meta.get("version", number) != number:
        _emit({
            "command": "recover",
            "status": "conflict",
            "reason": "metadata_version_mismatch",
            "path": relative_path,
            "directory_version": number,
            "metadata_version": existing_meta.get("version"),
            "message": (
                "The directory and metadata disagree on the version number; "
                "no files were changed."
            ),
            "recovery_performed": False,
        }, exit_code=1)
        return
    if existing_meta and any(
        key in existing_meta and not isinstance(existing_meta[key], dict)
        for key in ("outputs", "core", "artifacts")
    ):
        _emit({
            "command": "recover",
            "status": "unrecoverable",
            "reason": "corrupt_metadata",
            "path": relative_path,
            "message": (
                "meta.json has an invalid outputs/core/artifacts structure; it was "
                "preserved unchanged."
            ),
            "recovery_performed": False,
        }, exit_code=1)
        return

    if matching_entry and matching_entry.get("status", "success") != "success":
        _emit({
            "command": "recover",
            "status": "conflict",
            "reason": "manifest_status_not_success",
            "path": relative_path,
            "message": "The existing history entry is not successful; no files were changed.",
            "recovery_performed": False,
        }, exit_code=1)
        return
    if existing_meta and existing_meta.get("status", "success") != "success":
        _emit({
            "command": "recover",
            "status": "conflict",
            "reason": "metadata_status_not_success",
            "path": relative_path,
            "message": "Existing metadata is not successful; no files were changed.",
            "recovery_performed": False,
        }, exit_code=1)
        return
    if (
        matching_entry is not None
        and existing_meta is not None
        and (path / "output.step").is_file()
    ):
        _emit({
            "command": "recover",
            "status": "success",
            "recovered": False,
            "reason": "already_consistent",
            "version": number,
            "label": matching_entry.get("label"),
            "path": relative_path,
            "message": (
                "This version already has metadata and a matching history "
                "entry; no files or current selection were changed."
            ),
            "recovery_performed": False,
            "current_advanced": False,
        })
        return

    step_path = path / "output.step"
    if not step_path.is_file():
        _emit({
            "command": "recover",
            "status": "unrecoverable",
            "reason": "missing_core_step",
            "path": relative_path,
            "message": "No output.step exists; the directory was preserved unchanged.",
            "recovery_performed": False,
        }, exit_code=1)
        return

    try:
        from agentcad.step_io import load_cad_shape

        with silence_native_stdout():
            shape = load_cad_shape(step_path)
    except Exception as exc:
        _emit({
            "command": "recover",
            "status": "malformed",
            "reason": "core_step_unreadable",
            "path": relative_path,
            "message": f"output.step could not be loaded: {exc}",
            "recovery_performed": False,
        }, exit_code=1)
        return

    from agentcad.metrics import compute_metrics

    with silence_native_stdout():
        metrics = compute_metrics(shape)
    if metrics.get("is_valid") is not True:
        _emit({
            "command": "recover",
            "status": "invalid_geometry",
            "reason": "invalid_core_geometry",
            "path": relative_path,
            "metrics": metrics,
            "message": "output.step is invalid; it was preserved but not registered.",
            "recovery_performed": False,
        }, exit_code=1)
        return

    label = (
        (matching_entry or {}).get("label")
        or (existing_meta or {}).get("label")
        or inferred_label
    )
    source = (
        (matching_entry or {}).get("source")
        or (existing_meta or {}).get("source")
        or _infer_source(path)
    )
    reconciled_at = datetime.now(timezone.utc).isoformat()
    meta = dict(existing_meta or {})
    meta.setdefault("command", "recover")
    meta.update({
        "status": "success",
        "version": number,
        "label": label,
        "source": source,
        "metrics": metrics,
    })
    meta.setdefault("created", datetime.fromtimestamp(
        step_path.stat().st_mtime, tz=timezone.utc
    ).isoformat())
    outputs = dict(meta.get("outputs") or {})
    outputs["step"] = f"{path.name}/output.step"
    if (path / "script.py").is_file():
        outputs.setdefault("script", f"{path.name}/script.py")
        meta.setdefault("script", f"{path.name}/script.py")
    source_files = sorted(path.glob("source.*"))
    if source_files:
        outputs.setdefault("source", f"{path.name}/{source_files[0].name}")
    meta["outputs"] = outputs
    core = dict(meta.get("core") or {})
    core["status"] = "success"
    core["recovered_at"] = reconciled_at
    meta["core"] = core
    artifacts = dict(meta.get("artifacts") or {})
    for name, state in artifacts.items():
        if isinstance(state, dict) and state.get("status") == "pending":
            artifacts[name] = {
                **state,
                "status": "unavailable",
                "message": (
                    "Completion was unknown after interruption; recovery did "
                    "not rerun this optional artifact."
                ),
            }
    if artifacts:
        meta["artifacts"] = artifacts
    meta["recovery"] = {
        "reconciled": True,
        "reconciled_at": reconciled_at,
        "original_metadata_present": existing_meta is not None,
    }

    from agentcad.versioning import (
        VersionConflictError,
        VersionReservation,
        commit_version,
    )

    reservation = VersionReservation(number, label, path.name, path)
    entry = dict(matching_entry or {})
    entry.update({
        "version": number,
        "label": label,
        "status": "success",
        "source": source,
        "path": relative_path,
    })
    try:
        commit_version(
            reservation,
            meta,
            entry,
            advance_current=make_current,
        )
    except VersionConflictError as exc:
        _emit({
            "command": "recover",
            "status": "conflict",
            "reason": "version_number_already_registered",
            "version": number,
            "path": relative_path,
            "registered_path": exc.registered_path,
            "message": str(exc),
            "recovery_performed": False,
        }, exit_code=1)
        return

    response = {
        "command": "recover",
        "status": "success",
        "recovered": True,
        "version": number,
        "label": label,
        "source": source,
        "path": relative_path,
        "outputs": outputs,
        "metrics": metrics,
        "current_advanced": make_current,
    }
    if artifacts:
        response["artifacts"] = artifacts
    _emit(response)
