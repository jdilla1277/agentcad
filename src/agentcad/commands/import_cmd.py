"""`agentcad import` — adopt a CAD file as a versioned baseline.

Phase 1b of the M60 edit journey. Tier 0 only (STEP / STP / BREP); other
formats get the same polite-no responses we already produce in `inspect`.
On success, produces the same v{N}_{label}/ layout `run` produces, plus
`source.{ext}` for provenance — so every existing command (diff, render,
view, inspect) works unchanged on imported versions.
"""
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from agentcad import file_detect
from agentcad.commands.context import TOOL_VERSION
from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.commands.init import _bootstrap_manifest
from agentcad.manifest import MANIFEST_FILE, save_manifest
from agentcad.native_io import silence_native_stdout


@click.command("import", short_help="Import a CAD file as a versioned baseline.")
@click.argument("file")
@click.option("--label", default=None, help="Label override (default: filename stem).")
@click.option(
    "--init", "init_flag", is_flag=True,
    help="Bootstrap a manifest if none exists in the current directory.",
)
@click.option(
    "--view/--no-view", "open_view", default=True,
    help="Open the generated review viewer after a successful import (default on).",
)
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
def import_cmd(file, label, init_flag, open_view, no_daemon):
    """Import a CAD file (STEP/STP/BREP) as a versioned baseline.

    The imported file becomes v_N in the manifest with full provenance
    tracking. Every other command (run, diff, render, view) works on it
    just like a scripted version.
    """
    # Try routing through daemon. Exits before returning if reachable.
    argv = ["import", file]
    if label:
        argv.extend(["--label", label])
    if init_flag:
        argv.append("--init")
    if not open_view:
        argv.append("--no-view")
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    file_path = Path(file)

    # 1. Sniff the file type. Tier 0 only.
    detection = file_detect.detect_file_type(file_path)
    if detection["category"] != file_detect.TIER0_BREP:
        _emit_non_tier0(file, detection)
        return

    # 2. Manifest handling.
    manifest_path = Path.cwd() / MANIFEST_FILE
    if not manifest_path.exists():
        if init_flag:
            _bootstrap_manifest()
        else:
            _emit({
                "command": "import", "status": "error",
                "message": f"{MANIFEST_FILE} not found in current directory.",
                "suggestion": (
                    f"Run `agentcad init` first, or use "
                    f"`agentcad import --init {file}` to bootstrap a manifest "
                    "and import in one command."
                ),
            }, exit_code=1)
            return

    manifest = json.loads(manifest_path.read_text())
    versions = manifest.get("versions", [])

    # 3. Allocate version number + label.
    version_num = len(versions) + 1
    if label is None:
        label = file_path.stem or "import"
    label = _safe_label(label)
    dir_name = f"v{version_num}_{label}"
    version_dir = Path.cwd() / dir_name
    version_dir.mkdir(parents=True, exist_ok=True)

    # 4. Verbatim source copy. Done before parsing so we have provenance even
    #    if the kernel rejects the file.
    ext = file_path.suffix.lower()
    source_copy = version_dir / f"source{ext}"
    try:
        shutil.copy2(str(file_path), str(source_copy))
    except OSError as exc:
        shutil.rmtree(version_dir, ignore_errors=True)
        _emit({
            "command": "import", "status": "error",
            "format_detected": detection.get("format"),
            "message": f"Could not copy source file: {exc}",
        }, exit_code=1)
        return

    # 5. Read the shape via the consolidated loader. It silences fd-1
    #    around the OCCT call and raises a clean ValueError with an
    #    agent-actionable message on empty compounds / null shapes /
    #    parser failures.
    try:
        from agentcad.step_io import load_cad_shape
        topo_shape = load_cad_shape(file_path)
    except Exception as exc:
        shutil.rmtree(version_dir, ignore_errors=True)
        # load_cad_shape's message is already complete (what + why + what to
        # do) — don't wrap it with another sentence that duplicates content.
        _emit({
            "command": "import", "status": "malformed",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": str(exc),
            "suggestion": (
                "If re-exporting from your CAD tool doesn't help, "
                "run `agentcad inspect <file>` for more diagnostic detail."
            ),
        }, exit_code=1)
        return

    # 6. Re-export normalized output.step. Every other command reads this,
    #    so we standardize on STEP regardless of input format.
    try:
        with silence_native_stdout():
            _export_step(topo_shape, version_dir / "output.step")  # noqa: silencer wraps the writer

    except Exception as exc:
        shutil.rmtree(version_dir, ignore_errors=True)
        _emit({
            "command": "import", "status": "malformed",
            "format_detected": detection.get("format"),
            "message": f"Imported the file but couldn't re-export to STEP: {exc}",
            "suggestion": "Geometry may have non-manifold features; try cleaning the source.",
        }, exit_code=1)
        return

    # 7. Metrics + visual artifacts (preview, glb, viewer).
    from agentcad.metrics import compute_metrics
    from agentcad.render import (
        render_composite_4view, render_diff_overlay, render_diff_side_by_side,
    )
    from agentcad.export import export_glb
    from agentcad.commands.view import _render_unified

    with silence_native_stdout():
        metrics = compute_metrics(topo_shape)

    preview_path = version_dir / "preview.png"
    with silence_native_stdout():
        render_composite_4view(topo_shape, preview_path, per_view_size=512)

    glb_path = version_dir / "output.glb"
    with silence_native_stdout():
        export_glb(topo_shape, str(glb_path))

    # 8. Auto-diff against most recent successful prior version.
    diff_meta = None
    prev = _find_prev_success(versions)
    if prev is not None:
        prev_step = Path.cwd() / prev["path"].rstrip("/") / "output.step"
        if prev_step.exists():
            try:
                from agentcad.step_io import load_cad_shape
                prev_shape = load_cad_shape(prev_step)
                side = version_dir / "diff_side.png"
                overlay = version_dir / "diff_overlay.png"
                render_diff_side_by_side(
                    prev_shape, topo_shape, prev["label"], label,
                    side, width=512, height=512,
                )
                comparison = render_diff_overlay(
                    prev_shape, topo_shape, prev["label"], label,
                    overlay, width=1024, height=1024,
                )
                from agentcad.solid_compare import (
                    compare_solid_volumes,
                    write_solid_comparison_artifacts,
                )

                solid_comparison = compare_solid_volumes(
                    prev_shape,
                    topo_shape,
                )
                diff_meta = {
                    "against": prev["label"],
                    "side_by_side": f"{dir_name}/diff_side.png",
                    "overlay": f"{dir_name}/diff_overlay.png",
                    "projection_comparison": comparison,
                    "comparison_3d": solid_comparison.data,
                }
                volume_glb = version_dir / "diff_volume.glb"
                volume_png = version_dir / "diff_volume.png"
                try:
                    if write_solid_comparison_artifacts(
                        solid_comparison,
                        volume_glb,
                        volume_png,
                    ):
                        diff_meta["volume_glb"] = (
                            f"{dir_name}/diff_volume.glb"
                        )
                        diff_meta["volume_png"] = (
                            f"{dir_name}/diff_volume.png"
                        )
                except Exception:
                    # Numeric comparison remains useful if artifact export fails.
                    pass
            except Exception:
                # Diff is best-effort — never fail the whole import.
                diff_meta = None

    # 9. Unified viewer HTML.
    prev_glb = None
    if prev is not None:
        cand = Path.cwd() / prev["path"].rstrip("/") / "output.glb"
        if cand.exists():
            prev_glb = cand
    viewer_path = version_dir / "viewer.html"
    _render_unified(
        viewer_path, prev_glb or glb_path, glb_path if prev_glb else None,
        label_a=prev["label"] if prev_glb else label,
        label_b=label if prev_glb else None,
        default_mode="side-by-side" if prev_glb else "single-a",
        preview_png=preview_path,
        diff_side_png=(version_dir / "diff_side.png") if diff_meta else None,
        diff_overlay_png=(version_dir / "diff_overlay.png") if diff_meta else None,
        diff_volume_png=(
            version_dir / "diff_volume.png"
            if diff_meta and diff_meta.get("volume_png")
            else None
        ),
    )

    viewer_opened = False
    if open_view:
        try:
            from agentcad.commands.view import _open_browser

            viewer_opened = _open_browser(viewer_path.resolve().as_uri()) is not False
        except Exception:
            # Browser launch is best-effort and must not discard a valid import.
            viewer_opened = False

    # 10. Provenance metadata in meta.json.
    sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    outputs = {
        "step": f"{dir_name}/output.step",
        "source": f"{dir_name}/source{ext}",
        "glb": f"{dir_name}/output.glb",
    }
    meta = {
        "command": "import",
        "status": "success",
        "version": version_num,
        "label": label,
        "source": "import",
        "original_filename": file_path.name,
        "sha256": sha256,
        "tool_version": TOOL_VERSION,
        "created": datetime.now(timezone.utc).isoformat(),
        "outputs": outputs,
        "preview": f"{dir_name}/preview.png",
        "viewer": f"{dir_name}/viewer.html",
        "metrics": metrics,
    }
    if diff_meta:
        meta["diff"] = diff_meta
    (version_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")

    # 11. Update manifest with the new version. `source: "import"` flags it
    #     for downstream commands (context, diff) that distinguish journeys.
    versions.append({
        "version": version_num,
        "label": label,
        "status": "success",
        "source": "import",
        "path": f"{dir_name}/",
    })
    manifest["versions"] = versions
    manifest["current"] = label
    save_manifest(manifest)

    # 12. Edit scaffold — write `edit.py` if absent, so the agent has a
    #     templated starting point. The scaffold must match the project's
    #     pinned runtime (b3d uses the M60 edit helpers, cq uses the
    #     kernel-neutral `importers.importStep` fallback documented in
    #     `agentcad docs editing`).
    from agentcad.runners import dispatch
    project_rt = manifest.get("runtime") or dispatch.DEFAULT_RUNTIME
    scaffold_path = Path.cwd() / "edit.py"
    scaffold_written = False
    if not scaffold_path.exists():
        scaffold_path.write_text(
            _edit_scaffold(dir_name, file_path.name, label, project_rt)
        )
        scaffold_written = True

    # 13. Output JSON. Mirrors meta.json plus next_actions per the
    #     `next_actions` design convention.
    response = dict(meta)
    response["viewer_opened"] = viewer_opened
    if scaffold_written:
        response["scaffold"] = "edit.py"
    response["next_actions"] = [
        "agentcad docs editing — read the imported-Part contract, then modify and run edit.py",
        f"agentcad measure {dir_name}/output.step — check dimensions before editing",
    ] if scaffold_written else [
        f"agentcad view {dir_name}/output.step — open in browser to inspect or share with humans",
        f"agentcad measure {dir_name}/output.step — check dimensions before editing",
    ]
    response["more_at"] = "agentcad docs editing"
    click.echo(json.dumps(response))

    maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)


