"""Human account connection, independent of local projects and CAD runtimes."""
import json
import webbrowser

import click

from agentcad.account import AccountClient, AccountError


def _call(operation, **kwargs):
    try:
        with AccountClient() as client:
            result = getattr(client, operation)(**kwargs)
    except (OSError, AccountError) as error:
        result = {"command": "account", "status": "error", "reason": getattr(error, "reason", "state_unavailable"),
                  "message": getattr(error, "message", "Account state is unavailable. Check your private state file and retry."),
                  "suggestion": ("Run agentcad account disconnect --forget, then agentcad account connect."
                    if getattr(error, "reason", "") == "not_connected" else
                    "Preserve the state file for recovery or choose a new AGENTCAD_ACCOUNT_STATE path."
                    if getattr(error, "reason", "") == "invalid_state" else
                    "Retry the same command when the service is available; keep the saved account state."),
                  "more_at": "agentcad docs account"}
    return result


def _emit(result):
    click.echo(json.dumps(result))
    if result["status"] == "error":
        raise SystemExit(1)


@click.group()
def account():
    """Create or connect an account through your browser; local work needs no login."""


@account.command()
@click.option("--name", help="Name for this installation, shown during browser approval.")
@click.option("--no-browser", is_flag=True, help="Return the approval link without opening a browser.")
def connect(name, no_browser):
    """Start signup/sign-in and explicitly approve this CLI installation in the browser."""
    result = _call("connect", name=name)
    if result.get("approval_url"):
        opened = False
        if not no_browser:
            try:
                opened = bool(webbrowser.open(result["approval_url"]))
            except (OSError, webbrowser.Error):
                pass  # The printed link remains usable if the browser cannot open.
        result["browser_opened"] = opened
    _emit(result)


@account.command()
def status():
    """Finish an approved connection or check the saved installation online."""
    _emit(_call("status"))


@account.command()
@click.option("--forget", is_flag=True, help="Clear local state without server revocation; revoke any approved installation on the website.")
def disconnect(forget):
    """Revoke this installation and clear its local credential."""
    _emit(_call("disconnect", forget=forget))
