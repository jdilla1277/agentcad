"""Invocation-scoped opt-in for exact geometry search, independent of Inspect."""

from functools import wraps

import click

from agentcad.project import ProjectError

FLAG = "--experimental-geometry-search"
MARKER = "experimental_geometry_search"


def enabled():
    ctx = click.get_current_context(silent=True)
    return bool(ctx and ctx.meta.get(MARKER))


def option(function):
    @click.option(FLAG, is_flag=True, default=False,
                  help="WIP: opt in to exact geometry filters and retained search pages for this call.")
    @wraps(function)
    def wrapped(*args, experimental_geometry_search=False, **kwargs):
        click.get_current_context().meta[MARKER] = experimental_geometry_search
        return function(*args, **kwargs)
    return wrapped


def requested(values):
    return (values.get("query") is not None or values.get("search_snapshot") is not None
            or values.get("cursor") is not None or values.get("search_id") is not None
            or values.get("page_size", 10) != 10 or values.get("search_images", 10) != 10
            or str(values.get("artifact") or "").startswith("search_"))


def validate(ctx, values):
    if ctx.command.name == "inspect" and requested(values) and not enabled():
        raise ProjectError("experimental_geometry_search_required",
                           "Geometry search is experimental and requires per-call opt-in.",
                           f"Repeat with {FLAG} (MCP: experimental_geometry_search=true).")
