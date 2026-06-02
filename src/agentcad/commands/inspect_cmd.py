import json
import sys
from pathlib import Path

import click

from agentcad import file_detect
from agentcad.commands._daemon_routing import (
    maybe_route_through_daemon,
    maybe_spawn_daemon_for_next_run,
)
from agentcad.native_io import silence_native_stdout


@click.command("inspect")
@click.argument("file")
@click.option(
    "--ids", "with_ids", is_flag=True,
    help="Include per-feature ID lists (solids, faces, edges) for use with "
         "pick_face / pick_edge in edit scripts. IDs are 1-indexed and "
         "stable within a shape but not across edits.",
)
@click.option(
    "--summary", "with_summary", is_flag=True,
    help="Cluster faces and edges into semantic groups (planar by axis, "
         "cylindrical by axis+radius, edges by curve type). Compact "
         "alternative to --ids for parts where the per-feature payload "
         "would blow the context budget. Combine with --ids for both.",
)
@click.option("--no-daemon", is_flag=True, default=False, help="Skip daemon routing for this run, even if a daemon is running. Useful for debugging.")
def inspect_cmd(file, with_ids, with_summary, no_daemon):
    """Inspect any file. STEP/BREP get a full topology report; other formats
    get a structured 'recognized but not editable here' response. Never throws."""
    # Try routing through daemon. Exits before returning if reachable.
    argv = ["inspect", file]
    if with_ids:
        argv.append("--ids")
    if with_summary:
        argv.append("--summary")
    maybe_route_through_daemon(argv, no_daemon=no_daemon)

    file_path = Path(file)
    detection = file_detect.detect_file_type(file_path)
    category = detection["category"]

    if category == file_detect.MISSING:
        _emit({
            "command": "inspect", "status": "error",
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
            "command": "inspect", "status": "error",
            "format_detected": None,
            "message": f"Path '{file}' is not a regular file ({reason}).",
            "suggestion": "Pass a path to a real CAD file.",
        }, exit_code=1)
        return

    if category == file_detect.EMPTY:
        _emit({
            "command": "inspect", "status": "empty",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": 0,
            "message": f"File '{file}' is empty.",
            "suggestion": "Re-export from your CAD tool; the file may be a failed write.",
        }, exit_code=1)
        return

    if category == file_detect.MALFORMED:
        _emit_malformed(file, detection)
        return

    if category == file_detect.UNKNOWN_FORMAT:
        _emit({
            "command": "inspect", "status": "unknown_format",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"agentcad doesn't recognize extension '{detection.get('extension')}'."
            ),
            "suggestion": (
                "Supported formats: .step .stp .brep (full edit), .stl (inspect-only). "
                "If this is a CAD file, re-export as STEP."
            ),
        }, exit_code=0)
        return

    if category == file_detect.DISPLAY_FORMAT:
        fmt = detection["format"]
        _emit({
            "command": "inspect", "status": "display_format",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"{fmt.upper()} is an export/display format, not a source CAD format."
            ),
            "suggestion": (
                "Ask the original tool to export STEP — that's the format agentcad edits."
            ),
        }, exit_code=0)
        return

    if category == file_detect.TIER2_RECOGNIZED:
        fmt = detection["format"]
        _emit({
            "command": "inspect", "status": "recognized_deferred",
            "format_detected": fmt,
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"Format '{fmt}' is recognized but not in agentcad's v0 edit scope."
            ),
            "suggestion": (
                "Convert to STEP (lossless for B-rep formats) or run "
                "`agentcad feedback` if support would unblock you."
            ),
        }, exit_code=0)
        return

    if category == file_detect.TIER1_MESH:
        _emit({
            "command": "inspect", "status": "limited",
            "format_detected": detection["format"],
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "editable": False,
            "message": (
                "STL is a triangle mesh — agentcad's edit pipeline is B-rep. "
                "Faces and edges in a mesh are facets, not semantic features, so "
                "fillet, shell, and face-extrude don't apply."
            ),
            "suggestion": (
                "Recreate parametrically (run `agentcad docs patterns` for examples), "
                "or ask the original tool to export STEP."
            ),
        }, exit_code=0)
        return

    if category == file_detect.TIER0_BREP:
        _inspect_tier0(str(file_path.resolve()), detection, with_ids=with_ids, with_summary=with_summary)
        # Fork off the warm process as the daemon — only the Tier 0 path
        # paid the OCP cost worth keeping around. Idempotent on its own.
        maybe_spawn_daemon_for_next_run(no_daemon=no_daemon)
        return

    # Defensive: no other categories should reach here.
    _emit({
        "command": "inspect", "status": "error",
        "format_detected": None,
        "message": f"Unhandled file category '{category}'.",
    }, exit_code=1)


