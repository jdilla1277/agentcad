import json
import shutil
import struct
import time
from pathlib import Path

from agentcad.cli import cli
from agentcad.manifest import MANIFEST_FILE


SIMPLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""

# Test fixtures pin cadquery via explicit import. Post-Phase-6, scripts
# without an import default to build123d via dispatch, and `cq` is no
# longer auto-injected — these tests need cq specifically (they're
# cq integration tests). The b3d twins live in
# `tests_b3d/test_run_end_to_end.py`.
PARAMETRIC_SCRIPT = """\
import cadquery as cq
length = 50.0
width = 20.0
height = 10.0
result = cq.Workplane("XY").box(length, width, height)
show_object(result)
"""

MULTI_SHOW_SCRIPT = """\
import cadquery as cq
box = cq.Workplane("XY").box(10, 10, 10)
show_object(box)
cyl = cq.Workplane("XY").workplane(offset=20).circle(5).extrude(10)
show_object(cyl)
"""


NAMED_PARTS_SCRIPT = """\
import cadquery as cq
deck = cq.Workplane("XY").box(10, 10, 2)
pin = cq.Workplane("XY").circle(1).extrude(5).translate((3, 0, 0))
arm = cq.Workplane("XY").box(8, 2, 1).translate((0, 5, 0))
show_object(deck, name="deck", options={"color": "gray"})
show_object(pin, name="pin", options={"color": "blue"})
show_object(arm, name="arm", options={"color": "red"})
"""


PARTIAL_NAMED_PARTS_SCRIPT = """\
import cadquery as cq
deck = cq.Workplane("XY").box(10, 10, 2)
pin = cq.Workplane("XY").circle(1).extrude(5).translate((3, 0, 0))
arm = cq.Workplane("XY").box(8, 2, 1).translate((0, 5, 0))
show_object(deck, name="deck")
show_object(pin)
show_object(arm, name="arm")
"""


GROUPED_PARTS_SCRIPT = """\
import cadquery as cq
base = cq.Workplane("XY").box(20, 10, 2)
rib = cq.Workplane("XY").box(3, 14, 4).translate((0, 0, 3))
pin = cq.Workplane("XY").circle(1).extrude(5).translate((8, 0, 0))
show_object(base, id="base_plate", name="Base Plate", options={
    "part_of": "frame", "group_color": "steelblue"
})
show_object(rib, id="center_rib", name="Center Rib", options={
    "part_of": "frame", "group_color": "steelblue"
})
show_object(pin, id="locator_pin", name="Locator Pin", options={"color": "coral"})
"""

GROUP_COLOR_CONFLICTS_SCRIPT = """\
import cadquery as cq
base = cq.Workplane("XY").box(20, 10, 2)
rib = cq.Workplane("XY").box(3, 14, 4).translate((0, 0, 3))
pin = cq.Workplane("XY").circle(1).extrude(5).translate((8, 0, 0))
show_object(base, id="base_plate", name="Base Plate", options={
    "part_of": "frame", "group_color": "steelblue"
})
show_object(rib, id="center_rib", name="Center Rib", options={
    "part_of": "frame", "group_color": "seagreen"
})
show_object(pin, id="locator_pin", name="Locator Pin", options={"group_color": "gold"})
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
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    assert "agentcad.json" in parsed["message"]


def test_run_script_not_found_error(runner, isolated_dir):
    _init_project(runner)
    result = runner.invoke(cli, ["run", "missing.py", "--output", "v1"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    assert "missing.py" in parsed["message"]


def test_run_rejects_unsupported_export_format(runner, isolated_dir):
    """`run --export ply` must reject the unknown format the way
    `agentcad export` does — before allocating a version or writing artifacts —
    instead of succeeding with no ply output. Rejection happens before any
    CAD kernel import, so this runs without cadquery/build123d installed."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(
        cli, ["run", "script.py", "--output", "test", "--export", "ply"]
    )
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    assert "ply" in parsed["message"]
    assert "stl, glb, obj" in parsed["message"]
    # No version consumed and no artifacts written.
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert manifest["versions"] == []
    assert not (isolated_dir / "v1_test").exists()
    assert not (isolated_dir / "test").exists()


