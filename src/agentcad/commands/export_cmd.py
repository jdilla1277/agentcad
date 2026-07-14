import json
import re
import sys
from pathlib import Path

import click

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)

VALID_FORMATS = {"stl", "glb", "obj"}


def parse_export_formats(formats: str) -> list[str]:
    """Split a comma-separated mesh-format value into trimmed, non-empty
    entries. Dropping empties means a trailing comma (e.g. ``stl,``) doesn't
    leave a blank that gets silently ignored downstream."""
    return [f.strip() for f in formats.split(",") if f.strip()]


def unsupported_export_formats(formats: str) -> list[str]:
    """Return the requested formats that aren't in :data:`VALID_FORMATS`.
    Shared by ``agentcad export`` and ``agentcad run --export`` so both
    reject unknown formats identically."""
    return [f for f in parse_export_formats(formats) if f not in VALID_FORMATS]


def _is_version_dir(directory):
    """Check if directory matches v\\d+_\\w+ pattern and contains meta.json."""
    return bool(re.match(r"v\d+_\w+", directory.name)) and (directory / "meta.json").exists()


@click.command("export")
@click.argument("step_file")
@click.option("--format", "formats", required=True, help="Comma-separated mesh formats: stl, glb, obj")
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
def export_cmd(step_file, formats, no_daemon):
    """Export a STEP file to mesh formats (STL, GLB, OBJ)."""
    # Try routing through daemon. Exits before returning if reachable.
    maybe_route_through_daemon(
        ["export", step_file, "--format", formats],
        no_daemon=no_daemon,
    )

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
            "message": f"Unsupported format(s): {', '.join(invalid)}. Supported: stl, glb, obj",
        }))
        sys.exit(1)

    # Import STEP (silencer + clean errors via shared helper)
    import cadquery as cq
    from cadquery import exporters
    from agentcad.step_io import load_cad_shape

    try:
        topo_shape = load_cad_shape(step_path)
    except ValueError as exc:
        click.echo(json.dumps({
            "command": "export",
            "status": "malformed",
            "message": str(exc),
            "suggestion": "Re-export from your CAD tool; the file may be incomplete or corrupted.",
        }))
        sys.exit(1)
    # Wrap into a Workplane for cadquery's STL exporter (which expects a wp).
    wp = cq.Workplane().newObject([cq.Shape.cast(topo_shape)])

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
            from agentcad.export import export_glb
            export_glb(topo_shape, str(out_path))
        elif fmt == "obj":
            from agentcad.export import export_obj
            export_obj(topo_shape, str(out_path))
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

    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