# --- helpers ----------------------------------------------------------------

def _emit(payload: dict, exit_code: int = 0) -> None:
    click.echo(json.dumps(payload))
    if exit_code != 0:
        sys.exit(exit_code)


def _edit_scaffold(
    version_dir: str,
    original_filename: str,
    label: str,
    runtime: str,
) -> str:
    """Templated edit.py — gives the agent a working starting point.

    Build123d projects get the M60 edit helpers (`load_step`,
    `fillet_edges`, etc.). Cadquery projects get the documented
    kernel-neutral path (`importers.importStep` + native selectors)
    since the M60 helpers fail-fast under cq.
    """
    if runtime == "cadquery":
        return _edit_scaffold_cadquery(version_dir, original_filename, label)
    return _edit_scaffold_build123d(version_dir, original_filename, label)


def _edit_scaffold_build123d(version_dir: str, original_filename: str, label: str) -> str:
    return f'''"""Edit script for `{label}` (imported from `{original_filename}`).

Modify the imported part below, then run:

    agentcad run edit.py --output my_edit

Each run produces a new version that auto-diffs against the most recent
prior version (the imported baseline on first run; your previous edit
on subsequent runs). Use `agentcad diff {label} my_edit` for an explicit
comparison against the import.
"""
from build123d import *


# load_step() returns the imported baseline as a build123d Part.
# Part topology collections are methods: base.solids(), base.faces(), base.edges().
# Use base.bounding_box() when the edit itself needs the bounds; for read-only
# dimensions and feature discovery, prefer `agentcad measure` / `agentcad inspect`.
base = load_step("{version_dir}/output.step")


# --- Edit here ---------------------------------------------------------------
# Examples (delete or replace as needed):
#
#   target features by ID — run `agentcad inspect <step> --ids` to see them:
#     result = fillet_edges(base, [4, 6, 8, 9], 0.5)   # IDs from inspect output
#     result = chamfer_edges(base, 4, 1.0)             # single ID also works
#     result = shell_faces(base, top_face_id, -1.5)    # negative = inward wall
#
#   or use build123d operators directly:
#     result = base + Box(10, 10, 5).move(Location((25, 0, 0)))   # union
#     result = base - Cylinder(radius=2.5, height=20)             # cut
#
result = base


show_object(result)
'''


