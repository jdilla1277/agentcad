"""agentcad MCP server — exposes CLI commands as MCP tools."""

import asyncio
import json
import os
import sys
import traceback
from pathlib import Path
import base64
import hashlib
import shlex
from typing import Any, Literal

from click.testing import CliRunner
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

from agentcad.cli import cli
from agentcad.cloud import CloudError, cloud_call

class AgentCADMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        for tool in tools:
            if tool.name.startswith("cloud_") or tool.name == "import_cad":
                tool.inputSchema["additionalProperties"] = False
        return tools

    async def call_tool(self, name, arguments):
        # FastMCP otherwise drops unknown arguments before calling a tool.
        # Closed tool contracts must reject unsupported options before work.
        if name.startswith("cloud_") or name == "import_cad":
            import jsonschema

            tool = self._tool_manager.get_tool(name)
            if tool is not None:
                try:
                    jsonschema.validate(arguments, {**tool.parameters, "additionalProperties": False})
                except jsonschema.ValidationError:
                    message = ("Invalid cloud arguments. Use the tool's declared parameters; local-only options are unavailable."
                               if name.startswith("cloud_") else "Invalid import_cad arguments. Use the tool's declared parameters.")
                    raise ValueError(message) from None
        return await super().call_tool(name, arguments)


mcp = AgentCADMCP(name="agentcad")


async def _cloud_tool(operation, **kwargs):
    if operation in {"run", "import_step", "check_spec", "download"}:
        # Local tools temporarily chdir on this event loop. Freeze the caller's
        # base directory before yielding so a worker cannot use that temporary cwd.
        try:
            kwargs["cwd"] = str(Path(kwargs.get("cwd") or Path.cwd()).expanduser().resolve())
        except (OSError, RuntimeError, ValueError):
            error = CloudError("invalid_directory", "cwd must resolve to a local directory.")
            return {**error.payload(operation), "_exit_code": 1}
    # HTTP requests, polling sleeps, and local file I/O must not block MCP's
    # event loop: discovery, pings, and cancellation still need to be served.
    result = await asyncio.to_thread(cloud_call, operation, **kwargs)
    return {**result, "_exit_code": 1 if result.get("status") in {"error", "failed"} else 0}


@mcp.tool()
async def cloud_create_project(name: str, runtime: Literal["build123d", "cadquery"] = "build123d") -> dict:
    """Create an account-owned cloud project. Runtime is build123d or cadquery.

    Requires AGENTCAD_API_URL, AGENTCAD_KEY_ID, AGENTCAD_KEY_SECRET in the server
    process environment. A lost response may still have created the project;
    check cloud_projects before creating another one.
    """
    return await _cloud_tool("create_project", name=name, runtime=runtime)


@mcp.tool()
async def cloud_projects() -> dict:
    """List cloud projects owned by the configured API account."""
    return await _cloud_tool("projects")


@mcp.tool()
async def cloud_project(project: str) -> dict:
    """Read a returned cloud project ID and its version count."""
    return await _cloud_tool("project", project=project)


@mcp.tool()
async def cloud_run(project: str, script: str, label: str, cwd: str | None = None, idempotency_key: str | None = None) -> dict:
    """Upload one explicit UTF-8 .py CAD script (at most 256 KiB) to a cloud project.

    Returns a job immediately. Call cloud_job with the returned project/job IDs.
    Keep idempotency_key for an identical retry after a lost response; never
    resubmit a running job. Edit the local script and use a new key/label for a
    new version. Only this file is uploaded, not its folder or dependencies.
    cwd resolves the local file; no local AgentCAD project is required.
    """
    return await _cloud_tool("run", project=project, script=script, label=label, cwd=cwd, idempotency_key=idempotency_key)


