"""build123d script runner — AST-based parameter handling, exec-based capture.

Unlike CadQuery's CQGI, build123d has no gateway for discovering
top-level parameters or capturing ``show_object()`` results. This
module fills that gap:

* Parameter discovery walks the AST for top-level ``name = <literal>``
  assignments — mirroring CQGI's conservative default.
* Parameter overrides are applied by rewriting those literal nodes,
  which keeps line numbers stable for tracebacks.
* ``show_object`` is injected into the script's globals alongside the
  star-imported build123d API and the full ``agentcad.helpers`` set, so
  scripts can call any helper without imports — just like the CadQuery
  path.

Behavior comes directly from the Phase 0 spike (`spike_build123d/runner.py`)
with the only changes being the rename to ``execute`` and a switch from
the internal ``RunResult`` to the runtime-agnostic ``ExecutionResult``.
"""

from __future__ import annotations

import ast
import traceback
import warnings as _warnings
from typing import Any

from agentcad.runners import ExecutionResult


PREAMBLE = ""


def with_preamble(user_source: str) -> str:
    """build123d injects via globals, not via text. Kept for interface parity."""
    return PREAMBLE + user_source


def validate(user_source: str) -> list:
    """Run pre-execution checks against the script.

    Mirrors the CadQuery runner's contract — same ``validate_script`` helper,
    same checks (syntax_error, show_object_missing, import_error), so an
    unparseable script or one missing show_object lands as
    ``status: validation_error`` (no version consumed, no disk artifacts)
    on either runtime instead of falling through to ``execution_error``.
    """
    from agentcad.validate import validate_script

    return list(validate_script(
        with_preamble(user_source),
        output_calls=("show_object", "show_assembly", "show_compound"),
    ))


_SIMPLE_LITERALS = (ast.Constant,)


def discover_parameters(source: str) -> dict[str, Any]:
    """Find top-level ``name = <literal>`` assignments."""
    tree = ast.parse(source)
    params: dict[str, Any] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not isinstance(node.value, _SIMPLE_LITERALS):
            continue
        params[node.targets[0].id] = node.value.value
    return params


