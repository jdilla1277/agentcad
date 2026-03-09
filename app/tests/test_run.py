import json
import shutil
from pathlib import Path

from cadtool.cli import cli
from cadtool.manifest import MANIFEST_FILE


SIMPLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""


def _init_project(runner):
    runner.invoke(cli, ["init", "--name", "test_project"])


def _write_script(directory, content=SIMPLE_BOX_SCRIPT, filename="script.py"):
    path = directory / filename
    path.write_text(content)
    return path


def test_run_no_manifest_error(runner, isolated_dir):
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    assert "cadtool.json" in parsed["message"]


def test_run_script_not_found_error(runner, isolated_dir):
    _init_project(runner)
    result = runner.invoke(cli, ["run", "missing.py", "--output", "v1"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    assert "missing.py" in parsed["message"]


def test_run_creates_version_directory(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert (isolated_dir / "v1").is_dir()


def test_run_copies_script_to_version_dir(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    copied = isolated_dir / "v1" / "script.py"
    assert copied.exists()
    assert copied.read_text() == SIMPLE_BOX_SCRIPT


def test_run_produces_step_file(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    step = isolated_dir / "v1" / "output.step"
    assert step.exists()
    assert step.stat().st_size > 0


def test_run_success_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "success"
    assert parsed["version"] == 1
    assert parsed["label"] == "v1"
    assert "step" in parsed["outputs"]
    assert "script" in parsed["outputs"]


def test_run_creates_meta_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    meta_path = isolated_dir / "v1" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["version"] == 1
    assert meta["label"] == "v1"
    assert meta["status"] == "success"
    assert "created" in meta
    assert "script" in meta
    assert "outputs" in meta


def test_run_updates_manifest_versions(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 1
    entry = manifest["versions"][0]
    assert entry["version"] == 1
    assert entry["label"] == "v1"
    assert entry["status"] == "success"
    assert manifest["current"] == "v1"


def test_run_auto_increments_version(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "first"])
    result = runner.invoke(cli, ["run", "script.py", "--output", "second"])
    parsed = json.loads(result.output)
    assert parsed["version"] == 2
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 2


def test_run_label_in_directory_name(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "my_label"])
    assert (isolated_dir / "v1_my_label").is_dir()
    assert (isolated_dir / "v1_my_label" / "output.step").exists()


def test_run_script_error_creates_failed_directory(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content="this is not valid python(")
    result = runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    assert result.exit_code == 1
    # Failed directory exists with _failed suffix
    failed_dir = isolated_dir / "v1_broken_failed"
    assert failed_dir.is_dir()
    # meta.json with status failed and error key
    meta = json.loads((failed_dir / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert "error" in meta
    # Script copied
    assert (failed_dir / "script.py").exists()
    # No STEP output
    assert not (failed_dir / "output.step").exists()
    # Manifest has 1 failed entry
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 1
    assert manifest["versions"][0]["status"] == "failed"


def test_run_failed_json_response(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content="this is not valid python(")
    result = runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "failed"
    assert parsed["version"] == 1
    assert "error" in parsed
    assert parsed["path"] == "v1_broken_failed/"


def test_run_failed_does_not_advance_current(runner, isolated_dir):
    _init_project(runner)
    # Successful v1
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "good"])
    # Failed v2
    _write_script(isolated_dir, content="bad(")
    runner.invoke(cli, ["run", "script.py", "--output", "bad"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert manifest["current"] == "good"


def test_run_failed_consumes_version_number(runner, isolated_dir):
    _init_project(runner)
    # Failed v1
    _write_script(isolated_dir, content="bad(")
    runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    # Successful v2
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "fixed"])
    parsed = json.loads(result.output)
    assert parsed["version"] == 2
    assert (isolated_dir / "v2_fixed").is_dir()


def test_run_runtime_error_creates_failed_version(runner, isolated_dir):
    _init_project(runner)
    script = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
raise ValueError("something went wrong")
show_object(result)
"""
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    assert result.exit_code == 1
    failed_dir = isolated_dir / "v1_broken_failed"
    assert failed_dir.is_dir()
    meta = json.loads((failed_dir / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert "something went wrong" in meta["error"]


def test_run_no_show_object_creates_failed_version(runner, isolated_dir):
    _init_project(runner)
    script = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
"""
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "empty"])
    assert result.exit_code == 1
    failed_dir = isolated_dir / "v1_empty_failed"
    assert failed_dir.is_dir()
    meta = json.loads((failed_dir / "meta.json").read_text())
    assert meta["status"] == "failed"


# --- Render integration tests ---


def test_run_with_render_produces_png(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--render", "iso"])
    assert result.exit_code == 0
    png = isolated_dir / "v1_label" / "renders" / "iso.png"
    assert png.exists()
    assert png.read_bytes()[:4] == b"\x89PNG"


def test_run_with_render_multiple_views(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--render", "front,iso"])
    assert result.exit_code == 0
    renders_dir = isolated_dir / "v1_label" / "renders"
    assert (renders_dir / "front.png").exists()
    assert (renders_dir / "iso.png").exists()


def test_run_with_render_all(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--render", "all"])
    assert result.exit_code == 0
    renders_dir = isolated_dir / "v1_label" / "renders"
    for name in ["front", "right", "top", "iso"]:
        assert (renders_dir / f"{name}.png").exists()


def test_run_with_render_meta_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "label", "--render", "iso"])
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "renders" in meta
    assert "iso" in meta["renders"]
    assert meta["renders"]["iso"] == "v1_label/renders/iso.png"


def test_run_with_render_json_response(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--render", "iso"])
    parsed = json.loads(result.output)
    assert "renders" in parsed
    assert parsed["renders"]["iso"] == "v1_label/renders/iso.png"


def test_run_without_render_no_renders_key(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label"])
    parsed = json.loads(result.output)
    assert "renders" not in parsed
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "renders" not in meta


# --- Export integration tests ---


def test_run_with_export_stl(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "stl"])
    assert result.exit_code == 0
    stl = isolated_dir / "v1_label" / "output.stl"
    assert stl.exists()
    assert stl.stat().st_size > 0


def test_run_with_export_glb(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "glb"])
    assert result.exit_code == 0
    glb = isolated_dir / "v1_label" / "output.glb"
    assert glb.exists()
    assert glb.read_bytes()[:4] == b"glTF"


def test_run_with_export_multiple(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "stl,glb"])
    assert result.exit_code == 0
    assert (isolated_dir / "v1_label" / "output.stl").exists()
    assert (isolated_dir / "v1_label" / "output.glb").exists()


def test_run_with_export_and_render(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "glb", "--render", "iso"])
    assert result.exit_code == 0
    assert (isolated_dir / "v1_label" / "output.glb").exists()
    assert (isolated_dir / "v1_label" / "renders" / "iso.png").exists()


def test_run_with_export_meta_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "stl,glb"])
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert meta["outputs"]["stl"] == "v1_label/output.stl"
    assert meta["outputs"]["glb"] == "v1_label/output.glb"
    assert meta["outputs"]["step"] == "v1_label/output.step"


