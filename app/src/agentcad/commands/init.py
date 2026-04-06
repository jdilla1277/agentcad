import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad.manifest import MANIFEST_FILE


@click.command()
@click.option("--name", default=None, help="Project name (defaults to directory name).")
def init(name):
    """Initialize a new agentcad project."""
    manifest_path = Path.cwd() / MANIFEST_FILE

    if manifest_path.exists():
        click.echo(json.dumps({
            "command": "init",
            "status": "error",
            "message": f"{MANIFEST_FILE} already exists",
        }))
        sys.exit(1)

    project_name = name if name else Path.cwd().name
    created = datetime.now(timezone.utc).isoformat()

    manifest = {
        "name": project_name,
        "version": "0.1.0",
        "created": created,
        "versions": [],
    }

    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    click.echo(json.dumps({
        "command": "init",
        "status": "success",
        "project": project_name,
    }))
