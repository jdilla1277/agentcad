import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad import __version__
from agentcad.commands.instructions import install_instructions
from agentcad.commands.skill import install_skill
from agentcad.guide import guide_fingerprint
from agentcad.manifest import MANIFEST_FILE
from agentcad.project import ProjectError, get_project, project_options
from agentcad.versioning import _version_lock, atomic_write_json
from agentcad.runners import dispatch


def install_agent_setup(cwd: Path, runtime: str) -> dict:
    """Install every agent-guide surface for a project and report evidence.

    A setup failure must not fail project initialization — the manifest is
    already written and the CLI remains usable — so errors are reported in
    the returned status instead of raised.
    """
    try:
        skill_result = install_skill(cwd, runtime)
        instructions_result = install_instructions(cwd, "auto", runtime)
    except OSError as exc:
        return {
            "status": "error",
            "message": f"agent guide install failed: {exc}",
            "suggestion": (
                "Run `agentcad instructions install` and `agentcad skill "
                "install` once the underlying issue is fixed."
            ),
        }
    return {
        "status": "ready",
        "guide_fingerprint": guide_fingerprint(runtime),
        "targets": [
            {
                "name": "claude-skill",
                "paths": [skill_result["path"]],
                "activation": "skill-discovery",
            },
            {
                "name": "project-instructions",
                "paths": instructions_result["paths"],
                "activation": "always-loaded",
            },
        ],
    }


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
@click.option(
    "--no-agent-setup", is_flag=True,
    help=(
        "Skip installing the agent guide (AGENTS.md/CLAUDE.md block and Claude "
        "skill). By default init installs both so agents receive the operating "
        "guide automatically."
    ),
)
@project_options
def init(name, runtime, force, no_agent_setup):
    """Initialize a new agentcad project."""
    layout = get_project()
    manifest_path = layout.manifest_path

    if manifest_path.exists() and not force:
        click.echo(json.dumps({
            "command": "init",
            "status": "error",
            "message": f"{MANIFEST_FILE} already exists",
            "suggestion": (
                "The project is already initialized. Run `agentcad context` "
                "to see its state and agent-guide status, or `agentcad "
                "instructions install` to refresh the agent guide. Pass "
                "--force only to recreate agentcad.json and start over."
            ),
        }))
        sys.exit(1)

    # Fail before writing anything if the requested engine is not installed.
    # A manifest pinned to an absent CadQuery would make every later `run`
    # fail; better to say so now with the exact pip command.
    if runtime:
        try:
            dispatch.require_runtime_available(runtime)
        except ValueError as exc:
            click.echo(json.dumps({
                "command": "init",
                "status": "error",
                "message": str(exc),
            }))
            sys.exit(1)

    project_name = name if name else layout.project_root.name
    manifest = _build_manifest(project_name, runtime)
    if layout.configured:
        manifest["project_root"] = str(layout.project_root)
    with _version_lock(layout.build_root):
        if manifest_path.exists() and not force:
            raise ProjectError("already_initialized", f"{manifest_path} already exists",
                               "Run agentcad context to inspect the existing history.")
        atomic_write_json(manifest_path, manifest)

    response = {
        "command": "init",
        "status": "success",
        "project": project_name,
        "runtime": manifest["runtime"],
    }
    if no_agent_setup:
        response["agent_setup"] = {"status": "skipped"}
    else:
        response["agent_setup"] = install_agent_setup(
            layout.project_root, manifest["runtime"]
        )
    cad_input = _preferred_cad_input(layout.project_root)
    build_arg = " --build-dir " + shlex.quote(str(layout.build_root)) if layout.configured else ""
    if cad_input is not None:
        response["next_actions"] = [
            f"agentcad import {shlex.quote(cad_input.name)}{build_arg} — adopt the existing "
            "CAD as a versioned baseline and create edit.py"
        ]
        response["more_at"] = "agentcad docs editing"
    else:
        response["next_actions"] = [
            f"agentcad docs quickstart{build_arg} — follow the first-script workflow for "
            "this project",
            f"agentcad docs preamble{build_arg} — see the names available in "
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
    """Create a manifest in the selected build directory. Used by `agentcad import
    --init` so an agent handed a STEP in a fresh folder doesn't have to
    issue two commands. Installs the agent guide like `agentcad init` does."""
    layout = get_project()
    manifest_path = layout.manifest_path
    project_name = name if name else layout.project_root.name
    manifest = _build_manifest(project_name, runtime)
    if layout.configured:
        manifest["project_root"] = str(layout.project_root)
    with _version_lock(layout.build_root):
        if not manifest_path.exists():
            atomic_write_json(manifest_path, manifest)
    install_agent_setup(layout.project_root, manifest["runtime"])
