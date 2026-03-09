import json
import sys

import click

SECTIONS = {
    "commands": (
        "cadtool commands:\n"
        "  init    — Initialize a new cadtool project (creates cadtool.json).\n"
        "  run     — Execute a CadQuery script, produce versioned STEP output.\n"
        "  render  — Render PNG views of an existing STEP file.\n"
        "  export  — Export an existing STEP file to mesh formats (STL, GLB).\n"
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
    "export": (
        "Exporting mesh formats:\n"
        "  cadtool export <step_file> --format stl,glb,obj\n"
        "  cadtool run script.py --output label --export stl,glb,obj\n"
        "\n"
        "  Supported formats: stl, glb, obj\n"
        "  Files are written alongside the STEP file.\n"
        "  In a version directory, meta.json outputs are updated.\n"
        "\n"
        "  In scripts, you can also import directly:\n"
        "    from cadtool.export import export_glb\n"
        "    export_glb(shape, 'output.glb')  # shape must be a TopoDS_Shape\n"
        "\n"
        "  STL export uses CadQuery's built-in exporter.\n"
        "  GLB export handles tessellation automatically — do not use\n"
        "  cadquery.exporters.export() for GLTF/GLB (it is not supported).\n"
    ),
    "helpers": (
        "cadtool.helpers — Organic geometry primitives:\n"
        "  from cadtool.helpers import loft_sections, tapered_sweep, naca_wire, mirror_fuse\n"
        "\n"
        "  loft_sections(sections, smooth=True)\n"
        "    Loft through a list of TopoDS_Wire sections to produce a solid.\n"
        "    sections: list of TopoDS_Wire (minimum 2). smooth: True for smooth, False for ruled.\n"
        "\n"
        "  tapered_sweep(spine, radii)\n"
        "    Loft circular sections along a spine with varying radii.\n"
        "    spine: list of (x,y,z) tuples. radii: list of floats, one per point.\n"
        "\n"
        "  naca_wire(y, le_x, te_x, thickness, profile='0012')\n"
        "    Generate a closed NACA 4-digit airfoil wire at a given Y position.\n"
        "    thickness: max thickness as percentage of chord (e.g. 12).\n"
        "\n"
        "  mirror_fuse(shape, plane='XZ')\n"
        "    Mirror a shape about a coordinate plane (XZ, YZ, or XY) and fuse.\n"
    ),
    "workflow": (
        "Typical workflow:\n"
        "  1. cadtool init --name myproject\n"
        "  2. Write a CadQuery script (script.py) with show_object().\n"
        "  3. cadtool run script.py --output label [--render iso] [--export stl,glb]\n"
        "  4. cadtool render v1_label/output.step --view front,top --zoom 1.5\n"
        "  5. cadtool export v1_label/output.step --format stl,glb\n"
        "  6. cadtool context — review project state.\n"
        "  7. cadtool diff 1 2 — compare versions.\n"
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