def test_run_reports_every_unsupported_export_format(runner, isolated_dir):
    """A mixed list names each invalid format; the valid one (stl) is not
    flagged, and an empty trailing entry (stl,) is trimmed, not treated as
    an unknown format."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(
        cli, ["run", "script.py", "--output", "test", "--export", "stl,ply,fbx"]
    )
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"
    unsupported = parsed["message"].split("Supported:")[0]
    assert "ply" in unsupported
    assert "fbx" in unsupported
    assert "stl" not in unsupported


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
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "run"
    assert parsed["status"] == "success"
    assert parsed["version"] == 1
    assert parsed["label"] == "v1"
    assert "step" in parsed["outputs"]
    assert "script" in parsed["outputs"]
    assert "glb" not in parsed["outputs"]
    assert parsed["viewer"] == "v1/viewer.html"
    assert parsed["viewer_glb"] == "v1/output.glb"


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
    assert "glb" not in meta["outputs"]
    assert meta["viewer"] == "v1/viewer.html"
    assert meta["viewer_glb"] == "v1/output.glb"


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
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
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


def test_run_timeout_during_script_execution_reports_phase(
    runner, isolated_dir, monkeypatch
):
    _init_project(runner)
    _write_script(isolated_dir)

    from agentcad.runners import cadquery as cq_runner

    def slow_execute(*_args, **_kwargs):
        time.sleep(1)

    monkeypatch.setenv("AGENTCAD_RUN_TIMEOUT_S", "0.1")
    monkeypatch.setattr(cq_runner, "validate", lambda _source: [])
    monkeypatch.setattr(cq_runner, "execute", slow_execute)

    result = runner.invoke(
        cli, ["run", "script.py", "--output", "slow", "--no-preview", "--no-daemon"]
    )

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert parsed["error_kind"] == "timeout"
    assert parsed["timeout_phase"] == "script_exec"
    assert parsed["completed_phases"] == ["validation"]
    assert "validation_ms" in parsed["phase_timings"]
    assert "script" in parsed["suggestion"].lower()


def test_run_timeout_during_step_export_reports_completed_phases(
    runner, isolated_dir, monkeypatch
):
    _init_project(runner)
    _write_script(isolated_dir)

    # Warm the CAD imports and first version with the watchdog disabled so the
    # short test timeout below is only exercising the post-script export phase.
    monkeypatch.setenv("AGENTCAD_RUN_TIMEOUT_S", "0")
    warmup = runner.invoke(
        cli, ["run", "script.py", "--output", "warmup", "--no-preview", "--no-daemon"]
    )
    assert warmup.exit_code == 0, warmup.output

    from agentcad.runners import cadquery as cq_runner

    def slow_export_step(*_args, **_kwargs):
        time.sleep(1)

    monkeypatch.setenv("AGENTCAD_RUN_TIMEOUT_S", "0.1")
    monkeypatch.setattr(cq_runner, "export_step", slow_export_step)

    result = runner.invoke(
        cli, ["run", "script.py", "--output", "slow_export", "--no-preview", "--no-daemon"]
    )

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert parsed["error_kind"] == "timeout"
    assert parsed["timeout_phase"] == "export_step"
    assert parsed["completed_phases"][-2:] == ["script_exec", "metrics"]
    assert "script_exec_ms" in parsed["phase_timings"]
    assert "metrics_ms" in parsed["phase_timings"]
    assert "export" in parsed["suggestion"].lower()


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
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
    assert "renders" in parsed
    assert parsed["renders"]["iso"] == "v1_label/renders/iso.png"


def test_run_without_render_no_renders_key(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label"])
    parsed = json.loads(result.stdout)
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
    assert meta["viewer_glb"] == "v1_label/output.glb"


def test_run_with_export_json_response(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--export", "stl,glb"])
    parsed = json.loads(result.stdout)
    assert parsed["outputs"]["stl"] == "v1_label/output.stl"
    assert parsed["outputs"]["glb"] == "v1_label/output.glb"
    assert parsed["outputs"]["step"] == "v1_label/output.step"
    assert parsed["viewer_glb"] == "v1_label/output.glb"


def test_run_without_export_no_extra_outputs(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label"])
    parsed = json.loads(result.stdout)
    assert "stl" not in parsed["outputs"]
    assert "glb" not in parsed["outputs"]
    assert parsed["viewer_glb"] == "v1_label/output.glb"
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "stl" not in meta["outputs"]
    assert "glb" not in meta["outputs"]


# --- Metrics integration tests ---


def test_run_success_includes_metrics(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
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
    assert json.loads(r.stdout)["status"] == "success"

    # 2. run
    _write_script(isolated_dir)
    r = runner.invoke(cli, ["run", "script.py", "--output", "box"])
    assert r.exit_code == 0
    parsed = json.loads(r.stdout)
    assert parsed["status"] == "success"
    assert parsed["version"] == 1
    step_path = isolated_dir / "v1_box" / "output.step"
    assert step_path.exists()

    # 3. render
    r = runner.invoke(cli, ["render", str(step_path), "--view", "iso"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["status"] == "success"

    # 4. export
    r = runner.invoke(cli, ["export", str(step_path), "--format", "stl,glb,obj"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["status"] == "success"

    # 5. run a second version
    r = runner.invoke(cli, ["run", "script.py", "--output", "box2"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["version"] == 2

    # 6. context
    r = runner.invoke(cli, ["context"])
    assert r.exit_code == 0
    ctx = json.loads(r.stdout)
    assert ctx["status"] == "success"
    assert ctx["current"] == "box2"

    # 7. diff
    r = runner.invoke(cli, ["diff", "1", "2"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["status"] == "success"


# --- Multiple show_object() tests ---


def test_run_multiple_show_object_produces_compound(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    assert result.exit_code == 0
    step = isolated_dir / "v1_multi" / "output.step"
    assert step.exists()
    assert step.stat().st_size > 0


def test_run_multiple_show_object_no_compound_warning(runner, isolated_dir):
    """Multi-show_object runs don't emit the old 'combined into compound' warning.
    The parts[] array carries the per-part breakdown now, so suggesting the
    user call makeCompound() would actively lose information."""
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    parsed = json.loads(result.stdout)
    for w in parsed.get("warnings", []):
        assert "combined into a single compound" not in w


def test_run_multiple_show_object_meta_has_parts_not_warning(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    meta = json.loads((isolated_dir / "v1_multi" / "meta.json").read_text())
    assert "parts" in meta and len(meta["parts"]) == 2
    for w in meta.get("warnings", []):
        assert "combined into a single compound" not in w


def test_run_multiple_show_object_metrics_cover_both(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    parsed = json.loads(result.stdout)
    m = parsed["metrics"]
    # Box is 10x10x10 at origin (z: -5 to 5), cyl at offset 20 extends to z=30
    z_extent = m["dimensions"]["z"]
    assert z_extent > 25  # must cover both shapes


def test_run_single_show_object_no_warning(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
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


def test_run_default_includes_preview(runner, isolated_dir):
    """Preview is on by default — every successful run produces a preview PNG."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label"])
    parsed = json.loads(result.stdout)
    assert parsed["preview"] == "v1_label/preview.png"
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert meta["preview"] == "v1_label/preview.png"
    assert (isolated_dir / "v1_label" / "preview.png").exists()


