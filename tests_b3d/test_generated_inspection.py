"""A saved edit remains inspectable/selectable without another import."""

import base64
import hashlib
import json
import shlex
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.geometry_manifest import load_geometry, read_manifest
from agentcad.inspection_report import inspection_report


FAST = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]


def _json(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _save(runner, root, source, label="part", extra=()):
    path = root / "edit.py"
    path.write_text(source, encoding="utf-8")
    return _json(runner, ["run", str(path), "--label", label, *FAST, *extra])


@pytest.mark.parametrize("runtime", ["build123d", "cadquery"])
def test_saved_step_issues_ids_and_lazy_quality_bound_report(runner, isolated_dir, runtime):
    if runtime == "cadquery":
        pytest.importorskip("cadquery")
    _json(runner, ["init", "--runtime", runtime])
    source = ("show_object(Box(10, 20, 5), name='display object')" if runtime == "build123d"
              else "import cadquery as cq\nshow_object(cq.Workplane('XY').box(10, 20, 5), name='display object')")
    saved = _save(runner, isolated_dir, source)
    step = Path(saved["outputs"]["step"])
    identity = read_manifest(step)
    assert saved["artifacts"]["geometry_manifest"]["status"] == "success"
    assert Path(saved["outputs"]["geometry_manifest"]).is_file()
    assert saved["geometry_manifest"]["manifest_id"] == identity["manifest_id"]
    assert saved["geometry_manifest"]["cross_version_correspondence"] == "not_available"
    assert identity["source"] == {
        "kind": "script", "filename": "edit.py", "runtime": runtime,
        "sha256": hashlib.sha256(step.with_name("script.py").read_bytes()).hexdigest(),
        "assembly_metadata": "derived_normalized_geometry",
    }
    assert saved["parts"][0]["id"] == "display_object"  # Existing display IDs remain separate.
    assert all(e["metadata_origin"] == "derived_normalized_geometry"
               for e in identity["entities"].values() if e["kind"] in {"part", "occurrence"})
    assert not (step.parent / "inspection").exists()  # No eager second visual bundle.
    registry = step.with_name("geometry_manifest.json").read_bytes()
    response = _json(runner, ["inspect", str(step), "--report", "--no-images"])
    assert response["quality"]["is_valid"] is True
    assert response["quality"]["status"] == "available"
    assert response["recognition"]["recognized_feature_count"] is None
    assert response["version"] == saved["version"] == 1
    assert response["baseline_sha256"] == identity["baseline"]["sha256"]
    assert saved["timings"]["geometry_manifest_ms"] >= 0
    assert "geometry_manifest" in saved["completed_phases"]
    assert len(json.loads((isolated_dir / "agentcad.json").read_text())["versions"]) == 1
    assert step.with_name("geometry_manifest.json").read_bytes() == registry


def test_generated_quality_rejects_neighbouring_metadata(runner, isolated_dir):
    _json(runner, ["init"])
    saved = _save(runner, isolated_dir, "show_object(Box(10,20,5))")
    step = Path(saved["outputs"]["step"])
    original = json.loads(step.with_name("meta.json").read_text())
    good, _ = inspection_report(step, step.parent, images=False, meta=original)
    assert good["quality"]["status"] == "available"
    for field in ("manifest_id", "baseline_sha256"):
        wrong = json.loads(json.dumps(original))
        wrong["geometry_manifest"][field] = "a-neighbouring-version"
        unavailable, _ = inspection_report(step, step.parent, images=False, meta=wrong)
        assert unavailable["quality"]["status"] == "not_available"
        assert unavailable["analysis_revision"] != good["analysis_revision"]
    assert Path(good["inventory"]["report"]).exists()


def test_real_import_edit_inspect_and_second_edit_preserve_other_solid(runner, isolated_dir):
    fixture = Path(__file__).parents[1] / "tests/fixtures/real_world/cadgenbench_101_agentcad.step"
    imported = _json(runner, ["import", str(fixture), "--init", "--no-view", "--no-diff", "--no-inspection-image"])
    old_step = Path(imported["outputs"]["step"])
    old_registry = old_step.with_name("geometry_manifest.json").read_bytes()
    generated = _save(runner, isolated_dir,
                      "from agentcad.geometry_manifest import load_geometry\n"
                      f"base = load_geometry({str(old_step)!r}).shape\n"
                      "show_object(Compound(children=[base, Box(10,10,10).translate((400,0,0))]))", "added")
    step = Path(generated["outputs"]["step"])
    context = load_geometry(step)
    assert len(context.shape.solids()) == 2
    assert read_manifest(step)["counts"]["solids"] == 2
    edge = next(e for e in context.manifest["entities"].values()
                if e["kind"] == "edge" and e["endpoints"][0]["x"] > 350)
    looked_up = _json(runner, ["inspect", str(step), "--id", edge["id"], "--image"])
    assert looked_up["entity"] == context.record(edge["id"])
    assert looked_up["image"]["annotations"][0]["id"] == edge["id"]
    assert context.pick_edge(edge["id"]).length == pytest.approx(edge["length"])
    second = _save(runner, isolated_dir,
                   "from agentcad.geometry_manifest import load_geometry\n"
                   f"geometry = load_geometry({str(step)!r})\n"
                   f"show_object(geometry.shape.fillet(0.5, [geometry.pick_edge({edge['id']!r})]))", "rounded")
    newest = load_geometry(second["outputs"]["step"])
    assert second["version"] == 3
    assert newest.manifest["counts"]["solids"] == 2
    with pytest.raises(ValueError, match="another version"):
        newest.pick_edge(edge["id"])
    original_solid = max(context.shape.solids(), key=lambda s: s.volume)
    retained_solid = max(newest.shape.solids(), key=lambda s: s.volume)
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from agentcad.topo_ids import _solid_volume

    for before, after in ((original_solid, retained_solid), (retained_solid, original_solid)):
        difference = BRepAlgoAPI_Cut(before.wrapped, after.wrapped)
        assert difference.IsDone()
        assert _solid_volume(difference.Shape()) == pytest.approx(0, abs=1e-6)
    original_box = min(context.shape.solids(), key=lambda s: s.volume)
    rounded_box = min(newest.shape.solids(), key=lambda s: s.volume)
    assert rounded_box.volume < original_box.volume
    assert old_step.with_name("geometry_manifest.json").read_bytes() == old_registry
    assert len(json.loads((isolated_dir / "agentcad.json").read_text())["versions"]) == 3


def test_mcp_saved_run_and_inspection_match_cli_with_configured_paths(runner, isolated_dir):
    from agentcad.mcp.server import run as mcp_run, inspect as mcp_inspect
    from mcp.types import CallToolResult

    (isolated_dir / "agentcad.toml").write_text('build_dir = "artifacts with spaces"\n', encoding="utf-8")
    _json(runner, ["init"])
    (isolated_dir / "edit.py").write_text("show_object(Box(10,20,5))", encoding="utf-8")
    saved = mcp_run(script="edit.py", cwd=str(isolated_dir), output="generated", preview=False, diff=False, view=False)
    assert saved["_exit_code"] == 0
    step = Path(saved["outputs"]["step"])
    assert step.is_absolute()
    assert "artifacts with spaces" in str(step)
    assert Path(saved["outputs"]["geometry_manifest"]).is_file()
    assert saved["geometry_manifest"]["manifest_id"] == read_manifest(step)["manifest_id"]
    cli_report = _json(runner, ["inspect", str(step), "--report", "--max-images", "1"])
    mcp_report = mcp_inspect(file=str(step), cwd=str(isolated_dir), report=True, max_images=1)
    assert isinstance(mcp_report, CallToolResult)
    assert mcp_report.structuredContent.pop("_exit_code") == 0
    assert mcp_report.structuredContent == cli_report
    png = Path(cli_report["inventory"]["image_root"]) / cli_report["media"][0]["png"]
    assert base64.b64decode(mcp_report.content[1].data) == png.read_bytes()
    assert hashlib.sha256(png.read_bytes()).hexdigest() == cli_report["media"][0]["sha256"]
    face_id = saved["geometry_manifest"]["sample_ids"]["face"]
    looked_up = mcp_inspect(file=str(step), cwd=str(isolated_dir), entity_id=face_id)
    assert looked_up["entity"] == load_geometry(step).record(face_id)


@pytest.mark.parametrize("failure", ["exception", "timeout"])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("transport", ["cli", "mcp"])
def test_inventory_failure_preserves_committed_step(runner, isolated_dir, monkeypatch, failure, configured, transport):
    from agentcad.commands.run import _handle_run_timeout

    if configured:
        (isolated_dir / "agentcad.toml").write_text('build_dir = "artifacts with spaces"\n', encoding="utf-8")
    _json(runner, ["init"])
    # Exercise recovery independently of platform alarm support. The timeout
    # below is injected at the phase boundary, not scheduled by a real timer.
    monkeypatch.setenv("AGENTCAD_RUN_TIMEOUT_S", "0")

    def broken(*args, **kwargs):
        if failure == "timeout":
            _handle_run_timeout(None, None)
        raise RuntimeError("inventory unavailable")

    monkeypatch.setattr("agentcad.geometry_manifest.create_manifest", broken)
    if transport == "cli":
        saved = _save(runner, isolated_dir, "show_object(Box(10,20,5))", label="saved part")
    else:
        from agentcad.mcp.server import run as mcp_run

        (isolated_dir / "edit.py").write_text("show_object(Box(10,20,5))", encoding="utf-8")
        saved = mcp_run(script="edit.py", cwd=str(isolated_dir), output="saved part", preview=False, diff=False, view=False)
        assert saved["_exit_code"] == 0
    assert saved["status"] == saved["core"]["status"] == "success"
    assert saved["artifacts"]["geometry_manifest"]["status"] == ("failed" if failure == "exception" else "timeout")
    assert "geometry_manifest" not in saved["outputs"]
    assert Path(saved["outputs"]["step"]).is_file()
    assert Path(saved["outputs"]["script"]).is_file()
    assert any("inventory" in warning or "geometry_manifest" in warning for warning in saved["warnings"])
    build = isolated_dir / "artifacts with spaces" if configured else isolated_dir
    history = (build / "agentcad.json").read_bytes()
    manifest = json.loads(history)
    assert manifest["versions"][0]["status"] == "success"
    if failure == "timeout":
        assert "typed-ID inventory timed out" in saved["suggestion"]
        assert "inspect --ids" in saved["suggestion"]
        action = shlex.split(saved["next_actions"][0])
        assert action[:2] == ["agentcad", "inspect"]
        assert action[2] == saved["outputs"]["step"]
        assert "--ids" in action
        if configured:
            assert action[-2:] == ["--build-dir", str(build)]
        step_bytes = Path(saved["outputs"]["step"]).read_bytes()
        legacy = _json(runner, action[1:])
        assert legacy["status"] == "success"
        assert legacy["faces"] and all("object_id" not in face for face in legacy["faces"])
        assert Path(saved["outputs"]["step"]).read_bytes() == step_bytes
        assert (build / "agentcad.json").read_bytes() == history
    failed_lookup = runner.invoke(cli, ["inspect", saved["outputs"]["step"], "--id", "face_v1_1"])
    assert failed_lookup.exit_code == 1
    assert not Path(saved["outputs"]["step"]).with_name("geometry_manifest.json").exists()


def test_graphics_failure_without_alarm_preserves_generated_ids(runner, isolated_dir, monkeypatch):
    import signal

    _json(runner, ["init"])
    monkeypatch.delattr(signal, "SIGALRM", raising=False)

    def broken(*args, **kwargs):
        raise RuntimeError("graphics unavailable")

    monkeypatch.setattr("agentcad.render.render_shape", broken)
    saved = _save(runner, isolated_dir, "show_object(Box(10,20,5))", extra=["--render", "iso"])
    assert saved["status"] == saved["core"]["status"] == "success"
    assert saved["artifacts"]["renders"]["status"] == "failed"
    assert saved["artifacts"]["geometry_manifest"]["status"] == "success"
    assert read_manifest(saved["outputs"]["step"])["manifest_id"] == saved["geometry_manifest"]["manifest_id"]


def test_dry_invalid_and_failed_runs_do_not_issue_ids(runner, isolated_dir, monkeypatch):
    _json(runner, ["init"])

    def unexpected(*args, **kwargs):
        pytest.fail("No valid saved STEP was committed; IDs must not be issued")

    monkeypatch.setattr("agentcad.geometry_manifest.create_manifest", unexpected)
    dry = _save(runner, isolated_dir, "show_object(Box(10,20,5))", extra=["--dry-run"])
    assert dry["artifact_created"] is False
    (isolated_dir / "edit.py").write_text("show_object(Box(10,20,5))\nraise RuntimeError('execution failed')", encoding="utf-8")
    assert runner.invoke(cli, ["run", "edit.py", "--label", "failed", *FAST]).exit_code == 1
    fixture = Path(__file__).parents[1] / "tests/fixtures/validation/open_shell.step"
    (isolated_dir / "edit.py").write_text(f"show_object(load_step_shape({str(fixture)!r}))", encoding="utf-8")
    invalid = runner.invoke(cli, ["run", "edit.py", "--label", "invalid", *FAST])
    assert invalid.exit_code == 1
    assert json.loads(invalid.stdout)["status"] == "invalid_geometry"
    assert not list(isolated_dir.rglob("geometry_manifest.json"))
