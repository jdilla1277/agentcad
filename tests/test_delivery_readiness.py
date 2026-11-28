"""Readiness must fail closed when geometry evidence is incomplete."""

import pytest

from agentcad.core_build import delivery_readiness
from agentcad.validation import LAYERS


def passing_report():
    return {"profile": "deliverable", "is_valid": True,
            "layers": {layer.name: {"status": "pass"} for layer in LAYERS}}


@pytest.mark.parametrize("status,reason", [
    ("skipped", "mesh_manifold_not_checked"),
    ("timeout", "mesh_manifold_timeout"),
    ("error", "mesh_manifold_error"),
    ("blocked", "mesh_manifold_blocked"),
    ("fail", "mesh_manifold_failed"),
])
def test_incomplete_gate_blocks_readiness_even_if_aggregate_says_valid(status, reason):
    report = passing_report()
    report["layers"]["mesh_manifold"]["status"] = status
    assert delivery_readiness(report)["blocking_reasons"] == [reason]
    assert delivery_readiness(report)["ready"] is False


def test_missing_evidence_is_not_a_pass_and_advisory_does_not_gate():
    report = passing_report()
    report["layers"]["advisory"]["status"] = "error"
    assert delivery_readiness(report)["ready"] is True
    del report["layers"]["shell_closure"]
    assert delivery_readiness(report)["blocking_reasons"] == ["shell_closure_not_checked"]


def test_declared_structure_expectations_cannot_be_waived():
    report = passing_report()
    report["layers"]["structure"] = {"status": "error", "gates": True}
    assert delivery_readiness(report)["blocking_reasons"] == ["structure_error"]
