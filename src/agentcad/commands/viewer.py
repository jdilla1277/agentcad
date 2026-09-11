"""Diagnostics and explicit opening for the local project viewer."""
import json

import click

from agentcad import project_viewer as live
from agentcad.project import get_project, project_options


@click.command("viewer")
@click.argument("action", type=click.Choice(["open", "status", "stop"]), default="open")
@project_options
def viewer(action):
    """Open the live project, inspect its service, or stop it.

    The saved port and URLs survive stop/start. The next viewed build or
    `agentcad viewer open` restarts the service. Version snapshots remain files.
    """
    try:
        if action == "status":
            result = live.service_status()
        elif action == "stop":
            result = live.stop_service()
        else:
            from agentcad.commands.view import _open_browser
            result = live.open_project(get_project().build_root, _open_browser)
        click.echo(json.dumps({"command": "viewer", "status": "success", **result}))
    except (live.ViewerUnavailable, OSError, ValueError) as exc:
        click.echo(json.dumps({"command": "viewer", "status": "error", "message": str(exc)}))
        raise SystemExit(1)
