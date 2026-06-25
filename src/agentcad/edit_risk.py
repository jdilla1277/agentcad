"""Edit-risk classification for imported B-rep inputs.

When an agent inspects or measures an imported STEP/BREP that it intends to
edit, raw topology numbers (face/edge counts, validity, free edges) don't tell
it *how* to approach the edit. A large, invalid part is a trap: normal
``load_step()`` + boolean/fillet edits commonly fail or time out on it, and an
agent that doesn't know this burns turns rediscovering it (see CADGenBench
sample 202 — 2157 faces, 5239 edges, 526 free edges, self-intersecting +
unorientable, no final output).

This module turns those numbers into an ``edit_risk`` level plus actionable,
command-oriented workflow guidance, so the warning arrives before the agent
commits to a fragile strategy.
"""

# Thresholds chosen so ordinary mechanical parts (tens to low-hundreds of
# faces) never trip, while CADGenBench-202-class inputs (thousands of faces,
# hundreds of free edges) classify as large/complex.
LARGE_FACE_COUNT = 1000
LARGE_EDGE_COUNT = 2000
MANY_FREE_EDGES = 100

# Validity errors whose presence makes boolean/fillet editing of the imported
# solid especially unreliable — the classes worth naming in the guidance.
SEVERE_VALIDITY_ERRORS = frozenset({
    "BRepCheck_SelfIntersectingWire",
    "BRepCheck_UnorientableShape",
    "BRepCheck_BadOrientation",
    "BRepCheck_BadOrientationOfSubshape",
    "BRepCheck_InvalidCurveOnSurface",
})


def classify_edit_risk(
    *,
    face_count,
    edge_count,
    is_valid,
    free_edge_count=0,
    validity_errors=None,
):
    """Classify how risky it is to edit an imported B-rep via the normal
    ``load_step()`` + boolean/fillet path.

    Returns a dict with ``edit_risk`` ("high"/"medium"), ``risk_reasons``,
    and ``recommended_workflow`` — or ``None`` for low-risk inputs (valid,
    modestly sized, well-formed), where there is nothing worth warning about.

    ``free_edge_count`` is optional: ``measure`` doesn't compute it (the
    full face/edge cross-reference is expensive), so it defaults to 0 there.
    """
    validity_errors = list(validity_errors or [])
    invalid = not is_valid
    large = face_count >= LARGE_FACE_COUNT or edge_count >= LARGE_EDGE_COUNT
    many_free = free_edge_count >= MANY_FREE_EDGES
    severe = [e for e in validity_errors if e in SEVERE_VALIDITY_ERRORS]

    reasons = []
    if invalid:
        if severe:
            reasons.extend(f"invalid topology: {e}" for e in severe)
        elif validity_errors:
            reasons.append("invalid topology: " + ", ".join(validity_errors))
        else:
            reasons.append("invalid topology")
    if large:
        reasons.append(
            f"large topology: {face_count} faces / {edge_count} edges"
        )
    if many_free:
        reasons.append(f"many free edges: {free_edge_count}")

    if not reasons:
        return None

    # Invalid topology combined with size/complexity is the timeout-prone case
    # (CADGenBench 202) -> high. Invalid-but-small or large-but-valid inputs
    # are recoverable -> medium. Distinguishing the two is what lets the
    # guidance tell an agent when *not* to start with load_step() + boolean.
    if invalid and (large or many_free or severe):
        level = "high"
    else:
        level = "medium"

    return {
        "edit_risk": level,
        "risk_reasons": reasons,
        "recommended_workflow": _recommended_workflow(
            level, invalid=invalid, large=large, many_free=many_free
        ),
    }


def _recommended_workflow(level, *, invalid, large, many_free):
    steps = []
    if level == "high":
        steps.append(
            "Do not start with load_step() + boolean/fillet edits — on "
            "invalid, high-complexity topology these commonly fail or time "
            "out. Treat in-place editing as a last resort, not the first path."
        )
        steps.append(
            "Prefer additive/proxy strategies: rebuild only the geometry you "
            "need parametrically, or export the raw compound and add to it, "
            "rather than mutating the imported solid."
        )
        if large:
            steps.append(
                "Skip whole-part `inspect --ids` and `measure --features` — "
                "the per-feature payload is huge here. Use targeted `measure` "
                "queries for the specific dimension you need."
            )
        if invalid:
            steps.append(
                "If you must edit in place, run a heal/sew pass first and "
                "re-run `inspect` to confirm is_valid before any boolean op."
            )
    elif invalid and not large:
        # Invalid but small: recoverable. Heal, then edit.
        steps.append(
            "Topology is invalid but small — attempt a heal/sew (or rebuild "
            "the affected feature), then re-run `inspect` to confirm is_valid "
            "before boolean edits."
        )
        steps.append(
            "Boolean/fillet edits may still work after healing; re-run "
            "`inspect` after each step to catch regressions early."
        )
    else:
        # Large (or many free edges) but valid: editable, just slow/heavy.
        steps.append(
            "Topology is valid but heavy — edits work, but each boolean/"
            "fillet op is slow. Avoid whole-part `inspect --ids` / "
            "`measure --features` (large payloads); use targeted queries."
        )
        if many_free:
            steps.append(
                "Many free edges suggest open shells — confirm the feature "
                "you edit belongs to a closed solid before relying on "
                "boolean ops."
            )
    return steps
