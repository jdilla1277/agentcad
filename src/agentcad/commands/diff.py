import json
import sys
from pathlib import Path

import click

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.manifest import load_manifest


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
@click.option("--visual", is_flag=True, default=False, help="Open a visual side-by-side (or overlay) diff in the browser.")
@click.option("--overlay", is_flag=True, default=False, help="With --visual, use tinted overlay mode instead of side-by-side.")
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
def diff(ref1, ref2, visual, overlay, no_daemon):
    """Compare two versions of a model."""
    # Try routing through daemon. Exits before returning if reachable.
    argv = ["diff", ref1, ref2]
    if visual:
        argv.append("--visual")
    if overlay:
        argv.append("--overlay")
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

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

    # Compare parts if present in either version. Key by name when unique on
    # BOTH sides, else fall back to id so unnamed / duplicated parts still
    # match positionally. Names can be absent (optional field).
    parts1_list = meta1.get("parts", [])
    parts2_list = meta2.get("parts", [])

    def _part_keys(parts):
        counts = {}
        for p in parts:
            n = p.get("name")
            if n is not None:
                counts[n] = counts.get(n, 0) + 1
        keys = []
        for p in parts:
            n = p.get("name")
            keys.append(n if (n is not None and counts.get(n) == 1) else f"part_{p['id']}")
        return keys

    k1 = _part_keys(parts1_list)
    k2 = _part_keys(parts2_list)
    parts1 = dict(zip(k1, parts1_list))
    parts2 = dict(zip(k2, parts2_list))
    if parts1 or parts2:
        parts_changes = {
            "names": _compute_set_diff(parts1.keys(), parts2.keys()),
        }
        shared = sorted(set(parts1.keys()) & set(parts2.keys()))
        for key in shared:
            p1_part = parts1[key]
            p2_part = parts2[key]
            part_diff = {"color": _scalar_diff(p1_part.get("color"), p2_part.get("color"))}
            m1_metrics = p1_part.get("metrics", {})
            m2_metrics = p2_part.get("metrics", {})
            all_mkeys = sorted(set(m1_metrics.keys()) | set(m2_metrics.keys()))
            for k in all_mkeys:
                part_diff[k] = _scalar_diff(m1_metrics.get(k), m2_metrics.get(k))
            parts_changes[key] = part_diff
        changes["parts"] = parts_changes

    response = {
        "command": "diff",
        "status": "success",
        "v1": {"version": meta1["version"], "label": meta1["label"]},
        "v2": {"version": meta2["version"], "label": meta2["label"]},
        "changes": changes,
    }

    if visual:
        step_a = _find_step_path(v1_entry, meta1)
        step_b = _find_step_path(v2_entry, meta2)
        if step_a is None or step_b is None:
            click.echo(json.dumps({
                "command": "diff",
                "status": "error",
                "message": "Could not find STEP outputs for one or both versions.",
            }))
            sys.exit(1)

        from agentcad.commands.view import _resolve_to_glb_and_shape, _render_diff, _render_diff_png, _open_browser

        glb_a, shape_a, err = _resolve_to_glb_and_shape(str(step_a))
        if err:
            click.echo(json.dumps({"command": "diff", "status": "error", "message": err}))
            sys.exit(1)
        glb_b, shape_b, err = _resolve_to_glb_and_shape(str(step_b))
        if err:
            click.echo(json.dumps({"command": "diff", "status": "error", "message": err}))
            sys.exit(1)

        html_path, url, mode = _render_diff(glb_a, glb_b, overlay=overlay, out_dir=Path.cwd())

        visual_resp = {
            "mode": mode,
            "html": _relative_to_cwd(html_path),
            "url": url,
        }

        # Agent-facing: side-by-side PNG (skipped for overlay mode for now)
        if shape_a is not None and shape_b is not None and not overlay:
            png_path = _render_diff_png(shape_a, shape_b, glb_a, glb_b, Path.cwd())
            visual_resp["png"] = _relative_to_cwd(png_path)

        _open_browser(url)
        response["visual"] = visual_resp

    click.echo(json.dumps(response))

    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)


def _relative_to_cwd(path):
    return str(path.relative_to(Path.cwd())) if path.is_relative_to(Path.cwd()) else str(path)


def _find_step_path(version_entry, meta):
    """Resolve the STEP file path for a version, preferring meta.outputs.step."""
    step_rel = meta.get("outputs", {}).get("step")
    if step_rel:
        p = Path.cwd() / step_rel
        if p.exists():
            return p
    # Fallback: version_dir/output.step
    version_dir = Path.cwd() / version_entry["path"]
    fallback = version_dir / "output.step"
    return fallback if fallback.exists() else None