def test_run_with_export_json_response(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "stl,glb"])
    parsed = json.loads(result.output)
    assert parsed["outputs"]["stl"] == "v1_label/output.stl"
    assert parsed["outputs"]["glb"] == "v1_label/output.glb"
    assert parsed["outputs"]["step"] == "v1_label/output.step"


def test_run_without_export_no_extra_outputs(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label"])
    parsed = json.loads(result.output)
    assert "stl" not in parsed["outputs"]
    assert "glb" not in parsed["outputs"]
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "stl" not in meta["outputs"]
    assert "glb" not in meta["outputs"]


def test_run_with_export_obj(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "obj"])
    assert result.exit_code == 0
    obj = isolated_dir / "v1_label" / "output.obj"
    assert obj.exists()
    assert obj.stat().st_size > 0


# --- End-to-end workflow test ---


def test_end_to_end_workflow(runner, isolated_dir):
    """Full workflow: init -> run -> render -> export -> context -> diff."""
    # 1. init
    r = runner.invoke(cli, ["init", "--name", "e2e_test"])
    assert r.exit_code == 0
    assert json.loads(r.output)["status"] == "success"

    # 2. run
    _write_script(isolated_dir)
    r = runner.invoke(cli, ["run", "script.py", "--output", "box"])
    assert r.exit_code == 0
    parsed = json.loads(r.output)
    assert parsed["status"] == "success"
    assert parsed["version"] == 1
    step_path = isolated_dir / "v1_box" / "output.step"
    assert step_path.exists()

    # 3. render
    r = runner.invoke(cli, ["render", str(step_path), "--view", "iso"])
    assert r.exit_code == 0
    assert json.loads(r.output)["status"] == "success"

    # 4. export
    r = runner.invoke(cli, ["export", str(step_path), "--format", "stl,glb,obj"])
    assert r.exit_code == 0
    assert json.loads(r.output)["status"] == "success"

    # 5. run a second version
    r = runner.invoke(cli, ["run", "script.py", "--output", "box2"])
    assert r.exit_code == 0
    assert json.loads(r.output)["version"] == 2

    # 6. context
    r = runner.invoke(cli, ["context"])
    assert r.exit_code == 0
    ctx = json.loads(r.output)
    assert ctx["status"] == "success"
    assert ctx["current"] == "box2"

    # 7. diff
    r = runner.invoke(cli, ["diff", "1", "2"])
    assert r.exit_code == 0
    assert json.loads(r.output)["status"] == "success"
