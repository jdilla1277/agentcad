import json
import os
import sys
from pathlib import Path

import click

from agentcad.session_log import SessionLogger
from agentcad.commands.check_spec import check_spec
from agentcad.commands.context import context
from agentcad.commands.daemon_cmd import daemon
from agentcad.commands.diff import diff
from agentcad.commands.docs import docs
from agentcad.commands.export_cmd import export_cmd
from agentcad.commands.feedback import feedback
from agentcad.commands.import_cmd import import_cmd
from agentcad.commands.init import init
from agentcad.commands.inspect_cmd import inspect_cmd
from agentcad.commands.measure import measure
from agentcad.commands.parts import parts_cmd
from agentcad.commands.render import render
from agentcad.commands.run import run
from agentcad.commands.skill import skill
from agentcad.commands.subscribe import subscribe
from agentcad.commands.view import view


# Use __RUNTIME__ as a placeholder so the EXAMPLE SESSION's `"runtime": "..."`
# line can be substituted at help-render time. _LoggingGroup.format_epilog
# detects the project's runtime via dispatch.project_runtime() and inserts
# either "cadquery" or "build123d" so a fresh agent in a build123d project
# doesn't read this and write `cq.Workplane(...)` syntax that won't work.
_BRIEFING_TEMPLATE = """\b
SCRIPT WRITING
  agentcad runs Python scripts that call show_object() to surface geometry.
  The script-writing API depends on the project's engine (cadquery or
  build123d). Run:

    $ agentcad init --name myproject [--runtime cadquery|build123d]
    $ agentcad docs preamble     # what names are pre-injected into scripts
    $ agentcad docs quickstart   # first-script walkthrough
    $ agentcad docs examples     # worked examples (build123d projects only)
    $ agentcad docs patterns     # idioms + footguns

  The docs follow the project's --runtime automatically. Pass --runtime
  to docs for a one-off override. With no project pinned, docs use the
  global default (build123d).

\b
CHOOSING A RUNTIME
  Same shape, both ways:
    cadquery:    box = cq.Workplane('XY').box(10, 20, 5); show_object(box)
    build123d:   box = Box(10, 20, 5); show_object(box)

  build123d is recommended for new projects (direct primitives, Python
  operators for booleans). cadquery is the legacy supported runtime — pick it when
  porting existing scripts.

  Dispatch order: --runtime flag > script imports (`import cadquery` /
  `from build123d`) > project's agentcad.json `runtime` > build123d
  (global default). See `agentcad docs runtimes` for the full picture.

\b
EXAMPLE SESSION
  $ agentcad init --name myproject --runtime __RUNTIME__
  {"command": "init", "status": "success", "project": "myproject"}
\b
  # Write script.py (see 'agentcad docs quickstart'), then:
  $ agentcad run script.py --output first --render iso
  {"command": "run", "status": "success", "runtime": "__RUNTIME__",
   "version": 1, "label": "first",
   "outputs": {"step": "v1_first/output.step", "script": "v1_first/script.py"},
   "viewer": "v1_first/viewer.html", "viewer_glb": "v1_first/output.glb",
   "metrics": {"dimensions": {"x": 10.0, "y": 20.0, "z": 5.0},
               "volume": 1000.0, "is_valid": true, ...},
   "preview": "v1_first/preview.png"}

\b
  Version directory layout:
    v1_first/
      output.step       STEP geometry
      output.glb        GLB backing viewer.html (always)
      script.py         copy of the executed script
      meta.json         full run metadata (includes "runtime" field)
      preview.png       4-view composite (front/right/top/iso, 1024x1068)
      diff_side.png     side-by-side vs. prior version (from v2 onward)
      diff_overlay.png  tinted overlay vs. prior version (from v2 onward)
      viewer.html       interactive 3D viewer (always — open in a browser)
      renders/          PNG views (when --render used)

\b
COMMANDS
  agentcad init [--name NAME] [--runtime cadquery|build123d]
    Initialize project. --runtime pins the CAD engine for this project so
    subsequent `agentcad run` and `agentcad docs` default to it.

\b
  agentcad run SCRIPT --output LABEL [flags]
    Execute script, produce versioned STEP + metrics.
    --render VIEWS   PNG views: front,back,left,right,top,bottom,iso,
                     'all', custom angle az:el (e.g. 45:30),
                     or mixed (front,right,45:30).
    --export FMT     Mesh export: stl, glb (GLB auto-colors per-solid).
                     `outputs.glb` appears only for explicit --export glb;
                     `viewer_glb` always points at viewer.html's GLB.
    --preview / --no-preview
                     4-view composite PNG + per-part previews (default on,
                     ~2-4s). viewer.html, GLB, and diff PNGs always
                     generate regardless — --no-preview only skips the
                     composite render. Turntable GIFs are on-demand via
                     the Export GIF button in viewer.html.
    --params K=V,..  Override top-level script constants.
    --runtime ENGINE Force cadquery or build123d (beats project default).
    --dry-run        Metrics only — no version consumed, no disk artifacts.

\b
  agentcad render STEP --view SPEC [--zoom N] [--focus x,y,z] [--no-fit] [--name LABEL]
    Render PNG views of an existing STEP file. Same view spec as --render.

\b
  agentcad export STEP --format stl,glb,obj
    Export STEP to mesh formats. GLB auto-colors individual solids.

\b
  agentcad inspect STEP [--ids|--summary] [--limit N|--no-limit]
    Topology report: solid_count, shell_count, shells (open/closed + face
    count per shell), face_count, face_orientations (forward/reversed),
    edge_count, free_edge_count, is_valid. --ids is capped by default.

\b
  agentcad measure STEP [--features] [--cylinders-only] [--limit N|--no-limit]
    Dimensional report: overall metrics plus compact feature measurements
    (edge lengths, face areas, circular/cylindrical radii and diameters).
    Full feature lists are capped by default; use --no-limit only when needed.

\b
  agentcad check-spec STEP SPEC.json
    Compare measured cylindrical features against an explicit JSON spec.
    Reports pass/fail, matched features, missing features, and count errors.

\b
  agentcad parts list REF       List named/captured parts for a version.
  agentcad parts show REF ID    Show one part from that version by stable id.
  agentcad parts view REF [--isolate ID] [--hide ID] [--ghost-rest] [--focus ID]
                                Generate a reproducible part review viewer.
                                Uses the same meta.json snapshot as viewer.html.
  agentcad view FILE [--measure] [--spec spec.json]
                                Open GLB/STEP in browser. STEP auto-converts.
                                Review mode needs STEP/STP source geometry.
  agentcad diff REF1 REF2        Compare versions (by number or label).
  agentcad context               Project state: versions, current, tool_version.
  agentcad docs [SECTION] [--runtime ENGINE]
                                 Engine-specific docs. Defaults to project runtime.

\b
RESPONSE SCHEMA
  Every command returns JSON with "command" and "status" keys.
  Successful `run` responses also include "runtime" (cadquery or build123d).
    "success"          — completed normally.
    "failed"           — script error. Version IS consumed. Creates v{N}_{label}_failed/.
    "error"            — CLI error (bad args, missing file, ambiguous runtime).
                         No version consumed. No disk artifacts.
    "validation_error" — static check failed (syntax, missing show_object, bad import).
                         No version consumed. No disk artifacts. Instant (<100ms).

\b
METRICS (in every successful run response)
  bounding_box   {x: [min,max], y: [min,max], z: [min,max]}
  dimensions     {x, y, z}       bbox extents
  volume         float            unit-agnostic (mm defaults -> mm^3)
  surface_area   float
  center_of_mass {x, y, z}
  face_count     int              unique faces
  edge_count     int              unique edges
  is_valid       bool             BRepCheck shape validity
  Tip: verify geometry from metrics alone — check volume, dimensions, face_count
  before rendering. Use 'agentcad diff' to compare metrics across versions.

\b
SPEC AND MEASUREMENT CHECKS
  Visual renders are not enough for dimensional work. When the prompt has
  explicit holes, bores, diameters, counts, or overall dimensions:
    1. Run normally and inspect the generated STEP.
    2. Use `agentcad measure` to read dimensions and feature buckets from CAD.
    3. Use `agentcad check-spec` when you have an explicit JSON checklist.
    4. Revise before marking the model done if measurements or spec rows fail.

\b
DEBUGGING
  Geometry wrong? Check metrics first — volume and dimensions catch most issues.
  $ agentcad run script.py --output test --dry-run        # metrics, no disk artifacts
  $ agentcad measure v1_test/output.step                  # dimensions + feature sizes
  $ agentcad check-spec v1_test/output.step spec.json     # compare against intended features
  $ agentcad inspect v1_test/output.step                  # topology deep-dive
    Hollow shape?     -> free_edge_count > 0, shell not closed
    Inverted normals? -> face_orientations imbalanced
    Invalid?          -> is_valid: false
  $ agentcad render v1_test/output.step --view all        # visual from 4 angles

\b
MCP INTEGRATION
  For native tool integration with Claude Code, Cursor, Windsurf, or any
  MCP-compatible agent, install the MCP extra and add to your .mcp.json:

    pip install agentcad[mcp]
    {"agentcad": {"command": "python", "args": ["-m", "agentcad.mcp"]}}

  This exposes all agentcad commands as native agent tools.
"""


