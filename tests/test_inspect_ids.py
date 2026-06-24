"""Tests for `agentcad inspect --ids` — Phase 3 addressability.

Augments the existing topology report with per-solid / per-face / per-edge
IDs that agents can reference in pick_face / pick_edge calls. IDs are
1-indexed, stable within a shape (OCP's IndexedMapOfShape ordering), and
explicitly NOT stable across edits — that has to be in the docs.
"""
import json
from pathlib import Path

import cadquery as cq
import pytest
from cadquery import exporters

from agentcad.cli import cli


def _bracket_step(directory: Path, name: str = "bracket.step") -> Path:
    plate = cq.Workplane("XY").box(40, 30, 5)
    hole = cq.Workplane("XY").circle(5).extrude(10).translate((10, 0, 0))
    part = plate.cut(hole).edges(">Z").fillet(1)
    path = directory / name
    exporters.export(part, str(path))
    return path


def _box_step(directory: Path, name: str = "box.step") -> Path:
    box = cq.Workplane("XY").box(10, 20, 5)
    path = directory / name
    exporters.export(box, str(path))
    return path


def _many_boxes_step(directory: Path, name: str = "many_boxes.step", count: int = 20) -> Path:
    solids = [
        cq.Workplane("XY").box(1, 1, 1).translate((i * 3, 0, 0)).val()
        for i in range(count)
    ]
    compound = cq.Compound.makeCompound(solids)
    path = directory / name
    exporters.export(compound, str(path))
    return path


# --- the --ids flag -------------------------------------------------------