def _apply_overrides(source: str, overrides: dict[str, Any]) -> str:
    if not overrides:
        return source
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        if name in overrides:
            node.value = ast.Constant(value=overrides[name])
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def execute(
    user_source: str,
    params: dict[str, Any] | None = None,
    filename: str = "<script>",
) -> ExecutionResult:
    """Execute a build123d script and capture show_object() results."""
    from build123d import Compound, Shape, ShapeList

    source = with_preamble(user_source)
    discovered = discover_parameters(source)

    if params:
        unknown = set(params) - set(discovered)
        if unknown:
            available = ", ".join(sorted(discovered)) if discovered else "(none)"
            return ExecutionResult(
                status="validation_error",
                discovered_parameters=discovered,
                exception=(
                    f"Unknown parameter(s): {', '.join(sorted(unknown))}. "
                    f"Available: {available}"
                ),
            )
        source = _apply_overrides(source, params)

    # List of (obj, explicit_id, name, color, part_of, group_color) tuples
    # in declaration order.
    captured: list[tuple[Any, Any, Any, Any, Any, Any]] = []
    assembly_requested = False

    def show_object(
        obj,
        *_args,
        id=None,
        name=None,
        part_of=None,
        group_color=None,
        options=None,
        **_kwargs,
    ):
        # Boolean ops in build123d (`a - b - c`) can return a ShapeList instead
        # of a single Shape when the result is multi-piece. Auto-extract the
        # single-element case (the common one) and surface a recovery hint
        # for the rest.
        if isinstance(obj, ShapeList):
            if len(obj) == 1:
                obj = obj[0]
            elif len(obj) == 0:
                raise TypeError(
                    "show_object() received an empty ShapeList. The boolean "
                    "operation produced no result — check that your subtractions "
                    "actually intersect the base shape."
                )
            else:
                raise TypeError(
                    f"show_object() received a ShapeList of {len(obj)} shapes. "
                    "For intentional multi-body output, call "
                    "show_assembly(result, name=...). Otherwise call "
                    "show_object() once per shape (e.g. `for s in result: "
                    "show_object(s)`), or fuse them first with "
                    "`Compound(children=list(result))` if you want a single part."
                )
        if not isinstance(obj, Shape) and not _is_topods_shape(obj):
            raise TypeError(
                f"show_object() expects a build123d Shape or raw OCP "
                f"TopoDS_Shape, got {type(obj).__name__}. For fragile STEP "
                "edits, use `load_step_shape(path)`, build a raw TopoDS "
                "feature/compound, and pass that to `show_object()`."
            )
        color = None
        if isinstance(options, dict):
            if id is None:
                id = options.get("id")
            color = options.get("color")
            if name is None:
                name = options.get("name")
            if part_of is None:
                part_of = options.get("part_of") or options.get("group")
            if group_color is None:
                group_color = options.get("group_color")
        captured.append((obj, id, name, color, part_of, group_color))

    def show_assembly(
        shapes,
        *_args,
        id=None,
        name=None,
        part_of=None,
        group_color=None,
        options=None,
        **_kwargs,
    ):
        """Capture an intentional multi-body build123d output.

        This is the supported escape hatch for ShapeList/list/tuple outputs:
        callers do not need to know the exact Compound(children=...) idiom.
        """
        nonlocal assembly_requested

        if isinstance(shapes, Shape):
            children = [shapes]
        else:
            try:
                children = list(shapes)
            except TypeError as exc:
                raise TypeError(
                    "show_assembly() expects a build123d Shape or an iterable "
                    f"of Shapes, got {type(shapes).__name__}"
                ) from exc

        if not children:
            raise TypeError("show_assembly() received no shapes.")

        bad = [type(s).__name__ for s in children if not isinstance(s, Shape)]
        if bad:
            raise TypeError(
                "show_assembly() expects only build123d Shape objects; "
                f"got {', '.join(bad)}"
            )

        color = None
        if isinstance(options, dict):
            if id is None:
                id = options.get("id")
            color = options.get("color")
            if name is None:
                name = options.get("name")
            if part_of is None:
                part_of = options.get("part_of") or options.get("group")
            if group_color is None:
                group_color = options.get("group_color")

        captured.append(
            (Compound(children=children), id, name, color, part_of, group_color)
        )
        assembly_requested = True

    import build123d as _b3d

    script_globals: dict[str, Any] = {
        "__name__": "__agentcad_script__",
        "__file__": filename,
        "show_object": show_object,
        "show_assembly": show_assembly,
        "show_compound": show_assembly,
        "build123d": _b3d,
    }
    for attr in getattr(_b3d, "__all__", dir(_b3d)):
        if not attr.startswith("_"):
            script_globals[attr] = getattr(_b3d, attr)

    try:
        from agentcad import helpers as _helpers
    except ImportError:
        _helpers = None
    if _helpers is not None:
        for attr in (
            "loft_sections", "tapered_sweep", "naca_wire", "mirror_fuse",
            "translate", "rotate", "bbox_point", "place_at",
            "ellipse_wire", "spline_wire", "polygon_wire", "rounded_rect_wire",
            "elliptical_sweep", "involute_gear_profile",
        ):
            if hasattr(_helpers, attr):
                script_globals[attr] = getattr(_helpers, attr)

    # agentcad.helpers.assemble() returns a cq.Workplane — wrong type for b3d
    # scripts. Substitute a build123d-native version that accepts either
    # build123d Shapes or raw OCP TopoDS_* and returns a Compound.
    def _b3d_assemble(*shapes):
        from build123d import Compound, Shape

        wrapped = []
        for s in shapes:
            if isinstance(s, Shape):
                wrapped.append(s)
            else:
                wrapped.append(Compound(s))
        return Compound(children=wrapped)

    script_globals["assemble"] = _b3d_assemble

    # M60 Phase 2: edit-journey helpers for loading existing CAD files.
    # `load_step` returns a build123d Part for the algebraic API; the
    # `_shape` variant returns the raw TopoDS_Shape for use with helpers
    # like mirror_fuse that operate on raw OCCT types.
    script_globals["load_step"] = _load_step
    script_globals["load_step_shape"] = _load_step_shape

    # M60 Phase 3: addressability — agents read inspect --ids output to
    # identify a face/edge by ID, then pick it here for use in edit ops.
    # Returns build123d Face / Edge so the algebraic API works directly.
    script_globals["pick_face"] = _pick_face
    script_globals["pick_edge"] = _pick_edge

    # M60 Phase 4a: edit helpers built on Phase 3's pick_*. Each takes
    # an ID (or list of IDs) from `inspect --ids` and applies a
    # high-level operation, returning a build123d Part.
    script_globals["fillet_edges"] = _fillet_edges
    script_globals["chamfer_edges"] = _chamfer_edges
    script_globals["shell_faces"] = _shell_faces

    # M60 Phase 4b: split a shape by an infinite plane. Returns both
    # halves; agents can keep one or use both.
    script_globals["split_by_plane"] = _split_by_plane

    # M60 Phase 4c: feature ops — add NEW geometry by extruding a 2D
    # profile from a picked face. cut_pocket goes into the solid;
    # boss goes outward.
    script_globals["cut_pocket"] = _cut_pocket
    script_globals["boss"] = _boss

    effective_params = dict(discovered)
    if params:
        effective_params.update(params)

    # Capture warnings.warn() calls from helpers + the script body so they
    # surface in the run output's warnings array. Without this, helper-side
    # signals (tapered_sweep kink detection, etc.) vanish silently.
    #
    # NB: bind the warnings record to a name that doesn't collide with the
    # `captured` list of show_object results above. `as captured` here would
    # rebind the show_object capture list inside the `with` block, breaking
    # every build123d script (regression introduced in #142, found by the
    # #131 friction trial).
    captured_warnings: list[_warnings.WarningMessage] = []
    try:
        with _warnings.catch_warnings(record=True) as ws:
            _warnings.simplefilter("always", category=UserWarning)
            code = compile(source, filename, "exec")
            exec(code, script_globals)
            captured_warnings = list(ws)
    except Exception as e:
        return ExecutionResult(
            status="execution_error",
            discovered_parameters=discovered,
            parameters=effective_params,
            exception=f"Script execution failed: {type(e).__name__}: {e}",
            traceback=traceback.format_exc(),
        )

    if not captured:
        return ExecutionResult(
            status="execution_error",
            discovered_parameters=discovered,
            parameters=effective_params,
            exception=(
                "Script produced no results. Did you call show_object() "
                "or show_assembly()?"
            ),
        )

    warnings: list[str] = [
        str(w.message)
        for w in captured_warnings
        if issubclass(w.category, UserWarning)
        and not issubclass(w.category, DeprecationWarning)
    ]
    shapes = [obj for obj, _id, _n, _c, _g, _gc in captured]
    if len(shapes) == 1:
        native_shape = shapes[0]
    elif any(_is_topods_shape(s) for s in shapes):
        native_shape = _make_topods_compound(shapes)
    else:
        native_shape = Compound(children=list(shapes))
    output_type = (
        "assembly" if assembly_requested or len(shapes) > 1 else "single_part"
    )

    topo_shape = _topods_shape(native_shape)

    parts = [
        {
            "id": idx,
            "explicit_id": explicit_id,
            "name": name,
            "color": color,
            "part_of": part_of,
            "group_color": group_color,
            "topo_shape": _topods_shape(obj),
        }
        for idx, (obj, explicit_id, name, color, part_of, group_color) in enumerate(captured)
    ]

    return ExecutionResult(
        status="success",
        native_shape=native_shape,
        topo_shape=topo_shape,
        parameters=effective_params,
        discovered_parameters=discovered,
        warnings=warnings,
        output_type=output_type,
        parts=parts,
    )


