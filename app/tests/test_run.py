import json
import shutil
import struct
from pathlib import Path

from agentcad.cli import cli
from agentcad.manifest import MANIFEST_FILE


SIMPLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""

PARAMETRIC_SCRIPT = """\
length = 50.0
width = 20.0
height = 10.0
result = cq.Workplane("XY").box(length, width, height)
show_object(result)
"""

MULTI_SHOW_SCRIPT = """\
box = cq.Workplane("XY").box(10, 10, 10)
show_object(box)
cyl = cq.Workplane("XY").workplane(offset=20).circle(5).extrude(10)
show_object(cyl)
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
    assert "agentcad.json" in parsed["message"]


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


def test_run_syntax_error_caught_by_validation(runner, isolated_dir):
    """Syntax errors are now caught by validation — no version consumed, no disk artifacts."""
    _init_project(runner)
    _write_script(isolated_dir, content="this is not valid python(")
    result = runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["status"] == "validation_error"
    assert any(c["check"] == "syntax_error" for c in parsed["checks"])
    # No version directory created
    assert not (isolated_dir / "v1_broken_failed").is_dir()
    # Manifest unchanged
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 0


def test_run_validation_error_json_response(runner, isolated_dir):
    """Syntax errors return validation_error with checks array."""
    _init_project(runner)
    _write_script(isolated_dir, content="this is not valid python(")
    result = runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    parsed = json.loads(result.output)
    assert parsed["command"] == "run"
    assert parsed["status"] == "validation_error"
    assert "checks" in parsed
    assert "version" not in parsed


def test_run_failed_does_not_advance_current(runner, isolated_dir):
    _init_project(runner)
    # Successful v1
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "good"])
    # Runtime error v2 (passes validation, fails at execution)
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'raise ValueError("boom")\n'
        'show_object(cq.Workplane("XY").box(1,1,1))\n'
    ))
    runner.invoke(cli, ["run", "script.py", "--output", "bad"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert manifest["current"] == "good"


def test_run_validation_error_does_not_consume_version(runner, isolated_dir):
    """Validation errors don't consume a version number."""
    _init_project(runner)
    # Validation error (syntax)
    _write_script(isolated_dir, content="bad(")
    runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    # Next successful run is v1, not v2
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "fixed"])
    parsed = json.loads(result.output)
    assert parsed["version"] == 1
    assert (isolated_dir / "v1_fixed").is_dir()


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


def test_run_no_show_object_caught_by_validation(runner, isolated_dir):
    """Missing show_object is now caught by validation — no version consumed."""
    _init_project(runner)
    script = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
"""
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "empty"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["status"] == "validation_error"
    assert any(c["check"] == "show_object_missing" for c in parsed["checks"])
    # No version directory
    assert not (isolated_dir / "v1_empty_failed").is_dir()
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 0


# --- M24: Custom angles in --render ---


def test_run_render_custom_angle(runner, isolated_dir):
    """agentcad run --render 45:30 should produce a custom-angle render."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--render", "45:30"])
    assert result.exit_code == 0
    png = isolated_dir / "v1_label" / "renders" / "45_30.png"
    assert png.exists()
    assert png.read_bytes()[:4] == b"\x89PNG"


