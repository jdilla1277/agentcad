"""Strict, transport-independent validation and normalization of exact queries."""

import json
import math
import re

KINDS = {"part", "occurrence", "solid", "face", "edge"}
SURFACES = {"plane", "cylinder", "cone", "sphere", "torus", "bezier", "bspline", "revolution", "extrusion", "offset", "other"}
CURVES = {"line", "circle", "ellipse", "hyperbola", "parabola", "bezier", "bspline", "offset", "other", "degenerate"}
NUMERIC = {"area": 2, "volume": 3, "length": 1, "radius": 1, "diameter": 1}
COMMON = {"id", "part_id", "occurrence_id", "dimensions", "bounds", "location", "plane", "relation"}
ALLOWED = {
    "face": COMMON | {"surface_type", "area", "radius", "diameter", "orientation", "axis"},
    "edge": COMMON | {"curve_type", "length", "radius", "diameter", "orientation", "axis"},
    "solid": COMMON | {"volume"},
    "part": COMMON | {"volume", "placement"},
    "occurrence": COMMON | {"volume", "placement"},
}
ID = re.compile(r"(part|occurrence|solid|face|edge)_v[1-9][0-9]*_[1-9][0-9]*\Z")


class InvalidQuery(ValueError):
    reason = "invalid_geometry_query"


