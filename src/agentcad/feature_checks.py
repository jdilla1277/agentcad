"""Shared geometric feature probes for retained checks and ``check-spec``.

These helpers deliberately work on raw OCCT shapes and plain dictionaries so
the build123d and CadQuery runtimes can share one measurement vocabulary. A
probe box is always ``[xmin, ymin, zmin, xmax, ymax, zmax]`` in the assembly
frame and may include negative coordinates (models may place floors below
``z=0``).
"""

from __future__ import annotations

import math

AXIS_INDEX = {"x": 0, "+x": 0, "y": 1, "+y": 1, "z": 2, "+z": 2}

# Bound both the declared positions and the measured candidates so one-to-one
# matching stays predictable in time and recursion depth.
MAX_POSITIONED_CYLINDERS = 200


def _number(value, name, *, minimum=None):
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number.")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{name} must be a finite number.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}.")
    return float(value)


def validate_positions(value, name="positions_mm"):
    """Validate 2D cylinder positions used perpendicular to a declared axis."""
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name} must be a non-empty array of [a, b] positions.")
    if len(value) > MAX_POSITIONED_CYLINDERS:
        raise ValueError(
            f"{name} may declare at most {MAX_POSITIONED_CYLINDERS} positions."
        )
    positions = []
    for index, position in enumerate(value):
        if not isinstance(position, (list, tuple)) or len(position) != 2:
            raise ValueError(
                f"{name}[{index}] must be [a, b] in the plane perpendicular to the axis."
            )
        positions.append([
            _number(position[0], f"{name}[{index}][0]"),
            _number(position[1], f"{name}[{index}][1]"),
        ])
    if len({(a, b) for a, b in positions}) != len(positions):
        raise ValueError(
            f"{name} must not repeat a position; cylinders are matched one-to-one."
        )
    return positions


def normalize_box(value, name="box_mm"):
    """Validate and normalize a six-number material-probe box."""
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise ValueError(f"{name} must be [xmin, ymin, zmin, xmax, ymax, zmax].")
    box = tuple(_number(v, f"{name}[{i}]") for i, v in enumerate(value))
    if any(box[i + 3] <= box[i] for i in range(3)):
        raise ValueError(f"{name} maximum values must be greater than minimums.")
    return box


def box_key(box):
    """Return the exact normalized tuple for a probe box.

    Rounding here would let two distinct boxes share one measurement, so the
    lookup key preserves the full float values.
    """
    return tuple(float(v) for v in box)



def _shape(shape):
    return getattr(shape, "wrapped", shape)


def extract_cylinders(shape):
    """Return neutral cylinder rows with exact centers and face evidence.

    Split cylindrical faces on the same infinite axis are deduplicated by
    diameter, canonical direction, and perpendicular axis location. The
    center is the perpendicular projection of the axis location, so a Z-axis
    hole reports ``(x, y, 0)``.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS

    from agentcad.topo_ids import _iter_indexed_map

    shape = _shape(shape)
    cylinders = {}
    for index, raw in enumerate(_iter_indexed_map(shape, TopAbs_FACE), 1):
        face = TopoDS.Face_s(raw)
        try:
            adaptor = BRepAdaptor_Surface(face)
            if adaptor.GetType() == GeomAbs_Cylinder:
                cylinder = adaptor.Cylinder()
                axis = cylinder.Axis()
                direction = list(axis.Direction().Coord())
                if next((v for v in direction if abs(v) > 1e-12), 1) < 0:
                    direction = [-v for v in direction]
                point = list(axis.Location().Coord())
                projection = sum(p * d for p, d in zip(point, direction))
                center = [p - projection * d for p, d in zip(point, direction)]
                diameter = cylinder.Radius() * 2
                key = tuple(round(v, 9) for v in [diameter, *direction, *center])
                row = cylinders.setdefault(
                    key,
                    {
                        "diameter_mm": diameter,
                        "direction": direction,
                        "center_mm": center,
                        "face_indices": [],
                    },
                )
                row["face_indices"].append(index)
        except Exception:
            pass
    return list(cylinders.values())


def material_volumes(shape, boxes):
    """Measure material volume inside each probe box, deduplicated by box."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.GProp import GProp_GProps
    from OCP.gp import gp_Pnt

    shape = _shape(shape)
    result = {}
    for raw_box in boxes:
        box = normalize_box(raw_box)
        key = box_key(box)
        if key in result:
            continue
        xmin, ymin, zmin, xmax, ymax, zmax = box
        tool = BRepPrimAPI_MakeBox(
            gp_Pnt(xmin, ymin, zmin), gp_Pnt(xmax, ymax, zmax)
        ).Shape()
        common = BRepAlgoAPI_Common(shape, tool)
        common.Build()
        if not common.IsDone():
            raise ValueError(f"Material probe failed for box {list(box)}.")
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(common.Shape(), props)
        volume = props.Mass()
        if abs(volume) < 1e-12:
            volume = 0.0
        result[key] = {
            "box_mm": list(box),
            "volume_mm3": volume,
        }
    return list(result.values())


def volume_lookup(rows):
    return {box_key(row["box_mm"]): row for row in (rows or [])}


def axis_index(axis):
    try:
        return AXIS_INDEX[axis]
    except KeyError:
        raise ValueError("axis must be x, y, or z (optionally prefixed with '+').") from None


def planar_position(axis, center):
    """Return the two coordinates perpendicular to ``axis``.

    For Z: (x, y); for X: (y, z); for Y: (x, z).
    """
    index = axis_index(axis)
    values = [float(v) for v in center]
    if index == 0:
        return [values[1], values[2]]
    if index == 1:
        return [values[0], values[2]]
    return [values[0], values[1]]


