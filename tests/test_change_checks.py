"""Declared requirements are evaluated against pinned written geometry."""

import copy

import pytest

from agentcad import change_contract as contract
from agentcad.change_checks import compile_policy, evaluate_checks, measure_geometry


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
