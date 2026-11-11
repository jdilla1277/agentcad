"""Request-scoped Python imports for authored project scripts."""

from contextlib import contextmanager
from contextvars import ContextVar
import importlib
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder, SourceFileLoader
from pathlib import Path
import sys
from threading import RLock

_roots = ContextVar("agentcad_script_import_roots", default=())
_lock = RLock()
_reserved = frozenset(sys.stdlib_module_names) | {"agentcad"}


def is_local_import(name):
    """Locate local source without executing it during preflight validation."""
    if name.split(".")[0] in _reserved:
        return False
    for root in _roots.get():
        path = root.joinpath(*name.split("."))
        if path.with_suffix(".py").is_file() or path.is_dir():
            return True
    return False


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
                and any(Path(spec.origin).is_relative_to(root) for root in _roots.get())):
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
        names = {
            child.stem if child.suffix == ".py" else child.name
            for root in roots for child in root.iterdir()
            if (child.suffix == ".py" or child.is_dir())
        } - _reserved
        names = {name for name in names if name.isidentifier()}
        previous = {name: module for name, module in sys.modules.copy().items()
                    if name.split(".")[0] in names}
        path_before = sys.path[:]
        finder = _LocalSourceFinder()
        token = _roots.set(roots)
        for name in previous:
            sys.modules.pop(name, None)
        sys.path[:0] = [str(root) for root in roots]
        sys.meta_path.insert(0, finder)
        importlib.invalidate_caches()
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
            importlib.invalidate_caches()
