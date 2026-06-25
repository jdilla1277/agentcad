"""Single entry point for loading STEP / BREP files into a TopoDS_Shape.

Before this module, ten callsites across the codebase did the same
``importers.importStep(path).val().wrapped`` dance with inconsistent
silencing of OCCT's native stdout writes and inconsistent handling of
empty-compound results. This consolidates all of them.

Public API:
    load_cad_shape(path) -> TopoDS_Shape
        Reads STEP, STP, or BREP and returns the unwrapped OCCT shape.
        Always silences fd-1 to keep JSON-on-stdout contracts intact.
        Raises ValueError with a context-rich message on failure
        (missing file, unsupported extension, parser failure, empty
        compound — none of which should leak as stack traces to agents).

Failure modes worth knowing about:
    - Empty compound: the file parsed but contains no solids / shells /
      faces. Common with assembly references that don't include their
      children, or surfaces-only exports from older Fusion / SolidWorks
      that strip solid bodies.
    - Null shape: the parser succeeded but returned a TopoDS_Shape with
      ``IsNull() == True``. Rare but seen with truncated or zero-byte
      files that slip past extension sniffing.
    - OCCT exception: any exception from ``importers.importStep`` or
      ``BRepTools.Read_s`` is wrapped with the input path so error
      messages name the file at fault instead of bubbling up an OCP
      stack frame.
"""
from pathlib import Path
from typing import Union

from agentcad.native_io import silence_native_stdout

_STEP_SUFFIXES = (".step", ".stp")
_BREP_SUFFIXES = (".brep",)


def load_cad_shape(path: Union[str, Path], *, format_hint: str | None = None):
    """Load a STEP / STP / BREP file as an unwrapped TopoDS_Shape.

    Wraps the OCCT call in ``silence_native_stdout()`` so parser
    diagnostics go to stderr (not stdout) and JSON-on-stdout contracts
    stay clean. Raises ``ValueError`` with a context-rich message on
    any failure — never lets an OCCT exception leak unwrapped.

    ``format_hint`` may be ``"step"`` or ``"brep"`` when the caller has
    already sniffed the file type. This matters for resolved symlinks and
    content-addressed blobs whose real path has no useful suffix.
    """
    p = Path(path)
    if not p.exists():
        raise ValueError(f"CAD file not found: {p}")

    fmt = _cad_format(p, format_hint=format_hint)
    if fmt == "step":
        shape = _load_step_topods(p)
    elif fmt == "brep":
        shape = _load_brep_topods(p)
    else:
        suffix = p.suffix.lower()
        raise ValueError(
            f"Unsupported CAD extension '{suffix}' for {p.name}. "
            "load_cad_shape handles .step / .stp / .brep — use file_detect "
            "for graceful intake of other formats."
        )

    if shape is None or shape.IsNull():
        raise ValueError(
            f"{p.name} parsed but produced a null shape. The file may be "
            "truncated or corrupted."
        )

    if _is_empty_compound(shape):
        raise ValueError(
            f"{p.name} parsed but contains no solids, shells, or faces. "
            "Common causes: assembly references missing their children, "
            "or surfaces-only export from a CAD tool that stripped the "
            "solid bodies. Try re-exporting with 'include solids' enabled."
        )

    return shape


# --- internals --------------------------------------------------------------

def _cad_format(path: Path, *, format_hint: str | None) -> str | None:
    if format_hint is not None:
        normalized = format_hint.lower().lstrip(".")
        if normalized == "stp":
            normalized = "step"
        if normalized in {"step", "brep"}:
            return normalized
        raise ValueError(
            f"Unsupported CAD format hint '{format_hint}'. "
            "load_cad_shape accepts 'step' or 'brep'."
        )

    suffix = path.suffix.lower()
    if suffix in _STEP_SUFFIXES:
        return "step"
    if suffix in _BREP_SUFFIXES:
        return "brep"
    return None

def _load_step_topods(path: Path):
    from cadquery import Shape, importers

    try:
        with silence_native_stdout():
            wp = importers.importStep(str(path))
    except Exception as exc:
        raise ValueError(
            f"Could not parse STEP file {path.name}: {exc}. "
            "The file may be incomplete or corrupted."
        ) from exc

    # An empty STEP yields a Workplane whose .val() falls through to the
    # plane origin (a Vector), not a Shape. Detect that explicitly so the
    # downstream IsNull / empty-compound checks have a real shape to work
    # with — and so the agent gets an honest "no geometry" message.
    val = wp.val()
    if not isinstance(val, Shape):
        raise ValueError(
            f"{path.name} parsed but contains no geometric shapes. "
            "The file may be empty or contain only metadata. Try re-exporting "
            "with 'include solids' enabled in your CAD tool."
        )
    return val.wrapped


def _load_brep_topods(path: Path):
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    builder = BRep_Builder()
    try:
        with silence_native_stdout():
            ok = BRepTools.Read_s(shape, str(path), builder)
    except Exception as exc:
        raise ValueError(
            f"Could not parse BREP file {path.name}: {exc}."
        ) from exc

    if not ok:
        raise ValueError(
            f"BRepTools.Read returned false for {path.name}. "
            "The file may be incomplete or corrupted."
        )
    return shape


def _is_empty_compound(shape) -> bool:
    """True if the shape has no solids, shells, or faces — typically a
    compound wrapping nothing useful."""
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    for tp in (TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE):
        if TopExp_Explorer(shape, tp).More():
            return False
    return True