def _matches(cylinders, *, axis, diameter_mm, diameter_tolerance_mm):
    index = axis_index(axis)
    candidates = []
    for cylinder in cylinders:
        direction = cylinder.get("direction") or [0, 0, 0]
        if abs(float(direction[index])) < 1 - 1e-10:
            continue
        if abs(float(cylinder["diameter_mm"]) - diameter_mm) > diameter_tolerance_mm + 1e-9:
            continue
        candidates.append(
            {
                "diameter_mm": float(cylinder["diameter_mm"]),
                "position_mm": planar_position(axis, cylinder["center_mm"]),
                "face_indices": list(cylinder.get("face_indices", [])),
            }
        )
    return candidates


def _assign(costs, tolerance):
    """Maximum bipartite matching for candidate distances within tolerance."""
    assigned = {}

    def assign(expected_index, seen):
        for candidate_index, distance in enumerate(costs[expected_index]):
            if distance > tolerance + 1e-9 or candidate_index in seen:
                continue
            seen.add(candidate_index)
            previous = assigned.get(candidate_index)
            if previous is None or assign(previous, seen):
                assigned[candidate_index] = expected_index
                return True
        return False

    for expected_index in range(len(costs)):
        assign(expected_index, set())
    return assigned


def check_positioned_cylinders(
    cylinders,
    *,
    axis,
    diameter_mm,
    diameter_tolerance_mm=0.1,
    positions_mm,
    position_tolerance_mm=0.1,
):
    """Compare measured cylinder axes with explicitly positioned cylinders.

    This proves that solid cylinders of the given diameter exist at each
    position. It does not establish a hole, its depth, or which side contains
    material.
    """
    positions = validate_positions(positions_mm)
    tolerance = float(position_tolerance_mm)
    candidates = _matches(
        cylinders,
        axis=axis,
        diameter_mm=float(diameter_mm),
        diameter_tolerance_mm=float(diameter_tolerance_mm),
    )
    if len(candidates) > MAX_POSITIONED_CYLINDERS:
        raise ValueError(
            f"Positioned-cylinder check matched more than "
            f"{MAX_POSITIONED_CYLINDERS} measured cylinders; narrow the "
            "diameter or axis."
        )
    costs = [
        [
            math.dist(position, candidate["position_mm"])
            for candidate in candidates
        ]
        for position in positions
    ]
    assigned = _assign(costs, tolerance)

    by_expected = {
        expected_index: candidate_index
        for candidate_index, expected_index in assigned.items()
    }
    nearest_errors = [min(row) if row else None for row in costs]
    matches = []
    missing = []
    matched_errors = []
    for expected_index, position in enumerate(positions):
        candidate_index = by_expected.get(expected_index)
        if candidate_index is None:
            missing.append(position)
            matches.append(
                {
                    "expected_mm": position,
                    "measured_mm": None,
                    "error_mm": None,
                    "nearest_error_mm": nearest_errors[expected_index],
                }
            )
            continue
        candidate = candidates[candidate_index]
        error = costs[expected_index][candidate_index]
        matched_errors.append(error)
        matches.append(
            {
                "expected_mm": position,
                "measured_mm": candidate["position_mm"],
                "error_mm": error,
                "nearest_error_mm": nearest_errors[expected_index],
                "diameter_mm": candidate["diameter_mm"],
                "face_indices": candidate["face_indices"],
            }
        )

    matched_candidates = set(assigned)
    extra = [
        candidate
        for candidate_index, candidate in enumerate(candidates)
        if candidate_index not in matched_candidates
    ]
    max_matched_error = max(matched_errors) if matched_errors else None
    position_errors = [
        match["error_mm"]
        if match["measured_mm"] is not None
        else match["nearest_error_mm"]
        for match in matches
    ]
    position_errors = [error for error in position_errors if error is not None]
    nearest_errors = [error for error in nearest_errors if error is not None]
    passed = (
        not missing
        and not extra
        and len(candidates) == len(positions)
        and max_matched_error is not None
        and max_matched_error <= tolerance + 1e-9
    )
    observed = {
        "expected_count": len(positions),
        "measured_count": len(candidates),
        "positions_mm": positions,
        "diameter_mm": float(diameter_mm),
        "diameter_tolerance_mm": float(diameter_tolerance_mm),
        "position_tolerance_mm": tolerance,
        "matches": matches,
        "missing_positions_mm": missing,
        "extra_cylinders": extra,
        "max_position_error_mm": (
            max(position_errors) if position_errors else None
        ),
        "max_nearest_position_error_mm": (
            max(nearest_errors) if nearest_errors else None
        ),
        "max_matched_position_error_mm": max_matched_error,
        "affected_face_indices": sorted(
            index
            for candidate in candidates
            for index in candidate["face_indices"]
        ),
        "units": "mm",
        "coordinate_frame": "assembly",
    }
    return passed, observed



def check_probe(rows, *, kind, box_mm, threshold_mm3):
    """Evaluate a keepout or material volume probe against measured evidence."""
    if kind not in ("keepout", "material"):
        raise ValueError("kind must be keepout or material.")
    box = normalize_box(box_mm)
    row = volume_lookup(rows).get(box_key(box))
    if row is None:
        raise ValueError(
            f"Material probe evidence is unavailable for box {list(box)}."
        )
    volume = float(row["volume_mm3"])
    threshold = float(threshold_mm3)
    passed = (
        volume <= threshold + 1e-9
        if kind == "keepout"
        else volume >= threshold - 1e-9
    )
    observed = {
        "kind": kind,
        "box_mm": list(box),
        "volume_mm3": volume,
        "units": "mm3",
        "coordinate_frame": "assembly",
    }
    if kind == "keepout":
        observed["max_volume_mm3"] = threshold
    else:
        observed["min_volume_mm3"] = threshold
    return passed, observed
