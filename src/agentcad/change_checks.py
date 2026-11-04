"""Versioned, explicit CAD requirements; no inferred design intent or identity matcher."""

from __future__ import annotations

import math
import re
from pathlib import Path

from agentcad import change_contract as contract

CORE_IDS = {"execution", "written_geometry"}


def _number(value, name, *, minimum=0):
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}.")


def compile_policy(declaration, *, previous=None, core=False, profile="deliverable"):
    """Compile readable declarations, inheriting or explicitly versioning a policy."""
    if declaration is None:
        if previous is not None:
            if core and previous["checks"][0]["settings"].get("validation_profile") != profile:
                raise ValueError(
                    "Changing the validation profile needs an explicit checks file with actor and rationale."
                )
            return contract.validate_document(previous, kind="target_policy")
        return None
    if not isinstance(declaration, dict) or set(declaration) - {
        "schema_version",
        "checks",
        "actor",
        "rationale",
    }:
        raise ValueError(
            "Checks must be an object with schema_version, checks, and optional actor/rationale."
        )
    if type(declaration.get("schema_version")) is not int or declaration["schema_version"] != 1:
        raise ValueError("Unsupported requirements schema_version; use 1.")
    rows = declaration.get("checks")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("checks must be an array of at most 100 declarations.")
    specs = []
    if core:
        specs = [
            {
                "check_id": name,
                "definition_version": 1,
                "settings": {"kind": name, "validation_profile": profile},
                "require_completion": True,
                "blocks_on_failure": True,
            }
            for name in ("execution", "written_geometry")
        ]
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each check must be an object.")
        check_id = row.get("check_id")
        if (
            not isinstance(check_id, str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,79}", check_id)
            or check_id in CORE_IDS
        ):
            raise ValueError(
                "Use a unique check_id (letters, digits, hyphens, underscores); core IDs are reserved."
            )
        kind = row.get("kind")
        fields = {
            "dimension": {"axis", "expected_mm", "tolerance_mm"},
            "cylinders": {"axis", "diameter_mm", "count", "tolerance_mm"},
            "preserve_cylinders": {"axis", "diameter_mm", "tolerance_mm", "baseline_revision_id"},
            "external": {"validator"},
        }.get(kind)
        if fields is None or set(row) - fields - {"check_id", "kind", "required"}:
            raise ValueError(
                "Unknown check kind or fields; use the documented requirements schema."
            )
        required = row.get("required", True)
        if type(required) is not bool:
            raise ValueError("required must be a boolean.")
        settings = {k: v for k, v in row.items() if k not in ("check_id", "required")}
        if kind != "external":
            if row.get("axis") not in ("x", "y", "z"):
                raise ValueError("axis must be x, y, or z in the assembly frame.")
            settings.setdefault("tolerance_mm", 0.1)
            _number(settings["tolerance_mm"], "tolerance_mm", minimum=1e-6)
            _number(
                row.get("expected_mm") if kind == "dimension" else row.get("diameter_mm"),
                "expected_mm" if kind == "dimension" else "diameter_mm",
                minimum=1e-6,
            )
        if kind == "cylinders" and (
            type(row.get("count")) is not int or not 0 <= row["count"] <= 200
        ):
            raise ValueError("count must be an integer between 0 and 200.")
        if kind == "preserve_cylinders" and not re.fullmatch(
            r"r_[0-9a-f]{32}", str(row.get("baseline_revision_id"))
        ):
            raise ValueError("baseline_revision_id must identify a retained geometry revision.")
        if kind == "external" and (
            not isinstance(row.get("validator"), str) or not row["validator"].strip()
        ):
            raise ValueError(
                "external checks need a validator name; unavailable adapters never pass."
            )
        specs.append(
            {
                "check_id": check_id,
                "definition_version": 1,
                "settings": settings,
                "require_completion": required,
                "blocks_on_failure": required,
            }
        )
    if len({r["check_id"] for r in specs}) != len(specs):
        raise ValueError("check_id values must be unique.")
    if previous and specs == previous["checks"]:
        return contract.validate_document(previous, kind="target_policy")
    if previous and (not declaration.get("actor") or not declaration.get("rationale")):
        raise ValueError(
            "A policy change requires actor and rationale; requirements cannot silently disappear."
        )
    return contract.make_policy(
        target_id="cad",
        policy_id="cad-requirements",
        version=previous["version"] + 1 if previous else 1,
        checks=specs,
        previous_policy=previous,
        actor=declaration.get("actor"),
        rationale=declaration.get("rationale"),
    )


