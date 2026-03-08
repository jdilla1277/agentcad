import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from cadtool.manifest import MANIFEST_FILE, load_manifest, save_manifest


def _record_failure(manifest, script_path, label, version_num, error_msg):
    """Record a script failure on disk and in the manifest."""
    dir_name = f"v{version_num}_{label}_failed"
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True)

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
@click.option("--render", default=None, help="Comma-separated views to render (front,back,left,right,top,bottom,iso,all).")
def run(script, output, render):
    """Execute a CadQuery script and produce a versioned STEP file."""
    manifest = load_manifest(command="run")

    script_path = Path(script)
    if not script_path.exists():
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": f"Script file '{script}' not found",
        }))
        sys.exit(1)

    # Determine version number before execution (failures consume a number)
    versions = manifest.get("versions", [])
    version_num = len(versions) + 1
    label = output

    # Execute CadQuery script via CQGI
    from cadquery import cqgi, exporters

    script_source = script_path.read_text()
    try:
        build_result = cqgi.parse(script_source).build()
    except Exception as e:
        _record_failure(manifest, script_path, label, version_num,
                        f"Script execution failed: {e}")

    if not build_result.success:
        _record_failure(manifest, script_path, label, version_num,
                        f"Script execution failed: {build_result.exception}")

    if not build_result.results:
        _record_failure(manifest, script_path, label, version_num,
                        "Script produced no results. Did you call show_object()?")

    # Script succeeded — create version directory and write files
    dir_name = f"v{version_num}_{label}" if label != f"v{version_num}" else label
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True)

    # Copy script into version directory
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    # Export STEP file
    shape = build_result.results[0].shape
    exporters.export(shape, str(version_dir / "output.step"))

    # Render views if requested
    renders_meta = {}
    if render:
        from cadtool.render import render_views, ALL_VIEWS

        view_names = ALL_VIEWS if render == "all" else [v.strip() for v in render.split(",")]
        renders_dir = version_dir / "renders"
        topo_shape = shape.val().wrapped
        rendered = render_views(topo_shape, view_names, renders_dir)
        for view_name, abs_path in rendered.items():
            renders_meta[view_name] = f"{dir_name}/renders/{view_name}.png"

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
        },
    }
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
        },
    }
    if renders_meta:
        output_json["renders"] = renders_meta
    click.echo(json.dumps(output_json))
