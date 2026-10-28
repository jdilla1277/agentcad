"""Deterministic representative samples, without claiming feature recognition."""

import math


def dimensions(bbox):
    return {axis: round(bbox[axis][1] - bbox[axis][0], 6) for axis in ("x", "y", "z")}


def _signature(entry):
    if entry["kind"] == "part":
        return tuple(sorted(round(v, 3) for v in dimensions(entry["bbox"]).values()))
    if entry["kind"] == "face":
        return (round(entry["area"], 3), *sorted(round(v, 3) for v in dimensions(entry["bbox"]).values()))
    ends = entry["endpoints"]
    chord = math.sqrt(sum((ends[0][a] - ends[1][a]) ** 2 for a in ("x", "y", "z")))
    return (round(entry["length"], 3), round(chord, 3))


def _varied(entries, count, weight):
    if count <= 0:
        return []
    ranked = sorted(entries, key=lambda e: (-weight(e), int(e["id"].rsplit("_", 1)[1])))
    picked, seen = [], set()
    for entry in ranked:
        signature = _signature(entry)
        if signature not in seen:
            picked.append(entry)
            seen.add(signature)
        if len(picked) == count:
            return picked
    for entry in ranked:
        if entry not in picked:
            picked.append(entry)
        if len(picked) == count:
            break
    return picked


def select_samples(saved):
    entities = list(saved["entities"].values())
    parts = [e for e in entities if e["kind"] == "part" and e["definition_kind"] == "part"]
    faces = [e for e in entities if e["kind"] == "face"]
    # Collapsed edges (for example, sphere poles) have IDs but no visible curve.
    # Keep them in the inventory and explicit lookup, not representative media.
    edges = [e for e in entities if e["kind"] == "edge" and e["length"] > 0]
    repetitions = {e["id"]: len(saved["entities"][e["part_id"]]["occurrence_ids"])
                   for e in entities if e["kind"] == "occurrence"}

    def repeated(entry):
        return max((repetitions[value] for value in entry["occurrence_ids"]), default=1)

    def priority(entry, measure):
        return entry[measure] * (1 + math.log2(max(1, repeated(entry))))
    selected_parts = _varied(parts, 4, lambda e: abs(e["volume"]) * max(1, len(e["occurrence_ids"])))
    selected_faces = _varied(faces, 2 if edges else 4, lambda e: priority(e, "area"))
    selected_edges = _varied(edges, 4 - len(selected_faces), lambda e: priority(e, "length"))
    if len(selected_faces) + len(selected_edges) < 4:
        remaining = [e for e in faces if e not in selected_faces]
        selected_faces += _varied(remaining, 4 - len(selected_faces) - len(selected_edges), lambda e: priority(e, "area"))
    return {
        "parts": [{"id": e["id"], "reason": "prominent placed volume; preference for varied measured dimensions"} for e in selected_parts],
        "features": [{"id": e["id"], "part_repetitions": repeated(e), "reason": (
            "face area weighted by part repetition; varied measured sizes" if e["kind"] == "face"
            else "edge length weighted by part repetition; varied lengths and endpoint spans"
        )} for e in selected_faces + selected_edges],
    }
