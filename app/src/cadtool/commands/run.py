import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from cadtool.daemon import _default_socket_path, send_request
from cadtool.manifest import MANIFEST_FILE, load_manifest, save_manifest


def _daemon_socket_path():
    return _default_socket_path()


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


def _record_failure(manifest, script_path, label, version_num, error_msg):
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
    click.echo(json.dumps({
        "command": "run",
        "status": "failed",
        "version": version_num,
        "label": label,
        "error": error_msg,
        "path": f"{dir_name}/",
    }))
    sys.exit(1)


@click.command()
@click.argument("script")
@click.option("--output", required=True, help="Label for this version.")
@click.option("--render", default=None, help="Comma-separated views to render (front,back,left,right,top,bottom,iso). 'all' renders front,right,top,iso.")
@click.option("--export", default=None, help="Comma-separated mesh formats to export (stl, glb).")
@click.option("--preview", is_flag=True, default=False, help="Render a quick 256x256 iso preview.")
@click.option("--params", default=None, help="Parameter overrides as key=value,key=value.")
@click.option("--dry-run", is_flag=True, default=False, help="Compute metrics without creating a version or disk artifacts.")
def run(script, output, render, export, preview, params, dry_run):
    """Execute a CadQuery script and produce a versioned STEP file."""
    # Try routing through daemon if available (skip if already inside daemon)
    if not os.environ.get("CADTOOL_DAEMON"):
        argv = ["run", script, "--output", output]
        if render:
            argv.extend(["--render", render])
        if export:
            argv.extend(["--export", export])
        if preview:
            argv.append("--preview")
        if params:
            argv.extend(["--params", params])
        if dry_run:
            argv.append("--dry-run")

        result = send_request(
            {"type": "run", "cwd": str(Path.cwd()), "argv": argv},
            socket_path=_daemon_socket_path(),
        )
        if result is not None:
            output = result.get("output", "")
            # Inject "via": "daemon" so callers know which path handled it
            try:
                data = json.loads(output)
                data["via"] = "daemon"
                output = json.dumps(data)
            except (json.JSONDecodeError, TypeError):
                pass
            if output:
                click.echo(output, nl=False)
            sys.exit(result.get("exit_code", 0))

    # Fallback: direct execution
    manifest = load_manifest(command="run")

    # Python version check (before CadQuery imports)
    if sys.version_info >= (3, 13):
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": (
                f"cadtool requires Python 3.10-3.12 "
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

    # Pre-execution validation (before version allocation)
    from cadtool.validate import validate_script

    raw_source = script_path.read_text()
    PREAMBLE = "import cadquery as cq; from cadtool.helpers import loft_sections, tapered_sweep, naca_wire, mirror_fuse, translate, rotate, bbox_point, place_at, assemble, ellipse_wire, spline_wire, polygon_wire, rounded_rect_wire, elliptical_sweep, involute_gear_profile\n"
    script_source = PREAMBLE + raw_source

    validation_errors = validate_script(script_source)
    if validation_errors:
        click.echo(json.dumps({
            "command": "run",
            "status": "validation_error",
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

    # Parse the script model (before version allocation to catch param errors)
    from cadquery import cqgi, exporters
    from cadquery.cqgi import InvalidParameterError

    model = cqgi.parse(script_source)

    # Validate parameter names before version allocation
    if parsed_params:
        available = set(model.metadata.parameters.keys())
        unknown = set(parsed_params.keys()) - available
        if unknown:
            click.echo(json.dumps({
                "command": "run",
                "status": "error",
                "message": (
                    f"Unknown parameter(s): {', '.join(sorted(unknown))}. "
                    f"Available: {', '.join(sorted(available)) if available else '(none)'}"
                ),
            }))
            sys.exit(1)

    # Determine version number before execution (failures consume a number)
    versions = manifest.get("versions", [])
    version_num = len(versions) + 1
    label = output

    # Execute CadQuery script via CQGI
    try:
        build_result = model.build(build_parameters=parsed_params)
    except InvalidParameterError as e:
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": str(e),
        }))
        sys.exit(1)
    except Exception as e:
        _record_failure(manifest, script_path, label, version_num,
                        _enrich_error(f"Script execution failed: {e}"))

    if not build_result.success:
        _record_failure(manifest, script_path, label, version_num,
                        _enrich_error(f"Script execution failed: {build_result.exception}"))

    if not build_result.results:
        _record_failure(manifest, script_path, label, version_num,
                        "Script produced no results. Did you call show_object()?")

    # Extract shape(s) — auto-compound if multiple show_object() calls
    import cadquery as cq

    warnings = []
    if len(build_result.results) == 1:
        shape = build_result.results[0].shape
    else:
        shapes = []
        for r in build_result.results:
            s = r.shape
            if hasattr(s, 'val'):
                shapes.append(s.val())
            else:
                shapes.append(cq.Shape.cast(s))
        shape = cq.Workplane("XY").newObject([cq.Compound.makeCompound(shapes)])
        warnings.append(
            f"{len(build_result.results)} show_object() calls detected, "
            "results combined into a single compound. "
            "Consider using cq.Compound.makeCompound() in your script instead."
        )

    # Compute geometric metrics
    from cadtool.metrics import compute_metrics

    topo_shape_for_metrics = shape.val().wrapped
    metrics = compute_metrics(topo_shape_for_metrics)

    # Surface validity issues as top-level warnings
    if not metrics.get("is_valid", True):
        warnings.append(
            "Invalid geometry detected (is_valid: false). "
            "Run 'cadtool inspect' on the STEP file for diagnostic details."
        )
    if metrics.get("warnings"):
        warnings.extend(metrics["warnings"])

    # Dry-run: return metrics only, no version/disk artifacts
    if dry_run:
        output_json = {
            "command": "run",
            "status": "success",
            "metrics": metrics,
        }
        if warnings:
            output_json["warnings"] = warnings
        click.echo(json.dumps(output_json))
        return

    # Script succeeded — create version directory and write files
    dir_name = f"v{version_num}_{label}" if label != f"v{version_num}" else label
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True, exist_ok=True)

    # Copy script into version directory
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    # Export STEP file
    exporters.export(shape, str(version_dir / "output.step"))

    # Export mesh formats if requested
    exports_meta = {}
    if export:
        formats = [f.strip() for f in export.split(",")]
        topo_shape = shape.val().wrapped
        for fmt in formats:
            if fmt == "stl":
                stl_path = version_dir / "output.stl"
                exporters.export(shape, str(stl_path), exportType="STL")
                exports_meta["stl"] = f"{dir_name}/output.stl"
            elif fmt == "glb":
                from cadtool.export import export_glb

                glb_path = version_dir / "output.glb"
                export_glb(topo_shape, str(glb_path))
                exports_meta["glb"] = f"{dir_name}/output.glb"
            elif fmt == "obj":
                from cadtool.export import export_obj

                obj_path = version_dir / "output.obj"
                export_obj(topo_shape, str(obj_path))
                exports_meta["obj"] = f"{dir_name}/output.obj"

    # Render views if requested
    renders_meta = {}
    if render:
        from cadtool.render import (
            parse_view_spec, render_shape as render_shape_view,
            render_shape_custom, render_views, ALL_VIEWS,
        )

        renders_dir = version_dir / "renders"
        renders_dir.mkdir(parents=True, exist_ok=True)
        topo_shape = shape.val().wrapped
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

    # Quick preview if requested
    preview_meta = None
    if preview:
        from cadtool.render import render_shape
        preview_path = version_dir / "preview.png"
        render_shape(topo_shape_for_metrics, "iso", preview_path, width=256, height=256)
        preview_meta = f"{dir_name}/preview.png"

    # Write meta.json
    created = datetime.now(timezone.utc).isoformat()
    meta = {
        "version": version_num,
        "label": label,
        "status": "success",
        "created": created,
        "script": f"{dir_name}/script.py",
        "outputs": {
            "step": f"{dir_name}/output.step",
            **exports_meta,
        },
    }
    meta["metrics"] = metrics
    if parsed_params:
        meta["params"] = parsed_params
    if warnings:
        meta["warnings"] = warnings
    if preview_meta:
        meta["preview"] = preview_meta
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

    # Output success JSON
    output_json = {
        "command": "run",
        "status": "success",
        "version": version_num,
        "label": label,
        "outputs": {
            "step": f"{dir_name}/output.step",
            "script": f"{dir_name}/script.py",
            **exports_meta,
        },
    }
    output_json["metrics"] = metrics
    if parsed_params:
        output_json["params"] = parsed_params
    if warnings:
        output_json["warnings"] = warnings
    if preview_meta:
        output_json["preview"] = preview_meta
    if renders_meta:
        output_json["renders"] = renders_meta
    click.echo(json.dumps(output_json))
