import json
from pathlib import Path

import cadquery as cq

from agentcad.cli import cli
from agentcad.export import export_glb


def _make_glb(directory):
    """Create a real GLB file in the given directory."""
    box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
    glb_path = directory / "output.glb"
    export_glb(box, str(glb_path))
    return glb_path


def test_view_missing_file_error(runner, isolated_dir):
    result = runner.invoke(cli, ["view", "nope.glb"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["status"] == "error"


def test_view_unsupported_format_error(runner, isolated_dir):
    (isolated_dir / "model.obj").write_text("v 0 0 0")
    result = runner.invoke(cli, ["view", "model.obj"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
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
    parsed = json.loads(result.output)
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
    parsed = json.loads(result.output)
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
    parsed = json.loads(result.output)
    assert parsed["status"] == "success"
    assert opened_urls[0].startswith("file://")
