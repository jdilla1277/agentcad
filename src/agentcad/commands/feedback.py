"""agentcad feedback — submit agent feedback with session context."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import click

from agentcad.session_log import SessionLogger


_DEFAULT_FEEDBACK_URL = "https://agentcad.dev/api/feedback"


def _resolve_feedback_url() -> str:
    return os.environ.get("AGENTCAD_FEEDBACK_URL") or _DEFAULT_FEEDBACK_URL


def _send_remote(bundle: dict) -> dict:
    """POST bundle to the feedback endpoint.

    Returns a dict with:
      - err: None on 2xx, error message string otherwise.
      - discord: the API-reported Discord webhook status when available
        ("ok", "failed: ...", "skipped: ..."), else None.
      - neon_row_id: the API-reported Neon row id when available, else None.

    HTTPError messages include the status code (e.g. "HTTP Error 413: ...") so callers
    can substring-match on "413" to detect payload-too-large and retry with fewer entries.
    """
    try:
        data = json.dumps(bundle).encode("utf-8")
        req = Request(
            _resolve_feedback_url(),
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-agentcad-key": "agentcad-alpha-2026",
            },
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                try:
                    body = json.loads(resp.read().decode("utf-8"))
                except Exception:
                    body = {}
                return {
                    "err": None,
                    "discord": body.get("discord"),
                    "neon_row_id": body.get("neon_row_id"),
                }
            return {"err": f"Server returned {resp.status}", "discord": None, "neon_row_id": None}
    except HTTPError as e:
        return {"err": f"HTTP Error {e.code}: {e.reason}", "discord": None, "neon_row_id": None}
    except URLError as e:
        return {"err": str(e), "discord": None, "neon_row_id": None}
    except Exception as e:
        return {"err": str(e), "discord": None, "neon_row_id": None}


@click.command()
@click.argument("message")
@click.option(
    "--max-entries",
    default=10,
    type=int,
    help="Max session log entries to include in the bundle (default: 10). "
         "Auto-halves on remote 413 responses.",
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

    If the remote rejects the payload as too large (HTTP 413), the command
    automatically retries with halved --max-entries until it fits or reaches 1.

    \b
    Response fields agents should check:
      - "status": "success" iff Neon stored the bundle; "partial" if the
        remote upload failed (the local file is still written).
      - "discord": the human alert webhook status — "ok" / "failed: ..."
        / "skipped: ...". Note: "status" can be "success" while "discord"
        is "failed: ..." — that means Neon has the row but no one was paged.
      - "neon_row_id": id of the stored row, or null if the upload failed.

    \b
    Set AGENTCAD_FEEDBACK_URL to point at a non-default endpoint (preview
    deploys, self-hosted instances). Defaults to the production endpoint.
    """
    project_dir = Path.cwd()
    logger = SessionLogger(project_dir)

    feedback_dir = project_dir / ".agentcad" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_file = feedback_dir / f"{ts}.json"

    current_entries = max_entries
    bundle = logger.get_session_bundle(message, max_entries=current_entries)

    remote_status = "skipped"
    status = "success"
    discord_status: str | None = None
    neon_row_id: int | None = None
    retried = False

    if not local_only:
        result = _send_remote(bundle)
        err = result.get("err")
        while err and "413" in err and current_entries > 1:
            retried = True
            current_entries = max(1, current_entries // 2)
            bundle = logger.get_session_bundle(message, max_entries=current_entries)
            result = _send_remote(bundle)
            err = result.get("err")

        if err is None:
            remote_status = (
                f"sent (retried with --max-entries {current_entries})"
                if retried
                else "sent"
            )
            discord_status = result.get("discord")
            neon_row_id = result.get("neon_row_id")
        else:
            remote_status = f"failed: {err}"
            status = "partial"

    bundle_file.write_text(json.dumps(bundle, indent=2) + "\n")

    click.echo(json.dumps({
        "command": "feedback",
        "status": status,
        "bundle_file": str(bundle_file),
        "remote": remote_status,
        "discord": discord_status,
        "neon_row_id": neon_row_id,
        "bundle": bundle,
    }))
