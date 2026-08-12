"""Concurrency-safe version reservation and atomic project JSON writes."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


_VERSION_DIR_RE = re.compile(r"^v(\d+)(?:_|$)")
_LOCK_TIMEOUT_S = 10.0
_STALE_LOCK_S = 120.0


@dataclass(frozen=True)
class VersionReservation:
    number: int
    label: str
    dir_name: str
    path: Path


def atomic_write_json(path: Path, payload: dict) -> None:
    """Replace *path* with complete JSON, never a partially written document."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


@contextmanager
def _version_lock(project_dir: Path):
    """Portable lock directory protecting reservation and manifest updates."""
    project_dir = Path(project_dir)
    state_dir = project_dir / ".agentcad"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = state_dir / "version.lock"
    deadline = time.monotonic() + _LOCK_TIMEOUT_S

    while True:
        try:
            lock_dir.mkdir()
            break
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
                if age > _STALE_LOCK_S:
                    lock_dir.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for version lock at {lock_dir}"
                )
            time.sleep(0.01)

    try:
        yield
    finally:
        try:
            lock_dir.rmdir()
        except FileNotFoundError:
            pass


def _used_version_numbers(project_dir: Path, manifest: dict) -> set[int]:
    used = {
        int(entry["version"])
        for entry in manifest.get("versions", [])
        if isinstance(entry.get("version"), int)
    }
    for child in project_dir.iterdir():
        if not child.is_dir():
            continue
        match = _VERSION_DIR_RE.match(child.name)
        if match:
            used.add(int(match.group(1)))
    return used


def reserve_version(
    project_dir: Path,
    label: str,
    *,
    suffix: str = "",
) -> VersionReservation:
    """Atomically claim a unique version number and empty directory."""
    project_dir = Path(project_dir)
    manifest_path = project_dir / "agentcad.json"
    with _version_lock(project_dir):
        manifest = json.loads(manifest_path.read_text())
        used = _used_version_numbers(project_dir, manifest)
        number = max(used, default=0) + 1
        if not suffix and label == f"v{number}":
            dir_name = label
        else:
            dir_name = f"v{number}_{label}{suffix}"
        path = project_dir / dir_name
        path.mkdir(parents=False, exist_ok=False)
    return VersionReservation(number, label, dir_name, path)


def commit_version(
    reservation: VersionReservation,
    meta: dict,
    manifest_entry: dict,
    *,
    advance_current: bool,
) -> dict:
    """Atomically write metadata, then merge the reservation into the manifest."""
    atomic_write_json(reservation.path / "meta.json", meta)
    project_dir = reservation.path.parent
    manifest_path = project_dir / "agentcad.json"
    with _version_lock(project_dir):
        manifest = json.loads(manifest_path.read_text())
        versions = [
            entry for entry in manifest.get("versions", [])
            if entry.get("version") != reservation.number
        ]
        versions.append(manifest_entry)
        versions.sort(key=lambda entry: entry.get("version", 0))
        manifest["versions"] = versions
        if advance_current:
            manifest["current"] = reservation.label
        atomic_write_json(manifest_path, manifest)
    return manifest