def _edit_scaffold_cadquery(version_dir: str, original_filename: str, label: str) -> str:
    """Cadquery scaffold — uses the kernel-neutral fallback documented in
    `agentcad docs editing`. The M60 edit helpers (`load_step`,
    `fillet_edges`, `pick_face`, `pick_edge`) are build123d-only and
    fail validation under cq, so cq scripts go through
    `cadquery.importers.importStep` and use native selectors.
    """
    return f'''"""Edit script for `{label}` (imported from `{original_filename}`).

Modify the imported part below, then run:

    agentcad run edit.py --output my_edit

Each run produces a new version that auto-diffs against the most recent
prior version. Use `agentcad diff {label} my_edit` for an explicit
comparison against the import.
"""
import cadquery as cq
from cadquery import importers


# Load the imported baseline as a cadquery Workplane.
base = importers.importStep("{version_dir}/output.step")


# --- Edit here ---------------------------------------------------------------
# Examples (delete or replace as needed):
#
#   filter edges by position, then fillet/chamfer:
#     bottom_rim = [
#         e for e in base.edges().vals()
#         if abs(e.BoundingBox().zmin) < 1e-3 and abs(e.BoundingBox().zmax) < 1e-3
#     ]
#     solid = base.val()
#     result = cq.Workplane("XY").add(solid.fillet(0.5, bottom_rim))
#
#   or use cadquery operators directly:
#     result = base.union(cq.Workplane("XY").box(10, 10, 5).translate((25, 0, 2.5)))
#     result = base.cut(cq.Workplane("XY").circle(2.5).extrude(20))
#
result = base


show_object(result)
'''


