import json
import sys
from pathlib import Path

import click


def _emit_error(message, **extra):
    payload = {
        "command": "explode",
        "status": "error",
        "message": message,
        **extra,
    }
    click.echo(json.dumps(payload))
    sys.exit(1)


def _resolve_input(ref):
    """Resolve REF to (step_path, version_entry, meta, version_dir).

    REF may be a STEP/STP file path or a project version reference. File
    inputs return None for the version fields.
    """
    ref_path = Path(ref)
    if ref_path.suffix.lower() in (".step", ".stp"):
        if not ref_path.exists():
            _emit_error(f"STEP file '{ref}' not found.")
        return ref_path.resolve(), None, None, None

    from agentcad.manifest import load_manifest
    from agentcad.commands.parts import _resolve_version

    manifest = load_manifest(command="explode")
    version_entry = _resolve_version(manifest, ref)
    if version_entry is None:
        _emit_error(
            f"Version '{ref}' not found. Pass a version number, vN, label, "
            "current, latest, or a STEP file path.",
            ref=ref,
        )
    version_dir = Path.cwd() / version_entry["path"]
    meta_path = version_dir / "meta.json"
    if not meta_path.exists():
        _emit_error(
            f"meta.json not found for version '{ref}'.",
            ref=ref,
            path=str(meta_path),
        )
    meta = json.loads(meta_path.read_text())
    step_rel = (meta.get("outputs") or {}).get("step")
    if not step_rel:
        _emit_error(
            f"Version '{ref}' has no STEP output to explode.",
            ref=ref,
        )
    step_path = Path.cwd() / step_rel
    if not step_path.exists():
        _emit_error(
            f"STEP output for version '{ref}' is missing on disk.",
            ref=ref,
            path=str(step_path),
        )
    return step_path, version_entry, meta, version_dir


def _build_entries(solids, parts_meta):
    """Group solids into explodable entries.

    Prefer the version's named parts (stable ids, colors, groups). Fall back
    to one entry per solid when parts can't be matched or when part-level
    grouping would leave nothing to explode (single part wrapping several
    solids, like an unnamed compound).
    """
    from agentcad.explode import group_solids_by_part, make_compound

    grouping = "solids"
    entries = []
    if parts_meta:
        grouped = group_solids_by_part(solids, parts_meta)
        if grouped and len(grouped) >= 2:
            grouping = "parts"
            for item in grouped:
                part = item["part"]
                shape = (
                    item["solids"][0]
                    if len(item["solids"]) == 1
                    else make_compound(item["solids"])
                )
                entries.append({
                    "id": part.get("id"),
                    "name": part.get("name"),
                    "color": part.get("color"),
                    "part_of": part.get("part_of"),
                    "topo_shape": shape,
                })
            return entries, grouping

    for idx, solid in enumerate(solids):
        entries.append({
            "id": f"part_{idx + 1}",
            "name": None,
            "color": None,
            "part_of": None,
            "topo_shape": solid,
        })
    return entries, grouping