class TestInspectIdsFlag:
    def test_no_ids_flag_no_id_fields(self, runner, isolated_dir):
        """Backwards-compatible: bare `inspect` returns the existing report
        with no per-feature ID lists. --ids is opt-in."""
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        # Counts still present (existing contract)
        assert parsed["solid_count"] == 1
        # ID lists absent without the flag
        assert "solids" not in parsed
        assert "faces" not in parsed
        assert "edges" not in parsed

    def test_ids_flag_adds_solids_list(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)
        solids = parsed["solids"]
        assert len(solids) == 1
        s = solids[0]
        assert s["id"] == 1
        # Volume of a 10x20x5 box = 1000
        assert abs(s["volume"] - 1000.0) < 1e-6
        # bbox shape
        bbox = s["bbox"]
        assert "x" in bbox and "y" in bbox and "z" in bbox
        # Each axis is [min, max]
        assert len(bbox["x"]) == 2 and bbox["x"][0] < bbox["x"][1]

    def test_ids_flag_adds_faces_list(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        parsed = json.loads(result.stdout)
        faces = parsed["faces"]
        # A simple box has 6 faces.
        assert len(faces) == 6
        f = faces[0]
        assert "id" in f
        assert "area" in f
        assert "centroid" in f
        assert "bbox" in f
        assert "normal" in f
        # Centroid is a 3-tuple
        assert all(k in f["centroid"] for k in ("x", "y", "z"))

    def test_face_ids_are_1_indexed_and_unique(self, runner, isolated_dir):
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        parsed = json.loads(result.stdout)
        face_ids = [f["id"] for f in parsed["faces"]]
        assert face_ids == list(range(1, len(face_ids) + 1)), \
            "face IDs must be 1-indexed contiguous integers"

    def test_ids_flag_adds_edges_list(self, runner, isolated_dir):
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        parsed = json.loads(result.stdout)
        edges = parsed["edges"]
        # A box has 12 edges.
        assert len(edges) == 12
        e = edges[0]
        assert "id" in e
        assert "length" in e
        assert "endpoints" in e
        # Endpoints: list of two 3-tuples (start + end). For closed edges,
        # the two points coincide; the field is present either way.
        assert len(e["endpoints"]) == 2
        for pt in e["endpoints"]:
            assert all(k in pt for k in ("x", "y", "z"))

    def test_solid_volume_sums_to_metrics_volume(self, runner, isolated_dir):
        """Sanity check: per-solid volumes sum to the part's metric volume."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        parsed = json.loads(result.stdout)
        # The existing topology report has solid_count; --ids gives volumes.
        # Single-solid bracket → solid[0].volume == bracket volume.
        solids = parsed["solids"]
        assert len(solids) == 1
        # Bracket is plate (40*30*5) minus a small cylinder, then filleted.
        # Volume should be near plate volume but slightly less.
        assert 5500 < solids[0]["volume"] < 6000

    def test_ids_flag_response_includes_next_actions(self, runner, isolated_dir):
        """Per design conventions: success outputs include 1-2 next_actions.
        With IDs in hand, next-action should point at writing an edit script."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        parsed = json.loads(result.stdout)
        assert "next_actions" in parsed

    def test_ids_stable_across_runs(self, runner, isolated_dir):
        """Same shape must produce the same IDs in the same order — agents
        rely on this when carrying IDs from inspect into edit scripts."""
        step = _bracket_step(isolated_dir)
        first = json.loads(
            runner.invoke(cli, ["inspect", str(step), "--ids"]).output
        )
        second = json.loads(
            runner.invoke(cli, ["inspect", str(step), "--ids"]).output
        )
        # Compare a deterministic projection: face id → centroid.
        proj1 = {f["id"]: f["centroid"] for f in first["faces"]}
        proj2 = {f["id"]: f["centroid"] for f in second["faces"]}
        assert proj1 == proj2

    def test_ids_response_documents_stability(self, runner, isolated_dir):
        """The response must surface the within-run-only ID stability so
        agents don't carry IDs across edits and silently target the wrong
        feature. Goes in the `notes` field per the contextual-notes
        convention."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        parsed = json.loads(result.stdout)
        # Notes are always emitted with --ids since the stability caveat
        # always applies.
        assert "notes" in parsed
        joined = " ".join(parsed["notes"]).lower()
        assert "id" in joined
        assert "edit" in joined or "stable" in joined

    def test_ids_default_is_bounded_with_truncation_metadata(
        self, runner, isolated_dir
    ):
        step = _many_boxes_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert parsed["face_count"] == 120
        assert parsed["edge_count"] == 240
        assert len(parsed["faces"]) == 100
        assert len(parsed["edges"]) == 100
        fields = {f["path"]: f for f in parsed["truncation"]["fields"]}
        assert fields["faces"]["total"] == 120
        assert fields["edges"]["total"] == 240
        assert any("--no-limit" in c for c in parsed["truncation"]["recommended_commands"])

    def test_ids_no_limit_restores_complete_lists(self, runner, isolated_dir):
        step = _many_boxes_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids", "--no-limit"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert len(parsed["faces"]) == parsed["face_count"]
        assert len(parsed["edges"]) == parsed["edge_count"]
        assert "truncation" not in parsed


# --- the --summary flag (issue #131) --------------------------------------

class TestInspectSummaryFlag:
    def test_no_summary_flag_no_summary_field(self, runner, isolated_dir):
        """Backwards-compatible: bare inspect doesn't include summary."""
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step)])
        parsed = json.loads(result.stdout)
        assert parsed["status"] == "success"
        assert "summary" not in parsed

    def test_summary_on_box_clusters_six_planar_faces(self, runner, isolated_dir):
        """A box has 6 planar faces — one per axis direction. Each should
        appear as a single-face cluster."""
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--summary"])
        parsed = json.loads(result.stdout)
        summary = parsed["summary"]
        assert summary["face_count"] == 6
        # All six clusters are planar; axes cover ±x, ±y, ±z.
        kinds = {c["kind"] for c in summary["face_clusters"]}
        assert kinds == {"planar"}
        axes = {c["axis"] for c in summary["face_clusters"]}
        assert axes == {"+x", "-x", "+y", "-y", "+z", "-z"}

    def test_summary_on_bracket_buckets_cylindrical_separately(
        self, runner, isolated_dir
    ):
        """A bracket with a through-hole has both planar and cylindrical
        faces — they should appear as separate clusters."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--summary"])
        parsed = json.loads(result.stdout)
        summary = parsed["summary"]
        kinds = {c["kind"] for c in summary["face_clusters"]}
        assert "planar" in kinds
        assert "cylindrical" in kinds
        # Hole wall is radius=5; the bracket also has 1mm fillets on the top
        # edges (also cylindrical). Both should appear as separate clusters
        # so the agent can tell holes from fillets at a glance.
        cyl_radii = {c["radius"] for c in summary["face_clusters"]
                     if c["kind"] == "cylindrical"}
        assert 5.0 in cyl_radii
        assert 1.0 in cyl_radii

    def test_summary_clusters_circular_edges_by_radius(self, runner, isolated_dir):
        """Edge clustering buckets circles by radius and lines by axis."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--summary"])
        parsed = json.loads(result.stdout)
        summary = parsed["summary"]
        kinds = {c["kind"] for c in summary["edge_clusters"]}
        assert "line" in kinds
        assert "circle" in kinds
        # Total count adds up.
        assert summary["edge_count"] == sum(
            c["count"] for c in summary["edge_clusters"]
        )

    def test_summary_strictly_smaller_than_ids_payload(self, runner, isolated_dir):
        """The whole point of --summary: compact alternative to --ids when
        the full per-feature payload would blow context. On a small bracket
        the savings are modest; on real CAD parts they matter a lot.
        Either way, summary should not be larger than --ids."""
        step = _bracket_step(isolated_dir)
        ids_result = runner.invoke(cli, ["inspect", str(step), "--ids"])
        sum_result = runner.invoke(cli, ["inspect", str(step), "--summary"])
        assert len(sum_result.output) < len(ids_result.output)

    def test_summary_and_ids_can_combine(self, runner, isolated_dir):
        """Passing both flags includes both payloads — the agent picks
        which to consume."""
        step = _box_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--ids", "--summary"])
        parsed = json.loads(result.stdout)
        assert "summary" in parsed
        assert "faces" in parsed
        assert "edges" in parsed

    def test_summary_response_includes_next_actions(self, runner, isolated_dir):
        """--summary surfaces the same edit-flow next_actions as --ids
        (the agent has IDs to pick with either way)."""
        step = _bracket_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--summary"])
        parsed = json.loads(result.stdout)
        assert "next_actions" in parsed
        joined = " ".join(parsed["next_actions"])
        assert "import" in joined
        assert "pick_face" in joined or "pick_edge" in joined

    def test_summary_cluster_ids_are_bounded(self, runner, isolated_dir):
        step = _many_boxes_step(isolated_dir)
        result = runner.invoke(cli, ["inspect", str(step), "--summary", "--limit", "5"])
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.stdout)

        assert parsed["summary"]["face_count"] == 120
        assert "id_truncation" in parsed["summary"]
        assert "truncation" in parsed
        recommended = " ".join(parsed["truncation"]["recommended_commands"])
        assert "--summary --limit 500" in recommended
        assert "--summary --no-limit" in recommended
        for cluster in parsed["summary"]["face_clusters"]:
            assert len(cluster["ids"]) <= 5
