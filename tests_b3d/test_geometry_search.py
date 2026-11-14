import base64
import hashlib
import json
from pathlib import Path
import shlex

import pytest

from agentcad.cli import cli
from agentcad.geometry_manifest import create_manifest, load_geometry, read_manifest
from agentcad.geometry_query import parse_query
from agentcad.geometry_search_index import evaluate
from agentcad.step_assembly import read_step_assembly, write_step_assembly

FLAG = "--experimental-geometry-search"


def baseline(root, shape=None, source=None):
    root.mkdir(parents=True, exist_ok=True)
    if source is None:
        from build123d import Box, export_step
        source = root / "source.step"
        export_step(shape or Box(10, 20, 5), source)
    path = root / "output.step"
    write_step_assembly(read_step_assembly(source), path)
    create_manifest(path, 1, source={"assembly_metadata": "preserved_source_document"})
    return path


@pytest.fixture
def box(isolated_dir):
    return baseline(isolated_dir / "v1_box")


def invoke(runner, step, query=None, *extra, code=0):
    args = ["inspect", str(step), FLAG, "--no-daemon", *extra]
    if query is not None:
        args.extend(["--query", json.dumps(query), "--search-images", "0"])
    result = runner.invoke(cli, args)
    assert result.exit_code == code, (result.stdout, result.exception)
    return json.loads(result.stdout)


def raw(step, query):
    return evaluate(step, read_manifest(step), parse_query(query))


def test_box_exact_sets_units_orientation_and_boolean_filters(box, runner):
    query = {"kind": "face", "where": {"all": [
        {"surface_type": "plane"}, {"area": {"value": 2, "units": "cm2", "tolerance": .00001}},
        {"orientation": {"direction": [0, 0, 1]}}
    ]}}
    response = invoke(runner, box, query)
    assert response["match_count"] == 2
    assert {r["location"][2] for r in response["results"]} == {-2.5, 2.5}
    assert response["ranking_mode"] == "not_applicable" and response["ranking_complete"] is None
    assert response["comparison_counts"] is None and response["shape_comparison_calls"] == 0
    assert response["recognition"]["recognized_feature_count"] is None
    assert response["filter_coverage"]["status"] == "complete"
    results, coverage, _, _ = raw(box, {"kind": "face", "where": {"any": [
        {"area": {"value": 50, "units": "mm2"}}, {"area": {"value": 100, "units": "mm2"}}
    ], "not": {"area": {"value": 200, "units": "mm2"}}}})
    assert len(results) == 4 and coverage["failed"] == 0


def test_all_pages_and_mcp_manifest_chunks_are_equivalent(box, runner, isolated_dir, monkeypatch):
    from agentcad.mcp.server import inspect as mcp_inspect
    before = {p: p.read_bytes() for p in (box, box.with_name("geometry_manifest.json"))}
    first = invoke(runner, box, {"kind": "edge"})
    expected = sorted(i for i, e in read_manifest(box)["entities"].items() if e["kind"] == "edge")
    assert first["match_count"] == 12 and first["page"]["has_more"]
    ids, page = [], first
    monkeypatch.setattr("agentcad.geometry_search_index.evaluate", lambda *a: pytest.fail("Re-evaluated retained query"))
    monkeypatch.setattr("agentcad.geometry_manifest.BaselineGeometry", lambda *a: pytest.fail("Loaded CAD on replay"))
    while True:
        ids.extend(r["id"] for r in page["results"])
        if not page["page"]["next"]:
            break
        action = page["page"]["next"]
        result = runner.invoke(cli, [*shlex.split(action["cli"])[1:], "--no-daemon"])
        assert result.exit_code == 0, result.stdout
        page = json.loads(result.stdout)
        mcp = mcp_inspect(**action["mcp"]["arguments"])
        mcp = json.loads(mcp.content[0].text)
        assert mcp.pop("_exit_code") == 0
        assert mcp == page
    assert ids == expected and len(set(ids)) == 12
    action = first["candidate_manifest"]["retrieve"]["mcp"]["arguments"]
    chunks = []
    while True:
        artifact = mcp_inspect(**action)
        assert artifact["_exit_code"] == 0, artifact
        chunks.append(base64.b64decode(artifact["artifact"]["data"]))
        if artifact["next"] is None:
            break
        action = artifact["next"]["mcp"]["arguments"]
        assert action["experimental_geometry_search"] is True
        assert action["artifact_sha256"] and action["artifact_expires_at"]
    raw_bytes = b"".join(chunks)
    assert hashlib.sha256(raw_bytes).hexdigest() == first["candidate_manifest"]["sha256"]
    assert [r["id"] for r in json.loads(raw_bytes)["results"]] == expected
    assert {p: p.read_bytes() for p in before} == before