def measure_geometry(path: str | Path) -> dict:
    """Measure the written STEP, retaining full precision and indexed face evidence."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS
    from agentcad.step_io import load_cad_shape
    from agentcad.topo_ids import _iter_indexed_map

    shape = load_cad_shape(path)
    bounds = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, bounds)
    values = bounds.Get()
    cylinders = {}
    for index, raw in enumerate(_iter_indexed_map(shape, TopAbs_FACE), 1):
        adaptor = BRepAdaptor_Surface(TopoDS.Face_s(raw))
        if adaptor.GetType() != GeomAbs_Cylinder:
            continue
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
    return {
        "dimensions": {a: values[i + 3] - values[i] for i, a in enumerate("xyz")},
        "cylinders": list(cylinders.values()),
    }


def _selected(measured, settings, *, diameter=True):
    axis = "xyz".index(settings["axis"])
    return [
        c
        for c in measured["cylinders"]
        if abs(c["direction"][axis]) >= 1 - 1e-10
        and (
            not diameter
            or abs(c["diameter_mm"] - settings["diameter_mm"]) <= settings["tolerance_mm"] + 1e-9
        )
    ]


def _displacement(before, after):
    """Find one assignment satisfying center and diameter tolerances together."""
    distances = [[math.dist(a["center_mm"], b["center_mm"]) for b in after] for a in before]
    diameters = [[abs(a["diameter_mm"] - b["diameter_mm"]) for b in after] for a in before]
    costs = [[max(d, r) for d, r in zip(ds, rs)] for ds, rs in zip(distances, diameters)]
    choices = sorted({v for row in costs for v in row})

    def fits(limit):
        assigned = {}

        def assign(i, seen):
            for j, distance in enumerate(costs[i]):
                if distance > limit or j in seen:
                    continue
                seen.add(j)
                if j not in assigned or assign(assigned[j], seen):
                    assigned[j] = i
                    return True
            return False

        return assigned if all(assign(i, set()) for i in range(len(before))) else None

    lo, hi = 0, len(choices) - 1
    while lo < hi:
        middle = (lo + hi) // 2
        if fits(choices[middle]) is not None:
            hi = middle
        else:
            lo = middle + 1
    assigned = fits(choices[lo])
    return (
        max(distances[i][j] for j, i in assigned.items()),
        max(diameters[i][j] for j, i in assigned.items()),
    )


def evaluate_checks(candidate, policy, measurements, *, baselines=None, reason=None):
    """Return all declared results; unavailable evidence is never a measured failure."""
    results = []
    for spec in policy["checks"]:
        if spec["check_id"] in CORE_IDS:
            continue
        settings = spec["settings"]
        state, observed = "unavailable", {"reason": reason or "Measurement evidence unavailable."}
        evidence = [
            {"artifact": a["path"], "sha256": a["sha256"]}
            for a in candidate["inputs"]
            if a["role"] != "source"
        ]
        if settings["kind"] == "external":
            observed = {
                "reason": reason or "Validator adapter is not installed.",
                "validator": settings["validator"],
            }
        elif measurements is not None:
            try:
                tolerance = settings["tolerance_mm"]
                if settings["kind"] == "dimension":
                    value = measurements["dimensions"][settings["axis"]]
                    _number(value, "measured dimension")
                    observed = {
                        "expected_mm": settings["expected_mm"],
                        "measured_mm": value,
                        "tolerance_mm": tolerance,
                        "units": "mm",
                        "coordinate_frame": "assembly",
                    }
                    state = (
                        "pass"
                        if abs(value - settings["expected_mm"]) <= tolerance + 1e-9
                        else "fail"
                    )
                else:
                    found = _selected(measurements, settings)
                    if len(found) > 200:
                        raise ValueError(
                            "Selected cylinder budget exceeded (200); narrow the requirement."
                        )
                    observed = {
                        "cylinders": _selected(measurements, settings, diameter=False),
                        "matched_count": len(found),
                        "tolerance_mm": tolerance,
                        "units": "mm",
                        "coordinate_frame": "assembly",
                        "affected_face_indices": sorted(
                            i for c in found for i in c["face_indices"]
                        ),
                    }
                    if settings["kind"] == "cylinders":
                        observed.update(
                            expected_diameter_mm=settings["diameter_mm"],
                            expected_count=settings["count"],
                        )
                        state = "pass" if len(found) == settings["count"] else "fail"
                    else:
                        baseline = (baselines or {}).get(spec["check_id"])
                        if baseline is None:
                            raise ValueError("Pinned preservation baseline is unavailable.")
                        before = _selected(baseline, settings)
                        if not before or len(before) > 200:
                            raise ValueError(
                                "Baseline must identify 1..200 cylinders; inspect the requirement."
                            )
                        displacement, diameter_change = (
                            _displacement(before, found)
                            if len(before) == len(found)
                            else (None, None)
                        )
                        observed.update(
                            baseline_revision_id=settings["baseline_revision_id"],
                            baseline_cylinders=before,
                            expected_displacement_mm=0,
                            max_displacement_mm=displacement,
                            max_diameter_change_mm=diameter_change,
                        )
                        state = (
                            "pass"
                            if displacement is not None
                            and max(displacement, diameter_change) <= tolerance + 1e-9
                            else "fail"
                        )
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                observed = {"reason": str(exc)}
                state = "unavailable"
        action = {
            "action": (
                "inspect" if state == "pass" else "repair" if state == "fail" else "supply_evidence"
            ),
            "check_id": spec["check_id"],
            "revision_id": candidate["revision_id"],
            "more_at": "agentcad docs check-spec",
        }
        results.append(
            contract.make_check_result(
                candidate,
                policy,
                spec["check_id"],
                state,
                observed=observed,
                evidence=evidence,
                actions=[action],
            )
        )
    return results
