"""Issued IDs agree across real pixels, records, instances and edit selectors."""

import base64
import json
from pathlib import Path

import pytest
from PIL import Image

from agentcad.cli import cli
from agentcad.geometry_images import _anchor, geometry_image
from agentcad.geometry_manifest import create_manifest, load_geometry
from agentcad.step_assembly import read_step_assembly, write_step_assembly
from tests_b3d.test_step_assembly import _nested_step


def _baseline(root, source=None):
    source = source or _nested_step(root / "source.step", tilted=True)
    path = root / "output.step"
    write_step_assembly(read_step_assembly(source), path)
    saved = create_manifest(path, 1, source={"assembly_metadata": "preserved_source_document"})
    return path, saved


def _json(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _assert_sheet(result):
    with Image.open(result["png"]) as image:
        assert image.size == (1024, 1024)
    metadata = json.loads(Path(result["metadata"]).read_text(encoding="utf-8"))
    assert {p["view"] for p in metadata["projections"]} == {"iso", "top", "front", "right"}
    assert all(0 <= p["pixel"][0] < 1024 and 0 <= p["pixel"][1] < 1024 for p in metadata["projections"])
    assert "projections" not in result  # Full camera evidence stays in the artifact.
    return metadata


def test_import_prepares_bounded_overall_sheet_with_portable_build_paths(runner, isolated_dir):
    (isolated_dir / "agentcad.toml").write_text('build_dir = "artifacts with spaces"\n', encoding="utf-8")
    source = _nested_step(isolated_dir / "supplier.step", tilted=True)
    imported = _json(runner, ["import", str(source), "--init", "--label", "baseline", "--no-view", "--no-diff"])
    assert imported["artifacts"]["inspection_image"]["status"] == "success"
    sheet = imported["inspection_image"]
    assert Path(sheet["png"]).is_absolute()
    assert Path(sheet["metadata"]).is_absolute()
    assert 0 < len(sheet["labeled_ids"]) <= 8
    assert not sheet["labels_complete"]
    stored = json.loads((isolated_dir / "artifacts with spaces" / "v1_baseline" / "meta.json").read_text())
    assert not Path(stored["inspection_image"]["png"]).is_absolute()
    viewed = _json(runner, ["inspect", imported["outputs"]["step"], "--image"])
    assert viewed["image"]["png"] == sheet["png"]
    assert viewed["image"]["frame"]["dimensions"] == imported["metrics"]["dimensions"]
    _assert_sheet(viewed["image"])


def test_unsampled_edge_image_cli_mcp_and_selector_agree(runner, isolated_dir):
    from agentcad.mcp.server import inspect as mcp_inspect, mcp
    from mcp.types import CallToolResult
    import asyncio

    step, saved = _baseline(isolated_dir)
    context = load_geometry(step)
    right = next(e for e in saved["entities"].values() if e.get("name") == "right instance")
    edge = right["members"]["edge"][-1]
    actual = _json(runner, ["inspect", str(step), "--id", edge, "--image"])
    assert actual["entity"] == context.record(edge)
    sheet = actual["image"]
    assert sheet["annotations"][0]["id"] == edge
    point = sheet["annotations"][0]["point"]
    assert 90 <= point[1] <= 100
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    distance = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(gp_Pnt(*point)).Vertex(), context.pick_edge(edge).wrapped)
    assert distance.Value() < 1e-6
    metadata = _assert_sheet(sheet)
    assert {p["id"] for p in metadata["projections"]} == {edge}
    response = mcp_inspect(file=str(step), cwd=str(isolated_dir), entity_id=edge, image=True)
    assert isinstance(response, CallToolResult)
    data = response.structuredContent
    assert data.pop("_exit_code") == 0
    assert data == actual
    assert base64.b64decode(response.content[1].data) == Path(sheet["png"]).read_bytes()
    # Exercise FastMCP's registered transport conversion, not only the Python function.
    converted = asyncio.run(mcp.call_tool("inspect", {"file": str(step), "cwd": str(isolated_dir), "entity_id": edge, "image": True}))
    assert isinstance(converted, CallToolResult)
    assert [c.type for c in converted.content] == ["text", "image"]


def test_occurrence_and_part_targets_keep_their_distinct_scope(isolated_dir):
    step, saved = _baseline(isolated_dir)
    context = load_geometry(step)
    right = next(e for e in saved["entities"].values() if e.get("name") == "right instance")
    occurrence = geometry_image(step, isolated_dir, object_id=right["id"], isolate=True)
    assert occurrence["target"]["dimensions"] == {"x": 5.0, "y": 10.0, "z": 20.0}
    assert occurrence["frame"] == occurrence["target"]
    definition = geometry_image(step, isolated_dir, object_id=right["part_id"], isolate=True)
    assert definition["target"] == definition["model"]
    assert definition["part_scope"] == "all placed occurrences"
    from agentcad.topo_ids import topology_counts

    assert topology_counts(context.image_shape(right["id"]))["solids"] == 1
    assert topology_counts(context.image_shape(right["part_id"]))["solids"] == 2
    _assert_sheet(occurrence)
    _assert_sheet(definition)


