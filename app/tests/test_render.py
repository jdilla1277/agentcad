from pathlib import Path

import cadquery as cq

import pytest

from agentcad.render import (
    render_shape,
    render_shape_custom,
    render_views,
    parse_view_spec,
    VIEWS,
)


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


# --- parse_view_spec tests ---


def test_parse_view_spec_single_named():
    result = parse_view_spec("iso")
    assert result == [("named", "iso")]


def test_parse_view_spec_multiple_named():
    result = parse_view_spec("front,top,iso")
    assert result == [("named", "front"), ("named", "top"), ("named", "iso")]


def test_parse_view_spec_all():
    result = parse_view_spec("all")
    assert len(result) == 4
    assert all(t == "named" for t, _ in result)
    assert [v for _, v in result] == ["front", "right", "top", "iso"]


def test_parse_view_spec_custom_angle():
    result = parse_view_spec("45,30")
    assert result == [("custom", (45.0, 30.0))]


def test_parse_view_spec_invalid():
    with pytest.raises(ValueError):
        parse_view_spec("notaview")


# --- zoom tests ---


def test_render_shape_with_zoom(tmp_path):
    shape = _make_box_shape()
    default_path = tmp_path / "default.png"
    zoomed_path = tmp_path / "zoomed.png"
    render_shape(shape, "iso", default_path)
    render_shape(shape, "iso", zoomed_path, zoom=2.0)
    assert default_path.read_bytes() != zoomed_path.read_bytes()


# --- custom angle render tests ---


def test_render_shape_custom_produces_png(tmp_path):
    shape = _make_box_shape()
    out = tmp_path / "custom.png"
    render_shape_custom(shape, 45, 30, out)
    assert out.exists()
    assert out.read_bytes()[:4] == b"\x89PNG"


def test_render_shape_custom_different_angles_differ(tmp_path):
    shape = _make_box_shape()
    a_path = tmp_path / "a.png"
    b_path = tmp_path / "b.png"
    render_shape_custom(shape, 0, 0, a_path)
    render_shape_custom(shape, 90, 45, b_path)
    assert a_path.read_bytes() != b_path.read_bytes()


# --- focus tests ---


def test_render_shape_focus_changes_output(tmp_path):
    shape = _make_box_shape()
    default_path = tmp_path / "default.png"
    focused_path = tmp_path / "focused.png"
    render_shape(shape, "iso", default_path)
    render_shape(shape, "iso", focused_path, focus=(5, 5, 5))
    assert default_path.read_bytes() != focused_path.read_bytes()


def test_render_shape_focus_with_zoom(tmp_path):
    shape = _make_box_shape()
    focused_path = tmp_path / "focused.png"
    focused_zoomed_path = tmp_path / "focused_zoomed.png"
    render_shape(shape, "iso", focused_path, focus=(5, 5, 5))
    render_shape(shape, "iso", focused_zoomed_path, focus=(5, 5, 5), zoom=3.0)
    assert focused_path.read_bytes() != focused_zoomed_path.read_bytes()


def test_render_shape_custom_focus(tmp_path):
    shape = _make_box_shape()
    default_path = tmp_path / "default.png"
    focused_path = tmp_path / "focused.png"
    render_shape_custom(shape, 45, 30, default_path)
    render_shape_custom(shape, 45, 30, focused_path, focus=(5, 5, 5))
    assert default_path.read_bytes() != focused_path.read_bytes()


def test_render_shape_no_fit(tmp_path):
    shape = _make_box_shape()
    fit_path = tmp_path / "fit.png"
    nofit_path = tmp_path / "nofit.png"
    render_shape(shape, "iso", fit_path, focus=(5, 5, 5), zoom=2.0)
    render_shape(shape, "iso", nofit_path, focus=(5, 5, 5), zoom=2.0, fit=False)
    assert fit_path.read_bytes() != nofit_path.read_bytes()


# --- mixed view spec tests (M20) ---


def test_parse_view_spec_mixed_named_and_angle():
    result = parse_view_spec("front,right,45:15")
    assert len(result) == 3
    assert result[0] == ("named", "front")
    assert result[1] == ("named", "right")
    assert result[2] == ("custom", (45.0, 15.0))


def test_parse_view_spec_colon_angle_only():
    result = parse_view_spec("45:15")
    assert result == [("custom", (45.0, 15.0))]


def test_parse_view_spec_backward_compat_comma_angle():
    result = parse_view_spec("45,30")
    assert result == [("custom", (45.0, 30.0))]


# --- M24: Render brightness ---


def test_render_has_fill_light(tmp_path):
    """Renders should use multiple lights for visible surfaces from all angles."""
    shape = _make_box_shape()
    # Iso view has visible faces from multiple directions
    iso = tmp_path / "iso.png"
    render_shape(shape, "iso", iso, width=128, height=128)
    # A properly lit render of a 3D box has significant pixel data
    assert iso.stat().st_size > 400, "Render appears too dim or blank"
