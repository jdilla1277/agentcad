import json
from pathlib import Path

import click

from agentcad import __version__
from agentcad.manifest import load_manifest
from agentcad.recovery import recovery_summary
from agentcad.reviews import review_summary


@click.command()
def context():
    """Show the current project context."""
    manifest = load_manifest(command="context")

    versions = manifest.get("versions", [])
    current = manifest.get("current", None)
    recovery = recovery_summary(Path.cwd(), manifest)
    reviews = review_summary(Path.cwd())

    versions_summary = [
        {
            "version": v["version"],
            "label": v["label"],
            "status": v["status"],
            "path": v["path"],
            # Pre-1b entries don't have `source` — default to "script" since
            # before `agentcad import` shipped, every version was scripted.
            "source": v.get("source", "script"),
        }
        for v in versions
    ]

    response = {
        "command": "context",
        "status": "success",
        "project": manifest["name"],
        "tool_version": __version__,
        "current": current,
        "version_count": len(versions),
        "versions": versions_summary,
        "recovery": recovery,
        "open_review_comments": reviews["open_review_comments"],
        "addressed_review_comments": reviews["addressed_review_comments"],
    }
    if reviews["pending_review_batch"]:
        response["pending_review_batch"] = reviews["pending_review_batch"]
    if reviews["open_review_comments"]:
        response["review_next_action"] = "agentcad review list --status open"
    click.echo(json.dumps(response))