def test_run_no_preview_flag_suppresses_preview(runner, isolated_dir):
    """--no-preview suppresses the default preview PNG."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--no-preview"])
    parsed = json.loads(result.stdout)
    assert "preview" not in parsed
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert "preview" not in meta
    assert not (isolated_dir / "v1_label" / "preview.png").exists()


def test_run_no_preview_still_writes_viewer_and_glb(runner, isolated_dir):
    """--no-preview only skips the 4-view composite PNG. The viewer.html and
    output.glb are still produced because they are cheap (~ms / sub-second)
    and the agent has no reason to opt out of them. This is the fix for the
    'viewer only loads one version' problem: the viewer no longer rides
    along with the same flag that gates the slow composite render."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--no-preview"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["viewer"] == "v1_label/viewer.html"
    assert parsed["viewer_glb"] == "v1_label/output.glb"
    assert (isolated_dir / "v1_label" / "viewer.html").exists()
    assert (isolated_dir / "v1_label" / "output.glb").exists()
    meta = json.loads((isolated_dir / "v1_label" / "meta.json").read_text())
    assert meta["viewer"] == "v1_label/viewer.html"
    assert meta["viewer_glb"] == "v1_label/output.glb"
    assert "glb" not in parsed["outputs"]
    assert "glb" not in meta["outputs"]


def test_run_no_preview_second_run_still_writes_diff(runner, isolated_dir):
    """Auto-diff against the prior successful version runs regardless of
    --preview. The diff PNGs are what the viewer uses for comparison —
    they have to be present so 'open the viewer' always works."""
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "first", "--no-preview"])
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'result = cq.Workplane("XY").box(20, 20, 20)\n'
        'show_object(result)\n'
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "second", "--no-preview"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert "diff" in parsed
    assert parsed["diff"]["against"] == "first"
    assert (isolated_dir / "v2_second" / "diff_side.png").exists()
    assert (isolated_dir / "v2_second" / "diff_overlay.png").exists()


def test_run_no_preview_second_run_viewer_includes_prior(runner, isolated_dir):
    """The viewer for a --no-preview run #2 still embeds the prior version's
    GLB so side-by-side comparison works. This is the actual user-facing
    fix: open the viewer at any version, see the previous version next to it."""
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "first", "--no-preview"])
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'result = cq.Workplane("XY").box(20, 20, 20)\n'
        'show_object(result)\n'
    ))
    runner.invoke(cli, ["run", "script.py", "--output", "second", "--no-preview"])
    viewer_html = (isolated_dir / "v2_second" / "viewer.html").read_text()
    assert 'DEFAULT_MODE = "side-by-side"' in viewer_html