def _load_step(path: str):
    """Load a STEP/STP file as a build123d ``Part``.

    Phase 2 of the M60 edit journey. Wraps build123d's ``import_step``,
    silencing OCCT's native stdout writes (which would otherwise leak
    into the script's output and break JSON contracts).
    """
    from build123d import Part, import_step
    from agentcad.native_io import silence_native_stdout

    with silence_native_stdout():
        loaded = import_step(path)
    # `import_step` returns a Compound; wrap in Part for the documented
    # `load_step(path) -> Part` contract. Part accepts a Compound.
    if isinstance(loaded, Part):
        return loaded
    return Part(loaded.wrapped) if hasattr(loaded, "wrapped") else Part(loaded)


def _load_step_shape(path: str):
    """Load a STEP/STP/BREP file as a raw ``TopoDS_Shape``.

    The escape hatch for using raw-OCCT helpers (``mirror_fuse``,
    ``loft_sections`` etc.) without the ``.val().wrapped`` dance.
    Delegates to the consolidated loader so silencing + empty-compound
    detection live in one place.
    """
    from agentcad.step_io import load_cad_shape
    return load_cad_shape(path)


def _pick_face(shape, face_id: int):
    """Pick a face by ID (from `agentcad inspect --ids`) and return a
    build123d Face. Accepts either a build123d Shape or a raw TopoDS_Shape.
    Raises ValueError on out-of-range IDs."""
    from build123d import Face, Shape
    from agentcad.topo_ids import pick_face_topods

    topo = shape.wrapped if isinstance(shape, Shape) else shape
    return Face(pick_face_topods(topo, face_id))


