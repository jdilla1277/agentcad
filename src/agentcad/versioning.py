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


class VersionConflictError(RuntimeError):
    def __init__(self, number: int, registered_path: str):
        self.number = number
        self.registered_path = registered_path
        super().__init__(
            f"Version {number} is already registered at {registered_path}; "
            "no history was changed."
        )


def atomic_write_json(path: Path, payload: dict, *, overwrite: bool = True) -> None:
    """Publish complete JSON, optionally refusing to replace an existing file."""
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
        if overwrite:
            os.replace(temp_path, path)
        else:
            # Linking the completed file publishes it atomically and fails if
            # another writer has already issued this immutable document.
            os.link(temp_path, path)
            temp_path.unlink()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


@contextmanager
def _file_lock(path: Path):
    """OS-owned lock: process death releases it without a stale timeout."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            if path.stat().st_size == 0:
                handle.write(b"\0")
                handle.flush()

            def acquire():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

        else:
            import fcntl

            def acquire():
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(handle, fcntl.LOCK_UN)

        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                acquire()
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for version lock at {path}")
                time.sleep(0.01)
        try:
            yield
        finally:
            release()


@contextmanager
def _version_lock(project_dir: Path):
    """Serialize current writers, retaining the directory bridge for older CLIs."""
    project_dir = Path(project_dir)
    state_dir = project_dir / ".agentcad"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_dir = state_dir / "version.lock"
    marker = lock_dir / "os-owned"
    with _file_lock(state_dir / "version-write.lock"):
        # Having the OS lock proves no current writer owns this marker. An
        # older writer uses an empty directory and follows the original rules.
        if marker.is_file():
            marker.unlink()
            lock_dir.rmdir()
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                lock_dir.mkdir()
                break
            except FileExistsError:
                try:
                    if time.time() - lock_dir.stat().st_mtime > _STALE_LOCK_S:
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
            marker.write_text(str(os.getpid()))
            yield
        finally:
            marker.unlink(missing_ok=True)
            lock_dir.rmdir()


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
    from agentcad.project import validate_version_label

    validate_version_label(label)
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
    project_dir = reservation.path.parent
    manifest_path = project_dir / "agentcad.json"
    revision_id = (meta.get("change") or {}).get("revision_id")
    if advance_current and revision_id:
        from agentcad.change_promotion import PromotionRefused, PromotionStore

        promotions = PromotionStore(project_dir)
        request_key = "auto:" + revision_id
        promotions.begin(revision_id, request_key=request_key)
        result = promotions.commit(request_key, reservation, meta, manifest_entry)
        if result["outcome"] != "promoted":
            raise PromotionRefused(result)
        return promotions._manifest()
    with _version_lock(project_dir):
        manifest = json.loads(manifest_path.read_text())
        _merge_version_entry(
            manifest, reservation, manifest_entry, advance_current=advance_current
        )
        atomic_write_json(reservation.path / "meta.json", meta)
        atomic_write_json(manifest_path, manifest)
    return manifest


def _merge_version_entry(manifest, reservation, manifest_entry, *, advance_current):
    """Merge into an in-memory manifest while the caller holds the root lock."""
    conflict = next(
        (
            entry
            for entry in manifest.get("versions", [])
            if entry.get("version") == reservation.number
            and str(entry.get("path", "")).rstrip("/") != reservation.dir_name
        ),
        None,
    )
    if conflict is not None:
        raise VersionConflictError(
            reservation.number,
            str(conflict.get("path", "")),
        )
    versions = [
        entry
        for entry in manifest.get("versions", [])
        if entry.get("version") != reservation.number
    ]
    versions.append(manifest_entry)
    versions.sort(key=lambda entry: entry.get("version", 0))
    manifest["versions"] = versions
    if advance_current:
        manifest["current"] = reservation.label
