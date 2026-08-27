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
from agentcad.commands.instructions import instructions
from agentcad.commands.inspect_cmd import inspect_cmd
from agentcad.commands.measure import measure
from agentcad.commands.parts import parts_cmd
from agentcad.commands.render import render
from agentcad.commands.recover import recover
from agentcad.commands.run import _OUTPUT_DEPRECATION, run
from agentcad.commands.skill import skill
from agentcad.commands.subscribe import subscribe
from agentcad.commands.view import view


_SCRIPT_EDIT_HELPERS = {
    "boss",
    "chamfer_edges",
    "cut_pocket",
    "fillet_edges",
    "load_step",
    "load_step_shape",
    "pick_edge",
    "pick_face",
    "shell_faces",
    "split_by_plane",
}


# Runtime placeholders keep the how-to guide aligned with the current project.
# A fresh/default project gets one build123d authoring guide; a CadQuery-pinned
# project gets a compatibility-only guide.
_GUIDE_TEMPLATE = """\
__AUTHORING_GUIDE__

QUICK START WORKFLOW
  1. Write script.py using the authoring API above and surface geometry with
     show_object(). Check metrics without consuming a version:
       $ agentcad run script.py --label test --dry-run
  2. Run for real. The interactive review viewer opens automatically:
       $ agentcad run script.py --label first --render iso
  3. Verify dimensions and feature sizes from the generated STEP:
       $ agentcad measure v1_first/output.step
  4. Iterate with a new label and review the automatic previous/current diff.

NAMED PARTS AND GROUPS
  Named outputs become stable reviewable parts:
    show_object(wheel, id="wheel_left", name="Left wheel",
                options={"color": "red", "part_of": "wheels"})
  Use `agentcad parts` after a run to list, isolate, hide, ghost, or focus
  captured parts and groups. Run `agentcad docs parts` for the full API.

EXAMPLE SESSION
  $ agentcad init --name myproject__INIT_RUNTIME__
  {"command": "init", "status": "success", "project": "myproject",
   "runtime": "__RUNTIME__"}
  # Write script.py (see `agentcad docs quickstart`), then:
  $ agentcad run script.py --label first --render iso
  {"command": "run", "status": "success", "runtime": "__RUNTIME__",
   "output_type": "single_part", "version": 1, "label": "first",
   "artifact_created": true,
   "outputs": {"step": "v1_first/output.step", "script": "v1_first/script.py"},
   "viewer": "v1_first/viewer.html", "viewer_glb": "v1_first/output.glb",
   "metrics": {"dimensions": {"x": 10.0, "y": 20.0, "z": 5.0},
               "volume": 1000.0, "is_valid": true, ...},
   "preview": "v1_first/preview.png"}

VERSION OUTPUTS
  A successful first run creates:
    v1_first/
      output.step       STEP geometry
      output.glb        GLB backing viewer.html (normally; skipped by fast path)
      script.py         copy of the executed script
      meta.json         full run metadata, including runtime and parts
      preview.png       4-view composite: top, bottom, upper iso, lower iso
      diff_side.png     side-by-side vs. prior success (from v2 onward)
      diff_overlay.png  centered 2D projection map vs. prior (from v2 onward)
      diff_volume.png   source-frame shared/reference-only/candidate-only
                        3D volume vs. prior (from v2 onward, valid solids)
      diff_volume.glb   interactive colored geometry backing the 3D volume map
      viewer.html       interactive review viewer (opens automatically;
                        from v2: A=previous, B=current)
      renders/          requested PNG views

  The viewer can inspect parts and groups, compare versions, and export an
  on-demand turntable GIF. When used by itself, `--no-preview` skips only
  preview.png and per-part previews; viewer.html, its GLB, and diff PNGs still
  generate. `--no-view` prevents the automatic browser launch without removing
  viewer artifacts.
  `--no-diff` skips automatic comparison with the prior version; an explicit
  `agentcad diff` remains available. Combine
  `--no-preview --no-diff --no-view` for the core-only fast path: output.step,
  the saved script, meta.json (including metrics), and only explicitly
  requested exports.

COMMAND REFERENCE: CREATE AND IMPORT
  agentcad init [--name NAME]__INIT_RUNTIME_OPTION__ [--force]
    __INIT_COMMAND_DESCRIPTION__
    --force replaces an existing manifest.

  agentcad run SCRIPT --label LABEL [OPTIONS]
    Execute a script and produce a versioned STEP, metrics, viewer, and preview.
    Passing a STEP/STP/BREP path dispatches to `agentcad import` automatically.
    --label LABEL        Name this version; outputs.step is the artifact path.
    --output LABEL       Deprecated compatibility alias for --label. It never
                         denotes an output path.
    --render VIEWS       Named views, `all`, angle azimuth:elevation, or a mix:
                         front,right,45:30
    --export FORMATS     Comma-separated stl, glb, obj. Explicit GLB appears in
                         outputs.glb; viewer_glb normally generates separately.
    --preview / --no-preview
                         Generate or skip the agent-readable composite and
                         per-part previews. Preview is on by default (~2-4s).
    --view / --no-view   Open the review viewer after success (default on).
                         From v2, previous/current comparison is preloaded.
    --diff / --no-diff   Generate or skip automatic comparison with the prior
                         successful version (default on).
    --params K=V,...     Override top-level constants; values may be numbers,
                         booleans, or strings.
    __RUN_RUNTIME_HELP__
    --dry-run            Return validation and metrics without consuming a
                         version or writing artifacts.

  agentcad import FILE [--label LABEL] [--init] [--no-diff]
    Adopt STEP/STP/BREP as a versioned baseline with provenance. --init creates
    a manifest when needed. Later render, view, measure, parts, and diff commands
    work on an imported version like a scripted one. --no-diff skips the
    automatic prior-version comparison without disabling explicit diff commands.

COMMAND REFERENCE: RENDER, EXPORT, AND REVIEW
  agentcad render STEP --view SPEC [OPTIONS]
    Render PNGs after a run. SPEC accepts named views, `all`, custom
    azimuth:elevation, or a mix. Camera options: --zoom N, --size WxH,
    --msaa N, --focus x,y,z, --no-fit, and --name LABEL (single view only).

  agentcad export STEP --format stl,glb,obj
    Export one or more mesh formats. GLB auto-colors individual solids.

  agentcad view FILE [FILE_B] [--overlay] [--measure] [--spec spec.json]
    Open an explicit browser review for STEP/STP or GLB; STEP auto-converts.
    A second file opens synchronized A/B comparison, or a red/green overlay
    with --overlay. Two STEP files also produce centered 2D projection and
    source-frame 3D volume artifacts. --measure embeds geometry measurements;
    --spec runs the checklist and opens Spec check mode. Measurement and spec
    review require STEP/STP source geometry.

  agentcad diff REF1 REF2 [--visual] [--overlay]
    Compare version metrics, outputs, parameters, and parts. Refs may be version
    numbers, labels, or STEP/BREP paths. Closed solids also return actual shared,
    reference-only, and candidate-only source-frame volumes; no alignment is
    applied. --visual opens the browser comparison and writes colored volume
    artifacts; --overlay selects its tinted interactive mode.
    Comparison responses expose comparison_phases; read each status and
    duration_ms to distinguish source loading, rendering, 2D projection,
    exact_3d_comparison, approximate_3d_comparison, artifact export, and viewer
    generation.
    Exact 3D work runs in a terminable worker with a 30s default budget.
    Set AGENTCAD_DIFF_TIMEOUT_S=N to override it; 0 disables this dedicated
    limit for diagnostics. A timeout preserves completed projection results
    and the committed version, then automatically runs a bounded approximate
    voxel comparison. Approximate results report method, resolution_mm, and a
    non-strict error_estimate; its absolute_volume values are heuristic errors,
    not measurements. Exact diagnostics remain in exact_attempt.
    Set AGENTCAD_APPROX_DIFF_TIMEOUT_S or AGENTCAD_APPROX_RESOLUTION_MM to tune
    the fallback. Exact results use independent inputs and one non-destructive
    exact partition. Native diagnostics appear at comparison_3d.kernel for an
    exact result and comparison_3d.exact_attempt.kernel after fallback;
    exact_partition_runs distinguishes that pass from compound canonicalization,
    and repeated native messages include counts. volume_semantics explains
    compound overlap only when applicable.
    Impossible or non-conserving volumes are rejected. If exact volumes are needed,
    retry `agentcad diff OLD NEW` with a larger budget instead of rerunning CAD.

  agentcad parts list REF
  agentcad parts show REF PART_ID
    List captured parts/groups or return one stable part record. REF may be a
    number, vN, label, version directory, current, or latest.

  agentcad parts view REF [--isolate ID] [--hide ID] [--ghost-rest] [--focus ID]
                          [--isolate-group GROUP] [--hide-group GROUP]
                          [--focus-group GROUP] [--label TEXT] [--note TEXT]
                          [--no-open]
    Create a temporary part review handoff viewer. Repeat --isolate/--hide for parts,
    or --isolate-group/--hide-group for groups; use --ghost-rest, --focus,
    --focus-group, --label, --note, and --no-open as needed.
    Browser changes are not saved; generate another viewer to change the state.

COMMAND REFERENCE: VERIFY AND DEBUG
  agentcad measure FILE [OPTIONS]
    Return dimensions, metrics, and compact feature summaries for STEP/STP/BREP.
    --features includes full solid/face/edge lists; --cylinders-only trims the
    response. Filter cylinder buckets with --diameter N [--tolerance N] and
    --axis +/-x|+/-y|+/-z|other. Lists are capped; use --limit or --no-limit.

  agentcad check-spec FILE SPEC.json
    Compare cylindrical features to an explicit checklist. Example:
      {"features":[{"name":"bolt_holes","type":"cylinder",
                    "diameter_mm":6,"count":4}]}
    `status: success` means the check ran; inspect `passed` to determine whether
    the model meets the spec. Results include matches, missing features, and
    count errors. Specs may set diameter/count tolerances and an axis.

  agentcad inspect FILE [--validate-only] [--validation-timeout SECONDS]
                        [--ids] [--summary] [--limit N|--no-limit]
    Report recognized format and, for STEP/STP/BREP, solids, shells, face
    orientations, edges, free edges, and validity. --summary clusters topology;
    --ids returns 1-indexed feature IDs used by editing helpers. Lists are capped
    unless --no-limit is requested. Validation reports native_load,
    structural_validation, topology_extraction, and feature_extraction phases.
    --validate-only skips deep topology; AGENTCAD_INSPECT_TIMEOUT_S configures
    the same budget as --validation-timeout (90s default, 0 disables it).

COMMAND REFERENCE: PROJECT AND INTEGRATIONS
  agentcad context
    Return project name, current version, version history, tool version, and any
    interrupted version directories that need explicit recovery.

  agentcad recover VERSION_DIR [--make-current]
    Validate an interrupted directory's output.step and safely restore its
    metadata/history entry with atomic JSON replacements. Recovery never deletes
    the directory, STEP, or source files and does not change current unless
    --make-current is explicitly passed.

  agentcad docs [SECTION] [--runtime ENGINE]
    Read the full built-in documentation or one section. Useful sections include
    quickstart, preamble, commands, workflow, recovery, render, measure,
    check-spec, inspect, parts, editing, helpers, patterns, runtimes, feedback,
    and mcp.

  agentcad instructions install
    Record a short project note so future agents read `agentcad --help`.

  agentcad skill install
  agentcad skill show
    Install the Claude Code skill in the current project, or return its content
    as JSON for another agent integration.

  agentcad feedback "MESSAGE" [--max-entries N] [--local-only]
    Save a diagnostic bundle under .agentcad/feedback and, unless --local-only,
    send it to the agentcad team. The bundle includes recent session context.

  agentcad subscribe EMAIL
    Request product updates. Subscription uses email confirmation (double opt-in).

JSON RESPONSE CONTRACT
  Agent-facing command results are JSON with "command" and "status" keys.
  Successful `run` results also include "runtime". Run-specific statuses:
    "success"          primary command work completed; inspect artifact statuses
    "failed"           script execution failed; consumes a version and creates
                       v{N}_{label}_failed/ with the script and metadata
    "error"            CLI/input error; no version or artifacts
    "validation_error" static script check failed; no version or artifacts
    "invalid_geometry" script/import produced an invalid final B-rep; records
                       a numbered diagnostic attempt unless --dry-run, writes no
                       STEP, and does not advance current; see
                       version_recorded/current_advanced

  Run/import metrics include bounding_box, dimensions, volume, surface_area,
  center_of_mass, face_count, edge_count, and is_valid. A successful materialized
  build has is_valid=true and an exported STEP; --dry-run is explicitly metrics
  only. Check metrics before rendering; visual appearance alone does not prove
  dimensional correctness.

  Materialized run/import responses separate `core.status` from `artifacts`.
  Core success is committed before optional work. Each artifact reports pending,
  success, unavailable, timeout, failed, or skipped; a non-success artifact does
  not reverse core success or remove the registered STEP. Keep and use that STEP;
  retry only the optional render/view work if you need it.

SPEC AND MEASUREMENT CHECKS
  When requirements include explicit holes, bores, diameters, counts, or
  overall dimensions:
    1. Check run.metrics: dimensions, volume, face_count, and is_valid.
    2. Read preview.png and, when iterating, diff_side.png.
    3. Run `agentcad measure` on output.step.
    4. Run `agentcad check-spec` for explicit cylindrical requirements.
       Revise before marking the model done when `passed` is false.
    5. Run `agentcad inspect` when an existing source CAD file or successful
       output STEP has topology that looks wrong. An `invalid_geometry` run has
       no output STEP; repair its recorded script/source using response metrics.
    6. Review the automatically opened viewer with the user. Use
       `agentcad view old.step new.step` for an explicit non-adjacent comparison.

DEBUGGING
  Geometry wrong? Check metrics first — volume and dimensions catch most issues.
  $ agentcad run script.py --label test --dry-run        # metrics, no disk artifacts
  $ agentcad measure v1_test/output.step                  # dimensions + feature sizes
  $ agentcad check-spec v1_test/output.step spec.json     # compare against intended features
  $ agentcad inspect v1_test/output.step                  # successful STEP deep-dive

  Geometry edits belong in script.py or an imported model's edit.py; helper
  names such as fillet_edges are Python calls, not top-level commands. A failed
  run returns outputs.step=null and an exact rerun command. Never repair STEP by
  writing or truncating text; use agentcad run or import so the CAD kernel writes it.
    Hollow shape?     -> free_edge_count > 0, shell not closed
    Inverted normals? -> face_orientations imbalanced
    Invalid?          -> is_valid: false
  $ agentcad render v1_test/output.step --view all        # visual from 4 angles

MCP INTEGRATION
  Install the extra and add agentcad to .mcp.json for Claude Code, Cursor,
  Windsurf, or another MCP-compatible agent:

    $ pip install 'agentcad[mcp]'
    {"agentcad": {"command": "python", "args": ["-m", "agentcad.mcp"]}}

  This exposes agentcad operations as native agent tools.
"""


