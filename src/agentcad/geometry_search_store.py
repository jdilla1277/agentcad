"""Immutable exact-query candidate manifests with age and byte bounded retention."""

from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
import time

from agentcad.geometry_manifest import _digest
from agentcad.import_requests import _locked
from agentcad.inspection_snapshots import _utc
from agentcad.project import tomllib
from agentcad.versioning import atomic_write_json

SEARCH_ID = re.compile(r"search_[0-9a-f]{64}\Z")
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


class SearchUnavailable(ValueError):
    reason = "search_snapshot_unavailable"

    def __init__(self, message, *, query=None):
        super().__init__(message)
        self.query = query


class SearchExpired(SearchUnavailable):
    reason = "search_snapshot_expired"


class SearchResourceLimit(SearchUnavailable):
    reason = "search_resource_limit"


def _now():
    return time.time()


class SearchStore:
    def __init__(self, layout):
        self.layout = layout
        self.directory = layout.artifact_path(".agentcad/geometry_search")
        self.max_bytes = DEFAULT_MAX_BYTES
        self.max_age_seconds = DEFAULT_MAX_AGE_SECONDS
        config = layout.project_root / "agentcad.toml"
        try:
            settings = (tomllib.loads(config.read_text(encoding="utf-8")).get("inspection", {}).get("search", {})
                        if config.exists() else {})
            if not isinstance(settings, dict) or set(settings) - {"max_age_seconds", "max_bytes"}:
                raise ValueError("expected only max_age_seconds and max_bytes")
            for key, value in settings.items():
                if type(value) is not int or value < 1:
                    raise ValueError(f"{key} must be a positive integer")
                setattr(self, key, value)
            _utc(_now() + self.max_age_seconds)
        except (ValueError, OSError, TypeError, AttributeError, OverflowError) as exc:
            raise SearchUnavailable(f"Invalid [inspection.search] in {config}: {exc}") from exc

    @contextmanager
    def transaction(self):
        # Resolve_project checks the build root; check cache descendants too.
        lexical = self.layout.build_root / ".agentcad/geometry_search"
        if lexical.is_symlink() or lexical.parent.is_symlink():
            raise SearchUnavailable("Search storage cannot follow symlinks.")
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / "retention.lock"
        if lock.is_symlink():
            raise SearchUnavailable("Search retention lock cannot be a symlink.")
        with _locked(lock) as acquired:
            if not acquired:
                raise SearchResourceLimit("Search storage is busy; retry this call.")
            for path in self.directory.glob(".search_*.json.*.tmp"):
                if path.is_symlink():
                    raise SearchUnavailable("Search storage cannot follow symlinks.")
                path.unlink()
            yield

    def _entries(self):
        entries = []
        for path in self.directory.glob("search_*.json"):
            if not SEARCH_ID.fullmatch(path.stem):
                continue
            if path.is_symlink() or not path.is_file():
                raise SearchUnavailable("Search snapshots must be regular files.")
            stat = path.stat()
            try:
                data = json.loads(path.read_bytes()) if stat.st_size <= self.max_bytes else {}
                created, expires = self._timing(data)
            except (ValueError, TypeError, KeyError, RecursionError):
                created, expires = stat.st_mtime, stat.st_mtime + self.max_age_seconds
            entries.append((created, path.name, path, stat.st_size, expires))
        return sorted(entries)

    def _timing(self, data):
        created, expires = data["created_at"], data["expires_at"]
        if (type(created) not in (int, float) or type(expires) not in (int, float)
                or not math.isfinite(created) or not math.isfinite(expires) or expires < created):
            raise SearchUnavailable("Invalid search retention evidence.")
        return created, min(expires, created + self.max_age_seconds)

    def _prune(self, *, reserve=0, keep=None):
        entries = self._entries()
        remaining = []
        for entry in entries:
            if entry[4] <= _now() and entry[2] != keep:
                entry[2].unlink()
            else:
                remaining.append(entry)
        size = sum(e[3] for e in remaining) + reserve
        if reserve + sum(e[3] for e in remaining if e[2] == keep) > self.max_bytes:
            raise SearchResourceLimit("Search snapshot exceeds inspection.search.max_bytes; increase it before retrying.")
        for entry in remaining:
            if size <= self.max_bytes:
                break
            if entry[2] != keep:
                entry[2].unlink()
                size -= entry[3]

    def _load(self, snapshot_id):
        if not isinstance(snapshot_id, str) or not SEARCH_ID.fullmatch(snapshot_id):
            raise SearchUnavailable("Use the search_snapshot ID returned by an exact query.")
        path = self.directory / (snapshot_id + ".json")
        if path.is_symlink():
            raise SearchUnavailable("Search snapshots cannot be symlinks.")
        try:
            if path.stat().st_size > self.max_bytes:
                raise SearchResourceLimit("Search snapshot exceeds inspection.search.max_bytes; increase it before reading.")
            raw = path.read_bytes()
        except FileNotFoundError as exc:
            raise SearchUnavailable("Search snapshot is missing or evicted; rerun the original query for new evidence.") from exc
        try:
            data = json.loads(raw)
            checksum = data.pop("id")
            if (checksum != snapshot_id or "search_" + _digest(data) != snapshot_id
                    or data["schema_version"] != 1
                    or data["project_root"] != str(self.layout.project_root)
                    or data["build_root"] != str(self.layout.build_root)):
                raise ValueError("identity or project binding mismatch")
            _, expires = self._timing(data)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
            raise SearchUnavailable(f"Search snapshot integrity check failed: {exc}") from exc
        if expires <= _now():
            path.unlink()
            raise SearchExpired("Search snapshot expired; rerun the original query for new evidence. Old pages cannot be regenerated.", query=data.get("query"))
        reference = {"id": snapshot_id, "sha256": hashlib.sha256(raw).hexdigest(),
                     "retention": {"state": "retained", "expires_at": _utc(expires),
                                   "may_evict_earlier": True, "max_bytes": self.max_bytes,
                                   "max_age_seconds": self.max_age_seconds}}
        return data, reference, raw

    def publish(self, body):
        now = _now()
        data = {**body, "schema_version": 1, "project_root": str(self.layout.project_root),
                "build_root": str(self.layout.build_root), "created_at": now,
                "expires_at": now + self.max_age_seconds}
        snapshot_id = "search_" + _digest(data)
        document = {"id": snapshot_id, **data}
        size = len((json.dumps(document, indent=2) + "\n").replace("\n", os.linesep).encode("utf-8"))
        if size > self.max_bytes:
            raise SearchResourceLimit(f"Search manifest needs {size} bytes; increase inspection.search.max_bytes (currently {self.max_bytes}).")
        with self.transaction():
            path = self.directory / (snapshot_id + ".json")
            self._prune(reserve=0 if path.exists() else size, keep=path if path.exists() else None)
            if not path.exists():
                atomic_write_json(path, document, overwrite=False)
            return self._load(snapshot_id)[:2]

    def read(self, snapshot_id):
        with self.transaction():
            data, reference, raw = self._load(snapshot_id)
            self._prune(keep=self.directory / (snapshot_id + ".json"))
            return data, reference, raw
