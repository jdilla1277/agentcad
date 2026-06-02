import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad.manifest import MANIFEST_FILE


@click.command()
@click.option("--name", default=None, help="Project name (defaults to directory name).")
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help=(
        "Default CAD engine for `agentcad run` in this project. "
        "Sets the project mode — scripts that don't import either library "
        "are routed to this engine, and `agentcad docs` shows engine-specific "
        "content. Defaults to cadquery (omitted from manifest)."
    ),
)
@click.option(
    "--force", is_flag=True,
    help="Overwrite an existing agentcad.json. Use when re-initializing a project.",
)
def init(name, runtime, force):
    """Initialize a new agentcad project."""
    manifest_path = Path.cwd() / MANIFEST_FILE

    if manifest_path.exists() and not force:
        click.echo(json.dumps({
            "command": "init",
            "status": "error",
            "message": f"{MANIFEST_FILE} already exists",
            "suggestion": (
                "Pass --force to overwrite, or run `agentcad context` to see "
                "the current project state."
            ),
        }))
        sys.exit(1)

    project_name = name if name else Path.cwd().name
    manifest = _build_manifest(project_name, runtime)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    response = {
        "command": "init",
        "status": "success",
        "project": project_name,
    }
    if runtime is not None:
        response["runtime"] = runtime
    if force:
        response["overwrote_existing"] = True
    click.echo(json.dumps(response))


def _build_manifest(project_name: str, runtime: str | None) -> dict:
    manifest = {
        "name": project_name,
        "version": "0.1.0",
        "created": datetime.now(timezone.utc).isoformat(),
        "versions": [],
    }
    if runtime is not None:
        manifest["runtime"] = runtime
    return manifest


def _bootstrap_manifest(name: str | None = None, runtime: str | None = None) -> None:
    """Create a manifest in the current directory. Used by `agentcad import
    --init` so an agent handed a STEP in a fresh folder doesn't have to
    issue two commands."""
    manifest_path = Path.cwd() / MANIFEST_FILE
    project_name = name if name else Path.cwd().name
    manifest = _build_manifest(project_name, runtime)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
