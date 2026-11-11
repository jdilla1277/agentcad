"""Browse retained revisions and compare their saved evidence."""

import json
import shlex
import sys

import click

from agentcad.change_history import ChangeHistory
from agentcad.experimental_changes import option
from agentcad.manifest import load_manifest
from agentcad.project import ProjectError, get_project, project_options, resolve_project


class ChangesGroup(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (ProjectError, OSError, ValueError) as exc:
            payload = (
                exc.payload("changes")
                if isinstance(exc, ProjectError)
                else {
                    "command": "changes",
                    "status": "error",
                    "reason": "change_history_unavailable",
                    "message": str(exc),
                    "suggestion": "Inspect the selected build directory and retained evidence; see agentcad docs changes.",
                }
            )
            click.echo(
                json.dumps(
                    {
                        **payload,
                        "action": ctx.invoked_subcommand,
                        "current_advanced": False,
                        "more_at": "agentcad docs changes",
                    }
                )
            )
            sys.exit(1)


@click.group("changes", cls=ChangesGroup)
@click.option(
    "--build-dir",
    default=None,
    metavar="PATH",
    help="Artifact/history root; may also be supplied after the subcommand.",
)
@option
def changes(build_dir):
    """WIP: read saved attempts; requires --experimental-changes.

    Never select, accept or rerun CAD.

    Use returned r_ revision IDs; working and accepted resolve the current
    retained selections once. Labels and file paths are not revision IDs.
    """
    # Defer default resolution to the leaf: its explicit override must work
    # even if the configured default history is missing or damaged.
    if build_dir is not None:
        click.get_current_context().meta["project_layout"] = resolve_project(build_dir)


def _history():
    load_manifest(command="changes")
    return ChangeHistory(get_project().build_root)


def _root():
    return (
        " --build-dir " + shlex.quote(str(get_project().build_root))
        if get_project().configured
        else ""
    )


def _emit(action, result, actions=None):
    click.echo(
        json.dumps(
            {
                "command": "changes",
                "action": action,
                "status": "success",
                "current_advanced": False,
                **result,
                **({"next_actions": actions} if actions else {}),
                "more_at": "agentcad docs changes",
            }
        )
    )


@changes.command("list")
@click.option("--limit", type=click.IntRange(1, 100), default=20, show_default=True)
@click.option("--before", default=None, help="Use next_before from the previous page.")
@project_options
def list_changes(limit, before):
    """List retained attempts, including failures and unavailable evidence."""
    result = _history().list(limit=limit, before=before)
    actions = (
        [
            f"agentcad changes show {result['changes'][0]['revision_id']}{_root()} — inspect the saved attempt"
        ]
        if result["changes"]
        else None
    )
    _emit("list", result, actions)


@changes.command("show")
@click.argument("revision")
@project_options
def show_change(revision):
    """Read source, geometry, checks and diagnostics for one saved revision."""
    result = _history().show(revision)
    if result["promotions"]:
        result["notes"] = [
            "Checks are the latest saved evidence; promotion receipts preserve their original decision and evidence. A later passing check does not reopen a final refusal."
        ]
    revision_id = result["change"]["revision_id"]
    actions = [
        f"agentcad check-spec {revision_id}{_root()} — inspect the pinned check receipt"
    ]
    if result["geometry"]:
        actions.insert(
            0,
            f"agentcad view {revision_id}{_root()} — open this fixed candidate snapshot",
        )
    _emit("show", result, actions)


@changes.command("compare")
@click.argument("baseline")
@click.argument("revision")
@project_options
def compare_changes(baseline, revision):
    """Compare saved measurements/checks; this does not run a geometry diff."""
    result = _history().compare(baseline, revision)
    geometry = result["geometry_comparison"]
    if geometry["baseline"] and geometry["candidate"]:
        paths = " ".join(
            shlex.quote(geometry[key]["path"]) for key in ("baseline", "candidate")
        )
        actions = [
            f"agentcad diff {paths} --no-daemon{_root()} — compare the exact saved occupied geometry"
        ]
    else:
        actions = [
            f"agentcad changes show {result['candidate']['revision_id']}{_root()} — inspect retained source and failure evidence"
        ]
    _emit("compare", result, actions)
