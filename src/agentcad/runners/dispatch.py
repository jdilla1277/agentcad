"""Runtime dispatcher — pick a runner by reading what the script imports.

The CLI stays a single ``agentcad run`` command. A pinned project presents
one authoring API; scripts that clearly use the other API fail with an
actionable override instead of silently changing engines. For legacy projects
without a runtime field, explicit imports and the old zero-import
``cq.Workplane(...)`` preamble select CadQuery. Everything else defaults to
build123d.

Scripts that somehow import *both* are rejected — silently guessing
would be worse than a loud error. A ``--runtime`` CLI flag bypasses
detection entirely when the agent needs to force a choice.

Precedence (highest to lowest):
  1. ``--runtime`` CLI flag (one-off override)
  2. Project mode (``runtime`` field in ``agentcad.json``)
  3. Legacy source detection for unpinned projects
  4. Global default (``build123d``)
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Literal

RuntimeName = Literal["cadquery", "build123d"]

_VALID_RUNTIMES: tuple[RuntimeName, ...] = ("cadquery", "build123d")
DEFAULT_RUNTIME: RuntimeName = "build123d"


def project_runtime(
    start: Path | None = None,
    *,
    search_parents: bool = False,
) -> RuntimeName | None:
    """Read the ``runtime`` field from the nearest ``agentcad.json``.

    By default reads only ``start`` (or cwd) — matches the existing
    ``manifest.load_manifest`` contract used by ``run``/``inspect``, which
    expect the manifest in the current working directory.

    Pass ``search_parents=True`` to walk up the directory tree until a
    manifest is found, the filesystem root is reached, or none exists.
    Used by ``docs`` so an agent invoking it from a subdir of the project
    (a common pattern when driving via shell tools that don't preserve
    cwd between calls) still gets runtime-aware documentation.

    Returns ``None`` if no manifest is found or it doesn't pin a runtime;
    callers should fall back to ``DEFAULT_RUNTIME``.
    """
    base = Path.cwd() if start is None else start
    candidates: list[Path] = [base]
    if search_parents:
        candidates.extend(base.parents)
    for directory in candidates:
        manifest_path = directory / "agentcad.json"
        if not manifest_path.exists():
            continue
        try:
            data = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        rt = data.get("runtime")
        if rt in _VALID_RUNTIMES:
            return rt  # type: ignore[return-value]
        return None
    return None


def _imports(tree: ast.AST) -> set[str]:
    """Return the top-level package names imported by the script."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def _attribute_root_name(node: ast.AST) -> str | None:
    """Return the root name of an attribute chain such as ``cq.Workplane``."""
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _declared_runtime(source: str) -> RuntimeName | None:
    """Return the runtime clearly declared by script syntax, if any.

    Besides imports, recognize ``cq.<name>`` attribute access for scripts from
    the original zero-import CadQuery preamble. Syntax errors deliberately
    return ``None`` so the selected runner's validator can report them using
    the normal structured contract.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    imported = _imports(tree)
    has_cq = "cadquery" in imported or any(
        isinstance(node, ast.Attribute) and _attribute_root_name(node) == "cq"
        for node in ast.walk(tree)
    )
    has_b3d = "build123d" in imported
    if has_cq and has_b3d:
        raise ValueError(
            "runtime ambiguous: script references both cadquery and build123d. "
            "Remove one, or pass --runtime=<cadquery|build123d> to force a choice."
        )
    if has_b3d:
        return "build123d"
    if has_cq:
        return "cadquery"
    return None


def detect(source: str, default: RuntimeName | None = None) -> RuntimeName:
    """Pick a runtime based on declarations in ``source``.

    * ``cadquery`` imports → ``"cadquery"``
    * legacy ``cq.<name>`` access → ``"cadquery"``
    * ``build123d`` imports → ``"build123d"``
    * Both → ``ValueError`` (ambiguous)
    * Neither → ``default`` if given, else ``DEFAULT_RUNTIME``

    Syntax errors are deliberately *not* raised here: if the source
    won't parse, we fall back to the default runtime so its own
    ``validate()`` can surface the syntax error in the contract shape
    the CLI already expects (``{"status": "validation_error", "checks": [...]}``).
    Raising would bypass that path.
    """
    fallback: RuntimeName = default if default is not None else DEFAULT_RUNTIME
    return _declared_runtime(source) or fallback


def get_runner(name: RuntimeName):
    """Return the runner module for ``name``.

    Import is done lazily so that simply importing the dispatcher
    doesn't pull in both engines.
    """
    if name == "cadquery":
        from agentcad.runners import cadquery as runner
    elif name == "build123d":
        from agentcad.runners import build123d as runner
    else:
        raise ValueError(
            f"unknown runtime '{name}'. Expected one of: {', '.join(_VALID_RUNTIMES)}"
        )
    return runner


def resolve(
    source: str,
    override: str | None = None,
    project_default: RuntimeName | None = None,
) -> tuple[RuntimeName, object]:
    """Pick a runtime and return ``(name, runner_module)``.

    Precedence: ``override`` > ``project_default`` > legacy source detection >
    ``DEFAULT_RUNTIME``. A declaration that conflicts with a pinned project is
    an error with a one-off override recovery.
    Callers (i.e. ``commands/run.py``) typically populate ``project_default``
    from :func:`project_runtime`.
    """
    if override:
        if override not in _VALID_RUNTIMES:
            raise ValueError(
                f"unknown --runtime '{override}'. Expected one of: {', '.join(_VALID_RUNTIMES)}"
            )
        name: RuntimeName = override  # type: ignore[assignment]
    else:
        declared = _declared_runtime(source)
        if project_default is not None:
            if declared is not None and declared != project_default:
                raise ValueError(
                    f"runtime mismatch: project uses {project_default}, but the "
                    f"script uses {declared}. Pass --runtime {declared} for a "
                    "one-off run, or update the runtime in agentcad.json."
                )
            name = project_default
        else:
            name = declared or DEFAULT_RUNTIME
    return name, get_runner(name)
