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
            if "experimental_inspect" in tool.inputSchema.get("properties", {}):
                tool.inputSchema["properties"]["experimental_inspect"]["description"] = "Deprecated compatibility input; ignored. Milestone 2 import and Inspect features are available without opt-in."
                tool.inputSchema["properties"]["experimental_inspect"]["deprecated"] = True
            if "experimental_changes" in tool.inputSchema.get("properties", {}):
                tool.inputSchema["properties"]["experimental_changes"]["description"] = (
                    "Explicit per-call WIP opt-in. Must be true for retained changes, declared checks, stage, promote and authored-run retry/status; default false."
                )
                tool.description += "\nWIP change workflows require experimental_changes=true on every call; ordinary CAD calls default to false."
            if tool.name.startswith("cloud_") or tool.name in {"import_cad", "inspect", "run", "run_status", "promote", "changes_list", "changes_show", "changes_compare", "changes_restore", "changes_repair"}:
                tool.inputSchema["additionalProperties"] = False
        return tools

    async def call_tool(self, name, arguments):
        if arguments and "experimental_inspect" in arguments and type(arguments["experimental_inspect"]) is not bool:
            raise ValueError(f"Invalid {name} arguments: experimental_inspect must be a boolean.")
        if arguments and "experimental_changes" in arguments and type(arguments["experimental_changes"]) is not bool:
            raise ValueError(f"Invalid {name} arguments: experimental_changes must be a boolean.")
        # FastMCP otherwise drops unknown arguments before calling a tool.
        # Closed tool contracts must reject unsupported options before work.
        if name.startswith("cloud_") or name in {"import_cad", "inspect", "run", "run_status", "promote", "changes_list", "changes_show", "changes_compare", "changes_restore", "changes_repair"}:
            import jsonschema

            tool = self._tool_manager.get_tool(name)
            if tool is not None:
                try:
                    jsonschema.validate(arguments, {**tool.parameters, "additionalProperties": False})
                except jsonschema.ValidationError:
                    message = ("Invalid cloud arguments. Use the tool's declared parameters; local-only options are unavailable."
                               if name.startswith("cloud_") else f"Invalid {name} arguments. Use the tool's declared parameters.")
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


def _experimental(args, experimental_changes):
    return [*args, "--experimental-changes"] if experimental_changes else args


def _invoke(args: list[str], cwd: str | None = None) -> dict:
    """Invoke an agentcad CLI command and return parsed JSON response."""
    runner = CliRunner()
    env = {"AGENTCAD_DAEMON": "1"}

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


