"""M71 Slice 1b: the deliverable verdict on every command that reports geometry.

``is_valid`` means the same thing everywhere: the layered validator's
verdict. ``run`` and ``import`` gate on it by default; ``--validation-profile
kernel`` restores the kernel-only check for intentional surfaces.
"""

from __future__ import annotations

import json
import hashlib
import shutil
from pathlib import Path

from agentcad.cli import cli
from agentcad import validation


FIXTURES = Path(__file__).parent / "fixtures" / "validation"

BRACKET = """\
base = Box(40, 20, 6)
hole = Cylinder(3, 20)
bracket = base - hole
show_object(bracket, name="bracket")
"""

OPEN_SHELL = """\
box = Box(20, 20, 20)
faces = box.faces().sort_by(Axis.Z)[:-1]
show_object(Shell(faces), name="open_shell")
"""

QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]


def _init(runner):
    result = runner.invoke(cli, ["init", "--name", "m71", "--runtime", "build123d"])
    assert result.exit_code == 0, result.output


def _script(directory, content, name="script.py"):
    path = directory / name
    path.write_text(content)
    return path


def _run(runner, *args):
    result = runner.invoke(cli, ["run", *args])
    return result, json.loads(result.stdout)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_rejects_an_open_shell_by_default(runner, isolated_dir):
    _init(runner)
    _script(isolated_dir, OPEN_SHELL)

    result, payload = _run(runner, "script.py", "--label", "open", *QUIET)

    assert result.exit_code == 1, result.output
    assert payload["status"] == "invalid_geometry"
    assert payload["metrics"]["is_valid"] is False
    assert payload["validation"]["first_failure"] == "shell_closure"
    assert payload["validation_profile"] == "deliverable"
    assert "not watertight" in payload["message"]
    assert payload["validation"]["layers"]["shell_closure"]["free_edge_count"] == 4
    assert not list(isolated_dir.glob("v1_*/output.step"))
    assert payload["current_advanced"] is False


def test_run_kernel_profile_restores_the_old_behavior_and_records_it(runner, isolated_dir):
    _init(runner)
    _script(isolated_dir, OPEN_SHELL)

    result, payload = _run(
        runner, "script.py", "--label", "surface", "--validation-profile", "kernel", *QUIET
    )

    assert result.exit_code == 0, result.output
    assert payload["status"] == "success"
    assert payload["metrics"]["is_valid"] is True
    assert payload["validation_profile"] == "kernel"
    assert payload["validation"]["profile"] == "kernel"
    assert payload["validation"]["layers"]["shell_closure"]["status"] == "skipped"
    assert payload["validation"]["layers"]["mesh_manifold"]["status"] == "skipped"
    assert any("no closed solid" in warning for warning in payload["warnings"])
    assert not any("Occupied-volume measurement failed" in warning for warning in payload["warnings"])
    meta = json.loads((isolated_dir / "v1_surface" / "meta.json").read_text())
    assert meta["validation_profile"] == "kernel"
    assert meta["validation"]["is_valid"] is True
    readiness = payload["delivery_readiness"]
    assert readiness == meta["delivery_readiness"]
    assert readiness["ready"] is False
    assert readiness["profile"] == "kernel"
    assert readiness["blocking_reasons"] == [
        "shell_closure_not_checked", "mesh_manifold_not_checked",
    ]
    assert readiness["artifact_sha256"] == hashlib.sha256(
        (isolated_dir / payload["outputs"]["step"]).read_bytes()
    ).hexdigest()
    assert "not ready for delivery" in payload["message"]
    assert "not ready for delivery" in meta["message"]


def test_run_success_carries_the_layer_report_and_reliable_volume(runner, isolated_dir):
    _init(runner)
    _script(isolated_dir, BRACKET)

    result, payload = _run(runner, "script.py", "--label", "bracket", *QUIET)

    assert result.exit_code == 0, result.output
    assert payload["metrics"]["is_valid"] is True
    assert payload["metrics"]["reliable"] is True
    assert payload["validation"]["is_valid"] is True
    assert payload["validation"]["layers"]["mesh_manifold"]["status"] == "pass"
    assert payload["validation_profile"] == "deliverable"
    meta = json.loads((isolated_dir / "v1_bracket" / "meta.json").read_text())
    assert meta["validation"]["first_failure"] is None
    assert meta["validation_profile"] == "deliverable"
    assert payload["delivery_readiness"] == meta["delivery_readiness"]
    assert payload["delivery_readiness"]["ready"] is True
    assert payload["delivery_readiness"]["blocking_reasons"] == []


def test_run_dry_run_reports_the_verdict_without_a_version(runner, isolated_dir):
    _init(runner)
    _script(isolated_dir, OPEN_SHELL)

    result, payload = _run(runner, "script.py", "--label", "probe", "--dry-run", *QUIET)

    assert result.exit_code == 1
    assert payload["status"] == "invalid_geometry"
    assert payload["validation"]["first_failure"] == "shell_closure"
    assert payload["metrics"]["reliable"] is False
    assert not list(isolated_dir.glob("v1_*"))


def test_run_mesh_timeout_is_undetermined_not_invalid(runner, isolated_dir, monkeypatch):
    _init(runner)
    _script(isolated_dir, BRACKET)
    monkeypatch.setattr(validation, "MESH_INPROCESS_FACE_LIMIT", 0)
    monkeypatch.setenv(validation.MESH_TIMEOUT_ENV, "0.001")

    result, payload = _run(runner, "script.py", "--label", "slow", *QUIET)

    assert result.exit_code == 0, result.output
    assert payload["status"] == "success"
    assert payload["metrics"]["is_valid"] is None
    assert payload["validation"]["undetermined_layer"] == "mesh_manifold"
    assert any("could not finish" in w for w in payload["warnings"])
    assert (isolated_dir / "v1_slow" / "output.step").is_file()
    assert payload["delivery_readiness"]["ready"] is False
    assert "mesh_manifold_timeout" in payload["delivery_readiness"]["blocking_reasons"]


