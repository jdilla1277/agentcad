import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.manifest import MANIFEST_FILE, load_manifest, save_manifest


def _parse_params(raw):
    """Parse a --params string like 'length=60,width=20' into a dict."""
    params = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            raise ValueError(f"Invalid param format: '{pair}'. Expected key=value.")
        key, val = pair.split("=", 1)
        key, val = key.strip(), val.strip()
        # Bool
        if val.lower() in ("true", "false"):
            params[key] = val.lower() == "true"
        else:
            # Int → Float → String
            try:
                params[key] = int(val)
            except ValueError:
                try:
                    params[key] = float(val)
                except ValueError:
                    params[key] = val
    return params


def _enrich_error(msg):
    """Add actionable context to common OCC error messages."""
    if "BRep_API: command not done" in msg:
        msg += (
            " — this usually means a wire is not closed. "
            "Check that all edge endpoints connect within tolerance (~1e-7mm). "
            "Common cause: floating-point drift across many rotated/translated points."
        )
    return msg


def _find_prev_success(versions):
    """Return the most recent successful version in the manifest, or None.

    Skips failed runs. Used to pick the baseline for auto-diff on every run.
    """
    for entry in reversed(versions):
        if entry.get("status") == "success":
            return entry
    return None


def _record_failure(manifest, script_path, label, version_num, error_msg, runtime=None):
    """Record a script failure on disk and in the manifest."""
    dir_name = f"v{version_num}_{label}_failed"
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True, exist_ok=True)

    # Copy script into failed directory
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    # Write meta.json
    created = datetime.now(timezone.utc).isoformat()
    meta = {
        "version": version_num,
        "label": label,
        "status": "failed",
        "created": created,
        "error": error_msg,
        "script": f"{dir_name}/script.py",
    }
    if runtime is not None:
        meta["runtime"] = runtime
    (version_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    # Update manifest (current does NOT advance)
    versions = manifest.get("versions", [])
    versions.append({
        "version": version_num,
        "label": label,
        "status": "failed",
        "path": f"{dir_name}/",
    })
    manifest["versions"] = versions
    save_manifest(manifest)

    # Output failure JSON
    output_json = {
        "command": "run",
        "status": "failed",
        "version": version_num,
        "label": label,
        "error": error_msg,
        "path": f"{dir_name}/",
    }
    if runtime is not None:
        output_json["runtime"] = runtime
    click.echo(json.dumps(output_json))
    sys.exit(1)




@click.command()
@click.argument("script")
@click.option("--output", required=True, help="Label for this version.")
@click.option("--render", default=None, help="Comma-separated views to render (front,back,left,right,top,bottom,iso). 'all' renders front,right,top,iso.")
@click.option("--export", default=None, help="Comma-separated mesh formats to export (stl, glb, obj).")
@click.option("--preview/--no-preview", default=True, help="Visual feedback (default on): 4-view composite PNG + 60-frame turntable GIF. Adds ~6-10s per run. Pass --no-preview while iterating to keep runs sub-second.")
@click.option("--params", default=None, help="Parameter overrides as key=value,key=value.")
@click.option("--dry-run", is_flag=True, default=False, help="Compute metrics without creating a version or disk artifacts.")
@click.option("--runtime", default=None, type=click.Choice(["cadquery", "build123d"]), help="Force a runtime. Default: auto-detect from the script's imports; falls back to cadquery when neither library is imported.")
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
@click.pass_context
def run(ctx, script, output, render, export, preview, params, dry_run, runtime, no_daemon):
    """Execute a CadQuery or build123d script and produce a versioned STEP file.

    If `script` is a CAD file (.step / .stp / .brep), dispatches to
    `agentcad import` instead — agents who instinctively reach for `run`
    when handed a CAD file get the right behavior automatically.
    """
    # M60 Phase 2 (slice 2b): suffix-dispatch CAD files to `agentcad import`.
    # This closes a footgun where agents handed a STEP would write
    # `agentcad run widget.step` and hit a confusing parse error. Tier 0
    # files actually import; Tier 1+ files surface the polite-no responses
    # via the same dispatch (better than a Python parse error).
    from agentcad import file_detect as _fd
    if _fd.is_recognized_cad_extension(script):
        from agentcad.commands.import_cmd import import_cmd
        ctx.invoke(import_cmd, file=script, label=output, init_flag=False)
        return

    # Run the actual work inside a try/except so internal exceptions
    # (e.g. `Bnd_Box is void` from compute_metrics on a degenerate
    # shape) surface as JSON on stdout — preserving the docs' "All
    # output is JSON" contract. Without this, daemon-routed runs emit
    # only stderr heartbeats and exit non-zero with empty stdout, and
    # the agent has no recovery signal.
    try:
        _run_impl(
            ctx, script, output, render, export,
            preview, params, dry_run, runtime, no_daemon,
        )
    except SystemExit:
        # Explicit sys.exit() calls inside _run_impl are intentional —
        # they already emitted the proper JSON. Pass through.
        raise
    except Exception as e:
        import traceback as _tb
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": f"Internal error: {type(e).__name__}: {e}",
            "traceback": _tb.format_exc(),
        }))
        sys.exit(1)


