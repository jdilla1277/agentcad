import json
from pathlib import Path

import cadquery as cq
from cadquery import exporters

from agentcad.cli import cli
from agentcad.export import export_glb


def _make_glb(directory):
    """Create a real GLB file in the given directory."""
    box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
    glb_path = directory / "output.glb"
    export_glb(box, str(glb_path))
    return glb_path


def _make_two_hole_step(directory):
    plate = cq.Workplane("XY").box(50, 20, 5)
    cutters = (
        cq.Workplane("XY")
        .pushPoints([(-10, 0), (10, 0)])
        .circle(3)
        .extrude(10)
    )
    step_path = directory / "two_holes.step"
    exporters.export(plate.cut(cutters), str(step_path))
    return step_path


def test_view_missing_file_error(runner, isolated_dir):
    result = runner.invoke(cli, ["view", "nope.glb"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"


def test_view_unsupported_format_error(runner, isolated_dir):
    (isolated_dir / "model.obj").write_text("v 0 0 0")
    result = runner.invoke(cli, ["view", "model.obj"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"


def test_view_glb_opens_browser(runner, isolated_dir, monkeypatch):
    glb_path = _make_glb(isolated_dir)
    opened_urls = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened_urls.append(url))
    result = runner.invoke(cli, ["view", str(glb_path)])
    assert result.exit_code == 0
    assert len(opened_urls) == 1
    assert opened_urls[0].startswith("file://")


def test_view_glb_returns_json(runner, isolated_dir, monkeypatch):
    glb_path = _make_glb(isolated_dir)
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    result = runner.invoke(cli, ["view", str(glb_path)])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "view"
    assert parsed["status"] == "success"
    assert "url" in parsed


def test_view_step_auto_exports(runner, isolated_dir, monkeypatch):
    # Create a STEP file
    box = cq.Workplane("XY").box(10, 10, 10)
    step_path = isolated_dir / "output.step"
    from cadquery import exporters
    exporters.export(box, str(step_path))

    opened_urls = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened_urls.append(url))
    result = runner.invoke(cli, ["view", str(step_path)])
    assert result.exit_code == 0
    assert len(opened_urls) == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"


# --- M24: Relative path fix ---


def test_view_html_embeds_glb_data(runner, isolated_dir, monkeypatch):
    """Generated HTML embeds GLB as a base64 data URI (no relative file fetch)."""
    glb_path = _make_glb(isolated_dir)
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    runner.invoke(cli, ["view", str(glb_path)])
    html_path = isolated_dir / "output_viewer.html"
    html = html_path.read_text()
    assert "data:application/octet-stream;base64," in html
    # Should NOT contain a bare filename reference
    assert "loader.load('output.glb')" not in html


def test_view_relative_path(runner, isolated_dir, monkeypatch):
    """agentcad view should work with relative paths."""
    subdir = isolated_dir / "subdir"
    subdir.mkdir()
    glb_path = _make_glb(subdir)

    opened_urls = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened_urls.append(url))
    # Use relative path
    result = runner.invoke(cli, ["view", "subdir/output.glb"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert opened_urls[0].startswith("file://")


# --- M58: Visual diff view (two-file mode) ---


def _make_glb_named(directory, name, size=10):
    """Create a GLB with a custom filename and size."""
    box = cq.Workplane("XY").box(size, size, size).val().wrapped
    glb_path = directory / f"{name}.glb"
    export_glb(box, str(glb_path))
    return glb_path


def test_view_two_files_produces_diff_html(runner, isolated_dir, monkeypatch):
    """Passing two files produces a diff HTML with a `diff_` prefix."""
    a = _make_glb_named(isolated_dir, "a", size=10)
    b = _make_glb_named(isolated_dir, "b", size=20)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(a), str(b)])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["mode"] == "side-by-side"
    assert "model_a" in parsed
    assert "model_b" in parsed

    diff_html = isolated_dir / "diff_a_b.html"
    assert diff_html.exists()


def test_view_two_files_html_contains_both_models(runner, isolated_dir, monkeypatch):
    """Diff HTML must embed both models as separate data URIs."""
    a = _make_glb_named(isolated_dir, "a")
    b = _make_glb_named(isolated_dir, "b")
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    runner.invoke(cli, ["view", str(a), str(b)])

    html = (isolated_dir / "diff_a_b.html").read_text()
    # Two data URIs must be present (one per model)
    assert html.count("data:application/octet-stream;base64,") == 2


def test_view_two_files_side_by_side_markers(runner, isolated_dir, monkeypatch):
    """Side-by-side HTML has the structural markers for dual viewports."""
    a = _make_glb_named(isolated_dir, "a")
    b = _make_glb_named(isolated_dir, "b")
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    runner.invoke(cli, ["view", str(a), str(b)])

    html = (isolated_dir / "diff_a_b.html").read_text()
    # Renderer needs to render both scenes; look for setViewport or two canvases
    assert "setViewport" in html or html.count("<canvas") >= 2
    # Both model labels should appear in the UI
    assert "a.glb" in html
    assert "b.glb" in html
    # Two-model scenes normalize unrelated source origins into one comparison frame.
    assert "alignToCenter: true" in html
    assert "model.position.sub(sourceCenter)" in html


def test_view_two_files_step_auto_converts(runner, isolated_dir, monkeypatch):
    """Both files auto-convert from STEP; STEP inputs also produce a side-by-side PNG for the agent."""
    from cadquery import exporters as cq_exporters
    a_step = isolated_dir / "a.step"
    b_step = isolated_dir / "b.step"
    cq_exporters.export(cq.Workplane("XY").box(10, 10, 10), str(a_step))
    cq_exporters.export(cq.Workplane("XY").box(20, 20, 20), str(b_step))
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(a_step), str(b_step)])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    # Both GLBs get written alongside the STEPs
    assert (isolated_dir / "a.glb").exists()
    assert (isolated_dir / "b.glb").exists()
    # Agent-facing PNGs: four matched A/B views plus four aligned overlays.
    assert "png" in parsed
    png_path = Path(parsed["png"])
    assert png_path.exists()
    assert png_path.stat().st_size > 0
    assert "overlay_png" in parsed
    overlay_png_path = Path(parsed["overlay_png"])
    assert overlay_png_path.exists()
    assert overlay_png_path.stat().st_size > 0
    html = (isolated_dir / "diff_a_b.html").read_text()
    assert html.count("data:image/png;base64,") == 2
    assert "four matched views" in html
    assert "four center-aligned overlays" in html


