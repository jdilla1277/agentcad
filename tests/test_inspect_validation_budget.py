"""M69 Slice 4 contracts for observable, bounded CAD inspection."""

import json
import subprocess
import sys
import time

import cadquery as cq
from cadquery import exporters

from agentcad import file_detect
from agentcad.cli import cli


def _make_step(directory, name="input.step"):
    path = directory / name
    exporters.export(cq.Workplane("XY").box(10, 10, 10), str(path))
    return path


def test_small_inspect_reports_successful_validation_phases(
    runner, isolated_dir
):
    step = _make_step(isolated_dir)

    result = runner.invoke(cli, ["inspect", str(step), "--no-daemon"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["validation_mode"] == "deep"
    assert payload["validation_budget_s"] > 0
    phases = payload["validation_phases"]
    for name in (
        "native_load",
        "structural_validation",
        "topology_extraction",
    ):
        assert phases[name]["status"] == "success"
        assert phases[name]["duration_ms"] >= 0
    assert phases["feature_extraction"]["status"] == "skipped"
    assert payload["solid_count"] == 1
    assert payload["face_count"] == 6


def test_fast_edge_ancestry_still_counts_edges_without_faces(
    runner, isolated_dir
):
    solid = cq.Workplane("XY").box(10, 10, 10).val()
    isolated_edge = cq.Edge.makeLine(
        cq.Vector(20, 0, 0),
        cq.Vector(30, 0, 0),
    )
    compound = cq.Compound.makeCompound([solid, isolated_edge])
    step = isolated_dir / "solid-with-isolated-edge.step"
    exporters.export(compound, str(step))

    result = runner.invoke(cli, ["inspect", str(step), "--no-daemon"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["edge_count"] == 13
    assert payload["free_edge_count"] == 1


def test_validate_only_uses_fast_structural_path(runner, isolated_dir):
    step = _make_step(isolated_dir)

    result = runner.invoke(
        cli,
        ["inspect", str(step), "--validate-only", "--no-daemon"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["validation_mode"] == "structural_only"
    assert payload["is_valid"] is True
    assert payload["validation_phases"]["native_load"]["status"] == "success"
    assert (
        payload["validation_phases"]["structural_validation"]["status"]
        == "success"
    )
    assert payload["validation_phases"]["topology_extraction"] == {
        "status": "skipped",
        "message": "Skipped by --validate-only.",
    }
    assert "face_count" not in payload
    assert any("agentcad inspect" in item for item in payload["next_actions"])


def test_native_load_timeout_is_retryable_not_malformed(
    runner, isolated_dir, monkeypatch
):
    step = _make_step(isolated_dir)

    def _slow_load(*_args, **_kwargs):
        time.sleep(1)

    monkeypatch.setattr("agentcad.step_io.load_cad_shape", _slow_load)

    result = runner.invoke(cli, [
        "inspect",
        str(step),
        "--validation-timeout",
        "0.02",
        "--no-daemon",
    ])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_timeout"
    assert payload["error_kind"] == "validation_timeout"
    assert payload["file"] == str(step)
    assert payload["timed_out_phase"] == "native_load"
    assert payload["retryable"] is True
    assert payload["elapsed_s"] > 0
    assert payload["validation_budget_s"] == 0.02
    assert payload["validation_phases"]["native_load"]["status"] == "timeout"
    assert payload["validation_phases"]["native_load"]["duration_ms"] > 0
    assert payload["format_detected"] == "step"
    assert len(payload["next_actions"]) == 2
    assert "--validate-only" in payload["next_actions"][0]
    assert "--validation-timeout" in payload["next_actions"][1]


def test_real_subprocess_timeout_keeps_stream_contract(isolated_dir):
    step = _make_step(isolated_dir)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentcad",
            "inspect",
            str(step),
            "--validation-timeout",
            "0.0001",
            "--no-daemon",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_timeout"
    assert payload["timed_out_phase"] in {
        "native_load",
        "structural_validation",
    }
    assert "StepFile" not in result.stderr
    assert "\x1b" not in result.stderr


def test_large_timeout_has_phase_progress_and_json(
    runner, isolated_dir, monkeypatch
):
    step = _make_step(isolated_dir)
    detected = file_detect.detect_file_type(step)
    detected["size_bytes"] = 20_000_000
    monkeypatch.setattr(
        file_detect,
        "detect_file_type",
        lambda _path: detected,
    )

    def _slow_load(*_args, **_kwargs):
        time.sleep(1)

    monkeypatch.setattr("agentcad.step_io.load_cad_shape", _slow_load)

    result = runner.invoke(cli, [
        "inspect",
        str(step),
        "--validation-timeout",
        "0.02",
        "--no-daemon",
    ])

    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_timeout"
    assert "native load" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_validation_timeout_can_come_from_environment(
    runner, isolated_dir, monkeypatch
):
    step = _make_step(isolated_dir)
    monkeypatch.setenv("AGENTCAD_INSPECT_TIMEOUT_S", "0.02")

    def _slow_load(*_args, **_kwargs):
        time.sleep(1)

    monkeypatch.setattr("agentcad.step_io.load_cad_shape", _slow_load)

    result = runner.invoke(
        cli, ["inspect", str(step), "--no-daemon"]
    )

    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_timeout"
    assert payload["validation_budget_s"] == 0.02


def test_zero_validation_timeout_disables_budget(runner, isolated_dir):
    step = _make_step(isolated_dir)

    result = runner.invoke(cli, [
        "inspect",
        str(step),
        "--validation-timeout",
        "0",
        "--no-daemon",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["validation_budget_s"] == 0


def test_daemon_request_preserves_validation_controls(
    runner, isolated_dir, monkeypatch
):
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    requests = []

    def _send(request, **_kwargs):
        requests.append(request)
        return {
            "type": "result",
            "exit_code": 0,
            "output": json.dumps({
                "command": "inspect",
                "status": "success",
                "validation_mode": "structural_only",
            }),
        }

    monkeypatch.setattr("agentcad.daemon.send_request", _send)

    result = runner.invoke(cli, [
        "inspect",
        "input.step",
        "--validate-only",
        "--validation-timeout",
        "12",
    ])

    assert result.exit_code == 0
    assert requests[0]["argv"] == [
        "inspect",
        "input.step",
        "--validate-only",
        "--validation-timeout",
        "12.0",
    ]


def test_help_and_docs_explain_validation_controls(runner):
    help_result = runner.invoke(cli, ["inspect", "--help"])
    assert help_result.exit_code == 0
    assert "--validate-only" in help_result.output
    assert "--validation-timeout" in help_result.output
    assert "AGENTCAD_INSPECT_TIMEOUT_S" in help_result.output

    docs_result = runner.invoke(cli, ["docs", "inspect"])
    content = json.loads(docs_result.stdout)["content"]
    assert "validation_timeout" in content
    assert "timed_out_phase" in content
    assert "native_load" in content
    assert "structural_validation" in content


def test_validate_only_rejects_deep_feature_flags(runner, isolated_dir):
    result = runner.invoke(
        cli,
        ["inspect", "input.step", "--validate-only", "--ids"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "usage_error"
    assert "cannot be combined" in payload["message"]