@mcp.tool()
async def cloud_import(project: str, file: str, cwd: str | None = None, idempotency_key: str | None = None) -> dict:
    """Upload one explicit .step/.stp file (at most 20 MiB) to a cloud project.

    Returns a job immediately. Poll cloud_job; preserve the retry key for an
    identical lost-response retry. cwd resolves this one local file.
    """
    return await _cloud_tool("import_step", project=project, file=file, cwd=cwd, idempotency_key=idempotency_key)


@mcp.tool()
async def cloud_measure(project: str, version: int) -> dict:
    """Read stored cloud-version measurements using returned project/version IDs.

    Read metrics.is_valid separately from spec pass/fail and human acceptance.
    """
    return await _cloud_tool("measure", project=project, version=version)


@mcp.tool()
async def cloud_inspect(project: str, version: int, ids: bool = False, summary: bool = False, limit: int = 100,
                  validate_only: bool = False, idempotency_key: str | None = None) -> dict:
    """Inspect a stored cloud version; returns a job to poll with cloud_job.

    limit is 1..1000. validate_only cannot combine with ids/summary. Completed
    inspection and geometry_valid are separate; invalid geometry can be inspected.
    """
    return await _cloud_tool("inspect", project=project, version=version, ids=ids, summary=summary, limit=limit,
                       validate_only=validate_only, idempotency_key=idempotency_key)


@mcp.tool()
async def cloud_check_spec(project: str, version: int, spec_file: str, cwd: str | None = None, idempotency_key: str | None = None) -> dict:
    """Upload one explicit JSON spec (at most 256 KiB); returns a verification job.

    Poll cloud_job. Supported solid/shell counts and cylinder checks can pass or
    fail; unsupported requirements remain listed and passed can be null. Job
    completion, geometry_valid, and passed are separate. cwd resolves spec_file.
    """
    return await _cloud_tool("check_spec", project=project, version=version, spec_file=spec_file, cwd=cwd, idempotency_key=idempotency_key)


@mcp.tool()
async def cloud_render(project: str, version: int, idempotency_key: str | None = None) -> dict:
    """Request the fixed 800x600 isometric PNG of a cloud version; returns a job.

    Poll cloud_job then use cloud_download(kind='render', job=ID) to open the
    picture locally. Rendering does not establish geometry validity or approval.
    """
    return await _cloud_tool("render", project=project, version=version, idempotency_key=idempotency_key)


@mcp.tool()
async def cloud_diff(project: str, from_version: int, to_version: int, idempotency_key: str | None = None) -> dict:
    """Compare two stored versions in one cloud project; returns a job.

    Poll cloud_job for metric changes and comparison_3d. Preserve its exact or
    approximate method, error estimates, and unavailable/timeout states. An
    unavailable comparison does not establish unchanged geometry. Files are
    compared in their stored positions, without alignment or an interactive viewer.
    """
    return await _cloud_tool("diff", project=project, from_version=from_version, to_version=to_version, idempotency_key=idempotency_key)


@mcp.tool()
async def cloud_job(project: str, job: str, wait_seconds: int = 0) -> dict:
    """Read a cloud job and, when complete, its nested result or measurement.

    wait_seconds is 0..60. Queued/running means poll this same job again, not
    resubmit it. Job status is distinct from result.geometry_valid, result.passed,
    and result.comparison_3d.status. A failed job returns its reason.
    """
    return await _cloud_tool("job", project=project, job=job, wait_seconds=wait_seconds)


@mcp.tool()
async def cloud_download(project: str, destination: str, job: str | None = None, kind: Literal["step", "render", "report"] = "step",
                   version: int | None = None, path: str | None = None, cwd: str | None = None, overwrite: bool = False) -> dict:
    """Download a private STEP, PNG render, or JSON report to an explicit local file.

    Use job plus kind='step'/'render'/'report', OR a returned version/path STEP
    artifact reference. Pass project IDs and relative artifact paths, not URLs.
    cwd resolves destination. Existing files are preserved unless overwrite=True.
    Returns byte count and SHA-256; interrupted downloads leave no partial file.
    """
    return await _cloud_tool("download", project=project, destination=destination, job=job, kind=kind,
                       version=version, path=path, cwd=cwd, overwrite=overwrite)


