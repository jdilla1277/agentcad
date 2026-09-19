"""Stable authoring API for scripts executed by :command:`agentcad run`.

The build123d runner pre-injects these names for concise generated scripts,
but explicit imports are supported too::

    from agentcad.api import load_step, safe_cut, show_object

The runner installs output-capture callbacks for the duration of a script.
Keeping that state in a :class:`contextvars.ContextVar` makes imported
``show_object`` calls safe when separate scripts execute concurrently.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

from agentcad.helpers import (
    annular_boss,
    bbox_point,
    copy_shape,
    ellipse_wire,
    elliptical_sweep,
    involute_gear_profile,
    loft_sections,
    mirror_fuse,
    naca_wire,
    place_at,
    polygon_wire,
    raise_annulus,
    rotate,
    rounded_rect_wire,
    safe_cut,
    safe_fuse,
    safe_intersection,
    spline_wire,
    tapered_sweep,
    translate,
)


_OutputCallback = Callable[..., object]
_output_callbacks: ContextVar[tuple[_OutputCallback, _OutputCallback] | None] = (
    ContextVar("agentcad_output_callbacks", default=None)
)
_loaded_file_callback: ContextVar[Callable[[str], object] | None] = ContextVar(
    "agentcad_loaded_file_callback", default=None
)


def _active_output_callback(kind: str) -> _OutputCallback:
    callbacks = _output_callbacks.get()
    if callbacks is None:
        raise RuntimeError(
            f"{kind}() captures output only while a script is running under "
            "`agentcad run`. Put the call in the script passed to AgentCAD; "
            "AgentCAD will write the tracked STEP output."
        )
    return callbacks[0 if kind == "show_object" else 1]


def show_object(obj, *args, **kwargs):
    """Capture one part as script output for the active AgentCAD run."""
    return _active_output_callback("show_object")(obj, *args, **kwargs)


def show_assembly(shapes, *args, **kwargs):
    """Capture an intentional build123d multi-body assembly."""
    return _active_output_callback("show_assembly")(shapes, *args, **kwargs)


# Historical spelling retained as an exact alias, including for explicit
# imports. ``show_assembly`` is the preferred name in current documentation.
show_compound = show_assembly


@contextmanager
def _capture_output_with(
    object_callback: _OutputCallback,
    assembly_callback: _OutputCallback,
    loaded_file_callback: Callable[[str], object] | None = None,
) -> Iterator[None]:
    """Bind capture/provenance callbacks for one runner execution (internal)."""
    output_token = _output_callbacks.set((object_callback, assembly_callback))
    loaded_file_token = _loaded_file_callback.set(loaded_file_callback)
    try:
        yield
    finally:
        _loaded_file_callback.reset(loaded_file_token)
        _output_callbacks.reset(output_token)


def _record_loaded_file(path) -> None:
    callback = _loaded_file_callback.get()
    if callback is not None:
        callback(str(path))


def assemble(*shapes):
    """Return a build123d ``Compound`` from build123d or raw OCP shapes.

    Raw shapes are wrapped by topology type. ``Compound(raw_solid)`` is not
    the right idiom: it yields a wrapper with zero volume whose iteration
    walks shells (issue #194).
    """
    from build123d import Compound
    from agentcad.runners.build123d import _as_build123d

    return Compound(children=[_as_build123d(shape) for shape in shapes])


# The edit implementations still live beside the build123d runner for now.
# Import them at call time so importing this public module never creates an
# api -> runner -> api cycle. The runner injects these wrappers themselves,
# so explicit and import-free scripts still execute the exact same callables.
def load_step(path: str):
    """Load a STEP/STP file as a build123d ``Part``."""
    from agentcad.runners.build123d import _load_step

    _record_loaded_file(path)
    return _load_step(path)


def load_step_shape(path: str):
    """Load a STEP/STP/BREP file as a raw ``TopoDS_Shape``."""
    from agentcad.runners.build123d import _load_step_shape

    _record_loaded_file(path)
    return _load_step_shape(path)


def pick_face(shape, face_id: int):
    """Pick a face by ID from ``agentcad inspect --ids``."""
    from agentcad.runners.build123d import _pick_face

    return _pick_face(shape, face_id)


def pick_edge(shape, edge_id: int):
    """Pick an edge by ID from ``agentcad inspect --ids``."""
    from agentcad.runners.build123d import _pick_edge

    return _pick_edge(shape, edge_id)


def fillet_edges(shape, ids, r: float):
    """Fillet one or more edges identified by inspect IDs."""
    from agentcad.runners.build123d import _fillet_edges

    return _fillet_edges(shape, ids, r)


def chamfer_edges(shape, ids, d: float):
    """Chamfer one or more edges identified by inspect IDs."""
    from agentcad.runners.build123d import _chamfer_edges

    return _chamfer_edges(shape, ids, d)


def shell_faces(shape, ids, thickness: float):
    """Hollow a shape, opening the faces identified by inspect IDs."""
    from agentcad.runners.build123d import _shell_faces

    return _shell_faces(shape, ids, thickness)


def split_by_plane(shape, plane):
    """Split a shape and return its ``(below, above)`` halves."""
    from agentcad.runners.build123d import _split_by_plane

    return _split_by_plane(shape, plane)


def cut_pocket(shape, face_id: int, profile, depth: float):
    """Cut an extruded profile into a face selected by inspect ID."""
    from agentcad.runners.build123d import _cut_pocket

    return _cut_pocket(shape, face_id, profile, depth)


def boss(shape, face_id: int, profile, height: float):
    """Add an extruded profile to a face selected by inspect ID."""
    from agentcad.runners.build123d import _boss

    return _boss(shape, face_id, profile, height)


__all__ = [
    "show_object",
    "show_assembly",
    "show_compound",
    "load_step",
    "load_step_shape",
    "pick_face",
    "pick_edge",
    "fillet_edges",
    "chamfer_edges",
    "shell_faces",
    "split_by_plane",
    "cut_pocket",
    "boss",
    "loft_sections",
    "tapered_sweep",
    "naca_wire",
    "mirror_fuse",
    "copy_shape",
    "safe_cut",
    "safe_intersection",
    "safe_fuse",
    "translate",
    "rotate",
    "bbox_point",
    "place_at",
    "assemble",
    "annular_boss",
    "raise_annulus",
    "ellipse_wire",
    "spline_wire",
    "polygon_wire",
    "rounded_rect_wire",
    "elliptical_sweep",
    "involute_gear_profile",
]
