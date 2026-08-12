import json
import sys
from pathlib import Path

import click

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.comparison_phases import ComparisonPhaseRecorder
from agentcad.manifest import load_manifest
from agentcad.metrics import compute_metrics
from agentcad.step_io import load_cad_shape

_CAD_FILE_SUFFIXES = {".step", ".stp", ".brep"}


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


def _looks_like_file_ref(ref):
    p = Path(ref).expanduser()
    return p.suffix.lower() in _CAD_FILE_SUFFIXES or "/" in ref or "\\" in ref


def _resolve_file_ref(ref):
    p = Path(ref).expanduser()
    return p if p.is_file() else None


def _load_file_shape_and_metrics(path):
    shape = load_cad_shape(path)
    return shape, compute_metrics(shape)


def _metric_changes(metrics_a, metrics_b):
    all_keys = sorted(set(metrics_a.keys()) | set(metrics_b.keys()))
    return {k: _scalar_diff(metrics_a.get(k), metrics_b.get(k)) for k in all_keys}


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

    file_a = _resolve_file_ref(ref1)
    file_b = _resolve_file_ref(ref2)
    if (
        file_a is not None
        or file_b is not None
        or _looks_like_file_ref(ref1)
        or _looks_like_file_ref(ref2)
    ):
        if file_a is None:
            click.echo(json.dumps({
                "command": "diff",
                "status": "error",
                "message": f"File '{ref1}' not found.",
            }))
            sys.exit(1)
        if file_b is None:
            click.echo(json.dumps({
                "command": "diff",
                "status": "error",
                "message": f"File '{ref2}' not found.",
            }))
            sys.exit(1)

        phase_recorder = ComparisonPhaseRecorder()
        try:
            with phase_recorder.observe("source_loading"):
                shape_a, metrics_a = _load_file_shape_and_metrics(file_a)
                shape_b, metrics_b = _load_file_shape_and_metrics(file_b)
        except ValueError as exc:
            click.echo(json.dumps({
                "command": "diff",
                "status": "error",
                "message": str(exc),
            }))
            sys.exit(1)

        from agentcad.solid_compare import compare_solid_volumes

        solid_comparison = None
        try:
            with phase_recorder.observe("exact_3d_comparison") as phase:
                solid_comparison = compare_solid_volumes(shape_a, shape_b)
                phase.status = solid_comparison.data.get("status", "success")
                phase.message = (
                    solid_comparison.data.get("message")
                    or solid_comparison.data.get("reason", {}).get("message")
                )
        except Exception:
            pass
        response = {
            "command": "diff",
            "status": "success",
            "v1": {"file": _relative_to_cwd(file_a), "label": file_a.name},
            "v2": {"file": _relative_to_cwd(file_b), "label": file_b.name},
            "changes": {
                "metrics": _metric_changes(metrics_a, metrics_b),
            },
            "comparison_phases": phase_recorder.entries,
        }
        if solid_comparison is not None:
            response["comparison_3d"] = solid_comparison.data

        if visual:
            _add_visual_response(
                response,
                file_a,
                file_b,
                overlay,
                solid_comparison=solid_comparison,
                phase_recorder=phase_recorder,
            )
        else:
            phase_recorder.finalize_pending("Visual comparison not requested.")

        click.echo(json.dumps(response))
        maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
        return

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

    phase_recorder = ComparisonPhaseRecorder()
    with phase_recorder.observe("source_loading"):
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

    # Compare parts if present in either version. Newer parts have a string
    # id, which is the machine reference. Legacy meta without id_source used
    # numeric positional IDs, so retain its name-first behavior.
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
            if "id_source" in p and p.get("id") is not None:
                keys.append(str(p["id"]))
                continue
            n = p.get("name")
            fallback = f"part_{p['id']}" if p.get("id") is not None else str(len(keys))
            keys.append(n if (n is not None and counts.get(n) == 1) else fallback)
        return keys

    k1 = _part_keys(parts1_list)
    k2 = _part_keys(parts2_list)
    parts1 = dict(zip(k1, parts1_list))
    parts2 = dict(zip(k2, parts2_list))
    if parts1 or parts2:
        id_changes = _compute_set_diff(parts1.keys(), parts2.keys())
        parts_changes = {
            "ids": id_changes,
            "names": id_changes,  # Backward-compatible alias for older callers.
        }
        shared = sorted(set(parts1.keys()) & set(parts2.keys()))
        for key in shared:
            p1_part = parts1[key]
            p2_part = parts2[key]
            part_diff = {
                "name": _scalar_diff(p1_part.get("name"), p2_part.get("name")),
                "color": _scalar_diff(p1_part.get("color"), p2_part.get("color")),
            }
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

    step_a = _find_step_path(v1_entry, meta1)
    step_b = _find_step_path(v2_entry, meta2)
    solid_comparison = None
    if step_a is not None and step_b is not None:
        try:
            from agentcad.solid_compare import compare_solid_volumes

            with phase_recorder.observe("source_loading"):
                shape_a = load_cad_shape(step_a)
                shape_b = load_cad_shape(step_b)
            with phase_recorder.observe("exact_3d_comparison") as phase:
                solid_comparison = compare_solid_volumes(shape_a, shape_b)
                phase.status = solid_comparison.data.get("status", "success")
                phase.message = (
                    solid_comparison.data.get("message")
                    or solid_comparison.data.get("reason", {}).get("message")
                )
            response["comparison_3d"] = solid_comparison.data
        except Exception:
            solid_comparison = None
    else:
        phase_recorder.skip(
            "exact_3d_comparison",
            "No STEP outputs were available for exact comparison.",
        )

    if visual:
        if step_a is None or step_b is None:
            click.echo(json.dumps({
                "command": "diff",
                "status": "error",
                "message": "Could not find STEP outputs for one or both versions.",
            }))
            sys.exit(1)
        _add_visual_response(
            response,
            step_a,
            step_b,
            overlay,
            solid_comparison=solid_comparison,
            phase_recorder=phase_recorder,
        )
    else:
        phase_recorder.finalize_pending("Visual comparison not requested.")

    response["comparison_phases"] = phase_recorder.entries

    click.echo(json.dumps(response))

    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)


