"""Normalized imported identities: provenance, reload, lookup and real edits."""

import json
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.geometry_manifest import create_manifest, load_geometry, read_manifest
from agentcad.step_assembly import read_step_assembly, write_step_assembly
from tests_b3d.test_step_assembly import _nested_step


def _import(runner, path, label="baseline", *extra):
    result = runner.invoke(cli, ["import", str(path), "--init", "--label", label, "--no-view", "--no-diff", *extra])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _json(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.mark.parametrize("platform_encoding", ["utf-8", "cp1252"])
def test_named_assembly_import_issues_normalized_ids_and_targets_one_instance(
    runner, isolated_dir, monkeypatch, platform_encoding,
):
    # Exercise the Windows default even when this test runs on macOS/Linux.
    original_write_text = Path.write_text

    def platform_write_text(path, data, encoding=None, **kwargs):
        return original_write_text(path, data, encoding=encoding or platform_encoding, **kwargs)

    monkeypatch.setattr(Path, "write_text", platform_write_text)
    source = _nested_step(isolated_dir / "supplier.step", tilted=True)
    imported = _import(runner, source)
    step = Path(imported["outputs"]["step"])
    assert imported["artifacts"]["geometry_manifest"]["status"] == "success"
    assert Path(imported["outputs"]["geometry_manifest"]).is_file()
    saved = read_manifest(step)
    assert saved["source"]["assembly_metadata"] == "preserved_source_document"
    assert saved["counts"]["part_definitions"] == 1
    assert saved["counts"]["part_occurrences"] == 2
    assert saved["recognition"]["status"] == "not_available"
    context = load_geometry(step)
    targets = [e for e in saved["entities"].values() if e["kind"] == "occurrence" and e["name"] == "right instance"]
    assert len(targets) == 1
    target = targets[0]
    definition = saved["entities"][target["part_id"]]
    assert definition["name"] == "Bracket / pièce fournisseur"
    assert definition["coordinate_frame"] == "definition_local"
    edge_id = target["members"]["edge"][0]
    before = context.shape
    selected = context.pick_edge(edge_id)
    assert 90 <= selected.center().Y <= 100
    after = before.fillet(0.5, [selected])
    assert after.is_valid
    assert after.volume < before.volume
    original_other = min(before.solids(), key=lambda s: s.center().Y)
    edited_other = min(after.solids(), key=lambda s: s.center().Y)
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from agentcad.topo_ids import _solid_volume

    for left, right in ((original_other, edited_other), (edited_other, original_other)):
        assert _solid_volume(BRepAlgoAPI_Cut(left.wrapped, right.wrapped).Shape()) == pytest.approx(0, abs=1e-6)
    looked_up = _json(runner, ["inspect", str(step), "--id", edge_id])
    assert looked_up["entity"] == context.record(edge_id)
    assert looked_up["geometry_manifest"]["manifest_id"] == saved["manifest_id"]
    legacy = _json(runner, ["inspect", str(step), "--ids"])
    assert legacy["edges"][0]["id"] == 1
    assert legacy["edges"][0]["object_id"] == "edge_v1_1"
    # The generated handoff runs unmodified and uses this same checked map.
    scaffold = (isolated_dir / "edit.py").read_text(encoding="utf-8")
    assert "geometry = load_geometry(" in scaffold
    assert "geometry.pick_face" in scaffold
    _json(runner, ["run", "edit.py", "--label", "unchanged", "--no-preview", "--no-view", "--no-diff"])


@pytest.mark.parametrize("name", ["cadgenbench_101_agentcad.step", "pump_manifold.step"])
def test_real_file_reload_and_lookup(runner, isolated_dir, name):
    fixture = Path(__file__).parents[1] / "tests" / "fixtures" / "real_world" / name
    imported = _import(runner, fixture)
    context = load_geometry(imported["outputs"]["step"])
    faces = [e for e in context.manifest["entities"].values() if e["kind"] == "face"]
    for face in faces:
        assert context.pick_face(face["id"]).area == pytest.approx(face["area"], abs=1e-3)
    looked_up = _json(runner, ["inspect", imported["outputs"]["step"], "--id", faces[-1]["id"]])
    assert looked_up["entity"] == faces[-1]
    assert looked_up["geometry_manifest"]["manifest_id"] == context.manifest["manifest_id"]


def test_cli_and_mcp_lookup_are_equivalent(runner, isolated_dir):
    from agentcad.mcp.server import inspect as mcp_inspect

    imported = _import(runner, _nested_step(isolated_dir / "supplier.step"))
    step = imported["outputs"]["step"]
    expected = _json(runner, ["inspect", step, "--id", "face_v1_1"])
    actual = mcp_inspect(file=step, cwd=str(isolated_dir), entity_id="face_v1_1")
    assert actual.pop("_exit_code") == 0
    assert actual == expected


def test_wrong_version_kind_and_unknown_ids_fail(runner, isolated_dir):
    imported = _import(runner, _nested_step(isolated_dir / "supplier.step"))
    step = imported["outputs"]["step"]
    context = load_geometry(step)
    for value in ("face_v2_1", "face_v1_99999", "F1"):
        with pytest.raises(ValueError):
            context.pick_face(value)
        response = runner.invoke(cli, ["inspect", step, "--id", value])
        assert response.exit_code == 1
        assert json.loads(response.stdout)["status"] == "error"
    with pytest.raises(ValueError, match="Expected a face"):
        context.pick_face("edge_v1_1")
    # Caller-side metadata edits cannot retarget a live selection context.
    exposed = context.manifest
    exposed["entities"]["face_v1_1"]["index"] = 2
    assert context.record("face_v1_1")["index"] == 1
    from build123d import Location
    original_edge_center = context.pick_edge("edge_v1_1").center()
    moved = context.shape
    moved.move(Location((1000, 0, 0)))
    assert context.shape.bounding_box().min.X < 1000
    assert context.pick_edge("edge_v1_1").center() == original_edge_center


@pytest.mark.parametrize("change", ["baseline", "manifest", "kernel"])
def test_stale_data_fails_before_selection(runner, isolated_dir, monkeypatch, change):
    imported = _import(runner, _nested_step(isolated_dir / "supplier.step"))
    step = Path(imported["outputs"]["step"])
    if change == "baseline":
        step.write_bytes(step.read_bytes() + b"\n")
    elif change == "manifest":
        path = step.with_name("geometry_manifest.json")
        data = json.loads(path.read_text())
        data["entities"]["face_v1_1"]["index"] = 2
        path.write_text(json.dumps(data))
    else:
        monkeypatch.setattr("agentcad.geometry_manifest._kernel_version", lambda: "incompatible")
    with pytest.raises(ValueError):
        load_geometry(step)
    result = runner.invoke(cli, ["inspect", str(step), "--id", "face_v1_1"])
    assert result.exit_code == 1


def test_manifest_and_scaffold_survive_render_failure(runner, isolated_dir, monkeypatch):
    def broken(*args, **kwargs):
        assert (isolated_dir / "v1_baseline" / "geometry_manifest.json").exists()
        assert (isolated_dir / "edit.py").exists()
        raise RuntimeError("render unavailable")

    monkeypatch.setattr("agentcad.render.render_composite_4view", broken)
    imported = _import(runner, _nested_step(isolated_dir / "supplier.step"))
    assert imported["status"] == "success"
    assert imported["artifacts"]["preview"]["status"] == "failed"
    assert load_geometry(imported["outputs"]["step"]).pick_face("face_v1_1").area > 0


def test_failed_inventory_preserves_usable_baseline_and_legacy_handoff(runner, isolated_dir, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("inventory interrupted")

    monkeypatch.setattr("agentcad.geometry_manifest.create_manifest", fail)
    imported = _import(runner, _nested_step(isolated_dir / "supplier.step"))
    assert imported["status"] == "success"
    assert imported["artifacts"]["geometry_manifest"]["status"] == "failed"
    assert Path(imported["outputs"]["step"]).is_file()
    assert "base = load_step(" in (isolated_dir / "edit.py").read_text()


def test_brep_intake_marks_assembly_metadata_as_derived(runner, isolated_dir):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepTools import BRepTools

    path = isolated_dir / "source.brep"
    BRepTools.Write_s(BRepPrimAPI_MakeBox(10, 20, 30).Shape(), str(path))
    imported = _import(runner, path)
    context = load_geometry(imported["outputs"]["step"])
    assert context.manifest["source"]["assembly_metadata"] == "derived_normalized_geometry"


def test_issued_manifest_cannot_be_overwritten(tmp_path):
    path = tmp_path / "output.step"
    write_step_assembly(read_step_assembly(_nested_step(tmp_path / "source.step")), path)
    source = {"assembly_metadata": "preserved_source_document"}
    create_manifest(path, 7, source=source)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        create_manifest(path, 7, source=source)
    assert load_geometry(path).pick_face("face_v7_1").area > 0


def test_concurrent_manifest_publication_preserves_first_writer(tmp_path, monkeypatch):
    from agentcad.versioning import atomic_write_json

    path = tmp_path / "output.step"
    write_step_assembly(read_step_assembly(_nested_step(tmp_path / "source.step")), path)
    first = {"issued_by": "another writer"}

    def competing_writer(destination, payload, **kwargs):
        # Another writer wins after create_manifest's initial existence check.
        atomic_write_json(destination, first)
        atomic_write_json(destination, payload, **kwargs)

    monkeypatch.setattr("agentcad.geometry_manifest.atomic_write_json", competing_writer)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        create_manifest(path, 1, source={"assembly_metadata": "preserved_source_document"})
    assert json.loads(path.with_name("geometry_manifest.json").read_text()) == first
    assert not list(tmp_path.glob(".geometry_manifest.json.*.tmp"))
