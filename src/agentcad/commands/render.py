import json
import re
import sys
from pathlib import Path

import click

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)

MAX_RENDER_DIMENSION = 8192
MAX_RENDER_PIXELS = 32_000_000
MAX_ANTIALIASED_PIXELS = 9_000_000


def _is_version_dir(directory):
    """Check if directory matches v\\d+_\\w+ pattern and contains meta.json."""
    return bool(re.match(r"v\d+_\w+", directory.name)) and (directory / "meta.json").exists()


def _format_custom_angle_name(azimuth, elevation):
    """Format a custom angle into a filename-safe string."""
    def _fmt(v):
        return str(int(v)) if v == int(v) else str(v)
    return f"custom_{_fmt(azimuth)}_{_fmt(elevation)}"


def _parse_focus(focus_str):
    """Parse a focus string 'x,y,z' into a tuple of 3 floats."""
    parts = focus_str.split(",")
    if len(parts) != 3:
        raise ValueError(f"Invalid --focus '{focus_str}'. Expected 'x,y,z' (3 numeric values).")
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError:
        raise ValueError(f"Invalid --focus '{focus_str}'. Expected 'x,y,z' (3 numeric values).")


def _parse_size(_ctx, _param, value):
    """Parse a WIDTHxHEIGHT string into a pair of positive pixel dimensions."""
    match = re.fullmatch(r"(\d+)[xX](\d+)", value)
    if not match:
        raise click.BadParameter("expected WIDTHxHEIGHT, for example 2400x1800")
    components = match.groups()
    if any(len(component) > 5 for component in components):
        raise click.BadParameter(
            f"width and height must not exceed {MAX_RENDER_DIMENSION} pixels"
        )
    width, height = (int(component) for component in components)
    if width < 1 or height < 1:
        raise click.BadParameter("width and height must be positive")
    if width > MAX_RENDER_DIMENSION or height > MAX_RENDER_DIMENSION:
        raise click.BadParameter(
            f"width and height must not exceed {MAX_RENDER_DIMENSION} pixels"
        )
    if width * height > MAX_RENDER_PIXELS:
        raise click.BadParameter("output size must not exceed 32 million pixels")
    return width, height


@click.command()
@click.argument("step_file")
@click.option(
    "--view",
    required=True,
    help=(
        "Named view(s), 'all', custom azimuth:elevation, or a mix such as "
        "front,45:30. A legacy single 'azimuth,elevation' pair is also accepted."
    ),
)
@click.option("--zoom", default=1.0, type=float, help="Zoom factor (applied after FitAll).")
@click.option("--size", default="800x600", callback=_parse_size, metavar="WIDTHxHEIGHT",
              help="Output resolution (default: 800x600).")
@click.option("--msaa", default=0, type=click.IntRange(min=0, max=16),
              help="Antialiasing samples; 0 disables antialiasing (default: 0).")
@click.option("--name", default=None, help="Custom filename for the rendered PNG (single view only).")
@click.option("--focus", default=None, help="Camera target point 'x,y,z'.")
@click.option("--no-fit", is_flag=True, default=False, help="Skip FitAll (requires --focus).")
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
def render(step_file, view, zoom, size, msaa, name, focus, no_fit, no_daemon):
    """Render PNG views of an existing STEP file."""
    if msaa and size[0] * size[1] > MAX_ANTIALIASED_PIXELS:
        raise click.BadParameter(
            "antialiased output size must not exceed 9 million pixels",
            param_hint="--size",
        )

    # Try routing through daemon. Exits before returning if reachable.
    argv = ["render", step_file, "--view", view]
    if zoom != 1.0:
        argv.extend(["--zoom", str(zoom)])
    if size != (800, 600):
        argv.extend(["--size", f"{size[0]}x{size[1]}"])
    if msaa != 0:
        argv.extend(["--msaa", str(msaa)])
    if name:
        argv.extend(["--name", name])
    if focus:
        argv.extend(["--focus", focus])
    if no_fit:
        argv.append("--no-fit")
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    from cadquery import importers

    from agentcad.render import parse_view_spec, render_shape, render_shape_custom

    step_path = Path(step_file)
    if not step_path.exists():
        click.echo(json.dumps({
            "command": "render",
            "status": "error",
            "message": f"STEP file '{step_file}' not found",
        }))
        sys.exit(1)

    # Validate --no-fit requires --focus
    if no_fit and not focus:
        click.echo(json.dumps({
            "command": "render",
            "status": "error",
            "message": "--no-fit requires --focus",
        }))
        sys.exit(1)

    # Parse --focus
    focus_point = None
    if focus:
        try:
            focus_point = _parse_focus(focus)
        except ValueError as e:
            click.echo(json.dumps({
                "command": "render",
                "status": "error",
                "message": str(e),
            }))
            sys.exit(1)

    fit = not no_fit
    width, height = size

    # Parse view spec
    try:
        view_specs = parse_view_spec(view)
    except ValueError as e:
        click.echo(json.dumps({
            "command": "render",
            "status": "error",
            "message": str(e),
        }))
        sys.exit(1)

    # Validate --name with multiple views
    if name and len(view_specs) > 1:
        click.echo(json.dumps({
            "command": "render",
            "status": "error",
            "message": "--name cannot be used with multiple views",
        }))
        sys.exit(1)

    # Determine output directory
    parent_dir = step_path.parent
    if _is_version_dir(parent_dir):
        output_dir = parent_dir / "renders"
    else:
        output_dir = parent_dir

    output_dir.mkdir(parents=True, exist_ok=True)

    # Import STEP file (silencer + clean errors via shared helper)
    from agentcad.step_io import load_cad_shape
    try:
        shape = load_cad_shape(step_path)
    except ValueError as exc:
        # Don't let load_cad_shape's ValueError escape as a Python traceback;
        # the command's contract is JSON on stdout.
        click.echo(json.dumps({
            "command": "render",
            "status": "malformed",
            "message": str(exc),
            "suggestion": "Re-export from your CAD tool; the file may be incomplete or corrupted.",
        }))
        sys.exit(1)

    # Render each view
    renders = {}
    for spec_type, spec_value in view_specs:
        if spec_type == "named":
            if name:
                filename = f"{name}.png"
                key = name
            else:
                filename = f"{spec_value}.png"
                key = spec_value
            out_path = output_dir / filename
            render_shape(shape, spec_value, out_path, width=width, height=height,
                         zoom=zoom, focus=focus_point, fit=fit, msaa=msaa)
            renders[key] = str(out_path)
        elif spec_type == "custom":
            azimuth, elevation = spec_value
            if name:
                filename = f"{name}.png"
                key = name
            else:
                key = _format_custom_angle_name(azimuth, elevation)
                filename = f"{key}.png"
            out_path = output_dir / filename
            render_shape_custom(shape, azimuth, elevation, out_path,
                                width=width, height=height, zoom=zoom,
                                focus=focus_point, fit=fit, msaa=msaa)
            renders[key] = str(out_path)

    # Update meta.json if in a version directory
    if _is_version_dir(parent_dir):
        meta_path = parent_dir / "meta.json"
        meta = json.loads(meta_path.read_text())
        existing_renders = meta.get("renders", {})
        existing_renders.update({
            k: str(Path(parent_dir.name) / "renders" / Path(v).name)
            for k, v in renders.items()
        })
        meta["renders"] = existing_renders
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    click.echo(json.dumps({
        "command": "render",
        "status": "success",
        "renders": renders,
    }))

    # Fork off the warm process as the daemon so subsequent commands route
    # through it. Idempotent — silently no-ops if a daemon is already running.
    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
