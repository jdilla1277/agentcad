"""Retained attempts through real command paths, not a second build engine."""

# collect-on-default-profile

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentcad.cli import cli
from agentcad.change_store import ChangeStore

QUIET = ["--no-preview", "--no-diff", "--no-view", "--no-daemon"]
BRACKET = """from build123d import *
plate = Box(60, 30, 6) - Pos(-20, 0, 0) * Cylinder(3, 12) - Pos(20, 0, 0) * Cylinder(3, 12)
bracket = fillet(plate.edges().filter_by(Axis.Z), radius=1)
show_object(bracket, name="bracket")
"""
CQ_BRACKET = """import cadquery as cq
bracket = cq.Workplane("XY").box(60, 30, 6).faces(">Z").workplane().pushPoints([(-20, 0), (20, 0)]).hole(6)
bracket = bracket.edges("|Z").fillet(1)
show_object(bracket, name="bracket")
"""


def init(runner, runtime="build123d", build_dir=None):
    args = ["init", "--name", "retention", "--runtime", runtime]
    if build_dir:
        args += ["--build-dir", str(build_dir)]
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output


def run(runner, label="bracket", **kwargs):
    args = ["run", "script.py", "--label", label, *QUIET]
    if kwargs.get("build_dir"):
        args += ["--build-dir", str(kwargs["build_dir"])]
    result = runner.invoke(cli, args)
    return result, json.loads(result.stdout)


@pytest.mark.parametrize(
    "runtime,source",
    [
        ("build123d", BRACKET),
        pytest.param(
            "cadquery",
            CQ_BRACKET,
            marks=pytest.mark.skipif(
                importlib.util.find_spec("cadquery") is None,
                reason="CadQuery compatibility extra is not installed",
            ),
        ),
    ],
)
def test_success_returns_retained_inputs_without_changing_normal_current(
    runner, isolated_dir, runtime, source
):
    init(runner, runtime)
    (isolated_dir / "script.py").write_text(source)
    result, payload = run(runner)
    assert result.exit_code == 0, result.output
    ref = payload["change"]
    assert ref["status"] == "completed"
    assert Path(ref["source"]).read_text() == source
    assert Path(ref["geometry"]).read_bytes() == Path(payload["outputs"]["step"]).read_bytes()
    saved = ChangeStore(isolated_dir).read(ref["revision_id"])
    assert saved["receipt"]["validation_state"] == "valid"
    assert saved["outcome"]["response"]["version"] == payload["version"]
    manifest = json.loads((isolated_dir / "agentcad.json").read_text())
    assert manifest["current"] == "bracket"
    meta = json.loads((Path(payload["outputs"]["step"]).parent / "meta.json").read_text())
    assert meta["change"]["revision_id"] == ref["revision_id"]
    Path(payload["outputs"]["step"]).write_bytes(b"external replacement")
    assert Path(ref["geometry"]).read_bytes() != b"external replacement"
    assert (
        ChangeStore(isolated_dir).read(ref["revision_id"])["receipt"]["validation_state"] == "valid"
    )


def test_static_failure_and_execution_failure_are_retained_without_losing_last_success(
    runner, isolated_dir
):
    init(runner)
    script = isolated_dir / "script.py"
    script.write_text(BRACKET)
    result, good = run(runner, "accepted")
    assert result.exit_code == 0, result.output
    for label, source, expected in [
        ("syntax", "this is not valid Python(\n", "validation_error"),
        ("runtime", BRACKET + 'print("before failure")\nraise RuntimeError("boom")\n', "failed"),
    ]:
        script.write_text(source)
        result, failed = run(runner, label)
        assert result.exit_code != 0
        assert failed["status"] == expected
        assert Path(failed["change"]["source"]).read_text() == source
        saved = ChangeStore(isolated_dir).read(failed["change"]["revision_id"])
        assert saved["receipt"]["validation_state"] == "invalid"
        assert saved["outcome"]["response"]["status"] == expected
        assert failed["outputs"]["step"] is None
        assert json.loads((isolated_dir / "agentcad.json").read_text())["current"] == "accepted"
    assert len(ChangeStore(isolated_dir).list_attempts()) == 3
    script.write_text(BRACKET.replace("60, 30, 6", "70, 30, 6"))
    result, repaired = run(runner, "repaired")
    assert result.exit_code == 0, result.output
    assert repaired["change"]["parent_revision_id"] == failed["change"]["revision_id"]
    assert Path(good["change"]["source"]).read_text() == BRACKET
    # A fresh interpreter can reopen the failed attempt after command exit.
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            "import json; from agentcad.change_store import ChangeStore; "
            f"print(json.dumps(ChangeStore({str(isolated_dir)!r}).read({failed['change']['revision_id']!r})['outcome']['response']['status']))",
        ],
        text=True,
    )
    assert json.loads(output) == "failed"


