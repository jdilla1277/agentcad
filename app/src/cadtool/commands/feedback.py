"""cadtool feedback — submit agent feedback with session context."""

import json
from datetime import datetime, timezone
from pathlib import Path

import click

from cadtool.session_log import SessionLogger


@click.command()
@click.argument("message")
@click.option(
    "--max-entries",
    default=50,
    type=int,
    help="Max session log entries to include in the bundle (default: 50).",
)
def feedback(message, max_entries):
    """Submit feedback with session log context.

    Bundles your MESSAGE with the session log, friction signals, and
    environment info.  The bundle is written to .cadtool/feedback/ and
    printed as JSON so an agent can attach it to a GitHub issue.
    """
    project_dir = Path.cwd()
    logger = SessionLogger(project_dir)
    bundle = logger.get_session_bundle(message, max_entries=max_entries)

    # Write bundle to file
    feedback_dir = project_dir / ".cadtool" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_file = feedback_dir / f"{ts}.json"
    bundle_file.write_text(json.dumps(bundle, indent=2) + "\n")

    click.echo(json.dumps({
        "command": "feedback",
        "status": "success",
        "bundle_file": str(bundle_file),
        "bundle": bundle,
    }))
