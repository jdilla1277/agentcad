"""Read saved viewer history without importing CAD or changing project state."""
from pathlib import Path

from agentcad.project_viewer import read_json


def _directory(root, number, path):
    directory = root / str(path).rstrip("/")
    if (directory.parent != root or directory.name not in {f"v{number}"}
            and not directory.name.startswith(f"v{number}_")):
        raise ValueError("Invalid version directory")
    return directory


def _rows(record):
    root = Path(record["root"])
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("Project path changed")
    manifest_path = root / "agentcad.json"
    if manifest_path.resolve() != manifest_path:
        raise ValueError("Manifest path changed")
    manifest = read_json(manifest_path, {})
    if not isinstance(manifest, dict):
        raise ValueError("Invalid version history")
    entries = manifest.get("versions", [])
    if not isinstance(entries, list):
        raise ValueError("Invalid version history")
    rows = {}
    for entry in entries:
        if not isinstance(entry, dict) or type(entry.get("version")) is not int or entry["version"] < 1:
            raise ValueError("Invalid version history")
        number = entry["version"]
        rows[number] = (entry, _directory(root, number, entry.get("path", "")))
    # Older project registrations still work when no manifest was retained.
    for value, source in record.get("versions", {}).items():
        number = int(value)
        if number not in rows:
            file = Path(source)
            rows[number] = ({}, _directory(root, number, file.parent))
    return rows


def _describe(number, entry, directory):
    files = [directory / name for name in ("meta.json", "viewer.html", "output.glb")]
    safe = all(path.resolve() == path and path.is_file() for path in files)
    meta = read_json(files[0], {}) if safe else {}
    if not isinstance(meta, dict):
        raise ValueError("Invalid viewer metadata")
    status = entry.get("status", meta.get("status", "unknown"))
    available = bool(safe and meta.get("version") == number and status == "success"
                     and meta.get("status") == "success"
                     and meta.get("artifacts", {}).get("viewer", {}).get("status") in {None, "success"})
    label = entry.get("label", meta.get("label", f"v{number}"))
    created = entry.get("created", meta.get("created"))
    return {"version": number, "label": label if isinstance(label, str) else f"v{number}",
            "created": created if isinstance(created, str) else None,
            "status": status if isinstance(status, str) else "unknown", "viewer_available": available,
            **({"artifact": f"artifacts/{number}/viewer.html"} if available else {})}


def project_history(record):
    return {"versions": [_describe(number, entry, directory)
                         for number, (entry, directory) in sorted(_rows(record).items(), reverse=True)],
            "latest_version": record.get("latest", {}).get("version")}


def viewer_snapshot(record, number):
    row = _rows(record).get(number)
    if row and _describe(number, *row)["viewer_available"]:
        return row[1] / "viewer.html"
    return None
