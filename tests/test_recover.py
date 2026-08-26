import json

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli


def _write_box_step(path):
    exporters.export(cq.Workplane("XY").box(10, 10, 10), str(path))
    return path


def _init(runner):
    result = runner.invoke(cli, ["init", "--name", "recover-test"])
    assert result.exit_code == 0, result.output


def test_recover_registers_valid_orphan_without_advancing_current(
    runner, isolated_dir
):
    _init(runner)
    orphan = isolated_dir / "v1_interrupted_box"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")
    (orphan / "script.py").write_text("show_object(Box(10, 10, 10))\n")

    result = runner.invoke(cli, ["recover", "v1_interrupted_box"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["command"] == "recover"
    assert data["status"] == "success"
    assert data["recovered"] is True
    assert data["version"] == 1
    assert data["label"] == "interrupted_box"
    assert data["current_advanced"] is False
    assert data["metrics"]["is_valid"] is True
    assert data["metrics"]["volume"] == 1000.0

    meta = json.loads((orphan / "meta.json").read_text())
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    assert meta["status"] == "success"
    assert meta["core"]["status"] == "success"
    assert meta["recovery"]["reconciled"] is True
    assert manifest.get("current") is None
    assert manifest["versions"] == [{
        "version": 1,
        "label": "interrupted_box",
        "status": "success",
        "source": "script",
        "path": "v1_interrupted_box/",
    }]
    assert orphan.exists()
    assert (orphan / "output.step").exists()


def test_recover_make_current_is_explicit(runner, isolated_dir):
    _init(runner)
    orphan = isolated_dir / "v1_box"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")

    result = runner.invoke(cli, ["recover", "v1_box", "--make-current"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["current_advanced"] is True
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    assert manifest["current"] == "box"


def test_recover_already_consistent_version_is_no_op(runner, isolated_dir):
    _init(runner)
    version_dir = isolated_dir / "v1_box"
    version_dir.mkdir()
    _write_box_step(version_dir / "output.step")
    meta_path = version_dir / "meta.json"
    meta = {
        "version": 1,
        "label": "box",
        "status": "success",
        "custom": "preserve exactly",
    }
    meta_path.write_text(json.dumps(meta))
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 1,
        "label": "box",
        "status": "success",
        "path": "v1_box/",
    }]
    manifest_path.write_text(json.dumps(manifest))
    meta_before = meta_path.read_bytes()
    manifest_before = manifest_path.read_bytes()

    result = runner.invoke(cli, ["recover", "v1_box", "--make-current"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["recovered"] is False
    assert data["reason"] == "already_consistent"
    assert data["recovery_performed"] is False
    assert data["current_advanced"] is False
    assert meta_path.read_bytes() == meta_before
    assert manifest_path.read_bytes() == manifest_before


def test_recover_repairs_missing_meta_for_existing_manifest_entry(
    runner, isolated_dir
):
    _init(runner)
    version_dir = isolated_dir / "v1_original_label"
    version_dir.mkdir()
    _write_box_step(version_dir / "output.step")
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 1,
        "label": "manifest_label",
        "status": "success",
        "source": "import",
        "path": "v1_original_label/",
        "custom_history": {"source_system": "legacy"},
    }]
    manifest_path.write_text(json.dumps(manifest))

    result = runner.invoke(cli, ["recover", "v1_original_label"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["label"] == "manifest_label"
    meta = json.loads((version_dir / "meta.json").read_text())
    assert meta["source"] == "import"
    reconciled_manifest = json.loads(manifest_path.read_text())
    assert len(reconciled_manifest["versions"]) == 1
    assert reconciled_manifest["versions"][0]["custom_history"] == {
        "source_system": "legacy"
    }


def test_recover_registers_existing_metadata_without_losing_fields(
    runner, isolated_dir
):
    _init(runner)
    orphan = isolated_dir / "v1_box"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")
    (orphan / "meta.json").write_text(json.dumps({
        "command": "run",
        "status": "success",
        "version": 1,
        "label": "custom_label",
        "runtime": "build123d",
        "custom_provenance": {"ticket": "CAD-42"},
        "outputs": {"step": "v1_box/output.step"},
        "artifacts": {
            "preview": {"status": "success"},
            "viewer": {"status": "pending"},
        },
    }))

    result = runner.invoke(cli, ["recover", "v1_box"])

    assert result.exit_code == 0, result.output
    meta = json.loads((orphan / "meta.json").read_text())
    assert meta["label"] == "custom_label"
    assert meta["custom_provenance"] == {"ticket": "CAD-42"}
    assert meta["recovery"]["original_metadata_present"] is True
    assert meta["artifacts"]["preview"]["status"] == "success"
    assert meta["artifacts"]["viewer"]["status"] == "unavailable"
    assert "unknown after interruption" in meta["artifacts"]["viewer"]["message"]


def test_recover_refuses_directory_without_step_and_preserves_it(
    runner, isolated_dir
):
    _init(runner)
    interrupted = isolated_dir / "v1_no_step"
    interrupted.mkdir()
    script = interrupted / "script.py"
    script.write_text("important source\n")

    result = runner.invoke(cli, ["recover", "v1_no_step"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "unrecoverable"
    assert data["reason"] == "missing_core_step"
    assert data["recovery_performed"] is False
    assert script.read_text() == "important source\n"
    assert not (interrupted / "meta.json").exists()
    assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []


def test_recover_refuses_malformed_step_without_writing_history(
    runner, isolated_dir
):
    _init(runner)
    orphan = isolated_dir / "v1_broken"
    orphan.mkdir()
    step = orphan / "output.step"
    step.write_text("not a STEP file\n")

    result = runner.invoke(cli, ["recover", "v1_broken"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "malformed"
    assert data["recovery_performed"] is False
    assert step.read_text() == "not a STEP file\n"
    assert not (orphan / "meta.json").exists()
    assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []


def test_recover_refuses_invalid_geometry_without_writing_history(
    runner, isolated_dir, monkeypatch
):
    _init(runner)
    orphan = isolated_dir / "v1_invalid"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")
    from agentcad import metrics

    real_compute = metrics.compute_metrics

    def invalid_metrics(shape):
        result = real_compute(shape)
        result["is_valid"] = False
        result["validity_errors"] = ["BRepCheck_InvalidToleranceValue"]
        return result

    monkeypatch.setattr("agentcad.metrics.compute_metrics", invalid_metrics)

    result = runner.invoke(cli, ["recover", "v1_invalid"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "invalid_geometry"
    assert data["reason"] == "invalid_core_geometry"
    assert data["recovery_performed"] is False
    assert not (orphan / "meta.json").exists()
    assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []


def test_recover_refuses_corrupt_metadata_without_overwriting_it(
    runner, isolated_dir
):
    _init(runner)
    orphan = isolated_dir / "v1_box"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")
    meta_path = orphan / "meta.json"
    meta_path.write_text('{"status": "success"')

    result = runner.invoke(cli, ["recover", "v1_box"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "unrecoverable"
    assert data["reason"] == "corrupt_metadata"
    assert meta_path.read_text() == '{"status": "success"'


def test_recover_refuses_metadata_version_mismatch(runner, isolated_dir):
    _init(runner)
    orphan = isolated_dir / "v1_box"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")
    meta_path = orphan / "meta.json"
    original = {"status": "success", "version": 9, "label": "box"}
    meta_path.write_text(json.dumps(original))

    result = runner.invoke(cli, ["recover", "v1_box"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "conflict"
    assert data["reason"] == "metadata_version_mismatch"
    assert json.loads(meta_path.read_text()) == original
    assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []


def test_recover_refuses_manifest_version_mismatch(runner, isolated_dir):
    _init(runner)
    orphan = isolated_dir / "v1_box"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 9,
        "label": "box",
        "status": "success",
        "path": "v1_box/",
    }]
    manifest_path.write_text(json.dumps(manifest))

    result = runner.invoke(cli, ["recover", "v1_box"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "conflict"
    assert data["reason"] == "manifest_version_mismatch"
    assert not (orphan / "meta.json").exists()
    assert json.loads(manifest_path.read_text()) == manifest


def test_recover_refuses_version_number_collision(runner, isolated_dir):
    _init(runner)
    registered = isolated_dir / "v1_registered"
    registered.mkdir()
    _write_box_step(registered / "output.step")
    (registered / "meta.json").write_text(json.dumps({"status": "success"}))
    orphan = isolated_dir / "v1_orphan"
    orphan.mkdir()
    _write_box_step(orphan / "output.step")
    manifest_path = isolated_dir / "agentcad.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["versions"] = [{
        "version": 1,
        "label": "registered",
        "status": "success",
        "path": "v1_registered/",
    }]
    manifest_path.write_text(json.dumps(manifest))

    result = runner.invoke(cli, ["recover", "v1_orphan"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "conflict"
    assert data["reason"] == "version_number_already_registered"
    assert data["registered_path"] == "v1_registered/"
    assert not (orphan / "meta.json").exists()
    assert len(json.loads(manifest_path.read_text())["versions"]) == 1


def test_recover_refuses_duplicate_unregistered_version_directories(
    runner, isolated_dir
):
    _init(runner)
    first = isolated_dir / "v1_first"
    second = isolated_dir / "v1_second"
    first.mkdir()
    second.mkdir()
    _write_box_step(first / "output.step")
    _write_box_step(second / "output.step")

    result = runner.invoke(cli, ["recover", "v1_first"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "conflict"
    assert data["reason"] == "duplicate_version_directories"
    assert set(data["paths"]) == {"v1_first", "v1_second"}
    assert not (first / "meta.json").exists()
    assert not (second / "meta.json").exists()


def test_recover_requires_direct_version_directory(runner, isolated_dir):
    _init(runner)
    outside = isolated_dir / "other"
    outside.mkdir()
    _write_box_step(outside / "output.step")

    result = runner.invoke(cli, ["recover", "other"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["reason"] == "not_a_version_directory"


def test_recover_rejects_zero_version_directory(runner, isolated_dir):
    _init(runner)
    zero = isolated_dir / "v0_box"
    zero.mkdir()
    _write_box_step(zero / "output.step")

    result = runner.invoke(cli, ["recover", "v0_box"])

    assert result.exit_code == 1
    assert json.loads(result.stdout)["reason"] == "not_a_version_directory"
    assert not (zero / "meta.json").exists()


def test_recover_refuses_version_directory_symlink(runner, isolated_dir):
    _init(runner)
    target = isolated_dir / "outside"
    target.mkdir()
    _write_box_step(target / "output.step")
    link = isolated_dir / "v1_link"
    link.symlink_to(target, target_is_directory=True)

    result = runner.invoke(cli, ["recover", "v1_link"])

    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["status"] == "error"
    assert data["reason"] == "version_directory_symlink"
    assert not (target / "meta.json").exists()