def test_run_render_mixed_named_and_angle(runner, isolated_dir):
    """agentcad run --render front,45:30 should render both."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--render", "front,45:30"])
    assert result.exit_code == 0
    renders_dir = isolated_dir / "v1_label" / "renders"
    assert (renders_dir / "front.png").exists()
    assert (renders_dir / "45_30.png").exists()


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


# --- Metrics integration tests ---


def test_run_success_includes_metrics(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "metrics" in parsed
    m = parsed["metrics"]
    assert "bounding_box" in m
    assert "volume" in m
    assert "surface_area" in m
    assert "face_count" in m
    assert "edge_count" in m
    assert "is_valid" in m
    # 10x10x10 box
    assert m["volume"] > 900
    assert m["face_count"] == 6


def test_run_meta_json_includes_metrics(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    meta = json.loads((isolated_dir / "v1" / "meta.json").read_text())
    assert "metrics" in meta
    assert meta["metrics"]["face_count"] == 6


def test_run_failed_no_metrics(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content="bad(")
    result = runner.invoke(cli, ["run", "script.py", "--output", "broken"])
    parsed = json.loads(result.output)
    assert "metrics" not in parsed


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


# --- Multiple show_object() tests ---


def test_run_multiple_show_object_produces_compound(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    assert result.exit_code == 0
    step = isolated_dir / "v1_multi" / "output.step"
    assert step.exists()
    assert step.stat().st_size > 0


def test_run_multiple_show_object_warning_in_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    parsed = json.loads(result.output)
    assert "warnings" in parsed
    assert any("show_object()" in w for w in parsed["warnings"])


def test_run_multiple_show_object_warning_in_meta(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    meta = json.loads((isolated_dir / "v1_multi" / "meta.json").read_text())
    assert "warnings" in meta


def test_run_multiple_show_object_metrics_cover_both(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    parsed = json.loads(result.output)
    m = parsed["metrics"]
    # Box is 10x10x10 at origin (z: -5 to 5), cyl at offset 20 extends to z=30
    z_extent = m["dimensions"]["z"]
    assert z_extent > 25  # must cover both shapes


def test_run_single_show_object_no_warning(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    parsed = json.loads(result.output)
    assert "warnings" not in parsed


def test_run_multiple_show_object_with_render(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi", "--render", "iso"])
    assert result.exit_code == 0
    png = isolated_dir / "v1_multi" / "renders" / "iso.png"
    assert png.exists()
    assert png.read_bytes()[:4] == b"\x89PNG"


def test_run_multiple_show_object_with_export(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi", "--export", "stl"])
    assert result.exit_code == 0
    stl = isolated_dir / "v1_multi" / "output.stl"
    assert stl.exists()
    assert stl.stat().st_size > 0


# --- Preview integration tests ---


def test_run_preview_produces_png(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--preview"])
    assert result.exit_code == 0
    png = isolated_dir / "v1_label" / "preview.png"
    assert png.exists()
    assert png.read_bytes()[:4] == b"\x89PNG"


def test_run_preview_json_response(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--preview"])
    parsed = json.loads(result.output)
    assert "preview" in parsed
    assert parsed["preview"] == "v1_label/preview.png"


def test_run_preview_meta_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "label", "--preview"])
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "preview" in meta
    assert meta["preview"] == "v1_label/preview.png"


def test_run_preview_not_in_renders(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "label", "--preview"])
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "renders" not in meta


def test_run_preview_with_render_coexist(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--preview", "--render", "iso"])
    assert result.exit_code == 0
    assert (isolated_dir / "v1_label" / "preview.png").exists()
    assert (isolated_dir / "v1_label" / "renders" / "iso.png").exists()


def test_run_without_preview_no_preview_key(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label"])
    parsed = json.loads(result.output)
    assert "preview" not in parsed
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "preview" not in meta


def test_run_export_glb_multi_show_object_has_materials(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi", "--export", "glb"])
    assert result.exit_code == 0
    glb_path = isolated_dir / "v1_multi" / "output.glb"
    assert glb_path.exists()
    data = glb_path.read_bytes()
    json_length = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + json_length])
    assert len(gltf["materials"]) >= 2


def test_run_preview_is_256x256(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "label", "--preview"])
    png = isolated_dir / "v1_label" / "preview.png"
    data = png.read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    assert width == 256
    assert height == 256


# --- Parametric script tests (M21) ---


def test_run_params_override_changes_output(runner, isolated_dir):
    """Overriding length from 50 to 100 changes the bounding box."""
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    r1 = runner.invoke(cli, ["run", "script.py", "--output", "default"])
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    r2 = runner.invoke(cli, ["run", "script.py", "--output", "big", "--params", "length=100"])
    m1 = json.loads(r1.output)["metrics"]
    m2 = json.loads(r2.output)["metrics"]
    assert m2["dimensions"]["x"] > m1["dimensions"]["x"]


def test_run_params_in_json_response(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "length=100"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"
    assert "params" in parsed
    assert parsed["params"]["length"] == 100.0


def test_run_params_in_meta_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "length=100"])
    meta = json.loads((isolated_dir / "v1_p" / "meta.json").read_text())
    assert "params" in meta
    assert meta["params"]["length"] == 100.0


def test_run_params_multiple_values(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "length=60,width=30"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"
    assert parsed["params"]["length"] == 60.0
    assert parsed["params"]["width"] == 30.0


def test_run_params_int_preserved(runner, isolated_dir):
    """Integer values stay as int, not float."""
    _init_project(runner)
    script = "count = 5\nresult = cq.Workplane('XY').box(count, count, count)\nshow_object(result)\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "count=10"])
    parsed = json.loads(result.output)
    assert parsed["params"]["count"] == 10
    assert isinstance(parsed["params"]["count"], int)


def test_run_params_float_coercion(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "length=0.5"])
    parsed = json.loads(result.output)
    assert parsed["params"]["length"] == 0.5
    assert isinstance(parsed["params"]["length"], float)


def test_run_params_string_value(runner, isolated_dir):
    _init_project(runner)
    script = "label = 'default'\nresult = cq.Workplane('XY').box(10, 10, 10)\nshow_object(result)\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "label=test"])
    parsed = json.loads(result.output)
    assert parsed["params"]["label"] == "test"


def test_run_params_bool_true(runner, isolated_dir):
    _init_project(runner)
    script = "smooth = False\nresult = cq.Workplane('XY').box(10, 10, 10)\nshow_object(result)\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "smooth=true"])
    parsed = json.loads(result.output)
    assert parsed["params"]["smooth"] is True


def test_run_params_unknown_parameter_error(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "nonexistent=5"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["status"] == "error"
    assert "nonexistent" in parsed["message"]
    # Should list available params to help the agent
    assert "length" in parsed["message"]


def test_run_params_bad_format_error(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "badformat"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["status"] == "error"


def test_run_without_params_no_params_key(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    parsed = json.loads(result.output)
    assert "params" not in parsed
    meta = json.loads((isolated_dir / "v1" / "meta.json").read_text())
    assert "params" not in meta


# --- Python version check tests (M22) ---


def test_run_python_version_too_new_error(runner, isolated_dir, monkeypatch):
    """Python 3.13+ returns clear error, no version consumed."""
    _init_project(runner)
    _write_script(isolated_dir)
    monkeypatch.setattr("sys.version_info", (3, 14, 0, "final", 0))
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["status"] == "error"
    assert "3.10" in parsed["message"]
    assert "3.14" in parsed["message"]


def test_run_python_version_ok_no_error(runner, isolated_dir, monkeypatch):
    """Python 3.12 does not trigger version error."""
    _init_project(runner)
    _write_script(isolated_dir)
    monkeypatch.setattr("sys.version_info", (3, 12, 0, "final", 0))
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


# --- Dry-run tests (M22) ---


def test_run_dry_run_returns_metrics(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1", "--dry-run"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"
    assert "metrics" in parsed
    assert parsed["metrics"]["face_count"] == 6


def test_run_dry_run_no_version_consumed(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1", "--dry-run"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert len(manifest["versions"]) == 0
    # No version directory created
    assert not (isolated_dir / "v1").is_dir()
    assert not (isolated_dir / "v1_v1").is_dir()


def test_run_dry_run_no_disk_artifacts(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1", "--dry-run"])
    # Only agentcad.json and script.py should exist
    files = [f.name for f in isolated_dir.iterdir()]
    assert "agentcad.json" in files
    assert "script.py" in files
    assert not any(f.startswith("v1") for f in files)


# --- M24: Version directory collision ---


def test_run_existing_version_dir_no_crash(runner, isolated_dir):
    """Pre-existing version directory should not cause a crash."""
    _init_project(runner)
    _write_script(isolated_dir)
    # Create the directory that run would create
    (isolated_dir / "v1_label").mkdir()
    result = runner.invoke(cli, ["run", "script.py", "--output", "label"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"


# --- Daemon "via" field tests ---


def test_run_via_daemon_field_in_output(runner, isolated_dir, monkeypatch):
    """When routed through daemon, output JSON includes 'via': 'daemon'."""
    _init_project(runner)
    _write_script(isolated_dir)
    # Enable daemon routing (undo the autouse _no_daemon fixture)
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    # Simulate daemon returning a successful result
    daemon_output = json.dumps({"command": "run", "status": "success", "version": 1})
    monkeypatch.setattr(
        "agentcad.commands.run.send_request",
        lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
    )
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["via"] == "daemon"


def test_run_direct_no_via_field(runner, isolated_dir):
    """Direct execution (no daemon) should not include 'via' field."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "via" not in parsed


