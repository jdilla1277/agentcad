"""Record feedback locally; share only under an explicit human policy."""

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
from agentcad import __version__
from agentcad.feedback_policy import BRIEF_LIMIT, sharing_policy


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
                "hint": "Fill in the report, then record it locally. Add --send only when the human requests sharing. Nothing was sent.",
                "next_actions": [
                    _feedback_action("--file", str(destination.resolve()))
                ],
            }
        )
    else:
        response.update(
            {
                "template": FEEDBACK_TEMPLATE,
                "hint": "Save a template, fill it in, then record the file locally. Nothing was sent.",
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

    HTTPError messages include the status code (e.g. "HTTP Error 413: ...").
    Reviewed payloads are never automatically modified or retried.
    """
    try:
        # JSON escapes preserve POSIX filename surrogates in diagnostic context.
        data = json.dumps(bundle).encode("utf-8")
        req = Request(
            _resolve_feedback_url(),
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-agentcad-key": "agentcad-alpha-2026",
                "User-Agent": "AgentCAD",
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
@click.option("--file", "feedback_file", type=click.File("r", encoding="utf-8"),
              help="Record a UTF-8 report locally; '-' reads stdin. --send shares this report once.")
@click.option("--template", "show_template", is_flag=True, help="Export a guided template without sending.")
@click.option("--output", type=click.Path(path_type=Path), help="Create a new template file with --template.")
@click.option("--max-entries", default=10, type=click.IntRange(min=1),
              help="Recent commands in the LOCAL diagnostic bundle (default 10). Never attached to brief uploads.")
@click.option("--local-only", is_flag=True, help="Keep this observation local even when automatic sharing is enabled.")
@click.option("--send", is_flag=True, help="Human-requested one-off upload; does not enable future sharing.")
@click.option("--diagnostics", is_flag=True, help="Prepare and display a complete LOCAL diagnostic payload for review/redaction. Never sends.")
@click.option("--send-diagnostics", type=click.File("r", encoding="utf-8"),
              help="With --send, upload exactly this reviewed JSON object, including your redactions. No context is added.")
@project_options
def feedback(message, feedback_file, show_template, output, max_entries, local_only,
             send, diagnostics, send_diagnostics):
    """Record friction, workarounds, or positive observations without interrupting CAD.

    Default: save locally. A human can enable/revoke automatic brief sharing in
    the live viewer's Feedback sharing panel. This choice persists for this build
    root only, for the displayed recipient. Agents must not enable it on behalf
    of the human. Automatic uploads contain only a brief MESSAGE and AgentCAD
    version. Avoid private design details in observation text. Files/long reports
    are never automatically uploaded. No saved backlog is sent on enable.

    --send authorizes this one observation/report only, when requested by the
    human. It never attaches session history or runtime details. --local-only
    overrides automatic sharing. No input returns a guide and sends nothing.

    Diagnostics are separate: --diagnostics prepares a LOCAL bundle and displays
    the entire payload. Review all fields, remove/redact anything unwanted, then
    use --send-diagnostics FILE --send only with explicit human authorization of
    that complete payload. The exact JSON object is sent without additions or
    automatic retries that change its contents.

    Receipts distinguish local storage, confirmed upload and notification.
    status=partial means remote storage was not confirmed; the local copy remains.
    AGENTCAD_FEEDBACK_URL overrides the recipient; a changed recipient disables
    an existing automatic-sharing choice until the human approves it again.
    """
    if local_only and send:
        raise click.UsageError("--local-only and --send cannot be combined.")
    if diagnostics and send:
        raise click.UsageError("--diagnostics only prepares a local preview; review it before --send-diagnostics FILE --send.")
    if send_diagnostics is not None:
        if not send or any((message is not None, feedback_file is not None, show_template, output is not None, diagnostics, local_only)):
            raise click.UsageError("Use --send-diagnostics FILE --send alone, after human review and authorization of the complete payload.")
        try:
            reviewed = json.load(send_diagnostics)
        except (ValueError, OSError) as exc:
            raise click.BadParameter(f"Cannot read reviewed JSON: {exc}", param_hint="--send-diagnostics") from exc
        if not isinstance(reviewed, dict) or not reviewed:
            raise click.BadParameter("Reviewed payload must be a nonempty JSON object.", param_hint="--send-diagnostics")
        click.echo(json.dumps(deliver_payload(get_project().build_root, reviewed, _send_remote)))
        return
    if (show_template or (message is None and feedback_file is None)) and (send or diagnostics or local_only):
        raise click.UsageError("Provide a MESSAGE or --file for recording, preview, or one-off sharing.")
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
                "Fill in the report, then record locally with "
                + _feedback_action("--file", "feedback.md")
                + ".\nTo save it directly as UTF-8: "
                + _feedback_action("--template", "--output", "feedback.md")
                + "\nNothing was sent. Reports stay local unless the human requests --send.",
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

    # Keep diagnostics locally for backwards-compatible evidence, but construct
    # the network payload independently using an explicit allowlist.
    bundle = logger.get_session_bundle(
        _report_summary(message) if is_report else message, max_entries=max_entries,
    )
    if is_report:
        bundle.update(report=message, report_format="markdown")
    bundle_file.write_text(json.dumps(bundle, indent=2) + "\n", encoding="utf-8")
    policy = sharing_policy(project_dir, _resolve_feedback_url())
    payload = {"summary": message, "agentcad_version": __version__}
    automatic = policy["enabled"] and not is_report and len(message) <= BRIEF_LIMIT
    should_send = not local_only and not diagnostics and (send or automatic)
    response = {"command": "feedback", "status": "success",
                "bundle_file": str(bundle_file), "remote": "skipped", "submitted": False,
                "discord": None, "neon_row_id": None, "sharing": policy,
                "mode": "diagnostic_preview" if diagnostics else "observation"}
    if should_send:
        response.update(deliver_payload(project_dir, payload, _send_remote))
        # Keep the diagnostic evidence reference distinct from the exact upload.
        response["bundle_file"] = str(bundle_file)
    if diagnostics:
        response.update(payload=bundle, recipient=_resolve_feedback_url(),
                        next_actions=[_feedback_action("--send-diagnostics", str(bundle_file), "--send")],
                        hint="Nothing sent. Review the COMPLETE payload, remove/redact fields in the JSON file, and obtain human authorization before sending.")
    if is_report:
        response.update(report_format="markdown", report_chars=len(message))
    else:
        response["bundle"] = bundle
    click.echo(json.dumps(response))


def deliver_payload(project_dir, payload, sender):
    """Save the exact upload before delivery. Never append diagnostics or retry edits."""
    directory = project_dir / ".agentcad/feedback"
    directory.mkdir(parents=True, exist_ok=True)
    upload_file = directory / f"upload-{uuid4().hex}.json"
    upload_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    result = sender(payload)
    error = result.get("err")
    return {"command": "feedback", "status": "partial" if error else "success",
            "submitted": error is None, "remote": f"failed: {error}" if error else "sent",
            "upload_file": str(upload_file), "bundle_file": str(upload_file),
            "recipient": _resolve_feedback_url(),
            "discord": result.get("discord"), "neon_row_id": result.get("neon_row_id")}
