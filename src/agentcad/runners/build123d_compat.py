"""Compatibility constructors for build123d primitives.

Generated scripts routinely call ``Cylinder(diameter=10, height=20)``,
``Cylinder(d=10, h=20)`` or ``Box(10, 20, 5, center=(0, 0, 10))``. Native
build123d rejects every one of those with a bare
``unexpected keyword argument`` TypeError, and the agent has to guess
the fix. Issue #192 counted 35 such failures in a single benchmark run.

This module wraps the public constructors in thin subclasses that:

* normalize high-confidence dimension aliases (``diameter``/``d`` ->
  ``radius``, ``h`` -> ``height``, ...) before delegating to the native
  ``__init__``;
* fail clearly when an alias and its canonical name are both supplied;
* normalize unambiguous ``align=`` strings and reject coordinate tuples with
  a copyable ``.translate(...)`` correction instead of treating numbers as
  :class:`build123d.Align` enum values;
* reject placement keywords (``center=``, ``at=``, ``centered=``,
  ``axis=``) with a copyable ``.translate(...)`` / ``align=`` /
  ``rotation=`` example instead of a generic TypeError;
* let builder contexts accept familiar plane strings such as ``"XY"`` while
  diagnosing other strings before they reach ``WorkplaneList``.

Positional arguments are passed through untouched — native build123d
semantics are preserved and never guessed at. The wrappers subclass the
native classes, so ``isinstance`` checks, builder-context integration
(``with BuildPart(): Cylinder(...)``) and every native keyword keep
working exactly as before.
"""

from __future__ import annotations

import inspect
import math
import numbers
from enum import Enum
from typing import Any, NamedTuple


# Canonical parameter -> accepted aliases, per primitive.
_DIMENSION_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "Cylinder": {
        "radius": ("r", "diameter", "dia", "d"),
        "height": ("h", "length"),
    },
    "Box": {
        "length": ("l",),
        "width": ("w",),
        "height": ("h",),
    },
    "Circle": {
        "radius": ("r", "diameter", "dia", "d"),
    },
    "Sphere": {
        "radius": ("r", "diameter", "dia", "d"),
    },
    "Cone": {
        "height": ("h",),
    },
}

# Aliases whose value is a diameter and must be halved to get a radius.
_DIAMETER_ALIASES = frozenset({"diameter", "dia", "d"})

# Placement keywords that build123d primitives do not accept. Each maps to
# the kind of guidance the agent needs.
_PLACEMENT_KEYWORDS: dict[str, str] = {
    "center": "position",
    "centre": "position",
    "at": "position",
    "position": "position",
    "pos": "position",
    "location": "position",
    "loc": "position",
    "origin": "position",
    "offset": "position",
    "centered": "centered",
    "centred": "centered",
    "axis": "orientation",
    "direction": "orientation",
    "dir": "orientation",
    "normal": "orientation",
}

_ALIGN_DIMENSIONS = {
    "Box": 3,
    "Cone": 3,
    "Cylinder": 3,
    "Sphere": 3,
    "Torus": 3,
    "Wedge": 3,
    "Circle": 2,
    "Ellipse": 2,
    "Polygon": 2,
    "Rectangle": 2,
    "RectangleRounded": 2,
    "RegularPolygon": 2,
    "SlotOverall": 2,
    "Text": 2,
    "Trapezoid": 2,
    "Triangle": 2,
}

_SKETCH_PRIMITIVES = frozenset(
    name for name, dimensions in _ALIGN_DIMENSIONS.items() if dimensions == 2
)

_BUILDER_NAMES = ("BuildPart", "BuildSketch", "BuildLine")
_PLANE_NAMES = ("XY", "XZ", "YZ", "YX", "ZX", "ZY")
# Signed axis -> rotation= that points a Z-built solid along it. A bare
# "X" means +X. Verified against Cone apexes.
_AXIS_ROTATIONS = {
    "+X": (0, 90, 0),
    "-X": (0, -90, 0),
    "+Y": (-90, 0, 0),
    "-Y": (90, 0, 0),
    "+Z": None,
    "-Z": (180, 0, 0),
}
# Sketch primitives are flat faces: their orientation is the plane they are
# drawn on, not a rotation tuple. Each named plane's normal is signed;
# swapping the letters flips it (``Plane.XZ`` faces -Y, ``Plane.ZX`` +Y).
_AXIS_SKETCH_PLANES = {
    "+X": "Plane.YZ",
    "-X": "Plane.ZY",
    "+Y": "Plane.ZX",
    "-Y": "Plane.XZ",
    "+Z": "Plane.XY",
    "-Z": "Plane.YX",
}