# --- M36: Validity warnings & diagnostics ---

def _fake_metrics_invalid(real_compute):
    """Wrap compute_metrics to force is_valid=False."""
    def wrapper(topo_shape):
        m = real_compute(topo_shape)
        m["is_valid"] = False
        m["validity_errors"] = ["BRepCheck_InvalidToleranceValue"]
        return m
    return wrapper


def test_run_invalid_shape_has_warnings_in_output(runner, isolated_dir, monkeypatch):
    """is_valid: false in metrics should produce top-level warnings."""
    _init_project(runner)
    _write_script(isolated_dir)
    from agentcad import metrics
    monkeypatch.setattr(
        "agentcad.metrics.compute_metrics",
        _fake_metrics_invalid(metrics.compute_metrics),
    )
    result = runner.invoke(cli, ["run", "script.py", "--output", "inv"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"
    assert "warnings" in parsed
    assert any("invalid geometry" in w.lower() for w in parsed["warnings"])


def test_run_invalid_shape_warnings_in_meta(runner, isolated_dir, monkeypatch):
    """is_valid: false should also appear in meta.json warnings."""
    _init_project(runner)
    _write_script(isolated_dir)
    from agentcad import metrics
    monkeypatch.setattr(
        "agentcad.metrics.compute_metrics",
        _fake_metrics_invalid(metrics.compute_metrics),
    )
    runner.invoke(cli, ["run", "script.py", "--output", "inv"])
    meta = json.loads((isolated_dir / "v1_inv" / "meta.json").read_text())
    assert "warnings" in meta
    assert any("invalid geometry" in w.lower() for w in meta["warnings"])


def test_run_invalid_shape_dry_run_has_warnings(runner, isolated_dir, monkeypatch):
    """Dry-run should also surface validity warnings."""
    _init_project(runner)
    _write_script(isolated_dir)
    from agentcad import metrics
    monkeypatch.setattr(
        "agentcad.metrics.compute_metrics",
        _fake_metrics_invalid(metrics.compute_metrics),
    )
    result = runner.invoke(cli, ["run", "script.py", "--output", "inv", "--dry-run"])
    parsed = json.loads(result.output)
    assert "warnings" in parsed
    assert any("invalid geometry" in w.lower() for w in parsed["warnings"])


def test_run_valid_shape_no_warnings(runner, isolated_dir):
    """Valid shapes should not produce warnings."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    parsed = json.loads(result.output)
    assert "warnings" not in parsed


def test_run_negative_volume_warning_surfaces(runner, isolated_dir, monkeypatch):
    """Negative volume warning from metrics should appear in top-level warnings."""
    _init_project(runner)
    _write_script(isolated_dir)
    from agentcad import metrics
    real = metrics.compute_metrics
    def fake(topo_shape):
        m = real(topo_shape)
        m["volume"] = -1000.0
        m["warnings"] = ["Negative volume detected — check winding order."]
        return m
    monkeypatch.setattr("agentcad.metrics.compute_metrics", fake)
    result = runner.invoke(cli, ["run", "script.py", "--output", "neg"])
    parsed = json.loads(result.output)
    assert "warnings" in parsed
    assert any("negative volume" in w.lower() for w in parsed["warnings"])


def test_run_multiple_show_object_uses_warnings_list(runner, isolated_dir):
    """Multi show_object warning should use 'warnings' list, not 'warning' string."""
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    parsed = json.loads(result.output)
    assert "warnings" in parsed
    assert isinstance(parsed["warnings"], list)
    assert any("show_object()" in w for w in parsed["warnings"])


def test_run_brep_api_error_enriched(runner, isolated_dir, monkeypatch):
    """BRep_API errors should include wire closure guidance."""
    _init_project(runner)
    _write_script(isolated_dir)
    from cadquery import cqgi
    def fake_build(self, **kwargs):
        raise RuntimeError("BRep_API: command not done")
    monkeypatch.setattr(cqgi.CQModel, "build", fake_build)
    result = runner.invoke(cli, ["run", "script.py", "--output", "brep"])
    parsed = json.loads(result.output)
    assert parsed["status"] == "failed"
    assert "wire" in parsed["error"].lower()