def test_view_two_glbs_no_png(runner, isolated_dir, monkeypatch):
    """GLB inputs skip PNG generation — we don't have a TopoDS_Shape to render."""
    a = _make_glb_named(isolated_dir, "a")
    b = _make_glb_named(isolated_dir, "b")
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(a), str(b)])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert "png" not in parsed


def test_view_two_files_missing_second_errors(runner, isolated_dir, monkeypatch):
    """Missing second file is a clean error."""
    a = _make_glb_named(isolated_dir, "a")
    monkeypatch.setattr("webbrowser.open", lambda url: None)
    result = runner.invoke(cli, ["view", str(a), str(isolated_dir / "nope.glb")])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"
    assert "nope" in parsed["message"]


def test_view_overlay_mode(runner, isolated_dir, monkeypatch):
    """--overlay produces an overlay-mode HTML with opacity controls."""
    a = _make_glb_named(isolated_dir, "a")
    b = _make_glb_named(isolated_dir, "b")
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(a), str(b), "--overlay"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["mode"] == "overlay"

    html = (isolated_dir / "diff_a_b.html").read_text()
    # Overlay mode: opacity controls for both models
    assert 'id="opacity-a"' in html
    assert 'id="opacity-b"' in html
    # Both tints represented
    assert html.count("data:application/octet-stream;base64,") == 2


def test_view_single_file_still_works(runner, isolated_dir, monkeypatch):
    """Single-file invocation is unchanged — no regression."""
    glb_path = _make_glb(isolated_dir)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(glb_path)])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert "mode" not in parsed  # single-file mode doesn't set a mode key
    assert (isolated_dir / "output_viewer.html").exists()


def test_render_unified_embeds_review_payload(isolated_dir):
    from agentcad.commands.view import _render_unified

    glb = isolated_dir / "fake.glb"
    glb.write_bytes(b"")
    out = isolated_dir / "v.html"
    review = {
        "measure": {
            "metrics": {
                "dimensions": {"x": 50.0, "y": 20.0, "z": 5.0},
                "is_valid": True,
            },
            "cylindrical_features": [
                {
                    "diameter_mm": 6.0,
                    "count": 2,
                    "axis": "+z",
                    "representative_centers": [
                        {"x": -10.0, "y": 0.0, "z": 0.0},
                        {"x": 10.0, "y": 0.0, "z": 0.0},
                    ],
                }
            ],
        },
        "check_spec": {
            "passed": False,
            "matched_features": [],
            "missing_features": [],
            "total_abs_count_error": 1,
        },
    }

    _render_unified(out, glb_a=glb, label_a="x", default_mode="spec", review=review)

    html = out.read_text()
    assert 'id="btn-spec"' in html
    assert 'id="spec-panel"' in html
    assert 'const REVIEW = {"measure"' in html
    assert '"cylindrical_features"' in html
    assert 'DEFAULT_MODE = "spec"' in html