def test_run_dry_run_skips_preview(runner, isolated_dir):
    """--dry-run must not produce any disk artifacts, including a preview."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "label", "--dry-run"])
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert "preview" not in parsed
    # Dry-run: no version dir at all
    assert not (isolated_dir / "v1_label").exists()


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


def test_run_viewer_glb_uses_requested_part_colors(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=NAMED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "colored", "--no-preview"])
    assert result.exit_code == 0, result.output

    data = (isolated_dir / "v1_colored" / "output.glb").read_bytes()
    json_length = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + json_length])
    colors = [
        tuple(m["pbrMetallicRoughness"]["baseColorFactor"])
        for m in gltf["materials"]
    ]
    node_names = [n.get("name") for n in gltf["nodes"]]

    assert set(node_names) >= {"deck", "pin", "arm"}
    assert (0.50, 0.50, 0.50, 1.0) in colors
    assert (0.0, 0.0, 1.0, 1.0) in colors
    assert (1.0, 0.0, 0.0, 1.0) in colors


def test_run_preview_is_4view_composite_1024(runner, isolated_dir):
    """Preview is now a 2x2 composite of front/right/top/iso, 512px per quadrant."""
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "label", "--preview"])
    png = isolated_dir / "v1_label" / "preview.png"
    data = png.read_bytes()
    width, height = struct.unpack(">II", data[16:24])
    # 2 panels wide × 512px = 1024, plus label bars stacked (2 × (512 + 22) = 1068)
    assert width == 1024
    assert height == 1068


def test_run_auto_diff_png_when_prior_success(runner, isolated_dir):
    """From v2 onward, run produces diff_side.png + diff_overlay.png against prev version."""
    _init_project(runner)
    _write_script(isolated_dir)
    # v1: success, no diff yet (first version)
    r1 = runner.invoke(cli, ["run", "script.py", "--output", "first"])
    p1 = json.loads(r1.stdout)
    assert "diff" not in p1  # no prior

    # v2: different geometry, should produce diff
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'result = cq.Workplane("XY").box(20, 20, 20)\n'
        'show_object(result)\n'
    ))
    r2 = runner.invoke(cli, ["run", "script.py", "--output", "second"])
    p2 = json.loads(r2.stdout)
    assert "diff" in p2
    assert p2["diff"]["against"] == "first"
    assert p2["diff"]["side_by_side"] == "v2_second/diff_side.png"
    assert p2["diff"]["overlay"] == "v2_second/diff_overlay.png"
    assert (isolated_dir / "v2_second" / "diff_side.png").exists()
    assert (isolated_dir / "v2_second" / "diff_overlay.png").exists()


def test_run_auto_diff_skips_failed_versions(runner, isolated_dir):
    """Auto-diff picks the most recent SUCCESSFUL prior, skipping failed runs in between."""
    _init_project(runner)
    # v1: success
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "good"])
    # v2: runtime error (failed version — no output.step in the failed dir)
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'raise ValueError("boom")\n'
        'show_object(cq.Workplane("XY").box(1,1,1))\n'
    ))
    runner.invoke(cli, ["run", "script.py", "--output", "bad"])
    # v3: success again — should diff against v1 (the last success), not v2 (failed)
    _write_script(isolated_dir, content=(
        'import cadquery as cq\n'
        'result = cq.Workplane("XY").box(30, 30, 30)\n'
        'show_object(result)\n'
    ))
    r3 = runner.invoke(cli, ["run", "script.py", "--output", "recovered"])
    p3 = json.loads(r3.stdout)
    assert "diff" in p3
    assert p3["diff"]["against"] == "good"  # skipped "bad"


# --- Parametric script tests (M21) ---


def test_run_params_override_changes_output(runner, isolated_dir):
    """Overriding length from 50 to 100 changes the bounding box."""
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    r1 = runner.invoke(cli, ["run", "script.py", "--output", "default"])
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    r2 = runner.invoke(cli, ["run", "script.py", "--output", "big", "--params", "length=100"])
    m1 = json.loads(r1.stdout)["metrics"]
    m2 = json.loads(r2.stdout)["metrics"]
    assert m2["dimensions"]["x"] > m1["dimensions"]["x"]


def test_run_params_in_json_response(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "length=100"])
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["params"]["length"] == 60.0
    assert parsed["params"]["width"] == 30.0


def test_run_params_int_preserved(runner, isolated_dir):
    """Integer values stay as int, not float."""
    _init_project(runner)
    script = "import cadquery as cq\ncount = 5\nresult = cq.Workplane('XY').box(count, count, count)\nshow_object(result)\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "count=10"])
    parsed = json.loads(result.stdout)
    assert parsed["params"]["count"] == 10
    assert isinstance(parsed["params"]["count"], int)


def test_run_params_float_coercion(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "length=0.5"])
    parsed = json.loads(result.stdout)
    assert parsed["params"]["length"] == 0.5
    assert isinstance(parsed["params"]["length"], float)


def test_run_params_string_value(runner, isolated_dir):
    _init_project(runner)
    script = "import cadquery as cq\nlabel = 'default'\nresult = cq.Workplane('XY').box(10, 10, 10)\nshow_object(result)\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "label=test"])
    parsed = json.loads(result.stdout)
    assert parsed["params"]["label"] == "test"


def test_run_params_bool_true(runner, isolated_dir):
    _init_project(runner)
    script = "import cadquery as cq\nsmooth = False\nresult = cq.Workplane('XY').box(10, 10, 10)\nshow_object(result)\n"
    _write_script(isolated_dir, content=script)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "smooth=true"])
    parsed = json.loads(result.stdout)
    assert parsed["params"]["smooth"] is True


def test_run_params_unknown_parameter_error(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "nonexistent=5"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"
    assert "nonexistent" in parsed["message"]
    # Should list available params to help the agent
    assert "length" in parsed["message"]


def test_run_params_bad_format_error(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=PARAMETRIC_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "p", "--params", "badformat"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"


def test_run_without_params_no_params_key(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"


# --- Dry-run tests (M22) ---


def test_run_dry_run_returns_metrics(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1", "--dry-run"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
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
    # Issue #177 routing helper extracted to commands/_daemon_routing.py.
    # Monkeypatch the underlying daemon.send_request so all commands that
    # call into the helper see the simulated daemon.
    monkeypatch.setattr(
        "agentcad.daemon.send_request",
        lambda *a, **kw: {"type": "result", "exit_code": 0, "output": daemon_output},
    )
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["via"] == "daemon"


def test_run_direct_no_via_field(runner, isolated_dir):
    """Direct execution (no daemon) should not include 'via' field."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert "warnings" in parsed
    assert any("invalid geometry" in w.lower() for w in parsed["warnings"])


