import cadquery as cq
import pytest
from PIL import Image

from agentcad.solid_compare import (
    compare_solid_volumes,
    write_solid_comparison_artifacts,
)


def _box(size=10, translation=(0, 0, 0)):
    return (
        cq.Workplane("XY")
        .box(size, size, size)
        .translate(translation)
        .val()
        .wrapped
    )


def test_identical_solids_have_complete_shared_volume():
    comparison = compare_solid_volumes(_box(), _box())

    assert comparison.available
    assert comparison.data["classification"] == "same_occupied_volume"
    assert comparison.data["volumes"] == {
        "reference": 1000.0,
        "candidate": 1000.0,
        "shared": 1000.0,
        "reference_only": 0.0,
        "candidate_only": 0.0,
        "union": 1000.0,
    }
    assert comparison.data["ratios"] == {
        "volume_iou": 1.0,
        "reference_coverage": 1.0,
        "candidate_coverage": 1.0,
    }


def test_partially_overlapping_solids_report_directional_volumes():
    comparison = compare_solid_volumes(_box(), _box(translation=(5, 0, 0)))

    assert comparison.data["classification"] == "partial_shared_volume"
    assert comparison.data["volumes"]["shared"] == pytest.approx(500.0)
    assert comparison.data["volumes"]["reference_only"] == pytest.approx(500.0)
    assert comparison.data["volumes"]["candidate_only"] == pytest.approx(500.0)
    assert comparison.data["ratios"]["volume_iou"] == pytest.approx(0.3333)
    assert comparison.data["ratios"]["reference_coverage"] == pytest.approx(0.5)
    assert comparison.data["ratios"]["candidate_coverage"] == pytest.approx(0.5)


def test_source_frame_does_not_align_identical_translated_solids():
    comparison = compare_solid_volumes(
        _box(),
        _box(translation=(100, 0, 0)),
    )

    assert comparison.data["classification"] == "no_shared_volume"
    assert comparison.data["volumes"]["shared"] == 0.0
    assert comparison.data["ratios"]["volume_iou"] == 0.0
    assert comparison.data["alignment"] == {
        "mode": "source_frame",
        "transform_applied": False,
    }


def test_surface_without_closed_solid_is_unavailable():
    face = cq.Workplane("XY").rect(10, 10).val().wrapped

    comparison = compare_solid_volumes(face, _box())

    assert not comparison.available
    assert comparison.data["status"] == "unavailable"
    assert comparison.data["reason"]["code"] == "reference_has_no_closed_solid"


def test_boolean_failure_is_structured(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("agentcad.solid_compare._run_boolean", fail)

    comparison = compare_solid_volumes(_box(), _box())

    assert not comparison.available
    assert comparison.data["reason"]["code"] == "boolean_operation_failed"
    assert "synthetic failure" in comparison.data["reason"]["message"]


def test_successful_comparison_writes_colored_glb_and_png(tmp_path):
    comparison = compare_solid_volumes(
        _box(),
        _box(translation=(5, 0, 0)),
    )
    glb_path = tmp_path / "diff_volume.glb"
    png_path = tmp_path / "diff_volume.png"

    written = write_solid_comparison_artifacts(
        comparison,
        glb_path,
        png_path,
    )

    assert written
    assert glb_path.exists()
    assert glb_path.stat().st_size > 0
    with Image.open(png_path) as image:
        assert image.size == (1024, 1216)
