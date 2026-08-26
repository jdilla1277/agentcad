import pytest

from agentcad.comparison_phases import (
    COMPARISON_PHASES,
    ComparisonPhaseRecorder,
)


def test_recorder_has_stable_order_and_observed_duration():
    recorder = ComparisonPhaseRecorder()

    with recorder.observe("source_loading"):
        pass
    recorder.finalize_pending("not requested")

    assert tuple(recorder.entries) == COMPARISON_PHASES
    assert recorder.entries["source_loading"]["status"] == "success"
    assert recorder.entries["source_loading"]["duration_ms"] >= 0
    for name in COMPARISON_PHASES[1:]:
        assert recorder.entries[name] == {
            "status": "skipped",
            "message": "not requested",
        }


def test_recorder_attributes_exception_and_preserves_message():
    recorder = ComparisonPhaseRecorder()

    with pytest.raises(RuntimeError, match="exact exploded"):
        with recorder.observe("exact_3d_comparison"):
            raise RuntimeError("exact exploded")

    entry = recorder.entries["exact_3d_comparison"]
    assert entry["status"] == "failed"
    assert entry["duration_ms"] >= 0
    assert entry["message"] == "RuntimeError: exact exploded"


def test_recorder_accepts_structured_unavailable_outcome():
    recorder = ComparisonPhaseRecorder()

    with recorder.observe("exact_3d_comparison") as phase:
        phase.status = "unavailable"
        phase.message = "Boolean result was invalid."

    entry = recorder.entries["exact_3d_comparison"]
    assert entry["status"] == "unavailable"
    assert entry["duration_ms"] >= 0
    assert entry["message"] == "Boolean result was invalid."


def test_repeated_source_loading_spans_are_accumulated(monkeypatch):
    ticks = iter((10.0, 10.002, 20.0, 20.003))
    monkeypatch.setattr(
        "agentcad.comparison_phases.time.perf_counter", lambda: next(ticks)
    )
    recorder = ComparisonPhaseRecorder()

    with recorder.observe("source_loading"):
        pass
    with recorder.observe("source_loading"):
        pass

    assert recorder.entries["source_loading"] == {
        "status": "success",
        "duration_ms": 5,
    }
