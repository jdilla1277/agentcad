"""Declared requirements are evaluated against pinned written geometry."""

import copy

import pytest

from agentcad import change_contract as contract
from agentcad.change_checks import (
    compile_policy,
    evaluate_checks,
    measure_geometry,
    probe_boxes_for_policy,
)
from agentcad.feature_checks import check_holes


def declaration(*checks, **extra):
    return {"schema_version": 1, "checks": list(checks), **extra}


def dimension(**extra):
    return {
        "check_id": "length",
        "kind": "dimension",
        "axis": "x",
        "expected_mm": 80,
        "tolerance_mm": 0.1,
        **extra,
    }


def candidate(policy):
    return contract.make_candidate(
        revision_id="r_test",
        inputs=[
            {
                "name": "output.step",
                "role": "geometry",
                "path": "geometry/output.step",
                "sha256": "a" * 64,
                "size_bytes": 10,
            }
        ],
        model={},
        policies=[policy],
    )


def test_dimension_reports_exact_expected_observed_tolerance_and_advisory():
    policy = compile_policy(
        declaration(dimension(), dimension(check_id="height", axis="z", required=False))
    )
    results = evaluate_checks(
        candidate(policy), policy, {"dimensions": {"x": 79.8, "z": 79.8}, "cylinders": []}
    )
    assert [r["state"] for r in results] == ["fail", "fail"]
    assert results[0]["observed"]["expected_mm"] == 80
    assert results[0]["observed"]["measured_mm"] == 79.8
    assert results[0]["observed"]["tolerance_mm"] == 0.1
    assert results[0]["actions"]
    receipt = contract.make_change_receipt(candidate(policy), policy, results)
    assert [b["check_id"] for b in receipt["blockers"]] == ["length"]
    assert [w["check_id"] for w in receipt["warnings"]] == ["height"]


def test_policy_inherits_and_requires_audited_changes_instead_of_dropping_requirements():
    original = compile_policy(declaration(dimension()))
    assert compile_policy(None, previous=original) == original
    assert compile_policy(declaration(dimension()), previous=original) == original
    with pytest.raises(ValueError, match="actor|rationale"):
        compile_policy(declaration(), previous=original)
    changed = compile_policy(
        declaration(dimension(required=False), actor="human", rationale="Length becomes advisory"),
        previous=original,
    )
    assert changed["version"] == 2
    assert changed["change"]["requirements_diff"]["checks"]["before"] == original["checks"]
    assert original["version"] == 1


@pytest.mark.parametrize(
    "bad",
    [
        dimension(expected_mm=True),
        dimension(tolerance_mm=-1),
        dimension(expected_mm=float("nan")),
        dimension(axis="all"),
        dimension(kind="guessed"),
        dimension(misspelled=True),
    ],
)
def test_bad_requirements_are_rejected_before_geometry_work(bad):
    with pytest.raises(ValueError):
        compile_policy(declaration(bad))


def test_missing_measurements_and_unsupported_validator_are_unavailable_not_failed():
    policy = compile_policy(
        declaration(
            dimension(), {"check_id": "vendor", "kind": "external", "validator": "vendor-test"}
        )
    )
    results = evaluate_checks(candidate(policy), policy, None, reason="STEP unavailable")
    assert all(r["state"] == "unavailable" for r in results)
    assert all("STEP unavailable" in r["observed"]["reason"] for r in results)
    receipt = contract.make_change_receipt(candidate(policy), policy, results)
    assert receipt["validation_state"] == "unknown"


