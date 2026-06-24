"""CadQuery script runner — the CQGI-backed execution path.

Behavior is intentionally identical to the inline implementation that
lived in ``commands/run.py`` before Phase 1 of the build123d migration.
Moving it here gives every runner the same call surface so the Phase 2
dispatcher can route by script content without special-casing either
engine.
"""

from __future__ import annotations

import warnings as _warnings
from typing import Any

from agentcad.runners import ExecutionResult
from agentcad.validate import validate_script


PREAMBLE = (
    "import cadquery as cq; "
    "from agentcad.helpers import ("
    "loft_sections, tapered_sweep, naca_wire, mirror_fuse, translate, "
    "rotate, bbox_point, place_at, assemble, ellipse_wire, spline_wire, "
    "polygon_wire, rounded_rect_wire, elliptical_sweep, involute_gear_profile"
    ")\n"
)


def with_preamble(user_source: str) -> str:
    """Prepend the implicit CadQuery imports every agentcad script gets."""
    return PREAMBLE + user_source


# build123d-only helpers per design_conventions.md. A CQ-routed script
# calling these would otherwise get a bare NameError at exec time.
_B3D_ONLY_HELPERS = (
    "show_assembly", "show_compound",
    "load_step", "load_step_shape",
    "pick_face", "pick_edge",
    "fillet_edges", "chamfer_edges", "shell_faces",
    "split_by_plane",
    "cut_pocket", "boss",
)


def validate(user_source: str) -> list:
    """Run pre-execution checks against the fully-preambled script."""
    errors = list(validate_script(with_preamble(user_source)))
    errors.extend(_check_b3d_only_helpers(user_source))
    return errors