_BUILD123D_AUTHORING_GUIDE = """BUILD123D AUTHORING
  New agentcad projects use build123d. Scripts call show_object() to surface
  geometry; use show_assembly() for intentional multi-body ShapeList/list
  output. build123d primitives and agentcad edit helpers are pre-injected:

    box = Box(10, 20, 5)
    show_object(box)

  Start here:

    $ agentcad init --name myproject
    $ agentcad docs preamble     # pre-injected build123d names
    $ agentcad docs quickstart   # first-script walkthrough
    $ agentcad docs examples     # worked build123d examples
    $ agentcad docs patterns     # idioms + footguns

  CadQuery compatibility remains available for existing projects and scripts.
  See `agentcad docs runtimes` for the explicit compatibility workflow.
"""


_CADQUERY_AUTHORING_GUIDE = """CADQUERY COMPATIBILITY AUTHORING
  This project is pinned to the CadQuery compatibility runtime. Scripts call
  show_object() to surface geometry, and the CadQuery preamble is pre-injected:

    box = cq.Workplane('XY').box(10, 20, 5)
    show_object(box)

  Use the project-scoped compatibility docs:

    $ agentcad docs preamble
    $ agentcad docs quickstart
    $ agentcad docs patterns

  Keep this project on one authoring API. A one-off build123d run requires an
  explicit runtime override; new projects use build123d by default.
"""


