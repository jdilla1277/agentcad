import math

import cadquery as cq
import pytest
from PIL import Image

import agentcad.solid_compare as solid_compare_module
from agentcad.solid_compare import (
    approximate_compare_solid_volumes,
    bounded_approximate_solid_volumes,
    bounded_compare_solid_volumes,
    compare_solid_volumes_with_fallback,
    compare_solid_volumes,
    write_solid_comparison_artifacts,
)
from agentcad.comparison_phases import ComparisonPhaseRecorder


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


def _compound(*shapes):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


# Issue #119: a compound whose member solids touch or overlap EACH OTHER
# (e.g. raise_annulus output: part + boss seated on a face) is a degenerate
# Boolean argument — members must be fused before comparison, or Common
# returns empty and the diff reports no_shared_volume for a superset.
def test_compound_with_touching_members_reports_shared_volume():
    # boss seated exactly on the reference box's top face (z=5)
    candidate = _compound(_box(), _box(size=4, translation=(0, 0, 7)))

    comparison = compare_solid_volumes(_box(), candidate)

    assert comparison.available
    assert comparison.data["classification"] == "partial_shared_volume"
    assert comparison.data["volumes"]["shared"] == pytest.approx(1000.0)
    assert comparison.data["volumes"]["candidate_only"] == pytest.approx(64.0)
    assert comparison.data["volumes"]["reference_only"] == 0.0


def test_compound_with_disjoint_member_reports_shared_volume():
    candidate = _compound(_box(), _box(size=4, translation=(100, 0, 0)))

    comparison = compare_solid_volumes(_box(), candidate)

    assert comparison.available
    assert comparison.data["classification"] == "partial_shared_volume"
    assert comparison.data["volumes"]["shared"] == pytest.approx(1000.0)
    assert comparison.data["volumes"]["candidate_only"] == pytest.approx(64.0)


def test_compound_with_overlapping_members_does_not_double_count():
    # small box fully inside the big box: occupied volume == big box volume
    candidate = _compound(_box(), _box(size=4))

    comparison = compare_solid_volumes(_box(), candidate)

    assert comparison.available
    assert comparison.data["classification"] == "same_occupied_volume"
    assert comparison.data["volumes"]["candidate"] == pytest.approx(1000.0)
    assert comparison.data["volumes"]["shared"] == pytest.approx(1000.0)


def test_surface_without_closed_solid_is_unavailable():
    face = cq.Workplane("XY").rect(10, 10).val().wrapped

    comparison = compare_solid_volumes(face, _box())

    assert not comparison.available
    assert comparison.data["status"] == "unavailable"
    assert comparison.data["reason"]["code"] == "reference_has_no_closed_solid"


def test_boolean_failure_is_structured(monkeypatch):
    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("agentcad.solid_compare._partition_regions", fail)

    comparison = compare_solid_volumes(_box(), _box())

    assert not comparison.available
    assert comparison.data["reason"]["code"] == "boolean_operation_failed"
    assert "synthetic failure" in comparison.data["reason"]["message"]
    assert "kernel" not in comparison.data["reason"]
    assert comparison.data["kernel"]["exact_partition_runs"] == 1
    assert "--visual" in comparison.data["suggestion"]
    assert "does not mean the CAD build failed" in comparison.data["suggestion"]


def test_comparison_uses_independent_inputs_and_one_partition(monkeypatch):
    reference = _box()
    candidate = _box(translation=(5, 0, 0))
    original_partition = solid_compare_module._partition_regions
    captured = []

    def capture_partition(reference_copy, candidate_copy, tolerance_mm):
        captured.append((reference_copy, candidate_copy))
        return original_partition(reference_copy, candidate_copy, tolerance_mm)

    monkeypatch.setattr(
        solid_compare_module,
        "_partition_regions",
        capture_partition,
    )

    comparison = compare_solid_volumes(reference, candidate)

    assert comparison.available
    assert len(captured) == 1
    reference_copy, candidate_copy = captured[0]
    assert not reference.IsPartner(reference_copy)
    assert not candidate.IsPartner(candidate_copy)
    assert comparison.data["kernel"]["non_destructive"] is True
    assert comparison.data["kernel"]["exact_partition_runs"] == 1
    assert comparison.data["volume_semantics"] == {
        "measurement": "physical_occupied_volume",
    }


