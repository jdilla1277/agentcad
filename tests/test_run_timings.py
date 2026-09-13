"""Phase timings separate what export_step_ms used to hide.

The M71 review found the umbrella export phase lumping source validation,
the STEP write, the reload, and the delivered validation, and the static
script check reported under the name ``validation_ms``. These tests pin the
split and the heartbeat elapsed suffix.
"""

import json
from pathlib import Path

from agentcad.cli import cli


def _run(runner, isolated_dir, source, label, extra=()):
    assert runner.invoke(cli, ["init", "--runtime", "build123d"]).exit_code == 0
    (isolated_dir / "part.py").write_text(source)
    return runner.invoke(cli, ["run", "part.py", "--label", label, *extra,
                               "--no-preview", "--no-view", "--no-diff", "--no-daemon"])


def test_export_phase_reports_its_parts(runner, isolated_dir):
    result = _run(runner, isolated_dir, "show_object(Box(10, 20, 5))", "split")
    assert result.exit_code == 0, result.output
    timings = json.loads(result.stdout)["timings"]
    for key in ("validation_ms", "script_exec_ms", "metrics_ms", "export_step_ms",
                "source_validation_ms", "export_write_ms", "reload_ms",
                "delivered_validation_ms", "total_ms"):
        assert key in timings, key
    parts = (timings["source_validation_ms"] + timings["export_write_ms"]
             + timings["reload_ms"] + timings["delivered_validation_ms"])
    assert parts <= timings["export_step_ms"] + 5, timings


def test_dry_run_reports_the_same_split(runner, isolated_dir):
    result = _run(runner, isolated_dir, "show_object(Box(10, 20, 5))", "dry", ("--dry-run",))
    assert result.exit_code == 0, result.output
    timings = json.loads(result.stdout)["timings"]
    assert {"source_validation_ms", "export_write_ms", "reload_ms",
            "delivered_validation_ms"} <= set(timings)


def test_heartbeats_carry_elapsed_seconds(runner, isolated_dir):
    result = _run(runner, isolated_dir, "show_object(Box(10, 20, 5))", "beat")
    assert result.exit_code == 0, result.output
    lines = [l for l in result.stderr.splitlines() if l.startswith("[agentcad] ")]
    assert any("running script" in l for l in lines)
    assert any("writing STEP" in l for l in lines)
    assert any("validating the written STEP" in l for l in lines)
    assert all(l.rstrip().endswith("s)") and "(+" in l for l in lines), lines


def test_delivered_validation_timings_survive_a_parse_failure(tmp_path):
    from agentcad.core_build import validate_delivered_step
    bad = tmp_path / "bad.step"
    bad.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n")
    timings = {}
    report = validate_delivered_step(bad, timings=timings)
    assert report["is_valid"] is False and report["first_failure"] in ("file_parse", "kernel_load")
    assert "reload_ms" in timings and "delivered_validation_ms" not in timings


def test_invalid_geometry_response_carries_timings(runner, isolated_dir):
    import shutil
    fixtures = Path(__file__).parent / "fixtures" / "validation"
    shutil.copyfile(fixtures / "open_shell.step", isolated_dir / "open_shell.step")
    result = _run(runner, isolated_dir, 'show_object(load_step_shape("open_shell.step"))', "bad")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "invalid_geometry"
    assert {"script_exec_ms", "metrics_ms", "export_step_ms", "delivered_validation_ms",
            "total_ms"} <= set(data["timings"])
    assert data["phase_timings"] == data["timings"]
    assert "export_step" in data["completed_phases"]