def test_preservation_compares_centers_not_only_unchanged_diameter_and_count():
    definition = {
        "check_id": "mounts",
        "kind": "preserve_cylinders",
        "diameter_mm": 6,
        "axis": "z",
        "tolerance_mm": 0.05,
        "baseline_revision_id": "r_" + "1" * 32,
    }
    policy = compile_policy(declaration(definition))
    baseline = {
        "dimensions": {},
        "cylinders": [
            {
                "diameter_mm": 6,
                "direction": [0, 0, 1],
                "center_mm": [-20, 0, 0],
                "face_indices": [2],
            },
            {
                "diameter_mm": 6,
                "direction": [0, 0, 1],
                "center_mm": [20, 0, 0],
                "face_indices": [4],
            },
        ],
    }
    measured = copy.deepcopy(baseline)
    assert (
        evaluate_checks(candidate(policy), policy, measured, baselines={"mounts": baseline})[0][
            "state"
        ]
        == "pass"
    )
    measured["cylinders"][1]["center_mm"][0] += 3
    result = evaluate_checks(candidate(policy), policy, measured, baselines={"mounts": baseline})[0]
    assert result["state"] == "fail"
    assert result["observed"]["max_displacement_mm"] == pytest.approx(3)
    assert result["observed"]["affected_face_indices"] == [2, 4]


def test_written_bore_is_not_rounded_into_tolerance(tmp_path):
    from build123d import Box, Cylinder, Pos, export_step

    part = Box(80, 30, 6) - Cylinder(5.8, 10) - Pos(-30, 0, 0) * Cylinder(3, 10)
    path = tmp_path / "bracket.step"
    export_step(part, path)
    measured = measure_geometry(path)
    policy = compile_policy(
        declaration(
            {
                "check_id": "bore",
                "kind": "cylinders",
                "diameter_mm": 12,
                "count": 1,
                "axis": "z",
                "tolerance_mm": 0.1,
            }
        )
    )
    result = evaluate_checks(candidate(policy), policy, measured)[0]
    assert result["state"] == "fail"
    assert any(c["diameter_mm"] == pytest.approx(11.6) for c in result["observed"]["cylinders"])


def test_preservation_checks_diameter_change_relative_to_the_baseline():
    policy = compile_policy(
        declaration(
            {
                "check_id": "mounts",
                "kind": "preserve_cylinders",
                "axis": "z",
                "diameter_mm": 6,
                "tolerance_mm": 0.1,
                "baseline_revision_id": "r_" + "1" * 32,
            }
        )
    )
    baseline = {
        "dimensions": {},
        "cylinders": [
            {
                "diameter_mm": 6.09,
                "direction": [0, 0, 1],
                "center_mm": [0, 0, 0],
                "face_indices": [1],
            }
        ],
    }
    measured = copy.deepcopy(baseline)
    measured["cylinders"][0]["diameter_mm"] = 5.91
    result = evaluate_checks(candidate(policy), policy, measured, baselines={"mounts": baseline})[0]
    assert result["state"] == "fail"
    assert result["observed"]["max_diameter_change_mm"] == pytest.approx(0.18)


def test_matching_is_one_to_one_and_not_greedy_by_cylinder_order():
    policy = compile_policy(
        declaration(
            {
                "check_id": "mounts",
                "kind": "preserve_cylinders",
                "axis": "z",
                "diameter_mm": 6,
                "tolerance_mm": 0.6,
                "baseline_revision_id": "r_" + "1" * 32,
            }
        )
    )

    def model(xs):
        return {
            "dimensions": {},
            "cylinders": [
                {
                    "diameter_mm": 6,
                    "direction": [0, 0, 1],
                    "center_mm": [x, 0, 0],
                    "face_indices": [i + 1],
                }
                for i, x in enumerate(xs)
            ],
        }

    result = evaluate_checks(
        candidate(policy), policy, model([0.5, -0.5]), baselines={"mounts": model([0, 1])}
    )[0]
    assert result["state"] == "pass"
    assert result["observed"]["max_displacement_mm"] == pytest.approx(0.5)

def z_cylinder(diameter, x, y, face):
    return {
        "diameter_mm": diameter,
        "direction": [0, 0, 1],
        "center_mm": [x, y, 0],
        "face_indices": [face],
    }


