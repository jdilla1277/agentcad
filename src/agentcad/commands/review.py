"""Agent- and human-facing commands for spatial review comments."""

import json
import math
import sys
import webbrowser
from pathlib import Path

import click

from agentcad.commands.parts import _load_version_meta, _parts_from_meta, _resolve_version
from agentcad.manifest import load_manifest
from agentcad.review_server import viewer_url
from agentcad.reviews import (
    create_comment,
    get_comment,
    list_comments,
    reply_to_comment,
    submit_drafts,
    transition_comment,
)


def _error(message):
    click.echo(json.dumps({"command": "review", "status": "error", "message": message}))
    sys.exit(1)


@click.group("review")
def review_cmd():
    """Read and manage human comments from the review viewer."""


@review_cmd.command("list")
@click.option("--status", type=click.Choice(["draft", "open", "addressed", "resolved"]))
def list_review_comments(status):
    """List spatial review comments as structured JSON."""
    load_manifest(command="review")
    comments = list_comments(Path.cwd(), status=status)
    click.echo(json.dumps({
        "command": "review",
        "action": "list",
        "status": "success",
        "filter": status,
        "count": len(comments),
        "comments": comments,
    }))


@review_cmd.command("show")
@click.argument("comment_id")
def show_review_comment(comment_id):
    """Show one review comment."""
    load_manifest(command="review")
    comment = get_comment(Path.cwd(), comment_id)
    if comment is None:
        _error(f"Comment '{comment_id}' not found")
    click.echo(json.dumps({"command": "review", "action": "show", "status": "success", "comment": comment}))


@review_cmd.command("submit")
@click.argument("comment_ids", nargs=-1)
def submit_review(comment_ids):
    """Submit draft viewer comments as one review batch."""
    load_manifest(command="review")
    try:
        batch = submit_drafts(Path.cwd(), list(comment_ids) or None)
    except ValueError as exc:
        _error(str(exc))
    click.echo(json.dumps({"command": "review", "action": "submit", "status": "success", **batch}))


@review_cmd.command("mark-addressed")
@click.argument("comment_id")
@click.option("--version", "version_ref", required=True, help="Revision that addresses the comment.")
@click.option("--message", help="Optional explanation for the human reviewer.")
def mark_addressed(comment_id, version_ref, message):
    """Mark an open human comment addressed by a revision."""
    manifest = load_manifest(command="review")
    version = _resolve_version(manifest, version_ref)
    if version is None:
        _error(f"Version '{version_ref}' not found")
    try:
        comment = transition_comment(
            Path.cwd(), comment_id, "address", version=version.get("version"),
            actor="agent", message=message,
        )
    except KeyError:
        _error(f"Comment '{comment_id}' not found")
    except ValueError as exc:
        _error(str(exc))
    click.echo(json.dumps({"command": "review", "action": "mark-addressed", "status": "success", "comment": comment}))


def _optional_version(manifest, version_ref):
    if version_ref is None:
        return None
    version = _resolve_version(manifest, version_ref)
    if version is None:
        _error(f"Version '{version_ref}' not found")
    return version.get("version")


def _previous_successful_version(manifest, version_entry):
    previous = None
    for candidate in manifest.get("versions", []):
        if candidate is version_entry or (
            candidate.get("version") == version_entry.get("version")
            and candidate.get("path") == version_entry.get("path")
        ):
            return previous
        if candidate.get("status") == "success":
            previous = candidate
    return previous


def _part_from_version(version_entry, part_id):
    meta = _load_version_meta(version_entry)
    for part in _parts_from_meta(meta):
        aliases = {str(part.get("id"))}
        if "legacy_id" in part:
            aliases.add(str(part["legacy_id"]))
        if part_id in aliases:
            return part, meta
    available = sorted(str(part.get("id")) for part in _parts_from_meta(meta))
    _error(
        f"Part '{part_id}' not found in revision '{version_entry.get('label')}'. "
        f"Available parts: {', '.join(available) or 'none'}"
    )


def _point_anchor(part, point_value):
    if point_value is None:
        return {"kind": "part"}
    try:
        values = [float(value.strip()) for value in point_value.split(",")]
    except ValueError:
        values = []
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        _error("--point-mm must be three finite comma-separated values: X,Y,Z")
    bounds = (part.get("metrics") or {}).get("bounding_box") or {}
    axes = [bounds.get(axis) for axis in ("x", "y", "z")]
    if any(not isinstance(axis, list) or len(axis) != 2 for axis in axes):
        _error(f"Part '{part.get('id')}' has no bounding box for --point-mm anchoring")
    relative = []
    for value, axis in zip(values, axes):
        extent = float(axis[1]) - float(axis[0])
        relative.append((value - float(axis[0])) / extent if extent else 0.5)
    return {"kind": "surface", "point_mm": values, "part_relative": relative}