def test_kernel_warnings_are_exposed_on_success(monkeypatch):
    original_partition = solid_compare_module._partition_regions

    def partition_with_warning(*args, **kwargs):
        shared, reference_only, candidate_only, diagnostics = (
            original_partition(*args, **kwargs)
        )
        diagnostics["warnings"] = [
            {"message": "synthetic kernel warning", "count": 3}
        ]
        return shared, reference_only, candidate_only, diagnostics

    monkeypatch.setattr(
        solid_compare_module,
        "_partition_regions",
        partition_with_warning,
    )

    comparison = compare_solid_volumes(_box(), _box())

    assert comparison.available
    operations = comparison.data["kernel"]["operations"]
    assert operations[-1]["warnings"] == [
        {"message": "synthetic kernel warning", "count": 3}
    ]


def test_repeated_kernel_messages_are_counted_once():
    class SyntheticOperation:
        def DumpWarnings(self, output):
            output.write(b"orientation warning\norientation warning\n")

    warnings = solid_compare_module._dump_kernel_messages(
        SyntheticOperation(),
        "DumpWarnings",
    )

    assert warnings == [{"message": "orientation warning", "count": 2}]


def test_multisolid_canonicalization_failure_never_uses_raw_input(monkeypatch):
    candidate = _compound(_box(), _box(size=4))

    def fail(*_args, **_kwargs):
        raise solid_compare_module._ExactComparisonError(
            "candidate_canonicalization_failed",
            "synthetic canonicalization failure",
        )

    monkeypatch.setattr(
        solid_compare_module,
        "_perform_cells_partition",
        fail,
    )

    comparison = compare_solid_volumes(_box(), candidate)

    assert not comparison.available
    assert (
        comparison.data["reason"]["code"]
        == "candidate_canonicalization_failed"
    )
    assert "synthetic canonicalization failure" in (
        comparison.data["reason"]["message"]
    )
    assert "kernel" not in comparison.data["reason"]
    assert comparison.data["kernel"]["exact_partition_runs"] == 0


def test_compound_semantics_appear_only_after_canonicalization():
    candidate = _compound(_box(), _box(size=4))

    comparison = compare_solid_volumes(_box(), candidate)

    assert comparison.available
    assert "compound_members" in comparison.data["volume_semantics"]


@pytest.mark.parametrize(
    ("raw_volumes", "expected_code"),
    [
        ([2000.0, 0.0, 0.0], "shared_volume_exceeds_input"),
        ([0.0, 1200.0, 1000.0], "subtraction_increased_volume"),
        ([-10.0, 1010.0, 1000.0], "negative_boolean_volume"),
        ([float("nan"), 0.0, 0.0], "non_finite_boolean_volume"),
    ],
)
def test_impossible_raw_volumes_are_not_published(
    monkeypatch,
    raw_volumes,
    expected_code,
):
    raw = iter(raw_volumes)
    monkeypatch.setattr(solid_compare_module, "_volume", lambda _shape: next(raw))

    with pytest.raises(solid_compare_module._ExactComparisonError) as error:
        solid_compare_module._validated_region_volumes(
            1000.0,
            1000.0,
            object(),
            object(),
            object(),
        )

    assert error.value.code == expected_code


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


def test_bounded_comparison_returns_exact_shapes_from_worker(monkeypatch):
    monkeypatch.setenv("AGENTCAD_DIFF_TIMEOUT_S", "10")

    comparison = bounded_compare_solid_volumes(
        _box(),
        _box(translation=(5, 0, 0)),
    )

    assert comparison.available
    assert comparison.data["volumes"]["shared"] == pytest.approx(500.0)
    assert comparison.shared_shape is not None
    assert comparison.reference_only_shape is not None
    assert comparison.candidate_only_shape is not None