def test_run_emits_json_error_on_unexpected_exception_after_script_start(
    runner, isolated_dir, monkeypatch
):
    """Internal exceptions after the script started running must still surface
    as JSON on stdout — the docs' contract is "All output is JSON."

    The bug was first surfaced in the vase side-by-side (cq-2 trial,
    `Bnd_Box is void` from a degenerate revolve crashing compute_metrics).
    Pre-fix: stdout was empty, exit code was 1, and only stderr had any
    diagnostic (and only under --no-daemon — daemon-routed runs hid the
    traceback entirely). The agent had no JSON to parse and no recovery
    path.
    """
    _init_project(runner)
    _write_script(isolated_dir)

    def _raise_bnd_box_void(_):
        raise RuntimeError("Bnd_Box is void")

    monkeypatch.setattr("agentcad.metrics.compute_metrics", _raise_bnd_box_void)
    result = runner.invoke(cli, ["run", "script.py", "--output", "boom"])

    assert result.exit_code != 0, "expected non-zero exit on unexpected exception"
    assert result.stdout.strip(), "stdout must not be empty — contract is JSON output"
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "run"
    assert parsed["status"] == "error"
    # The error payload should help the agent — surface the exception text
    # so the agent can decide whether to retry or fix the script.
    assert "Bnd_Box is void" in (parsed.get("message", "") + parsed.get("traceback", ""))


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
    parsed = json.loads(result.stdout)
    assert "warnings" in parsed
    assert any("invalid geometry" in w.lower() for w in parsed["warnings"])


