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
) -> dict:
    """Execute a CadQuery script and produce a versioned STEP file with metrics.

    Args:
        script: Path to the CadQuery Python script.
        output: Label for this version.
        cwd: Project directory (must contain agentcad.json).
        render: Comma-separated views to render (front,right,top,iso,all).
        export: Comma-separated mesh formats (stl, glb, obj).
        preview: Render a quick 256x256 iso preview. Default True — pass False to suppress.
        params: Parameter overrides as key=value,key=value.
        dry_run: Compute metrics without creating a version.
    """
    args = ["run", script, "--output", output]
    if render:
        args.extend(["--render", render])
    if export:
        args.extend(["--export", export])
    if not preview:
        args.append("--no-preview")
    if params:
        args.extend(["--params", params])
    if dry_run:
        args.append("--dry-run")
    return _invoke(args, cwd=cwd)


@mcp.tool()
def render(
    step_file: str,
    view: str,
    cwd: str,
    zoom: float | None = None,
    name: str | None = None,
    focus: str | None = None,
    no_fit: bool = False,
) -> dict:
    """Render PNG views of an existing STEP file.

    Args:
        step_file: Path to the STEP file.
        view: View spec (front,right,iso,all or custom angle az:el).
        cwd: Project directory.
        zoom: Zoom factor.
        name: Output name label.
        focus: Camera focus point as x,y,z.
        no_fit: Skip FitAll (requires focus).
    """
    args = ["render", step_file, "--view", view]
    if zoom is not None:
        args.extend(["--zoom", str(zoom)])
    if name:
        args.extend(["--name", name])
    if focus:
        args.extend(["--focus", focus])
    if no_fit:
        args.append("--no-fit")
    return _invoke(args, cwd=cwd)


@mcp.tool()
def export(step_file: str, formats: str, cwd: str) -> dict:
    """Export a STEP file to mesh formats (stl, glb, obj).

    Args:
        step_file: Path to the STEP file.
        formats: Comma-separated formats (stl, glb, obj).
        cwd: Project directory.
    """
    return _invoke(["export", step_file, "--format", formats], cwd=cwd)


@mcp.tool()
def inspect(file: str, cwd: str) -> dict:
    """Inspect topology of a STEP file (solids, shells, faces, edges, validity).

    Args:
        file: Path to the STEP file.
        cwd: Project directory.
    """
    return _invoke(["inspect", file], cwd=cwd)


@mcp.tool()
def docs(section: str | None = None) -> dict:
    """Show agentcad documentation. No project directory needed.

    Args:
        section: Optional section name (quickstart, commands, helpers, patterns, etc).
    """
    args = ["docs"]
    if section:
        args.append(section)
    return _invoke(args)


@mcp.tool()
def context(cwd: str) -> dict:
    """Show project state (versions, current version, tool version).

    Args:
        cwd: Project directory (must contain agentcad.json).
    """
    return _invoke(["context"], cwd=cwd)


@mcp.tool()
def diff(ref1: str, ref2: str, cwd: str) -> dict:
    """Compare two versions by number or label.

    Args:
        ref1: First version reference (number or label).
        ref2: Second version reference (number or label).
        cwd: Project directory.
    """
    return _invoke(["diff", ref1, ref2], cwd=cwd)


@mcp.tool()
def view(file: str, cwd: str) -> dict:
    """Open a GLB or STEP file in the browser via three.js.

    Args:
        file: Path to GLB or STEP file.
        cwd: Project directory.
    """
    return _invoke(["view", file], cwd=cwd)
