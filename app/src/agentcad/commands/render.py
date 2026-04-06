import json
import re
import sys
from pathlib import Path

import click


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


@click.command()
@click.argument("step_file")
@click.option("--view", required=True, help="View spec: named view(s), 'all', or 'azimuth,elevation'.")
@click.option("--zoom", default=1.0, type=float, help="Zoom factor (applied after FitAll).")
@click.option("--name", default=None, help="Custom filename for the rendered PNG (single view only).")
@click.option("--focus", default=None, help="Camera target point 'x,y,z'.")
@click.option("--no-fit", is_flag=True, default=False, help="Skip FitAll (requires --focus).")
def render(step_file, view, zoom, name, focus, no_fit):
    """Render PNG views of an existing STEP file."""
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

    # Import STEP file
    wp = importers.importStep(str(step_path))
    shape = wp.val().wrapped

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
            render_shape(shape, spec_value, out_path, zoom=zoom,
                         focus=focus_point, fit=fit)
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
            render_shape_custom(shape, azimuth, elevation, out_path, zoom=zoom,
                                focus=focus_point, fit=fit)
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