def _relative_to_cwd(path):
    return str(path.relative_to(Path.cwd())) if path.is_relative_to(Path.cwd()) else str(path)


def _add_visual_response(
    response,
    step_a,
    step_b,
    overlay,
    *,
    solid_comparison=None,
    phase_recorder=None,
):
    from agentcad.commands.view import (
        _open_browser,
        _render_diff,
        _render_diff_overlay_png,
        _render_diff_png,
        _render_solid_comparison_artifacts,
        _resolve_to_glb_and_shape,
    )

    glb_a, shape_a, err = _resolve_to_glb_and_shape(str(step_a))
    if err:
        click.echo(json.dumps({"command": "diff", "status": "error", "message": err}))
        sys.exit(1)
    glb_b, shape_b, err = _resolve_to_glb_and_shape(str(step_b))
    if err:
        click.echo(json.dumps({"command": "diff", "status": "error", "message": err}))
        sys.exit(1)

    png_path = None
    overlay_png_path = None
    volume_glb_path = None
    volume_png_path = None
    projection_comparison = None
    if phase_recorder is None:
        phase_recorder = ComparisonPhaseRecorder()
    if shape_a is not None and shape_b is not None:
        with phase_recorder.observe("comparison_rendering"):
            png_path = _render_diff_png(
                shape_a, shape_b, glb_a, glb_b, Path.cwd()
            )
        with phase_recorder.observe("projection_comparison"):
            overlay_png_path, projection_comparison = _render_diff_overlay_png(
                shape_a, shape_b, glb_a, glb_b, Path.cwd()
            )
        if (
            solid_comparison is None
            and phase_recorder.entries["exact_3d_comparison"].get("status")
            == "pending"
        ):
            from agentcad.solid_compare import compare_solid_volumes

            try:
                with phase_recorder.observe("exact_3d_comparison") as phase:
                    solid_comparison = compare_solid_volumes(shape_a, shape_b)
                    phase.status = solid_comparison.data.get("status", "success")
                    phase.message = (
                        solid_comparison.data.get("message")
                        or solid_comparison.data.get("reason", {}).get("message")
                    )
                response["comparison_3d"] = solid_comparison.data
            except Exception:
                pass
        if solid_comparison is not None and solid_comparison.available:
            with phase_recorder.observe("difference_artifact_export") as phase:
                volume_glb_path, volume_png_path = (
                    _render_solid_comparison_artifacts(
                        solid_comparison,
                        glb_a,
                        glb_b,
                        Path.cwd(),
                    )
                )
                if volume_glb_path is None and volume_png_path is None:
                    phase.status = "unavailable"
                    phase.message = "3D difference artifacts were not available."
        else:
            phase_recorder.skip(
                "difference_artifact_export",
                "Exact 3D comparison produced no exportable geometry.",
            )
    else:
        phase_recorder.skip(
            "comparison_rendering", "Loaded inputs did not include B-rep shapes."
        )
        phase_recorder.skip(
            "projection_comparison", "Loaded inputs did not include B-rep shapes."
        )
        phase_recorder.skip(
            "difference_artifact_export",
            "Loaded inputs did not include B-rep shapes.",
        )

    with phase_recorder.observe("viewer_generation"):
        html_path, url, mode = _render_diff(
            glb_a,
            glb_b,
            overlay=overlay,
            out_dir=Path.cwd(),
            diff_side_png=png_path,
            diff_overlay_png=overlay_png_path,
            diff_volume_png=volume_png_path,
        )

    visual_resp = {
        "mode": mode,
        "html": _relative_to_cwd(html_path),
        "url": url,
    }

    if png_path is not None:
        visual_resp["png"] = _relative_to_cwd(png_path)
    if overlay_png_path is not None:
        visual_resp["overlay_png"] = _relative_to_cwd(overlay_png_path)
    if projection_comparison is not None:
        visual_resp["projection_comparison"] = projection_comparison
    if volume_glb_path is not None:
        visual_resp["volume_glb"] = _relative_to_cwd(volume_glb_path)
    if volume_png_path is not None:
        visual_resp["volume_png"] = _relative_to_cwd(volume_png_path)

    _open_browser(url)
    response["visual"] = visual_resp


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
