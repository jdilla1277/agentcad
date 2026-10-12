import json
import re
import shlex
import sys
from pathlib import Path

import click
from agentcad.project import get_project, project_options, derived_dir

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.commands._input_recovery import missing_step_payload
from agentcad.view_spec import parse_view_spec

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


def _parse_face_ids(value):
    """Parse '--highlight-faces 3,7' into an ordered, de-duplicated ID list."""
    from agentcad.face_labels import MAX_HIGHLIGHT_FACES

    error = (
        f"Invalid --highlight-faces '{value}'. Expected comma-separated "
        "positive face IDs from `agentcad inspect FILE --ids`, e.g. 3,7."
    )
    ids = []
    for part in value.split(","):
        part = part.strip()
        if not part.isdigit() or not part.isascii() or int(part) < 1:
            raise ValueError(error)
        if int(part) not in ids:
            ids.append(int(part))
    if len(ids) > MAX_HIGHLIGHT_FACES:
        raise ValueError(
            f"--highlight-faces accepts at most {MAX_HIGHLIGHT_FACES} face IDs."
        )
    return ids


def _face_label_hint(highlighted, centroids=None):
    """Actionable next step for faces that got no label in any rendered view.

    ``centroids`` maps face ID -> (x, y, z), used to suggest a --focus
    for faces the camera framing left out of the image.
    """
    hidden, small, cropped = [], [], []
    for entry in highlighted:
        statuses = set(entry["labels"].values())
        if "labeled" in statuses:
            continue
        if "too_small" in statuses:
            small.append(entry["id"])
        elif statuses == {"out_of_frame"}:
            cropped.append(entry["id"])
        else:
            hidden.append(entry["id"])
    hints = []
    if cropped:
        center = (centroids or {}).get(cropped[0])
        point = (
            ",".join(_fmt_coord(v) for v in center) if center is not None else "X,Y,Z"
        )
        hints.append(
            f"Face(s) {', '.join(map(str, cropped))} out of frame: they lie outside "
            "the image in every rendered view (the --zoom/--focus framing cut "
            "them off), so this says nothing about whether they are covered. "
            f"Lower --zoom, or center the camera on the face with --focus {point} "
            f"--no-fit (centroid of face {cropped[0]} from `inspect --ids`; "
            "without --no-fit the view is re-fit to the whole part)."
        )
    if hidden:
        hints.append(
            f"Face(s) {', '.join(map(str, hidden))} hidden: inside the frame but "
            "covered by other faces (or seen edge-on) in every rendered view; try "
            "another named --view that looks at that side (e.g. top, bottom, "
            "back, left, or all)."
        )
    if small:
        hints.append(
            f"Face(s) {', '.join(map(str, small))} visible but too small to label; "
            "check the color, or use --zoom with --focus on the face, or a larger --size."
        )
    return " ".join(hints) or None


def _fmt_coord(value):
    text = f"{round(value, 3):g}"
    return "0" if text == "-0" else text