@review_cmd.command("comment")
@click.option("--message", required=True, help="Comment text.")
@click.option("--part", "part_id", required=True, help="Named part id to attach to.")
@click.option(
    "--scope", type=click.Choice(["current", "previous", "both"]),
    default="current", show_default=True, help="Revision side the comment applies to.",
)
@click.option(
    "--version", "version_ref", default="current", show_default=True,
    help="Saved viewer revision whose Current/Previous sides should be used.",
)
@click.option(
    "--point-mm", metavar="X,Y,Z",
    help="Optional exact CAD-space pin location; defaults to the part center.",
)
def create_review_comment(message, part_id, scope, version_ref, point_mm):
    """Start a new spatial review thread as the agent."""
    manifest = load_manifest(command="review")
    current = _resolve_version(manifest, version_ref)
    if current is None:
        _error(f"Version '{version_ref}' not found")
    previous = _previous_successful_version(manifest, current)
    if scope in {"previous", "both"} and previous is None:
        _error(f"Revision '{version_ref}' has no previous successful revision")

    source = previous if scope == "previous" else current
    part, source_meta = _part_from_version(source, part_id)
    if scope == "both":
        _part_from_version(previous, part_id)

    has_previous = previous is not None
    model = {"current": "b" if has_previous else "a", "previous": "a", "both": "both"}[scope]
    source_model = "a" if scope == "previous" else ("b" if has_previous else "a")
    payload = {
        "text": message,
        "source_version": source_meta.get("version", source.get("version")),
        "source_label": source_meta.get("label", source.get("label")),
        "target": {"model": model, "source_model": source_model, "part_id": str(part.get("id"))},
        "anchor": _point_anchor(part, point_mm),
        "view": {"mode": "side-by-side" if scope == "both" else f"single-{source_model}"},
    }
    try:
        comment = create_comment(Path.cwd(), payload, actor="agent", status="open")
    except ValueError as exc:
        _error(str(exc))
    click.echo(json.dumps({
        "command": "review", "action": "comment", "status": "success", "comment": comment,
    }))


@review_cmd.command("reply")
@click.argument("comment_id")
@click.option("--message", required=True, help="Reply text.")
@click.option("--version", "version_ref", help="Revision associated with this reply.")
def reply_to_review_comment(comment_id, message, version_ref):
    """Reply to an open review thread as the agent."""
    manifest = load_manifest(command="review")
    version = _optional_version(manifest, version_ref)
    try:
        comment = reply_to_comment(
            Path.cwd(), comment_id, message, actor="agent", version=version
        )
    except KeyError:
        _error(f"Comment '{comment_id}' not found")
    except ValueError as exc:
        _error(str(exc))
    click.echo(json.dumps({
        "command": "review", "action": "reply", "status": "success", "comment": comment,
    }))


@review_cmd.command("resolve")
@click.argument("comment_id")
@click.option("--message", help="Optional closing reply.")
@click.option("--version", "version_ref", help="Revision associated with this resolution.")
def resolve_review_comment(comment_id, message, version_ref):
    """Resolve an open review thread as the agent."""
    manifest = load_manifest(command="review")
    version = _optional_version(manifest, version_ref)
    try:
        comment = transition_comment(
            Path.cwd(), comment_id, "resolve", version=version,
            actor="agent", message=message,
        )
    except KeyError:
        _error(f"Comment '{comment_id}' not found")
    except ValueError as exc:
        _error(str(exc))
    click.echo(json.dumps({
        "command": "review", "action": "resolve", "status": "success", "comment": comment,
    }))


@review_cmd.command("reopen")
@click.argument("comment_id")
@click.option("--message", help="Optional explanation for reopening.")
def reopen_review_comment(comment_id, message):
    """Reopen a resolved review thread as the agent."""
    load_manifest(command="review")
    try:
        comment = transition_comment(
            Path.cwd(), comment_id, "reopen", actor="agent", message=message
        )
    except KeyError:
        _error(f"Comment '{comment_id}' not found")
    except ValueError as exc:
        _error(str(exc))
    click.echo(json.dumps({
        "command": "review", "action": "reopen", "status": "success", "comment": comment,
    }))


@review_cmd.command("open")
@click.argument("ref", default="current")
@click.option("--open/--no-open", "open_browser", default=True)
def open_review(ref, open_browser):
    """Open a version viewer with persistent comments enabled."""
    manifest = load_manifest(command="review")
    version = _resolve_version(manifest, ref)
    if version is None:
        _error(f"Version '{ref}' not found")
    meta = _load_version_meta(version)
    relative = meta.get("viewer")
    if not relative:
        _error(f"Version '{ref}' has no viewer artifact")
    path = (Path.cwd() / relative).resolve()
    if not path.exists():
        _error(f"Viewer not found for version '{ref}'")
    try:
        url = viewer_url(path, project_dir=Path.cwd(), require_review=True)
    except RuntimeError as exc:
        _error(str(exc))
    opened = webbrowser.open(url) is not False if open_browser else False
    click.echo(json.dumps({
        "command": "review",
        "action": "open",
        "status": "success",
        "version": version.get("version"),
        "label": version.get("label"),
        "url": url,
        "viewer_opened": opened,
    }))