def isolate_protocol_stdout() -> None:
    """Move the stdio JSON-RPC stream off fd 1 before the transport starts.

    CAD scripts, child processes, and OCCT can write to fd 1 directly, and
    ``agentcad run`` briefly redirects fd 1 to capture script output. Neither
    may touch the protocol stream, so the transport gets a private duplicate
    of the original stdout (via ``sys.stdout``, which it wraps at startup)
    and fd 1 is pointed at stderr for everything else.
    """
    sys.stdout.flush()
    protocol_fd = os.dup(1)
    os.dup2(2, 1)
    sys.stdout = open(protocol_fd, "w", encoding="utf-8", buffering=1)


def _format_result(
    output: str,
    exit_code: int,
    exception: BaseException | None = None,
    stderr: str = "",
) -> dict:
    """Build the MCP response dict from a Click invocation's stdout.

    Normal commands print JSON on stdout, which we pass through; progress
    heartbeats live on stderr and must not be parsed with it. When Click
    *catches* an unexpected exception (``exception`` is non-None), the output
    is often empty — previously this collapsed to ``{"message": "No output"}``,
    hiding the traceback and leaving callers (notably on Windows) with no way
    to diagnose the failure. We surface the exception type, message, and
    traceback in that case instead, falling back to stderr for context.
    """
    try:
        return {**json.loads(output), "_exit_code": exit_code}
    except (json.JSONDecodeError, TypeError):
        pass

    message = output.strip() if output else ""
    if not message and stderr:
        message = stderr.strip()
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

    # result.output merges stdout and stderr heartbeats; the JSON contract
    # is stdout only (the daemon reads it the same way).
    return _format_result(
        result.stdout, result.exit_code, result.exception, stderr=result.stderr,
    )


def _with_images(result, media, *, cwd, image_root=None, keep_core=False):
    """Attach only the images named by the response, checking bytes at delivery."""
    if not media:
        return result
    attachments = []
    try:
        for item in media:
            path = Path(item["png"])
            if image_root is not None:
                path = Path(image_root) / path
            if not path.is_absolute():
                path = Path(cwd) / path
            png = path.read_bytes()
            if hashlib.sha256(png).hexdigest() != item["sha256"]:
                raise ValueError("Image changed before attachment; retry the image request.")
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Inspection image is not a PNG; retry the image request.")
            attachments.append(ImageContent(type="image", mimeType="image/png", data=base64.b64encode(png).decode("ascii")))
    except (OSError, ValueError) as exc:
        if keep_core and result.get("core", {}).get("status") == "success":
            action = ["agentcad", "inspect", result["outputs"]["step"], "--report"]
            budget = result.get("inspection", {}).get("budget", {})
            for field, option in (("max_images", "--max-images"), ("text_tokens", "--text-budget")):
                if field in budget:
                    action.extend([option, str(budget[field])])
            if result.get("build_root"):
                action.extend(["--build-dir", result["build_root"]])
            return {
                **result,
                "warnings": [*result.get("warnings", []), "CAD import succeeded; inspection images could not be attached."],
                "image_delivery": {"status": "failed", "attached": 0, "requested": len(media),
                                   "message": str(exc), "next_actions": [shlex.join(action)]},
            }
        return {**result, "status": "error", "_exit_code": 1,
                "image": {"status": "failed", "message": str(exc)}}
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(result)), *attachments],
        structuredContent=result,
    )


