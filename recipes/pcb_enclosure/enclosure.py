"""Adaptable PCB enclosure recipe for build123d scripts.

The template turns a small, explicit board description into a printable tray
and lid. It is deliberately *spec-first*: every dimension the geometry depends
on is named in :class:`BoardSpec`, so the same JSON can drive both the model and
the checks that verify it.

Coordinate frame
----------------
The board's lower-left corner is ``(0, 0)`` and its bottom face is ``z=0``.
The board occupies ``z=0..thickness``. Standoffs rise to ``z=0``; the floor is
below them at ``z=-standoff-floor``. The tray rim is at
``z=thickness + top_max + lid_clearance``. The lid sits on the rim, so the case
and lid share one frame and can be assembled directly.

Quick start from this recipe's directory::

    agentcad run example.py --label pcb_v1

Copy this directory into your project and adapt the code and dimensions.
The helpers and JSON format belong to this recipe; see the sibling README.md.
Based on Santiago Ariza's contribution in agentcad PR #271.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path

_MISSING = object()

# ISO metric machine-screw dimensions: (head clearance hole, tapping pilot).
_SCREWS = {
    "M2": (2.4, 1.6),
    "M2.5": (2.9, 2.05),
    "M3": (3.4, 2.5),
}


def _field(mapping, key, *aliases, default=_MISSING):
    for name in (key, *aliases):
        if name in mapping:
            return mapping[name]
    if default is _MISSING:
        raise ValueError(f"{key} is required")
    return default


def _number(value, name, *, minimum=0.0, strictly_positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if strictly_positive and result <= 0:
        raise ValueError(f"{name} must be greater than zero")
    if result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def _positive(value, name):
    return _number(value, name, minimum=0.0, strictly_positive=True)


def _mapping(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _list(value, name):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be an array")
    return value


def _box(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise ValueError(f"{name} must be [xmin, ymin, zmin, xmax, ymax, zmax]")
    # Box coordinates are signed: the enclosure floor sits below z=0 and a
    # keepout may extend outside the board outline to protect a connector.
    box = tuple(
        _number(v, f"{name}[{i}]", minimum=-math.inf) for i, v in enumerate(value)
    )
    if any(box[i + 3] <= box[i] for i in range(3)):
        raise ValueError(f"{name} maximum values must be greater than minimums")
    return box


def _screw(name):
    if name not in _SCREWS:
        choices = ", ".join(sorted(_SCREWS))
        raise ValueError(f"case.screw must be one of {choices}")
    return _SCREWS[name]


def _normalize(data):
    data = _mapping(data, "board spec")
    unknown = set(data) - {
        "name",
        "board",
        "mounting_holes",
        "cutouts",
        "lid_windows",
        "keepouts",
        "heights",
        "case",
    }
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"unknown top-level board spec keys: {names}")

    board = _mapping(data.get("board"), "board")
    board_width = _positive(_field(board, "width_mm", "width"), "board.width_mm")
    board_height = _positive(_field(board, "height_mm", "height"), "board.height_mm")
    thickness = _positive(
        _field(board, "thickness_mm", "thickness", default=1.6),
        "board.thickness_mm",
    )
    corner_radius = _number(
        _field(board, "corner_radius_mm", "corner_radius", default=1.0),
        "board.corner_radius_mm",
    )
    if corner_radius > min(board_width, board_height) / 2:
        raise ValueError("board.corner_radius_mm cannot exceed half the board's smaller side")

    heights = _mapping(data.get("heights") or {}, "heights")
    standoff = _positive(
        _field(heights, "standoff_mm", "standoff", default=3.0),
        "heights.standoff_mm",
    )
    bottom_max = _number(
        _field(heights, "bottom_max_mm", "bottom_max", default=1.5),
        "heights.bottom_max_mm",
    )
    top_max = _number(
        _field(heights, "top_max_mm", "top_max", default=3.0),
        "heights.top_max_mm",
    )
    lid_clearance = _number(
        _field(heights, "lid_clearance_mm", "lid_clearance", default=0.5),
        "heights.lid_clearance_mm",
    )
    if standoff < bottom_max:
        raise ValueError(
            "heights.standoff_mm must be >= heights.bottom_max_mm so "
            "bottom-side components clear the floor"
        )

    case = _mapping(data.get("case") or {}, "case")
    wall = _positive(
        _field(case, "wall_mm", "wall", default=2.0), "case.wall_mm"
    )
    floor = _positive(
        _field(case, "floor_mm", "floor", default=1.6), "case.floor_mm"
    )
    clearance = _number(
        _field(case, "clearance_mm", "clearance", default=0.3),
        "case.clearance_mm",
    )
    screw = _field(case, "screw", default="M2.5")
    screw_clearance, screw_pilot = _screw(screw)
    screw_clearance = _positive(
        _field(case, "screw_clearance_mm", "screw_clearance", default=screw_clearance),
        "case.screw_clearance_mm",
    )
    screw_pilot = _positive(
        _field(case, "screw_pilot_mm", "screw_pilot", default=screw_pilot),
        "case.screw_pilot_mm",
    )
    boss_wall = _number(
        _field(case, "boss_wall_mm", "boss_wall", default=1.5),
        "case.boss_wall_mm",
    )
    lid = _field(case, "lid", default="screw")
    if lid not in {"screw", "none"}:
        raise ValueError("case.lid must be 'screw' or 'none'")
    lid_thickness = _positive(
        _field(case, "lid_thickness_mm", "lid_thickness", default=2.0),
        "case.lid_thickness_mm",
    )
    lid_lip = _number(
        _field(case, "lid_lip_mm", "lid_lip", default=1.2),
        "case.lid_lip_mm",
    )
    lid_lip_thickness = _positive(
        _field(case, "lid_lip_thickness_mm", "lid_lip_thickness", default=1.2),
        "case.lid_lip_thickness_mm",
    )
    lid_lip_clearance = _number(
        _field(case, "lid_lip_clearance_mm", "lid_lip_clearance", default=0.2),
        "case.lid_lip_clearance_mm",
    )
    space_above_board = top_max + lid_clearance
    if lid_lip > space_above_board:
        raise ValueError(
            "case.lid_lip_mm cannot exceed heights.top_max_mm + "
            "heights.lid_clearance_mm"
        )
    if lid_lip_thickness * 2 >= min(board_width, board_height) + 2 * clearance:
        raise ValueError("case.lid_lip_thickness_mm is too large for the cavity")

    holes = []
    for index, raw in enumerate(_list(data.get("mounting_holes"), "mounting_holes")):
        raw = _mapping(raw, f"mounting_holes[{index}]")
        x = _number(_field(raw, "x_mm", "x"), f"mounting_holes[{index}].x_mm")
        y = _number(_field(raw, "y_mm", "y"), f"mounting_holes[{index}].y_mm")
        diameter = _positive(
            _field(raw, "diameter_mm", "diameter"),
            f"mounting_holes[{index}].diameter_mm",
        )
        if not (0 <= x <= board_width and 0 <= y <= board_height):
            raise ValueError(f"mounting_holes[{index}] center must be inside the board outline")
        boss_diameter = _positive(
            _field(
                raw,
                "boss_diameter_mm",
                "boss_diameter",
                default=diameter + 2 * boss_wall,
            ),
            f"mounting_holes[{index}].boss_diameter_mm",
        )
        pilot_diameter = _positive(
            _field(raw, "pilot_diameter_mm", "pilot_diameter", default=screw_pilot),
            f"mounting_holes[{index}].pilot_diameter_mm",
        )
        if boss_diameter <= pilot_diameter:
            raise ValueError(
                f"mounting_holes[{index}].boss_diameter_mm must exceed its pilot diameter"
            )
        holes.append(
            {
                "x_mm": x,
                "y_mm": y,
                "diameter_mm": diameter,
                "boss_diameter_mm": boss_diameter,
                "pilot_diameter_mm": pilot_diameter,
            }
        )

    cutouts = []
    for index, raw in enumerate(_list(data.get("cutouts"), "cutouts")):
        raw = _mapping(raw, f"cutouts[{index}]")
        name = _field(raw, "name", default=f"cutout_{index + 1}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"cutouts[{index}].name must be a non-empty string")
        edge = _field(raw, "edge")
        if edge not in {"bottom", "top", "left", "right"}:
            raise ValueError(
                f"cutouts[{index}].edge must be bottom, top, left, or right"
            )
        center = _number(
            _field(raw, "center_mm", "center"),
            f"cutouts[{index}].center_mm",
        )
        opening_width = _positive(
            _field(raw, "width_mm", "width"), f"cutouts[{index}].width_mm"
        )
        z_min = _number(
            _field(raw, "z_min_mm", "z_min", default=0.0),
            f"cutouts[{index}].z_min_mm",
            minimum=-math.inf,
        )
        if "z_max_mm" in raw or "z_max" in raw:
            z_max = _number(
                _field(raw, "z_max_mm", "z_max"),
                f"cutouts[{index}].z_max_mm",
                minimum=-math.inf,
            )
        else:
            opening_height = _field(raw, "height_mm", "height", default=None)
            if opening_height is None:
                raise ValueError(
                    f"cutouts[{index}] needs height_mm or z_max_mm"
                )
            z_max = z_min + _positive(
                opening_height, f"cutouts[{index}].height_mm"
            )
        if z_max <= z_min:
            raise ValueError(f"cutouts[{index}].z_max_mm must exceed z_min_mm")
        along = board_width if edge in {"bottom", "top"} else board_height
        if center - opening_width / 2 < 0 or center + opening_width / 2 > along:
            raise ValueError(
                f"cutouts[{index}] opening must stay inside the board edge"
            )
        if z_max > thickness + space_above_board:
            raise ValueError(
                f"cutouts[{index}].z_max_mm exceeds the cavity above the board"
            )
        cutouts.append(
            {
                "name": name.strip(),
                "edge": edge,
                "center_mm": center,
                "width_mm": opening_width,
                "z_min_mm": z_min,
                "z_max_mm": z_max,
            }
        )

    lid_windows = []
    for index, raw in enumerate(_list(data.get("lid_windows"), "lid_windows")):
        raw = _mapping(raw, f"lid_windows[{index}]")
        name = _field(raw, "name", default=f"window_{index + 1}")
        x = _number(_field(raw, "x_mm", "x"), f"lid_windows[{index}].x_mm")
        y = _number(_field(raw, "y_mm", "y"), f"lid_windows[{index}].y_mm")
        window_width = _positive(
            _field(raw, "width_mm", "width"), f"lid_windows[{index}].width_mm"
        )
        window_height = _positive(
            _field(raw, "height_mm", "height"), f"lid_windows[{index}].height_mm"
        )
        if (
            x < 0
            or y < 0
            or x + window_width > board_width
            or y + window_height > board_height
        ):
            raise ValueError(f"lid_windows[{index}] must stay inside the board outline")
        lid_windows.append(
            {
                "name": str(name),
                "x_mm": x,
                "y_mm": y,
                "width_mm": window_width,
                "height_mm": window_height,
            }
        )

    keepouts = []
    for index, raw in enumerate(_list(data.get("keepouts"), "keepouts")):
        raw = _mapping(raw, f"keepouts[{index}]")
        name = _field(raw, "name", default=f"keepout_{index + 1}")
        if "box_mm" in raw or "box" in raw:
            box = _box(_field(raw, "box_mm", "box"), f"keepouts[{index}].box_mm")
        else:
            x = _number(_field(raw, "x_mm", "x"), f"keepouts[{index}].x_mm")
            y = _number(_field(raw, "y_mm", "y"), f"keepouts[{index}].y_mm")
            box_width = _positive(
                _field(raw, "width_mm", "width"), f"keepouts[{index}].width_mm"
            )
            box_height = _positive(
                _field(raw, "height_mm", "height"), f"keepouts[{index}].height_mm"
            )
            z_min = _number(
                _field(raw, "z_min_mm", "z_min", default=0.0),
                f"keepouts[{index}].z_min_mm",
                minimum=-math.inf,
            )
            z_max = _number(
                _field(raw, "z_max_mm", "z_max", default=z_min + box_height),
                f"keepouts[{index}].z_max_mm",
                minimum=-math.inf,
            )
            box = (x, y, z_min, x + box_width, y + box_height, z_max)
            box = _box(box, f"keepouts[{index}]")
        keepouts.append({"name": str(name), "box_mm": box})

    return {
        "name": data.get("name"),
        "board": {
            "width_mm": board_width,
            "height_mm": board_height,
            "thickness_mm": thickness,
            "corner_radius_mm": corner_radius,
        },
        "mounting_holes": holes,
        "cutouts": cutouts,
        "lid_windows": lid_windows,
        "keepouts": keepouts,
        "heights": {
            "standoff_mm": standoff,
            "bottom_max_mm": bottom_max,
            "top_max_mm": top_max,
            "lid_clearance_mm": lid_clearance,
        },
        "case": {
            "wall_mm": wall,
            "floor_mm": floor,
            "clearance_mm": clearance,
            "screw": screw,
            "screw_clearance_mm": screw_clearance,
            "screw_pilot_mm": screw_pilot,
            "boss_wall_mm": boss_wall,
            "lid": lid,
            "lid_thickness_mm": lid_thickness,
            "lid_lip_mm": lid_lip,
            "lid_lip_thickness_mm": lid_lip_thickness,
            "lid_lip_clearance_mm": lid_lip_clearance,
        },
    }


class BoardSpec:
    """Validated, normalized board description used by the enclosure helpers."""

    def __init__(self, data):
        self.data = _normalize(data)

    @classmethod
    def from_dict(cls, data):
        return cls(data)

    @classmethod
    def from_json(cls, path):
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(f"board spec not found: {path}") from None
        except json.JSONDecodeError as exc:
            raise ValueError(f"board spec is not valid JSON: {exc.msg}") from None
        return cls(data)

    def to_dict(self):
        return deepcopy(self.data)

    @property
    def board(self):
        return self.data["board"]

    @property
    def mounting_holes(self):
        return self.data["mounting_holes"]

    @property
    def cutouts(self):
        return self.data["cutouts"]

    @property
    def lid_windows(self):
        return self.data["lid_windows"]

    @property
    def keepouts(self):
        return self.data["keepouts"]

    @property
    def heights(self):
        return self.data["heights"]

    @property
    def case(self):
        return self.data["case"]

    def __getitem__(self, key):
        return self.data[key]


def _as_spec(spec):
    if isinstance(spec, BoardSpec):
        return spec
    if isinstance(spec, (str, Path)):
        return BoardSpec.from_json(spec)
    return BoardSpec.from_dict(spec)


def _rounded_rect(width, height, radius):
    from build123d import Rectangle, RectangleRounded

    if radius <= 0:
        return Rectangle(width, height)
    return RectangleRounded(width, height, radius)


def _require_valid(shape, label):
    if shape is None or shape.is_null:
        raise ValueError(f"{label} produced a null shape")
    if not shape.is_valid:
        raise ValueError(f"{label} produced invalid geometry")
    if shape.volume <= 0:
        raise ValueError(f"{label} produced zero volume")
    return shape


def _geometry(spec):
    """Return the shared coordinate values for the case and lid."""
    board = spec.board
    heights = spec.heights
    case = spec.case
    width = board["width_mm"]
    height = board["height_mm"]
    clearance = case["clearance_mm"]
    wall = case["wall_mm"]
    floor = case["floor_mm"]
    standoff = heights["standoff_mm"]
    thickness = board["thickness_mm"]
    rim = thickness + heights["top_max_mm"] + heights["lid_clearance_mm"]
    center_x = width / 2.0
    center_y = height / 2.0
    return {
        "width": width,
        "height": height,
        "thickness": thickness,
        "clearance": clearance,
        "wall": wall,
        "floor": floor,
        "standoff": standoff,
        "rim": rim,
        "z_bottom": -standoff - floor,
        "center_x": center_x,
        "center_y": center_y,
        "inner_width": width + 2 * clearance,
        "inner_height": height + 2 * clearance,
        "outer_width": width + 2 * clearance + 2 * wall,
        "outer_height": height + 2 * clearance + 2 * wall,
        "inner_radius": board["corner_radius_mm"] + clearance,
        "outer_radius": board["corner_radius_mm"] + clearance + wall,
    }


def _cutout_box(cutout, geo):
    from build123d import Align, Box, Pos

    edge = cutout["edge"]
    width = cutout["width_mm"]
    center = cutout["center_mm"]
    z_min = cutout["z_min_mm"]
    z_max = cutout["z_max_mm"]
    reach = geo["wall"] + 2.0
    if edge in {"bottom", "top"}:
        if edge == "bottom":
            y = -geo["clearance"] - geo["wall"] / 2.0
        else:
            y = geo["height"] + geo["clearance"] + geo["wall"] / 2.0
        box = Box(width, reach, z_max - z_min, align=(Align.CENTER, Align.CENTER, Align.MIN))
        return Pos(center, y, z_min) * box
    if edge == "left":
        x = -geo["clearance"] - geo["wall"] / 2.0
    else:
        x = geo["width"] + geo["clearance"] + geo["wall"] / 2.0
    box = Box(reach, width, z_max - z_min, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return Pos(x, center, z_min) * box


def _keepout_box(keepout, geo):
    from build123d import Align, Box, Pos

    xmin, ymin, zmin, xmax, ymax, zmax = keepout["box_mm"]
    box = Box(
        xmax - xmin,
        ymax - ymin,
        zmax - zmin,
        align=(Align.CENTER, Align.CENTER, Align.MIN),
    )
    return Pos((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, zmin) * box


def pcb_case(spec):
    """Build the open tray for a :class:`BoardSpec` or spec dict."""
    from build123d import Align, Cylinder, Pos, extrude

    spec = _as_spec(spec)
    geo = _geometry(spec)
    heights = spec.heights
    case = spec.case

    outer = extrude(
        _rounded_rect(geo["outer_width"], geo["outer_height"], geo["outer_radius"]),
        amount=geo["rim"] - geo["z_bottom"],
    )
    outer = Pos(geo["center_x"], geo["center_y"], geo["z_bottom"]) * outer

    cavity = extrude(
        _rounded_rect(geo["inner_width"], geo["inner_height"], geo["inner_radius"]),
        amount=geo["rim"] - geo["z_bottom"] + 1.0,
    )
    cavity = Pos(geo["center_x"], geo["center_y"], -geo["standoff"]) * cavity
    tray = outer - cavity

    for hole in spec.mounting_holes:
        boss = Cylinder(
            hole["boss_diameter_mm"] / 2.0,
            heights["standoff_mm"],
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        tray = tray + Pos(hole["x_mm"], hole["y_mm"], -heights["standoff_mm"]) * boss

    for hole in spec.mounting_holes:
        pilot_depth = heights["standoff_mm"] + case["floor_mm"] * 0.8
        pilot = Cylinder(
            hole["pilot_diameter_mm"] / 2.0,
            pilot_depth + 0.5,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        tray = tray - Pos(hole["x_mm"], hole["y_mm"], -pilot_depth) * pilot

    for cutout in spec.cutouts:
        tray = tray - _cutout_box(cutout, geo)

    for keepout in spec.keepouts:
        tray = tray - _keepout_box(keepout, geo)

    return _require_valid(tray, "pcb_case")


def pcb_lid(spec):
    """Build the screw-on lid for a :class:`BoardSpec` or spec dict."""
    from build123d import Align, Cylinder, Pos, extrude

    spec = _as_spec(spec)
    geo = _geometry(spec)
    case = spec.case
    if case["lid"] == "none":
        raise ValueError("this board spec sets case.lid to 'none'")

    rim = geo["rim"]
    thickness = case["lid_thickness_mm"]
    lid = extrude(
        _rounded_rect(geo["outer_width"], geo["outer_height"], geo["outer_radius"]),
        amount=thickness,
    )
    lid = Pos(geo["center_x"], geo["center_y"], rim) * lid

    lip_depth = case["lid_lip_mm"]
    lip_clearance = case["lid_lip_clearance_mm"]
    lip_thickness = case["lid_lip_thickness_mm"]
    if lip_depth > 0 and lip_thickness > 0:
        lip_outer_width = geo["inner_width"] - 2 * lip_clearance
        lip_outer_height = geo["inner_height"] - 2 * lip_clearance
        lip_outer_radius = max(0.0, geo["inner_radius"] - lip_clearance)
        lip_inner_width = lip_outer_width - 2 * lip_thickness
        lip_inner_height = lip_outer_height - 2 * lip_thickness
        lip_inner_radius = max(0.0, lip_outer_radius - lip_thickness)
        if min(lip_inner_width, lip_inner_height) <= 0:
            raise ValueError("case.lid_lip_thickness_mm is too large for the cavity")
        ring_outer = extrude(
            _rounded_rect(lip_outer_width, lip_outer_height, lip_outer_radius),
            amount=lip_depth + 0.2,
        )
        ring_outer = Pos(geo["center_x"], geo["center_y"], rim - lip_depth) * ring_outer
        ring_inner = extrude(
            _rounded_rect(lip_inner_width, lip_inner_height, lip_inner_radius),
            amount=lip_depth + 1.2,
        )
        ring_inner = Pos(
            geo["center_x"], geo["center_y"], rim - lip_depth - 0.5
        ) * ring_inner
        lid = lid + (ring_outer - ring_inner)

    for hole in spec.mounting_holes:
        screw = Cylinder(
            case["screw_clearance_mm"] / 2.0,
            lip_depth + thickness + 2.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        lid = lid - Pos(hole["x_mm"], hole["y_mm"], rim - lip_depth - 1.0) * screw

    for window in spec.lid_windows:
        from build123d import Box

        opening = Box(
            window["width_mm"],
            window["height_mm"],
            lip_depth + thickness + 3.0,
            align=(Align.CENTER, Align.CENTER, Align.MIN),
        )
        lid = lid - Pos(
            window["x_mm"] + window["width_mm"] / 2.0,
            window["y_mm"] + window["height_mm"] / 2.0,
            rim - lip_depth - 1.0,
        ) * opening

    for keepout in spec.keepouts:
        lid = lid - _keepout_box(keepout, geo)

    return _require_valid(lid, "pcb_lid")


def pcb_enclosure(spec):
    """Build the open enclosure tray (alias for :func:`pcb_case`)."""
    return pcb_case(spec)


def pcb_enclosure_lid(spec):
    """Build the enclosure lid (alias for :func:`pcb_lid`)."""
    return pcb_lid(spec)


__all__ = [
    "BoardSpec",
    "pcb_case",
    "pcb_lid",
    "pcb_enclosure",
    "pcb_enclosure_lid",
]