def _pick_edge(shape, edge_id: int):
    """Pick an edge by ID (from `agentcad inspect --ids`) and return a
    build123d Edge. Accepts either a build123d Shape or a raw TopoDS_Shape.
    Raises ValueError on out-of-range IDs."""
    from build123d import Edge, Shape
    from agentcad.topo_ids import pick_edge_topods

    topo = shape.wrapped if isinstance(shape, Shape) else shape
    return Edge(pick_edge_topods(topo, edge_id))


def _coerce_id_list(ids):
    """Accept a single int or an iterable of ints; return a list."""
    if isinstance(ids, int):
        return [ids]
    return list(ids)


def _fillet_edges(shape, ids, r: float):
    """Fillet the edges identified by ID (from `agentcad inspect --ids`).

    `ids` may be a single int or a list of ints. `r` is the fillet radius.
    Returns a build123d Part. Out-of-range IDs raise ValueError.
    """
    edges = [_pick_edge(shape, i) for i in _coerce_id_list(ids)]
    return shape.fillet(radius=r, edge_list=edges)


def _chamfer_edges(shape, ids, d: float):
    """Chamfer the edges identified by ID (from `agentcad inspect --ids`).

    `ids` may be a single int or a list of ints. `d` is the chamfer distance.
    Returns a build123d Part. Out-of-range IDs raise ValueError.
    """
    edges = [_pick_edge(shape, i) for i in _coerce_id_list(ids)]
    return shape.chamfer(length=d, length2=None, edge_list=edges)


