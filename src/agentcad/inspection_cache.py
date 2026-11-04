"""Project-scoped retention for inspection analysis, catalogues and image pairs."""

from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import time

from agentcad.geometry_manifest import _digest
from agentcad.import_requests import _locked
from agentcad.project import get_project, tomllib
from agentcad.versioning import atomic_write_json

_CURRENT = ContextVar("inspection_cache_lease", default=None)
_IMAGE = r"(?:overall|(?:part|occurrence|solid|face|edge|feature)_v[1-9][0-9]*_[1-9][0-9]*)_(?:overall|highlighted|isolated)_[0-9a-f]{16}"
_GROUP = re.compile(r"(?:report_[0-9a-f]{16}|bundle_[0-9a-f]{16}|" + _IMAGE + r")\.(?:json|png)\Z")
DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60
MAX_INDEX_BYTES = 4 * 1024 * 1024


class CacheUnavailable(ValueError):
    reason = "inspection_cache_unavailable"


class CacheResourceLimit(CacheUnavailable):
    reason = "inspection_cache_resource_limit"


def _now():
    return time.time()


def _size_json(value):
    return len((json.dumps(value, indent=2) + "\n").replace("\n", os.linesep).encode("utf-8"))


def _utc(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError) as exc:
        raise CacheUnavailable("inspection.cache.max_age_seconds produces an unsupported expiry.") from exc