_COMPAT_MARKER = "__agentcad_compat__"


class PrimitiveArgumentError(TypeError):
    """Raised for alias conflicts, invalid alias values and placement keywords."""


def _is_number(value: Any) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _fmt(value: Any) -> str:
    """Compact literal for building copyable examples."""
    if _is_number(value):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, tuple):
        values = ", ".join(_fmt(item) for item in value)
        if len(value) == 1:
            values += ","
        return f"({values})"
    if isinstance(value, list):
        return "[" + ", ".join(_fmt(item) for item in value) + "]"
    return repr(value)


def _positional_names(native_cls: type) -> list[str]:
    params = list(inspect.signature(native_cls.__init__).parameters.values())
    return [p.name for p in params[1:] if p.kind in (
        p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD,
    )]


def _resolved_dimensions(
    class_name: str, kwargs: dict[str, Any], skip: str,
) -> dict[str, Any]:
    """Best-effort canonical view of ``kwargs`` for a conflict message.

    Skips the offending alias, halves diameters, and keeps the canonical
    value when both were given, so the example reads as what the agent
    most likely meant.
    """
    alias_map = _DIMENSION_ALIASES.get(class_name, {})
    alias_to_canonical = {
        alias: canonical
        for canonical, aliases in alias_map.items()
        for alias in aliases
    }
    resolved: dict[str, Any] = {}
    for name, value in kwargs.items():
        if name == skip:
            continue
        if name in alias_map:
            resolved[name] = value
        elif name in alias_to_canonical:
            canonical = alias_to_canonical[name]
            if canonical in resolved:
                continue
            if name in _DIAMETER_ALIASES and _is_number(value):
                value = value / 2
            resolved[canonical] = value
    return resolved


def _example_call(
    class_name: str,
    args: tuple,
    kwargs: dict[str, Any],
    *,
    exclude: frozenset[str] = frozenset(),
    overrides: dict[str, Any] | None = None,
) -> str:
    """Render a copyable call while preserving every non-offending argument."""
    overrides = overrides or {}
    rendered = [_fmt(a) for a in args]
    rendered.extend(
        f"{name}={_fmt(overrides.get(name, value))}"
        for name, value in kwargs.items()
        if name not in exclude
    )
    rendered.extend(
        f"{name}={_fmt(value)}"
        for name, value in overrides.items()
        if name not in kwargs and name not in exclude
    )
    if not rendered:
        rendered.append("...")
    return f"{class_name}({', '.join(rendered)})"


def _vector_literal(value: Any, size: int) -> str | None:
    if isinstance(value, (tuple, list)) and len(value) == size and all(
        _is_number(v) for v in value
    ):
        return "(" + ", ".join(_fmt(v) for v in value) + ")"
    return None


class _BuilderInfo(NamedTuple):
    """The builder context a primitive was constructed in."""

    kind: str          # "BuildSketch", "BuildPart", "BuildLine"
    plane: str | None  # "Plane.XY" when the builder sits on one named plane


def _current_builder() -> _BuilderInfo | None:
    """Describe the innermost active build123d builder, if any."""
    try:
        from build123d import Plane
        from build123d.build_common import Builder
    except ImportError:  # pragma: no cover - build123d missing entirely
        return None
    context = Builder._get_context(log=False)
    if context is None:
        return None
    plane = None
    workplanes = getattr(context, "workplanes", None) or []
    if len(workplanes) == 1:
        for name in _PLANE_NAMES:
            if workplanes[0] == getattr(Plane, name):
                plane = f"Plane.{name}"
                break
    return _BuilderInfo(type(context).__name__, plane)


