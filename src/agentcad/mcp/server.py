"""agentcad MCP server — exposes CLI commands as MCP tools."""

import json
import os
import traceback

from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP

from agentcad.cli import cli

mcp = FastMCP(name="agentcad")


def _format_result(output: str, exit_code: int, exception: BaseException | None = None) -> dict:
    """Build the MCP response dict from a Click invocation's output.

    Normal commands print JSON, which we pass through. When Click *catches*
    an unexpected exception (``exception`` is non-None), the output is often
    empty — previously this collapsed to ``{"message": "No output"}``, hiding
    the traceback and leaving callers (notably on Windows) with no way to
    diagnose the failure. We surface the exception type, message, and
    traceback in that case instead.
    """
    try:
        return {**json.loads(output), "_exit_code": exit_code}
    except (json.JSONDecodeError, TypeError):
        pass

    message = output.strip() if output else ""
    if exception is not None:
        tb = "".join(traceback.format_exception(
            type(exception), exception, exception.__traceback__
        )).strip()
        message = f"{message}\n{tb}".strip() if message else tb

    return {
        "status": "error",
        "message": message or "No output",
        "exit_code": exit_code,
        "_exit_code": exit_code,
    }


def _build_args(args: list[str], build_dir: str | None) -> list[str]:
    return [*args, "--build-dir", build_dir] if build_dir is not None else args


def _invoke(args: list[str], cwd: str | None = None) -> dict:
    """Invoke an agentcad CLI command and return parsed JSON response."""
    runner = CliRunner()
    env = {"AGENTCAD_DAEMON": "1", "AGENTCAD_NO_LOG": "1"}

    old_cwd = os.getcwd()
    try:
        if cwd:
            os.chdir(cwd)
        result = runner.invoke(cli, args, env=env)
    finally:
        os.chdir(old_cwd)

    return _format_result(result.output, result.exit_code, result.exception)


@mcp.tool()
def run(
    script: str,
    output: str,
    cwd: str,
    render: str | None = None,
    export: str | None = None,
    preview: bool = True,
    params: str | None = None,
    dry_run: bool = False,
    diff: bool = True,
    view: bool = True,
    build_dir: str | None = None,
) -> dict:
    """Execute a build123d script and produce a versioned STEP file with metrics.

    New projects use build123d. Existing CadQuery compatibility projects follow
    the runtime pinned in their agentcad.json.

    Args:
        script: Path to the Python CAD script.
        output: Label for this version.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Initialize this root first.
        render: Comma-separated views to render (front,right,top,iso,all).
        export: Comma-separated mesh formats (stl, glb, obj).
        preview: Render a quick 256x256 iso preview. Default True — pass False to suppress.
        params: Parameter overrides as key=value,key=value.
        dry_run: Compute metrics without creating a version.
        diff: Compare automatically with the prior successful version. Pass
            False to skip it; explicit diff remains available.
        view: Open or reuse the live project viewer. Share project_viewer.url
            with the human for automatic updates; viewer is a fixed snapshot.
            Pass False with preview=False
            and diff=False to also bypass viewer generation on the core-only
            fast path.
    """
    args = ["run", script, "--label", output]
    if render:
        args.extend(["--render", render])
    if export:
        args.extend(["--export", export])
    if not preview:
        args.append("--no-preview")
    if not diff:
        args.append("--no-diff")
    if not view:
        args.append("--no-view")
    if params:
        args.extend(["--params", params])
    if dry_run:
        args.append("--dry-run")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def render(
    step_file: str,
    view: str,
    cwd: str,
    zoom: float | None = None,
    name: str | None = None,
    focus: str | None = None,
    no_fit: bool = False,
    build_dir: str | None = None,
    highlight: str | None = None,
) -> dict:
    """Render PNG views of an existing STEP file.

    Args:
        step_file: Path to the STEP file.
        view: View spec (front,right,iso,all or custom angle az:el).
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
        zoom: Zoom factor.
        name: Output name label.
        focus: Camera focus point as x,y,z.
        no_fit: Skip FitAll (requires focus).
        highlight: Use "validation" to mark failing edges/vertices in red and return the report.
    """
    args = ["render", step_file, "--view", view]
    if highlight is not None:
        args.extend(["--highlight", highlight])
    if zoom is not None:
        args.extend(["--zoom", str(zoom)])
    if name:
        args.extend(["--name", name])
    if focus:
        args.extend(["--focus", focus])
    if no_fit:
        args.append("--no-fit")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def export(step_file: str, formats: str, cwd: str, build_dir: str | None = None) -> dict:
    """Export a STEP file to mesh formats (stl, glb, obj).

    Returns source CAD validation separately from mesh_validation per written
    format. Success means files were written; require that mesh's is_valid is
    true before handoff. Failed/unknown mesh checks retain files and warn.

    Args:
        step_file: Path to the STEP file.
        formats: Comma-separated formats (stl, glb, obj).
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    return _invoke(_build_args(["export", step_file, "--format", formats], build_dir), cwd=cwd)


@mcp.tool()
def measure(
    file: str,
    cwd: str,
    features: bool = False,
    cylinders_only: bool = False,
    diameter: float | None = None,
    tolerance: float = 0.5,
    axis: str | None = None,
    limit: int | None = None,
    no_limit: bool = False,
    build_dir: str | None = None,
) -> dict:
    """Measure dimensions and feature sizes in a STEP/BREP file.

    Args:
        file: Path to the STEP/STP/BREP file.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
        features: Include full per-solid, per-face, and per-edge measurement lists.
        cylinders_only: Return only cylindrical feature buckets and core metrics.
        diameter: Optional cylindrical feature diameter filter.
        tolerance: Diameter tolerance used with diameter.
        axis: Optional axis filter (+x, -x, +y, -y, +z, -z, other).
        limit: Optional maximum records per feature list.
        no_limit: Return complete feature lists. Can be very large.
    """
    args = ["measure", file]
    if features:
        args.append("--features")
    if cylinders_only:
        args.append("--cylinders-only")
    if diameter is not None:
        args.extend(["--diameter", str(diameter)])
    if tolerance != 0.5:
        args.extend(["--tolerance", str(tolerance)])
    if axis:
        args.extend(["--axis", axis])
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if no_limit:
        args.append("--no-limit")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def inspect(
    file: str,
    cwd: str,
    ids: bool = False,
    summary: bool = False,
    limit: int | None = None,
    no_limit: bool = False,
    build_dir: str | None = None,
) -> dict:
    """Inspect topology of a STEP file (solids, shells, faces, edges, validity).

    Args:
        file: Path to the STEP file.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
        ids: Include per-feature ID lists.
        summary: Include compact face/edge clusters.
        limit: Optional maximum records per ID list and IDs per summary cluster.
        no_limit: Return complete ID lists. Can be very large.
    """
    args = ["inspect", file]
    if ids:
        args.append("--ids")
    if summary:
        args.append("--summary")
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if no_limit:
        args.append("--no-limit")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def check_spec(file: str, spec_file: str, cwd: str, build_dir: str | None = None) -> dict:
    """Check a STEP/BREP file against a JSON cylindrical-feature spec.

    Returns the same structured result as `agentcad check-spec`, including
    passed, matched_features, and missing_features.

    Args:
        file: Path to the STEP/STP/BREP file to check.
        spec_file: Path to the JSON spec (cylindrical feature checklist).
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    return _invoke(_build_args(["check-spec", file, spec_file], build_dir), cwd=cwd)