class CacheLease:
    def __init__(self, layout, output_root=None):
        self.layout = layout
        self.root = Path(output_root).resolve() / "inspection" if output_root is not None else None
        self.managed = self.root is None or self.root.is_relative_to(layout.build_root)
        self.max_age_seconds, self.max_bytes = DEFAULT_MAX_AGE_SECONDS, DEFAULT_MAX_BYTES
        config = layout.project_root / "agentcad.toml"
        if config.exists():
            try:
                settings = tomllib.loads(config.read_text(encoding="utf-8")).get("inspection", {}).get("cache", {})
                if not isinstance(settings, dict) or set(settings) - {"max_age_seconds", "max_bytes"}:
                    raise ValueError("expected only max_age_seconds and max_bytes")
                for name, value in settings.items():
                    if type(value) is not int or value < 1:
                        raise ValueError(f"{name} must be a positive integer")
                    setattr(self, name, value)
            except (OSError, ValueError, AttributeError, TypeError) as exc:
                raise CacheUnavailable(f"Invalid [inspection.cache] in {config}: {exc}") from exc
        _utc(_now() + self.max_age_seconds)
        self.directory = layout.artifact_path(".agentcad/inspection_cache")
        self.index_path = self.directory / "index.json"
        self.used = set()
        self.pins, self.job_bytes = set(), 0
        self.state = None
        self._paths = {}
        self._relatives = {}
        self._sizes, self._mtimes = {}, {}

    def _path(self, value):
        if value in self._paths:
            return self._paths[value]
        path = self.layout.artifact_path(value)
        lexical = self.layout.build_root / value
        for item in (lexical, *lexical.parents):
            if item == self.layout.build_root:
                break
            if item.is_symlink():
                raise CacheUnavailable(f"Inspection cache must not follow symlinks: {item}")
        self._paths[value] = path
        return path

    def _relative(self, path):
        absolute = os.path.abspath(path)
        if absolute in self._relatives:
            return self._relatives[absolute]
        resolved = Path(path).resolve()
        value = resolved.relative_to(self.layout.build_root).as_posix()
        if self._path(value) != resolved:
            raise CacheUnavailable("Inspection cache path changed.")
        self._relatives[absolute] = value
        return value

    def _read_index(self):
        if not self.index_path.exists():
            return {"schema_version": 1, "project_root": str(self.layout.project_root),
                    "build_root": str(self.layout.build_root), "roots": [], "groups": {}}
        try:
            value = json.loads(self.index_path.read_bytes())
            checksum = value.pop("checksum")
            if (checksum != _digest(value) or value["schema_version"] != 1
                    or value["project_root"] != str(self.layout.project_root)
                    or value["build_root"] != str(self.layout.build_root)
                    or not isinstance(value["roots"], list) or not isinstance(value["groups"], dict)):
                raise ValueError("identity or checksum mismatch")
            return value
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
            raise CacheUnavailable(f"Inspection cache index unavailable; restore {self.index_path}: {exc}") from exc

    def _save(self):
        document = {**self.state, "checksum": _digest(self.state)}
        if _size_json(document) > MAX_INDEX_BYTES:
            raise CacheResourceLimit("Inspection cache index exceeds its 4 MiB metadata allowance.")
        atomic_write_json(self.index_path, document)

    def _collect_roots(self):
        roots = set(self.state["roots"])
        if self.root is not None:
            roots.add(self._relative(self.root))
        # Adopt version and configured external-input caches from before this
        # policy. Other custom roots are registered when inspected.
        candidates = [self.layout.build_root / "inspection"]
        candidates.extend(self.layout.build_root.glob("v*_*/inspection"))
        candidates.extend(self.layout.build_root.glob("derived/inspect/*/inspection"))
        roots.update(self._relative(p) for p in candidates if p.is_dir())
        self.state["roots"] = sorted(roots)

    def _discover(self):
        self._sizes, self._mtimes = {}, {}
        groups = {}
        for root in self.state["roots"]:
            directory = self._path(root)
            for path in directory.glob(".render-*"):
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
            for path in [*directory.glob(".*.tmp"), *directory.glob("jobs/inspection_*/.result_*.json.*.tmp")]:
                original = path.name[1:].rsplit(".", 2)[0]
                if _GROUP.fullmatch(original) or re.fullmatch(r"result_[0-9a-f]{16}\.json", original):
                    path.unlink()
            files = {}
            if directory.is_dir():
                # The directory was resolved and checked above. Whitelisted
                # basenames cannot escape it; DirEntry provides one stat per
                # artifact instead of resolving every ancestor repeatedly.
                canonical_root = directory.relative_to(self.layout.build_root).as_posix()
                with os.scandir(directory) as scanned:
                    for item in scanned:
                        if not _GROUP.fullmatch(item.name):
                            continue
                        if item.is_symlink() or not item.is_file(follow_symlinks=False):
                            raise CacheUnavailable(f"Inspection cache artifact must be a regular file: {item.path}")
                        value = canonical_root + "/" + item.name
                        stat = item.stat(follow_symlinks=False)
                        self._paths[value] = Path(item.path)
                        self._relatives[os.path.abspath(item.path)] = value
                        self._sizes[value], self._mtimes[value] = stat.st_size, stat.st_mtime
                        files.setdefault(Path(item.name).stem, []).append(value)
            for paths in files.values():
                paths.sort()
                key = _digest(paths)
                # Image PNG/sidecar groups keep a stable key even if one file
                # was lost. Both paths remain in the group for resume checks.
                if re.fullmatch(_IMAGE, Path(paths[0]).stem):
                    stem = Path(paths[0]).with_suffix("")
                    paths = [stem.with_suffix(".json").as_posix(), stem.with_suffix(".png").as_posix()]
                    key = _digest(paths)
                previous = self.state["groups"].get(key)
                if previous:
                    if previous["paths"] != paths:
                        raise CacheUnavailable("Inspection cache group identity changed.")
                    groups[key] = previous
                else:
                    created = min(self._mtimes[p] for p in paths if p in self._mtimes)
                    groups[key] = {"paths": paths, "created_at": created, "expires_at": created + self.max_age_seconds,
                                   "pinned": False}
            for path in directory.glob("jobs/inspection_*/result_*.json"):
                if not re.fullmatch(r"result_[0-9a-f]{16}\.json", path.name):
                    continue
                value = self._relative(path)
                stat = path.stat()
                self._sizes[value], self._mtimes[value] = stat.st_size, stat.st_mtime
                key = _digest([value])
                groups[key] = self.state["groups"].get(key) or {"paths": [value], "created_at": path.stat().st_mtime,
                    "expires_at": path.stat().st_mtime + self.max_age_seconds, "pinned": False}
        self.state["groups"] = groups

    def _protection(self):
        self.pins, self.job_bytes = set(), 0
        completed = {}
        releasing = {os.path.abspath(self._path(p)): p for entry in self.state["groups"].values()
                     if entry["pinned"] for p in entry["paths"]}
        for root in self.state["roots"]:
            for directory in (self._path(root) / "jobs").glob("inspection_*"):
                if directory.is_symlink():
                    raise CacheUnavailable("Inspection job directory must not be a symlink.")
                try:
                    state = json.loads((directory / "state.json").read_bytes())
                    checksum = state.pop("checksum")
                    if checksum != _digest(state) or state["job_id"] != directory.name:
                        raise ValueError("job state identity or checksum mismatch")
                    if state["status"] == "complete":
                        finished = state.get("completed_at", (directory / "state.json").stat().st_mtime)
                        for stage in state["stages"].values():
                            for artifact in stage.get("artifacts", []):
                                value = releasing.get(os.path.abspath(artifact["path"]))
                                if value is not None:
                                    completed[value] = max(completed.get(value, finished), finished)
                        if state.get("result"):
                            value = releasing.get(os.path.abspath(state["result"]["path"]))
                            if value is not None:
                                completed[value] = max(completed.get(value, finished), finished)
                        continue
                    for stage in state["stages"].values():
                        for artifact in stage.get("artifacts", []):
                            self.pins.add(self._relative(Path(artifact["path"])))
                    if state.get("result"):
                        self.pins.add(self._relative(Path(state["result"]["path"])))
                    self.job_bytes += sum(p.stat().st_size for p in directory.glob("*.json")
                                          if p.is_file() and not p.name.startswith("result_"))
                except FileNotFoundError:
                    # A job being prepared has no checkpoints yet. Its
                    # bootstrap JSON still counts against the quota.
                    self.job_bytes += sum(p.stat().st_size for p in directory.glob("*.json") if p.is_file())
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                    raise CacheUnavailable(f"Cannot establish protected inspection checkpoints at {directory}: {exc}") from exc
        for entry in self.state["groups"].values():
            pinned = bool(self.pins.intersection(entry["paths"]))
            if entry["pinned"] and not pinned:
                released = max((completed[p] for p in entry["paths"] if p in completed), default=entry["created_at"])
                entry.update(released_at=released, expires_at=released + self.max_age_seconds)
            entry["pinned"] = pinned

    def mark_completed(self, state):
        state["completed_at"] = _now()

    def _expiry(self, entry):
        return min(entry["expires_at"], entry.get("released_at", entry["created_at"]) + self.max_age_seconds)

    def _bytes(self, entry):
        return sum(self._sizes.get(p, 0) for p in entry["paths"])

    def enforce(self, reserve=0):
        self._protection()
        protected = self.job_bytes + reserve + sum(self._bytes(e) for e in self.state["groups"].values()
            if e["pinned"] or self.used.intersection(e["paths"]))
        if protected > self.max_bytes:
            self._save()
            raise CacheResourceLimit(f"Inspection cache needs {protected} bytes with protected work, above inspection.cache.max_bytes={self.max_bytes}. Increase the quota and resume the same inspection job; no geometry was discarded.")
        now = _now()
        for key, entry in list(self.state["groups"].items()):
            if not entry["pinned"] and not self.used.intersection(entry["paths"]) and self._expiry(entry) <= now:
                self._evict(key)
        size = sum(self._bytes(e) for e in self.state["groups"].values()) + self.job_bytes + reserve
        for key, entry in sorted(self.state["groups"].items(), key=lambda pair: (pair[1]["created_at"], pair[0])):
            if size <= self.max_bytes:
                break
            if entry["pinned"] or self.used.intersection(entry["paths"]):
                continue
            size -= self._bytes(entry)
            self._evict(key)
        self._save()
        if size > self.max_bytes:
            raise CacheResourceLimit(f"Inspection cache needs {size} bytes with protected work, above inspection.cache.max_bytes={self.max_bytes}. Increase the quota and resume the same inspection job; no geometry was discarded.")

    def _evict(self, key):
        for value in self.state["groups"][key]["paths"]:
            self._path(value).unlink(missing_ok=True)
            self._sizes.pop(value, None)
        del self.state["groups"][key]

    def use(self, paths):
        if self.managed:
            self.used.update(self._relative(p) for p in paths)

    def publish_json(self, path, body):
        if not self.managed:
            atomic_write_json(path, body)
            return
        self.use([path])
        encoded = (json.dumps(body, indent=2) + "\n").replace("\n", os.linesep).encode("utf-8")
        if Path(path).is_file() and Path(path).read_bytes() == encoded:
            return
        self._protection()
        if self._relative(path) in self.pins:
            raise CacheUnavailable("Protected inspection checkpoint is missing or corrupt; restore its original bytes before resuming.")
        self.enforce(_size_json(body))
        atomic_write_json(path, body)
        self._sizes[self._relative(path)] = len(encoded)
        self._register([path])
        self._save()

    def publish_image(self, temporary, png, metadata, body):
        if self.managed:
            self.use([png, metadata])
            self._protection()
            expected = {png: Path(temporary).read_bytes(), metadata: (json.dumps(body, indent=2) + "\n").replace("\n", os.linesep).encode("utf-8")}
            if any(self._relative(p) in self.pins and (not Path(p).is_file() or Path(p).read_bytes() != data) for p, data in expected.items()):
                raise CacheUnavailable("Protected inspection checkpoint is missing or corrupt; restore its original bytes before resuming.")
            self.enforce(Path(temporary).stat().st_size + _size_json(body))
        os.replace(temporary, png)
        atomic_write_json(metadata, body)
        if self.managed:
            self._sizes[self._relative(png)] = Path(png).stat().st_size
            self._sizes[self._relative(metadata)] = _size_json(body)
            self._register([png, metadata])
            self._save()

    def _register(self, paths):
        values = sorted(self._relative(p) for p in paths)
        key = _digest(values)
        if key not in self.state["groups"]:
            self.state["groups"][key] = {"paths": values, "created_at": _now(),
                "expires_at": _now() + self.max_age_seconds, "pinned": False}

    def reference(self):
        if not self.managed:
            return {"state": "unmanaged", "reason": "external_standalone_cache", "suggestion": "Use --build-dir to contain this input's derived artifacts in a managed build root."}
        self._protection()
        entries = [e for e in self.state["groups"].values() if self.used.intersection(e["paths"])]
        deadlines = [self._expiry(e) for e in entries if not e["pinned"]]
        self._save()
        return {"scope": "inspection_cache", "state": "retained" if deadlines else "protected",
                "expires_at": _utc(min(deadlines)) if deadlines else None, "may_evict_earlier": bool(deadlines),
                "max_age_seconds": self.max_age_seconds, "max_bytes": self.max_bytes,
                "protected_bytes": self.job_bytes + sum(self._bytes(e) for e in self.state["groups"].values() if e["pinned"])}

    def validate(self, paths, declared=None):
        if declared and declared.get("expires_at") and datetime.fromisoformat(declared["expires_at"].replace("Z", "+00:00")).timestamp() <= _now():
            raise CacheUnavailable("Inspection artifacts expired; request a fresh report or image for new evidence.")
        for path in paths:
            if not Path(path).is_file():
                raise CacheUnavailable("Inspection artifact is missing or evicted; request a fresh report or image.")
        if self.managed:
            wanted = {self._relative(p) for p in paths}
            if any(wanted.intersection(e["paths"]) and not e["pinned"] and self._expiry(e) <= _now()
                   for e in self.state["groups"].values()):
                raise CacheUnavailable("Inspection artifacts expired; request a fresh report or image for new evidence.")
        self.use(paths)