def _axis_from_value(value: Any) -> str | None:
    """Return a signed axis (``"+X"`` ... ``"-Z"``) for an axis name or vector.

    Accepts ``"X"`` (meaning +X), ``"-y"``, and unit vectors such as
    ``(0, 0, -1)``. Anything else is unreadable and returns ``None``.
    """
    if hasattr(value, "to_tuple"):
        value = value.to_tuple()
    if isinstance(value, str):
        text = value.strip().upper()
        if len(text) == 1 and text in "XYZ":
            return "+" + text
        if len(text) == 2 and text[0] in "+-" and text[1] in "XYZ":
            return text
        return None
    if isinstance(value, (tuple, list)) and len(value) == 3 and all(
        _is_number(v) for v in value
    ):
        nonzero = [(i, v) for i, v in enumerate(value) if v != 0]
        if len(nonzero) == 1 and abs(nonzero[0][1]) == 1:
            index, component = nonzero[0]
            return ("+" if component > 0 else "-") + "XYZ"[index]
    return None


_AXIS_FORMS = "'X', '-Y', or a unit vector such as (0, 0, 1)"


def _sketch_rotation_note(
    class_name: str, native_cls: type | None, value: Any,
) -> tuple[bool, str]:
    """Return ``(keep, note)`` for a ``rotation=`` given to a sketch primitive.

    Only a finite scalar on a primitive whose native signature has
    ``rotation`` survives into the literal repair: ``Circle`` has no such
    keyword and the others take one in-plane angle, never a tuple.
    """
    accepts = native_cls is not None and "rotation" in inspect.signature(
        native_cls.__init__
    ).parameters
    if not accepts:
        return False, f" {class_name} has no rotation= keyword, so it was dropped."
    if _is_number(value) and math.isfinite(value):
        return True, " rotation= stays an in-plane angle on that plane."
    return False, (
        f" rotation= on {class_name} is a single in-plane angle in degrees, "
        f"not {_fmt(value)}, so it was dropped; add rotation=<degrees> if the "
        f"face should turn within its plane."
    )


def _sketch_orientation_hint(
    class_name: str,
    keyword: str,
    axis: str,
    args: tuple,
    kwargs: dict[str, Any],
    *,
    native_cls: type | None = None,
    builder: _BuilderInfo | None = None,
) -> str:
    """Plane-based repair for an axis-like request on a sketch primitive.

    Sketch primitives have no 3D ``rotation=`` tuple; orientation comes from
    the sketch plane. The repair depends on where the call sits: inside an
    existing BuildSketch the *enclosing* plane must change (a nested sketch
    is flattened onto the outer plane), directly inside a BuildPart the
    primitive needs its own BuildSketch, and outside any builder the face
    is relocated with ``Plane.YZ * Circle(5)``.
    """
    plane = _AXIS_SKETCH_PLANES[axis]
    exclude = {keyword}
    rotation_note = ""
    if "rotation" in kwargs:
        keep, rotation_note = _sketch_rotation_note(
            class_name, native_cls, kwargs["rotation"],
        )
        if not keep:
            exclude.add("rotation")
    call = _example_call(class_name, args, kwargs, exclude=frozenset(exclude))
    label = f"{plane} (the default)" if plane == "Plane.XY" else plane
    where = (
        f"{label}, whose normal points along {axis}, so extrude(amount=N) "
        f"grows toward {axis}"
    )
    kind = builder.kind if builder is not None else None

    if kind == "BuildSketch":
        if builder.plane == plane:
            hint = (
                f"The enclosing sketch is already on {plane}; drop {keyword}= "
                f"and keep: {call}."
            )
        else:
            current = f" (currently {builder.plane})" if builder.plane else ""
            hint = (
                f"Change the enclosing sketch's plane{current} and drop "
                f"{keyword}=: replace its header with "
                f"`with BuildSketch({plane}):` and this call with {call}. Do "
                f"not nest a second BuildSketch here; a nested sketch is "
                f"flattened onto the outer plane. The sketch then lies on "
                f"{where}."
            )
        mode = kwargs.get("mode")
        if mode is not None and getattr(mode, "name", "ADD") != "ADD":
            hint += (
                f" Leave it in the same sketch as the additive geometry it "
                f"modifies; a separate sketch holding only mode={_fmt(mode)} "
                f"has nothing to operate on."
            )
    elif kind == "BuildPart":
        hint = (
            f"Sketch primitives need a BuildSketch inside the BuildPart; drop "
            f"{keyword}= and use: with BuildSketch({plane}): {call}. The "
            f"sketch lies on {where}."
        )
    else:
        standalone = call if plane == "Plane.XY" else f"{plane} * {call}"
        hint = (
            f"Choose the sketch plane instead; drop {keyword}= and use: "
            f"{standalone}. The face lies on {where}. Inside a BuildPart, "
            f"use with BuildSketch({plane}): {call}."
        )
    return hint + rotation_note


