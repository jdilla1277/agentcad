"""One kernel check per side: run must not repeat BRepCheck or tessellation.

Fixture 202 of the CADGenBench Nano review (a 2,157-face imported part) spent
about 125 s in a single kernel check and a run performed it four times:
whole-shape metrics, per-part metrics, in-memory source validation, and the
reloaded STEP. These tests pin the deduplicated contract.
"""

import json

import pytest

from agentcad.cli import cli
from agentcad.export_validation import compare_step_reports
from agentcad.metrics import compute_metrics
from agentcad.step_io import load_cad_shape
from agentcad.validation import round_trip_skip_layers, validate_shape

from tests.test_written_mesh_validation import FIXTURES


def _box(x=10, y=10, z=10):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    return BRepPrimAPI_MakeBox(x, y, z).Shape()


def test_compute_metrics_can_skip_the_kernel_check():
    with_check = compute_metrics(_box())
    without = compute_metrics(_box(), check_validity=False)
    assert with_check["is_valid"] is True
    assert "is_valid" not in without and "validity_errors" not in without
    assert without["volume"] == with_check["volume"] == 1000.0


def test_validate_shape_skip_layers_leaves_verdict_null():
    report = validate_shape(_box(), skip_layers=("brep_check", "mesh_manifold"))
    assert report["layers"]["brep_check"]["status"] == "skipped"
    assert report["layers"]["mesh_manifold"]["status"] == "skipped"
    assert report["layers"]["shell_closure"]["status"] == "pass"
    assert report["layers"]["structure"]["solid_count"] == 1
    assert report["is_valid"] is None
    assert report["first_failure"] is None
    assert report["undetermined_layer"] == "brep_check"


def test_validate_shape_rejects_unknown_skip_layer():
    with pytest.raises(ValueError, match="Unknown validation layers"):
        validate_shape(_box(), skip_layers=("tessellate",))


def test_round_trip_skip_policy_follows_face_count(monkeypatch):
    from agentcad import validation
    assert round_trip_skip_layers(_box()) == ()
    monkeypatch.setattr(validation, "ROUND_TRIP_FULL_FACE_LIMIT", 5)
    assert round_trip_skip_layers(_box()) == ("brep_check", "mesh_manifold")


def test_compare_ignores_skipped_layers_and_names_them():
    before = validate_shape(_box(), skip_layers=("brep_check", "mesh_manifold"))
    after = validate_shape(_box())
    result = compare_step_reports(before, after)
    assert result["matches"] is True, result
    assert result["skipped_layers"] == ["brep_check", "mesh_manifold"]
    assert result["skipped_reason"] == "Not run on this shape at the caller's request."
    assert result["differences"] == []
    assert "not compared" in result["message"]


def test_compare_with_skipped_layers_still_detects_a_lost_body(tmp_path):
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from agentcad.step_io import write_step_shape
    source = load_cad_shape(FIXTURES / "disjoint_solids.step")
    before = validate_shape(source, skip_layers=("brep_check", "mesh_manifold"))
    path = tmp_path / "lost-body.step"
    write_step_shape(TopExp_Explorer(source, TopAbs_SOLID).Current(), path)
    after = validate_shape(load_cad_shape(path))
    result = compare_step_reports(before, after)
    assert result["matches"] is False
    assert {"field": "solid_count", "before": 2, "after": 1} in result["differences"]


def test_compare_keeps_timeouts_unknown():
    before = validate_shape(_box())
    after = validate_shape(_box())
    after["layers"]["mesh_manifold"] = {"status": "timeout"}
    after["is_valid"] = None
    result = compare_step_reports(before, after)
    assert result["matches"] is None
    assert result["skipped_layers"] == []
    assert result["skipped_reason"] is None