def _emit(payload: dict, exit_code: int = 0) -> None:
    click.echo(json.dumps(payload))
    if exit_code != 0:
        sys.exit(exit_code)


def _emit_malformed(file: str, detection: dict) -> None:
    expected = detection.get("expected_format")
    actual = detection.get("format")
    if actual == "html":
        message = (
            f"Extension is .{expected} but content is HTML — likely a failed download "
            "(error page saved instead of the file)."
        )
        suggestion = "Re-download the file; verify the source URL returns the CAD file, not HTML."
    else:
        message = (
            f"Extension claims '{expected}' but content looks like '{actual}'. "
            "File appears mislabeled or corrupted."
        )
        suggestion = f"Re-export as {expected} from your CAD tool, or rename to match the real format."
    _emit({
        "command": "inspect", "status": "malformed",
        "format_detected": actual,
        "expected_format": expected,
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "message": message,
        "suggestion": suggestion,
    }, exit_code=1)


def _inspect_tier0(file_path: str, detection: dict, *, with_ids: bool = False, with_summary: bool = False) -> None:
    """Full topology report for STEP/BREP files. OCCT failures are caught and
    surfaced as malformed — never as a stack trace, never as a leaked native
    diagnostic on stdout."""
    try:
        # load_cad_shape (called inside _topology_report) handles silencing.
        payload, topo_shape = _topology_report(file_path, return_shape=True)
    except Exception as exc:
        _emit({
            "command": "inspect", "status": "malformed",
            "format_detected": detection.get("format"),
            "extension": detection.get("extension"),
            "size_bytes": detection.get("size_bytes"),
            "message": (
                f"Recognized as {detection.get('format')} but the parser couldn't "
                f"read it: {exc}. The file may be incomplete or corrupted."
            ),
            "suggestion": "Re-export from your CAD tool; the file may be incomplete or corrupted.",
        }, exit_code=1)
        return

    payload.update({
        "format_detected": detection.get("format"),
        "extension": detection.get("extension"),
        "size_bytes": detection.get("size_bytes"),
        "next_actions": [
            f"agentcad view {file_path} — open in a browser to inspect or share with humans",
            f"agentcad render {file_path} --view iso — produce a PNG snapshot for handoff",
        ],
        "more_at": "agentcad docs",
    })

    if with_ids or with_summary:
        from agentcad import topo_ids
        if with_ids:
            payload["solids"] = topo_ids.solid_entries(topo_shape)
            payload["faces"] = topo_ids.face_entries(topo_shape)
            payload["edges"] = topo_ids.edge_entries(topo_shape)
        if with_summary:
            payload["summary"] = topo_ids.summary_entries(topo_shape)
        # When the agent has IDs (or summary IDs), the most-likely next
        # action shifts: they're here because they want to write an edit
        # script that targets specific features. Point them at the
        # import → load_step → pick_face flow.
        payload["next_actions"] = [
            f"agentcad import {file_path} — adopt as v1 so you can edit and diff",
            "use pick_face(base, ID) / pick_edge(base, ID) in your edit script — "
            "see `agentcad docs editing`",
        ]

    notes = _compute_notes(payload)
    if with_ids or with_summary:
        # ID-stability caveat — load-bearing for the addressability flow.
        # Per the `notes` convention, fires whenever IDs are emitted (full
        # via --ids or per-cluster via --summary) because the same caveat
        # always applies.
        notes.append(
            "Feature IDs are 1-indexed and stable within this shape (OCP "
            "topology-traversal order) but not across edits — even a small "
            "boolean or fillet can renumber faces and edges. Re-run "
            "`inspect --ids` (or `--summary`) after each edit to get current IDs."
        )
    if notes:
        payload["notes"] = notes

    _emit(payload, exit_code=0)