def _build_guide(runtime: str = "build123d") -> str:
    init_runtime = "" if runtime == "build123d" else f" --runtime {runtime}"
    authoring_guide = (
        _CADQUERY_AUTHORING_GUIDE
        if runtime == "cadquery"
        else _BUILD123D_AUTHORING_GUIDE
    )
    if runtime == "cadquery":
        init_runtime_option = " --runtime cadquery"
        init_description = (
            "Initialize a CadQuery compatibility project. The runtime pin keeps "
            "run, docs, help, and skill on the same API."
        )
        run_runtime_help = (
            "--runtime ENGINE     Explicit one-off override of this\n"
            "                         compatibility project's runtime."
        )
    else:
        init_runtime_option = ""
        init_description = (
            "Initialize a build123d project. Subsequent run, docs, help, and "
            "skill commands follow that project mode."
        )
        run_runtime_help = (
            "--runtime ENGINE     Explicit compatibility override; see\n"
            "                         `agentcad docs runtimes`."
        )
    return (
        _GUIDE_TEMPLATE
        .replace("__AUTHORING_GUIDE__", authoring_guide)
        .replace("__INIT_RUNTIME_OPTION__", init_runtime_option)
        .replace("__INIT_COMMAND_DESCRIPTION__", init_description)
        .replace("__RUN_RUNTIME_HELP__", run_runtime_help)
        .replace("__RUNTIME__", runtime)
        .replace("__INIT_RUNTIME__", init_runtime)
    )