@click.command(name="explode")
@click.argument("ref")
@click.option(
    "--factor",
    default="100%",
    help=(
        "Explosion amount: a percentage like 50% or a factor like 0.5. "
        "100% doubles each part's distance from the assembly center; "
        "0% is fully assembled."
    ),
)
@click.option(
    "--view",
    "view_spec",
    default="iso",
    help="PNG view(s) to render: named views, 'all', azimuth:elevation, or a mix (same spec as render).",
)
@click.option(
    "--open/--no-open",
    "open_browser",
    default=True,
    help="Open the interactive exploded viewer in a browser.",
)
def explode(ref, factor, view_spec, open_browser):
    """Render an exploded view showing how parts fit together.

    REF may be a version reference (number, vN, label, current, latest) or a
    STEP/STP file path. Parts move apart radially from the assembly center so
    interfaces and overlaps between them become visible. Output includes
    agent-readable PNG renders plus an interactive viewer with an explode
    slider for human review.
    """
    from agentcad.explode import (
        explode_offsets,
        make_compound,
        parse_explode_factor,
        shape_bbox,
        shape_center,
        split_solids,
        translate_shape,
    )

    try:
        factor_value = parse_explode_factor(factor)
    except ValueError as exc:
        _emit_error(str(exc))
    percent = int(round(factor_value * 100))

    step_path, version_entry, meta, version_dir = _resolve_input(ref)

    from agentcad.step_io import load_cad_shape

    try:
        shape = load_cad_shape(step_path)
    except ValueError as exc:
        _emit_error(str(exc))

    solids = split_solids(shape)
    if len(solids) < 2:
        _emit_error(
            "Model contains a single solid; an exploded view needs at least "
            "two separate parts.",
            solid_count=len(solids),
            suggestion=(
                "Use show_object() per part (or show_assembly()) so the model "
                "has multiple parts to explode."
            ),
        )

    parts_meta = (meta or {}).get("parts") or []
    groups_meta = (meta or {}).get("groups") or []
    entries, grouping = _build_entries(solids, parts_meta)
    if len(entries) < 2:
        _emit_error(
            "Model resolved to a single part; nothing to explode.",
            part_count=len(entries),
        )

    assembly_center = shape_center(shape)
    bboxes = [shape_bbox(e["topo_shape"]) for e in entries]
    centers = [
        tuple((lo + hi) / 2 for lo, hi in zip(*bbox)) for bbox in bboxes
    ]
    offsets = explode_offsets(centers, assembly_center, factor_value)

    warnings = []
    stuck = [
        e["id"] for e, off in zip(entries, offsets)
        if factor_value > 0 and off == (0.0, 0.0, 0.0)
    ]
    if stuck:
        warnings.append(
            "Parts centered on the assembly center do not separate in a "
            f"radial explode: {', '.join(stuck)}. Concentric parts stay in "
            "place; inspect them with parts view --isolate instead."
        )

    exploded_shapes = [
        translate_shape(e["topo_shape"], off)
        for e, off in zip(entries, offsets)
    ]
    exploded_compound = make_compound(exploded_shapes)

    # --- agent-facing PNG renders of the exploded state ---
    from agentcad.render import parse_view_spec, render_shape, render_shape_custom

    try:
        view_specs = parse_view_spec(view_spec)
    except ValueError as exc:
        _emit_error(str(exc))

    if version_dir is not None:
        render_dir = version_dir / "renders"
    else:
        render_dir = step_path.parent
    render_dir.mkdir(parents=True, exist_ok=True)

    render_parts = [
        {"id": e["id"], "color": e["color"], "topo_shape": exploded}
        for e, exploded in zip(entries, exploded_shapes)
    ]
    renders = {}
    for spec_type, spec_value in view_specs:
        if spec_type == "named":
            key = f"exploded_{percent}_{spec_value}"
            out_path = render_dir / f"{key}.png"
            render_shape(exploded_compound, spec_value, out_path, parts=render_parts)
        else:
            azimuth, elevation = spec_value
            key = f"exploded_{percent}_{int(azimuth)}_{int(elevation)}"
            out_path = render_dir / f"{key}.png"
            render_shape_custom(
                exploded_compound, azimuth, elevation, out_path, parts=render_parts,
            )
        renders[key] = str(out_path)

    # Record renders in meta.json so diff/context see them like render output.
    if version_dir is not None:
        meta_path = version_dir / "meta.json"
        existing = meta.get("renders", {})
        existing.update({
            k: str(Path(version_dir.name) / "renders" / Path(v).name)
            for k, v in renders.items()
        })
        meta["renders"] = existing
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    # --- interactive viewer with an explode slider (for humans and agents) ---
    # Export a dedicated GLB from the grouped entries so viewer node names
    # always match the entry ids, regardless of how the source GLB was built.
    from agentcad.export import export_glb

    viewer_dir = version_dir if version_dir is not None else step_path.parent
    glb_path = viewer_dir / "explode_view.glb"
    export_glb(shape, str(glb_path), parts=[
        {"id": e["id"], "color": e["color"], "topo_shape": e["topo_shape"]}
        for e in entries
    ])

    label = (
        f"{(meta or {}).get('label', step_path.stem)} exploded {percent}%"
    )
    review_state = {
        "mode": "exploded",
        "source": "agentcad explode",
        "temporary": True,
        "persisted": False,
        "explode": factor_value,
        "review_label": f"Exploded view ({percent}%)",
        "note": (
            "Drag the Explode slider to move parts apart and inspect how "
            "they fit together. Browser changes are not saved."
        ),
    }
    viewer_parts = [
        {k: e[k] for k in ("id", "name", "color", "part_of") if e.get(k) is not None}
        for e in entries
    ]
    viewer_groups = groups_meta if grouping == "parts" else []

    from agentcad.commands.view import _open_browser, _render_unified

    html_path = viewer_dir / f"exploded_{percent}.html"
    _render_unified(
        html_path,
        glb_a=glb_path,
        label_a=label,
        default_mode="single-a",
        parts=viewer_parts,
        groups=viewer_groups,
        part_review=review_state,
    )
    url = html_path.as_uri()
    if open_browser:
        _open_browser(url)

    try:
        viewer_out = html_path.relative_to(Path.cwd()).as_posix()
    except ValueError:
        viewer_out = str(html_path)

    def _round3(values):
        return [round(v, 3) for v in values]

    response = {
        "command": "explode",
        "status": "success",
        "factor": factor_value,
        "percent": percent,
        "mode": "radial",
        "grouping": grouping,
        "assembly_center": _round3(assembly_center),
        "part_count": len(entries),
        "parts": [
            {
                "id": e["id"],
                **({"name": e["name"]} if e["name"] else {}),
                **({"color": e["color"]} if e["color"] else {}),
                "center": _round3(center),
                # Assembled-position bounds, so contact/clearance questions
                # are answerable from this response alone.
                "bounding_box": {
                    axis: [round(lo, 3), round(hi, 3)]
                    for axis, lo, hi in zip("xyz", bbox[0], bbox[1])
                },
                "offset": _round3(offset),
                "moved": offset != (0.0, 0.0, 0.0),
            }
            for e, center, bbox, offset in zip(entries, centers, bboxes, offsets)
        ],
        "renders": renders,
        "viewer": viewer_out,
        "url": url,
        "guidance": (
            "Each part moved (center - assembly_center) * factor away from "
            "the assembly center. Read the exploded render to check which "
            "parts touch, overlap, or float; re-run with a different --factor "
            "to adjust separation."
        ),
    }
    if version_entry is not None:
        response["version"] = meta.get("version", version_entry.get("version"))
        response["label"] = meta.get("label", version_entry.get("label"))
    if warnings:
        response["warnings"] = warnings
    click.echo(json.dumps(response))
