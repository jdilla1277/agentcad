"""agentcad feedback — submit agent feedback with session context."""

import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

import click

from agentcad.session_log import SessionLogger


_FEEDBACK_URL = "https://agentcad-site-igs34x6x6-jdilla1277s-projects.vercel.app/api/feedback"


def _send_remote(bundle: dict) -> str | None:
    """POST bundle to the feedback endpoint. Returns None on success, error message on failure."""
    try:
        data = json.dumps(bundle).encode("utf-8")
        req = Request(
            _FEEDBACK_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-agentcad-key": "agentcad-alpha-2026",
            },
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return None
            return f"Server returned {resp.status}"
    except URLError as e:
        return str(e)
    except Exception as e:
        return str(e)


@click.command()
@click.argument("message")
@click.option(
    "--max-entries",
    default=50,
    type=int,
    help="Max session log entries to include in the bundle (default: 50).",
)
@click.option(
    "--local-only",
    is_flag=True,
    default=False,
    help="Save locally only, don't send to remote.",
)
def feedback(message, max_entries, local_only):
    """Submit feedback with session log context.

    Bundles your MESSAGE with the session log, friction signals, and
    environment info. The bundle is saved locally and sent to the agentcad
    team automatically. Use --local-only to skip remote submission.
    """
    project_dir = Path.cwd()
    logger = SessionLogger(project_dir)
    bundle = logger.get_session_bundle(message, max_entries=max_entries)

    # Write bundle to file
    feedback_dir = project_dir / ".agentcad" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_file = feedback_dir / f"{ts}.json"
    bundle_file.write_text(json.dumps(bundle, indent=2) + "\n")

    # Send remote
    remote_status = "skipped"
    if not local_only:
        err = _send_remote(bundle)
        remote_status = "sent" if err is None else f"failed: {err}"

    click.echo(json.dumps({
        "command": "feedback",
        "status": "success",
        "bundle_file": str(bundle_file),
        "remote": remote_status,
        "bundle": bundle,
    }))
