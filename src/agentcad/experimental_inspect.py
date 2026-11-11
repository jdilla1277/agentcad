"""Per-call acknowledgment of the unfinished Inspect/import experience."""

from functools import wraps
import hashlib
import re
import shlex

import click

from agentcad.project import ProjectError

FLAG = "--experimental-inspect"
MARKER = "experimental_inspect"


def enabled():
    ctx = click.get_current_context(silent=True)
    return bool(ctx and ctx.meta.get(MARKER))


def option(function):
    @click.option(FLAG, is_flag=True, default=False,
                  help="WIP: opt in to enriched inspection, typed IDs, reports, images and jobs for this call only.")
    @wraps(function)
    def wrapped(*args, experimental_inspect=False, **kwargs):
        click.get_current_context().meta[MARKER] = experimental_inspect
        return function(*args, **kwargs)
    return wrapped


def required(message="The enriched Inspect workflow is experimental and incomplete."):
    if not enabled():
        raise ProjectError("experimental_inspect_required", message,
                           f"Repeat this call with {FLAG} (MCP: experimental_inspect=true) to acknowledge the WIP workflow.")


def forward(args):
    if enabled():
        args.append(FLAG)
    return args


def ordinary_semantic():
    # Opted-in requests retain compatibility with pre-gate retry fingerprints.
    # A new ordinary request explicitly records that it did not start Inspect.
    return {} if enabled() else {MARKER: False}


def guard_retry(command, key):
    if enabled() or not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key):
        return
    from agentcad.import_requests import _read
    from agentcad.project import get_project

    request_id = command + "_" + hashlib.sha256(key.encode()).hexdigest()
    path = get_project().build_root / f".agentcad/{command}_requests/{request_id}/request.json"
    if path.exists():
        try:
            semantic = _read(path, request_id)["semantic"]
            ordinary = semantic.get(MARKER) is False
        except (OSError, ValueError, ProjectError, KeyError, TypeError, AttributeError) as exc:
            raise ProjectError(f"{command}_request_unavailable",
                               f"Cannot verify the saved retry's Inspect mode: {exc}",
                               "Restore the original retry record; no source was read or executed.") from exc
        if not ordinary:
            required("This retry key predates the Inspect gate or belongs to an experimental Inspect call. Its original evidence requires acknowledgment.")


def validate(ctx, values):
    """Before project preflight, source reads, execution or cache writes."""
    if ctx.command.name == "inspect":
        rich = ("entity_id", "with_image", "isolate", "with_report", "background", "wait_timeout",
                "job_id", "job_action", "response_snapshot", "artifact", "artifact_sha256", "artifact_expires_at")
        if any(values.get(name) is not None and values.get(name) is not False for name in rich):
            required()
        if values.get("no_images") or values.get("artifact_offset") or values.get("artifact_bytes", 3072) != 3072:
            required()
    if ctx.command.name in {"import", "inspect"}:
        from agentcad.inspection_delivery import OPTIONS
        if any(values.get(name) is not None for name in OPTIONS):
            required()
        if values.get("max_images", 9) != 9 or values.get("text_budget", 2000) != 2000:
            required()
    if ctx.command.name == "import":
        if values.get("inspection_background") or values.get("inspection_wait_timeout") is not None:
            required()
        for name in ("show_report", "inspection_image"):
            if values.get(name) and ctx.get_parameter_source(name) == click.core.ParameterSource.COMMANDLINE:
                required()
    if ctx.command.name in {"run", "import"}:
        command = ctx.command.name
        if command == "run":
            from agentcad.file_detect import is_recognized_cad_extension
            if is_recognized_cad_extension(values.get("script") or ""):
                command = "import"
        guard_retry(command, values.get("idempotency_key"))


_ACTION = re.compile(r"^(agentcad (?:inspect|import|run))(?=\s|$)")


def present(payload, *, acknowledged=False):
    if not enabled() and not acknowledged:
        return payload

    def action(value):
        if isinstance(value, str):
            # Explanations use an unquoted em dash. A quoted filename can
            # contain that same text, and must remain one unchanged operand.
            boundary, quote, escaped = len(value), None, False
            for index, char in enumerate(value):
                if escaped:
                    escaped = False
                elif char == "\\" and quote != "'":
                    escaped = True
                elif quote:
                    if char == quote:
                        quote = None
                elif char in {"'", '"'}:
                    quote = char
                elif value.startswith(" — ", index):
                    boundary = index
                    break
            command, explanation = value[:boundary], value[boundary:]
            try:
                opted = FLAG in shlex.split(command)
            except ValueError:
                opted = False
            if opted or not _ACTION.match(value):
                return value
            return command + " " + FLAG + explanation
        if isinstance(value, list):
            return [action(item) for item in value]
        if isinstance(value, dict):
            return {key: action(item) for key, item in value.items()}
        return value

    def visit(value):
        if isinstance(value, dict):
            if "content_sha256" in value and "kind" in value:
                return value  # sealed historical evidence stays byte-identical
            result = {key: (action(item) if key in {"next_actions", "suggestion", "actions", "retrieve", "cli", "lookup_example", "image_example", "recovery_command"}
                            else visit(item)) for key, item in value.items()}
            if result.get("tool") in {"inspect", "import_cad", "run", "run_status"} and isinstance(result.get("arguments"), dict):
                result["arguments"] = {**result["arguments"], MARKER: True}
            if isinstance(result.get("retrieve"), dict) and "artifact" in result["retrieve"]:
                result["retrieve"] = {**result["retrieve"], MARKER: True}
            return result
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    result = {**visit(payload), MARKER: {"enabled": True, "status": "work_in_progress"}}
    report = result.get("inspection")
    if report is None and "media_summary" in result and "inventory" in result:
        report = result
    if isinstance(report, dict) and report.get("budget", {}).get("scope") == "inspection_report":
        from agentcad.inspection_report import compact_report
        # MCP adds its exit code after the CLI report is prepared. The flat
        # report's estimate remains the CLI estimate; whole-response budgets
        # account for transport fields separately.
        transport = {}
        if report is result and "_exit_code" in result:
            transport["_exit_code"] = result.pop("_exit_code")
        compact = compact_report(report)
        if report is result:
            result = {**compact, **transport}
        else:
            result["inspection"] = compact
    return result
