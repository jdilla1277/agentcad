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

from agentcad import __version__
from agentcad import file_detect
from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.commands.init import _bootstrap_manifest
from agentcad.comparison_phases import ComparisonPhaseRecorder
from agentcad.manifest import MANIFEST_FILE
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
@click.option(
    "--diff/--no-diff",
    "auto_diff",
    default=True,
    help=(
        "Automatically compare against the previous successful version "
        "(default on). --no-diff skips automatic comparison; explicit "
        "`agentcad diff` remains available."
    ),
)
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help=(
        "Pin the CAD engine of the manifest bootstrapped by --init, and so of "
        "the edit.py scaffold. Mirrors `agentcad init --runtime`. Only valid "
        "together with --init: an existing manifest already records its "
        "runtime. Defaults to build123d."
    ),
)
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
def import_cmd(file, label, init_flag, open_view, auto_diff, runtime, no_daemon):
    """Import a CAD file (STEP/STP/BREP) as a versioned baseline.

    The imported file becomes v_N in the manifest with full provenance
    tracking. Every other command (run, diff, render, view) works on it
    just like a scripted version.
    """
    # --runtime pins only the manifest that --init creates. Refuse it when a
    # manifest already exists even if --init was also passed: --init is a
    # no-op in that case, so accepting the flag would silently ignore the
    # requested runtime and recreate the wrong-engine footgun this option
    # exists to remove.
    manifest_path = Path.cwd() / MANIFEST_FILE
    if runtime and manifest_path.exists():
        _emit({
            "command": "import", "status": "error",
            "message": (
                f"--runtime only applies when --init creates a new "
                f"{MANIFEST_FILE}; the manifest already exists."
            ),
            "suggestion": (
                f"Run `agentcad import {file}` to use the project's recorded "
                f"runtime, or use `agentcad import --init --runtime {runtime} "
                f"{file}` in a fresh directory. To override the engine for one "
                f"script, use `agentcad run --runtime {runtime} <script>`."
            ),
        }, exit_code=1)
        return

    if runtime and not init_flag:
        _emit({
            "command": "import", "status": "error",
            "message": f"--runtime requires --init to create {MANIFEST_FILE}.",
            "suggestion": (
                f"Use `agentcad import --init --runtime {runtime} {file}` to "
                "bootstrap the project and import in one command."
            ),
        }, exit_code=1)
        return

    # Try routing through daemon. Exits before returning if reachable.
    argv = ["import", file]
    if label:
        argv.extend(["--label", label])
    if init_flag:
        argv.append("--init")
    if runtime:
        argv.extend(["--runtime", runtime])
    if not open_view:
        argv.append("--no-view")
    if not auto_diff:
        argv.append("--no-diff")
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    file_path = Path(file)

    # 1. Sniff the file type. Tier 0 only.
    detection = file_detect.detect_file_type(file_path)
    if detection["category"] != file_detect.TIER0_BREP:
        _emit_non_tier0(file, detection)
        return

    # 2. Manifest handling.
    if not manifest_path.exists():
        if init_flag:
            _bootstrap_manifest(runtime=runtime)
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

    # 3. Resolve the label. Version numbers are reserved atomically only after
    # parsing/metrics establish whether this is valid or diagnostic output.
    if label is None:
        label = file_path.stem or "import"
    label = _safe_label(label)
    ext = file_path.suffix.lower()

    # 4. Read the shape via the consolidated loader. It silences fd-1
    #    around the OCCT call and raises a clean ValueError with an
    #    agent-actionable message on empty compounds / null shapes /
    #    parser failures.
    try:
        from agentcad.step_io import load_cad_shape
        topo_shape = load_cad_shape(file_path)
    except Exception as exc:
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

    # 5. Aggregate metrics and final validity are core work. Invalid geometry
    # is preserved for diagnosis but never normalized, previewed, or made
    # current.
    from agentcad.metrics import compute_metrics

    with silence_native_stdout():
        metrics = compute_metrics(topo_shape)

    from agentcad.core_build import invalid_geometry_payload

    invalid_response = invalid_geometry_payload("import", metrics)
    if invalid_response is not None:
        from agentcad.versioning import commit_version, reserve_version

        reservation = reserve_version(Path.cwd(), label, suffix="_invalid")
        version_num = reservation.number
        invalid_dir_name = reservation.dir_name
        invalid_dir = reservation.path
        source_copy = invalid_dir / f"source{ext}"
        try:
            shutil.copy2(str(file_path), str(source_copy))
        except OSError as exc:
            shutil.rmtree(invalid_dir, ignore_errors=True)
            _emit({
                "command": "import", "status": "error",
                "format_detected": detection.get("format"),
                "message": f"Could not copy source file: {exc}",
            }, exit_code=1)
            return

        invalid_meta = {
            **invalid_response,
            "version_recorded": True,
            "version": version_num,
            "label": label,
            "source": "import",
            "original_filename": file_path.name,
            "sha256": hashlib.sha256(file_path.read_bytes()).hexdigest(),
            "tool_version": __version__,
            "created": datetime.now(timezone.utc).isoformat(),
            "outputs": {"source": f"{invalid_dir_name}/source{ext}"},
        }
        commit_version(reservation, invalid_meta, {
            "version": version_num,
            "label": label,
            "status": "invalid_geometry",
            "source": "import",
            "path": f"{invalid_dir_name}/",
        }, advance_current=False)
        _emit({**invalid_meta, "path": f"{invalid_dir_name}/"}, exit_code=1)
        return

    # 6. Materialize provenance only after the input has parsed and passed
    # validity. Parser errors do not consume a version.
    from agentcad.versioning import reserve_version

    reservation = reserve_version(Path.cwd(), label)
    version_num = reservation.number
    dir_name = reservation.dir_name
    version_dir = reservation.path
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

    # 7. Re-export normalized output.step. Every other command reads this,
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

    # Core build boundary: source loading, metrics, validity, provenance copy,
    # and normalized STEP export have succeeded. Commit before visual work.
    from agentcad.core_build import ArtifactLifecycle
    from agentcad.versioning import commit_version

    prev = _find_prev_success(versions)
    has_previous_step = bool(
        prev
        and (Path.cwd() / prev["path"].rstrip("/") / "output.step").exists()
    )

    def _artifact_state(enabled, skipped_message):
        if enabled:
            return {"status": "pending"}
        return {"status": "skipped", "message": skipped_message}

    sha256 = hashlib.sha256(file_path.read_bytes()).hexdigest()
    created = datetime.now(timezone.utc).isoformat()
    meta = {
        "command": "import",
        "status": "success",
        "core": {"status": "success", "committed_at": created},
        "version": version_num,
        "label": label,
        "source": "import",
        "original_filename": file_path.name,
        "sha256": sha256,
        "tool_version": __version__,
        "created": created,
        "outputs": {
            "step": f"{dir_name}/output.step",
            "source": f"{dir_name}/source{ext}",
        },
        "metrics": metrics,
        "artifacts": {
            "preview": {"status": "pending"},
            "viewer_glb": {"status": "pending"},
            "diff": _artifact_state(
                auto_diff and has_previous_step,
                (
                    "Automatic comparison disabled with --no-diff."
                    if not auto_diff
                    else "No previous successful STEP to compare."
                ),
            ),
            "viewer": {"status": "pending"},
            "browser": _artifact_state(
                open_view, "Browser launch disabled with --no-view."
            ),
            "edit_scaffold": {"status": "pending"},
        },
    }
    comparison_enabled = auto_diff and has_previous_step
    if comparison_enabled:
        meta["comparison_phases"] = ComparisonPhaseRecorder().entries
    commit_version(reservation, meta, {
        "version": version_num,
        "label": label,
        "status": "success",
        "source": "import",
        "path": f"{dir_name}/",
    }, advance_current=True)
    lifecycle = ArtifactLifecycle(version_dir / "meta.json", meta)
    comparison_recorder = None
    if comparison_enabled:
        comparison_recorder = ComparisonPhaseRecorder(
            lifecycle.meta["comparison_phases"],
            persist=lifecycle.persist,
        )

    def _attempt_artifact(name, callback):
        try:
            value = callback()
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            lifecycle.set_artifact(name, "failed", message=message)
            lifecycle.add_warning(
                f"Optional {name} work failed; core STEP remains successful: {message}"
            )
            return False, None
        lifecycle.set_artifact(name, "success")
        return True, value

    # 8. Visual artifacts (preview, glb, viewer).
    from agentcad.render import render_composite_4view
    from agentcad.export import export_glb
    from agentcad.commands.view import _render_unified

    preview_path = version_dir / "preview.png"

    def _render_preview():
        with silence_native_stdout():
            render_composite_4view(topo_shape, preview_path, per_view_size=512)

    preview_ok, _ = _attempt_artifact("preview", _render_preview)
    if preview_ok:
        lifecycle.meta["preview"] = f"{dir_name}/preview.png"
        lifecycle.persist()

    glb_path = version_dir / "output.glb"
    def _export_viewer_glb():
        with silence_native_stdout():
            export_glb(topo_shape, str(glb_path))

    glb_ok, _ = _attempt_artifact("viewer_glb", _export_viewer_glb)
    if glb_ok:
        lifecycle.meta["outputs"]["glb"] = f"{dir_name}/output.glb"
        lifecycle.persist()

    # 8. Auto-diff against most recent successful prior version.
    diff_meta = None
    if comparison_recorder is not None:
        prev_step = Path.cwd() / prev["path"].rstrip("/") / "output.step"
        prev_shape = None
        try:
            with comparison_recorder.observe("source_loading"):
                from agentcad.step_io import load_cad_shape
                prev_shape = load_cad_shape(prev_step)
        except Exception as exc:
            lifecycle.add_warning(
                "Could not load the prior comparison source; core STEP remains "
                f"successful: {type(exc).__name__}: {exc}"
            )

        if prev_shape is not None:
            from agentcad.render import (
                render_comparison_source_views,
                render_diff_overlay,
                render_diff_side_by_side,
            )

            diff_meta = {"against": prev["label"]}
            side = version_dir / "diff_side.png"
            overlay = version_dir / "diff_overlay.png"
            source_views = None
            try:
                with comparison_recorder.observe("comparison_rendering"):
                    source_views = render_comparison_source_views(
                        prev_shape,
                        topo_shape,
                        per_view_size=512,
                    )
                    render_diff_side_by_side(
                        prev_shape, topo_shape, prev["label"], label,
                        side, width=512, height=512,
                        source_views=source_views,
                    )
                diff_meta["side_by_side"] = f"{dir_name}/diff_side.png"
            except Exception as exc:
                lifecycle.add_warning(
                    "Could not render the side-by-side comparison; core STEP "
                    f"remains successful: {type(exc).__name__}: {exc}"
                )

            try:
                if source_views is None:
                    raise RuntimeError(
                        "Comparison source views were unavailable."
                    )
                with comparison_recorder.observe("projection_comparison"):
                    comparison = render_diff_overlay(
                        prev_shape, topo_shape, prev["label"], label,
                        overlay, width=1024, height=1024,
                        source_views=source_views,
                    )
                diff_meta["overlay"] = f"{dir_name}/diff_overlay.png"
                diff_meta["projection_comparison"] = comparison
            except Exception as exc:
                lifecycle.add_warning(
                    "Could not compute the 2D projection comparison; core STEP "
                    f"remains successful: {type(exc).__name__}: {exc}"
                )

            solid_comparison = None
            try:
                from agentcad.solid_compare import (
                    compare_solid_volumes_with_fallback,
                )

                solid_comparison = compare_solid_volumes_with_fallback(
                    prev_shape,
                    topo_shape,
                    phase_recorder=comparison_recorder,
                )
                diff_meta["comparison_3d"] = solid_comparison.data
            except Exception as exc:
                lifecycle.add_warning(
                    "Could not compute the exact 3D comparison; core STEP "
                    f"remains successful: {type(exc).__name__}: {exc}"
                )

            if solid_comparison is not None and solid_comparison.available:
                try:
                    from agentcad.solid_compare import (
                        write_solid_comparison_artifacts,
                    )

                    with comparison_recorder.observe(
                        "difference_artifact_export"
                    ) as phase:
                        volume_glb = version_dir / "diff_volume.glb"
                        volume_png = version_dir / "diff_volume.png"
                        written = write_solid_comparison_artifacts(
                            solid_comparison,
                            volume_glb,
                            volume_png,
                        )
                        if not written:
                            phase.status = "unavailable"
                            phase.message = (
                                "3D difference artifacts were not available."
                            )
                    if written:
                        diff_meta["volume_glb"] = (
                            f"{dir_name}/diff_volume.glb"
                        )
                        diff_meta["volume_png"] = (
                            f"{dir_name}/diff_volume.png"
                        )
                except Exception as exc:
                    lifecycle.add_warning(
                        "Could not write 3D volume comparison artifacts; core "
                        "STEP remains successful: "
                        f"{type(exc).__name__}: {exc}"
                    )
            else:
                comparison_recorder.skip(
                    "difference_artifact_export",
                    "Exact 3D comparison produced no exportable geometry.",
                )

        comparison_recorder.finalize_pending(
            "Skipped because an earlier comparison phase was unavailable."
        )
        useful_diff = bool(diff_meta and any(
            key in diff_meta
            for key in (
                "side_by_side",
                "overlay",
                "projection_comparison",
                "comparison_3d",
            )
        ))
        if not useful_diff:
            lifecycle.set_artifact(
                "diff",
                "unavailable",
                message="Comparison artifacts could not be generated.",
            )
        else:
            lifecycle.meta["diff"] = diff_meta
            lifecycle.set_artifact("diff", "success")

    # 9. Unified viewer HTML.
    prev_glb = None
    if auto_diff and prev is not None:
        cand = Path.cwd() / prev["path"].rstrip("/") / "output.glb"
        if cand.exists():
            prev_glb = cand
    viewer_path = version_dir / "viewer.html"
    viewer_ok = False
    if glb_ok:
        def _write_viewer():
            def _render():
                _render_unified(
                    viewer_path,
                    prev_glb or glb_path,
                    glb_path if prev_glb else None,
                    label_a=prev["label"] if prev_glb else label,
                    label_b=label if prev_glb else None,
                    default_mode="side-by-side" if prev_glb else "single-a",
                    preview_png=preview_path if preview_ok else None,
                    diff_side_png=(
                        version_dir / "diff_side.png"
                        if diff_meta and diff_meta.get("side_by_side")
                        else None
                    ),
                    diff_overlay_png=(
                        version_dir / "diff_overlay.png"
                        if diff_meta and diff_meta.get("overlay")
                        else None
                    ),
                    diff_volume_png=(
                        version_dir / "diff_volume.png"
                        if diff_meta and diff_meta.get("volume_png")
                        else None
                    ),
                )

            if comparison_recorder is not None:
                with comparison_recorder.observe("viewer_generation"):
                    _render()
            else:
                _render()

        viewer_ok, _ = _attempt_artifact("viewer", _write_viewer)
        if viewer_ok:
            lifecycle.meta["viewer"] = f"{dir_name}/viewer.html"
            lifecycle.persist()
    else:
        lifecycle.set_artifact(
            "viewer",
            "skipped",
            message="Viewer GLB was unavailable.",
        )

    viewer_opened = False
    if open_view and viewer_ok:
        try:
            from agentcad.commands.view import _open_browser

            viewer_opened = _open_browser(viewer_path.resolve().as_uri()) is not False
            lifecycle.set_artifact(
                "browser",
                "success" if viewer_opened else "unavailable",
                message=None if viewer_opened else "Browser did not open.",
            )
        except Exception as exc:
            # Browser launch is best-effort and must not discard a valid import.
            viewer_opened = False
            lifecycle.set_artifact(
                "browser", "failed", message=f"{type(exc).__name__}: {exc}"
            )
    elif open_view:
        lifecycle.set_artifact(
            "browser", "skipped", message="Viewer artifact was unavailable."
        )

    # 10. Edit scaffold — write `edit.py` if absent, so the agent has a
    #     templated starting point. The scaffold must match the project's
    #     pinned runtime (b3d uses the M60 edit helpers, cq uses the
    #     kernel-neutral `importers.importStep` fallback documented in
    #     `agentcad docs editing`).
    from agentcad.runners import dispatch
    project_rt = manifest.get("runtime") or dispatch.DEFAULT_RUNTIME
    scaffold_path = Path.cwd() / "edit.py"
    scaffold_written = False
    if not scaffold_path.exists():
        def _write_scaffold():
            scaffold_path.write_text(
                _edit_scaffold(dir_name, file_path.name, label, project_rt)
            )

        scaffold_written, _ = _attempt_artifact(
            "edit_scaffold", _write_scaffold
        )
        if scaffold_written:
            lifecycle.meta["scaffold"] = "edit.py"
            lifecycle.persist()
    else:
        lifecycle.set_artifact(
            "edit_scaffold", "skipped", message="edit.py already exists."
        )

    lifecycle.finish_pending(message="Optional artifact was not needed.")

    # 11. Output JSON. Mirrors committed metadata plus next_actions per the
    #     `next_actions` design convention.
    response = lifecycle.response()
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
