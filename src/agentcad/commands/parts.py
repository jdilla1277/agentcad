import json
import re
import sys
from pathlib import Path

import click

from agentcad.manifest import load_manifest


def _emit_error(message, *, code=1, **extra):
    payload = {
        "command": "parts",
        "status": "error",
        "message": message,
        **extra,
    }
    click.echo(json.dumps(payload))
    sys.exit(code)


def _resolve_version(manifest, ref):
    """Resolve a version ref by number, vN, label, version dir, current, or latest."""
    versions = manifest.get("versions", [])
    successful = [v for v in versions if v.get("status") == "success"]

    if ref in {"current", "latest"}:
        if ref == "current" and manifest.get("current"):
            ref = str(manifest["current"])
        elif successful:
            return successful[-1]
        else:
            return None

    candidates = []
    try:
        candidates.append(("version", int(ref)))
    except ValueError:
        if ref.startswith("v") and ref[1:].isdigit():
            candidates.append(("version", int(ref[1:])))

    normalized_path = ref.strip().strip("/")
    for v in versions:
        for kind, value in candidates:
            if kind == "version" and v.get("version") == value:
                return v
        if v.get("label") == ref:
            return v
        if str(v.get("path", "")).strip("/") == normalized_path:
            return v
    return None


def _load_version_meta(version_entry):
    meta_path = Path.cwd() / version_entry["path"] / "meta.json"
    if not meta_path.exists():
        _emit_error(
            f"meta.json not found for version '{version_entry.get('label')}'.",
            version=version_entry.get("version"),
            label=version_entry.get("label"),
            path=str(meta_path),
        )
    return json.loads(meta_path.read_text())


def _normalize_part(part, idx):
    """Return the part payload agents should reference from the parts CLI.

    New versions already have string ids + id_source. Older meta.json files
    stored numeric positional IDs, so expose a string `part_<n>` handle while
    preserving the raw legacy value for diagnostics.
    """
    normalized = dict(part)
    if "id_source" not in normalized:
        raw_id = normalized.get("id", idx)
        normalized["legacy_id"] = raw_id
        normalized["id"] = f"part_{raw_id}" if isinstance(raw_id, int) else str(raw_id)
        normalized["id_source"] = "legacy"
        normalized["version_local"] = True
    return normalized


def _parts_from_meta(meta):
    return [
        _normalize_part(part, idx)
        for idx, part in enumerate(meta.get("parts", []))
    ]


def _base_response(manifest, version_entry, meta):
    response = {
        "project": manifest.get("name"),
        "version": meta.get("version", version_entry.get("version")),
        "label": meta.get("label", version_entry.get("label")),
        "version_status": meta.get("status", version_entry.get("status")),
        "path": version_entry.get("path"),
    }
    if meta.get("viewer"):
        response["viewer"] = meta["viewer"]
    if meta.get("viewer_glb"):
        response["viewer_glb"] = meta["viewer_glb"]
    if meta.get("script"):
        response["script"] = meta["script"]
    if meta.get("outputs"):
        response["outputs"] = meta["outputs"]
    return response


def _slugify_review_name(values):
    raw = "_".join(str(v) for v in values if v)
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", raw).strip("._-")
    return slug[:80] or "all"


def _validate_part_ids(parts, ids):
    available = {str(p.get("id")) for p in parts}
    missing = [part_id for part_id in ids if part_id not in available]
    return missing, sorted(available)


def _dedupe_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


@click.group(name="parts")
def parts_cmd():
    """List and inspect parts captured for version snapshots.

    Reads the same <version>/meta.json snapshot used by viewer.html.
    Version refs may be a number, vN, label, version directory, current,
    or latest.
    """


@parts_cmd.command(name="list")
@click.argument("ref")
def list_parts(ref):
    """List parts for a version reference.

    REF may be a version number (1), vN (v1), label, version directory,
    current, or latest. Output is JSON with version, label, viewer,
    outputs, part_count, and parts[].

    New-schema parts have string id plus id_source. Legacy numeric part
    IDs are exposed as part_<n>, with legacy_id and version_local=true.
    """
    manifest = load_manifest(command="parts")
    version_entry = _resolve_version(manifest, ref)
    if version_entry is None:
        _emit_error(f"Version '{ref}' not found.", ref=ref)

    meta = _load_version_meta(version_entry)
    parts = _parts_from_meta(meta)
    click.echo(json.dumps({
        "command": "parts list",
        "status": "success",
        **_base_response(manifest, version_entry, meta),
        "part_count": len(parts),
        "parts": parts,
    }))