def _check_b3d_only_helpers(source: str) -> list:
    """Reject CQ-routed scripts that call b3d-only edit helpers, with a
    clean message pointing at the kernel-neutral fallback."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []  # validate_script already reports syntax errors

    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _B3D_ONLY_HELPERS:
                used.add(node.func.id)

    if not used:
        return []

    name_list = ", ".join(sorted(used))
    return [{
        "check": "b3d_only_helper_in_cq_script",
        "severity": "error",
        "message": (
            f"Helper(s) {name_list} are build123d-only but this script is routed "
            "to the cadquery runner. Either rewrite as build123d (recommended — "
            "see `agentcad docs runtimes`) or use the kernel-neutral path: "
            "`from cadquery import importers; "
            "base = importers.importStep('path/to/file.step')`."
        ),
    }]


def execute(user_source: str, params: dict[str, Any] | None = None) -> ExecutionResult:
    """Parse, validate params, execute via CQGI, extract the result shape.

    The ``ExecutionResult`` the caller sees is runtime-agnostic; the
    ``native_shape`` field carries the CadQuery ``Workplane`` that the
    existing export code expects, and ``topo_shape`` carries the
    underlying OCP ``TopoDS_Shape`` that the metrics/render code uses.
    """
    import cadquery as cq
    from cadquery import cqgi
    from cadquery.cqgi import InvalidParameterError

    source = with_preamble(user_source)
    model = cqgi.parse(source)
    discovered = dict(model.metadata.parameters)

    # Upstream bug: cqgi.ScriptCallback.show_object has `options={}` as a
    # mutable default, and updates it with kwargs on every call — so every
    # ShapeResult ends up sharing the same dict object, and every part reports
    # the final call's options. Patch for the duration of build(). Fix is
    # local because we own the friction, even when it's upstream.
    original_show_object = cqgi.ScriptCallback.show_object

    def _safe_show_object(self, shape, options=None, **kwargs):
        opts = dict(options) if options else {}
        opts.update(kwargs)
        sr = cqgi.ShapeResult()
        sr.options = opts
        sr.shape = shape
        self.outputObjects.append(sr)

    cqgi.ScriptCallback.show_object = _safe_show_object

    if params:
        unknown = set(params.keys()) - set(discovered.keys())
        if unknown:
            available = ", ".join(sorted(discovered.keys())) if discovered else "(none)"
            return ExecutionResult(
                status="validation_error",
                discovered_parameters=discovered,
                exception=(
                    f"Unknown parameter(s): {', '.join(sorted(unknown))}. "
                    f"Available: {available}"
                ),
            )

    # Capture warnings.warn() calls from helpers + the script body so they
    # surface in the run output's warnings array. Without this, helper-side
    # signals (tapered_sweep kink detection, etc.) vanish silently.
    #
    # NB: bind the warnings record to `ws` rather than `as captured`. There
    # is no collision in this function today, but the b3d runner used the
    # same `as captured` pattern and shadowed its show_object list (#142,
    # fixed in #146). Keeping the binding name unambiguous in both runners
    # by convention so a future refactor that introduces a local `captured`
    # in this file can't reintroduce the same class of bug.
    captured_warnings: list[_warnings.WarningMessage] = []
    try:
        with _warnings.catch_warnings(record=True) as ws:
            _warnings.simplefilter("always")
            try:
                build_result = model.build(build_parameters=params)
            finally:
                cqgi.ScriptCallback.show_object = original_show_object
            captured_warnings = list(ws)
    except InvalidParameterError as e:
        return ExecutionResult(
            status="validation_error",
            discovered_parameters=discovered,
            exception=str(e),
        )
    except Exception as e:
        return ExecutionResult(
            status="execution_error",
            discovered_parameters=discovered,
            parameters=params or {},
            exception=f"Script execution failed: {e}",
        )

    if not build_result.success:
        return ExecutionResult(
            status="execution_error",
            discovered_parameters=discovered,
            parameters=params or {},
            exception=f"Script execution failed: {build_result.exception}",
        )

    if not build_result.results:
        return ExecutionResult(
            status="execution_error",
            discovered_parameters=discovered,
            parameters=params or {},
            exception="Script produced no results. Did you call show_object()?",
        )

    warnings: list[str] = [
        str(w.message)
        for w in captured_warnings
        if issubclass(w.category, UserWarning)
        and not issubclass(w.category, DeprecationWarning)
    ]
    try:
        per_part_shapes = []
        parts: list[dict[str, Any]] = []
        for idx, r in enumerate(build_result.results):
            s = r.shape
            wp = s.val() if hasattr(s, "val") else cq.Shape.cast(s)
            per_part_shapes.append(wp)
            opts = r.options or {}
            parts.append({
                "id": idx,
                "explicit_id": opts.get("id"),
                "name": opts.get("name"),
                "color": opts.get("color"),
                "topo_shape": wp.wrapped,
            })

        if len(per_part_shapes) == 1:
            shape = build_result.results[0].shape
        else:
            shape = cq.Workplane("XY").newObject(
                [cq.Compound.makeCompound(per_part_shapes)]
            )
            # Compound warning intentionally dropped: `parts` now carries the
            # per-part breakdown, so the old "consider makeCompound()" tip
            # would be actively misleading — following it would collapse the
            # breakdown into a single result.

        topo_shape = shape.val().wrapped
    except AttributeError as e:
        # Typically: non-CadQuery shape passed to show_object (e.g. forcing
        # --runtime=cadquery on a build123d script). Surface as an execution
        # error instead of leaking an AttributeError.
        return ExecutionResult(
            status="execution_error",
            discovered_parameters=discovered,
            parameters=params or {},
            exception=(
                f"Script execution failed: show_object() received an object "
                f"the cadquery runtime can't handle ({e}). "
                "If the script is written in build123d, drop --runtime or "
                "use --runtime=build123d."
            ),
        )

    return ExecutionResult(
        status="success",
        native_shape=shape,
        topo_shape=topo_shape,
        parameters=params or {},
        discovered_parameters=discovered,
        warnings=warnings,
        parts=parts,
    )


def export_step(native_shape: Any, path: str) -> None:
    """Write STEP using CadQuery's ``exporters.export``."""
    from cadquery import exporters

    exporters.export(native_shape, path)


def export_stl(native_shape: Any, path: str) -> None:
    """Write STL using CadQuery's ``exporters.export``."""
    from cadquery import exporters

    exporters.export(native_shape, path, exportType="STL")