def test_invalid_step_is_retained_for_repair_but_not_exposed_as_accepted_output(
    runner, isolated_dir
):
    init(runner)
    (isolated_dir / "script.py").write_text(
        "box = Box(20, 20, 20)\nshow_object(Shell(box.faces().sort_by(Axis.Z)[:-1]))\n"
    )
    result, payload = run(runner, "invalid")
    assert result.exit_code == 1, result.output
    assert payload["status"] == "invalid_geometry"
    assert payload["outputs"]["step"] is None
    assert not list(isolated_dir.glob("v1_*/output.step"))
    assert Path(payload["change"]["geometry"]).is_file()
    saved = ChangeStore(isolated_dir).read(payload["change"]["revision_id"])
    assert saved["receipt"]["validation_state"] == "invalid"
    assert saved["outcome"]["response"]["message"] == payload["message"]
    assert saved["outcome"]["response"]["notes"] == payload["notes"]
    assert json.loads((isolated_dir / "agentcad.json").read_text()).get("current") is None


def test_executed_source_is_retained_even_if_script_rewrites_itself(runner, isolated_dir):
    init(runner)
    source = (
        BRACKET
        + 'from pathlib import Path\nPath("script.py").write_text("# overwritten during execution")\n'
    )
    (isolated_dir / "script.py").write_text(source)
    result, payload = run(runner)
    assert result.exit_code == 0, result.output
    assert Path(payload["change"]["source"]).read_text() == source
    assert Path(payload["outputs"]["script"]).read_text() == source
    assert (isolated_dir / "script.py").read_text() != source


def test_dry_run_and_uninitialized_run_do_not_create_change_history(runner, isolated_dir):
    (isolated_dir / "script.py").write_text(BRACKET)
    result, _ = run(runner)
    assert result.exit_code == 1
    assert not (isolated_dir / ".agentcad" / "changes").exists()
    init(runner)
    result = runner.invoke(cli, ["run", "script.py", "--dry-run", *QUIET])
    assert result.exit_code == 0, result.output
    assert "change" not in json.loads(result.stdout)
    assert not (isolated_dir / ".agentcad" / "changes").exists()


def test_configured_build_root_owns_the_retained_failure(runner, isolated_dir):
    build = isolated_dir / "artifacts"
    init(runner, build_dir=build)
    (isolated_dir / "script.py").write_text("bad syntax(\n")
    result, payload = run(runner, build_dir=build)
    assert result.exit_code == 1
    assert Path(payload["change"]["source"]).is_relative_to(build)
    assert not (isolated_dir / ".agentcad" / "changes").exists()


