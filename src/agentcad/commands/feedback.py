"""agentcad feedback — submit agent feedback; list/show/resolve the queue."""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import click

from agentcad.session_log import SessionLogger


_DEFAULT_FEEDBACK_URL = "https://agentcad.dev/api/feedback"

_READ_KEY_ENV = "AGENTCAD_FEEDBACK_READ_KEY"


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


def _call_read_api(method: str, key: str, params: dict | None = None, body: dict | None = None):
    """GET/PATCH the feedback endpoint with the maintainer read key.

    Returns (err, data): err is None on success and data is the parsed JSON
    response; on failure err is a message string and data is None.
    """
    url = _resolve_feedback_url()
    if params:
        url += "?" + urlencode(params)
    headers = {"x-agentcad-read-key": key}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    try:
        req = Request(url, data=data, headers=headers, method=method)
        with urlopen(req, timeout=10) as resp:
            return None, json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            detail = json.loads(e.read().decode("utf-8")).get("error")
        except Exception:
            detail = None
        return f"HTTP Error {e.code}: {detail or e.reason}", None
    except URLError as e:
        return str(e), None
    except Exception as e:
        return str(e), None


def _read_key_or_fail(command: str) -> str:
    key = os.environ.get(_READ_KEY_ENV)
    if not key:
        click.echo(json.dumps({
            "command": command,
            "status": "error",
            "error": f"{_READ_KEY_ENV} is not set.",
            "suggestion": (
                "Reading feedback requires the maintainer read key — export "
                f"{_READ_KEY_ENV} in your shell (see 'Feedback Read Access' in "
                "the internal repo's CLAUDE.md). Submitting feedback needs no key."
            ),
        }))
        sys.exit(1)
    return key


def _read_api_error(command: str, err: str):
    if "401" in err:
        suggestion = (
            f"The read key was rejected — check {_READ_KEY_ENV} matches the key "
            "configured on the server (Vercel project env)."
        )
    elif "503" in err:
        suggestion = (
            f"The server has no read key configured — set {_READ_KEY_ENV} in the "
            "Vercel project env and redeploy."
        )
    else:
        suggestion = "Check network connectivity and AGENTCAD_FEEDBACK_URL, then retry."
    click.echo(json.dumps({
        "command": command,
        "status": "error",
        "error": err,
        "suggestion": suggestion,
    }))
    sys.exit(1)


class _FeedbackGroup(click.Group):
    """Group that falls back to `submit` so `agentcad feedback "msg"` keeps working."""

    def resolve_command(self, ctx, args):
        # Snapshot first: click's resolve_command consumes from `args` in place
        # while re-parsing option-like tokens, before raising UsageError.
        original_args = list(args)
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            cmd = self.get_command(ctx, "submit")
            return "submit", cmd, original_args


@click.group(
    cls=_FeedbackGroup,
    no_args_is_help=False,
    context_settings=dict(ignore_unknown_options=True),
)
def feedback():
    """Submit feedback, or read the queue (maintainers).

    `agentcad feedback "your message"` submits — it is shorthand for
    `agentcad feedback submit "your message"`. Use the explicit form if your
    message could be mistaken for a subcommand name.

    list / show / resolve read the feedback queue and require the maintainer
    read key in AGENTCAD_FEEDBACK_READ_KEY; submitting needs no key.
    """


@feedback.command()
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
def submit(message, max_entries, local_only):
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


@feedback.command("list")
@click.option(
    "--status",
    "status_filter",
    type=click.Choice(["new", "triaged", "resolved", "all"]),
    default="new",
    help="Filter by queue status (default: new).",
)
@click.option(
    "--limit",
    default=20,
    type=int,
    help="Max rows to return (default: 20, server caps at 100).",
)
def feedback_list(status_filter, limit):
    """List feedback queue rows, newest first (requires the read key)."""
    key = _read_key_or_fail("feedback list")
    err, data = _call_read_api("GET", key, params={"status": status_filter, "limit": limit})
    if err:
        _read_api_error("feedback list", err)

    click.echo(json.dumps({
        "command": "feedback list",
        "status": "success",
        "filter": status_filter,
        "items": data.get("items", []),
        "count": data.get("count", 0),
        "next_actions": [
            "agentcad feedback show <id> — read a row's full bundle (session log, environment)",
            "agentcad feedback resolve <id> — mark it handled so the queue drains",
        ],
        "more_at": "agentcad feedback --help",
    }))


@feedback.command()
@click.argument("feedback_id", type=int)
def show(feedback_id):
    """Show one feedback row with its full payload (requires the read key)."""
    key = _read_key_or_fail("feedback show")
    err, data = _call_read_api("GET", key, params={"id": feedback_id})
    if err:
        _read_api_error("feedback show", err)

    click.echo(json.dumps({
        "command": "feedback show",
        "status": "success",
        "item": data.get("item"),
        "next_actions": [
            "agentcad feedback resolve <id> — mark it handled so the queue drains",
        ],
        "more_at": "agentcad feedback --help",
    }))


@feedback.command()
@click.argument("feedback_id", type=int)
@click.option(
    "--status",
    "new_status",
    type=click.Choice(["resolved", "triaged", "new"]),
    default="resolved",
    help="Status to set (default: resolved).",
)
def resolve(feedback_id, new_status):
    """Mark a feedback row resolved (or triaged) so the queue drains (requires the read key)."""
    key = _read_key_or_fail("feedback resolve")
    err, data = _call_read_api("PATCH", key, body={"id": feedback_id, "status": new_status})
    if err:
        _read_api_error("feedback resolve", err)

    click.echo(json.dumps({
        "command": "feedback resolve",
        "status": "success",
        "id": data.get("id"),
        "feedback_status": data.get("feedback_status"),
    }))