class _LoggingGroup(click.Group):
    """Click Group that auto-logs every command invocation to session.jsonl."""

    def main(
        self,
        args=None,
        prog_name=None,
        complete_var=None,
        standalone_mode=True,
        **extra,
    ):
        """Run Click while keeping every usage failure on the JSON contract."""
        raw_args = list(sys.argv[1:] if args is None else args)
        try:
            return super().main(
                args=raw_args,
                prog_name=prog_name,
                complete_var=complete_var,
                standalone_mode=False,
                **extra,
            )
        except click.UsageError as exc:
            click.echo(json.dumps(self._usage_error_payload(exc, raw_args)))
            if standalone_mode:
                raise SystemExit(exc.exit_code)
            raise

    def _usage_error_payload(self, exc, raw_args):
        command = (
            raw_args[0]
            if raw_args and not raw_args[0].startswith("-")
            else None
        )
        message = exc.format_message()
        if isinstance(exc, click.NoSuchOption):
            error_kind = "unknown_option"
            invalid_option = exc.option_name
        elif isinstance(exc, click.BadOptionUsage):
            error_kind = (
                "missing_parameter"
                if "requires an argument" in message
                else "usage_error"
            )
            invalid_option = exc.option_name
        elif isinstance(exc, click.MissingParameter):
            error_kind = "missing_parameter"
            invalid_option = (
                exc.param_hint if exc.param_type == "option" else None
            )
        elif isinstance(exc, click.BadParameter):
            error_kind = "invalid_value"
            invalid_option = None
            param = getattr(exc, "param", None)
            if isinstance(param, click.Option) and param.opts:
                invalid_option = param.opts[0]
        elif message.startswith("No such command"):
            error_kind = "unknown_command"
            invalid_option = None
        else:
            error_kind = "usage_error"
            invalid_option = getattr(exc, "option_name", None)

        if command in self.commands:
            command_obj = self.commands[command]
            usage_ctx = click.Context(
                command_obj,
                info_name=f"agentcad {command}",
            )
            usage = command_obj.get_usage(usage_ctx).strip()
            next_actions = [f"agentcad {command} --help"]
        else:
            usage = (
                exc.ctx.get_usage().strip()
                if exc.ctx is not None
                else "Usage: agentcad [OPTIONS] COMMAND [ARGS]..."
            )
            next_actions = ["agentcad --help"]

        if error_kind == "unknown_command" and command:
            if command in _SCRIPT_EDIT_HELPERS:
                message = (
                    f"`{command}` is a script helper, not a top-level "
                    "AgentCAD command. Call it inside edit.py, then run the "
                    "script to create a STEP."
                )
            else:
                message = (
                    f"`{command}` is not a top-level AgentCAD command. "
                    "AgentCAD geometry edits are written in a Python script "
                    "such as edit.py, then executed with `agentcad run`."
                )
            if Path("edit.py").is_file():
                run_action = "agentcad run edit.py --label recovered-edit"
                if not Path("agentcad.json").is_file():
                    run_action = (
                        "agentcad init --name recovered && " + run_action
                    )
                next_actions = [run_action]
            else:
                next_actions = ["agentcad docs editing"]

        payload = {
            "command": command,
            "status": "error",
            "error_kind": error_kind,
            "message": message,
            "usage": usage,
            "invalid_option": invalid_option,
            "next_actions": next_actions,
        }
        if command == "run":
            label, used_output = self._run_label_from_args(raw_args)
            payload.update({
                "label": label,
                "artifact_created": False,
                "outputs": {"step": None},
            })
            if used_output:
                payload["deprecation"] = _OUTPUT_DEPRECATION
        return payload

    @staticmethod
    def _run_label_from_args(raw_args):
        label = None
        used_output = False
        for index, token in enumerate(raw_args):
            for option in ("--label", "--output"):
                if token == option:
                    value = (
                        raw_args[index + 1]
                        if index + 1 < len(raw_args)
                        and not raw_args[index + 1].startswith("-")
                        else None
                    )
                else:
                    prefix = f"{option}="
                    if not token.startswith(prefix):
                        continue
                    value = token[len(prefix):]
                if option == "--label":
                    label = value
                else:
                    used_output = True
                    if label is None:
                        label = value
        return label, used_output

    def format_epilog(self, ctx, formatter):
        # Write the guide verbatim rather than passing it through Click's
        # paragraph wrapper. Shell snippets and JSON examples must retain their
        # line breaks to work as an agent-readable docs page.
        from agentcad.runners.dispatch import project_runtime, DEFAULT_RUNTIME
        rt = project_runtime() or DEFAULT_RUNTIME
        formatter.write("\n")
        formatter.write(_build_guide(rt))
        formatter.write("\n")

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
    epilog=_GUIDE_TEMPLATE,  # Replaced by format_epilog at render time.
    context_settings=dict(max_content_width=120),
)
def cli():
    """agentcad — CLI CAD tool for AI agents. Command results are JSON."""


cli.add_command(context)
cli.add_command(check_spec)
cli.add_command(daemon)
cli.add_command(diff)
cli.add_command(docs)
cli.add_command(export_cmd)
cli.add_command(feedback)
cli.add_command(import_cmd)
cli.add_command(init)
cli.add_command(instructions)
cli.add_command(inspect_cmd)
cli.add_command(measure)
cli.add_command(parts_cmd)
cli.add_command(render)
cli.add_command(recover)
cli.add_command(run)
cli.add_command(skill)
cli.add_command(subscribe)
cli.add_command(view)


if __name__ == "__main__":
    # Allow `python -m agentcad.cli ...`; without this the module imports
    # and exits silently. See also agentcad/__main__.py for `python -m agentcad`.
    cli()
