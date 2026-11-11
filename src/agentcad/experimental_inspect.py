"""Compatibility with pre-graduation Inspect flags and saved retry identities.

Milestone 2 inspection is available by default. Old acknowledgment inputs are
accepted so retained recovery commands remain executable, but do not control
availability. Existing ordinary retries keep their original no-enrichment mode.
"""

from functools import wraps
import hashlib
import re

import click

from agentcad.project import ProjectError

FLAG = "--experimental-inspect"
MARKER = "experimental_inspect"


def enabled():
    """Enable graduated inspection except while replaying an old ordinary key."""
    ctx = click.get_current_context(silent=True)
    return not bool(ctx and ctx.meta.get("inspection_legacy_retry"))


def option(function):
    @click.option(FLAG, is_flag=True, default=False, hidden=True,
                  help="Deprecated compatibility option; Milestone 2 Inspect is available by default.")
    @wraps(function)
    def wrapped(*args, experimental_inspect=False, **kwargs):
        return function(*args, **kwargs)
    return wrapped


def forward(args):
    """New continuations never need the deprecated acknowledgment."""
    return args


def ordinary_semantic():
    # New requests use the original enriched identity. A retained ordinary key
    # must preserve its original fingerprint instead of creating another build.
    return {} if enabled() else {MARKER: False}


def guard_retry(command, key):
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", key):
        return None
    from agentcad.import_requests import _read
    from agentcad.project import get_project

    request_id = command + "_" + hashlib.sha256(key.encode()).hexdigest()
    path = get_project().build_root / f".agentcad/{command}_requests/{request_id}/request.json"
    if not path.exists():
        return None
    try:
        semantic = _read(path, request_id)["semantic"]
        if not isinstance(semantic, dict):
            raise ValueError("Saved semantic identity is not an object")
        if command == "import" and semantic.get(MARKER) is False:
            if any(type(semantic.get(name)) is not bool for name in ("show_report", "inspection_image")):
                raise ValueError("Saved import inspection settings are missing or invalid")
    except (OSError, ValueError, ProjectError, KeyError, TypeError, AttributeError) as exc:
        raise ProjectError(f"{command}_request_unavailable",
                           f"Cannot verify the saved retry's Inspect mode: {exc}",
                           "Restore the original retry record; no source was read or executed.") from exc
    if semantic.get(MARKER) is False:
        click.get_current_context().meta["inspection_legacy_retry"] = True
    return semantic


def validate(ctx, values):
    """Preserve existing retry semantics before source reads or execution."""
    if ctx.command.name not in {"run", "import"}:
        return
    command = ctx.command.name
    if command == "run":
        from agentcad.file_detect import is_recognized_cad_extension
        if is_recognized_cad_extension(values.get("script") or ""):
            command = "import"
    semantic = guard_retry(command, values.get("idempotency_key"))
    if ctx.command.name == "import" and semantic and semantic.get(MARKER) is False:
        # The previous defaults were false. Explicit new options still conflict
        # normally; only omitted defaults retain the saved request's settings.
        for name in ("show_report", "inspection_image"):
            if ctx.get_parameter_source(name) != click.core.ParameterSource.COMMANDLINE:
                values[name] = semantic[name]


def present(payload, *, acknowledged=False):
    """Compatibility helper: preserve saved evidence; add no WIP decoration."""
    return payload
