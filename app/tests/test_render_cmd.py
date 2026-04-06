import json
from pathlib import Path

from agentcad.cli import cli


SIMPLE_BOX_SCRIPT = """\
import cadquery as cq
result = cq.Workplane("XY").box(10, 10, 10)
show_object(result)
"""


def _init_project(runner):
    runner.invoke(cli, ["init", "--name", "test_project"])


def _create_step(runner, isolated_dir):
    """Create a version directory with a STEP file via agentcad run."""
    _init_project(runner)
    path = isolated_dir / "script.py"
    path.write_text(SIMPLE_BOX_SCRIPT)
    runner.invoke(cli, ["run", "script.py", "--output", "box"])
    return isolated_dir / "v1_box" / "output.step"


def _create_standalone_step(isolated_dir):
    """Create a standalone STEP file (not in a version directory)."""
    from cadquery import Workplane, exporters

    shape = Workplane("XY").box(10, 10, 10)
    step_path = isolated_dir / "model.step"
    exporters.export(shape, str(step_path))
    return step_path


def test_render_step_not_found(runner, isolated_dir):
    result = runner.invoke(cli, ["render", "nonexistent.step", "--view", "iso"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "render"
    assert parsed["status"] == "error"


def test_render_invalid_view_error(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "notaview"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "render"
    assert parsed["status"] == "error"


def test_render_produces_png(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"
    png_path = Path(parsed["renders"]["iso"])
    assert png_path.exists()
    assert png_path.read_bytes()[:4] == b"\x89PNG"


def test_render_multiple_views(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "front,top,iso"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert len(parsed["renders"]) == 3
    for name in ["front", "top", "iso"]:
        assert Path(parsed["renders"][name]).exists()


def test_render_all_views(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "all"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    for name in ["front", "right", "top", "iso"]:
        assert name in parsed["renders"]
        assert Path(parsed["renders"][name]).exists()


def test_render_custom_angle(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "45,30"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "custom_45_30" in parsed["renders"]
    assert Path(parsed["renders"]["custom_45_30"]).exists()


def test_render_zoom(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    r1 = runner.invoke(cli, ["render", str(step_path), "--view", "iso", "--name", "default"])
    r2 = runner.invoke(cli, ["render", str(step_path), "--view", "iso", "--zoom", "2.0", "--name", "zoomed"])
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    p1 = json.loads(r1.output)
    p2 = json.loads(r2.output)
    png1 = Path(p1["renders"]["default"]).read_bytes()
    png2 = Path(p2["renders"]["zoomed"]).read_bytes()
    assert png1 != png2


def test_render_name_option(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso", "--name", "detail"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "detail" in parsed["renders"]
    assert Path(parsed["renders"]["detail"]).exists()
    assert Path(parsed["renders"]["detail"]).name == "detail.png"


def test_render_name_with_multiple_views_error(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "front,top", "--name", "x"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "render"
    assert parsed["status"] == "error"


def test_render_json_response(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso"])
    parsed = json.loads(result.output)
    assert parsed["command"] == "render"
    assert parsed["status"] == "success"
    assert "renders" in parsed


def test_render_version_dir_saves_to_renders(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso"])
    assert result.exit_code == 0
    renders_dir = isolated_dir / "v1_box" / "renders"
    assert renders_dir.is_dir()
    assert (renders_dir / "iso.png").exists()


def test_render_version_dir_updates_meta_json(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    runner.invoke(cli, ["render", str(step_path), "--view", "iso"])
    meta = json.loads((isolated_dir / "v1_box" / "meta.json").read_text())
    assert "renders" in meta
    assert "iso" in meta["renders"]


def test_render_standalone_step_saves_alongside(runner, isolated_dir):
    step_path = _create_standalone_step(isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    png_path = Path(parsed["renders"]["iso"])
    assert png_path.exists()
    # PNG saved next to the STEP file, not in a renders/ subdirectory
    assert png_path.parent == step_path.parent


# --- focus tests ---


def test_render_focus(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    r1 = runner.invoke(cli, ["render", str(step_path), "--view", "iso", "--name", "default"])
    r2 = runner.invoke(cli, ["render", str(step_path), "--view", "iso", "--focus", "5,5,5", "--name", "focused"])
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    p1 = json.loads(r1.output)
    p2 = json.loads(r2.output)
    assert Path(p1["renders"]["default"]).read_bytes() != Path(p2["renders"]["focused"]).read_bytes()


def test_render_focus_with_zoom(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso",
                                 "--focus", "5,5,5", "--zoom", "2.0"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert Path(parsed["renders"]["iso"]).exists()


def test_render_focus_with_custom_angle(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "45,30",
                                 "--focus", "5,5,5"])
    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert "custom_45_30" in parsed["renders"]


def test_render_no_fit_with_focus(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    r1 = runner.invoke(cli, ["render", str(step_path), "--view", "iso",
                              "--focus", "5,5,5", "--zoom", "2.0", "--name", "fit"])
    r2 = runner.invoke(cli, ["render", str(step_path), "--view", "iso",
                              "--focus", "5,5,5", "--zoom", "2.0", "--no-fit", "--name", "nofit"])
    assert r1.exit_code == 0
    assert r2.exit_code == 0
    p1 = json.loads(r1.output)
    p2 = json.loads(r2.output)
    assert Path(p1["renders"]["fit"]).read_bytes() != Path(p2["renders"]["nofit"]).read_bytes()


def test_render_no_fit_without_focus_error(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso", "--no-fit"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "render"
    assert parsed["status"] == "error"


def test_render_invalid_focus_error(runner, isolated_dir):
    step_path = _create_step(runner, isolated_dir)
    result = runner.invoke(cli, ["render", str(step_path), "--view", "iso", "--focus", "bad"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["command"] == "render"
    assert parsed["status"] == "error"
