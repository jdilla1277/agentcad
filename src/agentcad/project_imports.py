"""Request-scoped Python imports for authored project scripts."""

from contextlib import contextmanager
from contextvars import ContextVar
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import BuiltinImporter, FrozenImporter, PathFinder, SourceFileLoader
from pathlib import Path
import sys
from threading import RLock

_roots = ContextVar("agentcad_script_import_roots", default=())
_names = ContextVar("agentcad_script_import_names", default=frozenset())
_lock = RLock()


def is_local_import(name):
    """Locate local source without executing it during preflight validation."""
    if name.split(".")[0] not in _names.get():
        return False
    for root in _roots.get():
        path = root.joinpath(*name.split("."))
        if path.with_suffix(".py").is_file() or path.is_dir():
            return True
    return False


def _local_spec(spec, roots, name):
    if spec is None:
        return False
    for root in roots:
        path = root.joinpath(*name.split("."))
        if spec.origin and Path(spec.origin) == path.with_suffix(".py"):
            return True
        if any(Path(location) == path for location in spec.submodule_search_locations or ()):
            return True
    return False


def _local_names(roots):
    """Only evict names Python would actually resolve inside this project."""
    candidates = {
        child.stem if child.suffix == ".py" else child.name
        for root in roots for child in root.iterdir()
        if child.suffix == ".py" or child.is_dir()
    }
    search = [str(root) for root in roots] + sys.path
    return frozenset(
        name for name in candidates if name.isidentifier() and name != "agentcad"
        and BuiltinImporter.find_spec(name) is None
        and FrozenImporter.find_spec(name) is None
        and _local_spec(PathFinder.find_spec(name, search), roots, name)
    )


class _FreshSourceLoader(SourceFileLoader):
    def get_code(self, fullname):
        # Helpers can be edited twice within a timestamp tick at the same size.
        # Read source directly; never reuse or delete the user's cached bytecode.
        return self.source_to_code(self.get_data(self.path), self.path)


class _LocalSourceFinder(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if not is_local_import(fullname):
            return None
        spec = PathFinder.find_spec(fullname, path)
        if (spec is not None and isinstance(spec.loader, SourceFileLoader)
                and _local_spec(spec, _roots.get(), fullname)):
            spec.loader = _FreshSourceLoader(fullname, spec.origin)
            return spec
        return None


@contextmanager
def script_imports(script, project_root):
    """Expose script siblings, then project packages, without leaking caches.

    Validation uses the same paths but defers executing local modules until the
    runner executes the script. Keep this scope out of daemon startup: a fork
    must not inherit an active project's helper cache or native side effects.
    """
    roots = tuple(dict.fromkeys((Path(script).resolve().parent, Path(project_root).resolve())))
    with _lock:
        importlib.invalidate_caches()
        names = _local_names(roots)
        previous = {name: module for name, module in sys.modules.copy().items()
                    if name.split(".")[0] in names}
        path_before = sys.path[:]
        finder = _LocalSourceFinder()
        token = _roots.set(roots)
        names_token = _names.set(names)
        for name in previous:
            sys.modules.pop(name, None)
        sys.path[:0] = [str(root) for root in roots]
        sys.meta_path.insert(0, finder)
        try:
            yield
        finally:
            for name in list(sys.modules):
                if name.split(".")[0] in names:
                    sys.modules.pop(name, None)
            sys.modules.update(previous)
            sys.path[:] = path_before
            sys.meta_path.remove(finder)
            _roots.reset(token)
            _names.reset(names_token)
            importlib.invalidate_caches()