class _CountingAnalyzer:
    """Stand-in for BRepCheck_Analyzer that counts agentcad's constructions.

    build123d runs its own kernel checks while the script executes; only
    calls made from agentcad's modules are the run contract under test.
    """

    calls: list = []
    real = None  # the unpatched class, captured before patching

    def __init__(self, shape, *args, **kwargs):
        import sys
        caller = sys._getframe(1).f_code.co_filename.replace("\\", "/")
        if "/agentcad/" in caller and "/site-packages/build123d/" not in caller:
            type(self).calls.append(caller.rsplit("/", 1)[-1])
        self._real = type(self).real(shape, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _count_kernel_checks(monkeypatch):
    import OCP.BRepCheck
    from agentcad import metrics
    _CountingAnalyzer.calls = []
    _CountingAnalyzer.real = OCP.BRepCheck.BRepCheck_Analyzer
    monkeypatch.setattr(metrics, "BRepCheck_Analyzer", _CountingAnalyzer)
    monkeypatch.setattr(OCP.BRepCheck, "BRepCheck_Analyzer", _CountingAnalyzer)
    return _CountingAnalyzer.calls


def _run(runner, isolated_dir, source, label):
    assert runner.invoke(cli, ["init", "--runtime", "build123d"]).exit_code == 0
    (isolated_dir / "part.py").write_text(source)
    result = runner.invoke(cli, ["run", "part.py", "--label", label,
                                 "--no-preview", "--no-view", "--no-diff", "--no-daemon"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def test_run_single_part_checks_the_kernel_once_per_side(runner, isolated_dir, monkeypatch):
    calls = _count_kernel_checks(monkeypatch)
    data = _run(runner, isolated_dir, "show_object(Box(10, 20, 5))", "one")
    # Source validation plus the reloaded STEP; metrics no longer add checks.
    assert len(calls) == 2, len(calls)
    assert data["metrics"]["is_valid"] is True
    assert data["parts"][0]["metrics"]["is_valid"] is True
    assert data["validation"]["step_round_trip"]["matches"] is True
    assert data["validation"]["step_round_trip"]["skipped_layers"] == []
    assert data["validation"]["step_round_trip"]["skipped_reason"] is None
    # The single part reuses the whole-shape metrics; both carry the verdict.
    assert data["parts"][0]["metrics"]["volume"] == data["metrics"]["volume"]


def test_run_large_source_skips_expensive_layers_but_keeps_the_gate(runner, isolated_dir, monkeypatch):
    from agentcad import validation
    monkeypatch.setattr(validation, "ROUND_TRIP_FULL_FACE_LIMIT", 0)
    calls = _count_kernel_checks(monkeypatch)
    data = _run(runner, isolated_dir, "show_object(Box(10, 20, 5))", "big")
    assert len(calls) == 1, len(calls)  # the reloaded STEP only
    trip = data["validation"]["step_round_trip"]
    assert trip["matches"] is True, trip
    assert trip["skipped_layers"] == ["brep_check", "mesh_manifold"]
    assert "reloaded STEP" in trip["skipped_reason"]
    assert trip["before"]["layers"]["brep_check"] == "skipped"
    assert trip["after"]["layers"]["brep_check"] == "pass"
    assert data["validation"]["is_valid"] is True
    assert data["metrics"]["is_valid"] is True
    assert data["parts"][0]["metrics"]["is_valid"] is True
    meta = json.loads((isolated_dir / "v1_big" / "meta.json").read_text())
    assert meta["validation"]["step_round_trip"] == trip


def test_run_multi_part_keeps_per_part_kernel_verdicts(runner, isolated_dir, monkeypatch):
    calls = _count_kernel_checks(monkeypatch)
    data = _run(runner, isolated_dir,
                "show_object(Box(10, 10, 10), name='a')\n"
                "show_object(Pos(30, 0, 0) * Box(5, 5, 5), name='b')", "two")
    # Two per-part checks, source validation, reloaded STEP.
    assert len(calls) == 4, len(calls)
    assert [p["metrics"]["is_valid"] for p in data["parts"]] == [True, True]
    assert "is_valid" in data["metrics"]


def _load_brep(path):
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape
    shape = TopoDS_Shape()
    assert BRepTools.Read_s(shape, str(path), BRep_Builder())
    return shape


class _StatusCountingAnalyzer:
    """Wrap BRepCheck_Analyzer and count how many entity results are read."""

    def __init__(self, shape):
        from OCP.BRepCheck import BRepCheck_Analyzer
        self._real = BRepCheck_Analyzer(shape)
        self.result_reads = 0

    def IsValid(self, *args):
        return self._real.IsValid(*args)

    def Result(self, entity):
        self.result_reads += 1
        return self._real.Result(entity)


def test_kernel_evidence_reads_statuses_only_for_invalid_entities():
    """Reading every entity's status list cost minutes on large parts."""
    from agentcad.metrics import extract_validity_errors
    from agentcad.validation import _layer_brep_check
    shape = _load_brep(FIXTURES / "bowtie_prism_invalid.brep")
    counting = _StatusCountingAnalyzer(shape)
    assert counting.IsValid() is False
    errors = extract_validity_errors(counting, shape)
    assert errors, "fixture must be kernel-invalid"
    total = sum(1 for _ in _iter_entities(shape))
    assert 0 < counting.result_reads < total, (counting.result_reads, total)
    layer = _layer_brep_check(shape)
    assert layer["status"] == "fail" and layer["errors"] == errors
    assert layer["entity_count"] >= 1


def _iter_entities(shape):
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    for kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_WIRE, TopAbs_SHELL):
        explorer = TopExp_Explorer(shape, kind)
        while explorer.More():
            yield explorer.Current()
            explorer.Next()
