"""Contract tests for the M71 layered validator (Slice 1).

The catalog in ``tests/fixtures/validation`` is the oracle: every case
records the verdict the downstream gate gives and which layer rejects it.
The validator must reproduce both, and must not reject any case the gate
accepts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentcad import validation
from agentcad.step_io import load_cad_shape


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "validation"
CATALOG = json.loads((FIXTURE_DIR / "catalog.json").read_text())
CASES = sorted(CATALOG["cases"])
REAL_WORLD = Path(__file__).parent / "fixtures" / "real_world"


def _load(case):
    return load_cad_shape(FIXTURE_DIR / CATALOG["cases"][case]["file"])


def _validate(case, **kwargs):
    kwargs.setdefault("in_process", True)
    return validation.validate_shape(_load(case), **kwargs)


# ---------------------------------------------------------------------------
# Registry and report shape
# ---------------------------------------------------------------------------


def test_registry_lists_the_seven_deliverable_layers_in_order():
    names = [layer.name for layer in validation.LAYERS]
    assert names == [
        "file_parse",
        "kernel_load",
        "brep_check",
        "shell_closure",
        "mesh_manifold",
        "structure",
        "advisory",
    ]
    assert all(layer.profile == "deliverable" for layer in validation.LAYERS)
    gating = [layer.name for layer in validation.LAYERS if layer.gates]
    assert gating == ["file_parse", "kernel_load", "brep_check", "shell_closure", "mesh_manifold"]


def test_report_has_one_entry_per_layer_with_status_and_duration():
    report = _validate("closed_box")
    assert report["profile"] == "deliverable"
    assert set(report["layers"]) == {layer.name for layer in validation.LAYERS}
    for name, entry in report["layers"].items():
        assert entry["status"] in {"pass", "fail", "skipped", "timeout", "error"}, name
        if entry["status"] in {"pass", "fail", "timeout"}:
            assert isinstance(entry["duration_ms"], int), name
    # Loading is the caller's job; a shape-level validation records it as done.
    assert report["layers"]["file_parse"]["status"] == "pass"
    assert report["layers"]["kernel_load"]["status"] == "pass"


# ---------------------------------------------------------------------------
# Verdicts against the catalog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES)
def test_verdict_matches_the_gate(case):
    entry = CATALOG["cases"][case]
    report = _validate(case)
    expected = entry["expected_gate"] == "pass"
    assert report["is_valid"] is expected, (case, report.get("first_failure"), report.get("message"))
    if expected:
        assert report["first_failure"] is None
    else:
        assert report["first_failure"] == entry["expected_layer"], report["message"]
        assert report["layers"][entry["expected_layer"]]["status"] == "fail"


@pytest.mark.parametrize("case", CASES)
def test_layers_after_the_first_failure_are_skipped_not_guessed(case):
    entry = CATALOG["cases"][case]
    if entry["expected_gate"] == "pass":
        pytest.skip("only failing cases have a first failure")
    report = _validate(case)
    names = [layer.name for layer in validation.LAYERS]
    failed_at = names.index(entry["expected_layer"])
    for name in names[failed_at + 1:]:
        layer = validation.layer_by_name(name)
        if layer.gates:
            assert report["layers"][name]["status"] == "skipped", name


def test_kernel_only_result_lives_in_the_brep_check_layer():
    report = _validate("bowtie_prism_invalid")
    layer = report["layers"]["brep_check"]
    assert layer["status"] == "fail"
    assert "BRepCheck_SelfIntersectingWire" in layer["errors"]
    assert report["first_failure"] == "brep_check"


# ---------------------------------------------------------------------------
# Evidence that localizes the failure
# ---------------------------------------------------------------------------


def test_open_shell_lists_its_free_edges_by_id():
    report = _validate("open_shell")
    layer = report["layers"]["shell_closure"]
    assert layer["status"] == "fail"
    assert layer["open_shell_count"] == 1
    assert layer["free_edge_count"] == 4
    assert len(layer["free_edge_ids"]) == 4
    assert all(isinstance(i, int) and i >= 1 for i in layer["free_edge_ids"])
    # Endpoints let an agent see where the hole is without another call.
    assert all(len(e["endpoints"]) == 2 for e in layer["free_edges"])
    assert "free edge" in report["message"] or "open shell" in report["message"]


def test_loose_face_is_reported_as_an_open_shell_next_to_a_closed_solid():
    report = _validate("loose_face_compound")
    layer = report["layers"]["shell_closure"]
    assert layer["status"] == "fail"
    assert layer["open_shell_count"] == 1
    assert layer["closed_shell_count"] == 1


def test_sewn_cubes_report_the_four_face_edge_with_location():
    report = _validate("nonmanifold_sewn_cubes")
    layer = report["layers"]["mesh_manifold"]
    assert layer["status"] == "fail"
    assert layer["defect"]["kind"] == "non_manifold_edge"
    assert layer["defect"]["triangle_count"] == 4
    loc = layer["defect"]["location"]
    # Cubes are centered on (0,0,0) and (10,10,0), so the shared vertical
    # edge runs along x=5, y=5 and its midpoint is at z=0.
    assert loc["x"] == pytest.approx(5.0, abs=1e-6)
    assert loc["y"] == pytest.approx(5.0, abs=1e-6)
    assert loc["z"] == pytest.approx(0.0, abs=1e-6)
    assert len(layer["defect"]["face_ids"]) == 4
    assert layer["deflection_mm"] > 0


def test_bowtie_prism_reports_a_non_manifold_edge():
    report = _validate("nonmanifold_edge_prism")
    layer = report["layers"]["mesh_manifold"]
    assert layer["status"] == "fail"
    assert layer["defect"]["kind"] == "non_manifold_edge"
    assert layer["defect"]["location"]["x"] == pytest.approx(10.0, abs=1e-6)
    assert layer["defect"]["location"]["y"] == pytest.approx(10.0, abs=1e-6)


def test_structure_layer_counts_solids_and_never_gates():
    report = _validate("disjoint_solids")
    layer = report["layers"]["structure"]
    assert layer["status"] == "pass"
    assert layer["solid_count"] == 2
    assert layer["shell_count"] == 2
    assert report["is_valid"] is True


def test_advisory_layer_reports_fragility_without_gating():
    report = _validate("closed_box")
    layer = report["layers"]["advisory"]
    assert layer["status"] == "pass"
    assert layer["min_face_area_mm2"] == pytest.approx(400.0)
    assert layer["max_tolerance_mm"] >= 0
    assert layer["flags"] == []


# ---------------------------------------------------------------------------
# Real parts must keep passing (the regression bar)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["pump_manifold.step", "cadgenbench_101_agentcad.step"])
def test_real_world_parts_are_deliverable(name):
    report = validation.validate_shape(load_cad_shape(REAL_WORLD / name), in_process=True)
    assert report["is_valid"] is True, report.get("message")


# ---------------------------------------------------------------------------
# Bounded mesh layer
# ---------------------------------------------------------------------------


def test_mesh_layer_runs_in_a_bounded_worker_by_default():
    report = validation.validate_shape(_load("closed_box"))
    assert report["is_valid"] is True
    assert report["layers"]["mesh_manifold"]["status"] == "pass"
    assert report["layers"]["mesh_manifold"]["worker"] == "subprocess"


def test_mesh_timeout_yields_null_verdict_and_keeps_earlier_layers(monkeypatch):
    monkeypatch.setenv(validation.MESH_TIMEOUT_ENV, "0.001")
    report = validation.validate_shape(_load("closed_box"))
    assert report["layers"]["brep_check"]["status"] == "pass"
    assert report["layers"]["shell_closure"]["status"] == "pass"
    assert report["layers"]["mesh_manifold"]["status"] == "timeout"
    assert report["is_valid"] is None
    assert report["first_failure"] is None
    assert report["undetermined_layer"] == "mesh_manifold"
    assert "timeout" in report["message"] or "budget" in report["message"]


def test_load_failure_report_marks_the_parse_layer():
    report = validation.load_failure_report("file_parse", "Undefined parsing: line 2")
    assert report["is_valid"] is False
    assert report["first_failure"] == "file_parse"
    assert report["layers"]["file_parse"]["status"] == "fail"
    assert all(
        report["layers"][name]["status"] == "skipped"
        for name in report["layers"] if name != "file_parse"
    )


# ---------------------------------------------------------------------------
# Deflection ladder (gate parity on fragile meshes)
# ---------------------------------------------------------------------------


def test_ladder_matches_the_gate_and_records_the_rung():
    assert validation.DEFLECTION_LADDER == (1, 4, 16, 32)
    report = _validate("closed_box")
    layer = report["layers"]["mesh_manifold"]
    assert layer["ladder_divisor"] == 1
    assert layer["deflection_mm"] == pytest.approx(layer["requested_deflection_mm"])


def test_ladder_escalates_when_a_coarser_rung_fails(monkeypatch):
    """A rung that fails for a non-topological reason must not be the verdict."""
    calls = []
    real = validation._mesh_manifold_once

    def flaky(shape, deflection, **kwargs):
        calls.append(deflection)
        if len(calls) == 1:
            return {"status": "fail", "deflection_mm": deflection,
                    "defect": {"kind": "missing_triangulation", "face_ids": [3], "count": 1}}
        return real(shape, deflection, **kwargs)

    monkeypatch.setattr(validation, "_mesh_manifold_once", flaky)
    report = _validate("closed_box")
    layer = report["layers"]["mesh_manifold"]
    assert report["is_valid"] is True
    assert layer["ladder_divisor"] == 4
    assert len(calls) == 2 and calls[1] == pytest.approx(calls[0] / 4)


def test_ladder_reports_the_finest_rung_failure_for_real_defects():
    report = _validate("nonmanifold_sewn_cubes")
    layer = report["layers"]["mesh_manifold"]
    assert layer["status"] == "fail"
    assert layer["ladder_divisor"] == 32
    assert layer["defect"]["kind"] == "non_manifold_edge"


def test_triangle_ceiling_stops_the_ladder(monkeypatch):
    monkeypatch.setattr(validation, "MAX_TRIANGLES", 10)  # a box needs 12
    report = _validate("closed_box")
    layer = report["layers"]["mesh_manifold"]
    assert layer["status"] == "fail"
    assert layer["defect"]["kind"] == "triangle_ceiling"
    assert layer["ladder_divisor"] == 1
    assert report["is_valid"] is False