def _shell_faces(shape, ids, thickness: float):
    """Hollow `shape` by offsetting walls by `thickness`, opening at the
    listed face IDs.

    Negative thickness shells inward (the typical "carve out the inside,
    leave |thickness|mm walls" case). Positive shells outward. `ids` may
    be a single int or a list of ints. Returns a build123d Part.

    Implementation uses OCP's BRepOffsetAPI_MakeThickSolid directly
    rather than build123d's `offset()` builder because the latter's
    parameter shape varies across versions; the low-level OCP path is
    stable and matches what every other commercial CAD shells to.
    """
    from build123d import Part, Shape
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.TopTools import TopTools_ListOfShape

    topo = shape.wrapped if isinstance(shape, Shape) else shape
    face_objs = [_pick_face(shape, i) for i in _coerce_id_list(ids)]

    faces_to_remove = TopTools_ListOfShape()
    for f in face_objs:
        faces_to_remove.Append(f.wrapped if isinstance(f, Shape) else f)

    # Inner-loop detection: OCCT's MakeThickSolid can't shell a face that
    # has an inner wire (e.g. the top face of a through-holed plate). Surface
    # this as a topology error instead of letting it fail with a generic
    # "geometry too thin" message — the friction-test caught this masking.
    inner_loop_face_ids = _faces_with_inner_loops(face_objs, _coerce_id_list(ids))
    if inner_loop_face_ids:
        raise ValueError(
            f"shell_faces can't open face(s) {inner_loop_face_ids} — they have "
            "inner loops (e.g. a through-hole pierces the face). OCCT's "
            "MakeThickSolid only opens simply-connected faces. Workarounds: "
            "(a) shell with a different opening face, "
            "(b) plug the hole before shelling and re-cut afterward, "
            "(c) use boolean cut + offset instead of shell_faces."
        )

    builder = BRepOffsetAPI_MakeThickSolid()
    builder.MakeThickSolidByJoin(topo, faces_to_remove, thickness, 1e-3)
    if not builder.IsDone():
        raise ValueError(
            "shell_faces failed — geometry may be too thin for the requested "
            "thickness, or the face selection may not produce a valid hollow. "
            "Try a smaller |thickness| or different opening face(s)."
        )
    return Part(builder.Shape())


def _split_by_plane(shape, plane):
    """Cut a shape with an infinite plane and return both halves as a
    ``(below, above)`` tuple of build123d Parts.

    `plane` accepts:
      - a build123d Plane (e.g. ``Plane.XY``, ``Plane.XY.offset(5)``)
      - a string shorthand: ``"XY"``, ``"XZ"``, ``"YZ"``

    "above" is the side along the plane's normal direction; "below" is
    the opposite. Agents typically destructure: ``below, above = ...``
    and pick the half they want, or sum them back for a re-assembled
    cross-section view.
    """
    from build123d import Keep, split

    plane_obj = _resolve_plane(plane)
    above = split(shape, bisect_by=plane_obj, keep=Keep.TOP)
    below = split(shape, bisect_by=plane_obj, keep=Keep.BOTTOM)
    return (below, above)


def _cut_pocket(shape, face_id: int, profile, depth: float):
    """Cut a pocket on the face identified by ``face_id``, by extruding
    ``profile`` (a 2D Sketch / Face) into the solid by ``depth``.

    The profile is positioned on the picked face's plane (origin at the
    face's centroid, z_dir along the face's outward normal). The extrude
    direction is *into* the solid (opposite the outward normal).

    Limitation: works best on planar faces. Cylindrical / spherical faces
    use the tangent plane at the face centroid, which may produce
    unexpected results for curved cuts — use boolean ops on a separately-
    constructed feature instead.
    """
    if depth <= 0:
        raise ValueError(f"depth must be positive, got {depth}")
    from build123d import Plane, extrude

    face = _pick_face(shape, face_id)
    face_plane = Plane(face)
    positioned = face_plane * profile
    feature = extrude(positioned, amount=-depth)
    return shape - feature


def _boss(shape, face_id: int, profile, height: float):
    """Add a boss (raised feature) on the face identified by ``face_id``,
    by extruding ``profile`` (a 2D Sketch / Face) outward by ``height``.

    Mirror of cut_pocket: same face-plane setup, extrude direction is
    *away from* the solid (along the face's outward normal), and the
    feature is unioned in instead of cut out.
    """
    if height <= 0:
        raise ValueError(f"height must be positive, got {height}")
    from build123d import Plane, extrude

    face = _pick_face(shape, face_id)
    face_plane = Plane(face)
    positioned = face_plane * profile
    feature = extrude(positioned, amount=height)
    return shape + feature


_NAMED_PLANES = ("XY", "XZ", "YZ")


