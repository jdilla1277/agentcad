import json
import sys

import click

SECTIONS = {
    "commands": (
        "cadtool commands:\n"
        "  init    — Initialize a new cadtool project (creates cadtool.json).\n"
        "  run     — Execute a CadQuery script, produce versioned STEP output.\n"
        "  render  — Render PNG views of an existing STEP file.\n"
        "  context — Show the current project state (versions, current label).\n"
        "  docs    — Show this documentation.\n"
        "  diff    — Compare two versions of a model.\n"
    ),
    "render": (
        "Rendering:\n"
        "  cadtool render <step_file> --view <spec> [--zoom N] [--name label] "
        "[--focus x,y,z] [--no-fit]\n"
        "\n"
        "  --view   Named views (front, back, left, right, top, bottom, iso),\n"
        "           comma-separated, 'all', or custom 'azimuth,elevation'.\n"
        "  --zoom   Zoom factor applied after FitAll (default 1.0).\n"
        "  --focus  Camera target point as 'x,y,z'.\n"
        "  --no-fit Skip FitAll (requires --focus).\n"
        "  --name   Custom filename for single-view renders.\n"
    ),
    "schema": (
        "Response schema:\n"
        "  All commands return JSON with 'command' and 'status' fields.\n"
        "\n"
        "  success — The command completed normally. Additional fields vary by command.\n"
        "  failed  — A script execution failed (e.g. CadQuery error, no show_object).\n"
        "            Includes 'error' field with details. A version directory is created.\n"
        "  error   — A CLI-level error (missing file, bad arguments, no manifest).\n"
        "            Includes 'message' field. No disk artifacts are created.\n"
    ),
    "workflow": (
        "Typical workflow:\n"
        "  1. cadtool init --name myproject\n"
        "  2. Write a CadQuery script (script.py) with show_object().\n"
        "  3. cadtool run script.py --output label [--render iso] [--export stl,glb]\n"
        "  4. cadtool render v1_label/output.step --view front,top --zoom 1.5\n"
        "  5. cadtool context — review project state.\n"
        "  6. cadtool diff 1 2 — compare versions.\n"
    ),
}


@click.command()
@click.argument("section", required=False, default=None)
def docs(section):
    """Show cadtool documentation."""
    if section is not None:
        if section not in SECTIONS:
            click.echo(json.dumps({
                "command": "docs",
                "status": "error",
                "message": f"Unknown section '{section}'. "
                           f"Available: {', '.join(sorted(SECTIONS))}",
            }))
            sys.exit(1)

        click.echo(json.dumps({
            "command": "docs",
            "status": "success",
            "section": section,
            "content": SECTIONS[section],
        }))
        return

    # No section — return full documentation
    full_content = "\n".join(SECTIONS.values())

    click.echo(json.dumps({
        "command": "docs",
        "status": "success",
        "sections": sorted(SECTIONS.keys()),
        "content": full_content,
    }))