def _cropped_face_centroids(shape, highlighted):
    """Centroids of faces that were out of frame in every view (for the hint)."""
    from agentcad.topo_ids import _face_area_and_centroid, pick_face_topods

    centroids = {}
    for entry in highlighted:
        if set(entry["labels"].values()) == {"out_of_frame"}:
            try:
                _area, point = _face_area_and_centroid(pick_face_topods(shape, entry["id"]))
            except Exception:
                continue
            centroids[entry["id"]] = (point.X(), point.Y(), point.Z())
    return centroids


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
@click.option("--focus", default=None, help="Camera target point 'x,y,z' (the view is re-fit to the part unless --no-fit).")
@click.option("--no-fit", is_flag=True, default=False, help="Skip FitAll (requires --focus).")
@click.option("--highlight", type=click.Choice(["validation"]), help="Highlight validation failures in red.")
@click.option("--highlight-faces", default=None, metavar="IDS",
              help="Color and label face IDs from `inspect --ids`, e.g. 3,7 (writes to faces/).")
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
@project_options
def render(step_file, view, zoom, size, msaa, name, focus, no_fit, no_daemon,
           highlight=None, highlight_faces=None):
    """Render PNG views of an existing STEP file."""
    if msaa and size[0] * size[1] > MAX_ANTIALIASED_PIXELS:
        raise click.BadParameter(
            "antialiased output size must not exceed 9 million pixels",
            param_hint="--size",
        )

    # Reject known-invalid options before even offering missing-path recovery.
    # This parser is independent of the renderer and must not load CAD libraries.
    try:
        if no_fit and not focus:
            raise ValueError("--no-fit requires --focus")
        focus_point = _parse_focus(focus) if focus is not None else None
        view_specs = parse_view_spec(view)
        if name and len(view_specs) > 1:
            raise ValueError("--name cannot be used with multiple views")
        face_ids = None
        if highlight_faces is not None:
            face_ids = _parse_face_ids(highlight_faces)
            if highlight:
                raise ValueError(
                    "--highlight validation cannot be combined with "
                    "--highlight-faces; run them as separate renders."
                )
    except ValueError as exc:
        click.echo(json.dumps({
            "command": "render", "status": "error", "message": str(exc),
            "next_actions": ["agentcad render --help"],
        }))
        sys.exit(1)

    fit = not no_fit
    width, height = size

    step_path = Path(step_file)
    if not step_path.is_file():
        click.echo(json.dumps(missing_step_payload("render", step_file, {
            "--view": view, "--zoom": zoom, "--size": f"{size[0]}x{size[1]}",
            "--msaa": msaa, "--name": name, "--focus": focus,
            "--no-fit": no_fit, "--highlight": highlight,
            "--highlight-faces": highlight_faces, "--no-daemon": no_daemon,
        })))
        sys.exit(1)

    # Try routing through daemon. Exits before returning if reachable.
    argv = ["render", step_file, "--view", view]
    if highlight:
        argv.extend(["--highlight", highlight])
    if face_ids:
        argv.extend(["--highlight-faces", ",".join(map(str, face_ids))])
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

    from agentcad.render import (
        RenderUnavailableError,
        render_face_highlight,
        render_shape,
        render_shape_custom,
    )

    # Determine output directory
    parent_dir = derived_dir("render", step_path)
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

    validation = None
    render_options = {}
    if highlight:
        from agentcad.validation import validate_shape
        from agentcad.validation_guidance import validation_markers
        validation = validate_shape(shape)
        render_options["validation_markers"] = validation_markers(shape, validation)
        # Preserve ordinary renders when requesting an annotated variant.
        output_dir = output_dir / "validation"
        output_dir.mkdir(parents=True, exist_ok=True)
    elif face_ids:
        from agentcad.topo_ids import topology_counts
        face_count = topology_counts(shape)["faces"]
        out_of_range = [face_id for face_id in face_ids if face_id > face_count]
        if out_of_range:
            listed = ", ".join(map(str, out_of_range))
            click.echo(json.dumps({
                "command": "render",
                "status": "error",
                "message": (
                    f"Face ID{'s' if len(out_of_range) > 1 else ''} {listed} out of "
                    f"range: this shape has {face_count} "
                    f"faces (IDs 1-{face_count})."
                ),
                "next_actions": [shlex.join(["agentcad", "inspect", step_file, "--ids"])],
            }))
            sys.exit(1)
        # Keep annotated variants out of the ordinary render slots.
        output_dir = output_dir / "faces"
        output_dir.mkdir(parents=True, exist_ok=True)
        face_labels_by_id = {face_id: {} for face_id in face_ids}

    def _render_view(spec, out_path, key):
        if face_ids:
            placements = render_face_highlight(
                shape, spec, out_path, face_ids, width=width, height=height,
                zoom=zoom, focus=focus_point, fit=fit, msaa=msaa,
            )
            for face_id, placement in zip(face_ids, placements):
                face_labels_by_id[face_id][key] = placement["status"]
        elif isinstance(spec, str):
            render_shape(shape, spec, out_path, width=width, height=height,
                         zoom=zoom, focus=focus_point, fit=fit, msaa=msaa, **render_options)
        else:
            render_shape_custom(shape, spec[0], spec[1], out_path,
                                width=width, height=height, zoom=zoom,
                                focus=focus_point, fit=fit, msaa=msaa, **render_options)

    # Render each view
    renders = {}
    try:
        for spec_type, spec_value in view_specs:
            if spec_type == "named":
                if name:
                    filename = f"{name}.png"
                    key = name
                else:
                    filename = f"{spec_value}.png"
                    key = spec_value
                out_path = output_dir / filename
                if get_project().configured:
                    out_path = get_project().artifact_path(out_path)
                _render_view(spec_value, out_path, key)
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
                if get_project().configured:
                    out_path = get_project().artifact_path(out_path)
                _render_view((azimuth, elevation), out_path, key)
                renders[key] = str(out_path)
    except RenderUnavailableError as exc:
        click.echo(json.dumps({
            "command": "render",
            "status": "error",
            "error_kind": "render_unavailable",
            "message": str(exc),
            "suggestion": (
                "Use an X11/XWayland desktop session, or prefix the command "
                "with `xvfb-run -a` when running headlessly."
            ),
        }))
        sys.exit(1)

    # Update meta.json if in a version directory
    if _is_version_dir(parent_dir) and not highlight and not face_ids:
        meta_path = parent_dir / "meta.json"
        meta = json.loads(meta_path.read_text())
        existing_renders = meta.get("renders", {})
        existing_renders.update({
            k: str(Path(parent_dir.name) / "renders" / Path(v).name)
            for k, v in renders.items()
        })
        meta["renders"] = existing_renders
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    payload = {
        "command": "render",
        "status": "success",
        "renders": renders,
        **({"validation": validation, "is_valid": validation["is_valid"],
            "highlight": "validation", "highlight_count": len(render_options["validation_markers"])}
           if validation else {}),
    }
    if face_ids:
        from agentcad.face_labels import hex_color, highlight_color
        highlighted = [
            {"id": face_id, "color": hex_color(highlight_color(index)),
             "labels": face_labels_by_id[face_id]}
            for index, face_id in enumerate(face_ids)
        ]
        payload["highlight"] = "faces"
        payload["highlighted_faces"] = highlighted
        hint = _face_label_hint(highlighted, _cropped_face_centroids(shape, highlighted))
        if hint:
            payload["hint"] = hint
    click.echo(json.dumps(payload))

    # Fork off the warm process as the daemon so subsequent commands route
    # through it. Idempotent — silently no-ops if a daemon is already running.
    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