def _run_impl(ctx, script, output, render, export, preview, params,
              dry_run, runtime, no_daemon):
    _t_total_start = time.perf_counter()
    _timings = {}
    def _mark(key, start):
        _timings[key] = round((time.perf_counter() - start) * 1000)

    def _heartbeat(message):
        # Stderr progress line so callers can distinguish "still working"
        # from "wedged" during long phases (preview render, GIF encode).
        # One line per phase, always-on, lightweight. Stays on stderr so
        # the JSON on stdout remains parseable. Issue #164.
        click.echo(f"[agentcad] {message}", err=True)


    # Try routing through daemon. If reachable, this exits before returning.
    argv = ["run", script, "--output", output]
    if render:
        argv.extend(["--render", render])
    if export:
        argv.extend(["--export", export])
    if not preview:
        argv.append("--no-preview")
    if params:
        argv.extend(["--params", params])
    if dry_run:
        argv.append("--dry-run")
    if runtime:
        argv.extend(["--runtime", runtime])
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    # Fallback: direct execution
    manifest = load_manifest(command="run")

    # Python version check (before CadQuery imports)
    if sys.version_info >= (3, 13):
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": (
                f"agentcad requires Python 3.10-3.12 "
                f"(found {sys.version_info[0]}.{sys.version_info[1]}). "
                f"CadQuery/OCP bindings are not available on newer Python versions."
            ),
        }))
        sys.exit(1)

    script_path = Path(script)
    if not script_path.exists():
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": f"Script file '{script}' not found",
        }))
        sys.exit(1)

    # Dispatch to the right runner. Precedence:
    #   --runtime flag > script imports > project mode (agentcad.json) > default
    from agentcad.runners import dispatch

    raw_source = script_path.read_text()
    project_default = dispatch.project_runtime()

    try:
        runtime_name, runner = dispatch.resolve(
            raw_source, override=runtime, project_default=project_default
        )
    except ValueError as e:
        # Ambiguous imports or unknown --runtime — surface cleanly.
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": str(e),
        }))
        sys.exit(1)

    # Pre-execution validation (before version allocation)
    validation_errors = runner.validate(raw_source)
    if validation_errors:
        click.echo(json.dumps({
            "command": "run",
            "status": "validation_error",
            "runtime": runtime_name,
            "checks": validation_errors,
        }))
        sys.exit(1)

    # Parse --params before version allocation (errors should be cheap)
    parsed_params = None
    if params:
        try:
            parsed_params = _parse_params(params)
        except ValueError as e:
            click.echo(json.dumps({
                "command": "run",
                "status": "error",
                "message": str(e),
            }))
            sys.exit(1)

    # Spawn the daemon NOW — after the runner module is imported (so OCP is
    # in memory) but BEFORE the script executes. The script may use boolean
    # operations (cut/fuse/intersect) which initialize OCP's TBB thread
    # pool; fork-after-TBB-init creates a child with TBB state but no
    # actual worker threads, and the daemon would deadlock on its first
    # parallel-OCP request.
    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)

    # Execute via the runner — returns a runtime-agnostic ExecutionResult.
    _heartbeat(f"running script ({runtime_name})…")
    _t = time.perf_counter()
    result = runner.execute(raw_source, parsed_params)

    # Param validation errors (unknown names, CQGI InvalidParameterError) —
    # surface them without consuming a version number.
    if result.status == "validation_error":
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "runtime": runtime_name,
            "message": result.exception,
        }))
        sys.exit(1)

    # Determine version number before recording failures (failures consume a number)
    versions = manifest.get("versions", [])
    version_num = len(versions) + 1
    label = output

    if result.status == "execution_error":
        _record_failure(manifest, script_path, label, version_num,
                        _enrich_error(result.exception), runtime=runtime_name)

    # Success path
    shape = result.native_shape
    warnings = list(result.warnings)

    _mark("script_exec_ms", _t)

    # Compute geometric metrics
    _heartbeat("computing metrics…")
    _t = time.perf_counter()
    from agentcad.metrics import compute_metrics

    topo_shape_for_metrics = result.topo_shape
    metrics = compute_metrics(topo_shape_for_metrics)
    _mark("metrics_ms", _t)

    # Per-part breakdown (feedback #190): one entry per show_object() call.
    # Metrics computed now so --dry-run also surfaces them; per-part previews
    # are rendered later (below) only when the version directory is being
    # materialized and --no-preview wasn't passed.
    parts_output: list[dict] = []
    raw_parts = result.parts
    part_name_counts: dict[str, int] = {}
    for p in raw_parts:
        n = p.get("name")
        if n:
            part_name_counts[n] = part_name_counts.get(n, 0) + 1
    for p in raw_parts:
        entry: dict = {"id": p["id"]}
        if p.get("name") is not None:
            entry["name"] = p["name"]
        if p.get("color") is not None:
            entry["color"] = p["color"]
        entry["part_of"] = None
        entry["metrics"] = compute_metrics(p["topo_shape"])
        parts_output.append(entry)

    # Surface validity issues as top-level warnings
    if not metrics.get("is_valid", True):
        warnings.append(
            "Invalid geometry detected (is_valid: false). "
            "Run 'agentcad inspect' on the STEP file for diagnostic details."
        )
    if metrics.get("warnings"):
        warnings.extend(metrics["warnings"])

    # Dry-run: return metrics only, no version/disk artifacts
    if dry_run:
        output_json = {
            "command": "run",
            "status": "success",
            "runtime": runtime_name,
            "metrics": metrics,
        }
        if parts_output:
            output_json["parts"] = parts_output
        if warnings:
            output_json["warnings"] = warnings
        _timings["total_ms"] = round((time.perf_counter() - _t_total_start) * 1000)
        output_json["timings"] = _timings
        click.echo(json.dumps(output_json))
        return

    # Script succeeded — create version directory and write files
    dir_name = f"v{version_num}_{label}" if label != f"v{version_num}" else label
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True, exist_ok=True)

    # Copy script into version directory
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    # Export STEP file via the runner (each engine has its own writer)
    runner.export_step(shape, str(version_dir / "output.step"))

    # Export mesh formats if requested
    exports_meta = {}
    if export:
        formats = [f.strip() for f in export.split(",")]
        topo_shape = result.topo_shape
        for fmt in formats:
            if fmt == "stl":
                stl_path = version_dir / "output.stl"
                runner.export_stl(shape, str(stl_path))
                exports_meta["stl"] = f"{dir_name}/output.stl"
            elif fmt == "glb":
                from agentcad.export import export_glb

                glb_path = version_dir / "output.glb"
                export_glb(topo_shape, str(glb_path))
                exports_meta["glb"] = f"{dir_name}/output.glb"
            elif fmt == "obj":
                from agentcad.export import export_obj

                obj_path = version_dir / "output.obj"
                export_obj(topo_shape, str(obj_path))
                exports_meta["obj"] = f"{dir_name}/output.obj"

    # Render views if requested
    renders_meta = {}
    if render:
        from agentcad.render import (
            parse_view_spec, render_shape as render_shape_view,
            render_shape_custom, render_views, ALL_VIEWS,
        )

        renders_dir = version_dir / "renders"
        renders_dir.mkdir(parents=True, exist_ok=True)
        topo_shape = result.topo_shape
        view_specs = parse_view_spec(render)
        for spec_type, spec_value in view_specs:
            if spec_type == "named":
                out_path = renders_dir / f"{spec_value}.png"
                render_shape_view(topo_shape, spec_value, out_path)
                renders_meta[spec_value] = f"{dir_name}/renders/{spec_value}.png"
            else:
                az, el = spec_value
                name = f"{int(az)}_{int(el)}"
                out_path = renders_dir / f"{name}.png"
                render_shape_custom(topo_shape, az, el, out_path)
                renders_meta[name] = f"{dir_name}/renders/{name}.png"

    # Visual feedback by default: 4-view composite preview + auto-diff against the
    # most recent successful prior version (if any). Skip all of this if --no-preview.
    preview_meta = None
    preview_gif_meta = None
    diff_meta = None
    viewer_meta = None
    if preview:
        from agentcad.render import (
            render_composite_4view,
            render_diff_side_by_side,
            render_diff_overlay,
            render_turntable_gif,
        )

        _heartbeat("rendering preview (4-view composite)…")
        _t = time.perf_counter()
        preview_path = version_dir / "preview.png"
        render_composite_4view(topo_shape_for_metrics, preview_path, per_view_size=512)
        preview_meta = f"{dir_name}/preview.png"
        _mark("preview_ms", _t)

        _heartbeat("encoding preview.gif (60 frames)…")
        _t = time.perf_counter()
        preview_gif_path = version_dir / "preview.gif"
        render_turntable_gif(topo_shape_for_metrics, preview_gif_path,
                             width=512, height=512, frames=60)
        preview_gif_meta = f"{dir_name}/preview.gif"
        _mark("preview_gif_ms", _t)

        # Per-part previews. Filename is the name when unique, else part_<id>.png
        # so duplicates never overwrite.
        if parts_output:
            from agentcad.render import render_shape as _render_part_iso

            parts_dir = version_dir / "parts"
            parts_dir.mkdir(exist_ok=True)
            _n_parts = len(parts_output)
            _heartbeat(
                f"rendering per-part previews ({_n_parts} part"
                f"{'s' if _n_parts != 1 else ''})…"
            )
            _t = time.perf_counter()
            for entry, raw in zip(parts_output, raw_parts):
                name = entry.get("name")
                if name and part_name_counts.get(name) == 1:
                    fname = f"{name}.png"
                else:
                    fname = f"part_{entry['id']}.png"
                _render_part_iso(raw["topo_shape"], "iso", parts_dir / fname)
                entry["preview"] = f"{dir_name}/parts/{fname}"
            _mark("parts_preview_ms", _t)

        prev = _find_prev_success(versions)
        if prev is not None:
            prev_step_path = Path.cwd() / prev["path"] / "output.step"
            if prev_step_path.exists():
                _t = time.perf_counter()
                try:
                    from agentcad.step_io import load_cad_shape
                    prev_shape = load_cad_shape(prev_step_path)

                    side_path = version_dir / "diff_side.png"
                    overlay_path = version_dir / "diff_overlay.png"
                    render_diff_side_by_side(
                        prev_shape, topo_shape_for_metrics,
                        prev["label"], label, side_path,
                        width=512, height=512,
                    )
                    render_diff_overlay(
                        prev_shape, topo_shape_for_metrics,
                        prev["label"], label, overlay_path,
                        width=1024, height=1024,
                    )
                    diff_meta = {
                        "against": prev["label"],
                        "side_by_side": f"{dir_name}/diff_side.png",
                        "overlay": f"{dir_name}/diff_overlay.png",
                    }
                    _mark("diff_ms", _t)
                except Exception as e:
                    warnings.append(
                        f"Could not render diff against v{prev['version']}_{prev['label']}: {type(e).__name__}: {e}"
                    )

        # Unified viewer HTML — embeds the current model's GLB, previous version's
        # GLB (if any), and the agent PNGs, so humans can explore interactively.
        # We export a GLB specifically for the viewer if --export glb wasn't set.
        from agentcad.export import export_glb
        from agentcad.commands.view import _render_unified

        viewer_glb_path = version_dir / "output.glb"
        if not viewer_glb_path.exists():
            export_glb(topo_shape_for_metrics, str(viewer_glb_path))

        prev_glb_path = None
        if diff_meta is not None and prev is not None:
            candidate = Path.cwd() / prev["path"] / "output.glb"
            if candidate.exists():
                prev_glb_path = candidate
            else:
                prev_step_path = Path.cwd() / prev["path"] / "output.step"
                if prev_step_path.exists():
                    try:
                        from agentcad.step_io import load_cad_shape as _load
                        prev_shape = _load(prev_step_path)
                        candidate = Path.cwd() / prev["path"] / "output.glb"
                        export_glb(prev_shape, str(candidate))
                        prev_glb_path = candidate
                    except Exception:
                        prev_glb_path = None

        _heartbeat("writing viewer.html…")
        _t = time.perf_counter()
        viewer_path = version_dir / "viewer.html"
        _render_unified(
            viewer_path,
            glb_a=viewer_glb_path,
            glb_b=prev_glb_path,
            label_a=label,
            label_b=prev["label"] if prev_glb_path else "",
            default_mode="side-by-side" if prev_glb_path else "single-a",
            preview_png=version_dir / "preview.png" if preview_meta else None,
            diff_side_png=version_dir / "diff_side.png" if diff_meta else None,
            diff_overlay_png=version_dir / "diff_overlay.png" if diff_meta else None,
            parts=parts_output,
        )
        viewer_meta = f"{dir_name}/viewer.html"
        _mark("viewer_ms", _t)

    # Write meta.json
    created = datetime.now(timezone.utc).isoformat()
    meta = {
        "version": version_num,
        "label": label,
        "status": "success",
        "runtime": runtime_name,
        "created": created,
        "script": f"{dir_name}/script.py",
        "outputs": {
            "step": f"{dir_name}/output.step",
            **exports_meta,
        },
    }
    meta["metrics"] = metrics
    if parts_output:
        meta["parts"] = parts_output
    if parsed_params:
        meta["params"] = parsed_params
    if warnings:
        meta["warnings"] = warnings
    if preview_meta:
        meta["preview"] = preview_meta
    if preview_gif_meta:
        meta["preview_gif"] = preview_gif_meta
    if diff_meta:
        meta["diff"] = diff_meta
    if viewer_meta:
        meta["viewer"] = viewer_meta
    if renders_meta:
        meta["renders"] = renders_meta
    meta_path = version_dir / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    # Update manifest
    versions.append({
        "version": version_num,
        "label": label,
        "status": "success",
        "path": f"{dir_name}/",
    })
    manifest["versions"] = versions
    manifest["current"] = label
    save_manifest(manifest)

    hint = None
    if viewer_meta:
        if prev_glb_path:
            hint = (
                f"Show your work in the browser by opening {viewer_meta} "
                f"— side-by-side with the previous run."
            )
        else:
            hint = f"Show your work in the browser by opening {viewer_meta}."

    # Output success JSON
    output_json = {
        "command": "run",
        "status": "success",
        "runtime": runtime_name,
        "version": version_num,
        "label": label,
        "outputs": {
            "step": f"{dir_name}/output.step",
            "script": f"{dir_name}/script.py",
            **exports_meta,
        },
    }
    output_json["metrics"] = metrics
    if parts_output:
        output_json["parts"] = parts_output
    if parsed_params:
        output_json["params"] = parsed_params
    if warnings:
        output_json["warnings"] = warnings
    if preview_meta:
        output_json["preview"] = preview_meta
    if preview_gif_meta:
        output_json["preview_gif"] = preview_gif_meta
    if diff_meta:
        output_json["diff"] = diff_meta
    if viewer_meta:
        output_json["viewer"] = viewer_meta
    if renders_meta:
        output_json["renders"] = renders_meta
    if hint:
        output_json["hint"] = hint
    _timings["total_ms"] = round((time.perf_counter() - _t_total_start) * 1000)
    output_json["timings"] = _timings
    click.echo(json.dumps(output_json))