def _safe_label(s: str) -> str:
    """Sanitize a label for use as a directory name. Keeps alphanumerics,
    hyphens, and underscores; replaces everything else with underscores."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in s)
    return safe or "import"


def _find_prev_success(versions):
    for v in reversed(versions):
        if v.get("status") == "success":
            return v
    return None


def _export_step(topo_shape, output_path: Path) -> None:
    """Re-export a TopoDS_Shape to STEP via cadquery's exporter."""
    import cadquery as cq
    from cadquery import exporters
    wp = cq.Workplane().newObject([cq.Shape.cast(topo_shape)])
    exporters.export(wp, str(output_path))


# --- non-Tier-0 polite-no responses ----------------------------------------
# These mirror the responses in `inspect_cmd._inspect_*` branches but adapt
# the messaging to the import context ("can't import" vs "can't edit").

def _emit_non_tier0(file: str, detection: dict) -> None:
    category = detection["category"]

    if category == file_detect.MISSING:
        _emit({
            "command": "import", "status": "error",
            "format_detected": None,
            "message": f"File '{file}' not found.",
            "suggestion": (
                "Check the path — the file does not exist at this location. "
                "If you expected it from a download, verify the source returned the file."
            ),
        }, exit_code=1)
        return

    if category == file_detect.NOT_A_FILE:
        reason = detection.get("reason", "not_a_regular_file")
        _emit({
            "command": "import", "status": "error",
            "format_detected": None,
            "message": f"Path '{file}' is not a regular file ({reason}).",
            "suggestion": "Pass a path to a real CAD file.",
        }, exit_code=1)
        return

    if category == file_detect.EMPTY:
        _emit({
            "command": "import", "status": "empty",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": 0,
            "message": f"File '{file}' is empty.",
            "suggestion": "Re-export from your CAD tool; the file may be a failed write.",
        }, exit_code=1)
        return

    if category == file_detect.MALFORMED:
        expected = detection.get("expected_format")
        actual = detection.get("format")
        if actual == "html":
            message = (
                f"Extension is .{expected} but content is HTML — likely a failed "
                "download (error page saved instead of the file)."
            )
            suggestion = (
                "Re-download the file; verify the source URL returns the CAD file, not HTML."
            )
        else:
            message = (
                f"Extension claims '{expected}' but content looks like '{actual}'. "
                "File appears mislabeled or corrupted."
            )
            suggestion = (
                f"Re-export as {expected} from your CAD tool, or rename to match the real format."
            )
        _emit({
            "command": "import", "status": "malformed",
            "format_detected": actual,
            "expected_format": expected,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": message,
            "suggestion": suggestion,
        }, exit_code=1)
        return

    if category == file_detect.UNKNOWN_FORMAT:
        _emit({
            "command": "import", "status": "unknown_format",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"agentcad doesn't recognize extension '{detection.get('extension')}' "
                "for import."
            ),
            "suggestion": (
                "agentcad imports STEP / STP / BREP files. "
                "If this is a CAD file, re-export as STEP."
            ),
        }, exit_code=1)
        return

    if category == file_detect.DISPLAY_FORMAT:
        fmt = detection["format"]
        _emit({
            "command": "import", "status": "display_format",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"{fmt.upper()} is an export/display format, not a CAD source file. "
                "agentcad's edit pipeline operates on B-rep (STEP/BREP)."
            ),
            "suggestion": (
                "Ask the original tool to export STEP — agentcad imports STEP losslessly."
            ),
        }, exit_code=1)
        return

    if category == file_detect.TIER2_RECOGNIZED:
        fmt = detection["format"]
        _emit({
            "command": "import", "status": "recognized_deferred",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"Format '{fmt}' is recognized but not in agentcad's v0 import scope."
            ),
            "suggestion": (
                "Convert to STEP (lossless for B-rep formats) or run "
                "`agentcad feedback` if support would unblock you."
            ),
        }, exit_code=1)
        return

    if category == file_detect.TIER1_MESH:
        _emit({
            "command": "import", "status": "limited",
            "format_detected": detection["format"],
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "editable": False,
            "message": (
                "Can't import an STL — agentcad's edit pipeline is B-rep only. "
                "STL files describe shapes as triangle meshes, which can't be "
                "filleted, shelled, or have faces selected."
            ),
            "suggestion": (
                "Recreate the part parametrically (run `agentcad docs patterns` "
                "for examples), or ask the original tool to export STEP."
            ),
        }, exit_code=1)
        return

    # Fallback — should never happen if file_detect categories stay in sync.
    _emit({
        "command": "import", "status": "error",
        "format_detected": detection.get("format"),
        "message": f"Unhandled file category '{category}'.",
    }, exit_code=1)