def test_nonmanifold_run_is_intermediate_even_when_kernel_accepts_it(runner, isolated_dir):
    _init(runner)
    shutil.copy(FIXTURES / "nonmanifold_edge_prism.step", isolated_dir / "input.step")
    _script(isolated_dir, 'show_object(load_step("input.step"))\n')

    result, full = _run(runner, "script.py", "--label", "full", *QUIET)
    assert result.exit_code == 1, result.output
    assert full["validation"]["first_failure"] == "mesh_manifold"
    result, reduced = _run(runner, "script.py", "--label", "kernel",
                           "--validation-profile", "kernel", *QUIET)
    assert result.exit_code == 0, result.output
    assert reduced["delivery_readiness"]["ready"] is False
    meta = json.loads((isolated_dir / reduced["outputs"]["step"]).with_name("meta.json").read_text())
    assert meta["delivery_readiness"] == reduced["delivery_readiness"]


def test_real_imported_manifold_run_keeps_readiness_through_mcp(runner, isolated_dir):
    from agentcad.mcp.server import _invoke

    _init(runner)
    source = Path(__file__).parent / "fixtures" / "real_world" / "pump_manifold.step"
    shutil.copy(source, isolated_dir / "input.step")
    _script(isolated_dir, 'show_object(load_step("input.step"))\n')
    payload = _invoke(["run", "script.py", "--label", "manifold", *QUIET], cwd=str(isolated_dir))
    assert payload["status"] == "success", payload
    step = isolated_dir / payload["outputs"]["step"]
    assert payload["delivery_readiness"] == json.loads(step.with_name("meta.json").read_text())["delivery_readiness"]
    assert payload["delivery_readiness"]["ready"] is True
    assert payload["delivery_readiness"]["artifact_sha256"] == hashlib.sha256(step.read_bytes()).hexdigest()


def test_mcp_run_cannot_hide_reduced_validation(runner, isolated_dir):
    from agentcad.mcp.server import _invoke

    _init(runner)
    _script(isolated_dir, BRACKET)
    payload = _invoke(["run", "script.py", "--label", "intermediate",
                       "--validation-profile", "kernel", *QUIET], cwd=str(isolated_dir))
    assert payload["status"] == "success", payload
    assert payload["delivery_readiness"]["ready"] is False
    assert "not ready for delivery" in payload["message"]


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def test_import_rejects_an_open_shell_and_kernel_profile_accepts_it(runner, isolated_dir):
    _init(runner)
    shutil.copy(FIXTURES / "open_shell.step", isolated_dir / "open_shell.step")

    result = runner.invoke(cli, ["import", "open_shell.step", "--full-response", "--no-view", "--no-diff", "--no-daemon"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1, result.output
    assert payload["status"] == "invalid_geometry"
    assert payload["validation"]["first_failure"] == "shell_closure"
    assert payload["validation_profile"] == "deliverable"

    result = runner.invoke(cli, [
        "import", "open_shell.step", "--label", "surface",
        "--validation-profile", "kernel", "--full-response", "--no-view", "--no-diff", "--no-daemon",
    ])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["status"] == "success"
    assert payload["metrics"]["is_valid"] is True
    assert payload["validation_profile"] == "kernel"


# ---------------------------------------------------------------------------
# measure / check-spec / recover / inspect
# ---------------------------------------------------------------------------


def test_measure_and_check_spec_report_the_verdict(runner, isolated_dir):
    shutil.copy(FIXTURES / "open_shell.step", isolated_dir / "open_shell.step")
    (isolated_dir / "spec.json").write_text(json.dumps({"features": [
        {"name": "bore", "type": "cylinder", "diameter_mm": 6, "count": 1},
    ]}))

    result = runner.invoke(cli, ["measure", "open_shell.step", "--no-daemon"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["metrics"]["is_valid"] is False
    assert payload["metrics"]["reliable"] is False
    assert payload["validation"]["first_failure"] == "shell_closure"

    result = runner.invoke(cli, ["check-spec", "open_shell.step", "spec.json", "--no-daemon"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["validity"]["is_valid"] is False
    assert payload["validity"]["first_failure"] == "shell_closure"


def test_recover_refuses_a_non_deliverable_step(runner, isolated_dir):
    _init(runner)
    _script(isolated_dir, BRACKET)
    result, _ = _run(runner, "script.py", "--label", "bracket", *QUIET)
    assert result.exit_code == 0, result.output
    orphan = isolated_dir / "v2_open"
    orphan.mkdir()
    shutil.copy(FIXTURES / "open_shell.step", orphan / "output.step")

    result = runner.invoke(cli, ["recover", "v2_open"])
    payload = json.loads(result.stdout)
    assert result.exit_code == 1, result.output
    assert payload["status"] == "invalid_geometry"
    assert payload["validation"]["first_failure"] == "shell_closure"
    assert payload["recovery_performed"] is False


def test_inspect_honors_the_kernel_profile(runner, isolated_dir):
    shutil.copy(FIXTURES / "open_shell.step", isolated_dir / "open_shell.step")

    result = runner.invoke(cli, [
        "inspect", "open_shell.step", "--validation-profile", "kernel", "--no-daemon",
    ])
    payload = json.loads(result.stdout)
    assert result.exit_code == 0, result.output
    assert payload["is_valid"] is True
    assert payload["validation"]["profile"] == "kernel"
    assert payload["validation"]["layers"]["shell_closure"]["status"] == "skipped"
