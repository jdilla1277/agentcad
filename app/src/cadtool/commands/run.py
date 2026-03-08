import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from cadtool.manifest import MANIFEST_FILE, load_manifest, save_manifest


@click.command()
@click.argument("script")
@click.option("--output", required=True, help="Label for this version.")
def run(script, output):
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

    # Execute CadQuery script via CQGI (before any disk writes)
    from cadquery import cqgi, exporters

    script_source = script_path.read_text()
    try:
        build_result = cqgi.parse(script_source).build()
    except Exception as e:
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": f"Script execution failed: {e}",
        }))
        sys.exit(1)

    if not build_result.success:
        error_msg = str(build_result.exception)
        click.echo(json.dumps({
            "command": "run",
            "status": "error",
            "message": f"Script execution failed: {error_msg}",
        }))
        sys.exit(1)

    # Script succeeded — now create version directory and write files
    versions = manifest.get("versions", [])
    version_num = len(versions) + 1

    label = output
    dir_name = f"v{version_num}_{label}" if label != f"v{version_num}" else label
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True)

    # Copy script into version directory
    shutil.copy2(str(script_path), str(version_dir / "script.py"))

    # Export STEP file
    shape = build_result.results[0].shape
    exporters.export(shape, str(version_dir / "output.step"))

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
    click.echo(json.dumps({
        "command": "run",
        "status": "success",
        "version": version_num,
        "label": label,
        "outputs": {
            "step": f"{dir_name}/output.step",
            "script": f"{dir_name}/script.py",
        },
    }))