def test_run_valid_shape_no_warnings(runner, isolated_dir):
    """Valid shapes should not produce warnings."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    parsed = json.loads(result.stdout)
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
    parsed = json.loads(result.stdout)
    assert "warnings" in parsed
    assert any("negative volume" in w.lower() for w in parsed["warnings"])


def test_run_multi_show_object_warnings_key_is_list_type_if_present(runner, isolated_dir):
    """Any warnings emitted on multi-show_object should be a list, not a string."""
    _init_project(runner)
    _write_script(isolated_dir, content=MULTI_SHOW_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "multi"])
    parsed = json.loads(result.stdout)
    if "warnings" in parsed:
        assert isinstance(parsed["warnings"], list)


def test_run_brep_api_error_enriched(runner, isolated_dir, monkeypatch):
    """BRep_API errors should include wire closure guidance."""
    _init_project(runner)
    _write_script(isolated_dir)
    from cadquery import cqgi
    def fake_build(self, **kwargs):
        raise RuntimeError("BRep_API: command not done")
    monkeypatch.setattr(cqgi.CQModel, "build", fake_build)
    result = runner.invoke(cli, ["run", "script.py", "--output", "brep"])
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "failed"
    assert "wire" in parsed["error"].lower()


# ---------------------------------------------------------------------------
# Per-part metrics (feedback #190): named show_object() calls should produce
# a `parts` array with per-part id, name, color, metrics, preview, part_of.
# ---------------------------------------------------------------------------


def test_run_named_parts_emits_parts_array(runner, isolated_dir):
    """Named show_object() calls produce a parts[] array in the output JSON."""
    _init_project(runner)
    _write_script(isolated_dir, content=NAMED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "three_parts"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)

    assert "parts" in parsed, "parts key missing from run output"
    parts = parsed["parts"]
    assert len(parts) == 3

    # IDs are stable string handles derived from names when no explicit id is set.
    assert [p["id"] for p in parts] == ["deck", "pin", "arm"]
    assert [p["id_source"] for p in parts] == ["name", "name", "name"]

    # Names preserved in declaration order
    assert [p["name"] for p in parts] == ["deck", "pin", "arm"]

    # Colors preserved
    assert [p["color"] for p in parts] == ["gray", "blue", "red"]

    # part_of reserved in v1, always null
    assert all(p["part_of"] is None for p in parts)


def test_run_grouped_parts_emit_groups_and_inherit_group_color(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=GROUPED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "grouped"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)

    parts = parsed["parts"]
    assert [p["id"] for p in parts] == ["base_plate", "center_rib", "locator_pin"]
    assert [p["part_of"] for p in parts] == ["frame", "frame", None]
    assert [p["color"] for p in parts] == ["steelblue", "steelblue", "coral"]

    assert parsed["groups"] == [{
        "id": "frame",
        "name": "frame",
        "part_ids": ["base_plate", "center_rib"],
        "color": "steelblue",
    }]

    meta = json.loads((isolated_dir / "v1_grouped" / "meta.json").read_text())
    assert meta["groups"] == parsed["groups"]


def test_run_group_color_conflicts_warn_and_use_first_color(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=GROUP_COLOR_CONFLICTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "group_conflicts"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)

    assert parsed["groups"] == [{
        "id": "frame",
        "name": "frame",
        "part_ids": ["base_plate", "center_rib"],
        "color": "steelblue",
    }]
    assert [p.get("color") for p in parsed["parts"]] == ["steelblue", "steelblue", None]
    warnings = parsed.get("warnings", [])
    assert any("conflicting group_color" in w for w in warnings)
    assert any("group_color ignored for ungrouped part 'locator_pin'" in w for w in warnings)


def test_run_named_parts_have_metrics(runner, isolated_dir):
    """Each part has its own metrics dict with volume, area, CoM, bbox, counts."""
    _init_project(runner)
    _write_script(isolated_dir, content=NAMED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "parts_metrics"])
    parsed = json.loads(result.stdout)

    for p in parsed["parts"]:
        m = p["metrics"]
        for key in (
            "volume", "surface_area", "center_of_mass",
            "bounding_box", "dimensions",
            "face_count", "edge_count", "is_valid",
        ):
            assert key in m, f"missing metric {key!r} on part {p['id']}"
        assert m["volume"] > 0
        assert m["face_count"] > 0


def test_run_named_parts_preview_files_exist(runner, isolated_dir):
    """Each part has a preview PNG on disk, path returned in the JSON."""
    _init_project(runner)
    _write_script(isolated_dir, content=NAMED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "parts_preview"])
    parsed = json.loads(result.stdout)

    for p in parsed["parts"]:
        preview = p.get("preview")
        assert preview, f"part {p['id']} missing preview path"
        preview_path = isolated_dir / preview
        assert preview_path.exists(), f"preview file {preview_path} not written"
        # Smoke-check it's a non-trivial PNG.
        assert preview_path.stat().st_size > 1024
        assert preview_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_run_parts_persisted_in_meta_json(runner, isolated_dir):
    """parts[] is written to meta.json so diff and other commands can read it."""
    _init_project(runner)
    _write_script(isolated_dir, content=NAMED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "parts_meta"])
    parsed = json.loads(result.stdout)
    version_dir = Path(parsed["outputs"]["step"]).parent
    meta = json.loads((isolated_dir / version_dir / "meta.json").read_text())

    assert "parts" in meta
    assert [p["name"] for p in meta["parts"]] == ["deck", "pin", "arm"]


def test_run_partial_naming_still_emits_parts(runner, isolated_dir):
    """Unnamed show_object() calls still get IDs; naming is additive."""
    _init_project(runner)
    _write_script(isolated_dir, content=PARTIAL_NAMED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "partial"])
    parsed = json.loads(result.stdout)

    parts = parsed["parts"]
    assert [p["id"] for p in parts] == ["deck", "part_1", "arm"]
    assert [p["id_source"] for p in parts] == ["name", "generated", "name"]

    # Named parts keep their name; unnamed parts have name=None (or missing)
    assert parts[0].get("name") == "deck"
    assert parts[1].get("name") is None
    assert parts[2].get("name") == "arm"


def test_run_partial_naming_preview_falls_back_to_id(runner, isolated_dir):
    """Preview filenames use the resolved part id."""
    _init_project(runner)
    _write_script(isolated_dir, content=PARTIAL_NAMED_PARTS_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "partial_preview"])
    parsed = json.loads(result.stdout)

    previews = [p["preview"] for p in parsed["parts"]]
    assert previews[0].endswith("/parts/deck.png")
    assert previews[1].endswith("/parts/part_1.png")
    assert previews[2].endswith("/parts/arm.png")


def test_run_single_show_object_has_parts_array(runner, isolated_dir):
    """Even a single show_object() emits parts[] (single-element). One rule, no modes."""
    _init_project(runner)
    _write_script(isolated_dir, content=SIMPLE_BOX_SCRIPT)

    result = runner.invoke(cli, ["run", "script.py", "--output", "single"])
    parsed = json.loads(result.stdout)

    assert "parts" in parsed
    assert len(parsed["parts"]) == 1
    assert parsed["parts"][0]["id"] == "part_0"
    assert parsed["parts"][0]["id_source"] == "generated"
    assert parsed["parts"][0].get("name") is None


def test_run_no_preview_skips_per_part_renders(runner, isolated_dir):
    """--no-preview skips the per-part preview PNGs (JSON still includes parts)."""
    _init_project(runner)
    _write_script(isolated_dir, content=NAMED_PARTS_SCRIPT)

    result = runner.invoke(
        cli, ["run", "script.py", "--output", "nop", "--no-preview"]
    )
    parsed = json.loads(result.stdout)

    # parts still present (metrics don't depend on rendering)
    assert "parts" in parsed
    # But no preview paths (or preview: null)
    for p in parsed["parts"]:
        assert not p.get("preview")


def test_run_duplicate_part_names_are_allowed(runner, isolated_dir):
    """Duplicate names don't error — IDs disambiguate. Filenames fall back to id."""
    script = """\
import cadquery as cq
a = cq.Workplane("XY").box(10, 10, 2)
b = cq.Workplane("XY").box(5, 5, 2).translate((20, 0, 0))
show_object(a, name="wheel")
show_object(b, name="wheel")
"""
    _init_project(runner)
    _write_script(isolated_dir, content=script)

    result = runner.invoke(cli, ["run", "script.py", "--output", "dup"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)

    parts = parsed["parts"]
    assert [p["id"] for p in parts] == ["wheel", "wheel_2"]
    assert [p["id_source"] for p in parts] == ["name", "name"]
    assert [p["name"] for p in parts] == ["wheel", "wheel"]
    # Collision policy: IDs are deduped, so preview filenames do not overwrite.
    previews = [p["preview"] for p in parts]
    assert previews[0].endswith("/parts/wheel.png")
    assert previews[1].endswith("/parts/wheel_2.png")


def test_run_explicit_part_id_wins_over_name(runner, isolated_dir):
    """show_object(id=...) is the durable author-facing handle."""
    script = """\
import cadquery as cq
deck = cq.Workplane("XY").box(20, 10, 2)
pin = cq.Workplane("XY").cylinder(5, 2).translate((20, 0, 0))
show_object(deck, id="main-deck", name="Deck")
show_object(pin, options={"id": "pivot_pin", "name": "Pivot Pin", "color": "blue"})
"""
    _init_project(runner)
    _write_script(isolated_dir, content=script)

    result = runner.invoke(cli, ["run", "script.py", "--output", "explicit_parts"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)

    parts = parsed["parts"]
    assert [p["id"] for p in parts] == ["main_deck", "pivot_pin"]
    assert [p["id_source"] for p in parts] == ["explicit", "explicit"]
    assert [p["name"] for p in parts] == ["Deck", "Pivot Pin"]


def test_run_first_run_viewer_defaults_to_single_a(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0, result.output

    viewer_html = (isolated_dir / "v1" / "viewer.html").read_text()
    assert 'DEFAULT_MODE = "single-a"' in viewer_html
    assert 'DEFAULT_MODE = "side-by-side"' not in viewer_html


def test_run_second_run_viewer_defaults_to_side_by_side(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    result = runner.invoke(cli, ["run", "script.py", "--output", "v2"])
    assert result.exit_code == 0, result.output

    viewer_html = (isolated_dir / "v2" / "viewer.html").read_text()
    assert 'DEFAULT_MODE = "side-by-side"' in viewer_html


def test_run_first_run_emits_hint_in_json(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0, result.output

    parsed = json.loads(result.stdout)
    assert "hint" in parsed
    assert parsed["viewer"] in parsed["hint"]
    assert "side-by-side" not in parsed["hint"]


def test_run_second_run_hint_references_previous(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir)
    runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    result = runner.invoke(cli, ["run", "script.py", "--output", "v2"])
    assert result.exit_code == 0, result.output

    parsed = json.loads(result.stdout)
    assert "hint" in parsed
    assert "previous" in parsed["hint"]
    assert parsed["viewer"] in parsed["hint"]


def test_run_viewer_parts_panel_includes_named_parts(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=NAMED_PARTS_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0, result.output

    viewer_html = (isolated_dir / "v1" / "viewer.html").read_text()
    # Parts JSON is embedded; the names must round-trip through templating.
    import re
    match = re.search(r"const PARTS = (\[.*?\]);", viewer_html)
    assert match, "PARTS const not found in viewer.html"
    parts = json.loads(match.group(1))
    assert [p.get("name") for p in parts] == ["deck", "pin", "arm"]
    assert [p.get("color") for p in parts] == ["gray", "blue", "red"]
    assert "className = 'swatch'" in viewer_html
    assert "swatch.style.background = part.color" in viewer_html
    assert "registerPartMeshes(m)" in viewer_html
    assert "attach(sceneA_single, MODEL_A_URL, { onMesh:" in viewer_html
    assert "partMatchesNameExact" in viewer_html
    assert "Longest IDs first avoids" in viewer_html
    assert "&& !partState.ghostRest" in viewer_html
    assert "attach(sceneA_split, MODEL_A_URL, {})" in viewer_html

    from PIL import Image
    preview_img = Image.open(isolated_dir / "v1" / "preview.png").convert("RGB")
    if hasattr(preview_img, "get_flattened_data"):
        pixels = list(preview_img.get_flattened_data())
    else:
        pixels = list(preview_img.getdata())
    red_pixels = sum(1 for r, g, b in pixels if r > 120 and r > g * 1.2 and r > b * 1.2)
    blue_pixels = sum(1 for r, g, b in pixels if b > 120 and b > r * 1.2 and b > g * 1.2)
    assert red_pixels > 100
    assert blue_pixels > 100


def test_run_viewer_embeds_part_groups(runner, isolated_dir):
    _init_project(runner)
    _write_script(isolated_dir, content=GROUPED_PARTS_SCRIPT)
    result = runner.invoke(cli, ["run", "script.py", "--output", "grouped_viewer"])
    assert result.exit_code == 0, result.output

    viewer_html = (isolated_dir / "v1_grouped_viewer" / "viewer.html").read_text()
    import re
    parts_match = re.search(r"const PARTS = (\[.*?\]);", viewer_html)
    groups_match = re.search(r"const GROUPS = (\[.*?\]);", viewer_html)
    assert parts_match, "PARTS const not found in viewer.html"
    assert groups_match, "GROUPS const not found in viewer.html"

    parts = json.loads(parts_match.group(1))
    groups = json.loads(groups_match.group(1))
    assert [p.get("part_of") for p in parts] == ["frame", "frame", None]
    assert groups == [{
        "id": "frame",
        "name": "frame",
        "color": "steelblue",
        "part_ids": ["base_plate", "center_rib"],
    }]
    assert 'id="parts-groups-section"' in viewer_html
    assert "makeStaticGroupRow" in viewer_html
    assert "part-group-tag" in viewer_html
    assert "Parts ${PARTS.length} · Groups ${GROUPS.length}" in viewer_html
    assert "toggleGroupHidden" in viewer_html
    assert "toggleGroupIsolated" in viewer_html


def test_render_unified_empty_parts_payload_when_none(isolated_dir):
    """No parts → embedded JSON is empty array, JS path will grey the button."""
    from agentcad.commands.view import _render_unified

    glb = isolated_dir / "fake.glb"
    glb.write_bytes(b"")
    out = isolated_dir / "v.html"
    _render_unified(out, glb_a=glb, label_a="x")

    html = out.read_text()
    assert "const PARTS = [];" in html
    assert "const GROUPS = [];" in html


def test_run_default_does_not_auto_render_preview_gif(runner, isolated_dir):
    """Turntable GIFs are on-demand via the viewer's Export GIF button — they
    are not auto-rendered on every run. Cuts ~3-6s off the default preview
    path; the viewer's client-side capture covers the social-post use case."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0, result.output

    parsed = json.loads(result.stdout)
    assert "preview_gif" not in parsed
    assert not (isolated_dir / "v1" / "preview.gif").exists()

    meta = json.loads((isolated_dir / "v1" / "meta.json").read_text())
    assert "preview_gif" not in meta


def test_render_unified_includes_export_gif_button(isolated_dir):
    """Client-side Export GIF button is present in every viewer.html."""
    from agentcad.commands.view import _render_unified

    glb = isolated_dir / "fake.glb"
    glb.write_bytes(b"")
    out = isolated_dir / "v.html"
    _render_unified(out, glb_a=glb, label_a="x")

    html = out.read_text()
    assert 'id="export-gif-btn"' in html
    assert "agentcad.dev" in html  # watermark text


def test_render_unified_pauses_rotation_on_viewer_interaction(isolated_dir):
    """Auto-rotation pauses when OrbitControls reports user interaction."""
    from agentcad.commands.view import _render_unified

    glb = isolated_dir / "fake.glb"
    glb.write_bytes(b"")
    out = isolated_dir / "v.html"
    _render_unified(out, glb_a=glb, label_a="x")

    html = out.read_text()
    assert "function setAutoRotate(enabled)" in html
    assert "function pauseAutoRotate()" in html
    assert "controls.addEventListener('start', pauseAutoRotate);" in html
    assert "setAutoRotate(wasRotating);" in html


def test_render_unified_keeps_preserve_drawing_buffer(isolated_dir):
    """The WebGL renderer must be constructed with `preserveDrawingBuffer: true`.

    Without it, the live canvas is cleared after each render, so the Export
    GIF button's frame-capture loop (toDataURL / drawImage on
    renderer.domElement) produces blank frames — the GIF downloads but is
    empty. This guardrail catches that flag being silently removed for a
    "perf" reason later; PR #200 made client-side GIF the only path, so
    keeping this on is load-bearing for the social-post workflow.
    """
    from agentcad.commands.view import _render_unified

    glb = isolated_dir / "fake.glb"
    glb.write_bytes(b"")
    out = isolated_dir / "v.html"
    _render_unified(out, glb_a=glb, label_a="x")

    html = out.read_text()
    assert "preserveDrawingBuffer: true" in html


def test_run_surfaces_script_warnings_in_json(runner, isolated_dir):
    """warnings.warn() from the script body lands in the run JSON's
    warnings array. Underpins #88's tapered_sweep kink warning surfacing."""
    _init_project(runner)
    _write_script(isolated_dir, content=(
        "import warnings\n"
        "import cadquery as cq\n"
        "warnings.warn('AGENTCAD_SCRIPT_WARNING')\n"
        "result = cq.Workplane('XY').box(10, 10, 10)\n"
        "show_object(result)\n"
    ))
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert "AGENTCAD_SCRIPT_WARNING" in (parsed.get("warnings") or [])


def test_run_emits_progress_heartbeats_on_stderr(runner, isolated_dir):
    """Stderr heartbeats per phase let an agent distinguish 'still
    working' from 'wedged' during longer preview generation. Without
    them, a multi-second run looks like a hang. Issue #164."""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1"])
    assert result.exit_code == 0, result.output
    stderr = result.stderr
    # All phases must emit a heartbeat in order — text doesn't have to be
    # an exact match, just identifiable by phase keyword.
    assert "[agentcad] running script" in stderr
    assert "[agentcad] computing metrics" in stderr
    assert "[agentcad] rendering preview" in stderr
    # Heartbeats stay out of the JSON on stdout (Click 8.3 result.output
    # interleaves stdout + stderr; result.stdout is stdout-only).
    assert "[agentcad]" not in result.stdout
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"


def test_run_no_preview_skips_preview_heartbeats(runner, isolated_dir):
    """--no-preview short-circuits the composite render, so its
    heartbeat shouldn't fire. (Viewer/diff/GLB still generate but
    use their own heartbeats.)"""
    _init_project(runner)
    _write_script(isolated_dir)
    result = runner.invoke(cli, ["run", "script.py", "--output", "v1", "--no-preview"])
    assert result.exit_code == 0
    stderr = result.stderr
    assert "[agentcad] running script" in stderr
    assert "[agentcad] rendering preview (4-view" not in stderr