@pytest.mark.parametrize("name", ["cadgenbench_101_agentcad.step", "pump_manifold.step"])
def test_real_import_face_highlight_and_isolation(runner, isolated_dir, name):
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "real_world" / name
    step, saved = _baseline(isolated_dir, fixture)
    face = next(e for e in reversed(list(saved["entities"].values())) if e["kind"] == "face")
    highlighted = _json(runner, ["inspect", str(step), "--id", face["id"], "--image"])
    isolated = _json(runner, ["inspect", str(step), "--id", face["id"], "--image", "--isolate"])
    assert isolated["image"]["target"]["bbox"] == face["bbox"]
    assert highlighted["image"]["frame"] == highlighted["image"]["model"]
    _assert_sheet(highlighted["image"])
    _assert_sheet(isolated["image"])


def test_face_anchor_avoids_the_hole_in_an_annular_face():
    from build123d import Cylinder, Axis

    ring = Cylinder(10, 4) - Cylinder(5, 4)
    face = ring.faces().sort_by(Axis.Z)[-1]
    point = _anchor(face.wrapped)
    assert 5 - 1e-6 <= (point[0] ** 2 + point[1] ** 2) ** 0.5 <= 10 + 1e-6
    assert point[2] == pytest.approx(2)


def test_depth_prepass_preserves_visible_geometry(isolated_dir):
    from PIL import ImageChops
    from agentcad.render import _setup_render, _capture, VIEWS

    step, _ = _baseline(isolated_dir)
    context = load_geometry(step)
    paths = []
    for enabled in (False, True):
        view, display = _setup_render(
            context.shape.wrapped, 256, 256, show_edges=True,
            highlight_shape=context.image_shape("edge_v1_24"), depth_prepass=enabled,
        )
        path = isolated_dir / f"depth_{enabled}.png"
        try:
            view.SetProj(VIEWS["right"])
            view.FitAll(0.18)
            view.Redraw()
            _capture(view, path, 256, 256)
        finally:
            display.RemoveAll(False)
            view.Remove()
        del view, display
        paths.append(path)
    with Image.open(paths[0]) as original, Image.open(paths[1]) as optimized:
        assert ImageChops.difference(original.convert("RGB"), optimized.convert("RGB")).getbbox() is None


def test_cached_image_reuses_pixels_but_never_accepts_changed_baseline(isolated_dir, monkeypatch):
    import agentcad.render as renderer

    step, _ = _baseline(isolated_dir)
    first = geometry_image(step, isolated_dir, object_id="face_v1_1")
    original = renderer.render_geometry_sheet
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(renderer, "render_geometry_sheet", counted)
    assert geometry_image(step, isolated_dir, object_id="face_v1_1") == first
    assert not calls
    Path(first["png"]).write_bytes(b"interrupted image")
    regenerated = geometry_image(step, isolated_dir, object_id="face_v1_1")
    assert len(calls) == 1
    _assert_sheet(regenerated)
    metadata = Path(regenerated["metadata"])
    data = json.loads(metadata.read_text())
    data["annotations"][0]["id"] = "face_v1_2"
    metadata.write_text(json.dumps(data), encoding="utf-8")
    assert geometry_image(step, isolated_dir, object_id="face_v1_1")["annotations"][0]["id"] == "face_v1_1"
    assert len(calls) == 2
    step.write_bytes(step.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Baseline does not match"):
        geometry_image(step, isolated_dir, object_id="face_v1_1")
    assert len(calls) == 2


def test_sheet_failure_preserves_core_and_retry_and_opt_out_work(runner, isolated_dir, monkeypatch):
    import agentcad.render as renderer

    source = _nested_step(isolated_dir / "source.step")
    original = renderer.render_geometry_sheet

    def broken(*args, **kwargs):
        raise RuntimeError("graphics unavailable")

    monkeypatch.setattr(renderer, "render_geometry_sheet", broken)
    imported = _json(runner, ["import", str(source), "--init", "--no-view", "--no-diff"])
    assert imported["status"] == "success"
    assert imported["artifacts"]["inspection_image"]["status"] == "failed"
    assert (isolated_dir / "edit.py").is_file()
    assert load_geometry(imported["outputs"]["step"]).pick_edge("edge_v1_1").length > 0
    failed = runner.invoke(cli, ["inspect", imported["outputs"]["step"], "--id", "face_v1_1", "--image"])
    assert failed.exit_code == 1
    assert json.loads(failed.stdout)["entity"]["id"] == "face_v1_1"
    monkeypatch.setattr(renderer, "render_geometry_sheet", original)
    retried = _json(runner, ["inspect", imported["outputs"]["step"], "--image"])
    _assert_sheet(retried["image"])
    opted_out = _json(runner, ["import", str(source), "--label", "no_sheet", "--no-view", "--no-diff", "--no-inspection-image"])
    assert opted_out["artifacts"]["inspection_image"]["status"] == "skipped"
    _assert_sheet(_json(runner, ["inspect", opted_out["outputs"]["step"], "--image"])["image"])


def test_wrong_id_and_incompatible_image_options_fail_before_render(runner, isolated_dir, monkeypatch):
    step, _ = _baseline(isolated_dir)
    for extra in (["--isolate"], ["--image", "--isolate"], ["--image", "--ids"], ["--id", "face_v2_1", "--image"]):
        result = runner.invoke(cli, ["inspect", str(step), *extra])
        assert result.exit_code != 0
    assert not (isolated_dir / "inspection").exists()