def test_region_planes_adjacency_and_minimum_distance(box):
    entries = read_manifest(box)["entities"]
    top = next(i for i, e in entries.items() if e["kind"] == "face" and e["normal"] == {"x": 0, "y": 0, "z": 1})
    results, cov, _, _ = raw(box, {"kind": "face", "where": {"relation": {"type": "adjacent", "to": top}}})
    assert len(results) == 4 and cov["failed"] == 0
    results, _, _, _ = raw(box, {"kind": "face", "where": {"relation": {"type": "distance", "to": top,
        "distance": {"value": 5, "units": "mm", "tolerance": .0001}}}})
    assert len(results) == 1 and results[0]["location"][2] == -2.5
    for relation, count in (("coplanar", 1), ("intersecting", 5)):
        results, cov, failures, _ = raw(box, {"kind": "face", "where": {"plane": {
            "origin": [0, 0, 2.5], "normal": [0, 0, 1], "units": "mm", "tolerance": .0001, "relation": relation}}})
        assert cov["failed"] == 0, failures
        assert len(results) == count
    results, _, _, _ = raw(box, {"kind": "face", "where": {"bounds": {
        "min": [-5, -10, 2.499], "max": [5, 10, 2.501], "units": "mm", "relation": "contained"}}})
    assert [e["id"] for e in results] == [top]


def test_analytic_types_radius_and_coaxial_query(isolated_dir):
    from build123d import Cylinder
    step = baseline(isolated_dir / "cylinder", Cylinder(4, 10))
    results, coverage, failures, _ = raw(step, {"kind": "face", "where": {"surface_type": "cylinder",
        "radius": {"value": 4, "units": "mm", "tolerance": .0001},
        "axis": {"origin": [0, 0, 50], "direction": [0, 0, -1], "units": "mm", "tolerance": .0001}}})
    assert coverage["failed"] == 0, failures
    assert len(results) == 1
    edges, cov, failures, _ = raw(step, {"kind": "edge", "where": {"curve_type": "circle",
        "relation": {"type": "coaxial", "to": results[0]["id"], "units": "mm", "tolerance": .0001}}})
    assert cov["failed"] == 0, failures
    assert len(edges) == 2


def test_rotated_repeated_instances_filter_before_grouping(runner, isolated_dir):
    from tests_b3d.test_step_assembly import _nested_step
    step = baseline(isolated_dir / "nested", source=_nested_step(isolated_dir / "nested-source.step", tilted=True))
    all_parts = invoke(runner, step, {"kind": "part"})
    assert all_parts["match_count"] == 2
    right = next(r for r in all_parts["results"] if r["location"][1] > 80)
    selected = invoke(runner, step, {"kind": "part", "where": {"placement": {
        "origin": [100, 90, 0], "units": "mm", "tolerance": .001}}})
    assert [r["id"] for r in selected["results"]] == [right["id"]]
    grouped = invoke(runner, step, {"kind": "part", "result_mode": "definitions", "where": {"placement": {
        "origin": [100, 90, 0], "units": "mm", "tolerance": .001}}})
    assert grouped["match_count"] == 1
    assert grouped["results"][0]["occurrence_ids"] == [right["id"]]
    assert grouped["results"][0]["distinct_occurrence_count"] == 1
    assert grouped["results"][0]["member_counts"] == {"solid": 1, "face": 6, "edge": 12}
    context = load_geometry(step)
    faces, _, _, _ = raw(step, {"kind": "face", "where": {"occurrence_id": right["id"],
        "orientation": {"direction": [1, 0, 0], "sense": "same"}}})
    assert len(faces) == 1
    assert context.pick_face(faces[0]["id"]).normal_at().X == pytest.approx(1)
    edges, _, _, _ = raw(step, {"kind": "edge", "where": {"occurrence_id": right["id"]}})
    before = context.shape
    edited = before.fillet(.25, [context.pick_edge(edges[0]["id"])])
    assert edited.is_valid and edited.volume < before.volume
    unchanged_before = min(before.solids(), key=lambda solid: solid.center().Y)
    unchanged_after = min(edited.solids(), key=lambda solid: solid.center().Y)
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from agentcad.topo_ids import _solid_volume
    for left, other in ((unchanged_before, unchanged_after), (unchanged_after, unchanged_before)):
        assert _solid_volume(BRepAlgoAPI_Cut(left.wrapped, other.wrapped).Shape()) == pytest.approx(0, abs=1e-6)


