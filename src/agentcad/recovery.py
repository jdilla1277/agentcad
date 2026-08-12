"""Discovery helpers for interrupted or legacy version directories."""

from __future__ import annotations

import json
import re
from pathlib import Path


_VERSION_DIR_RE = re.compile(r"^v(?P<number>\d+)(?:_(?P<label>.+))?$")


def parse_version_dir_name(name: str) -> tuple[int, str] | None:
    """Return the version number and inferred label for a directory name."""
    match = _VERSION_DIR_RE.fullmatch(name)
    if match is None:
        return None
    number = int(match.group("number"))
    if number < 1:
        return None
    return number, match.group("label") or name


def _normalized_path(value: str) -> str:
    return value.rstrip("/")


def _read_metadata(path: Path) -> tuple[dict | None, bool]:
    if not path.exists():
        return None, False
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None, True
    if not isinstance(payload, dict):
        return None, True
    return payload, False


def find_recovery_candidates(project_dir: Path, manifest: dict) -> list[dict]:
    """Describe version-like directories whose history is incomplete.

    Discovery is deliberately read-only. A STEP is considered only a core
    output candidate here; `agentcad recover` performs the expensive parse and
    validity checks before changing metadata or history.
    """
    project_dir = Path(project_dir)
    entries_by_path = {
        _normalized_path(str(entry.get("path", ""))): entry
        for entry in manifest.get("versions", [])
        if entry.get("path")
    }
    directories_by_number: dict[int, list[str]] = {}
    parsed_directories: list[tuple[Path, int, str]] = []
    for child in project_dir.iterdir():
        if not child.is_dir() or child.is_symlink():
            continue
        parsed = parse_version_dir_name(child.name)
        if parsed is None:
            continue
        number, inferred_label = parsed
        parsed_directories.append((child, number, inferred_label))
        directories_by_number.setdefault(number, []).append(child.name)

    candidates = []
    for child, number, inferred_label in sorted(
        parsed_directories, key=lambda item: (item[1], item[0].name)
    ):
        relative_path = f"{child.name}/"
        entry = entries_by_path.get(child.name)
        metadata, corrupt_metadata = _read_metadata(child / "meta.json")
        step_path = child / "output.step"

        issues = []
        if entry is None:
            issues.append("missing_manifest_entry")
        if corrupt_metadata:
            issues.append("corrupt_metadata")
        elif metadata is None:
            issues.append("missing_metadata")
        expects_core_step = bool(
            entry is None
            or metadata is None
            or (entry or {}).get("status", "success") == "success"
            or (metadata or {}).get("status", "success") == "success"
        )
        if expects_core_step and not step_path.is_file():
            issues.append("missing_core_step")

        same_number_paths = [
            str(item.get("path", ""))
            for item in manifest.get("versions", [])
            if item.get("version") == number
            and _normalized_path(str(item.get("path", ""))) != child.name
        ]
        if same_number_paths:
            issues.append("version_number_conflict")
        if len(directories_by_number.get(number, [])) > 1:
            issues.append("duplicate_version_number")
        if entry is not None and entry.get("version") != number:
            issues.append("manifest_version_mismatch")
        if metadata is not None and metadata.get("version", number) != number:
            issues.append("metadata_version_mismatch")

        if not issues:
            continue

        label = (
            (entry or {}).get("label")
            or (metadata or {}).get("label")
            or inferred_label
        )
        recoverable = bool(
            step_path.is_file()
            and not corrupt_metadata
            and "version_number_conflict" not in issues
            and "duplicate_version_number" not in issues
            and "manifest_version_mismatch" not in issues
            and "metadata_version_mismatch" not in issues
            and (entry or {}).get("status", "success") == "success"
            and (metadata or {}).get("status", "success") == "success"
        )
        candidate = {
            "version": number,
            "label": label,
            "path": relative_path,
            "step": f"{child.name}/output.step" if step_path.is_file() else None,
            "issues": issues,
            "recoverable": recoverable,
        }
        if recoverable:
            candidate["recovery_command"] = f"agentcad recover {child.name}"
        candidates.append(candidate)

    return candidates


def recovery_summary(project_dir: Path, manifest: dict) -> dict:
    candidates = find_recovery_candidates(project_dir, manifest)
    return {
        "status": "needed" if candidates else "clean",
        "candidate_count": len(candidates),
        "recoverable_count": sum(
            candidate["recoverable"] for candidate in candidates
        ),
        "candidates": candidates,
    }