def _sketch_plane_menu(class_name: str, keyword: str, call: str) -> str:
    """Diagnostic for an orientation request whose axis cannot be read."""
    return (
        f"Could not tell which axis {keyword}= means (expected {_AXIS_FORMS}). "
        f"{class_name} takes its orientation from the sketch plane, not a "
        f"keyword: Plane.YZ faces +X (Plane.ZY -X), Plane.ZX faces +Y "
        f"(Plane.XZ -Y), Plane.XY faces +Z (Plane.YX -Z), and "
        f"extrude(amount=N) grows along that normal, e.g. "
        f"with BuildSketch(Plane.YZ): {call}. The rotation= keyword on a "
        f"sketch primitive is only an in-plane angle in degrees."
    )


def _orientation_correction(
    class_name: str,
    value: Any,
    args: tuple,
    kwargs: dict[str, Any],
    *,
    exclude: frozenset[str] = frozenset(),
) -> tuple[str, str, str | None] | None:
    """Return ``(axis, target_call, existing_call)`` for an axis guess.

    ``existing_call`` is present when the caller also supplied ``rotation=``.
    That is a semantic conflict, not a rotation-composition request: the
    diagnostic presents the two calls separately so neither intent is hidden.
    """
    axis = _axis_from_value(value)
    if axis is None:
        return None

    call = _example_call(class_name, args, kwargs, exclude=exclude)
    rotation = _AXIS_ROTATIONS[axis]
    if "rotation" in kwargs and "rotation" not in exclude:
        target_rotation = (0, 0, 0) if rotation is None else rotation
        return axis, _example_call(
            class_name,
            args,
            kwargs,
            exclude=exclude,
            overrides={"rotation": target_rotation},
        ), call
    if rotation is None:
        return axis, call, None
    return axis, _example_call(
        class_name,
        args,
        kwargs,
        exclude=exclude,
        overrides={"rotation": rotation},
    ), None


def _placement_message(
    class_name: str,
    keyword: str,
    value: Any,
    args: tuple,
    kwargs: dict[str, Any],
    *,
    native_cls: type | None = None,
    builder: _BuilderInfo | None = None,
) -> str:
    kind = _PLACEMENT_KEYWORDS[keyword]
    call = _example_call(class_name, args, kwargs)
    is_sketch = class_name in _SKETCH_PRIMITIVES
    head = f"{class_name}() does not accept '{keyword}='."

    if kind == "centered" or isinstance(value, bool):
        if value is True:
            return (
                f"{head} build123d already centers {class_name} on the origin, "
                f"so drop the keyword: {call}"
            )
        if is_sketch:
            return (
                f"{head} Use align= to choose which corner sits at the origin: "
                f"{call.rstrip(')')}, align=(Align.MIN, Align.MIN))"
            )
        return (
            f"{head} Use align= to choose which corner sits at the origin: "
            f"{call.rstrip(')')}, align=(Align.MIN, Align.MIN, Align.MIN)) "
            f"puts the minimum corner at the origin and the shape extends "
            f"in +X, +Y, +Z. build123d centers shapes by default."
        )

    if kind == "orientation":
        if is_sketch:
            axis = _axis_from_value(value)
            if axis is None:
                return f"{head} {_sketch_plane_menu(class_name, keyword, call)}"
            return f"{head} " + _sketch_orientation_hint(
                class_name, keyword, axis, args, kwargs,
                native_cls=native_cls, builder=builder,
            )
        correction = _orientation_correction(
            class_name, value, args, kwargs
        )
        if correction is not None:
            axis, oriented_call, existing_call = correction
            if existing_call is not None:
                return (
                    f"{head} rotation= and {keyword}={value!r} request two "
                    f"orientations and cannot both be preserved. To keep the "
                    f"existing rotation, drop {keyword}=: {existing_call}. To "
                    f"point along {axis} instead, replace rotation=: "
                    f"{oriented_call}."
                )
            if axis == "+Z":
                return (
                    f"{head} {class_name} already points along +Z; drop the "
                    f"keyword: {oriented_call}"
                )
            return (
                f"{head} Build along Z and orient it explicitly: "
                f"{oriented_call} points along {axis}."
            )
        return (
            f"{head} Could not tell which axis {keyword}= means (expected "
            f"{_AXIS_FORMS}). Build along Z and rotate: "
            f"{call.rstrip(')')}, rotation=(0, 90, 0)) points along X, "
            f"rotation=(-90, 0, 0) points along Y. Move it afterwards with "
            f".translate((x, y, z))."
        )

    # Position-style placement.
    if "mode" in kwargs:
        vec = _vector_literal(value, 2 if is_sketch else 3) or (
            "(x, y)" if is_sketch else "(x, y, z)"
        )
        return (
            f"{head} The mode= argument means this runs inside a builder. "
            f"Position it with a location context: with Locations({vec}): {call}"
        )
    if is_sketch:
        vec = _vector_literal(value, 2) or "(x, y)"
        return (
            f"{head} Build at the origin, then move it: "
            f"Pos{vec} * {call}. Inside a BuildSketch, use "
            f"with Locations({vec}): {call}"
        )
    vec = _vector_literal(value, 3) or "(x, y, z)"
    return (
        f"{head} Build at the origin, then move it: "
        f"{call}.translate({vec}) or Pos{vec} * {call}. Inside a BuildPart, "
        f"use with Locations({vec}): {call}"
    )


