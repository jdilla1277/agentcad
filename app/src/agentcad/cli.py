import json
import os
import sys
from pathlib import Path

import click

from agentcad.session_log import SessionLogger
from agentcad.commands.context import context
from agentcad.commands.daemon_cmd import daemon
from agentcad.commands.diff import diff
from agentcad.commands.docs import docs
from agentcad.commands.export_cmd import export_cmd
from agentcad.commands.feedback import feedback
from agentcad.commands.init import init
from agentcad.commands.inspect_cmd import inspect_cmd
from agentcad.commands.render import render
from agentcad.commands.run import run
from agentcad.commands.view import view


_BRIEFING = """\b
EXAMPLE SESSION
  $ agentcad init --name myproject
  {"command": "init", "status": "success", "project": "myproject"}
\b
  Write a script — no imports needed, cq and show_object are pre-injected:
    box = cq.Workplane('XY').box(10, 20, 5)
    show_object(box)
\b
  $ agentcad run box.py --output first --render iso --preview
  {"command": "run", "status": "success", "version": 1, "label": "first",
   "outputs": {"step": "v1_first/output.step", "script": "v1_first/script.py"},
   "metrics": {"dimensions": {"x": 10.0, "y": 20.0, "z": 5.0},
               "volume": 1000.0, "surface_area": 700.0, "face_count": 6,
               "edge_count": 12, "is_valid": true, ...},
   "preview": "v1_first/preview.png",
   "renders": {"iso": "v1_first/renders/iso.png"}}

\b
  Version directory layout:
    v1_first/
      output.step       STEP geometry
      script.py         copy of the executed script
      meta.json         full run metadata
      preview.png       256x256 iso preview (when --preview used)
      renders/          PNG views (when --render used)
        iso.png

\b
WRITING SCRIPTS
  show_object(result) is required — it tells agentcad what geometry to output.
  These names are pre-injected (no import needed):
    cq             cadquery module
    show_object    surfaces geometry to agentcad (required — at least one call)
    translate      translate(shape, x, y, z)
    rotate         rotate(shape, axis, angle_deg) — axis: 'X'/'Y'/'Z'
                   Right-hand rule: positive = counterclockwise from + axis
    mirror_fuse    mirror_fuse(shape, plane='XZ') — mirror + boolean fuse
    loft_sections  loft_sections(sections, smooth=True) — loft wires into solid
    tapered_sweep  tapered_sweep(spine, radii) — circles along spine
    naca_wire      naca_wire(y, le_x, te_x, thickness, profile='0012')

\b
  .val().wrapped — extracting a TopoDS_Shape from CadQuery's fluent API:
    CadQuery methods return Workplane objects. The helpers (translate, rotate,
    etc.) operate on raw TopoDS_Shape. To bridge them:
      part = cq.Workplane('XY').box(10, 20, 5).val().wrapped  # -> TopoDS_Shape
      moved = translate(part, 50, 0, 0)                       # -> TopoDS_Shape

\b
  Showing helper output:
    Helpers return TopoDS_Shape. To pass back to show_object:
      shape = cq.Shape.cast(topo_shape)
      show_object(cq.Workplane('XY').newObject([shape]))
    Or use multiple show_object() calls — agentcad auto-compounds them.

\b
  Explicit imports still work (adding 'import cadquery as cq' is harmless).
  For OCP internals (e.g. OCP.gp, OCP.BRepPrimAPI), import manually.

\b
COMMANDS
  agentcad init [--name NAME]
    Initialize project. Creates agentcad.json manifest.

\b
  agentcad run SCRIPT --output LABEL [flags]
    Execute script, produce versioned STEP + metrics.
    --render VIEWS   PNG views: front,back,left,right,top,bottom,iso,
                     'all', custom angle az:el (e.g. 45:30),
                     or mixed (front,right,45:30).
    --export FMT     Mesh export: stl, glb (GLB auto-colors per-solid).
    --preview        Quick 256x256 iso PNG.
    --params K=V,..  Override top-level script constants.
    --dry-run        Metrics only — no version consumed, no disk artifacts.

\b
  agentcad render STEP --view SPEC [--zoom N] [--focus x,y,z] [--no-fit] [--name LABEL]
    Render PNG views of an existing STEP file. Same view spec as --render.
    Use this for post-hoc rendering with camera control (zoom, focus).

\b
  agentcad export STEP --format stl,glb,obj
    Export STEP to mesh formats. GLB auto-colors individual solids.

\b
  agentcad inspect STEP
    Topology report: solid_count, shell_count, shells (open/closed + face
    count per shell), face_count, face_orientations (forward/reversed),
    edge_count, free_edge_count, is_valid.

\b
  agentcad diff REF1 REF2        Compare versions (by number or label).
  agentcad context               Project state: versions, current, tool_version.
  agentcad view FILE             Open GLB/STEP in browser (three.js). STEP auto-converts.
  agentcad daemon start|stop|status   Background worker — eliminates 3-5s cold start.
  agentcad docs [SECTION]        Deep-dive docs (15 sections).

\b
RESPONSE SCHEMA
  Every command returns JSON with "command" and "status" keys.
    "success"          — completed normally.
    "failed"           — script error. Version IS consumed. Creates v{N}_{label}_failed/.
    "error"            — CLI error (bad args, missing file). No version consumed. No disk artifacts.
    "validation_error" — static check failed (syntax, missing show_object, bad import).
                         No version consumed. No disk artifacts. Instant (<100ms).

\b
METRICS (in every successful run response)
  bounding_box   {x: [min,max], y: [min,max], z: [min,max]}
  dimensions     {x, y, z}       bbox extents
  volume         float            unit-agnostic (CadQuery defaults mm -> mm^3)
  surface_area   float
  center_of_mass {x, y, z}
  face_count     int              unique faces
  edge_count     int              unique edges
  is_valid       bool             BRepCheck shape validity
  Tip: verify geometry from metrics alone — check volume, dimensions, face_count
  before rendering. Use 'agentcad diff' to compare metrics across versions.

\b
PARAMETRIC SCRIPTS
  Top-level assignments become overridable parameters:
    length = 50.0
    width = 20.0
    result = cq.Workplane('XY').box(length, width, 10)
    show_object(result)
  $ agentcad run script.py --output v2 --params length=100,width=30
  Types auto-coerced: bool ('true'/'false') > int > float > string.
  Unknown param name -> "error" status with available names (no version consumed).

\b
CADQUERY PATTERNS
  Build at origin, then position:
    part = cq.Workplane('XY').box(10, 20, 5).val().wrapped
    placed = translate(part, 50, 0, 0)
\b
  Angled positioning (build along Z, rotate, translate to attachment):
    arm = cq.Workplane('XY').circle(5).extrude(120).val().wrapped
    tilted = rotate(arm, 'Y', 30)
    placed = translate(tilted, 0, 0, 8)
\b
  Compound vs Union:
    cq.Compound.makeCompound([...])  spatial grouping, parts stay separate
    .union()                         boolean fuse into single solid
\b
  Revolve: axis is relative to workplane origin, not sketch pen position.
    .move() shifts pen without moving origin — trap. Use .transformed(offset=(...)).
\b
  Workplane stacking:
    .transformed(offset=(x,y,z))  shift origin in local coords
    .workplane(offset=N)          stack along normal
    .center(x, y)                 move pen relative to origin
    .move(x, y)                   move pen only (origin stays)
\b
  twistExtrude performance:
    For profiles with >100 points, use polyline() instead of spline().
    1000-point spline + twistExtrude can take 5+ minutes; polyline < 1 min.

\b
DEBUGGING
  Geometry wrong? Check metrics first — volume and dimensions catch most issues.
  $ agentcad run script.py --output test --dry-run        # metrics, no disk artifacts
  $ agentcad inspect v1_test/output.step                  # topology deep-dive
    Hollow shape?     -> free_edge_count > 0, shell not closed
    Inverted normals? -> face_orientations imbalanced
    Invalid?          -> is_valid: false
  $ agentcad render v1_test/output.step --view all        # visual from 4 angles
  Then iterate: fix script, run with new --output label.
"""


