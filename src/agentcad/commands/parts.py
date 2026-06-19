import json
import sys
from pathlib import Path

import click

from agentcad.manifest import load_manifest


def _emit_error(message, *, code=1, **extra):
    payload = {
        "command": "parts",
        "status": "error",
        "message": message,
        **extra,
    }
    click.echo(json.dumps(payload))
    sys.exit(code)


def _resolve_version(manifest, ref):
    """Resolve a version ref by number, vN, label, version dir, current, or latest."""
    versions = manifest.get("versions", [])
    successful = [v for v in versions if v.get("status") == "success"]

    if ref in {"current", "latest"}:
        if ref == "current" and manifest.get("current"):
            ref = str(manifest["current"])
        elif successful:
            return successful[-1]
        else:
            return None

    candidates = []
    try:
        candidates.append(("version", int(ref)))
    except ValueError:
        if ref.startswith("v") and ref[1:].isdigit():
            candidates.append(("version", int(ref[1:])))

    normalized_path = ref.strip().strip("/")
    for v in versions:
        for kind, value in candidates:
            if kind == "version" and v.get("version") == value:
                return v
        if v.get("label") == ref:
            return v
        if str(v.get("path", "")).strip("/") == normalized_path:
            return v
    return None


def _load_version_meta(version_entry):
    meta_path = Path.cwd() / version_entry["path"] / "meta.json"
    if not meta_path.exists():
        _emit_error(
            f"meta.json not found for version '{version_entry.get('label')}'.",
            version=version_entry.get("version"),
            label=version_entry.get("label"),
            path=str(meta_path),
        )
    return json.loads(meta_path.read_text())


def _normalize_part(part, idx):
    """Return the part payload agents should reference from the parts CLI.

    New versions already have string ids + id_source. Older meta.json files
    stored numeric positional IDs, so expose a string `part_<n>` handle while
    preserving the raw legacy value for diagnostics.
    """
    normalized = dict(part)
    if "id_source" not in normalized:
        raw_id = normalized.get("id", idx)
        normalized["legacy_id"] = raw_id
        normalized["id"] = f"part_{raw_id}" if isinstance(raw_id, int) else str(raw_id)
        normalized["id_source"] = "legacy"
        normalized["version_local"] = True
    return normalized


def _parts_from_meta(meta):
    return [
        _normalize_part(part, idx)
        for idx, part in enumerate(meta.get("parts", []))
    ]


def _base_response(manifest, version_entry, meta):
    response = {
        "project": manifest.get("name"),
        "version": meta.get("version", version_entry.get("version")),
        "label": meta.get("label", version_entry.get("label")),
        "version_status": meta.get("status", version_entry.get("status")),
        "path": version_entry.get("path"),
    }
    if meta.get("viewer"):
        response["viewer"] = meta["viewer"]
    if meta.get("script"):
        response["script"] = meta["script"]
    if meta.get("outputs"):
        response["outputs"] = meta["outputs"]
    return response


@click.group(name="parts")
def parts_cmd():
    """List and inspect parts captured for version snapshots.

    Reads the same <version>/meta.json snapshot used by viewer.html.
    Version refs may be a number, vN, label, version directory, current,
    or latest.
    """


@parts_cmd.command(name="list")
@click.argument("ref")
def list_parts(ref):
    """List parts for a version reference.

    REF may be a version number (1), vN (v1), label, version directory,
    current, or latest. Output is JSON with version, label, viewer,
    outputs, part_count, and parts[].

    New-schema parts have string id plus id_source. Legacy numeric part
    IDs are exposed as part_<n>, with legacy_id and version_local=true.
    """
    manifest = load_manifest(command="parts")
    version_entry = _resolve_version(manifest, ref)
    if version_entry is None:
        _emit_error(f"Version '{ref}' not found.", ref=ref)

    meta = _load_version_meta(version_entry)
    parts = _parts_from_meta(meta)
    click.echo(json.dumps({
        "command": "parts list",
        "status": "success",
        **_base_response(manifest, version_entry, meta),
        "part_count": len(parts),
        "parts": parts,
    }))


@parts_cmd.command(name="show")
@click.argument("ref")
@click.argument("part_id")
def show_part(ref, part_id):
    """Show one part by id for a version reference.

    REF accepts the same forms as `parts list`. PART_ID is the stable
    string id from parts[]. For legacy numeric IDs, either part_<n> or
    the raw numeric alias is accepted.

    Output is JSON with version, label, viewer, outputs, and one part.
    """
    manifest = load_manifest(command="parts")
    version_entry = _resolve_version(manifest, ref)
    if version_entry is None:
        _emit_error(f"Version '{ref}' not found.", ref=ref)

    meta = _load_version_meta(version_entry)
    parts = _parts_from_meta(meta)
    for part in parts:
        aliases = {str(part.get("id"))}
        if "legacy_id" in part:
            aliases.add(str(part["legacy_id"]))
        if part_id in aliases:
            click.echo(json.dumps({
                "command": "parts show",
                "status": "success",
                **_base_response(manifest, version_entry, meta),
                "part": part,
            }))
            return

    _emit_error(
        f"Part '{part_id}' not found in version '{ref}'.",
        ref=ref,
        part_id=part_id,
        available_ids=[p.get("id") for p in parts],
    )
