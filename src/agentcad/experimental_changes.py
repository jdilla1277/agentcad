"""Explicit, invocation-scoped opt-in for work-in-progress change workflows."""

from functools import wraps
from pathlib import Path
import re

import click

from agentcad.project import ProjectError

FLAG = "--experimental-changes"
_COMMANDS = {
    "run",
    "import_cmd",
    "promote",
    "check_spec",
    "view",
    "context",
    "recover",
    "list_changes",
    "show_change",
    "compare_changes",
    "restore_change",
    "repair_change",
}


def enabled():
    ctx = click.get_current_context(silent=True)
    return bool(ctx and ctx.meta.get("experimental_changes"))


def option(function):
    @click.option(
        FLAG,
        is_flag=True,
        default=False,
        help="WIP: explicitly opt in to retained changes, requirements, staging and promotion for this call.",
    )
    @wraps(function)
    def wrapped(*args, experimental_changes=False, **kwargs):
        ctx = click.get_current_context()
        group_opt_in = bool(
            ctx.parent
            and ctx.parent.command.name == "changes"
            and ctx.parent.params.get("experimental_changes")
        )
        ctx.meta["experimental_changes"] = experimental_changes or group_opt_in
        return function(*args, **kwargs)

    return wrapped


def required(message="This change workflow is experimental and incomplete."):
    if not enabled():
        raise ProjectError(
            "experimental_changes_required",
            message,
            f"Repeat this command with {FLAG} to explicitly opt in to the WIP change workflow.",
        )


def guard_history(root):
    """Read only: legacy writers cannot bypass a selected policy or candidate."""
    if enabled():
        return
    working = root / ".agentcad/changes/working.json"
    attempts = root / ".agentcad/changes/attempts"
    if not working.exists():
        if attempts.exists() and any(attempts.iterdir()):
            required(
                "Existing experimental attempts have no working selection; restore or inspect that state with the WIP flag."
            )
        return
    from agentcad.change_store import Attempt, ChangeStore
    from agentcad.change_promotion import PromotionStore

    try:
        store = ChangeStore(root)
        selection = store._state()
        revision = selection["revision_id"]
        if not revision:
            return
        request = Attempt(store, revision)._read("request.json", "attempt_request")
        promotions = PromotionStore(root)
        manifest = promotions._manifest()
        operations = promotions._state(manifest)["operations"].values()
        accepted = any(
            operation["result"].get("outcome") == "promoted"
            and operation["request"]["candidate"]["revision_id"] == revision
            and operation["request"]["selection_generation"]
            == selection["selection_generation"]
            for operation in operations
        )
        active = bool(request.get("check_policy") or not accepted)
    except (OSError, ValueError, KeyError) as exc:
        required(f"Cannot verify the existing experimental working state: {exc}")
        return
    if active:
        required(
            "This build history has experimental requirements or unpromoted work; an ordinary call cannot bypass them."
        )


def validate(ctx, values):
    """Run after root selection and before preflight, source reads or execution."""
    name = ctx.command.name
    if ctx.find_root().invoked_subcommand == "changes" or name == "promote":
        required()
    if name == "promote":
        ctx.meta["experimental_promotion_status"] = values.get("status_only", False)
    if name in {"check-spec", "view"}:
        file = values.get("file", "")
        if values.get("complete") or (
            file.startswith("r_") and not Path(file).exists()
        ):
            required()
            ctx.meta["experimental_revision_read"] = True
            ctx.meta["experimental_check_completion"] = values.get("complete", False)
    if name in {"run", "import"}:
        from agentcad.file_detect import is_recognized_cad_extension

        keyed_authored_run = (
            name == "run"
            and values.get("idempotency_key") is not None
            and not is_recognized_cad_extension(values.get("script") or "")
        )
        if (
            values.get("stage")
            or values.get("checks_file") is not None
            or values.get("defer_checks")
            or keyed_authored_run
            or name == "run"
            and values.get("request_status")
        ):
            required()
        if not values.get("dry_run") and not values.get("request_status"):
            from agentcad.project import get_project

            guard_history(get_project().build_root)
    if name == "recover":
        from agentcad.project import get_project

        guard_history(get_project().build_root)


_ACTION = re.compile(
    r"^(agentcad (?:changes (?:list|show|compare|restore|repair)|run|import|promote|check-spec|view|context|recover))(?=\s|$)"
)


def present(payload):
    """Annotate the current call and keep executable recovery actions opted in."""
    if not enabled():
        return payload

    def action(value):
        if isinstance(value, str) and FLAG not in value:
            return _ACTION.sub(r"\1 " + FLAG, value, count=1)
        if isinstance(value, list):
            return [action(item) for item in value]
        if isinstance(value, dict):
            return {key: action(item) for key, item in value.items()}
        return value

    def visit(value):
        if isinstance(value, dict):
            if "content_sha256" in value and "kind" in value:
                # Signed historical evidence must retain its exact contents.
                return value
            return {
                key: (
                    action(item)
                    if key in {"next_actions", "suggestion", "actions", "mcp"}
                    else visit(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    return {
        **visit(payload),
        "experimental_changes": {"enabled": True, "status": "work_in_progress"},
    }
