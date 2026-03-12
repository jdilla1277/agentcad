import json

import click

from cadtool.manifest import load_manifest

TOOL_VERSION = "0.1.0"


@click.command()
def context():
    """Show the current project context."""
    manifest = load_manifest(command="context")

    versions = manifest.get("versions", [])
    current = manifest.get("current", None)

    versions_summary = [
        {
            "version": v["version"],
            "label": v["label"],
            "status": v["status"],
            "path": v["path"],
        }
        for v in versions
    ]

    click.echo(json.dumps({
        "command": "context",
        "status": "success",
        "project": manifest["name"],
        "tool_version": TOOL_VERSION,
        "current": current,
        "version_count": len(versions),
        "versions": versions_summary,
    }))
