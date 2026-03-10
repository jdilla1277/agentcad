import json
import sys

import click

SECTIONS = {
    "install": (
        "Installation:\n"
        "  Requires Python 3.10-3.12 (OpenCascade bindings do not support 3.13+).\n"
        "\n"
        "  pip install git+https://github.com/jdilla1277/mountain-climber.git#subdirectory=app\n"
        "\n"
        "  This installs cadtool and all dependencies (CadQuery, OpenCascade, Click)\n"
        "  automatically. No separate package installation is needed.\n"
        "\n"
        "  After installing, the 'cadtool' command is available in your shell.\n"
        "  Run 'cadtool docs' to see full documentation.\n"
    ),
    "quickstart": (
        "Quickstart:\n"
        "  1. Initialize a project:\n"
        "     cadtool init --name myproject\n"
        "\n"
        "  2. Write a script (no imports needed — cq is pre-injected):\n"
        "     box = cq.Workplane('XY').box(10, 20, 5)\n"
        "     show_object(box)\n"
        "\n"
        "  3. Run it:\n"
        "     cadtool run script.py --output first_box --render iso\n"
        "\n"
        "  Multi-part example (multiple show_object() calls):\n"
        "     base = cq.Workplane('XY').box(50, 50, 5)\n"
        "     show_object(base)\n"
        "     pin = cq.Workplane('XY').workplane(offset=5).circle(3).extrude(10)\n"
        "     show_object(pin)\n"
        "\n"
        "  When a script has multiple show_object() calls, cadtool auto-compounds\n"
        "  them into a single shape. A warning is included in the JSON output.\n"
        "  For explicit control, use cq.Compound.makeCompound() in your script.\n"
    ),
    "commands": (
        "cadtool commands:\n"
        "  init    — Initialize a new cadtool project (creates cadtool.json).\n"
        "  run     — Execute a CadQuery script, produce versioned STEP output.\n"
        "            Options: --render, --export, --preview (256x256 iso)\n"
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
        "  GLB export auto-colors individual solids — each part gets a distinct\n"
        "  color from a built-in palette for easy identification in 3D viewers.\n"
        "  GLB export handles tessellation automatically — do not use\n"
        "  cadquery.exporters.export() for GLTF/GLB (it is not supported).\n"
    ),
    "validation": (
        "Pre-execution validation:\n"
        "  Before building geometry, 'cadtool run' performs fast static checks.\n"
        "  Failures return immediately (<100ms) with status 'validation_error',\n"
        "  without consuming a version number or creating disk artifacts.\n"
        "\n"
        "  Checks:\n"
        "    syntax_error        Python syntax validation (ast.parse)\n"
        "    show_object_missing Script must call show_object() to surface geometry\n"
        "    import_error        All imported modules must be resolvable\n"
        "\n"
        "  Output format:\n"
        "    {\"command\": \"run\", \"status\": \"validation_error\",\n"
        "     \"checks\": [{\"check\": \"...\", \"severity\": \"error\", \"message\": \"...\"}]}\n"
    ),
    "preamble": (
        "Script preamble (pre-injected names):\n"
        "  Every script executed by 'cadtool run' has the following names\n"
        "  available without any import statements:\n"
        "\n"
        "  cq             cadquery module (import cadquery as cq)\n"
        "  show_object    CQGI's show_object function\n"
        "  loft_sections  cadtool.helpers.loft_sections\n"
        "  tapered_sweep  cadtool.helpers.tapered_sweep\n"
        "  naca_wire      cadtool.helpers.naca_wire\n"
        "  mirror_fuse    cadtool.helpers.mirror_fuse\n"
        "\n"
        "  Explicit imports still work — adding 'import cadquery as cq' is harmless.\n"
        "  For non-standard modules (e.g. OCP.gp), you still need explicit imports.\n"
    ),
    "metrics": (
        "Geometric metrics:\n"
        "  Every successful 'cadtool run' returns a 'metrics' key with:\n"
        "\n"
        "  bounding_box   {x: [min,max], y: [min,max], z: [min,max]}\n"
        "  dimensions     {x: float, y: float, z: float}  (bbox extents)\n"
        "  volume         float (mm^3)\n"
        "  surface_area   float (mm^2)\n"
        "  center_of_mass {x, y, z}\n"
        "  face_count     int\n"
        "  edge_count     int\n"
        "  is_valid       bool (OCP shape validity)\n"
        "\n"
        "  Metrics are also saved in meta.json and compared by 'cadtool diff'.\n"
        "  Use metrics to verify shape correctness without rendering.\n"
        "\n"
        "  Units: cadtool is unit-agnostic. All dimensions, volume, and area\n"
        "  are reported in whatever units your script uses. CadQuery defaults\n"
        "  to millimeters, so volume is mm^3 and area is mm^2 unless you\n"
        "  chose a different convention in your script.\n"
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
        "\n"
        "  Limitations:\n"
        "    tapered_sweep works best with smooth spines. Sharp direction changes\n"
        "    cause cross-section flipping and surface distortion. Workaround:\n"
        "    split the spine into smooth segments, sweep each separately, and\n"
        "    combine with cq.Compound.makeCompound().\n"
        "\n"
        "  Type conversion patterns (helper output -> show_object):\n"
        "    Helpers return raw TopoDS_Shape objects. To use with show_object:\n"
        "      shape = cq.Shape.cast(topo_shape)         # wrap single shape\n"
        "      wp = cq.Workplane('XY').newObject([shape]) # wrap for show_object\n"
        "      show_object(wp)\n"
        "\n"
        "    To combine multiple shapes:\n"
        "      compound = cq.Compound.makeCompound([cq.Shape.cast(s) for s in shapes])\n"
        "      show_object(cq.Workplane('XY').newObject([compound]))\n"
        "\n"
        "    Note: multiple show_object() calls are auto-compounded by cadtool\n"
        "    (see 'cadtool docs quickstart').\n"
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
