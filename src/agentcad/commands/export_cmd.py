import json
import re
import sys
from pathlib import Path

import click
from agentcad.project import get_project, project_options, derived_dir

from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.commands._input_recovery import missing_step_payload

VALID_FORMATS = {"stl", "glb", "obj", "usdz"}
# Shared wording for every "Supported:" message. USDZ needs the optional
# `usdz` extra (OpenUSD); requesting it without the extra fails in
# ``usdz_preflight`` before any work, with the install command.
SUPPORTED_FORMATS_TEXT = 'stl, glb, obj, usdz (usdz needs pip install "agentcad[usdz]")'


def parse_export_formats(formats: str) -> list[str]:
    """Split a comma-separated mesh-format value into trimmed, non-empty
    entries. Dropping empties means a trailing comma (e.g. ``stl,``) doesn't
    leave a blank that gets silently ignored downstream."""
    return [f.strip() for f in formats.split(",") if f.strip()]


def unsupported_export_formats(formats: str) -> list[str]:
    """Return the requested formats that aren't in :data:`VALID_FORMATS`.
    Shared by ``agentcad export`` and ``agentcad run --export`` so both
    reject unknown formats identically."""
    return [f for f in parse_export_formats(formats) if f not in VALID_FORMATS]


# Shared so ``export`` and ``run --export`` report an all-blank format list
# identically. Dropping blanks makes ``stl,`` trim cleanly, but when EVERY entry
# is blank (``--format ","``) the result is an empty list, which would otherwise
# export nothing and still report success.
NO_FORMATS_MESSAGE = f"No export formats specified. Supported: {SUPPORTED_FORMATS_TEXT}"


def usdz_preflight(command, formats):
    """Return the missing/broken-OpenUSD error payload if USDZ is requested
    but cannot run, else None. Called before loading geometry, running a
    script, daemon routing, version allocation, or writing any format."""
    if "usdz" not in parse_export_formats(formats):
        return None
    from agentcad.usdz import usdz_unavailable_payload
    return usdz_unavailable_payload(command)


# Formats that carry per-part names and colors when written by `run`.
_PART_AWARE_FORMATS = {"glb", "usdz"}


def _part_loss_warning(meta, fmt):
    """Warn when a standalone export replaces a run's part-aware mesh.

    Export only has the STEP, so it writes one mesh per solid with palette
    colors; the run's captured part ids and requested colors are not in it.
    """
    parts = meta.get("parts") or []
    if fmt not in _PART_AWARE_FORMATS or not fmt_in_outputs(meta, fmt):
        return None
    if len(parts) < 2 and not any(p.get("color") for p in parts):
        return None
    return (
        f"{fmt.upper()} replaced this version's run-written file, which kept its "
        f"{len(parts)} captured part name(s)/color(s). Export reads only the STEP, "
        "so it writes one mesh per solid with palette colors. For part names and "
        f"colors, use `agentcad run SCRIPT --label LABEL --export {fmt}`."
    )


def fmt_in_outputs(meta, fmt):
    return bool((meta.get("outputs") or {}).get(fmt))


def _is_version_dir(directory):
    """Check if directory matches v\\d+_\\w+ pattern and contains meta.json."""
    return bool(re.match(r"v\d+_\w+", directory.name)) and (directory / "meta.json").exists()