def _with_images(result, media, *, cwd, image_root=None, keep_core=False, full_response=False):
    """Attach only the images named by the response, checking bytes at delivery."""
    from agentcad.inspection_delivery import finalize_mcp

    def package(payload, attachments=()):
        # FastMCP pretty-prints bare dicts. Budgeted text must use the same
        # serialization we measured, even when no image blocks are attached.
        if attachments or payload.get("delivery", {}).get("scope") == "complete_response":
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload)), *attachments],
                                  structuredContent=payload)
        return payload

    result = finalize_mcp(result)
    if result.get("delivery", {}).get("scope") == "complete_response":
        report = result.get("inspection", result)
        media = report.get("media", []) if result["delivery"].get("budget_satisfied") else []
        image_root = report.get("inventory", {}).get("image_root")
    if not media:
        return package(result)
    attachments = []
    try:
        paths = []
        for item in media:
            path = Path(item["png"])
            if image_root is not None:
                path = Path(image_root) / path
            if not path.is_absolute():
                path = Path(cwd) / path
            paths.append(path.resolve())
        from agentcad.inspection_cache import cache_lease
        from agentcad.project import ProjectLayout
        layout = ProjectLayout(Path(result.get("project_root", cwd)), Path(result.get("build_root", cwd)))
        declared = result.get("inspection", result).get("retention") or result.get("image", {}).get("retention")
        with cache_lease(paths[0].parent.parent, layout=layout, protect=paths) as cache:
            cache.validate(paths, declared)
            for path, item in zip(paths, media):
                png = path.read_bytes()
                if hashlib.sha256(png).hexdigest() != item["sha256"]:
                    raise ValueError("Image changed before attachment; retry the image request.")
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Inspection image is not a PNG; retry the image request.")
                attachments.append(ImageContent(type="image", mimeType="image/png", data=base64.b64encode(png).decode("ascii")))
    except (OSError, ValueError) as exc:
        if keep_core and result.get("core", {}).get("status") == "success":
            action = ["agentcad", "inspect", result["outputs"]["step"], "--report"]
            if full_response:
                action.append("--full-response")
            budget = result.get("inspection", {}).get("budget", {})
            for field, option in (("max_images", "--max-images"), ("text_tokens", "--text-budget")):
                if field in budget:
                    action.extend([option, str(budget[field])])
            if result.get("build_root"):
                action.extend(["--build-dir", result["build_root"]])
            from agentcad.inspection_delivery import append_options
            append_options(action, result.get("delivery", {}).get("requested"))
            return package(finalize_mcp({
                **result,
                "warnings": [*result.get("warnings", []), "CAD import succeeded; inspection images could not be attached."],
                "image_delivery": {"status": "failed", "attached": 0, "requested": len(media),
                                   "message": str(exc), "next_actions": [shlex.join(action)]},
            }, delivered=False))
        return package(finalize_mcp({**result, "status": "error", "_exit_code": 1,
                "image": {"status": "failed", "message": str(exc)}}, delivered=False))
    return package(result, attachments)


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
    report: bool | None = None,
    report_images: bool | None = None,
    max_images: int = 9,
    text_budget: int = 2000,
    build_dir: str | None = None,
    idempotency_key: str | None = None,
    checks_file: str | None = None,
    defer_checks: bool = False,
    stage: bool = False,
    experimental_changes: bool = False,
    inspection_background: bool = False,
    inspection_wait_timeout: float | None = None,
    context_window: int | None = None,
    response_budget: int | None = None,
    response_text_budget: int | None = None,
    image_budget: int | None = None,
    vision_profile: dict[str, Any] | None = None,
    full_response: bool = False,
    experimental_inspect: bool = False,
) -> Any:
    """Import a local STEP/STP/BREP baseline with inspection images and measured IDs.

    file and cwd refer to the MCP execution filesystem. No cloud account is
    needed; cloud_import uploads to a hosted project instead. initialize=True
    maps to CLI --init and can create the selected project/build root. New
    projects default to build123d; runtime only pins a newly initialized project.

    Inspection is available by default; experimental_inspect is a deprecated,
    ignored compatibility input. report/report_images=None use the defaults.
    Complete CLI JSON is limited to 2000 estimated text tokens, including
    import/job metadata, with nested inspection, a delivery audit and full saved
    response_snapshot retrieval. Includes quality, dimensions, saved inventories
    and up to nine actual PNG image blocks (overall + up to four part/four raw
    face-edge samples). Images and selectors name the same version-local IDs.
    Named design-feature recognition is not available. Artifacts and edit.py
    remain on the execution filesystem; use returned paths with inspect/run.

    report=False omits inline inspection and attachments but retains the saved
    bundle. report_images=False skips inspection media while keeping the report;
    max_images (0..9) caps media. Neither disables legacy preview artifacts.
    text_budget separately bounds report preparation (default 2000 estimated
    tokens). Unknown context uses the whole-text fallback and image caps;
    image-token cost is unknown. full_response=True skips whole-response
    compaction and retains complete command fields, but report preparation
    limits still apply. Do not combine full_response with caller budgets. view/diff
    match CLI defaults; set view=False for no browser handoff. build_dir selects
    the artifact/history root, relative to the project root. Attachment failure
    preserves successful core import and returns explicit image_delivery status.

    Keep idempotency_key and identical source/options to retry a lost response
    without another version. Keys are scoped to the selected build root. A busy
    retry reports in_progress; interruption recovers committed CAD or explicitly
    reports stopped pre-commit work. It does not restart optional analysis.
    Replay actions target the caller directory; inline report/media may shrink
    to respect text_budget after formatting. Saved analysis and IDs stay fixed.
    Explicit checks JSON content and defer_checks must match on retries too.
    inspection_background returns a durable inspection_job while its worker
    continues. inspection_wait_timeout only limits waiting for inspection;
    core import and legacy previews still complete. Neither changes retry
    identity. Retry responses describe the original wait; use the returned
    inspect job actions for current status, wait, resume or cancellation.

    Optional context_window derives a 5% combined delivery target; response_budget
    overrides it. response_text_budget bounds the complete JSON, including import
    metadata/jobs. image_budget bounds selected image tokens. Supply vision_profile
    as {"name":"host", "tokens_per_image":1000}, a caller estimate per 1024-square
    sheet. Constrained images without a profile become references only. delivery
    reports actual estimates/omissions and budget_satisfied; an impossible budget
    preserves core success and provides a full response_snapshot retrieval action.
    These controls change delivery only and are excluded from import retry identity.
    Full response snapshots expire after 30 days and may be evicted earlier under
    the default 64 MiB per-build-root quota. Configure [inspection.snapshots] in
    agentcad.toml; references report retention/expiry. This never evicts geometry,
    IDs, import retry evidence or job checkpoints. An oversized snapshot reports
    delivery.status=resource_limit while preserving the import outcome.
    Reports/images and completed job results share a separate 30-day/512 MiB
    cache per build root, configured under [inspection.cache]. Resumable job
    checkpoints are protected; a resource_limit job has stopped. Raise the
    quota and use its resume action. retention states expiry/protected bytes.

    checks_file maps to CLI --checks, including on initialize=True imports.
    Requirements are inherited when omitted; changing them requires actor and
    rationale in the checks JSON. defer_checks maps to --defer-checks: retain
    geometry with pending checks without advancing current. Failed checks also
    leave current unchanged; check_spec(file=revision_id, complete=True) settles
    still-current automatic acceptance after recording deferred evidence.
    Superseded automatic intent stays cancelled, even after selection back.
    stage=True saves a checked candidate without accepting it, including when
    checks pass. Inspect it with view(file=revision_id), then explicitly promote
    it using a caller-known idempotency key. Staging prepares source/STEP/checks;
    optional inspection media can be prepared later with inspect.
    """
    args = _experimental(["import", file], experimental_changes)
    if experimental_inspect:
        args.append("--experimental-inspect")
    if report is True:
        args.append("--report")
    if report_images is True:
        args.append("--inspection-image")
    if full_response:
        args.append("--full-response")
    if idempotency_key is not None:
        args.extend(["--idempotency-key", idempotency_key])
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
    if report is False:
        args.append("--no-report")
    if report_images is False:
        args.append("--no-inspection-image")
    if max_images != 9:
        args.extend(["--max-images", str(max_images)])
    if text_budget != 2000:
        args.extend(["--text-budget", str(text_budget)])
    if checks_file is not None:
        args.extend(["--checks", checks_file])
    if defer_checks:
        args.append("--defer-checks")
    if stage:
        args.append("--stage")
    if inspection_background:
        args.append("--inspection-background")
    if inspection_wait_timeout is not None:
        args.extend(["--inspection-wait-timeout", str(inspection_wait_timeout)])
    from agentcad.inspection_delivery import append_options
    append_options(args, dict(context_window=context_window, response_budget=response_budget,
                             response_text_budget=response_text_budget, image_budget=image_budget, vision_profile=vision_profile))
    result = _invoke(_build_args(args, build_dir), cwd=cwd)
    inspection = result.get("inspection", {})
    return _with_images(result, inspection.get("media", []), cwd=cwd,
                        image_root=inspection.get("inventory", {}).get("image_root"), keep_core=True,
                        full_response=full_response)


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
    stage: bool = False,
    idempotency_key: str | None = None,
    experimental_inspect: bool = False,
    experimental_changes: bool = False,
) -> dict:
    """Execute a build123d script and produce a versioned STEP file with metrics.

    New projects use build123d. Existing CadQuery compatibility projects follow
    the runtime pinned in their agentcad.json.
    Saved STEP versions issue typed geometry IDs by default.
    Use inspect with report=True or entity_id to inspect the result without re-importing.
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
        stage: Save and check a candidate without accepting it. Open the saved
            revision with view, then use promote to accept exact saved bytes.
            Render/export diagnostics separately from change.geometry.
        idempotency_key: Save before sending. Identical source/options replay the
            original result without execution. Edits need a new key. Use
            run_status with only key/cwd/build_dir after source changes/removal.
        checks_file: Optional JSON dimension/preservation requirements. Repairs
            inherit the selected policy when omitted. See docs check-spec.
        defer_checks: Retain pending declared checks instead of evaluating them
            now. Complete with check_spec(revision_id, complete=True); completion
            accepts a still-current normal-run intent if checks pass. Staged or
            superseded work stays unaccepted; reads never change current.
        diff: Compare automatically with the prior successful version. Pass
            False to skip it; explicit diff remains available.
        view: Open or reuse the live project viewer. Share project_viewer.url
            with the human for automatic updates; viewer is a fixed snapshot.
            Pass False with preview=False
            and diff=False to also bypass viewer generation on the core-only
            fast path.
    """
    args = _experimental(["run", script], experimental_changes)
    if experimental_inspect:
        args.append("--experimental-inspect")
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
    if stage:
        args.append("--stage")
    if idempotency_key is not None:
        args.extend(["--idempotency-key", idempotency_key])
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def run_status(idempotency_key: str, cwd: str, build_dir: str | None = None,
               experimental_inspect: bool = False, experimental_changes: bool = False) -> dict:
    """Read the original authored run by key without executing source or selecting work.

    Works after the working script is edited or removed. Returned artifacts and
    check results belong to the original run; they may differ from current work.
    In-progress or interrupted results never authorize an automatic second run.
    """
    args = _experimental(["run", "--idempotency-key", idempotency_key, "--status"], experimental_changes)
    if experimental_inspect:
        args.append("--experimental-inspect")
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
    background: bool = False,
    wait_timeout: float | None = None,
    job_id: str | None = None,
    job_action: Literal["status", "wait", "resume", "cancel"] | None = None,
    response_snapshot: str | None = None,
    artifact: str | None = None,
    artifact_offset: int = 0,
    artifact_bytes: int = 3072,
    artifact_sha256: str | None = None,
    artifact_expires_at: str | None = None,
    context_window: int | None = None,
    response_budget: int | None = None,
    response_text_budget: int | None = None,
    image_budget: int | None = None,
    vision_profile: dict[str, Any] | None = None,
    full_response: bool = False,
    experimental_inspect: bool = False,
) -> Any:
    """Inspect topology of a STEP file (solids, shells, faces, edges, validity).

    Typed IDs, images, reports, jobs, snapshots and artifact-byte retrieval are
    available without opt-in. experimental_inspect is an ignored compatibility input.

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
        report: Return nested inspection plus a whole-response delivery audit,
            defaulting to 2000 estimated text tokens, and ranked image samples.
            retention reports the 30-day/512 MiB cache policy, expiry and protected
            checkpoint bytes. Configure [inspection.cache] in agentcad.toml.
            Expired job results fail explicitly with a fresh inspection action;
            resource_limit jobs stop and can resume after raising their quota.
        report_images: Prepare/deliver report images; false keeps the same analysis without images.
        max_images: Maximum distinct report images, 0..9; overall then alternating part/face-edge samples.
        text_budget: Estimated token allowance for the report JSON (compact UTF-8 bytes / 3).
        background: Start report in a detached local worker; return inspection_job.
        wait_timeout: Seconds to wait for report; deadline does not cancel its worker.
        job_id: Saved inspection job for this file. Uses its original image configuration.
        job_action: status, wait (default with job_id), resume or cancel. Requires job_id/report.
            Cancellation stops between stages and preserves completed artifacts.
        response_snapshot: Retrieve a complete saved delivery response with report=True,
            without CAD work. Returns historical_response; current never advances.
            References report expiry and possible earlier byte-quota eviction.
            Default retention is 30 days/64 MiB per build root; configure
            [inspection.snapshots] in agentcad.toml. Expired or evicted evidence
            fails explicitly with a fresh inspection action, never a substituted report.
        artifact: Read saved bytes over this tool: "manifest", "baseline", or an exact
            returned inventory/image path. Requires the original file/build_dir.
            Returns bounded base64 chunks with full SHA-256 and next.mcp arguments;
            no client filesystem access, CAD work or regeneration. Cannot combine
            with geometry/report/image/job controls. Existing --image attaches PNGs.
        artifact_offset: Byte offset; nonzero requires artifact_sha256 from first chunk.
        artifact_bytes: Maximum raw chunk bytes, 1..49152, default 3072. Encoding
            adds about one third; these explicit reads have no report token budget.
        artifact_sha256: Require the full file's returned SHA-256 on each continuation.
            Reject changed, expired, evicted or wrong-baseline evidence explicitly.
        artifact_expires_at: Retained-artifact expiry from the first chunk; required
            on its continuations. Generated next actions pin both hash and expiry.
        context_window: Derive a 5% combined response target from caller context tokens.
        response_budget: Override that target with a combined JSON/image estimated limit.
        response_text_budget: Bound complete JSON text; default is combined limit when
            known, otherwise 2000, including when no caller options are supplied.
            text_budget separately bounds report preparation.
        image_budget: Inline image-token limit. Without vision_profile, omit constrained images.
        vision_profile: Caller estimate {name: "host", tokens_per_image: 1000} per
            1024-square sheet. Inspect delivery fields for applied estimates/omissions.
        full_response: Skip whole-response compaction; a plain report has its
            previous flat shape. Report preparation limits still apply. Cannot
            combine with caller budgets. Harmless for already-unbounded ID lookups.
    """
    args = ["inspect", file]
    if experimental_inspect:
        args.append("--experimental-inspect")
    if artifact is not None:
        args.extend(["--artifact", artifact])
    if artifact_offset:
        args.extend(["--artifact-offset", str(artifact_offset)])
    if artifact_bytes != 3072:
        args.extend(["--artifact-bytes", str(artifact_bytes)])
    if artifact_sha256 is not None:
        args.extend(["--artifact-sha256", artifact_sha256])
    if artifact_expires_at is not None:
        args.extend(["--artifact-expires-at", artifact_expires_at])
    if full_response:
        args.append("--full-response")
    if report:
        args.append("--report")
    if background:
        args.append("--background")
    if wait_timeout is not None:
        args.extend(["--wait-timeout", str(wait_timeout)])
    if job_id is not None:
        args.extend(["--job", job_id])
    if job_action is not None:
        args.extend(["--job-action", job_action])
    if response_snapshot is not None:
        args.extend(["--response-snapshot", response_snapshot])
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
    from agentcad.inspection_delivery import append_options
    append_options(args, dict(context_window=context_window, response_budget=response_budget,
                             response_text_budget=response_text_budget, image_budget=image_budget, vision_profile=vision_profile))
    result = _invoke(_build_args(args, build_dir), cwd=cwd)
    report_payload = result.get("inspection", result)
    media = report_payload.get("media", []) if report else []
    if image and result.get("image", {}).get("status") == "success":
        media = [{"png": result["image"]["png"], "sha256": result["image"]["png_sha256"]}]
    return _with_images(result, media, cwd=cwd,
                        image_root=report_payload["inventory"]["image_root"] if report and media else None)


