"""agentcad feedback — submit agent feedback with session context."""

import json
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import click
from agentcad.project import get_project, project_options

from agentcad.session_log import SessionLogger


_DEFAULT_FEEDBACK_URL = "https://agentcad.dev/api/feedback"

FEEDBACK_TEMPLATE = """## Summary
<!-- One sentence describing the main observation. -->

## What I was trying to do
<!-- Describe the model/edit, relevant requirements, and the goal. -->

## Feedback
<!-- What happened? Where did you get stuck or need a workaround? -->

## Expected behavior (optional)
<!-- What would have helped you complete the task? -->

## Reproduction and evidence (optional)
<!-- Share relevant commands, errors, measurements, or artifact references.
Review the report and redact anything you do not want to send. -->

## Impact or workaround (optional)
<!-- How much did this interrupt the task? How did you continue? -->

## What worked well (optional)
<!-- Which behavior should AgentCAD preserve? -->

## Ideas (optional)
<!-- Suggest improvements, including ideas beyond the immediate problem. -->
"""


def _feedback_action(*args):
    command = ["agentcad", "feedback", *args]
    layout = get_project()
    if layout.configured:
        command.extend(["--build-dir", str(layout.build_root)])
    return shlex.join(command)


def _template_response(output):
    """Export Markdown explicitly; keep normal discovery on the JSON contract."""
    response = {
        "command": "feedback",
        "status": "success",
        "mode": "template",
        "submitted": False,
        "more_at": "agentcad docs feedback",
    }
    if output is not None:
        destination = Path(output)
        try:
            with destination.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(FEEDBACK_TEMPLATE)
        except OSError as exc:
            raise click.BadParameter(
                f"Cannot create template {destination}: {exc}. Choose a new file; existing files are not overwritten.",
                param_hint="--output",
            ) from exc
        response.update(
            {
                "template_file": str(destination.resolve()),
                "hint": "Fill in the report, then submit it. Optional sections and partial observations are welcome. Nothing was sent.",
                "next_actions": [
                    _feedback_action("--file", str(destination.resolve()))
                ],
            }
        )
    else:
        response.update(
            {
                "template": FEEDBACK_TEMPLATE,
                "hint": "Save a template, fill it in, then submit the file. Nothing was sent.",
                "next_actions": [
                    _feedback_action("--template", "--output", "feedback.md")
                ],
            }
        )
    click.echo(json.dumps(response))


def _report_summary(report):
    """Give list/notification readers a short heading without altering the report."""
    text = re.sub(r"<!--.*?-->", "", report, flags=re.DOTALL)
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:240]
    return "Feedback report"


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
        data = json.dumps(bundle, ensure_ascii=False).encode("utf-8")
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
            return {
                "err": f"Server returned {resp.status}",
                "discord": None,
                "neon_row_id": None,
            }
    except HTTPError as e:
        return {
            "err": f"HTTP Error {e.code}: {e.reason}",
            "discord": None,
            "neon_row_id": None,
        }
    except URLError as e:
        return {"err": str(e), "discord": None, "neon_row_id": None}
    except Exception as e:
        return {"err": str(e), "discord": None, "neon_row_id": None}


