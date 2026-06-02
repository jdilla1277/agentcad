"""build123d runner — CQGI-replacement contract tests.

Port of the Phase 0 spike tests (`spike_build123d/test_runner.py`) onto
the production module path (`agentcad.runners.build123d`). Each test
maps to one question the runner must answer for the migration to be safe:

  1. Does basic script execution work?
  2. Does show_object() capture work?
  3. Does parameter discovery + injection work?
  4. Do multiple show_object() calls combine into a compound?
  5. Do runtime errors surface cleanly (not crash the harness)?
  6. Does the resulting OCP shape flow through the existing metrics code?
  7. Does the resulting OCP shape flow through STEP export unchanged?
  8. Do missing-result and unknown-parameter errors report usefully?
"""

from __future__ import annotations

from pathlib import Path

from agentcad.metrics import compute_metrics
from agentcad.runners import build123d as b3d_runner
from agentcad.runners.build123d import discover_parameters


FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_box_runs_and_produces_shape():
    result = b3d_runner.execute(_read("box.py"))
    assert result.success, result.exception
    assert result.native_shape is not None
    assert result.topo_shape is not None


def test_show_object_captures_result():
    result = b3d_runner.execute(_read("box.py"))
    assert result.success
    # Single capture → native_shape is the captured Shape directly (not a compound).
    assert result.native_shape is not None


def test_parameter_discovery():
    source = b3d_runner.with_preamble(_read("parametric_bracket.py"))
    params = discover_parameters(source)
    assert params == {"length": 60, "width": 20, "thickness": 4, "hole_dia": 6}


def test_parameter_override_changes_geometry():
    baseline = b3d_runner.execute(_read("parametric_bracket.py"))
    overridden = b3d_runner.execute(_read("parametric_bracket.py"), params={"length": 120})
    assert baseline.success and overridden.success

    baseline_bbox = baseline.native_shape.bounding_box()
    overridden_bbox = overridden.native_shape.bounding_box()
    assert round(baseline_bbox.size.X, 3) == 60.0
    assert round(overridden_bbox.size.X, 3) == 120.0


def test_unknown_parameter_rejected():
    result = b3d_runner.execute(_read("parametric_bracket.py"), params={"notathing": 1})
    assert not result.success
    assert result.status == "validation_error"
    assert "Unknown parameter" in result.exception
    assert "notathing" in result.exception


def test_multiple_show_objects_compound():
    result = b3d_runner.execute(_read("multi_show.py"))
    assert result.success
    assert result.native_shape is not None
    # Two captures → runner records each as a part. The native_shape is the
    # auto-compound for downstream export/metrics, but the per-part breakdown
    # is what callers should reach for. No warning — the parts list replaces
    # the old "consider makeCompound()" tip.
    assert result.warnings == []
    assert len(result.parts) == 2
    assert [p["id"] for p in result.parts] == [0, 1]
    assert [p["name"] for p in result.parts] == [None, None]


def test_no_show_object_fails_usefully():
    result = b3d_runner.execute("x = Box(1, 1, 1)\n")
    assert not result.success
    assert result.status == "execution_error"
    assert "show_object" in result.exception


def test_runtime_error_is_caught():
    result = b3d_runner.execute("show_object(Box(0, 0, 0))\n")
    assert not result.success
    assert result.status == "execution_error"
    assert result.traceback is not None


def test_metrics_work_on_build123d_output():
    """The critical integration test: existing metrics code consumes a
    build123d-produced TopoDS_Shape unchanged."""
    result = b3d_runner.execute(_read("box.py"))
    metrics = compute_metrics(result.topo_shape)

    assert round(metrics["volume"], 2) == 1000.0
    assert round(metrics["surface_area"], 2) == 700.0
    assert metrics["face_count"] == 6
    assert metrics["edge_count"] == 12
    assert metrics["is_valid"] is True


def test_metrics_work_on_boolean_result():
    result = b3d_runner.execute(_read("boolean_ops.py"))
    metrics = compute_metrics(result.topo_shape)
    assert metrics["is_valid"] is True
    assert metrics["volume"] > 0
    assert metrics["face_count"] > 6


def test_step_export_accepts_build123d_shape(tmp_path):
    from build123d import export_step

    result = b3d_runner.execute(_read("box.py"))
    out = tmp_path / "out.step"
    export_step(result.native_shape, str(out))
    assert out.exists() and out.stat().st_size > 0


# --- ShapeList footgun (issue #97) ---

_SINGLE_SHAPELIST_SCRIPT = """\
from build123d import Box, ShapeList
result = ShapeList([Box(10, 10, 10)])
show_object(result)
"""

_MULTI_SHAPELIST_SCRIPT = """\
from build123d import Box, ShapeList
result = ShapeList([Box(10, 10, 10), Box(5, 5, 5)])
show_object(result)
"""

_EMPTY_SHAPELIST_SCRIPT = """\
from build123d import ShapeList
show_object(ShapeList([]))
"""


def test_show_object_accepts_single_element_shapelist():
    """A boolean op that yields ShapeList[Shape] of length 1 should not
    crash show_object — the common-case agent expectation is ``one shape``."""
    result = b3d_runner.execute(_SINGLE_SHAPELIST_SCRIPT)
    assert result.success, result.exception
    assert result.native_shape is not None
    assert result.topo_shape is not None
    metrics = compute_metrics(result.topo_shape)
    assert round(metrics["volume"], 2) == 1000.0


def test_show_object_rejects_multi_element_shapelist_with_hint():
    """A genuine multi-piece ShapeList should fail with a recovery hint —
    the agent likely meant one shape and the cut produced more."""
    result = b3d_runner.execute(_MULTI_SHAPELIST_SCRIPT)
    assert not result.success
    assert result.status == "execution_error"
    msg = result.exception or ""
    assert "ShapeList" in msg
    assert "show_object" in msg
    # Recovery hint must point at the canonical fix.
    assert "once per" in msg or "one at a time" in msg or "each" in msg


def test_show_object_rejects_empty_shapelist_with_hint():
    """Empty ShapeList means the boolean op produced no result — say so."""
    result = b3d_runner.execute(_EMPTY_SHAPELIST_SCRIPT)
    assert not result.success
    assert result.status == "execution_error"
    msg = result.exception or ""
    assert "empty" in msg.lower()


def test_runner_does_not_shadow_show_object_capture_with_warnings_record():
    """Regression for the variable-shadowing bug introduced by PR #142.
    `warnings.catch_warnings(record=True) as captured` rebound the
    show_object capture list, breaking every build123d script with
    `AttributeError: 'tuple' object has no attribute 'category'`.

    Pins the integration: a script with both `show_object` and
    `warnings.warn` should surface BOTH cleanly — the geometry through
    `result.topo_shape`, the warning string through `result.warnings`,
    with no cross-contamination between the two lists.
    """
    src = (
        "import warnings\n"
        "warnings.warn('test_b3d_warning_from_body')\n"
        "from build123d import Box\n"
        "show_object(Box(10, 10, 10))\n"
    )
    result = b3d_runner.execute(src)

    # show_object capture works.
    assert result.success, result.exception
    assert result.topo_shape is not None

    # Warning capture works.
    assert "test_b3d_warning_from_body" in (result.warnings or [])

    # No cross-contamination: every warnings entry is a string, not a
    # show_object tuple or a raw WarningMessage object.
    for w in result.warnings or []:
        assert isinstance(w, str), f"warnings contains non-string: {type(w).__name__}"