@mcp.tool()
def docs(
    section: str | None = None,
    runtime: str | None = None,
    cwd: str | None = None,
    build_dir: str | None = None,
) -> dict:
    """Show build123d documentation, or explicit CadQuery compatibility docs.

    Args:
        section: Optional section name (quickstart, commands, helpers, patterns, etc).
        runtime: Optional runtime override. Pass ``cadquery`` to retrieve the
            same compatibility docs as ``agentcad docs --runtime cadquery``.
        cwd: Optional source project directory for runtime-aware documentation.
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only.
    """
    args = ["docs"]
    if section:
        args.append(section)
    if runtime:
        args.extend(["--runtime", runtime])
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def context(cwd: str, build_dir: str | None = None) -> dict:
    """Show project state, including interrupted-version recovery candidates.

    Args:
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Initialize this root first.
    """
    return _invoke(_build_args(["context"], build_dir), cwd=cwd)


@mcp.tool()
def recover(version_dir: str, cwd: str, make_current: bool = False, build_dir: str | None = None) -> dict:
    """Validate and reconcile an interrupted version directory safely.

    Args:
        version_dir: Direct version directory name reported by context.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
        make_current: Explicitly make the recovered successful version current.
    """
    args = ["recover", version_dir]
    if make_current:
        args.append("--make-current")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def diff(ref1: str, ref2: str, cwd: str, build_dir: str | None = None) -> dict:
    """Compare two versions by number or label.

    Args:
        ref1: First version reference (number or label).
        ref2: Second version reference (number or label).
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    return _invoke(_build_args(["diff", ref1, ref2], build_dir), cwd=cwd)


@mcp.tool()
def view(file: str, cwd: str, build_dir: str | None = None, validation: bool = False) -> dict:
    """Open a GLB or STEP file in the browser via three.js.

    Args:
        file: Path to GLB or STEP file.
        validation: Show failure markers and conditional repair guidance for a STEP file.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    args = ["view", file]
    if validation:
        args.append("--validation")
    return _invoke(_build_args(args, build_dir), cwd=cwd)
