"""Runtime dispatcher — pick a runner by reading what the script imports.

The CLI stays a single ``agentcad run`` command. A pinned project presents
one authoring API; scripts that clearly use the other API fail with an
actionable override instead of silently changing engines. For legacy projects
without a runtime field, explicit imports and the old zero-import
``cq.Workplane(...)`` preamble select CadQuery. Everything else defaults to
build123d.

Unpinned scripts that reference both APIs are ambiguous. In pinned projects,
conflicting references are a mismatch against the configured runtime, never
an ambiguous choice. A ``--runtime`` CLI flag bypasses detection entirely.

Precedence (highest to lowest):
  1. ``--runtime`` CLI flag (one-off override)
  2. Project mode (``runtime`` field in ``agentcad.json``)
  3. Legacy source detection for unpinned projects
  4. Global default (``build123d``)
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from typing import Literal

RuntimeName = Literal["cadquery", "build123d"]

_VALID_RUNTIMES: tuple[RuntimeName, ...] = ("cadquery", "build123d")
DEFAULT_RUNTIME: RuntimeName = "build123d"

# The default `pip install agentcad` ships build123d only. CadQuery lives
# behind the `cadquery` extra, so a project or script that selects it on a
# default installation gets this one message everywhere (run, init, daemon,
# helpers) instead of a ModuleNotFoundError from whichever import fires first.
MISSING_CADQUERY_MESSAGE = (
    "CadQuery compatibility is not installed. Install it with "
    "`pip install \"agentcad[cadquery]\"` in the same environment as agentcad, "
    "then run `agentcad daemon restart` if a daemon is running."
)

# Offered alongside the missing-extra error on `run`, where porting is a
# real alternative to installing (it is not on `init`).
PORT_TO_BUILD123D_HINT = (
    "Without the extra, port the script to build123d: `agentcad docs runtimes` "
    "shows the same shape written both ways."
)


def runtime_available(name: RuntimeName) -> bool:
    """Report whether the package backing ``name`` is importable.

    Uses ``importlib.util.find_spec`` so the check never imports the
    engine: runtime detection, ``--help``, ``docs`` and daemon status must
    stay cheap and must not pull CadQuery (and its CasADi stack) into a
    process that only needs build123d.
    """
    if name == "cadquery":
        return importlib.util.find_spec("cadquery") is not None
    if name == "build123d":
        return importlib.util.find_spec("build123d") is not None
    return False


def require_runtime_available(name: RuntimeName) -> None:
    """Raise ``ValueError`` with the documented install hint if ``name``'s
    engine is not installed. build123d is a hard dependency, so only the
    optional CadQuery extra can actually be missing."""
    if name == "cadquery" and not runtime_available("cadquery"):
        raise ValueError(MISSING_CADQUERY_MESSAGE)


def project_runtime(
    start: Path | None = None,
    *,
    search_parents: bool = False,
) -> RuntimeName | None:
    """Read the runtime from the selected project's build manifest.

    CLI invocations share the discovered project and any --build-dir override.
    Outside Click, legacy projects retain cwd-only lookup unless callers pass
    ``search_parents=True``. Configured projects always follow their build root.

    Returns ``None`` if no manifest is found or it doesn't pin a runtime;
    callers should fall back to ``DEFAULT_RUNTIME``.
    """
    from agentcad.project import ProjectError, get_project, resolve_project

    try:
        layout = get_project() if start is None else resolve_project(start=start)
        import click
        # Preserve the helper's opt-in parent search outside a CLI invocation.
        if (not search_parents and click.get_current_context(silent=True) is None
                and not layout.configured and layout.project_root != (start or Path.cwd()).resolve()):
            return None
        data = layout.read_manifest()
    except (ProjectError, OSError, ValueError):
        return None
    rt = data.get("runtime")
    if rt in _VALID_RUNTIMES:
        return rt
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


def _referenced_runtimes(source: str) -> set[RuntimeName]:
    """Return the runtimes referenced by script syntax, without choosing one.

    Besides imports, recognize ``cq.<name>`` attribute access for scripts from
    the original zero-import CadQuery preamble. Syntax errors deliberately
    return an empty set so the selected runner's validator can report them using
    the normal structured contract.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    imported = _imports(tree)
    has_cq = "cadquery" in imported or any(
        isinstance(node, ast.Attribute) and _attribute_root_name(node) == "cq"
        for node in ast.walk(tree)
    )
    has_b3d = "build123d" in imported
    names: set[RuntimeName] = set()
    if has_cq:
        names.add("cadquery")
    if has_b3d:
        names.add("build123d")
    return names


def _declared_runtime(source: str) -> RuntimeName | None:
    names = _referenced_runtimes(source)
    if len(names) > 1:
        raise ValueError(
            "runtime ambiguous: script references both cadquery and build123d. "
            "Remove one, or pass --runtime=<cadquery|build123d> to force a choice."
        )
    return next(iter(names), None)


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
        require_runtime_available("cadquery")
        from agentcad.runners import cadquery as runner
    elif name == "build123d":
        from agentcad.runners import build123d as runner
    else:
        raise ValueError(
            f"unknown runtime '{name}'. Expected one of: {', '.join(_VALID_RUNTIMES)}"
        )
    return runner


def select_runtime(
    source: str,
    override: str | None = None,
    project_default: RuntimeName | None = None,
) -> tuple[RuntimeName, Literal["command", "project", "detection"]]:
    """Select the runtime and its source before validation or engine imports.

    Precedence: ``override`` > ``project_default`` > legacy source detection >
    ``DEFAULT_RUNTIME``. Detection includes the build123d fallback when no
    runtime is referenced. Project conflicts are checked separately so even
    rejected runs can report the authoritative runtime and its source.
    """
    if override:
        if override not in _VALID_RUNTIMES:
            raise ValueError(
                f"unknown --runtime '{override}'. Expected one of: {', '.join(_VALID_RUNTIMES)}"
            )
        name: RuntimeName = override  # type: ignore[assignment]
        return name, "command"
    if project_default is not None:
        return project_default, "project"
    return detect(source), "detection"


def validate_project_source(source: str, runtime: RuntimeName) -> None:
    """Reject references to another API without overriding the project pin."""
    other: RuntimeName = "cadquery" if runtime == "build123d" else "build123d"
    if other not in _referenced_runtimes(source):
        return
    message = (
        f"runtime mismatch: project uses {runtime}, but the script references {other}. "
        f"Remove the {other} imports and API usage, and use {runtime} throughout. "
        f"See `agentcad docs preamble --runtime {runtime}`. "
    )
    if other == "cadquery" and not runtime_available(other):
        message += f"If you intended to use {other}: {MISSING_CADQUERY_MESSAGE} Then "
    else:
        message += f"If you intended to use {other}, "
    message += (
        f"pass --runtime {other} for a one-off run, or update the runtime in agentcad.json."
    )
    raise ValueError(message)


def resolve(
    source: str,
    override: str | None = None,
    project_default: RuntimeName | None = None,
) -> tuple[RuntimeName, object]:
    """Select and validate a runtime, then return ``(name, runner_module)``."""
    name, runtime_source = select_runtime(source, override, project_default)
    if runtime_source == "project":
        validate_project_source(source, name)
    return name, get_runner(name)