def test_bounded_comparison_preserves_compound_occupied_volume(monkeypatch):
    monkeypatch.setenv("AGENTCAD_DIFF_TIMEOUT_S", "10")
    candidate = _compound(_box(), _box(size=4, translation=(0, 0, 7)))

    comparison = bounded_compare_solid_volumes(_box(), candidate)

    assert comparison.available
    assert comparison.data["volumes"]["shared"] == pytest.approx(1000.0)
    assert comparison.data["volumes"]["candidate_only"] == pytest.approx(64.0)


@pytest.mark.parametrize("value", ["invalid", "-1", "nan", "inf"])
def test_invalid_exact_timeout_uses_safe_default(monkeypatch, value):
    monkeypatch.setenv("AGENTCAD_DIFF_TIMEOUT_S", value)

    assert solid_compare_module._exact_timeout_seconds() == 30.0


def test_zero_exact_timeout_disables_dedicated_limit(monkeypatch):
    monkeypatch.setenv("AGENTCAD_DIFF_TIMEOUT_S", "0")

    assert solid_compare_module._exact_timeout_seconds() is None


@pytest.mark.parametrize("value", ["invalid", "-1", "nan", "inf"])
def test_invalid_approximate_timeout_uses_safe_default(monkeypatch, value):
    monkeypatch.setenv("AGENTCAD_APPROX_DIFF_TIMEOUT_S", value)

    assert solid_compare_module._approximate_timeout_seconds() == 30.0


def test_bounded_comparison_kills_worker_after_exact_budget(
    monkeypatch, tmp_path
):
    marker = tmp_path / "worker-finished"
    script = (
        "import pathlib,time; "
        "time.sleep(5); "
        f"pathlib.Path({str(marker)!r}).write_text('not killed')"
    )
    monkeypatch.setenv("AGENTCAD_DIFF_TIMEOUT_S", "0.05")
    monkeypatch.setattr(
        solid_compare_module,
        "_exact_worker_argv",
        lambda *_args: [solid_compare_module.sys.executable, "-c", script],
    )

    comparison = bounded_compare_solid_volumes(_box(), _box())

    assert comparison.data["status"] == "timeout"
    assert comparison.data["reason"]["code"] == "exact_comparison_timeout"
    assert comparison.data["timeout_s"] == pytest.approx(0.05)
    assert "larger AGENTCAD_DIFF_TIMEOUT_S" in comparison.data["suggestion"]
    assert "do not rerun the CAD build" in comparison.data["suggestion"]
    assert not marker.exists()


def test_bounded_approximation_kills_worker_after_its_budget(
    monkeypatch, tmp_path
):
    marker = tmp_path / "approximate-worker-finished"
    script = (
        "import pathlib,time; "
        "time.sleep(5); "
        f"pathlib.Path({str(marker)!r}).write_text('not killed')"
    )
    monkeypatch.setenv("AGENTCAD_APPROX_DIFF_TIMEOUT_S", "0.05")
    monkeypatch.setattr(
        solid_compare_module,
        "_approximate_worker_argv",
        lambda *_args: [solid_compare_module.sys.executable, "-c", script],
    )

    comparison = bounded_approximate_solid_volumes(_box(), _box())

    assert comparison.data["status"] == "timeout"
    assert comparison.data["reason"]["code"] == "approximate_comparison_timeout"
    assert comparison.data["timeout_s"] == pytest.approx(0.05)
    assert not marker.exists()


def test_approximate_comparison_reports_resolution_error_and_direction():
    reference = _box()
    candidate = (
        cq.Workplane("XY")
        .box(10, 10, 10)
        .faces(">Z")
        .workplane()
        .hole(4)
        .val()
        .wrapped
    )

    comparison = approximate_compare_solid_volumes(
        reference,
        candidate,
        resolution_mm=0.5,
    )

    assert comparison.available
    assert comparison.data["method"] == "approximate_voxel_volume"
    assert comparison.data["accuracy"] == "approximate"
    assert comparison.data["resolution_mm"] == 0.5
    assert comparison.data["volumes"]["reference_only"] > 0
    assert comparison.data["volumes"]["candidate_only"] == 0
    error_estimate = comparison.data["error_estimate"]
    assert error_estimate["is_strict_bound"] is False
    assert "not additional measured volumes" in error_estimate["interpretation"]
    assert comparison.data["grid"]["sampling"] == "voxel_center_occupancy"
    assert comparison.data["volume_semantics"] == {
        "measurement": "sampled_voxel_occupancy",
    }
    assert any(
        "not exact CAD Booleans" in limitation
        for limitation in comparison.data["limitations"]
    )