@mcp.tool()
def import_cad(
    file: str,
    cwd: str,
    label: str | None = None,
    initialize: bool = False,
    runtime: Literal["build123d", "cadquery"] | None = None,
    validation_profile: Literal["deliverable", "kernel"] = "deliverable",
    view: bool = True,
    diff: bool = True,
    report: bool = True,
    report_images: bool = True,
    max_images: int = 9,
    text_budget: int = 2000,
    build_dir: str | None = None,
    checks_file: str | None = None,
    defer_checks: bool = False,
) -> Any:
    """Import a local STEP/STP/BREP as a versioned baseline with inspection PNGs.

    file and cwd refer to the MCP execution filesystem. No cloud account is
    needed; cloud_import uploads to a hosted project instead. initialize=True
    maps to CLI --init and can create the selected project/build root. New
    projects default to build123d; runtime only pins a newly initialized project.

    Default: CLI import JSON, including quality, dimensions, saved inventories
    and up to nine actual PNG image blocks (overall + up to four part/four raw
    face-edge samples). Images and selectors name the same version-local IDs.
    Named design-feature recognition is not available. Artifacts and edit.py
    remain on the execution filesystem; use returned paths with inspect/run.

    report=False omits inline inspection and attachments but retains the saved
    bundle. report_images=False skips inspection media while keeping the report;
    max_images (0..9) caps media. Neither disables legacy preview artifacts.
    text_budget bounds the inspection object (default 2000 estimated tokens),
    excluding legacy import metadata; image-token cost is unknown. view/diff
    match CLI defaults; set view=False for no browser handoff. build_dir selects
    the artifact/history root, relative to the project root. Attachment failure
    preserves successful core import and returns explicit image_delivery status.

    checks_file maps to CLI --checks, including on initialize=True imports.
    Requirements are inherited when omitted; changing them requires actor and
    rationale in the checks JSON. defer_checks maps to --defer-checks: retain
    geometry with pending checks without advancing current. Failed checks also
    leave current unchanged; check_spec(file=revision_id, complete=True) records
    deferred evidence without changing current.
    """
    args = ["import", file]
    if label is not None:
        args.extend(["--label", label])
    if initialize:
        args.append("--init")
    if runtime is not None:
        args.extend(["--runtime", runtime])
    args.extend(["--validation-profile", validation_profile])
    if not view:
        args.append("--no-view")
    if not diff:
        args.append("--no-diff")
    if not report:
        args.append("--no-report")
    if not report_images:
        args.append("--no-inspection-image")
    args.extend(["--max-images", str(max_images), "--text-budget", str(text_budget)])
    if checks_file is not None:
        args.extend(["--checks", checks_file])
    if defer_checks:
        args.append("--defer-checks")
    result = _invoke(_build_args(args, build_dir), cwd=cwd)
    inspection = result.get("inspection", {})
    return _with_images(result, inspection.get("media", []), cwd=cwd,
                        image_root=inspection.get("inventory", {}).get("image_root"), keep_core=True)


