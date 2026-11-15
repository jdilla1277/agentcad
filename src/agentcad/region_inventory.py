"""Saved sketch-region maps: the inventory file and its Inspect read-back.

``show_regions()`` writes ``vN_LABEL/regions/<name>.png`` and ``<name>.json``.
The JSON is the durable inventory of that one map. ``agentcad inspect`` reads
it back without running the source or creating a version: a bounded summary,
one region by ID, and the saved PNG.

Region IDs are map-local: ``region_v{version}_{map}_{index}`` names region
``index`` (the number drawn on the map) of map ``map`` saved in version
``version``. They are not persistent identities across edits; a changed
sketch is a new run with a new map.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from pathlib import Path

KIND = "agentcad.region_map"
SCHEMA_VERSION = 1
UNITS = "mm"
DEFAULT_LIMIT = 20

_ID = re.compile(r"^region_v(\d+)_(.+)_(\d+)$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class RegionMapError(ValueError):
    """A saved map or region ID cannot be used; ``reason`` is machine-readable."""

    def __init__(self, reason, message, suggestion=None):
        super().__init__(message)
        self.reason = reason
        self.suggestion = suggestion


def region_id(version, name, index):
    return f"region_v{version}_{name}_{index}"


def id_prefix(version, name):
    return f"region_v{version}_{name}_"


def build_listing(*, version, label, name, script, plane, dimensions, items,
                  png, png_sha256, window=None, hint=None, naming=None):
    """The saved inventory for one map. ``items`` gain their ``id`` here."""
    regions = [{"id": region_id(version, name, item["index"]), **item} for item in items]
    listing = {
        "kind": KIND,
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "label": label,
        "map": name,
        "script": script,
        "units": UNITS,
        "plane": plane,
        "dimensions": dimensions,
        "naming": naming or {"strategy": "points"},
        "count": len(regions),
        "png": png,
        "png_sha256": png_sha256,
        "regions": regions,
    }
    if window is not None:
        listing["window"] = window
    if hint:
        listing["hint"] = hint
    return listing


def looks_like_region_map(path):
    """True for a ``.json`` file under a ``regions/`` directory (cheap check)."""
    path = Path(path)
    return path.suffix.lower() == ".json" and path.parent.name == "regions"


def read_map(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegionMapError(
            "region_map_missing", f"Region map {str(path)!r} does not exist.",
            "Use the regions.<name>.json path from the run response.",
        ) from None
    except (OSError, ValueError) as exc:
        raise RegionMapError(
            "region_map_unreadable", f"Cannot read region map {str(path)!r}: {exc}",
            "Rerun the sketch script to write a fresh map.",
        ) from exc
    if not isinstance(data, dict) or data.get("kind") != KIND:
        raise RegionMapError(
            "not_a_region_map",
            f"{str(path)!r} is not a saved region map (kind {KIND!r}).",
            "Pass the regions.<name>.json path from a run that called show_regions().",
        )
    if data.get("schema_version") != SCHEMA_VERSION:
        raise RegionMapError(
            "region_map_schema",
            f"Region map schema_version {data.get('schema_version')!r} is not "
            f"supported (expected {SCHEMA_VERSION}).",
            "Rerun the sketch script with this agentcad to write a current map.",
        )
    return data


def resolve(listing, entity_id):
    """The record for ``entity_id`` in ``listing``, or a RegionMapError."""
    version, name = listing["version"], listing["map"]
    prefix = id_prefix(version, name)
    count = listing["count"]
    span = f"{prefix}0 .. {prefix}{count - 1}" if count else "none"
    match = _ID.match(entity_id or "")
    if match is None:
        raise RegionMapError(
            "invalid_region_id",
            f"{entity_id!r} is not a region ID. IDs look like {prefix}0; this "
            f"map has {span}.",
            "Copy an id from this map's regions list.",
        )
    if not entity_id.startswith(prefix):
        other_version, other_name = int(match.group(1)), match.group(2)
        where = []
        if other_version != version:
            where.append(f"version {other_version}, not {version}")
        if other_name != name:
            where.append(f"map {other_name!r}, not {name!r}")
        raise RegionMapError(
            "region_id_wrong_map",
            f"{entity_id} belongs to {' and '.join(where)}. This file is map "
            f"{name!r} of version {version}; its IDs are {span}.",
            "Inspect the region map JSON that the ID came from, or copy an id "
            "from this map. IDs are map-local, not stable across runs.",
        )
    index = int(match.group(3))
    if index >= count:
        raise RegionMapError(
            "region_id_out_of_range",
            f"{entity_id} is not on this map: it has {count} regions ({span}).",
            "Copy an id from this map's regions list.",
        )
    return listing["regions"][index]


def check_image(listing, map_path):
    """Path of the saved PNG after checking it is intact, or RegionMapError."""
    if not listing.get("png"):
        raise RegionMapError(
            "region_image_unavailable",
            "This map's PNG was not written when the run rendered it; the "
            "region records are complete.",
            "Rerun the sketch script to render the map again.",
        )
    path = Path(map_path).with_suffix(".png")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise RegionMapError(
            "region_image_unavailable", f"Cannot read the map PNG {str(path)!r}: {exc}",
            "Rerun the sketch script to render the map again.",
        ) from exc
    if not data.startswith(_PNG_SIGNATURE):
        raise RegionMapError(
            "region_image_unavailable", f"{str(path)!r} is not a PNG.",
            "Rerun the sketch script to render the map again.",
        )
    digest = hashlib.sha256(data).hexdigest()
    if listing.get("png_sha256") and digest != listing["png_sha256"]:
        raise RegionMapError(
            "region_image_changed",
            f"{str(path)!r} no longer matches the PNG saved with this map.",
            "Rerun the sketch script to render the map again.",
        )
    return path, digest


def _summary_record(record):
    keys = ("id", "index", "area", "perimeter", "bounds", "interior", "holes", "selector")
    return {key: record[key] for key in keys if key in record}


def inspect_payload(map_path, *, entity_id=None, image=False, limit=DEFAULT_LIMIT,
                    no_limit=False, response_path=str, build_dir=None):
    """(payload, exit_code) for ``agentcad inspect <map.json>``. Reads only."""
    listing = read_map(map_path)
    map_arg = response_path(Path(map_path).resolve())
    suffix = ["--build-dir", str(build_dir)] if build_dir else []

    def action(*extra):
        return shlex.join(["agentcad", "inspect", str(map_arg), *extra, *suffix])

    regions = listing["regions"]
    map_info = {
        key: listing.get(key)
        for key in ("kind", "version", "label", "map", "script", "units", "plane",
                    "dimensions", "naming", "count", "window")
        if listing.get(key) is not None
    }
    map_info["json"] = map_arg
    map_info["id_prefix"] = id_prefix(listing["version"], listing["map"])
    payload = {"command": "inspect", "status": "success", "region_map": map_info}
    selector_note = (
        f"Paste into the same script (regions = the sketch_regions(...) list "
        f"from {listing.get('script') or 'the source'}) with the same sketch and "
        "parameters. IDs and indexes are local to this saved map; after "
        "changing the sketch, rerun and inspect the new map."
    )
    if entity_id is not None:
        record = resolve(listing, entity_id)
        payload["region"] = record
        payload["selector"] = {
            "backend": "build123d",
            "call": record["selector"],
            "index_call": f"regions[{record['index']}]",
            "note": selector_note,
        }
        payload["next_actions"] = [] if image else [action("--id", entity_id, "--image")]
    else:
        shown = regions if no_limit else regions[:limit]
        payload["regions"] = [_summary_record(record) for record in shown]
        if len(shown) < len(regions):
            payload["truncation"] = {
                "returned": len(shown), "total": len(regions),
                "next_action": action("--no-limit"),
            }
        payload["selector_note"] = selector_note
        first = regions[0]["id"] if regions else None
        payload["next_actions"] = (
            [action("--id", first, "--image")] if first else []
        )
    if image:
        try:
            png, digest = check_image(listing, map_path)
        except RegionMapError as exc:
            payload.update(status="error", error_kind="image_unavailable", reason=exc.reason)
            payload["image"] = {"status": "failed", "message": str(exc)}
            payload["suggestion"] = (
                f"{exc.suggestion} The region records and selectors above are "
                "still usable."
            )
            return payload, 1
        payload["image"] = {
            "status": "success",
            "png": response_path(png),
            "png_sha256": digest,
            "scope": "full_map",
            "note": (
                f"The image is the whole saved map {listing['map']!r} with "
                "every region numbered by its index"
                + (f"; the selected region is labeled {payload['region']['index']}."
                   if entity_id is not None else ".")
            ),
        }
    return payload, 0
