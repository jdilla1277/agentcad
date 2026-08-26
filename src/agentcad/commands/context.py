import json
from pathlib import Path

import click

from agentcad import __version__
from agentcad.manifest import load_manifest
from agentcad.recovery import recovery_summary


@click.command()
def context():
    """Show the current project context."""
    manifest = load_manifest(command="context")

    versions = manifest.get("versions", [])
    current = manifest.get("current", None)
    recovery = recovery_summary(Path.cwd(), manifest)

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

    click.echo(json.dumps({
        "command": "context",
        "status": "success",
        "project": manifest["name"],
        "tool_version": __version__,
        "current": current,
        "version_count": len(versions),
        "versions": versions_summary,
        "recovery": recovery,
    }))
