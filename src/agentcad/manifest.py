import json
import sys

import click
from agentcad.project import get_project

MANIFEST_FILE = "agentcad.json"


def load_manifest(command=None):
    """Load and return the manifest dict, or exit with error JSON if missing."""
    layout = get_project()
    manifest_path = layout.manifest_path
    if not manifest_path.exists():
        if layout.configured:
            raise layout.missing_manifest()
        error = {}
        if command:
            error["command"] = command
        error["status"] = "error"
        error["message"] = f"{MANIFEST_FILE} not found. Run 'agentcad init' first."
        click.echo(json.dumps(error))
        sys.exit(1)
    return layout.read_manifest()


def save_manifest(manifest):
    """Atomically write the manifest to agentcad.json."""
    from agentcad.versioning import atomic_write_json

    manifest_path = get_project().manifest_path
    atomic_write_json(manifest_path, manifest)