@mcp.tool()
def check_spec(file: str, spec_file: str | None = None, cwd: str | None = None, build_dir: str | None = None,
               complete: bool = False, experimental_changes: bool = False) -> dict:
    """Check a file/spec or inspect a retained revision's pinned check receipt.

    File checks return passed, matched_features, and missing_features. Revision
    reads return the saved receipt, validation_state, and check lifecycle.

    Args:
        file: STEP/STP/BREP path, or a returned retained revision ID.
        spec_file: JSON cylindrical-feature spec for file checks. Omit for a
            retained revision: its requirements are already pinned.
        complete: Complete deferred checks and settle the original automatic
            acceptance if still current. Staged/superseded work stays unaccepted.
            current_advanced reports a fresh acceptance; promotion is its durable
            decision, separate from passed/validation_state. Reads are read-only.
        cwd: Source project directory (or a subdirectory); defaults to current.
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    args = _experimental(["check-spec", file], experimental_changes)
    if spec_file is not None:
        args.append(spec_file)
    if complete:
        args.append("--complete")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def promote(idempotency_key: str, cwd: str, revision: str | None = None,
            status: bool = False, target: Literal["cad"] = "cad",
            build_dir: str | None = None, experimental_changes: bool = False) -> dict:
    """Accept an exact saved CAD revision or read its durable operation status.

    Save idempotency_key before sending. revision is a returned retained ID;
    omit it with status=True to read the original decision without mutation.
    Retrying the same revision/key resumes copying or replays its saved receipt.
    A key cannot name another revision/target. Failed checks and changed selection
    refuse acceptance; complete evidence or repair, then use a new key.
    No CAD source is executed. Staged candidates remain unaccepted until this
    explicit call. Replay/status reports current_advanced=False and separately
    identifies the original receipt versus the currently accepted revision.
    build_dir selects the same artifact/history root used to stage the candidate.
    """
    args = _experimental(["promote"], experimental_changes)
    if revision is not None:
        args.append(revision)
    args.extend(["--idempotency-key", idempotency_key, "--target", target])
    if status:
        args.append("--status")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def changes_list(cwd: str, build_dir: str | None = None, limit: int = 20, before: str | None = None,
                 experimental_changes: bool = False) -> dict:
    """Read retained CAD attempts, including failures and interrupted work.

    Newest first, limit 1..100 (default 20). Unreadable/missing request records
    follow attempts with known ordering. Continue with returned next_before.
    Each row uses its immutable r_ revision ID, not a display label. Working and
    accepted selections are separate. Damaged rows report integrity=unavailable;
    other history stays readable. Never selects, accepts, checks or reruns CAD.
    """
    args = _experimental(["changes", "list", "--limit", str(limit)], experimental_changes)
    if before is not None:
        args.extend(["--before", before])
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def changes_show(revision: str, cwd: str, build_dir: str | None = None,
                 experimental_changes: bool = False) -> dict:
    """Read a retained revision's verified source, geometry, checks and diagnostics.

    revision is a returned r_ ID, working, or accepted (resolved once per call).
    Labels and paths are not accepted as IDs. Source/geometry/artifact paths name
    immutable retained files; edited working scripts are not read. Checks are
    latest saved evidence; promotion receipts preserve their original decisions.
    Diagnostic text is capped at 2000 characters per field; use records.outcome
    for full logs/artifact references. Artifacts/promotions are bounded with
    omitted counts. No geometry is substituted for a no-output failed revision.
    This is read-only and does not complete checks or accept the candidate.
    """
    return _invoke(_build_args(_experimental(["changes", "show", revision], experimental_changes), build_dir), cwd=cwd)


@mcp.tool()
def changes_compare(baseline: str, revision: str, cwd: str, build_dir: str | None = None,
                    experimental_changes: bool = False) -> dict:
    """Compare exact retained revisions' saved measurements, checks and policies.

    Each reference is an r_ ID, working, or accepted, resolved once. Reports
    policy/definition changes separately from pass/fail changes, so a relaxed
    requirement cannot masquerade as preserved geometry. Numeric deltas use
    stored execution metrics; they do not prove occupied-space equality. No CAD
    executes, checks complete, selection changes or promotion occurs. For a new
    geometric analysis use the returned diff action on the two verified STEPs;
    missing geometry is explicit and never replaced by the accepted model.
    """
    return _invoke(_build_args(_experimental(["changes", "compare", baseline, revision], experimental_changes), build_dir), cwd=cwd)


@mcp.tool()
def changes_restore(idempotency_key: str, cwd: str, revision: str | None = None,
                    label: str | None = None, status: bool = False,
                    build_dir: str | None = None, experimental_changes: bool = False) -> dict:
    """Restore verified saved source/model/artifacts as NEW STAGED work.

    Supply revision (r_ ID, working, accepted), label and a unique key. Current
    intended requirements apply; older geometry can fail them. No CAD executes,
    accepted model/history is unchanged. Copied review assets are historical;
    use view on the returned revision for a fresh review. Retry identical inputs
    with the same key. status=true needs only the key/cwd/build_dir and reads
    the original result without executing, selecting or checking again.
    """
    args = _experimental(["changes", "restore"], experimental_changes)
    if revision is not None:
        args.append(revision)
    args.extend(["--idempotency-key", idempotency_key])
    if label is not None:
        args.extend(["--label", label])
    if status:
        args.append("--status")
    return _invoke(_build_args(args, build_dir), cwd=cwd)


@mcp.tool()
def changes_repair(idempotency_key: str, cwd: str, revision: str | None = None,
                   source: str | None = None, label: str | None = None,
                   params: str | None = None, status: bool = False,
                   build_dir: str | None = None, experimental_changes: bool = False) -> dict:
    """Execute edited Python CAD source as NEW STAGED work linked to a revision.

    Supply revision, source, label and a unique key. Inherits saved runtime and
    parameter overrides, plus current intended requirements. params optionally
    replaces pinned overrides using key=value,key2=value2 (empty string clears). Never accepts the result;
    review, then explicitly promote. Imported CAD supports restore only.
    Identical retry/key replays; changed inputs need a new key. status=true
    accepts only the key/cwd/build_dir, works without source, and never executes
    source or changes selection. Interrupted requests never automatically rerun.
    """
    args = _experimental(["changes", "repair"], experimental_changes)
    if revision is not None:
        args.append(revision)
    if source is not None:
        args.append(source)
    args.extend(["--idempotency-key", idempotency_key])
    if label is not None:
        args.extend(["--label", label])
    if params is not None:
        args.extend(["--params", params])
    if status:
        args.append("--status")
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
def context(cwd: str, build_dir: str | None = None, experimental_changes: bool = False) -> dict:
    """Show project state, including interrupted-version recovery candidates.

    Args:
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Initialize this root first.
    """
    return _invoke(_build_args(_experimental(["context"], experimental_changes), build_dir), cwd=cwd)


@mcp.tool()
def recover(version_dir: str, cwd: str, make_current: bool = False, build_dir: str | None = None,
            experimental_changes: bool = False) -> dict:
    """Validate and reconcile an interrupted version directory safely.

    Args:
        version_dir: Direct version directory name reported by context.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
        make_current: Explicitly make the recovered successful version current.
    """
    args = _experimental(["recover", version_dir], experimental_changes)
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
def view(file: str, cwd: str, build_dir: str | None = None, validation: bool = False,
         experimental_changes: bool = False) -> dict:
    """Open a GLB, STEP, or retained revision in the browser via three.js.

    Args:
        file: GLB/STEP path, or retained revision ID to review its diagnostic
            geometry and pinned check receipt without changing current.
        validation: Show failure markers and conditional repair guidance for a STEP file.
        cwd: Source project directory (or a subdirectory).
        build_dir: Optional artifact/history root, relative to the project root.
            Overrides agentcad.toml for this call only. Use returned artifact paths.
    """
    args = _experimental(["view", file], experimental_changes)
    if validation:
        args.append("--validation")
    return _invoke(_build_args(args, build_dir), cwd=cwd)