@click.command("export")
@click.argument("step_file")
@click.option("--format", "formats", required=True, help='Comma-separated mesh formats: stl, glb, obj, usdz. usdz needs pip install "agentcad[usdz]".')
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
@project_options
def export_cmd(step_file, formats, no_daemon):
    """Export a STEP file to mesh formats (STL, GLB, OBJ, USDZ).

    USDZ (Apple Quick Look / AR) is optional: pip install "agentcad[usdz]".
    """
    # Reject invalid formats before constructing any missing-path retry.
    fmt_list = parse_export_formats(formats)
    if not fmt_list:
        click.echo(json.dumps({
            "command": "export",
            "status": "error",
            "message": NO_FORMATS_MESSAGE,
            "next_actions": ["agentcad export --help"],
        }))
        sys.exit(1)
    invalid = unsupported_export_formats(formats)
    if invalid:
        click.echo(json.dumps({
            "command": "export",
            "status": "error",
            "message": (
                f"Unsupported format(s): {', '.join(invalid)}. Supported: {SUPPORTED_FORMATS_TEXT}. "
                "The input is already STEP; a successful run returns it in outputs.step. "
                "Export is only for mesh formats."
            ),
            "next_actions": ["agentcad export --help"],
        }))
        sys.exit(1)

    unavailable = usdz_preflight("export", formats)
    if unavailable:
        click.echo(json.dumps(unavailable))
        sys.exit(1)

    step_path = Path(step_file)
    if not step_path.is_file():
        click.echo(json.dumps(missing_step_payload("export", step_file, {
            "--format": formats, "--no-daemon": no_daemon,
        })))
        sys.exit(1)

    # Try routing through daemon. Exits before returning if reachable.
    maybe_route_through_daemon(
        ["export", step_file, "--format", formats],
        no_daemon=no_daemon,
    )

    # Import STEP (silencer + clean errors via shared helper)
    from agentcad.step_io import load_cad_shape

    try:
        topo_shape = load_cad_shape(step_path)
    except ValueError as exc:
        click.echo(json.dumps({
            "command": "export",
            "status": "malformed",
            "message": str(exc),
            "suggestion": "Re-export from your CAD tool; the file may be incomplete or corrupted.",
        }))
        sys.exit(1)
    # Determine output directory
    from agentcad.validation import validate_shape
    from agentcad.written_mesh import validate_written_mesh
    from agentcad.export_validation import mesh_warnings

    validation = validate_shape(topo_shape)
    parent_dir = derived_dir("export", step_path)
    stem = step_path.stem  # e.g. "output"

    # Export each format
    outputs = {}
    mesh_validation = {}
    part_warnings = []
    version_meta = (
        json.loads((parent_dir / "meta.json").read_text())
        if _is_version_dir(parent_dir) else {}
    )
    for fmt in fmt_list:
        out_path = parent_dir / f"{stem}.{fmt}"
        if get_project().configured:
            out_path = get_project().artifact_path(out_path)
        try:
            if fmt == "stl":
                from agentcad.export import export_stl
                export_stl(topo_shape, str(out_path))
            elif fmt == "glb":
                from agentcad.export import export_glb
                export_glb(topo_shape, str(out_path))
            elif fmt == "obj":
                from agentcad.export import export_obj
                export_obj(topo_shape, str(out_path))
            elif fmt == "usdz":
                from agentcad.usdz import export_usdz
                export_usdz(topo_shape, str(out_path))
            if not out_path.is_file():
                raise RuntimeError("Writer did not produce a file.")
        except Exception as exc:
            click.echo(json.dumps({
                "command": "export", "status": "error", "failed_format": fmt,
                "message": f"Could not write {fmt.upper()}: {exc}",
                "outputs": outputs, "mesh_validation": mesh_validation,
                "is_valid": validation["is_valid"], "validation": validation,
                "warnings": mesh_warnings(mesh_validation),
            }))
            sys.exit(1)
        outputs[fmt] = str(out_path)
        mesh_validation[fmt] = validate_written_mesh(out_path)
        warning = _part_loss_warning(version_meta, fmt)
        if warning:
            part_warnings.append(warning)

        # Preserve earlier outputs/checks if a later requested writer fails.
        if _is_version_dir(parent_dir):
            meta_path = parent_dir / "meta.json"
            meta = json.loads(meta_path.read_text())
            meta.setdefault("outputs", {})[fmt] = f"{parent_dir.name}/{stem}.{fmt}"
            meta.setdefault("mesh_validation", {})[fmt] = mesh_validation[fmt]
            meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    click.echo(json.dumps({
        "command": "export",
        "status": "success",
        "outputs": outputs,
        "is_valid": validation["is_valid"],
        "validation": validation,
        "mesh_validation": mesh_validation,
        "warnings": mesh_warnings(mesh_validation) + part_warnings,
    }))

    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
