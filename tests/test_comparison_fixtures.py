"""Integrity and baseline checks for Milestone 3 comparison inputs."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from PIL import ImageStat
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer

from agentcad.solid_compare import (
    approximate_compare_solid_volumes,
    compare_solid_volumes,
)
from agentcad.step_io import load_cad_shape
from agentcad.render import render_comparison_source_views, render_diff_overlay
from scripts.generate_comparison_fixtures import shared_location_pair


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "comparison"
CATALOG = json.loads((FIXTURE_DIR / "catalog.json").read_text())


def _volume(shape):
    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, properties)
    return properties.Mass()


def _solid_count(shape):
    count = 0
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def test_catalog_covers_every_milestone_3_fixture_family():
    expected = {
        "clean_exact_control",
        "shared_transformed_topology",
        "coincident_curved_surfaces",
        "overlapping_multisolid",
        "hidden_underside_geometry",
        "minimized_impeller",
    }
    mapped = {
        family
        for group in (CATALOG["cases"], CATALOG["in_memory_cases"])
        for case in group.values()
        for family in case["families"]
    }
    assert set(CATALOG["families"]) == expected
    assert mapped == expected


@pytest.mark.parametrize(
    "case_name", ["box_bore", "coincident_torus", "covered_rotor"]
)
def test_single_solid_fixture_metrics_match_catalog(case_name):
    case = CATALOG["cases"][case_name]
    reference = load_cad_shape(FIXTURE_DIR / case["reference"])
    candidate = load_cad_shape(FIXTURE_DIR / case["candidate"])

    assert BRepCheck_Analyzer(reference).IsValid()
    assert BRepCheck_Analyzer(candidate).IsValid()
    assert _solid_count(reference) == case["reference_solid_count"]
    assert _solid_count(candidate) == case["candidate_solid_count"]
    assert _volume(reference) == pytest.approx(
        case["reference_volume_mm3"], abs=1e-3
    )
    assert _volume(candidate) == pytest.approx(
        case["candidate_volume_mm3"], abs=1e-3
    )


def test_overlapping_compound_preserves_raw_members_and_occupied_volume():
    case = CATALOG["cases"]["overlapping_compound"]
    reference = load_cad_shape(FIXTURE_DIR / case["reference"])
    candidate = load_cad_shape(FIXTURE_DIR / case["candidate"])

    assert BRepCheck_Analyzer(candidate).IsValid()
    assert _solid_count(candidate) == 2
    assert _volume(candidate) == pytest.approx(
        case["candidate_raw_member_volume_mm3"], abs=1e-3
    )

    comparison = compare_solid_volumes(reference, candidate)
    assert comparison.available
    assert comparison.data["volumes"]["candidate"] == pytest.approx(
        case["candidate_occupied_volume_mm3"], abs=1e-3
    )


def test_box_bore_remains_the_clean_exact_control():
    case = CATALOG["cases"]["box_bore"]
    reference = load_cad_shape(FIXTURE_DIR / case["reference"])
    candidate = load_cad_shape(FIXTURE_DIR / case["candidate"])

    comparison = compare_solid_volumes(reference, candidate)

    assert comparison.available
    assert comparison.data["volumes"]["reference"] == 32000.0
    assert comparison.data["volumes"]["candidate"] == pytest.approx(29738.0533)
    assert comparison.data["volumes"]["reference_only"] == pytest.approx(
        2261.9467
    )
    assert comparison.data["volumes"]["candidate_only"] == 0.0


def test_approximate_box_bore_preserves_removed_feature_direction_and_error():
    case = CATALOG["cases"]["box_bore"]
    comparison = approximate_compare_solid_volumes(
        load_cad_shape(FIXTURE_DIR / case["reference"]),
        load_cad_shape(FIXTURE_DIR / case["candidate"]),
        resolution_mm=1,
    )

    exact_reference_only = 2261.9467
    approximate = comparison.data["volumes"]
    estimated_error = comparison.data["error_estimate"]["absolute_volume"]
    assert comparison.available
    assert approximate["reference_only"] > 0
    assert approximate["candidate_only"] == 0
    assert abs(approximate["reference_only"] - exact_reference_only) <= (
        estimated_error["reference_only"]
    )


def test_approximate_compound_preserves_added_feature_direction_and_error():
    case = CATALOG["cases"]["overlapping_compound"]
    comparison = approximate_compare_solid_volumes(
        load_cad_shape(FIXTURE_DIR / case["reference"]),
        load_cad_shape(FIXTURE_DIR / case["candidate"]),
        resolution_mm=1,
    )

    approximate = comparison.data["volumes"]
    estimated_error = comparison.data["error_estimate"]["absolute_volume"]
    assert comparison.available
    assert "compound_members" in comparison.data["volume_semantics"]
    assert approximate["candidate_only"] > 0
    assert approximate["reference_only"] == 0
    assert abs(approximate["candidate_only"] - 144.0) <= (
        estimated_error["candidate_only"]
    )


def test_shared_transform_fixture_really_shares_topology():
    reference, transformed = shared_location_pair()

    assert reference.IsPartner(transformed)
    assert not reference.IsSame(transformed)
    assert _volume(reference) == pytest.approx(1000.0)
    assert _volume(transformed) == pytest.approx(1000.0)

    comparison = compare_solid_volumes(reference, transformed)

    assert comparison.available
    assert comparison.data["classification"] == "partial_shared_volume"
    assert comparison.data["volumes"]["reference_only"] == pytest.approx(
        comparison.data["volumes"]["candidate_only"],
        abs=1e-4,
    )
    # The caller-owned aliases remain untouched; the engine compared deep
    # copies rather than handing shared topology to native Boolean code.
    assert reference.IsPartner(transformed)
    assert _volume(reference) == pytest.approx(1000.0)
    assert _volume(transformed) == pytest.approx(1000.0)


def test_default_views_expose_covered_rotor_underside_change(tmp_path):
    case = CATALOG["cases"]["covered_rotor"]
    reference = load_cad_shape(FIXTURE_DIR / case["reference"])
    candidate = load_cad_shape(FIXTURE_DIR / case["candidate"])
    source_views = render_comparison_source_views(
        reference,
        candidate,
        per_view_size=64,
    )

    projection = render_diff_overlay(
        reference,
        candidate,
        "7 blades",
        "5 blades",
        tmp_path / "covered_rotor_overlay.png",
        width=128,
        height=128,
        source_views=source_views,
    )

    views = {view["view"]: view for view in projection["views"]}
    assert set(views) == {"top", "bottom", "upper_iso", "lower_iso"}
    assert views["top"]["coincident_fraction_of_union"] == pytest.approx(
        case["top_projection_iou"]
    )
    assert views["lower_iso"]["reference_only_fraction_of_union"] > 0.02
    assert (
        source_views.reference[0].tobytes()
        == source_views.candidate[0].tobytes()
    )
    assert (
        source_views.reference[1].tobytes()
        != source_views.candidate[1].tobytes()
    )
    assert sum(ImageStat.Stat(source_views.reference[1]).mean) / 3 > 100
    assert (
        source_views.reference[3].tobytes()
        != source_views.candidate[3].tobytes()
    )


def test_checked_in_fixtures_stay_small_enough_for_the_public_repo():
    for path in FIXTURE_DIR.glob("*.step"):
        assert path.stat().st_size < 1_000_000, path.name


def test_checked_in_steps_match_the_deterministic_generator(tmp_path):
    script = Path(__file__).parents[1] / "scripts" / "generate_comparison_fixtures.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--output-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr

    checked_in = sorted(path.name for path in FIXTURE_DIR.glob("*.step"))
    generated = sorted(path.name for path in tmp_path.glob("*.step"))
    assert generated == checked_in
    for filename in checked_in:
        assert (tmp_path / filename).read_bytes() == (
            FIXTURE_DIR / filename
        ).read_bytes()


def test_baseline_harness_records_real_cli_phases_for_one_case():
    script = Path(__file__).parents[1] / "scripts" / "capture_comparison_baselines.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--case", "box_bore"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    captured = payload["cases"]["box_bore"]
    response = captured["response"]
    assert captured["wall_ms"] > 0
    assert response["status"] == "success"
    assert response["comparison_3d"]["status"] == "success"
    assert response["comparison_phases"]["source_loading"]["status"] == "success"
    assert (
        response["comparison_phases"]["exact_3d_comparison"]["status"]
        == "success"
    )


def test_baseline_harness_can_capture_only_an_external_pair():
    script = Path(__file__).parents[1] / "scripts" / "capture_comparison_baselines.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--external-reference",
            str(FIXTURE_DIR / "box.step"),
            "--external-candidate",
            str(FIXTURE_DIR / "bored_box.step"),
            "--external-only",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert list(payload["cases"]) == ["external_pair"]
    assert payload["cases"]["external_pair"]["response"]["status"] == "success"
