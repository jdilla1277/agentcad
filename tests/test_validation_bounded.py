"""Bounded whole-file validation: a stalled native check returns within budget.

The M71 P0 gate: a stalled layer returns ``is_valid: null`` inside the
budget, finished layers survive, an already-proven failure is kept, and an
unfinished gating check is never reported as a pass.
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

from agentcad import validation
from agentcad.cli import cli
from agentcad.validation import bounded_validate_file, validate_file

FIXTURES = Path(__file__).parent / "fixtures" / "validation"


def _use_worker(monkeypatch, budget="30"):
    monkeypatch.setattr(validation, "VALIDATION_WORKER_MIN_BYTES", 0)
    monkeypatch.setenv(validation.VALIDATION_TIMEOUT_ENV, budget)


def _layer_statuses(report):
    return {name: entry["status"] for name, entry in report["layers"].items()}


def test_small_file_validates_in_process():
    report = bounded_validate_file(FIXTURES / "closed_box.step")
    assert report["worker"] == "in_process"
    assert report["is_valid"] is True
    assert set(report["timings"]) == {"reload_ms", "delivered_validation_ms"}


def test_disabled_budget_stays_in_process(monkeypatch):
    monkeypatch.setattr(validation, "VALIDATION_WORKER_MIN_BYTES", 0)
    monkeypatch.setenv(validation.VALIDATION_TIMEOUT_ENV, "0")
    report = bounded_validate_file(FIXTURES / "closed_box.step")
    assert report["worker"] == "in_process"


def test_worker_report_matches_in_process_report(monkeypatch):
    _use_worker(monkeypatch)
    for name in ("closed_box.step", "open_shell.step", "disjoint_solids.step"):
        direct = validate_file(FIXTURES / name)
        worker = bounded_validate_file(FIXTURES / name)
        assert worker["worker"] == "subprocess" and worker["budget_s"] == 30.0
        assert worker["is_valid"] is direct["is_valid"], name
        assert worker["first_failure"] == direct["first_failure"], name
        assert _layer_statuses(worker) == _layer_statuses(direct), name
        assert set(worker["timings"]) == {"reload_ms", "delivered_validation_ms"}


def test_parse_failure_in_worker_is_a_file_parse_report(monkeypatch, tmp_path):
    _use_worker(monkeypatch)
    bad = tmp_path / "bad.step"
    bad.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
    report = bounded_validate_file(bad)
    assert report["worker"] == "subprocess"
    assert report["is_valid"] is False
    assert report["first_failure"] in ("file_parse", "kernel_load")


def _fake_worker(lines, *, raise_timeout=True, returncode=0, stderr=""):
    """Replace _run_worker: write the given layer lines, then report a stall or a failure."""
    def run(argv, budget_s, on_wait=None):
        result = Path(argv[argv.index("-m") + 3])
        result.write_text("".join(json.dumps(line) + "\n" for line in lines))
        if on_wait is not None:
            on_wait(budget_s)
        if raise_timeout:
            return -9, "", True
        return returncode, stderr, False
    return run


def test_timeout_keeps_finished_layers_and_leaves_verdict_null(monkeypatch):
    _use_worker(monkeypatch, budget="7")
    monkeypatch.setattr(validation, "_run_worker", _fake_worker([
        {"layer": "file_parse", "entry": {"status": "pass", "duration_ms": 0}},
        {"layer": "kernel_load", "entry": {"status": "pass", "duration_ms": 0}},
        {"layer": "brep_check", "entry": {"status": "pass", "errors": [], "entities": [],
                                          "entity_count": 0, "evidence_truncated": False, "duration_ms": 3}},
        {"layer": "shell_closure", "entry": {"status": "pass", "duration_ms": 1}},
    ]))
    report = bounded_validate_file(FIXTURES / "closed_box.step")
    statuses = _layer_statuses(report)
    assert statuses["brep_check"] == "pass" and statuses["shell_closure"] == "pass"
    assert statuses["mesh_manifold"] == "timeout"
    assert statuses["structure"] == "skipped" and statuses["advisory"] == "skipped"
    assert report["is_valid"] is None
    assert report["first_failure"] is None
    assert report["undetermined_layer"] == "mesh_manifold"
    assert report["timed_out_layer"] == "mesh_manifold"
    assert report["budget_s"] == 7.0 and report["worker"] == "subprocess"
    assert "7s budget" in report["layers"]["mesh_manifold"]["message"]


def test_timeout_after_a_proven_failure_keeps_the_failure(monkeypatch):
    _use_worker(monkeypatch, budget="7")
    monkeypatch.setattr(validation, "_run_worker", _fake_worker([
        {"layer": "file_parse", "entry": {"status": "pass", "duration_ms": 0}},
        {"layer": "kernel_load", "entry": {"status": "pass", "duration_ms": 0}},
        {"layer": "brep_check", "entry": {"status": "fail", "errors": ["BRepCheck_UnorientableShape"],
                                          "entities": [], "entity_count": 1, "evidence_truncated": False,
                                          "duration_ms": 3}},
    ]))
    report = bounded_validate_file(FIXTURES / "closed_box.step")
    assert report["is_valid"] is False
    assert report["first_failure"] == "brep_check"
    assert report["undetermined_layer"] is None
    assert _layer_statuses(report)["shell_closure"] == "skipped"
    assert _layer_statuses(report)["structure"] == "timeout"  # non-gating work was still running


def test_worker_crash_is_an_error_not_a_pass(monkeypatch):
    _use_worker(monkeypatch)
    monkeypatch.setattr(validation, "_run_worker", _fake_worker([
        {"layer": "file_parse", "entry": {"status": "pass", "duration_ms": 0}},
        {"layer": "kernel_load", "entry": {"status": "pass", "duration_ms": 0}},
    ], raise_timeout=False, returncode=1, stderr="MemoryError: boom"))
    report = bounded_validate_file(FIXTURES / "closed_box.step")
    assert report["is_valid"] is None
    assert report["layers"]["brep_check"]["status"] == "error"
    assert "MemoryError" in report["layers"]["brep_check"]["message"]
    assert "timed_out_layer" not in report


def test_real_worker_is_killed_at_the_budget(monkeypatch):
    """A budget far below the worker's start-up cost must still return promptly."""
    _use_worker(monkeypatch, budget="0.05")
    started = time.perf_counter()
    report = bounded_validate_file(FIXTURES / "closed_box.step")
    assert time.perf_counter() - started < 10
    assert report["is_valid"] is None
    assert report["timed_out_layer"] == "file_parse"
    assert report["layers"]["file_parse"]["status"] == "timeout"