def _alignment_message(
    class_name: str,
    value: Any,
    args: tuple,
    kwargs: dict[str, Any],
    *,
    native_cls: type | None = None,
    builder: _BuilderInfo | None = None,
) -> str:
    """Return targeted guidance for an invalid or positional ``align=`` value."""
    dimensions = _ALIGN_DIMENSIONS[class_name]
    call = _example_call(class_name, args, kwargs, exclude=frozenset({"align"}))
    tuple_example = ", ".join(
        ("Align.MIN", "Align.CENTER", "Align.MAX")[:dimensions]
    )
    supported = (
        "Use align=Align.MIN, align=Align.CENTER, align=Align.MAX, "
        "align=Align.NONE, or "
        f"align=({tuple_example}). The string shortcuts 'min', 'center', "
        "'max', and 'none' are also accepted."
    )

    if _vector_literal(value, dimensions) is not None:
        vector = _vector_literal(value, dimensions)
        if "mode" in kwargs:
            correction = f"with Locations({vector}): {call}"
            instruction = f"Inside a builder, use exactly: {correction}"
        elif dimensions == 2:
            correction = f"Pos{vector} * {call}"
            instruction = f"Move the shape instead: {correction}"
        else:
            correction = f"{call}.translate({vector})"
            instruction = f"Move the shape instead: {correction}"
        return (
            f"{class_name}() align= controls which bounding-box side is anchored "
            f"at the origin; it does not accept position coordinates {vector}. "
            f"{instruction}. {supported}"
        )

    if dimensions == 2:
        axis = _axis_from_value(value)
        if axis is None:
            return f"Invalid align={value!r} for {class_name}(). {supported}"
        hint = _sketch_orientation_hint(
            class_name, "align", axis, args, kwargs,
            native_cls=native_cls, builder=builder,
        )
        return (
            f"Invalid align={value!r} for {class_name}(): it names an axis, "
            f"not an alignment. {hint} {supported}"
        )

    orientation = _orientation_correction(
        class_name,
        value,
        args,
        kwargs,
        exclude=frozenset({"align"}),
    )
    if orientation is None:
        return f"Invalid align={value!r} for {class_name}(). {supported}"
    axis, correction, existing_call = orientation
    if existing_call is not None:
        axis_hint = (
            f" align={value!r} and rotation= request two orientations "
            f"that cannot both be preserved. To keep the existing rotation, drop "
            f"align=: {existing_call}. To point along {axis} instead, replace "
            f"rotation=: {correction}."
        )
        return f"Invalid align={value!r} for {class_name}(). {supported}{axis_hint}"
    if axis == "+Z":
        axis_hint = (
            f" align={value!r} is not an alignment value; {class_name} already "
            f"points along +Z. Drop align= and use: {correction}."
        )
    else:
        axis_hint = (
            f" align={value!r} is not an alignment value. To point the primitive "
            f"along {axis}, use: {correction}."
        )
    return f"Invalid align={value!r} for {class_name}(). {supported}{axis_hint}"