def test_positioned_holes_and_probes_compile_with_signed_coordinates():
    policy = compile_policy(
        declaration(
            {
                "check_id": "mounts",
                "kind": "holes",
                "axis": "z",
                "diameter_mm": 3,
                "positions_mm": [[-5.5, 2], [3, 3]],
                "position_tolerance_mm": 0.05,
            },
            {
                "check_id": "under_board",
                "kind": "keepout",
                "box_mm": [-2, -2, -5, 2, 2, -1],
            },
            {
                "check_id": "boss",
                "kind": "material",
                "box_mm": [-1, -1, -2, 1, 1, 0],
                "min_volume_mm3": 0.5,
            },
        )
    )
    by_id = {spec["check_id"]: spec for spec in policy["checks"]}
    assert by_id["mounts"]["settings"]["positions_mm"] == [[-5.5, 2.0], [3.0, 3.0]]
    assert by_id["mounts"]["settings"]["tolerance_mm"] == 0.1
    assert by_id["mounts"]["settings"]["position_tolerance_mm"] == 0.05
    assert by_id["under_board"]["settings"]["max_volume_mm3"] == 0.0
    assert by_id["boss"]["settings"]["box_mm"] == [-1.0, -1.0, -2.0, 1.0, 1.0, 0.0]
    assert probe_boxes_for_policy(policy) == [
        [-2.0, -2.0, -5.0, 2.0, 2.0, -1.0],
        [-1.0, -1.0, -2.0, 1.0, 1.0, 0.0],
    ]


@pytest.mark.parametrize(
    "bad",
    [
        {
            "check_id": "mounts",
            "kind": "holes",
            "axis": "z",
            "diameter_mm": 3,
            "positions_mm": [[1, 2], [1, 2]],
        },
        {
            "check_id": "mounts",
            "kind": "holes",
            "axis": "z",
            "diameter_mm": 3,
            "positions_mm": [[1, 2, 3]],
        },
        {
            "check_id": "mounts",
            "kind": "holes",
            "axis": "z",
            "diameter_mm": 3,
            "positions_mm": [],
        },
        {
            "check_id": "void",
            "kind": "keepout",
            "box_mm": [1, 1, 1, -1, 2, 2],
        },
        {
            "check_id": "boss",
            "kind": "material",
            "box_mm": [0, 0, 0, 1, 1, 1],
        },
        {
            "check_id": "boss",
            "kind": "material",
            "box_mm": [0, 0, 0, 1, 1, 1],
            "min_volume_mm3": 0,
        },
    ],
)
def test_bad_position_and_probe_requirements_are_rejected(bad):
    with pytest.raises(ValueError):
        compile_policy(declaration(bad))


def test_positioned_holes_match_one_to_one_and_report_missing_and_extra():
    policy = compile_policy(
        declaration(
            {
                "check_id": "mounts",
                "kind": "holes",
                "axis": "z",
                "diameter_mm": 3,
                "positions_mm": [[0, 0], [10, 0]],
                "position_tolerance_mm": 0.1,
            }
        )
    )
    measured = {
        "dimensions": {},
        "cylinders": [z_cylinder(3, 0, 0, 1), z_cylinder(3, 11, 0, 2)],
    }
    result = evaluate_checks(candidate(policy), policy, measured)[0]
    observed = result["observed"]
    assert result["state"] == "fail"
    assert observed["missing_positions_mm"] == [[10.0, 0.0]]
    assert observed["extra_holes"][0]["position_mm"] == [11.0, 0.0]
    assert observed["max_position_error_mm"] == pytest.approx(1.0)
    assert observed["affected_face_indices"] == [1, 2]

    measured["cylinders"][1]["center_mm"][0] = 10
    result = evaluate_checks(candidate(policy), policy, measured)[0]
    assert result["state"] == "pass"
    assert result["observed"]["max_position_error_mm"] == 0


def test_hole_summary_uses_assigned_error_and_keeps_nearest_diagnostic():
    cylinders = [z_cylinder(3, 0.04, 0, 1), z_cylinder(3, -0.1, 0, 2)]

    passed, observed = check_holes(
        cylinders,
        axis="z",
        diameter_mm=3,
        positions_mm=[[0, 0], [0.1, 0]],
        position_tolerance_mm=0.2,
    )

    assert passed is True
    assert observed["max_position_error_mm"] == pytest.approx(0.1)
    assert observed["max_nearest_position_error_mm"] == pytest.approx(0.06)