def _run(runner, isolated_dir, label):
    assert runner.invoke(cli, ["init", "--runtime", "build123d"]).exit_code == 0
    (isolated_dir / "part.py").write_text("show_object(Box(10, 20, 5))")
    return runner.invoke(cli, ["run", "part.py", "--label", label,
                               "--no-preview", "--no-view", "--no-diff", "--no-daemon"])


def test_run_saves_the_version_when_delivered_validation_times_out(runner, isolated_dir, monkeypatch):
    _use_worker(monkeypatch, budget="9")
    monkeypatch.setattr(validation, "_run_worker", _fake_worker([
        {"layer": "file_parse", "entry": {"status": "pass", "duration_ms": 0}},
        {"layer": "kernel_load", "entry": {"status": "pass", "duration_ms": 0}},
        {"layer": "brep_check", "entry": {"status": "pass", "errors": [], "entities": [],
                                          "entity_count": 0, "evidence_truncated": False, "duration_ms": 2}},
    ]))
    result = _run(runner, isolated_dir, "slow")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "success" and data["artifact_created"] is True
    assert data["validation"]["is_valid"] is None
    assert data["metrics"]["is_valid"] is None
    assert data["validation"]["timed_out_layer"] == "shell_closure"
    assert any("9s budget" in w and "AGENTCAD_VALIDATION_TIMEOUT_S" in w for w in data["warnings"]), data["warnings"]
    assert "delivered_validation_ms" in data["timings"]
    assert (isolated_dir / "v1_slow" / "output.step").exists()


def test_run_uses_the_worker_for_large_files_and_reports_it(runner, isolated_dir, monkeypatch):
    _use_worker(monkeypatch)
    result = _run(runner, isolated_dir, "big")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["validation"]["worker"] == "subprocess", data["validation"]
    assert data["validation"]["is_valid"] is True, {
        k: v for k, v in data["validation"].items() if k != "layers"} | {
        "statuses": {n: e.get("status") for n, e in data["validation"]["layers"].items()},
        "messages": {n: e.get("message") for n, e in data["validation"]["layers"].items()}}
    assert data["timings"]["reload_ms"] >= 0 and data["timings"]["delivered_validation_ms"] >= 0


def test_import_uses_the_bounded_validator(runner, isolated_dir, monkeypatch):
    import shutil
    _use_worker(monkeypatch)
    assert runner.invoke(cli, ["init", "--runtime", "build123d"]).exit_code == 0
    shutil.copyfile(FIXTURES / "closed_box.step", isolated_dir / "box.step")
    result = runner.invoke(cli, ["import", "box.step", "--label", "box", "--no-daemon"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["validation"]["worker"] == "subprocess"
    assert data["validation"]["is_valid"] is True


def test_worker_wait_emits_progress_callbacks(monkeypatch):
    _use_worker(monkeypatch, budget="30")
    monkeypatch.setattr(validation, "WORKER_WAIT_HEARTBEAT_S", 0.05)
    beats = []
    report = bounded_validate_file(FIXTURES / "closed_box.step", on_wait=beats.append)
    assert report["is_valid"] is True and report["worker"] == "subprocess"
    assert beats and all(b > 0 for b in beats)


def test_run_reports_worker_progress_on_stderr(runner, isolated_dir, monkeypatch):
    _use_worker(monkeypatch)
    monkeypatch.setattr(validation, "WORKER_WAIT_HEARTBEAT_S", 0.05)
    result = _run(runner, isolated_dir, "beat")
    assert result.exit_code == 0, result.output
    assert "worker still running" in result.stderr