def _resolve_plane(plane):
    """Coerce a `plane` argument (Plane or string) into a build123d Plane."""
    from build123d import Plane

    if isinstance(plane, Plane):
        return plane
    if isinstance(plane, str):
        upper = plane.strip().upper()
        if upper in _NAMED_PLANES:
            return getattr(Plane, upper)
        raise ValueError(
            f"Invalid plane string '{plane}'. Valid options: "
            f"{', '.join(_NAMED_PLANES)}, or pass a build123d Plane object "
            "(e.g. `Plane.XY.offset(5)` for an offset, or "
            "`Plane(origin=(0,0,5), z_dir=(0,0,1))` for explicit)."
        )
    raise ValueError(
        f"plane must be a build123d Plane or one of "
        f"{_NAMED_PLANES}; got {type(plane).__name__}"
    )


def _faces_with_inner_loops(face_objs, face_ids: list[int]) -> list[int]:
    """Return the IDs of any faces that have inner wires (holes through them).
    Used by shell_faces to give a topology-aware error message instead of
    a generic 'geometry too thin' that masks the real cause."""
    from build123d import Shape
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS

    flagged = []
    for face_obj, fid in zip(face_objs, face_ids):
        topods = face_obj.wrapped if isinstance(face_obj, Shape) else face_obj
        topods_face = topods if "Face" in type(topods).__name__ else TopoDS.Face_s(topods)
        outer = BRepTools.OuterWire_s(topods_face)
        # Iterate face wires; if any wire isn't the outer wire, it's an inner loop.
        from OCP.TopAbs import TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        exp = TopExp_Explorer(topods_face, TopAbs_WIRE)
        wire_count = 0
        while exp.More():
            wire_count += 1
            exp.Next()
        if wire_count > 1:
            flagged.append(fid)
    return flagged


def _is_topods_shape(obj) -> bool:
    """Return True for raw OCP TopoDS_Shape / TopoDS_Compound instances."""
    try:
        from OCP.TopoDS import TopoDS_Shape
    except ImportError:
        return False
    return isinstance(obj, TopoDS_Shape)


def _topods_shape(obj):
    """Extract the raw TopoDS shape from either build123d or OCP objects."""
    from build123d import Shape

    if isinstance(obj, Shape):
        return obj.wrapped
    if _is_topods_shape(obj):
        return obj
    raise TypeError(
        f"Expected build123d Shape or TopoDS_Shape, got {type(obj).__name__}"
    )


def _make_topods_compound(shapes):
    """Build a raw OCCT compound without converting through build123d."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, _topods_shape(shape))
    return compound


def _export_topods_step(shape, path: str) -> None:
    """Write a raw TopoDS shape through OCCT's STEP writer."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from agentcad.native_io import silence_native_stdout

    with silence_native_stdout():
        writer = STEPControl_Writer()
        status = writer.Transfer(shape, STEPControl_AsIs)
        if status != IFSelect_RetDone:
            raise RuntimeError(f"OCCT STEP transfer failed with status {status}")
        status = writer.Write(path)
        if status != IFSelect_RetDone:
            raise RuntimeError(f"OCCT STEP write failed with status {status}")


def export_step(native_shape: Any, path: str) -> None:
    """Write STEP using build123d, or OCCT directly for raw TopoDS shapes."""
    if _is_topods_shape(native_shape):
        _export_topods_step(native_shape, path)
        return

    from build123d import export_step as _export

    _export(native_shape, path)


def export_stl(native_shape: Any, path: str) -> None:
    """Write STL using build123d, or OCCT directly for raw TopoDS shapes."""
    if _is_topods_shape(native_shape):
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.StlAPI import StlAPI_Writer

        BRepMesh_IncrementalMesh(native_shape, 0.1)
        writer = StlAPI_Writer()
        ok = writer.Write(native_shape, path)
        if ok is False:
            raise RuntimeError(f"OCCT STL write failed for {path}")
        return

    from build123d import export_stl as _export

    _export(native_shape, path)