@click.command()
@click.argument("message", required=False)
@click.option(
    "--file",
    "feedback_file",
    type=click.File("r", encoding="utf-8"),
    help="Submit a UTF-8 report file; use '-' for stdin. The full report is saved with diagnostic context.",
)
@click.option(
    "--template",
    "show_template",
    is_flag=True,
    help="Export a guided Markdown template without sending. Add --output FILE to save as UTF-8, or redirect stdout.",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Create a new UTF-8 template file with --template. Existing files are never overwritten.",
)
@click.option(
    "--max-entries",
    default=10,
    type=click.IntRange(min=1),
    help="Max session log entries to include in the bundle (default: 10). "
    "Auto-halves on remote 413 responses.",
)
@click.option(
    "--local-only",
    is_flag=True,
    default=False,
    help="Save locally only, don't send to remote.",
)
@project_options
def feedback(message, feedback_file, show_template, output, max_entries, local_only):
    """Report friction, ideas, or what worked well, with session context.

    With no input, return guided questions and next steps as JSON; nothing is
    sent. Use --template --output feedback.md, fill in the report, then submit
    with --file feedback.md. --file - reads stdin. Partial reports are welcome;
    no headings are mandatory. A short MESSAGE still works for quick notes.

    Explicit --template without --output exports only Markdown on stdout and
    save/submit instructions on stderr. This is a text export, not a receipt.

    Submissions include recent session entries, friction signals and environment
    info. Review report text before sending. The full bundle is saved locally
    before uploading to the agentcad team. Use --local-only to skip remote
    submission.

    File submissions return a compact receipt; bundle_file contains the full
    report and context. Legacy MESSAGE replies retain their bundle field.

    If the remote rejects the payload as too large (HTTP 413), the command
    automatically retries with halved --max-entries until it fits or reaches 1.

    \b
    Response fields agents should check:
      - "status": "success" when the bundle was stored (remotely, or just
        locally with --local-only); "partial" if the remote upload failed
        (the local file is still written).
      - "discord": the human alert webhook status — "ok" / "failed: ..."
        / "skipped: ...". Note: "status" can be "success" while "discord"
        is "failed: ..." — that means Neon has the row but no one was paged.
      - "neon_row_id": id of the stored row, or null if the upload failed.

    \b
    Set AGENTCAD_FEEDBACK_URL to point at a non-default endpoint (preview
    deploys, self-hosted instances). Defaults to the production endpoint.
    """
    if output is not None and not show_template:
        raise click.UsageError("--output requires --template.")
    if sum((message is not None, feedback_file is not None, show_template)) > 1:
        raise click.UsageError(
            "Choose a MESSAGE, --file, or --template; do not combine them."
        )
    if show_template:
        if output is not None:
            _template_response(output)
        else:
            click.echo(FEEDBACK_TEMPLATE, nl=False)
            click.echo(
                "Fill in the report, then submit with "
                + _feedback_action("--file", "feedback.md")
                + ".\nTo save it directly as UTF-8: "
                + _feedback_action("--template", "--output", "feedback.md")
                + "\nNothing was sent. Use --local-only on a submission to keep it local.",
                err=True,
            )
        return
    if message is None and feedback_file is None:
        _template_response(None)
        return
    is_report = feedback_file is not None
    if is_report:
        try:
            message = feedback_file.read()
        except (UnicodeError, OSError) as exc:
            raise click.BadParameter(
                f"Cannot read report as UTF-8: {exc}. Convert the file to UTF-8 before submitting.",
                param_hint="--file",
            ) from exc
    if not message.strip():
        raise click.UsageError(
            "Feedback cannot be empty. Write an observation or fill in a report with --template."
        )

    project_dir = get_project().build_root
    logger = SessionLogger(project_dir)

    feedback_dir = project_dir / ".agentcad" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_file = feedback_dir / f"{ts}-{uuid4().hex}.json"

    current_entries = max_entries

    def save_bundle():
        bundle = logger.get_session_bundle(
            _report_summary(message) if is_report else message,
            max_entries=current_entries,
        )
        if is_report:
            bundle.update(report=message, report_format="markdown")
        bundle_file.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return bundle

    # Preserve a complete local report even if upload is interrupted or fails.
    bundle = save_bundle()

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
            bundle = save_bundle()
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

    response = {
        "command": "feedback",
        "status": status,
        "bundle_file": str(bundle_file),
        "remote": remote_status,
        "discord": discord_status,
        "neon_row_id": neon_row_id,
    }
    if is_report:
        response.update(report_format="markdown", report_chars=len(message))
    else:
        response["bundle"] = bundle
    click.echo(json.dumps(response))