def _compute_notes(payload: dict) -> list:
    """Contextual clarifications that fire only when a combination of fields
    might confuse an agent without domain knowledge. See `notes` convention
    in `design_conventions.md`."""
    notes = []
    # is_valid==True together with free_edge_count > 0 reads as contradictory
    # to an unfamiliar agent ("the shape has dangling edges *and* the tool
    # says it's valid"). Both can be correct: is_valid checks topological
    # consistency, not closure. Free edges may appear on intentionally open
    # shells (sheet metal, surfaces) or as artifacts of boolean/fillet ops
    # on closed solids — the note covers both cases without claiming either.
    # Asymmetric face_orientations (forward vs reversed) on a valid closed
    # solid looks alarming but isn't a defect — OCCT's importer happens to
    # orient faces however it likes during STEP read, and the resulting split
    # can be wildly skewed (e.g. forward=11, reversed=107 on the Pump Manifold
    # e2e test). is_valid + free_edge_count=0 confirm the topology is sound.
    # Add a note when the asymmetry shows up so agents don't chase a non-bug.
    orientations = payload.get("face_orientations", {})
    fwd = orientations.get("forward", 0)
    rev = orientations.get("reversed", 0)
    if (
        payload.get("is_valid")
        and payload.get("free_edge_count", 0) == 0
        and fwd != rev
        and (fwd + rev) > 0
    ):
        notes.append(
            "face_orientations asymmetry (forward vs reversed counts differ) on "
            "a closed valid solid is normal — it reflects how OCCT oriented "
            "faces during import, not a defect. is_valid: true with "
            "free_edge_count: 0 confirms the topology is sound."
        )

    if payload.get("is_valid") and payload.get("free_edge_count", 0) > 0:
        notes.append(
            "is_valid: true means the shape is topologically consistent — "
            "free_edge_count > 0 doesn't contradict that. Free edges can "
            "appear on intentionally open shells (sheet metal, surfaces) "
            "or as artifacts of boolean/fillet operations on closed solids."
        )
    return notes


def _topology_report(file_path: str, *, return_shape: bool = False):
    from agentcad.step_io import load_cad_shape
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.ShapeAnalysis import ShapeAnalysis_Shell
    from OCP.TopAbs import (
        TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED,
        TopAbs_SHELL, TopAbs_SOLID,
    )
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    shape = load_cad_shape(file_path)

    solid_count = 0
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solid_count += 1
        exp.Next()

    shells = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        shell = TopoDS.Shell_s(exp.Current())
        sa = ShapeAnalysis_Shell()
        sa.LoadShells(shell)
        has_free = sa.HasFreeEdges()
        shell_face_count = 0
        face_exp = TopExp_Explorer(shell, TopAbs_FACE)
        while face_exp.More():
            shell_face_count += 1
            face_exp.Next()
        shells.append({"closed": not has_free, "face_count": shell_face_count})
        exp.Next()

    face_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
    face_count = face_map.Extent()

    forward = reversed_count = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        orient = exp.Current().Orientation()
        if orient == TopAbs_FORWARD:
            forward += 1
        elif orient == TopAbs_REVERSED:
            reversed_count += 1
        exp.Next()

    edge_map = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edge_map)
    edge_count = edge_map.Extent()

    free_edge_count = 0
    for i in range(1, edge_count + 1):
        edge = edge_map.FindKey(i)
        face_ref_count = 0
        face_exp = TopExp_Explorer(shape, TopAbs_FACE)
        while face_exp.More():
            edge_exp = TopExp_Explorer(face_exp.Current(), TopAbs_EDGE)
            while edge_exp.More():
                if edge_exp.Current().IsSame(edge):
                    face_ref_count += 1
                    break
                edge_exp.Next()
            face_exp.Next()
        if face_ref_count < 2:
            free_edge_count += 1

    is_valid = BRepCheck_Analyzer(shape).IsValid()

    payload = {
        "command": "inspect",
        "status": "success",
        "file": file_path,
        "solid_count": solid_count,
        "shell_count": len(shells),
        "shells": shells,
        "face_count": face_count,
        "face_orientations": {"forward": forward, "reversed": reversed_count},
        "edge_count": edge_count,
        "free_edge_count": free_edge_count,
        "is_valid": is_valid,
    }
    if return_shape:
        return payload, shape
    return payload