def test_import_retains_malformed_input_without_consuming_a_legacy_version(runner, isolated_dir):
    init(runner)
    source = b"ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nnot CAD\nENDSEC;\nEND-ISO-10303-21;\n"
    path = isolated_dir / "broken.step"
    path.write_bytes(source)
    result = runner.invoke(cli, ["import", str(path), "--no-view", "--no-diff", "--no-daemon"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "malformed"
    assert Path(payload["change"]["source"]).read_bytes() == source
    assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []
    assert (
        ChangeStore(isolated_dir).read(payload["change"]["revision_id"])["outcome"]["response"][
            "status"
        ]
        == "malformed"
    )


def test_import_uses_pinned_source_even_if_original_is_replaced(runner, isolated_dir, monkeypatch):
    init(runner)
    (isolated_dir / "script.py").write_text(BRACKET)
    result, built = run(runner)
    assert result.exit_code == 0, result.output
    original = isolated_dir / "lab.step"
    original.write_bytes(Path(built["outputs"]["step"]).read_bytes())
    expected = original.read_bytes()
    from agentcad import step_assembly

    load = step_assembly.read_step_assembly

    def replacing_source(path):
        assert Path(path) != original
        result = load(path)
        original.write_bytes(b"external replacement")
        return result

    monkeypatch.setattr(step_assembly, "read_step_assembly", replacing_source)
    result = runner.invoke(
        cli,
        ["import", str(original), "--no-view", "--no-diff", "--no-inspection-image", "--no-daemon"],
    )
    assert result.exit_code == 0, result.output
    imported = json.loads(result.stdout)
    assert imported["original_filename"] == "lab.step"
    assert imported["sha256"] == hashlib.sha256(expected).hexdigest()
    assert Path(imported["change"]["source"]).read_bytes() == expected
    assert Path(imported["change"]["geometry"]).is_file()


def test_local_mcp_returns_the_same_durable_attempt(runner, isolated_dir):
    from agentcad.mcp import server

    init(runner)
    (isolated_dir / "script.py").write_text(BRACKET)
    payload = server.run(
        "script.py",
        output="mcp-bracket",
        cwd=str(isolated_dir),
        preview=False,
        diff=False,
        view=False,
    )
    assert payload["status"] == "success", payload
    saved = ChangeStore(isolated_dir).read(payload["change"]["revision_id"])
    assert saved["outcome"]["response"]["status"] == "success"
    assert Path(payload["change"]["source"]).read_text() == BRACKET
    assert len(ChangeStore(isolated_dir).list_attempts()) == 1


def test_core_retention_failure_does_not_accept_a_new_result(runner, isolated_dir, monkeypatch):
    from agentcad.change_store import Attempt

    init(runner)
    (isolated_dir / "script.py").write_text(BRACKET)
    result, good = run(runner, "accepted")
    assert result.exit_code == 0, result.output

    def fail_prepare(*args, **kwargs):
        raise OSError("core retention disk failure")

    monkeypatch.setattr(Attempt, "prepare", fail_prepare)
    result, payload = run(runner, "unaccepted")
    assert result.exit_code == 1, result.output
    assert payload["reason"] == "change_retention_failed"
    assert payload["change"]["status"] == "incomplete"
    assert Path(payload["change"]["request"]).is_file()
    assert json.loads((isolated_dir / "agentcad.json").read_text())["current"] == "accepted"
    assert Path(good["outputs"]["step"]).is_file()
    assert not list(isolated_dir.glob("v2_*"))


def test_final_diagnostics_failure_is_explicit_after_acceptance(runner, isolated_dir, monkeypatch):
    from agentcad.change_store import Attempt

    init(runner)
    (isolated_dir / "script.py").write_text(BRACKET)

    def fail_finish(*args, **kwargs):
        raise OSError("diagnostics disk failure")

    monkeypatch.setattr(Attempt, "finish", fail_finish)
    result, payload = run(runner)
    assert result.exit_code == 0, result.output
    assert payload["status"] == "success"
    assert payload["change"]["status"] == "incomplete"
    assert "diagnostics disk failure" in payload["change"]["message"]
    assert payload["warnings"]
    assert json.loads((isolated_dir / "agentcad.json").read_text())["current"] == "bracket"
    saved = ChangeStore(isolated_dir).read(payload["change"]["revision_id"])
    assert saved["candidate"] is not None
    assert saved["outcome"] is None


def test_partial_step_survives_an_export_exception(runner, isolated_dir, monkeypatch):
    from agentcad.runners import build123d as build123d_runner

    init(runner)
    (isolated_dir / "script.py").write_text(BRACKET)

    def partial_export(shape, path):
        Path(path).write_bytes(b"partial STEP bytes")
        raise RuntimeError("export interrupted")

    monkeypatch.setattr(build123d_runner, "export_step", partial_export)
    result, payload = run(runner)
    assert result.exit_code == 1, result.output
    assert "export interrupted" in payload["message"]
    assert Path(payload["change"]["geometry"]).read_bytes() == b"partial STEP bytes"
    saved = ChangeStore(isolated_dir).read(payload["change"]["revision_id"])
    assert saved["receipt"]["promotion_ready"] is False
    assert json.loads((isolated_dir / "agentcad.json").read_text()).get("current") is None


def test_import_internal_exception_keeps_source_and_structured_diagnostics(
    runner, isolated_dir, real_world_step, monkeypatch
):
    init(runner)

    def fail_metrics(*args, **kwargs):
        raise RuntimeError("metrics interrupted")

    monkeypatch.setattr("agentcad.metrics.compute_metrics", fail_metrics)
    result = runner.invoke(
        cli, ["import", str(real_world_step), "--no-view", "--no-diff", "--no-daemon"]
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert "metrics interrupted" in payload["message"]
    assert Path(payload["change"]["source"]).read_bytes() == real_world_step.read_bytes()
    saved = ChangeStore(isolated_dir).read(payload["change"]["revision_id"])
    assert saved["outcome"]["response"]["status"] == "error"
    assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []


def test_daemon_forwarding_preserves_worker_reference_without_duplicate_attempt(
    runner, isolated_dir, monkeypatch
):
    init(runner)
    (isolated_dir / "script.py").write_text(BRACKET)
    result, worker = run(runner)
    assert result.exit_code == 0, result.output
    monkeypatch.delenv("AGENTCAD_DAEMON", raising=False)
    monkeypatch.setattr(
        "agentcad.daemon.send_request",
        lambda *a, **kw: {
            "type": "result",
            "exit_code": 0,
            "output": json.dumps(worker),
        },
    )
    result = runner.invoke(cli, ["run", "script.py", "--label", "forwarded"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["via"] == "daemon"
    assert payload["change"] == worker["change"]
    assert len(ChangeStore(isolated_dir).list_attempts()) == 1


def test_undecodable_script_still_retains_its_exact_bytes(runner, isolated_dir):
    init(runner)
    source = b"# invalid UTF-8\xff\n"
    (isolated_dir / "script.py").write_bytes(source)
    result, payload = run(runner)
    assert result.exit_code == 1
    assert payload["error_kind"] == "script_unreadable"
    assert Path(payload["change"]["source"]).read_bytes() == source


def test_import_core_retention_failure_cleans_unaccepted_version(
    runner, isolated_dir, real_world_step, monkeypatch
):
    from agentcad.change_store import Attempt

    init(runner)

    def fail_prepare(*args, **kwargs):
        raise OSError("core retention disk failure")

    monkeypatch.setattr(Attempt, "prepare", fail_prepare)
    result = runner.invoke(
        cli, ["import", str(real_world_step), "--no-view", "--no-diff", "--no-daemon"]
    )
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["reason"] == "change_retention_failed"
    assert payload["change"]["status"] == "incomplete"
    assert not list(isolated_dir.glob("v1_*"))
    assert json.loads((isolated_dir / "agentcad.json").read_text())["versions"] == []
