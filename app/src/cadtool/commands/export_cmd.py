import json
import re
import sys
from pathlib import Path

import click

VALID_FORMATS = {"stl", "glb"}


def _is_version_dir(directory):
    """Check if directory matches v\\d+_\\w+ pattern and contains meta.json."""
    return bool(re.match(r"v\d+_\w+", directory.name)) and (directory / "meta.json").exists()


@click.command("export")
@click.argument("step_file")
@click.option("--format", "formats", required=True, help="Comma-separated mesh formats: stl, glb")
def export_cmd(step_file, formats):
    """Export a STEP file to mesh formats (STL, GLB)."""
    step_path = Path(step_file)
    if not step_path.exists():
        click.echo(json.dumps({
            "command": "export",
            "status": "error",
            "message": f"STEP file '{step_file}' not found",
        }))
        sys.exit(1)

    # Parse and validate formats
    fmt_list = [f.strip() for f in formats.split(",")]
    invalid = [f for f in fmt_list if f not in VALID_FORMATS]
    if invalid:
        click.echo(json.dumps({
            "command": "export",
            "status": "error",
            "message": f"Unsupported format(s): {', '.join(invalid)}. Supported: stl, glb",
        }))
        sys.exit(1)

    # Import STEP
    from cadquery import exporters, importers

    wp = importers.importStep(str(step_path))
    topo_shape = wp.val().wrapped

    # Determine output directory
    parent_dir = step_path.parent
    stem = step_path.stem  # e.g. "output"

    # Export each format
    outputs = {}
    for fmt in fmt_list:
        out_path = parent_dir / f"{stem}.{fmt}"
        if fmt == "stl":
            exporters.export(wp, str(out_path), exportType="STL")
        elif fmt == "glb":
            from cadtool.export import export_glb
            export_glb(topo_shape, str(out_path))
        outputs[fmt] = str(out_path)

    # Update meta.json if in a version directory
    if _is_version_dir(parent_dir):
        meta_path = parent_dir / "meta.json"
        meta = json.loads(meta_path.read_text())
        existing_outputs = meta.get("outputs", {})
        existing_outputs.update({
            fmt: f"{parent_dir.name}/{stem}.{fmt}"
            for fmt in fmt_list
        })
        meta["outputs"] = existing_outputs
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    click.echo(json.dumps({
        "command": "export",
        "status": "success",
        "outputs": outputs,
    }))
