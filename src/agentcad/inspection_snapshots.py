"""Bounded storage for complete inspection responses, separate from CAD evidence."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import time

from agentcad.geometry_manifest import _digest
from agentcad.import_requests import _locked
from agentcad.project import tomllib
from agentcad.versioning import atomic_write_json

SNAPSHOT_ID = re.compile(r"delivery_[0-9a-f]{64}\Z")
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class SnapshotUnavailable(ValueError):
    reason = "response_snapshot_unavailable"


class SnapshotExpired(SnapshotUnavailable):
    reason = "response_snapshot_expired"


class SnapshotResourceLimit(SnapshotUnavailable):
    reason = "response_snapshot_resource_limit"


def _now():
    return time.time()


def _utc(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError) as exc:
        raise SnapshotUnavailable("Snapshot max_age_seconds produces an unsupported expiry timestamp.") from exc


class SnapshotStore:
    def __init__(self, layout):
        self.directory = layout.artifact_path(".agentcad/inspection_delivery")
        self.max_age_seconds, self.max_bytes = DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_BYTES
        config = layout.project_root / "agentcad.toml"
        if config.exists():
            try:
                inspection = tomllib.loads(config.read_text(encoding="utf-8")).get("inspection", {})
                settings = inspection.get("snapshots", {})
                if not isinstance(settings, dict) or set(settings) - {"max_age_seconds", "max_bytes"}:
                    raise ValueError("expected only max_age_seconds and max_bytes")
                for key, value in settings.items():
                    if type(value) is not int or value < 1:
                        raise ValueError(f"{key} must be a positive integer")
                    setattr(self, key, value)
            except (AttributeError, OSError, ValueError, TypeError) as exc:
                raise SnapshotUnavailable(f"Invalid [inspection.snapshots] in {config}: {exc}") from exc

    @contextmanager
    def _transaction(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / "retention.lock"
        if lock.is_symlink():
            raise SnapshotUnavailable("Snapshot retention lock must not be a symlink.")
        with _locked(lock) as acquired:
            if not acquired:
                raise SnapshotResourceLimit("Snapshot storage is busy; retry this read or delivery.")
            # A killed writer can leave an unpublished temp file. Holding the
            # publication lock proves no current writer owns these files.
            for path in self.directory.glob(".delivery_*.json.*.tmp"):
                if re.fullmatch(r"\.delivery_[0-9a-f]{64}\.json\.[^.]+\.tmp", path.name):
                    path.unlink()
            yield

    def _timing(self, body, stat):
        # Old snapshots have no lifetime metadata; retain their original bytes.
        timing = body.get("snapshot_retention", {})
        if not isinstance(timing, dict):
            raise SnapshotUnavailable("Invalid snapshot retention evidence.")
        created = timing.get("created_at", stat.st_mtime)
        if type(created) not in (int, float) or not math.isfinite(created):
            raise SnapshotUnavailable("Invalid snapshot retention evidence.")
        expires = timing.get("expires_at", created + self.max_age_seconds)
        if (type(expires) not in (int, float) or not math.isfinite(expires) or expires < created):
            raise SnapshotUnavailable("Invalid snapshot retention evidence.")
        return created, min(expires, created + self.max_age_seconds)

    def _entries(self):
        entries = []
        for path in self.directory.glob("delivery_*.json"):
            if not SNAPSHOT_ID.fullmatch(path.stem):
                continue
            if path.is_symlink() or not path.is_file():
                raise SnapshotUnavailable(f"Snapshot must be a regular file: {path}")
            stat = path.stat()
            try:
                body = json.loads(path.read_bytes()) if stat.st_size <= self.max_bytes else {}
                created, expires = self._timing(body, stat)
            except (ValueError, TypeError, AttributeError, OverflowError):
                # Corrupt cache files still count and can be evicted. Reads
                # validate integrity before returning any response.
                created, expires = stat.st_mtime, stat.st_mtime + self.max_age_seconds
            entries.append((created, path.name, path, stat.st_size, expires))
        return sorted(entries)

    def _prune(self, entries, now, *, reserve=0, keep=None):
        remaining = []
        for entry in entries:
            if entry[4] <= now and entry[2] != keep:
                entry[2].unlink()
            else:
                remaining.append(entry)
        size = sum(entry[3] for entry in remaining) + reserve
        if reserve > self.max_bytes or sum(e[3] for e in remaining if e[2] == keep) + reserve > self.max_bytes:
            raise SnapshotResourceLimit(f"Snapshot exceeds the {self.max_bytes}-byte response snapshot quota. Increase inspection.snapshots.max_bytes.")
        for entry in remaining:
            if size <= self.max_bytes:
                break
            if entry[2] != keep:
                entry[2].unlink()
                size -= entry[3]

    def _load(self, path):
        if path.is_symlink():
            raise SnapshotUnavailable("Snapshot must not be a symlink.")
        try:
            if path.stat().st_size > self.max_bytes:
                raise SnapshotResourceLimit(f"Snapshot exceeds the {self.max_bytes}-byte response snapshot quota. Increase inspection.snapshots.max_bytes before reading.")
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise SnapshotUnavailable("Response snapshot is missing or evicted. Request a fresh report for new evidence; the historical response cannot be regenerated.") from exc
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SnapshotUnavailable("Response snapshot integrity check failed.") from exc
        if (not isinstance(body, dict) or body.pop("id", None) != path.stem
                or "delivery_" + _digest(body) != path.stem or body.get("schema_version") != 1):
            raise SnapshotUnavailable("Response snapshot integrity check failed.")
        _, expires = self._timing(body, path.stat())
        reference = {"id": path.stem, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                     "retention": {"scope": "response_snapshot", "state": "retained", "expires_at": _utc(expires),
                                   "may_evict_earlier": True, "max_age_seconds": self.max_age_seconds, "max_bytes": self.max_bytes}}
        return body, reference, expires

    def publish(self, body):
        now = _now()
        _utc(now + self.max_age_seconds)
        body = {**body, "snapshot_retention": {"created_at": now, "expires_at": now + self.max_age_seconds}}
        snapshot_id = "delivery_" + _digest(body)
        path = self.directory / (snapshot_id + ".json")
        document = {"id": snapshot_id, **body}
        # Match atomic_write_json's bytes before evicting anything for space.
        size = len((json.dumps(document, indent=2) + "\n").replace("\n", os.linesep).encode("utf-8"))
        if size > self.max_bytes:
            raise SnapshotResourceLimit(f"Snapshot needs {size} bytes, above the {self.max_bytes}-byte response snapshot quota. Increase inspection.snapshots.max_bytes.")
        with self._transaction():
            entries = self._entries()
            self._prune(entries, now, reserve=0 if path.exists() else size, keep=path if path.exists() else None)
            if not path.exists():
                atomic_write_json(path, document, overwrite=False)
            _, reference, _ = self._load(path)
        return reference

    def read(self, snapshot_id):
        if not SNAPSHOT_ID.fullmatch(snapshot_id):
            raise SnapshotUnavailable("Use the delivery snapshot ID returned by import or inspection.")
        path = self.directory / (snapshot_id + ".json")
        with self._transaction():
            # Copy and validate under the same lock as cleanup. Another process
            # cannot remove bytes halfway through a read; transport uses a copy.
            body, reference, expires = self._load(path)
            now = _now()
            if expires <= now:
                path.unlink()
                raise SnapshotExpired("Response snapshot expired. Request a fresh report for new evidence; the historical response cannot be regenerated.")
            self._prune(self._entries(), now, keep=path)
        return body, reference