class _LoggingGroup(click.Group):
    """Click Group that auto-logs every command invocation to session.jsonl."""

    def invoke(self, ctx):
        captured = []
        original_echo = click.echo

        def _capturing_echo(message=None, **kwargs):
            if message is not None:
                captured.append(str(message))
            original_echo(message, **kwargs)

        click.echo = _capturing_echo
        try:
            return super().invoke(ctx)
        finally:
            click.echo = original_echo
            self._log_session(ctx, captured)

    def _log_session(self, ctx, captured):
        if os.environ.get("AGENTCAD_NO_LOG"):
            return
        # Find the subcommand name and args
        cmd_name = ctx.invoked_subcommand
        if not cmd_name or cmd_name == "feedback":
            return
        # Parse the last JSON output (commands may echo multiple things)
        result = {}
        for line in reversed(captured):
            try:
                result = json.loads(line)
                break
            except (json.JSONDecodeError, TypeError):
                continue
        # Collect the raw args from sys.argv
        args = sys.argv[2:] if len(sys.argv) > 2 else []
        try:
            logger = SessionLogger(Path.cwd())
            logger.log(cmd_name, {"argv": args}, result)
        except Exception:
            pass  # Never let logging break the CLI


@click.group(
    cls=_LoggingGroup,
    epilog=_BRIEFING,
    context_settings=dict(max_content_width=120),
)
def cli():
    """agentcad — CLI CAD tool for AI agents. All output is JSON."""


cli.add_command(context)
cli.add_command(daemon)
cli.add_command(diff)
cli.add_command(docs)
cli.add_command(export_cmd)
cli.add_command(feedback)
cli.add_command(init)
cli.add_command(inspect_cmd)
cli.add_command(render)
cli.add_command(run)
cli.add_command(view)
