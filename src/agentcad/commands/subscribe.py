"""agentcad subscribe — sign up an email for agentcad updates.

Designed to be agent-fillable: an agent can run

    agentcad subscribe alice@example.com

on behalf of a user. The endpoint sends a double opt-in confirmation email,
so the address isn't added to the list until the user clicks the link.
"""

import json
import re
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

import click

from agentcad.session_log import _environment_info


_SUBSCRIBE_URL = "https://agentcad-site-five.vercel.app/api/subscribe"
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _send_remote(payload: dict) -> tuple[str | None, dict | None]:
    """POST payload to the subscribe endpoint.

    Returns (error, response_body). On success, error is None.
    """
    try:
        data = json.dumps(payload).encode("utf-8")
        req = Request(
            _SUBSCRIBE_URL,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=10) as resp:
            body_bytes = resp.read()
            try:
                body = json.loads(body_bytes.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body = {}
            if resp.status >= 400:
                return f"Server returned {resp.status}", body
            return None, body
    except URLError as e:
        return str(e), None
    except Exception as e:
        return str(e), None


@click.command()
@click.argument("email")
def subscribe(email):
    """Sign EMAIL up for agentcad updates (double opt-in).

    Submits the address to the agentcad signup endpoint. The user will
    receive a confirmation email; their address is added to the list
    only after they click the link.
    """
    normalised = email.strip().lower()
    if not _EMAIL_RE.match(normalised) or len(normalised) > 254:
        click.echo(
            json.dumps(
                {
                    "command": "subscribe",
                    "status": "error",
                    "error": f"Invalid email address: {email!r}",
                }
            )
        )
        sys.exit(1)

    payload = {
        "email": normalised,
        "source": "cli",
        "agent_context": _environment_info(),
    }

    err, body = _send_remote(payload)
    if err is not None:
        click.echo(
            json.dumps(
                {
                    "command": "subscribe",
                    "status": "error",
                    "error": err,
                    "remote": body,
                }
            )
        )
        sys.exit(1)

    click.echo(
        json.dumps(
            {
                "command": "subscribe",
                "status": "success",
                "email": normalised,
                "remote": body or {},
            }
        )
    )