def _object(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise InvalidQuery(f"Expected an object with {', '.join(sorted(allowed))}; required: {', '.join(sorted(required)) or 'none'}.")


def number(value, *, minimum=None):
    if type(value) not in (int, float) or not math.isfinite(value) or (minimum is not None and value < minimum):
        raise InvalidQuery("Use finite numeric values with nonnegative tolerances and measurements.")
    return value


def vector(value, *, direction=False):
    if not isinstance(value, list) or len(value) != 3:
        raise InvalidQuery("Use a three-number [x, y, z] vector.")
    result = [number(v) for v in value]
    if direction:
        magnitude = max(abs(v) for v in result)
        if magnitude == 0:
            raise InvalidQuery("A direction or plane normal must be nonzero.")
        reduced = [v / magnitude for v in result]
        norm = math.hypot(*reduced)
        result = [v / norm for v in reduced]
    return result


def scale(units, power=1):
    suffix = "" if power == 1 else str(power)
    factors = {name + suffix: factor ** power for name, factor in (("mm", 1), ("cm", 10), ("m", 1000), ("in", 25.4))}
    if not isinstance(units, str) or units not in factors:
        raise InvalidQuery(f"Use explicit units: {', '.join(factors)}.")
    return factors[units]


def scaled(value, factor):
    return number(value * factor)


def measurement(value, power=1):
    _object(value, {"min", "max", "value", "tolerance", "units"}, {"units"})
    if not ({"min", "max", "value"} & set(value)) or ("value" in value and {"min", "max"} & set(value)):
        raise InvalidQuery("A measurement needs min/max or value, with explicit units and optional tolerance.")
    factor = scale(value["units"], power)
    result = {key: number(number(item, minimum=0) * factor, minimum=0) for key, item in value.items() if key != "units"}
    result.setdefault("tolerance", 0)
    if result.get("min", 0) > result.get("max", math.inf):
        raise InvalidQuery("Measurement min must be <= max.")
    result["units"] = "mm" + (str(power) if power != 1 else "")
    return result


def identifiers(value, kind=None):
    values = value if isinstance(value, list) else [value]
    if not values or len(values) > 1000:
        raise InvalidQuery("ID filters need 1..1000 typed IDs.")
    for item in values:
        match = ID.fullmatch(item) if isinstance(item, str) else None
        if match is None or kind and match[1] != kind:
            raise InvalidQuery(f"Use typed {kind or 'geometry'} IDs returned by this baseline.")
    return sorted(set(values))


def _direction(value, *, axis=False):
    fields = {"direction", "angle_tolerance_deg"} | ({"origin", "units", "tolerance"} if axis else {"sense"})
    _object(value, fields, {"direction"} | ({"origin", "units", "tolerance"} if axis else set()))
    result = {"direction": vector(value["direction"], direction=True),
              "angle_tolerance_deg": number(value.get("angle_tolerance_deg", 1e-6), minimum=0)}
    if result["angle_tolerance_deg"] > 90:
        raise InvalidQuery("angle_tolerance_deg must be <=90.")
    if axis:
        factor = scale(value["units"])
        result.update(origin=[scaled(v, factor) for v in vector(value["origin"])],
                      units="mm", tolerance=scaled(number(value["tolerance"], minimum=0), factor))
    else:
        if value.get("sense", "either") not in {"same", "either"}:
            raise InvalidQuery("orientation.sense must be same or either.")
        result["sense"] = value.get("sense", "either")
    return result


def _spatial(value, field):
    if field == "bounds":
        _object(value, {"min", "max", "relation", "units", "tolerance"}, {"min", "max", "relation", "units"})
        if value["relation"] not in {"contained", "intersects"}:
            raise InvalidQuery("bounds.relation must be contained or intersects (bounding boxes).")
        factor = scale(value["units"])
        low, high = vector(value["min"]), vector(value["max"])
        if any(a > b for a, b in zip(low, high)):
            raise InvalidQuery("bounds.min must be <= bounds.max on every axis.")
        return {"min": [scaled(v, factor) for v in low], "max": [scaled(v, factor) for v in high],
                "relation": value["relation"], "units": "mm", "tolerance": scaled(number(value.get("tolerance", 0), minimum=0), factor)}
    if field in {"location", "placement"}:
        _object(value, {"origin", "units", "tolerance", "rotation"}, {"origin", "units", "tolerance"})
        if field == "location" and "rotation" in value:
            raise InvalidQuery("rotation is supported only on placement.")
        factor = scale(value["units"])
        result = {"origin": [scaled(v, factor) for v in vector(value["origin"])], "units": "mm",
                  "tolerance": scaled(number(value["tolerance"], minimum=0), factor)}
        if "rotation" in value:
            matrix = value["rotation"]
            if not isinstance(matrix, list) or len(matrix) != 3:
                raise InvalidQuery("placement.rotation needs a 3x3 matrix.")
            result["rotation"] = [vector(row) for row in matrix]
        return result
    _object(value, {"origin", "normal", "units", "tolerance", "relation"}, {"origin", "normal", "units", "tolerance", "relation"})
    if value["relation"] not in {"coplanar", "intersecting"}:
        raise InvalidQuery("plane.relation must explicitly be coplanar or intersecting.")
    factor = scale(value["units"])
    return {"origin": [scaled(v, factor) for v in vector(value["origin"])], "normal": vector(value["normal"], direction=True),
            "units": "mm", "tolerance": scaled(number(value["tolerance"], minimum=0), factor), "relation": value["relation"]}


def _where(value, kind, depth=0, counter=None):
    counter = [0] if counter is None else counter
    counter[0] += 1
    if depth > 12 or counter[0] > 100:
        raise InvalidQuery("Query exceeds 12 nesting levels or 100 filter nodes.")
    _object(value, ALLOWED[kind] | {"all", "any", "not"})
    result = {}
    for field, item in value.items():
        if field in {"all", "any"}:
            if not isinstance(item, list) or not item:
                raise InvalidQuery(f"{field} needs a nonempty list of filter objects.")
            result[field] = [_where(v, kind, depth + 1, counter) for v in item]
        elif field == "not":
            result[field] = _where(item, kind, depth + 1, counter)
        elif field in {"id", "part_id", "occurrence_id"}:
            result[field] = identifiers(item, {"part_id": "part", "occurrence_id": "occurrence"}.get(field))
        elif field in NUMERIC:
            result[field] = measurement(item, NUMERIC[field])
        elif field == "dimensions":
            _object(item, {"x", "y", "z"})
            if not item:
                raise InvalidQuery("dimensions needs at least one x/y/z measurement.")
            result[field] = {a: measurement(v) for a, v in item.items()}
        elif field in {"surface_type", "curve_type"}:
            choices = SURFACES if field == "surface_type" else CURVES
            values = item if isinstance(item, list) else [item]
            if not values or any(not isinstance(v, str) or v not in choices for v in values):
                raise InvalidQuery(f"{field} supports: {', '.join(sorted(choices))}.")
            result[field] = sorted(set(values))
        elif field in {"orientation", "axis"}:
            result[field] = _direction(item, axis=field == "axis")
        elif field == "relation":
            _object(item, {"type", "to", "distance", "tolerance", "units", "angle_tolerance_deg"}, {"type", "to"})
            if item["type"] not in {"adjacent", "shared_axis", "coaxial", "distance"}:
                raise InvalidQuery("relation.type must be adjacent, shared_axis, coaxial or distance.")
            identifiers(item["to"])
            if not isinstance(item["to"], str):
                raise InvalidQuery("relation.to needs one typed ID.")
            required = {"distance"} if item["type"] == "distance" else {"units", "tolerance"} if item["type"] in {"shared_axis", "coaxial"} else set()
            permitted = {"type", "to"} | required | ({"angle_tolerance_deg"} if "units" in required else set())
            _object(item, permitted, {"type", "to"} | required)
            result[field] = {"type": item["type"], "to": item["to"]}
            if "distance" in item:
                result[field]["distance"] = measurement(item["distance"])
            if "units" in required:
                factor = scale(item["units"])
                result[field].update(units="mm", tolerance=scaled(number(item["tolerance"], minimum=0), factor),
                                     angle_tolerance_deg=number(item.get("angle_tolerance_deg", 1e-6), minimum=0))
                if result[field]["angle_tolerance_deg"] > 90:
                    raise InvalidQuery("angle_tolerance_deg must be <=90.")
        else:
            result[field] = _spatial(item, field)
    return result


def parse_query(query):
    try:
        if isinstance(query, str):
            if len(query.encode("utf-8")) > 65536:
                raise InvalidQuery("Query JSON must be <=64 KiB.")
            def unique(pairs):
                obj = {}
                for key, value in pairs:
                    if key in obj:
                        raise InvalidQuery(f"Duplicate query field: {key}.")
                    obj[key] = value
                return obj
            query = json.loads(query, object_pairs_hook=unique)
        elif len(json.dumps(query).encode("utf-8")) > 65536:
            raise InvalidQuery("Query JSON must be <=64 KiB.")
        _object(query, {"kind", "where", "result_mode"}, {"kind"})
        if not isinstance(query["kind"], str) or query["kind"] not in KINDS:
            raise InvalidQuery(f"kind must be one of: {', '.join(sorted(KINDS))}.")
        mode = query.get("result_mode", "occurrences")
        if mode not in {"occurrences", "definitions"} or mode == "definitions" and query["kind"] not in {"part", "occurrence"}:
            raise InvalidQuery("result_mode is occurrences (default); definitions grouping is supported for part/occurrence queries.")
        return {"kind": query["kind"], "where": _where(query.get("where", {}), query["kind"]), "result_mode": mode}
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        if isinstance(exc, InvalidQuery):
            raise
        raise InvalidQuery(f"Invalid query JSON: {exc}") from exc


def referenced_ids(where):
    for field, value in where.items():
        if field in {"id", "part_id", "occurrence_id"}:
            yield from value
        elif field == "relation":
            yield value["to"]
        elif field in {"all", "any"}:
            for node in value:
                yield from referenced_ids(node)
        elif field == "not":
            yield from referenced_ids(value)


def in_range(value, spec):
    if value is None:
        return False
    tolerance = spec["tolerance"]
    if "value" in spec:
        return abs(value - spec["value"]) <= tolerance
    return value >= spec.get("min", -math.inf) - tolerance and value <= spec.get("max", math.inf) + tolerance
