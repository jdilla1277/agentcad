"""The structure layer reuses the caller's metrics for a single-solid shape.

On real imported parts the tight bounding box costs seconds, and inspect,
measure, import, and recover computed it twice on the same shape: once in
compute_metrics and again as the single solid's evidence.
"""

from pathlib import Path

from build123d import Box, Compound, Edge

from agentcad import topo_ids
from agentcad.core_build import validated_metrics
from agentcad.metrics import compute_metrics
from agentcad.step_io import load_cad_shape
from agentcad.validation import validate_shape

FIXTURES = Path(__file__).parent / "fixtures" / "validation"


def _count_bboxes(monkeypatch):
    calls = []
    real = topo_ids._bbox

    def counting(shape):
        calls.append(shape)
        return real(shape)

    monkeypatch.setattr(topo_ids, "_bbox", counting)
    return calls


def test_single_solid_structure_reuses_metrics_and_matches_recomputation(monkeypatch):
    shape = load_cad_shape(FIXTURES / "closed_box.step")
    metrics = compute_metrics(shape, check_validity=False)
    recomputed = validate_shape(shape)["layers"]["structure"]["solids"]
    calls = _count_bboxes(monkeypatch)
    reused = validate_shape(shape, shape_metrics=metrics)["layers"]["structure"]["solids"]
    assert calls == [], "the single solid's box must come from the caller's metrics"
    assert reused == recomputed


def test_multi_solid_shapes_still_compute_per_solid_evidence(monkeypatch):
    shape = load_cad_shape(FIXTURES / "disjoint_solids.step")
    metrics = compute_metrics(shape, check_validity=False)
    calls = _count_bboxes(monkeypatch)
    structure = validate_shape(shape, shape_metrics=metrics)["layers"]["structure"]
    assert structure["solid_count"] == 2 and len(structure["solids"]) == 2
    assert len(calls) == 2


def test_loose_faces_disable_the_shortcut(monkeypatch):
    shape = load_cad_shape(FIXTURES / "loose_face_compound.step")
    metrics = compute_metrics(shape, check_validity=False)
    calls = _count_bboxes(monkeypatch)
    structure = validate_shape(shape, shape_metrics=metrics)["layers"]["structure"]
    assert structure["faces_outside_solids"] >= 1
    assert len(calls) == structure["solid_count"]


def test_loose_edges_disable_the_shortcut(monkeypatch):
    shape = Compound(children=[
        Box(1, 1, 1),
        Edge.make_line((10, 0, 0), (11, 0, 0)),
    ]).wrapped
    metrics = compute_metrics(shape, check_validity=False)
    calls = _count_bboxes(monkeypatch)
    structure = validate_shape(shape, shape_metrics=metrics)["layers"]["structure"]
    assert structure["solid_count"] == 1
    assert structure["faces_outside_solids"] == 0
    assert len(calls) == 1
    assert structure["solids"][0]["bbox"] != metrics["bounding_box"]


def test_validated_metrics_passes_its_metrics_through(monkeypatch):
    shape = load_cad_shape(FIXTURES / "closed_box.step")
    calls = _count_bboxes(monkeypatch)
    metrics, report = validated_metrics(shape)
    assert calls == []
    assert report["layers"]["structure"]["solids"][0]["bbox"] == metrics["bounding_box"]
    assert metrics["is_valid"] is True
