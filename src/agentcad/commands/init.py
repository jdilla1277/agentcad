import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad import __version__
from agentcad.manifest import MANIFEST_FILE
from agentcad.runners import dispatch


@click.command()
@click.option("--name", default=None, help="Project name (defaults to directory name).")
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help=(
        "Default CAD engine for `agentcad run` in this project. "
        "Pins the project mode; scripts written for the other engine require "
        "an explicit `agentcad run --runtime` override. Engine-specific docs "
        "follow this mode. Defaults to the global default runtime (currently "
        f"{dispatch.DEFAULT_RUNTIME}) and is recorded in the manifest."
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
        "runtime": manifest["runtime"],
    }
    cad_input = _preferred_cad_input(Path.cwd())
    if cad_input is not None:
        response["next_actions"] = [
            f"agentcad import {shlex.quote(cad_input.name)} — adopt the existing "
            "CAD as a versioned baseline and create edit.py"
        ]
        response["more_at"] = "agentcad docs editing"
    else:
        response["next_actions"] = [
            "agentcad docs quickstart — follow the first-script workflow for "
            "this project",
            f"agentcad docs preamble — see the names available in "
            f"{manifest['runtime']} scripts",
        ]
        response["more_at"] = "agentcad docs quickstart"
    if force:
        response["overwrote_existing"] = True
    click.echo(json.dumps(response))


def _preferred_cad_input(directory: Path) -> Path | None:
    """Return one deterministic editable CAD file from ``directory``.

    ``init`` intentionally looks only at direct children. Descending into
    version folders would make a generated ``vN_*/output.step`` look like a
    new source input. STEP/STP sort ahead of BREP because they are the common
    interchange path; names break ties so the response is stable.
    """
    ranks = {".step": 0, ".stp": 0, ".brep": 1}
    try:
        candidates = [
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in ranks
        ]
    except OSError:
        return None
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda path: (
            ranks[path.suffix.lower()],
            path.name.casefold(),
            path.name,
        ),
    )


def _build_manifest(project_name: str, runtime: str | None) -> dict:
    from agentcad.runners import dispatch

    manifest = {
        "name": project_name,
        "version": __version__,
        "created": datetime.now(timezone.utc).isoformat(),
        "runtime": runtime or dispatch.DEFAULT_RUNTIME,
        "versions": [],
    }
    return manifest


def _bootstrap_manifest(name: str | None = None, runtime: str | None = None) -> None:
    """Create a manifest in the current directory. Used by `agentcad import
    --init` so an agent handed a STEP in a fresh folder doesn't have to
    issue two commands."""
    manifest_path = Path.cwd() / MANIFEST_FILE
    project_name = name if name else Path.cwd().name
    manifest = _build_manifest(project_name, runtime)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