@contextmanager
def cache_lease(output_root=None, *, layout=None, protect=()):
    current = _CURRENT.get()
    if current is not None:
        if not current.managed and output_root is not None and Path(output_root).resolve() / "inspection" != current.root:
            raise CacheUnavailable("Cannot nest inspection outputs from another cache root.")
        if current.managed and output_root is not None:
            root = current._relative(Path(output_root) / "inspection")
            if root not in current.state["roots"]:
                raise CacheUnavailable("Cannot nest inspection outputs from another cache root.")
        current.use(protect)
        yield current
        return
    lease = CacheLease(layout or get_project(), output_root)
    if not lease.managed:
        # A source-side cache can also be a registered cache of another
        # project. Coordinate its reads/writes without evicting foreign data.
        lease.root.mkdir(parents=True, exist_ok=True)
        access = lease.root / "access.lock"
        if access.is_symlink():
            raise CacheUnavailable("Inspection cache access lock must not be a symlink.")
        with _locked(access) as acquired:
            if not acquired:
                raise CacheResourceLimit("Source-side inspection cache is busy; retry or use --build-dir.")
            token = _CURRENT.set(lease)
            try:
                yield lease
            finally:
                _CURRENT.reset(token)
        return
    lease.directory.mkdir(parents=True, exist_ok=True)
    lock = lease.directory / "retention.lock"
    if lock.is_symlink() or lease.index_path.is_symlink():
        raise CacheUnavailable("Inspection retention records must not be symlinks.")
    with _locked(lock) as acquired:
        if not acquired:
            raise CacheResourceLimit("Inspection cache is busy; retry the same inspection request.")
        for path in lease.directory.glob(".index.json.*.tmp"):
            if path.is_file() or path.is_symlink():
                path.unlink()
        lease.state = lease._read_index()
        lease._collect_roots()
        with ExitStack() as stack:
            for root in lease.state["roots"]:
                directory = lease._path(root)
                directory.mkdir(parents=True, exist_ok=True)
                access = directory / "access.lock"
                if access.is_symlink():
                    raise CacheUnavailable("Inspection cache access lock must not be a symlink.")
                if not stack.enter_context(_locked(access)):
                    raise CacheResourceLimit("Inspection cache is busy; retry the same request.")
            lease._discover()
            lease.use(protect)
            lease.enforce()
            token = _CURRENT.set(lease)
            try:
                yield lease
            finally:
                _CURRENT.reset(token)
                lease._save()
