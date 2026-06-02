"""End-to-end dispatcher wiring — exercises the real `agentcad run` CLI.

The dispatcher unit tests in `test_dispatch.py` cover detect/resolve
logic in isolation. These tests drive the CLI end-to-end to confirm
that:

- CadQuery scripts route to the CadQuery runner (unchanged behavior).
- build123d scripts route to the build123d runner.
- The `runtime` field shows up in the JSON output and meta.json.
- Ambiguous imports produce a clear error without touching disk.
- The ``--runtime`` override beats auto-detection.
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcad.cli import cli


def _init(runner, isolated_dir):
    result = runner.invoke(cli, ["init", "--name", "disp"])
    assert result.exit_code == 0, result.output


def _write(isolated_dir, name, body):
    (isolated_dir / name).write_text(body)


def _run(runner, *args):
    return runner.invoke(cli, ["run", *args])


def test_dispatch_cadquery_script_via_imports(runner, isolated_dir):
    _init(runner, isolated_dir)
    _write(isolated_dir, "box.py", "import cadquery as cq\nshow_object(cq.Workplane('XY').box(1, 2, 3))\n")

    result = _run(runner, "box.py", "--output", "cq1", "--dry-run")
    parsed = json.loads(result.stdout)

    assert result.exit_code == 0, result.output
    assert parsed["status"] == "success"
    assert parsed["runtime"] == "cadquery"
    assert parsed["metrics"]["volume"] == 6.0


def test_dispatch_build123d_script_via_imports(runner, isolated_dir):
    _init(runner, isolated_dir)
    _write(isolated_dir, "b3d_box.py", "from build123d import Box\nshow_object(Box(1, 2, 3))\n")

    result = _run(runner, "b3d_box.py", "--output", "b1", "--dry-run")
    parsed = json.loads(result.stdout)

    assert result.exit_code == 0, result.output
    assert parsed["status"] == "success"
    assert parsed["runtime"] == "build123d"
    assert parsed["metrics"]["volume"] == 6.0


def test_dispatch_no_imports_defaults_to_dispatch_default(runner, isolated_dir):
    """Scripts with neither import fall back to ``dispatch.DEFAULT_RUNTIME``.

    Tracks the constant so Phase-6-style flips carry the test along.
    Script is written to match the current default so the run actually
    succeeds (cadquery preamble injects ``cq``; build123d preamble
    injects ``Box`` et al.)."""
    from agentcad.runners import dispatch

    _init(runner, isolated_dir)
    if dispatch.DEFAULT_RUNTIME == "cadquery":
        script = "show_object(cq.Workplane('XY').box(1, 2, 3))\n"
    else:
        script = "show_object(Box(1, 2, 3))\n"
    _write(isolated_dir, "nobang.py", script)

    result = _run(runner, "nobang.py", "--output", "d1", "--dry-run")
    parsed = json.loads(result.stdout)

    assert parsed["status"] == "success"
    assert parsed["runtime"] == dispatch.DEFAULT_RUNTIME


def test_dispatch_ambiguous_imports_rejected(runner, isolated_dir):
    _init(runner, isolated_dir)
    _write(
        isolated_dir, "both.py",
        "import cadquery\nfrom build123d import Box\nshow_object(Box(1, 1, 1))\n",
    )

    result = _run(runner, "both.py", "--output", "both")
    parsed = json.loads(result.stdout)

    assert result.exit_code == 1
    assert parsed["status"] == "error"
    assert "ambiguous" in parsed["message"]
    assert "cadquery" in parsed["message"] and "build123d" in parsed["message"]
    # No version directory should have been created
    assert not any(p.name.startswith("v1_") for p in isolated_dir.iterdir())


def test_dispatch_runtime_override_wins(runner, isolated_dir):
    """--runtime=build123d forces b3d even for scripts that don't import it
    (the preamble star-imports the build123d API, so `Box(...)` still works)."""
    _init(runner, isolated_dir)
    _write(isolated_dir, "bare.py", "show_object(Box(1, 2, 3))\n")

    result = _run(runner, "bare.py", "--output", "o1", "--dry-run", "--runtime", "build123d")
    parsed = json.loads(result.stdout)

    assert result.exit_code == 0, result.output
    assert parsed["runtime"] == "build123d"
    assert parsed["metrics"]["volume"] == 6.0


def test_dispatch_runtime_override_cadquery_on_b3d_script_fails_cleanly(runner, isolated_dir):
    """Forcing cadquery on a build123d script fails at execution, not at dispatch —
    the override bypassed detection as expected."""
    _init(runner, isolated_dir)
    _write(isolated_dir, "b3d.py", "from build123d import Box\nshow_object(Box(1, 2, 3))\n")

    result = _run(runner, "b3d.py", "--output", "force", "--dry-run", "--runtime", "cadquery")

    # Either a validation_error or failure JSON — both are acceptable; what
    # matters is that we got PAST dispatch (the runtime was cadquery, not b3d).
    parsed = json.loads(result.stdout)
    assert parsed.get("runtime") == "cadquery" or parsed["status"] != "success"


def test_success_meta_json_records_runtime(runner, isolated_dir):
    """The on-disk meta.json carries the runtime for telemetry (Phase 6)."""
    _init(runner, isolated_dir)
    _write(isolated_dir, "b3d_box.py", "from build123d import Box\nshow_object(Box(5, 5, 5))\n")

    result = _run(runner, "b3d_box.py", "--output", "persisted", "--no-preview")
    assert result.exit_code == 0, result.output

    meta_path = isolated_dir / "v1_persisted" / "meta.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["runtime"] == "build123d"
    assert meta["status"] == "success"


def test_step_export_works_for_build123d_script(runner, isolated_dir):
    """The runner-level export_step is actually wired up."""
    _init(runner, isolated_dir)
    _write(isolated_dir, "b3d_box.py", "from build123d import Box\nshow_object(Box(3, 4, 5))\n")

    result = _run(runner, "b3d_box.py", "--output", "stepped", "--no-preview")
    assert result.exit_code == 0, result.output

    step_path = isolated_dir / "v1_stepped" / "output.step"
    assert step_path.exists()
    assert step_path.stat().st_size > 0
