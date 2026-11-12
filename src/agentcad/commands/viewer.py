"""Diagnostics and explicit opening for the local project viewer."""

import json

import click

from agentcad import project_viewer as live
from agentcad.project import get_project, project_options


@click.command("viewer")
@click.argument("action", type=click.Choice(["open", "status", "stop"]), default="open")
@click.option(
    "--revision",
    default=None,
    help="WIP: read status for this pinned revision; requires status and --experimental-changes.",
)
@project_options
def viewer(action, revision):
    """Open the live project, inspect its service, or stop it.

    The saved port and URLs survive stop/start. The next viewed build or
    `agentcad viewer open` restarts the service. Version snapshots remain files.
    """
    try:
        from agentcad.experimental_changes import enabled, required, guard_history
        from agentcad.working_viewer import enable, prepare, state

        root = get_project().build_root
        if revision is not None:
            required("Pinned viewer status requires explicit experimental opt-in.")
            if action != "status":
                from agentcad.project import ProjectError

                raise ProjectError(
                    "viewer_revision_status_only",
                    "--revision is a read-only status selector.",
                    "Use viewer status --revision REVISION, or view REVISION to open a pin.",
                )
        if action == "open":
            guard_history(root)
            if (live.runtime_dir(create=False) / "config.json").is_file():
                record = live.read_json(
                    live.project_record(live.project_token(root)), {}
                )
                if record.get("experimental_changes"):
                    required(
                        "Opening an experimental working project requires explicit opt-in."
                    )
        experimental = enabled()
        click.get_current_context().meta["working_viewer_status"] = action == "status"
        if experimental:
            from agentcad.change_store import ChangeStore

            working_revision = ChangeStore(root).working_revision_id
            if action == "open" and working_revision:
                enable(root)
                try:
                    prepare(root, working_revision)
                except (OSError, ValueError, RuntimeError):
                    pass  # Open diagnostics even when geometry cannot render.
        if action == "status":
            result = (
                live.service_status()
                if (live.runtime_dir(create=False) / "config.json").is_file()
                else {"running": False, "port": None}
            )
        elif action == "stop":
            result = live.stop_service()
        else:
            from agentcad.commands.view import _open_browser

            result = live.open_project(get_project().build_root, _open_browser)
        if experimental and action != "stop":
            record = (
                live.read_json(live.project_record(live.project_token(root)), {})
                if (live.runtime_dir(create=False) / "config.json").is_file()
                else {}
            )
            result.update(state(root, record, revision))
        click.echo(json.dumps({"command": "viewer", "status": "success", **result}))
    except (live.ViewerUnavailable, OSError, ValueError) as exc:
        click.echo(
            json.dumps({"command": "viewer", "status": "error", "message": str(exc)})
        )
        raise SystemExit(1)