def _build_briefing(runtime: str = "build123d") -> str:
    return _BRIEFING_TEMPLATE.replace("__RUNTIME__", runtime)


class _LoggingGroup(click.Group):
    """Click Group that auto-logs every command invocation to session.jsonl."""

    def format_epilog(self, ctx, formatter):
        # Pin EXAMPLE SESSION's runtime to the project's runtime or global
        # default. Mirrors `agentcad docs`'s detection
        # so --help and docs stay in sync from a fresh agent's perspective.
        from agentcad.runners.dispatch import project_runtime, DEFAULT_RUNTIME
        rt = project_runtime() or DEFAULT_RUNTIME
        original = self.epilog
        try:
            self.epilog = _build_briefing(rt)
            super().format_epilog(ctx, formatter)
        finally:
            self.epilog = original

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
        if not cmd_name or cmd_name in ("feedback", "subscribe"):
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
    epilog=_BRIEFING_TEMPLATE,  # Replaced by format_epilog at render time.
    context_settings=dict(max_content_width=120),
)
def cli():
    """agentcad — CLI CAD tool for AI agents. All output is JSON."""


cli.add_command(context)
cli.add_command(check_spec)
cli.add_command(daemon)
cli.add_command(diff)
cli.add_command(docs)
cli.add_command(export_cmd)
cli.add_command(feedback)
cli.add_command(import_cmd)
cli.add_command(init)
cli.add_command(inspect_cmd)
cli.add_command(measure)
cli.add_command(parts_cmd)
cli.add_command(render)
cli.add_command(run)
cli.add_command(skill)
cli.add_command(subscribe)
cli.add_command(view)