def _normalize_alignment(
    class_name: str,
    args: tuple,
    kwargs: dict[str, Any],
    *,
    native_cls: type | None = None,
    builder: _BuilderInfo | None = None,
) -> dict[str, Any]:
    """Normalize semantic alignment strings without guessing coordinates."""
    if "align" not in kwargs:
        return kwargs

    def invalid() -> PrimitiveArgumentError:
        return PrimitiveArgumentError(_alignment_message(
            class_name, value, args, kwargs,
            native_cls=native_cls, builder=builder,
        ))

    from build123d import Align

    value = kwargs["align"]
    dimensions = _ALIGN_DIMENSIONS[class_name]
    names = {member.name.lower(): member for member in Align}

    if isinstance(value, Align) or value is None:
        return kwargs
    if isinstance(value, str):
        member = names.get(value.strip().lower())
        if member is None:
            raise invalid()
        normalized = dict(kwargs)
        normalized["align"] = member
        return normalized
    if isinstance(value, (tuple, list)):
        if len(value) == dimensions and all(
            isinstance(item, (Align, str)) for item in value
        ):
            members = []
            for item in value:
                if isinstance(item, Align):
                    members.append(item)
                    continue
                member = names.get(item.strip().lower())
                if member is None:
                    raise invalid()
                members.append(member)
            normalized = dict(kwargs)
            normalized["align"] = tuple(members)
            return normalized

    raise invalid()


def normalize_primitive_kwargs(
    class_name: str,
    native_cls: type,
    args: tuple,
    kwargs: dict[str, Any],
    *,
    builder: _BuilderInfo | None = None,
) -> dict[str, Any]:
    """Return ``kwargs`` with aliases normalized to native names.

    Raises :class:`PrimitiveArgumentError` for alias conflicts, invalid
    alias values, and placement keywords the native constructor does not
    accept. Keywords that are neither aliases nor placement are passed
    through untouched so native build123d errors still apply.
    """
    alias_map = _DIMENSION_ALIASES.get(class_name, {})
    alias_to_canonical = {
        alias: canonical
        for canonical, aliases in alias_map.items()
        for alias in aliases
    }
    positional = _positional_names(native_cls)
    supplied_positionally = set(positional[: len(args)])

    normalized = dict(kwargs)
    for alias, canonical in alias_to_canonical.items():
        if alias not in kwargs:
            continue
        value = normalized.pop(alias)
        example = _example_call(
            class_name, args, _resolved_dimensions(class_name, kwargs, skip=alias),
        )
        if canonical in kwargs:
            raise PrimitiveArgumentError(
                f"{class_name}() got both '{alias}=' and '{canonical}='. "
                f"Pass only one of them, e.g. {example}"
            )
        if canonical in supplied_positionally:
            index = positional.index(canonical)
            raise PrimitiveArgumentError(
                f"{class_name}() got '{alias}=' but {canonical} was already "
                f"passed positionally (argument {index + 1} = {_fmt(args[index])}). "
                f"Pass only one of them, e.g. {example}"
            )
        if canonical in normalized:
            other = next(
                a for a in alias_map[canonical] if a != alias and a in kwargs
            )
            raise PrimitiveArgumentError(
                f"{class_name}() got both '{alias}=' and '{other}=' — they both "
                f"set {canonical}. Pass only one, e.g. {example}"
            )
        if not _is_number(value) or not math.isfinite(value) or value <= 0:
            raise PrimitiveArgumentError(
                f"{class_name}({alias}=...) must be a positive number, got "
                f"{value!r}."
            )
        if alias in _DIAMETER_ALIASES:
            value = value / 2
        normalized[canonical] = value

    for keyword in _PLACEMENT_KEYWORDS:
        if keyword in normalized:
            value = normalized.pop(keyword)
            raise PrimitiveArgumentError(_placement_message(
                class_name, keyword, value, args, normalized,
                native_cls=native_cls, builder=builder,
            ))

    return _normalize_alignment(
        class_name, args, normalized, native_cls=native_cls, builder=builder,
    )