def test_bounded_approximate_comparison_returns_voxel_artifact_shapes():
    comparison = bounded_approximate_solid_volumes(
        _box(),
        _box(translation=(5, 0, 0)),
        resolution_mm=1,
    )

    assert comparison.available
    assert comparison.data["volumes"] == {
        "reference": 1000.0,
        "candidate": 1000.0,
        "shared": 500.0,
        "reference_only": 500.0,
        "candidate_only": 500.0,
        "union": 1500.0,
    }
    assert comparison.shared_shape is not None
    assert comparison.reference_only_shape is not None
    assert comparison.candidate_only_shape is not None
    assert {part["id"] for part in comparison.colored_parts()} == {
        "approximate_shared_volume",
        "approximate_reference_only_volume",
        "approximate_candidate_only_volume",
    }


def test_approximate_grid_coarsens_overly_fine_requested_resolution():
    _origin, dimensions, effective_resolution = solid_compare_module._voxel_grid(
        _box(),
        _box(),
        resolution_mm=0.001,
    )

    assert effective_resolution > 0.001
    assert math.prod(dimensions) <= 250_000


def test_voxel_surface_merges_coplanar_cells():
    merged = solid_compare_module._merge_grid_cells({
        (0, 0), (1, 0), (0, 1), (1, 1), (3, 0),
    })

    assert sorted(merged) == [(0, 0, 2, 2), (3, 0, 4, 1)]


def test_exact_failure_automatically_falls_back_and_keeps_diagnostics(
    monkeypatch,
):
    exact = solid_compare_module._unavailable(
        solid_compare_module._DEFAULT_TOLERANCE_MM,
        "boolean_result_invalid",
        "synthetic exact failure",
    )
    approximate = approximate_compare_solid_volumes(
        _box(),
        _box(translation=(5, 0, 0)),
        resolution_mm=1,
    )
    monkeypatch.setattr(
        solid_compare_module,
        "bounded_compare_solid_volumes",
        lambda *_args, **_kwargs: exact,
    )
    monkeypatch.setattr(
        solid_compare_module,
        "bounded_approximate_solid_volumes",
        lambda *_args, **_kwargs: approximate,
    )
    phases = ComparisonPhaseRecorder()

    comparison = compare_solid_volumes_with_fallback(
        _box(),
        _box(translation=(5, 0, 0)),
        phase_recorder=phases,
    )

    assert comparison.available
    assert comparison.data["method"] == "approximate_voxel_volume"
    assert comparison.data["exact_attempt"]["status"] == "unavailable"
    assert (
        comparison.data["exact_attempt"]["reason"]["code"]
        == "boolean_result_invalid"
    )
    assert phases.entries["exact_3d_comparison"]["status"] == "unavailable"
    assert phases.entries["approximate_3d_comparison"]["status"] == "success"


def test_exact_success_skips_approximation(monkeypatch):
    exact = compare_solid_volumes(_box(), _box())
    monkeypatch.setattr(
        solid_compare_module,
        "bounded_compare_solid_volumes",
        lambda *_args, **_kwargs: exact,
    )
    approximate_called = False

    def approximate(*_args, **_kwargs):
        nonlocal approximate_called
        approximate_called = True

    monkeypatch.setattr(
        solid_compare_module,
        "bounded_approximate_solid_volumes",
        approximate,
    )
    phases = ComparisonPhaseRecorder()

    comparison = compare_solid_volumes_with_fallback(
        _box(),
        _box(),
        phase_recorder=phases,
    )

    assert comparison.data["method"] == "source_frame_boolean_volume"
    assert not approximate_called
    assert phases.entries["exact_3d_comparison"]["status"] == "success"
    assert phases.entries["approximate_3d_comparison"]["status"] == "skipped"
