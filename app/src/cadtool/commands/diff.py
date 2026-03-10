import json
import sys
from pathlib import Path

import click

from cadtool.manifest import load_manifest


def _resolve_version(manifest, ref):
    """Resolve a version reference (number or label) to a manifest version entry."""
    versions = manifest.get("versions", [])

    # Try as version number first
    try:
        num = int(ref)
        for v in versions:
            if v["version"] == num:
                return v
    except ValueError:
        pass

    # Try as label
    for v in versions:
        if v["label"] == ref:
            return v

    return None


def _load_version_meta(version_entry):
    """Load meta.json for a version entry."""
    path = Path.cwd() / version_entry["path"] / "meta.json"
    return json.loads(path.read_text())


def _compute_set_diff(old_keys, new_keys):
    """Compute added/removed/unchanged between two sets of keys."""
    old_set = set(old_keys)
    new_set = set(new_keys)
    return {
        "added": sorted(new_set - old_set),
        "removed": sorted(old_set - new_set),
        "unchanged": sorted(old_set & new_set),
    }


def _scalar_diff(old_val, new_val):
    """Return None if same, {from, to} if different."""
    if old_val == new_val:
        return None
    return {"from": old_val, "to": new_val}


@click.command()
@click.argument("ref1")
@click.argument("ref2")
def diff(ref1, ref2):
    """Compare two versions of a model."""
    manifest = load_manifest(command="diff")

    v1_entry = _resolve_version(manifest, ref1)
    if v1_entry is None:
        click.echo(json.dumps({
            "command": "diff",
            "status": "error",
            "message": f"Version '{ref1}' not found",
        }))
        sys.exit(1)

    v2_entry = _resolve_version(manifest, ref2)
    if v2_entry is None:
        click.echo(json.dumps({
            "command": "diff",
            "status": "error",
            "message": f"Version '{ref2}' not found",
        }))
        sys.exit(1)

    meta1 = _load_version_meta(v1_entry)
    meta2 = _load_version_meta(v2_entry)

    # Compute changes
    changes = {
        "label": _scalar_diff(meta1.get("label"), meta2.get("label")),
        "status": _scalar_diff(meta1.get("status"), meta2.get("status")),
        "outputs": _compute_set_diff(
            meta1.get("outputs", {}).keys(),
            meta2.get("outputs", {}).keys(),
        ),
        "renders": _compute_set_diff(
            meta1.get("renders", {}).keys(),
            meta2.get("renders", {}).keys(),
        ),
    }

    # Compare metrics if present in either version
    m1 = meta1.get("metrics", {})
    m2 = meta2.get("metrics", {})
    if m1 or m2:
        all_keys = sorted(set(m1.keys()) | set(m2.keys()))
        changes["metrics"] = {
            k: _scalar_diff(m1.get(k), m2.get(k)) for k in all_keys
        }

    # Compare params if present in either version
    p1 = meta1.get("params", {})
    p2 = meta2.get("params", {})
    if p1 or p2:
        all_param_keys = sorted(set(p1.keys()) | set(p2.keys()))
        changes["params"] = {
            k: _scalar_diff(p1.get(k), p2.get(k)) for k in all_param_keys
        }

    click.echo(json.dumps({
        "command": "diff",
        "status": "success",
        "v1": {"version": meta1["version"], "label": meta1["label"]},
        "v2": {"version": meta2["version"], "label": meta2["label"]},
        "changes": changes,
    }))