def make_compat_primitive(native_cls: type) -> type:
    """Subclass ``native_cls`` so its constructor accepts the aliases above."""
    class_name = native_cls.__name__

    def __init__(self, *args, **kwargs):
        kwargs = normalize_primitive_kwargs(
            class_name, native_cls, args, kwargs, builder=_current_builder(),
        )
        native_cls.__init__(self, *args, **kwargs)

    __init__.__wrapped__ = native_cls.__init__  # inspect.signature parity
    __init__.__doc__ = native_cls.__init__.__doc__

    return type(class_name, (native_cls,), {
        "__init__": __init__,
        "__doc__": native_cls.__doc__,
        "__module__": __name__,
        "__qualname__": class_name,
        _COMPAT_MARKER: native_cls,
    })


def make_compat_builder(native_cls: type, plane_cls: type) -> type:
    """Wrap a builder context so common plane-name strings are unambiguous."""
    class_name = native_cls.__name__

    def __init__(self, *workplanes, **kwargs):
        normalized = []
        for workplane in workplanes:
            if not isinstance(workplane, str):
                normalized.append(workplane)
                continue
            name = workplane.strip().upper()
            if name not in _PLANE_NAMES:
                choices = ", ".join(f"Plane.{item}" for item in _PLANE_NAMES)
                raise PrimitiveArgumentError(
                    f"{class_name}() workplanes cannot be arbitrary strings; "
                    f"got {workplane!r}. Use one of {choices}, e.g. "
                    f"with {class_name}(Plane.XY): ..."
                )
            normalized.append(getattr(plane_cls, name))
        native_cls.__init__(self, *normalized, **kwargs)
        # build123d links a nested builder to its parent only when both were
        # created in the same Python frame, which Builder.__init__ records as
        # the frame two levels up (script -> BuildPart.__init__ -> Builder).
        # This wrapper adds a level, so without this line a BuildSketch
        # inside a BuildPart never hands its faces over and extrude() fails
        # with "A face or sketch must be provided".
        frame = inspect.currentframe()
        if frame is not None and frame.f_back is not None:
            self._python_frame = frame.f_back

    __init__.__wrapped__ = native_cls.__init__
    __init__.__doc__ = native_cls.__init__.__doc__

    return type(class_name, (native_cls,), {
        "__init__": __init__,
        "__doc__": native_cls.__doc__,
        "__module__": __name__,
        "__qualname__": class_name,
        _COMPAT_MARKER: native_cls,
    })


def compat_primitives(b3d_module) -> dict[str, type]:
    """Build (or reuse) the compatibility classes for ``b3d_module``."""
    result: dict[str, type] = {}
    constructor_names = dict.fromkeys((*_DIMENSION_ALIASES, *_ALIGN_DIMENSIONS))
    for class_name in constructor_names:
        current = getattr(b3d_module, class_name, None)
        if current is None:
            continue
        if getattr(current, _COMPAT_MARKER, None) is not None:
            result[class_name] = current  # already installed
        else:
            result[class_name] = make_compat_primitive(current)
    return result


def compat_builders(b3d_module) -> dict[str, type]:
    """Build (or reuse) wrappers for public builder context constructors."""
    result: dict[str, type] = {}
    for class_name in _BUILDER_NAMES:
        current = getattr(b3d_module, class_name, None)
        if current is None:
            continue
        if getattr(current, _COMPAT_MARKER, None) is not None:
            result[class_name] = current
        else:
            result[class_name] = make_compat_builder(current, b3d_module.Plane)
    return result


def install_compat_primitives(b3d_module) -> dict[str, type]:
    """Install compatible primitive and builder constructors on ``build123d``.

    Scripts commonly start with ``from build123d import *`` even though
    the runner pre-injects the API. Patching the package namespace means
    that import — and ``import build123d as bd; bd.Cylinder(...)`` — picks
    up the same forgiving constructors as the injected names. Idempotent:
    a second call is a no-op. Only the package-level names are touched;
    build123d's internal modules keep their native classes, which the
    wrappers subclass, so ``isinstance`` checks inside build123d hold.
    """
    classes = {
        **compat_primitives(b3d_module),
        **compat_builders(b3d_module),
    }
    for class_name, compat_cls in classes.items():
        if getattr(b3d_module, class_name) is not compat_cls:
            setattr(b3d_module, class_name, compat_cls)
    return classes