def test_holes_ignore_cylinders_outside_the_diameter_and_axis_selection():
    policy = compile_policy(
        declaration(
            {
                "check_id": "mounts",
                "kind": "holes",
                "axis": "z",
                "diameter_mm": 3,
                "positions_mm": [[0, 0]],
                "position_tolerance_mm": 0.1,
                "tolerance_mm": 0.05,
            }
        )
    )
    measured = {
        "dimensions": {},
        "cylinders": [
            z_cylinder(3.2, 0, 0, 1),
            {
                "diameter_mm": 3,
                "direction": [1, 0, 0],
                "center_mm": [0, 0, 0],
                "face_indices": [2],
            },
        ],
    }
    result = evaluate_checks(candidate(policy), policy, measured)[0]
    assert result["state"] == "fail"
    assert result["observed"]["measured_count"] == 0
    assert result["observed"]["missing_positions_mm"] == [[0.0, 0.0]]


@pytest.mark.parametrize(
    ("volume", "states"),
    [(0.0, ["pass", "fail"]), (1.0, ["fail", "pass"])],
)
def test_keepout_and_material_probes_compare_volume(volume, states):
    box = [0, 0, 0, 1, 1, 1]
    policy = compile_policy(
        declaration(
            {
                "check_id": "void",
                "kind": "keepout",
                "box_mm": box,
                "max_volume_mm3": 0,
            },
            {
                "check_id": "boss",
                "kind": "material",
                "box_mm": box,
                "min_volume_mm3": 0.5,
            },
        )
    )
    measured = {
        "dimensions": {},
        "cylinders": [],
        "material_volumes": [{"box_mm": box, "volume_mm3": volume}],
    }
    results = evaluate_checks(candidate(policy), policy, measured)
    assert [result["state"] for result in results] == states
    assert results[0]["observed"]["volume_mm3"] == volume
    assert results[0]["observed"]["max_volume_mm3"] == 0
    assert results[1]["observed"]["min_volume_mm3"] == 0.5


def test_missing_probe_evidence_is_unavailable_not_a_pass():
    policy = compile_policy(
        declaration(
            {
                "check_id": "void",
                "kind": "keepout",
                "box_mm": [0, 0, 0, 1, 1, 1],
            }
        )
    )
    result = evaluate_checks(
        candidate(policy), policy, {"dimensions": {}, "cylinders": []}
    )[0]
    assert result["state"] == "unavailable"
    assert "Material probe evidence" in result["observed"]["reason"]


def test_written_geometry_reports_exact_probe_volumes(tmp_path):
    from build123d import Box, export_step

    part = Box(10, 10, 10)
    path = tmp_path / "cube.step"
    export_step(part, path)
    measured = measure_geometry(
        path, probe_boxes=[[0, 0, 0, 1, 1, 1], [0, 0, 0, 1, 1, 1]]
    )
    assert measured["cylinders"] == []
    assert len(measured["material_volumes"]) == 1
    assert measured["material_volumes"][0]["volume_mm3"] == pytest.approx(1.0)

def test_probe_policy_flows_from_policy_to_measurement_to_result(tmp_path):
    from build123d import Box, export_step

    path = tmp_path / "cube.step"
    export_step(Box(10, 10, 10), path)
    box = [0, 0, 0, 1, 1, 1]
    policy = compile_policy(
        declaration(
            {
                "check_id": "boss",
                "kind": "material",
                "box_mm": box,
                "min_volume_mm3": 0.5,
            },
            {
                "check_id": "void",
                "kind": "keepout",
                "box_mm": box,
                "max_volume_mm3": 0.5,
            },
        )
    )
    measured = measure_geometry(path, probe_boxes=probe_boxes_for_policy(policy))
    results = evaluate_checks(candidate(policy), policy, measured)
    assert [result["state"] for result in results] == ["pass", "fail"]
    assert results[0]["observed"]["volume_mm3"] == pytest.approx(1.0)
    assert results[1]["observed"]["volume_mm3"] == pytest.approx(1.0)