@parts_cmd.command(name="show")
@click.argument("ref")
@click.argument("part_id")
def show_part(ref, part_id):
    """Show one part by id for a version reference.

    REF accepts the same forms as `parts list`. PART_ID is the stable
    string id from parts[]. For legacy numeric IDs, either part_<n> or
    the raw numeric alias is accepted.

    Output is JSON with version, label, viewer, outputs, and one part.
    """
    manifest = load_manifest(command="parts")
    version_entry = _resolve_version(manifest, ref)
    if version_entry is None:
        _emit_error(f"Version '{ref}' not found.", ref=ref)

    meta = _load_version_meta(version_entry)
    parts = _parts_from_meta(meta)
    for part in parts:
        aliases = {str(part.get("id"))}
        if "legacy_id" in part:
            aliases.add(str(part["legacy_id"]))
        if part_id in aliases:
            click.echo(json.dumps({
                "command": "parts show",
                "status": "success",
                **_base_response(manifest, version_entry, meta),
                "part": part,
            }))
            return

    _emit_error(
        f"Part '{part_id}' not found in version '{ref}'.",
        ref=ref,
        part_id=part_id,
        available_ids=[p.get("id") for p in parts],
    )


@parts_cmd.command(name="view")
@click.argument("ref")
@click.option(
    "--isolate",
    "isolate_ids",
    multiple=True,
    help="Part id to isolate in the generated review viewer. Repeat for multiple parts.",
)
@click.option(
    "--hide",
    "hide_ids",
    multiple=True,
    help="Part id to hide in the generated review viewer. Repeat for multiple parts.",
)
@click.option("--ghost-rest", is_flag=True, default=False, help="Make non-selected parts transparent.")
@click.option("--focus", "focus_id", default=None, help="Part id to focus the camera on.")
@click.option("--open/--no-open", "open_browser", default=True, help="Open the generated viewer in a browser.")
def view_parts(ref, isolate_ids, hide_ids, ghost_rest, focus_id, open_browser):
    """Generate a reproducible part review viewer for agents and humans.

    REF accepts the same forms as `parts list`. The generated HTML embeds the
    version's viewer GLB plus a review state for hide/isolate/ghost/focus.
    """
    manifest = load_manifest(command="parts")
    version_entry = _resolve_version(manifest, ref)
    if version_entry is None:
        _emit_error(f"Version '{ref}' not found.", ref=ref)

    meta = _load_version_meta(version_entry)
    parts = _parts_from_meta(meta)
    if not parts:
        _emit_error(
            f"Version '{ref}' has no parts snapshot.",
            ref=ref,
            version=meta.get("version", version_entry.get("version")),
            label=meta.get("label", version_entry.get("label")),
        )

    isolate_ids = _dedupe_preserve_order(isolate_ids)
    hide_ids = _dedupe_preserve_order(hide_ids)
    requested_ids = [*isolate_ids, *hide_ids]
    if focus_id:
        requested_ids.append(focus_id)
    missing, available_ids = _validate_part_ids(parts, requested_ids)
    if missing:
        _emit_error(
            "Unknown part id requested for review viewer.",
            ref=ref,
            missing_ids=missing,
            available_ids=available_ids,
        )

    viewer_glb = meta.get("viewer_glb")
    if not viewer_glb:
        _emit_error(
            f"Version '{ref}' has no viewer_glb artifact.",
            ref=ref,
            version=meta.get("version", version_entry.get("version")),
            label=meta.get("label", version_entry.get("label")),
        )
    glb_path = Path.cwd() / viewer_glb
    if not glb_path.exists():
        _emit_error(
            f"viewer_glb not found for version '{ref}'.",
            ref=ref,
            viewer_glb=viewer_glb,
            path=str(glb_path),
        )

    selected = focus_id or (isolate_ids[0] if isolate_ids else None)
    review_state = {
        "mode": "part-review",
        "source": "agentcad parts view",
        "version": meta.get("version", version_entry.get("version")),
        "label": meta.get("label", version_entry.get("label")),
        "selected": selected,
        "focus": focus_id,
        "isolated": isolate_ids,
        "hidden": hide_ids,
        "ghost_rest": ghost_rest,
    }

    from agentcad.commands.view import _open_browser, _render_unified

    version_dir = Path.cwd() / version_entry["path"]
    name_parts = ["parts_review"]
    if isolate_ids:
        name_parts.extend(["isolate", *isolate_ids])
    if hide_ids:
        name_parts.extend(["hide", *hide_ids])
    if focus_id:
        name_parts.extend(["focus", focus_id])
    if ghost_rest:
        name_parts.append("ghost")
    html_path = version_dir / f"{_slugify_review_name(name_parts)}.html"
    _render_unified(
        html_path,
        glb_a=glb_path,
        label_a=f"{meta.get('label', version_entry.get('label'))} parts",
        default_mode="single-a",
        parts=parts,
        part_review=review_state,
    )

    url = html_path.as_uri()
    if open_browser:
        _open_browser(url)

    rel_html = html_path.relative_to(Path.cwd()).as_posix()
    response = {
        "command": "parts view",
        "status": "success",
        **_base_response(manifest, version_entry, meta),
        "review_viewer": rel_html,
        "url": url,
        "part_review": review_state,
    }
    click.echo(json.dumps(response))