def test_review_markers_use_model_surface_raycast(isolated_dir):
    from agentcad.commands.view import _render_unified

    glb = isolated_dir / "fake.glb"
    glb.write_bytes(b"")
    out = isolated_dir / "v.html"
    review = {
        "measure": {
            "validity": {"is_valid": True},
            "metrics": {
                "dimensions": {"x": 50.0, "y": 20.0, "z": 5.0},
                "bounding_box": {
                    "x": [-25.0, 25.0],
                    "y": [-10.0, 10.0],
                    "z": [-2.5, 2.5],
                },
            },
            "cylindrical_features": [
                {
                    "diameter_mm": 6.0,
                    "count": 1,
                    "axis": "+z",
                    "representative_centers": [{"x": 0.0, "y": 0.0, "z": 0.0}],
                }
            ],
        },
        "check_spec": None,
    }

    _render_unified(out, glb_a=glb, label_a="x", default_mode="spec", review=review)

    html = out.read_text()
    assert "function surfacePositionFromModel" in html
    assert "raycaster.intersectObject(reviewModelA, true)" in html
    assert "fallbackMarkerSurfacePosition" in html
    assert "reviewModelA = m" in html


def test_view_step_with_spec_opens_spec_check_mode(runner, isolated_dir, monkeypatch):
    step = _make_two_hole_step(isolated_dir)
    spec = isolated_dir / "spec.json"
    spec.write_text(json.dumps({
        "features": [
            {
                "name": "mounting_holes",
                "type": "cylinder",
                "diameter_mm": 6,
                "count": 3,
                "axis": "+z",
            }
        ]
    }))
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(step), "--spec", str(spec)])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["mode"] == "spec"
    assert parsed["review"] is True

    html = (isolated_dir / "two_holes_viewer.html").read_text()
    assert 'DEFAULT_MODE = "spec"' in html
    assert '"check_spec"' in html
    assert '"passed": false' in html
    assert '"count_error": -1' in html


def test_view_review_requires_step_source(runner, isolated_dir, monkeypatch):
    glb_path = _make_glb(isolated_dir)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(glb_path), "--measure"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"
    assert "requires a STEP/STP source" in parsed["message"]


def test_view_missing_spec_returns_json_error(runner, isolated_dir, monkeypatch):
    step = _make_two_hole_step(isolated_dir)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["view", str(step), "--spec", "missing.json"])

    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "view"
    assert parsed["status"] == "error"
    assert "Spec file" in parsed["message"]


# --- M58: agentcad diff --visual wiring ---


def _init_project_with_two_runs(runner, isolated_dir):
    """Set up a project with two runs so diff has something to work with.

    Scripts explicitly import cadquery — post-Phase-6 the no-import path
    defaults to b3d, which doesn't inject ``cq``."""
    runner.invoke(
        cli,
        ["init", "--name", "diffproj", "--runtime", "cadquery"],
    )
    script_a = isolated_dir / "a.py"
    script_a.write_text(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(10, 10, 10)\n"
        "show_object(result)\n"
    )
    runner.invoke(cli, ["run", "a.py", "--output", "a", "--no-preview"])
    script_b = isolated_dir / "b.py"
    script_b.write_text(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(20, 20, 20)\n"
        "show_object(result)\n"
    )
    runner.invoke(cli, ["run", "b.py", "--output", "b", "--no-preview"])


def test_diff_visual_flag_produces_diff_html_and_png(runner, isolated_dir, monkeypatch):
    """agentcad diff v1 v2 --visual produces diff HTML (human) + side-by-side PNG (agent)."""
    _init_project_with_two_runs(runner, isolated_dir)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["diff", "1", "2", "--visual"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["command"] == "diff"
    assert "visual" in parsed

    html_path = isolated_dir / parsed["visual"]["html"]
    assert html_path.exists()
    html = html_path.read_text()
    assert html.count("data:application/octet-stream;base64,") == 2
    assert "data:image/png;base64," in html

    # Agent-facing PNG — the whole point of this feature
    assert "png" in parsed["visual"]
    png_path = isolated_dir / parsed["visual"]["png"]
    assert png_path.exists()
    assert png_path.stat().st_size > 0
    assert "overlay_png" in parsed["visual"]
    overlay_png_path = isolated_dir / parsed["visual"]["overlay_png"]
    assert overlay_png_path.exists()
    assert overlay_png_path.stat().st_size > 0


def test_diff_visual_with_overlay(runner, isolated_dir, monkeypatch):
    """agentcad diff v1 v2 --visual --overlay produces overlay mode."""
    _init_project_with_two_runs(runner, isolated_dir)
    monkeypatch.setattr("webbrowser.open", lambda url: None)

    result = runner.invoke(cli, ["diff", "1", "2", "--visual", "--overlay"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["visual"]["mode"] == "overlay"
    assert "png" in parsed["visual"]
    assert "overlay_png" in parsed["visual"]
    html = (isolated_dir / parsed["visual"]["html"]).read_text()
    assert 'id="opacity-a"' in html
    assert html.count("data:image/png;base64,") == 2


def test_diff_without_visual_unchanged(runner, isolated_dir):
    """Without --visual, diff returns metrics-only (no regression)."""
    _init_project_with_two_runs(runner, isolated_dir)

    result = runner.invoke(cli, ["diff", "1", "2"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert "changes" in parsed
    assert "visual" not in parsed
