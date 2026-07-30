from pathlib import Path

import cadquery as cq
from PIL import Image, ImageChops, ImageDraw

import pytest

from agentcad.render import (
    _comparison_frame_scales,
    _semantic_diff_panel,
    _setup_render,
    render_diff_overlay,
    render_diff_side_by_side,
    render_shape,
    render_shape_batch,
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


def test_render_shape_supports_custom_size_and_msaa(tmp_path):
    shape = _make_box_shape()
    aliased = tmp_path / "aliased.png"
    antialiased = tmp_path / "antialiased.png"
    render_shape(shape, "iso", aliased, width=320, height=240)
    render_shape(shape, "iso", antialiased, width=320, height=240, msaa=8)
    with Image.open(antialiased) as image:
        assert image.size == (320, 240)
    with Image.open(aliased) as before, Image.open(antialiased) as after:
        assert ImageChops.difference(before, after).getbbox() is not None


def test_setup_render_configures_msaa_samples():
    shape = _make_box_shape()
    view, _context = _setup_render(shape, width=64, height=64, msaa=8)
    assert view.ChangeRenderingParams().NbMsaaSamples == 8


def test_render_shape_batch_supports_msaa(tmp_path):
    shape = _make_box_shape()
    outputs = [tmp_path / "front.png", tmp_path / "iso.png"]
    render_shape_batch(
        shape, ["front", "iso"], outputs, width=160, height=120, msaa=4,
    )
    for output in outputs:
        with Image.open(output) as image:
            assert image.size == (160, 120)


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


def test_render_diff_side_by_side_contains_four_views_per_shape(tmp_path):
    shape_a = cq.Workplane("XY").box(10, 10, 10).val().wrapped
    shape_b = cq.Workplane("XY").box(20, 10, 5).val().wrapped
    output = tmp_path / "diff_side.png"

    render_diff_side_by_side(
        shape_a, shape_b, "previous", "current", output, width=96, height=96
    )

    with Image.open(output) as image:
        # Two 2x2 composites, plus one shared model-label bar.
        assert image.size == (384, 288)


def test_render_diff_overlay_contains_four_aligned_views(tmp_path):
    shape_a = cq.Workplane("XY").box(10, 10, 10).translate((0, 0, 50)).val().wrapped
    shape_b = cq.Workplane("XY").box(20, 10, 5).val().wrapped
    output = tmp_path / "diff_overlay.png"

    comparison = render_diff_overlay(
        shape_a, shape_b, "previous", "current", output, width=192, height=192
    )

    with Image.open(output) as image:
        # A 2x2 grid of 96px overlays, with view labels and a shared legend.
        assert image.size == (192, 368)
    assert comparison["method"] == "four_view_image_mask"
    assert comparison["alignment"]["mode"] == "bounding_box_center"
    assert comparison["score"]["classification"] in {
        "low", "moderate", "high",
    }
    assert len(comparison["views"]) == 4


def test_render_diff_overlay_classifies_same_geometry_as_high_overlap(tmp_path):
    shape_a = cq.Workplane("XY").box(10, 20, 5).val().wrapped
    shape_b = (
        cq.Workplane("XY").box(10, 20, 5).translate((100, -50, 25)).val().wrapped
    )

    comparison = render_diff_overlay(
        shape_a,
        shape_b,
        "previous",
        "current",
        tmp_path / "same_geometry.png",
        width=192,
        height=192,
    )

    assert comparison["score"]["classification"] == "high"
    assert comparison["score"]["value"] > 0.95


def test_semantic_diff_panel_classifies_shared_removed_and_added_pixels():
    image_a = Image.new("RGB", (100, 100), (77, 77, 77))
    image_b = Image.new("RGB", (100, 100), (77, 77, 77))
    ImageDraw.Draw(image_a).rectangle((10, 20, 59, 79), fill=(180, 180, 180))
    ImageDraw.Draw(image_b).rectangle((40, 20, 89, 79), fill=(180, 180, 180))

    panel, stats = _semantic_diff_panel(image_a, image_b)

    assert stats["coincident_fraction_of_union"] == pytest.approx(0.25)
    assert stats["reference_only_fraction_of_union"] == pytest.approx(0.375)
    assert stats["candidate_only_fraction_of_union"] == pytest.approx(0.375)
    pixels = (
        panel.get_flattened_data()
        if hasattr(panel, "get_flattened_data")
        else panel.getdata()
    )
    colors = set(pixels)
    assert (210, 214, 220) in colors
    assert (0, 114, 178) in colors
    assert (230, 159, 0) in colors


def test_diff_overlay_preserves_relative_scale():
    shape_a = cq.Workplane("XY").box(10, 10, 10).val().wrapped
    shape_b = cq.Workplane("XY").box(20, 20, 20).val().wrapped

    scale_a, scale_b = _comparison_frame_scales(shape_a, shape_b, "top")

    assert scale_a == pytest.approx(0.5)
    assert scale_b == pytest.approx(1.0)


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