@mcp.tool()
def run(
    script: str,
    cwd: str,
    output: str | None = None,
    render: str | None = None,
    export: str | None = None,
    preview: bool = True,
    params: str | None = None,
    dry_run: bool = False,
    diff: bool = True,
    view: bool = True,
    build_dir: str | None = None,
    checks_file: str | None = None,
    defer_checks: bool = False,
) -> dict:
    """Execute a build123d script and produce a versioned STEP file with metrics.

    New projects use build123d. Existing CadQuery compatibility projects follow
    the runtime pinned in their agentcad.json.
    Saved STEP versions also issue version-local geometry IDs. Use inspect
    with report=True or entity_id to inspect the result without re-importing.
    Reports/images are prepared on request; dry runs do not issue IDs.

    Args:
        script: Path to the Python CAD script.
        cwd: Source project directory (or a subdirectory).
        output: Label for this version. Required unless dry_run is True.
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Initialize this root first.
        render: Comma-separated views to render (front,right,top,iso,all).
        export: Comma-separated mesh formats (stl, glb, obj).
        preview: Render a quick 256x256 iso preview. Default True — pass False to suppress.
        params: Parameter overrides as key=value,key=value.
        dry_run: Compute metrics without creating a version.
        checks_file: Optional JSON dimension/preservation requirements. Repairs
            inherit the selected policy when omitted. See docs check-spec.
        defer_checks: Retain pending declared checks instead of evaluating them
            now. Complete with check_spec(revision_id, complete=True); completion
            records evidence without changing current.
        diff: Compare automatically with the prior successful version. Pass
            False to skip it; explicit diff remains available.
        view: Open or reuse the live project viewer. Share project_viewer.url
            with the human for automatic updates; viewer is a fixed snapshot.
            Pass False with preview=False
            and diff=False to also bypass viewer generation on the core-only
            fast path.
    """
    args = ["run", script]
    if output is not None:
        args.extend(["--label", output])
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
    if checks_file:
        args.extend(["--checks", checks_file])
    if defer_checks:
        args.append("--defer-checks")
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
    entity_id: str | None = None,
    image: bool = False,
    isolate: bool = False,
    report: bool = False,
    report_images: bool = True,
    max_images: int = 9,
    text_budget: int = 2000,
) -> Any:
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
        entity_id: Retrieve one typed ID from an imported or run-generated baseline's geometry manifest.
        image: Return a labeled four-view PNG attachment plus its JSON record.
        isolate: Show only the selected entity; requires image and entity_id.
        report: Return the saved compact inspection report and ranked sample bundle.
        report_images: Prepare/deliver report images; false keeps the same analysis without images.
        max_images: Maximum distinct report images, 0..9; overall then alternating part/face-edge samples.
        text_budget: Estimated token allowance for the report JSON (compact UTF-8 bytes / 3).
    """
    args = ["inspect", file]
    if report:
        args.append("--report")
    if not report_images:
        args.append("--no-images")
    if report or max_images != 9:
        args.extend(["--max-images", str(max_images)])
    if report or text_budget != 2000:
        args.extend(["--text-budget", str(text_budget)])
    if entity_id is not None:
        args.extend(["--id", entity_id])
    if image:
        args.append("--image")
    if isolate:
        args.append("--isolate")
    if ids:
        args.append("--ids")
    if summary:
        args.append("--summary")
    if limit is not None:
        args.extend(["--limit", str(limit)])
    if no_limit:
        args.append("--no-limit")
    result = _invoke(_build_args(args, build_dir), cwd=cwd)
    media = result.get("media", []) if report else []
    if image and result.get("image", {}).get("status") == "success":
        media = [{"png": result["image"]["png"], "sha256": result["image"]["png_sha256"]}]
    return _with_images(result, media, cwd=cwd,
                        image_root=result["inventory"]["image_root"] if report and media else None)


@mcp.tool()
def check_spec(file: str, spec_file: str | None = None, cwd: str | None = None, build_dir: str | None = None,
               complete: bool = False) -> dict:
    """Check a file/spec or inspect a retained revision's pinned check receipt.

    File checks return passed, matched_features, and missing_features. Revision
    reads return the saved receipt, validation_state, and check lifecycle.

    Args:
        file: STEP/STP/BREP path, or a returned retained revision ID.
        spec_file: JSON cylindrical-feature spec for file checks. Omit for a
            retained revision: its requirements are already pinned.
        complete: Complete deferred checks on a retained revision; never changes
            current or accepts an older candidate. A normal read is read-only.
        cwd: Source project directory (or a subdirectory); defaults to current.
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    args = ["check-spec", file]
    if spec_file is not None:
        args.append(spec_file)
    if complete:
        args.append("--complete")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


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
    """Open a GLB, STEP, or retained revision in the browser via three.js.

    Args:
        file: GLB/STEP path, or retained revision ID to review its diagnostic
            geometry and pinned check receipt without changing current.
        validation: Show failure markers and conditional repair guidance for a STEP file.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    args = ["view", file]
    if validation:
        args.append("--validation")
    return _invoke(_build_args(args, build_dir), cwd=cwd)
