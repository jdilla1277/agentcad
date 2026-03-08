from pathlib import Path

import cadquery as cq

from cadtool.render import render_shape, render_views, VIEWS


def _make_box_shape():
    """Create a simple box TopoDS_Shape for testing."""
    result = cq.Workplane("XY").box(10, 10, 10)
    return result.val().wrapped


def test_render_shape_produces_png(tmp_path):
    shape = _make_box_shape()
    out = tmp_path / "test.png"
    render_shape(shape, "iso", out)
    assert out.exists()
    assert out.stat().st_size > 0


def test_render_shape_iso_view(tmp_path):
    shape = _make_box_shape()
    out = tmp_path / "iso.png"
    render_shape(shape, "iso", out)
    magic = out.read_bytes()[:4]
    assert magic == b"\x89PNG"


def test_render_shape_front_view(tmp_path):
    shape = _make_box_shape()
    out = tmp_path / "front.png"
    render_shape(shape, "front", out)
    magic = out.read_bytes()[:4]
    assert magic == b"\x89PNG"


def test_render_shape_different_views_differ(tmp_path):
    shape = _make_box_shape()
    iso_path = tmp_path / "iso.png"
    front_path = tmp_path / "front.png"
    render_shape(shape, "iso", iso_path)
    render_shape(shape, "front", front_path)
    assert iso_path.read_bytes() != front_path.read_bytes()


def test_render_views_returns_dict(tmp_path):
    shape = _make_box_shape()
    result = render_views(shape, ["iso", "front"], tmp_path)
    assert isinstance(result, dict)
    assert "iso" in result
    assert "front" in result
    assert Path(result["iso"]).exists()
    assert Path(result["front"]).exists()


def test_render_views_creates_files(tmp_path):
    shape = _make_box_shape()
    render_views(shape, ["top", "right"], tmp_path)
    assert (tmp_path / "top.png").exists()
    assert (tmp_path / "right.png").exists()


def test_views_dict_has_expected_keys():
    expected = {"front", "back", "left", "right", "top", "bottom", "iso"}
    assert set(VIEWS.keys()) == expected
