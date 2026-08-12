import json
import sys
from pathlib import Path

import click

MANIFEST_FILE = "agentcad.json"


def load_manifest(command=None):
    """Load and return the manifest dict, or exit with error JSON if missing."""
    manifest_path = Path.cwd() / MANIFEST_FILE
    if not manifest_path.exists():
        error = {}
        if command:
            error["command"] = command
        error["status"] = "error"
        error["message"] = f"{MANIFEST_FILE} not found. Run 'agentcad init' first."
        click.echo(json.dumps(error))
        sys.exit(1)
    return json.loads(manifest_path.read_text())


def save_manifest(manifest):
    """Atomically write the manifest to agentcad.json."""
    from agentcad.versioning import atomic_write_json

    manifest_path = Path.cwd() / MANIFEST_FILE
    atomic_write_json(manifest_path, manifest)
