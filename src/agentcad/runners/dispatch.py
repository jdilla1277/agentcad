"""Runtime dispatcher — pick a runner by reading what the script imports.

The CLI stays a single ``agentcad run`` command. Dispatch is invisible
to users: a script that writes ``import cadquery as cq`` gets the
CadQuery runner, one that writes ``from build123d import Box`` gets
the build123d runner, and a script that imports neither falls back to
the project's default runtime (or ``cadquery`` if no project mode is
set), preserving every existing agentcad script on disk.

Scripts that somehow import *both* are rejected — silently guessing
would be worse than a loud error. A ``--runtime`` CLI flag bypasses
detection entirely when the agent needs to force a choice.

Precedence (highest to lowest):
  1. ``--runtime`` CLI flag (one-off override)
  2. Explicit script imports (``import cadquery`` / ``from build123d ...``)
  3. Project mode (``runtime`` field in ``agentcad.json``)
  4. Global default (``cadquery``)
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


def detect(source: str, default: RuntimeName | None = None) -> RuntimeName:
    """Pick a runtime based on imports in ``source``.

    * ``cadquery`` imports → ``"cadquery"``
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
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return fallback
    imported = _imports(tree)
    has_cq = "cadquery" in imported
    has_b3d = "build123d" in imported
    if has_cq and has_b3d:
        raise ValueError(
            "runtime ambiguous: script imports both cadquery and build123d. "
            "Remove one, or pass --runtime=<cadquery|build123d> to force a choice."
        )
    if has_b3d:
        return "build123d"
    if has_cq:
        return "cadquery"
    return fallback


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

    Precedence: ``override`` > script imports > ``project_default`` > ``DEFAULT_RUNTIME``.
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
        name = detect(source, default=project_default)
    return name, get_runner(name)
