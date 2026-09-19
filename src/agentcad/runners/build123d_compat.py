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
from typing import Any


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
_AXIS_ROTATIONS = {
    "X": (0, 90, 0),
    "Y": (-90, 0, 0),
    "Z": None,
}
# Sketch primitives are flat faces: their orientation is the plane they are
# drawn on, not a rotation tuple. Map each axis to the familiar named plane
# whose normal lies along it, plus the signed normal build123d actually uses
# (``Plane.XZ`` faces -Y, so ``extrude`` from it grows toward -Y).
_AXIS_SKETCH_PLANES = {
    "X": ("Plane.YZ", "+X"),
    "Y": ("Plane.XZ", "-Y"),
    "Z": ("Plane.XY", "+Z"),
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


def _axis_from_value(value: Any) -> str | None:
    """Return ``"X"``/``"Y"``/``"Z"`` for an axis name or a unit axis vector."""
    if isinstance(value, str):
        axis = value.strip().upper()
        return axis if axis in _AXIS_ROTATIONS else None
    if isinstance(value, (tuple, list)) and len(value) == 3 and all(
        _is_number(v) for v in value
    ):
        for axis, unit in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
            if tuple(value) == unit:
                return axis
    return None


def _sketch_orientation_hint(
    class_name: str,
    keyword: str,
    axis: str,
    args: tuple,
    kwargs: dict[str, Any],
) -> str:
    """Plane-based repair for an axis-like request on a sketch primitive.

    Sketch primitives have no 3D ``rotation=`` tuple: ``Circle`` rejects the
    keyword, ``RegularPolygon`` expects a scalar, and ``Rectangle`` silently
    stays on XY. The orientation comes from the builder plane instead, and
    any scalar ``rotation=`` the caller supplied is an in-plane angle that
    composes with the plane, so it is preserved rather than reported as a
    conflict.
    """
    call = _example_call(class_name, args, kwargs, exclude=frozenset({keyword}))
    plane, normal = _AXIS_SKETCH_PLANES[axis]
    if axis == "Z":
        hint = (
            f"Plane.XY, the default sketch plane, already has its normal along "
            f"+Z, so drop {keyword}=: with BuildSketch(Plane.XY): {call}."
        )
        if "mode" not in kwargs:
            hint += f" Outside a builder: {call}."
        return hint
    hint = (
        f"Choose the sketch plane instead: with BuildSketch({plane}): {call} "
        f"lies on {plane}, whose normal points along {normal}, so "
        f"extrude(amount=N) grows toward {normal}."
    )
    if axis == "Y":
        hint += " Use Plane.ZX for a +Y normal."
    if "mode" not in kwargs:
        hint += f" Outside a builder: {plane} * {call}."
    if "rotation" in kwargs:
        hint += " rotation= stays an in-plane angle on that plane."
    return hint


def _sketch_plane_menu(class_name: str, keyword: str, call: str) -> str:
    """Diagnostic for an orientation request whose axis cannot be read."""
    return (
        f"Could not read an axis from {keyword}=. {class_name} takes its "
        f"orientation from the sketch plane, not a keyword: "
        f"with BuildSketch(Plane.YZ): {call} faces +X, Plane.XZ faces -Y, "
        f"and Plane.XY (the default) faces +Z; extrude(amount=N) grows along "
        f"that normal. The rotation= keyword on a sketch primitive is only "
        f"an in-plane angle in degrees."
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
    class_name: str, keyword: str, value: Any, args: tuple, kwargs: dict[str, Any],
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
                    f"point along +{axis} instead, replace rotation=: "
                    f"{oriented_call}."
                )
            if axis == "Z":
                return (
                    f"{head} {class_name} already points along +Z; drop the "
                    f"keyword: {oriented_call}"
                )
            return (
                f"{head} Build along Z and orient it explicitly: "
                f"{oriented_call} points along +{axis}."
            )
        return (
            f"{head} axis must be 'X', 'Y', or 'Z'. Build along Z and rotate: "
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
    class_name: str, value: Any, args: tuple, kwargs: dict[str, Any],
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
        hint = _sketch_orientation_hint(class_name, "align", axis, args, kwargs)
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
            f"align=: {existing_call}. To point along +{axis} instead, replace "
            f"rotation=: {correction}."
        )
        return f"Invalid align={value!r} for {class_name}(). {supported}{axis_hint}"
    if axis == "Z":
        axis_hint = (
            f" align={value!r} is not an alignment value; {class_name} already "
            f"points along +Z. Drop align= and use: {correction}."
        )
    else:
        axis_hint = (
            f" align={value!r} is not an alignment value. To point the primitive "
            f"along +{axis}, use: {correction}."
        )
    return f"Invalid align={value!r} for {class_name}(). {supported}{axis_hint}"


def _normalize_alignment(
    class_name: str, args: tuple, kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Normalize semantic alignment strings without guessing coordinates."""
    if "align" not in kwargs:
        return kwargs

    from build123d import Align

    value = kwargs["align"]
    dimensions = _ALIGN_DIMENSIONS[class_name]
    names = {member.name.lower(): member for member in Align}

    if isinstance(value, Align) or value is None:
        return kwargs
    if isinstance(value, str):
        member = names.get(value.strip().lower())
        if member is None:
            raise PrimitiveArgumentError(
                _alignment_message(class_name, value, args, kwargs)
            )
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
                    raise PrimitiveArgumentError(
                        _alignment_message(class_name, value, args, kwargs)
                    )
                members.append(member)
            normalized = dict(kwargs)
            normalized["align"] = tuple(members)
            return normalized

    raise PrimitiveArgumentError(
        _alignment_message(class_name, value, args, kwargs)
    )


def normalize_primitive_kwargs(
    class_name: str, native_cls: type, args: tuple, kwargs: dict[str, Any],
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
            raise PrimitiveArgumentError(
                _placement_message(class_name, keyword, value, args, normalized)
            )

    return _normalize_alignment(class_name, args, normalized)


def make_compat_primitive(native_cls: type) -> type:
    """Subclass ``native_cls`` so its constructor accepts the aliases above."""
    class_name = native_cls.__name__

    def __init__(self, *args, **kwargs):
        kwargs = normalize_primitive_kwargs(class_name, native_cls, args, kwargs)
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