def test_zero_matches_unknown_ids_and_invalid_filters(box, runner):
    zero = invoke(runner, box, {"kind": "face", "where": {"area": {"min": 1e8, "units": "mm2"}}})
    assert zero["match_count"] == 0 and zero["match_count_complete"] is True
    assert zero["results"] == [] and zero["suggestions"]
    bad = invoke(runner, box, {"kind": "face", "where": {"occurrence_id": "occurrence_v1_99999"}}, code=1)
    assert "Unknown geometry ID" in bad["message"]
    bad = invoke(runner, box, {"kind": "face", "where": {"surface_type": "hole"}}, code=1)
    assert bad["reason"] == "invalid_geometry_query"


def test_expiry_eviction_corruption_and_wrong_baseline(box, runner, isolated_dir, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("agentcad.geometry_search_store._now", lambda: now[0])
    (isolated_dir / "agentcad.toml").write_text("[inspection.search]\nmax_age_seconds = 2\n")
    first = invoke(runner, box, {"kind": "face"})
    snapshot = first["search_snapshot"]["id"]
    path = isolated_dir / ".agentcad/geometry_search" / (snapshot + ".json")
    original = path.read_bytes()
    # Keep enough for one manifest but not two; a fresh query evicts the oldest.
    (isolated_dir / "agentcad.toml").write_text(f"[inspection.search]\nmax_age_seconds = 2\nmax_bytes = {len(original) + 1000}\n")
    now[0] += .1
    second = invoke(runner, box, {"kind": "face"})
    assert not path.exists()
    missing = invoke(runner, box, None, "--search-snapshot", snapshot, code=1)
    assert missing["reason"] == "search_snapshot_unavailable"
    snapshot2 = second["search_snapshot"]["id"]
    now[0] += 3
    expired = invoke(runner, box, None, "--search-snapshot", snapshot2, code=1)
    assert expired["reason"] == "search_snapshot_expired"
    assert expired["restart"]["mcp"]["arguments"]["query"]["kind"] == "face"
    assert expired["restart"]["mcp"]["arguments"]["experimental_geometry_search"] is True
    (isolated_dir / "agentcad.toml").write_text("[inspection.search]\nmax_bytes = 1000000\n")
    third = invoke(runner, box, {"kind": "face"})
    other = baseline(isolated_dir / "other")
    wrong = invoke(runner, other, None, "--search-snapshot", third["search_snapshot"]["id"], code=1)
    assert "another baseline" in wrong["message"]
    path = isolated_dir / ".agentcad/geometry_search" / (third["search_snapshot"]["id"] + ".json")
    data = json.loads(path.read_bytes())
    data["results"][0]["id"] = "face_v1_99999"
    path.write_text(json.dumps(data))
    corrupt = invoke(runner, box, None, "--search-snapshot", third["search_snapshot"]["id"], code=1)
    assert "integrity" in corrupt["message"]


def test_text_budget_and_partial_filter_coverage(box, runner, monkeypatch):
    from agentcad.geometry_search_index import ExactIndex
    original = ExactIndex.matches
    def failed(self, object_id, where):
        if object_id == "face_v1_1":
            raise ValueError("injected geometry failure")
        return original(self, object_id, where)
    monkeypatch.setattr(ExactIndex, "matches", failed)
    response = invoke(runner, box, {"kind": "face"})
    assert response["match_count_complete"] is False
    assert response["filter_coverage"]["failed"] == 1
    assert response["filter_coverage"]["evaluated"] == 5
    assert len(response["results"]) <= 5
    from agentcad.inspection_delivery import estimate, RESERVE
    assert estimate({**response, "_exit_code": 0}) + RESERVE <= 2000


def test_real_manifold_results_select_the_measured_target(isolated_dir):
    source = Path(__file__).parents[1] / "tests/fixtures/real_world/pump_manifold.step"
    step = baseline(isolated_dir / "manifold", source=source)
    results, coverage, failures, _ = raw(step, {"kind": "face", "where": {"surface_type": "cylinder",
        "radius": {"min": 1, "max": 10, "units": "mm"}}})
    assert coverage["status"] == "complete", failures
    assert results
    context = load_geometry(step)
    for entry in results:
        assert context.pick_face(entry["id"]).area == pytest.approx(entry["area"], abs=.001)


def test_local_mcp_images_and_replay_never_regenerate(box, runner, isolated_dir, monkeypatch):
    from agentcad.mcp.server import inspect as mcp_inspect
    from mcp.types import CallToolResult
    query = {"kind": "face", "where": {"area": {"value": 200, "units": "mm2"}}}
    response = mcp_inspect(file=str(box), cwd=str(isolated_dir), experimental_geometry_search=True,
                          query=query, full_response=True)
    assert isinstance(response, CallToolResult)
    data = json.loads(response.content[0].text)
    assert data["status"] == "success" and data["match_count"] == 2
    assert len(data["media"]) == 2 and len(response.content) == 3
    for item, content in zip(data["media"], response.content[1:]):
        png = base64.b64decode(content.data)
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(png).hexdigest() == item["sha256"]
    def forbidden(*a, **k):
        pytest.fail("Retained search regenerated geometry/image evidence")
    monkeypatch.setattr("agentcad.geometry_images.geometry_image", forbidden)
    monkeypatch.setattr("agentcad.geometry_manifest.BaselineGeometry", forbidden)
    snapshot = data["search_snapshot"]["id"]
    replay = mcp_inspect(file=str(box), cwd=str(isolated_dir), experimental_geometry_search=True,
                         search_snapshot=snapshot)
    assert isinstance(replay, CallToolResult) and len(replay.content) == 3
    Path(data["media"][0]["png"]).unlink()
    stale = mcp_inspect(file=str(box), cwd=str(isolated_dir), experimental_geometry_search=True,
                       search_snapshot=snapshot)
    stale_data = json.loads(stale.content[0].text)
    assert stale_data["status"] == "success"
    assert stale_data["image_failures"][0]["status"] == "unavailable"
    assert len(stale_data["media"]) == 1


def test_mcp_image_free_text_uses_the_budgeted_serialization(box, isolated_dir):
    import math
    from agentcad.mcp.server import inspect as mcp_inspect
    from mcp.types import CallToolResult
    response = mcp_inspect(file=str(box), cwd=str(isolated_dir), experimental_geometry_search=True,
                          query={"kind": "edge"}, search_images=0)
    assert isinstance(response, CallToolResult)
    assert len(response.content) == 1
    data = json.loads(response.content[0].text)
    assert response.content[0].text == json.dumps(data)
    assert math.ceil(len(response.content[0].text.encode("utf-8")) / 3) + 32 <= 2000


def test_image_failure_preserves_search_matches_and_vision_limits(box, runner, monkeypatch):
    monkeypatch.setattr("agentcad.geometry_images.geometry_image", lambda *a, **k: (_ for _ in ()).throw(ValueError("renderer unavailable")))
    query = {"kind": "face", "where": {"area": {"value": 200, "units": "mm2"}}}
    result = runner.invoke(cli, ["inspect", str(box), FLAG, "--query", json.dumps(query), "--no-daemon"])
    assert result.exit_code == 0, result.stdout
    response = json.loads(result.stdout)
    assert response["match_count"] == 2 and response["image_failures"]
    constrained = runner.invoke(cli, ["inspect", str(box), FLAG, "--query", json.dumps(query),
        "--response-budget", "2000", "--no-daemon"])
    assert constrained.exit_code == 0, constrained.stdout
    response = json.loads(constrained.stdout)
    assert response["media"] == [] and response["image_failures"] == []
    assert response["delivery"]["image_omission_reason"] == "vision_profile_required"


def test_disconnected_shape_does_not_intersect_plane_merely_by_box(isolated_dir):
    from build123d import Box, Compound
    shape = Compound(children=[Box(2, 2, 2).translate((0, 0, -5)), Box(2, 2, 2).translate((0, 0, 5))])
    step = baseline(isolated_dir / "compound", shape)
    results, coverage, failures, _ = raw(step, {"kind": "part", "where": {"plane": {
        "origin": [0, 0, 0], "normal": [0, 0, 1], "units": "mm", "tolerance": .001, "relation": "intersecting"}}})
    assert coverage["status"] == "complete", failures
    assert not results


def test_plane_tolerance_does_not_round_away_a_real_gap(box):
    results, coverage, failures, _ = raw(box, {"kind": "face", "where": {"plane": {
        "origin": [0, 0, 2.50004], "normal": [0, 0, 1], "units": "mm", "tolerance": .000001, "relation": "coplanar"}}})
    assert coverage["status"] == "complete", failures
    assert not results


def test_orientation_uses_full_precision_on_oblique_planes(isolated_dir):
    import math
    from build123d import Axis, Box
    step = baseline(isolated_dir / "oblique", Box(10, 20, 5).rotate(axis=Axis.Z, angle=30))
    results, coverage, failures, _ = raw(step, {"kind": "face", "where": {"orientation": {
        "direction": [math.cos(math.pi / 6), math.sin(math.pi / 6), 0], "sense": "same", "angle_tolerance_deg": .000001}}})
    assert coverage["status"] == "complete", failures
    assert len(results) == 1


def test_saved_pages_are_stable_after_new_queries_and_settings(box, runner, isolated_dir):
    first = invoke(runner, box, {"kind": "edge"}, "--page-size", "2")
    expected = first["results"]
    invoke(runner, box, {"kind": "edge", "where": {"length": {"min": 15, "units": "mm"}}})
    (isolated_dir / "agentcad.toml").write_text("[inspection.search]\nmax_bytes = 1000000\n")
    replay = invoke(runner, box, None, "--search-snapshot", first["search_snapshot"]["id"])
    assert replay["results"] == expected
    assert replay["page"]["next"] == first["page"]["next"]
    one = invoke(runner, box, None, "--search-snapshot", first["search_snapshot"]["id"],
                 "--search-id", expected[0]["id"])
    assert one["results"] == [expected[0]] and not one["page"]["has_more"]


def test_search_manifest_rejects_unpinned_changed_expiry_and_wrong_hash(box, runner, isolated_dir):
    from agentcad.mcp.server import inspect as mcp_inspect
    response = invoke(runner, box, {"kind": "edge"})
    args = response["candidate_manifest"]["retrieve"]["mcp"]["arguments"]
    first = mcp_inspect(**args, artifact_bytes=128)
    continuation = first["next"]["mcp"]["arguments"]
    bad = mcp_inspect(**{**continuation, "artifact_sha256": "a" * 64})
    assert bad["_exit_code"] == 1 and "hash" in bad["message"]
    bad = mcp_inspect(**{**continuation, "artifact_expires_at": None})
    assert bad["_exit_code"] == 1 and "artifact_expires_at" in bad["message"]
    (isolated_dir / "agentcad.toml").write_text("[inspection.search]\nmax_age_seconds = 1\n")
    bad = mcp_inspect(**continuation)
    assert bad["_exit_code"] == 1 and "expiry" in bad["message"]


def test_oversized_snapshot_does_not_evict_existing_evidence(box, runner, isolated_dir):
    first = invoke(runner, box, {"kind": "face", "where": {"id": "face_v1_1"}})
    path = isolated_dir / ".agentcad/geometry_search" / (first["search_snapshot"]["id"] + ".json")
    original = path.read_bytes()
    (isolated_dir / "agentcad.toml").write_text(f"[inspection.search]\nmax_bytes = {len(original) + 10}\n")
    too_big = invoke(runner, box, {"kind": "edge"}, code=1)
    assert too_big["reason"] == "search_resource_limit"
    assert path.read_bytes() == original
