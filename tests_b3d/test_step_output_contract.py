"""The public STEP contract: capture geometry and read outputs.step."""

import json

import pytest

from agentcad.cli import cli
from agentcad.guide import guide_body
from agentcad.validate import validate_script


@pytest.mark.parametrize(("construction", "solids", "volume"), [
    ("result = Part(Compound(children=[Box(2, 3, 4)]).wrapped)", 1, 24),
    ("result = Compound(children=[Box(2, 3, 4), Pos(10, 0, 0) * Box(2, 3, 4)])", 2, 48),
    ("result = Box(2, 3, 4).wrapped", 1, 24),
])
def test_capture_writes_canonical_step(runner, b3d_project, construction, solids, volume):
    from build123d import import_step

    script = b3d_project / "model.py"
    script.write_text(construction + "\nshow_object(result)\n")
    result = runner.invoke(cli, [
        "run", str(script), "--label", "capture", "--no-daemon",
        "--no-preview", "--no-diff", "--no-view",
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["artifact_created"] is True
    assert payload["outputs"]["step"] == "v1_capture/output.step"
    step = b3d_project / payload["outputs"]["step"]
    assert list(b3d_project.rglob("*.step")) == [step]
    loaded = import_step(str(step))
    assert len(loaded.solids()) == solids
    assert loaded.volume == pytest.approx(volume)


@pytest.mark.parametrize("writer", [
    "save_step(result, 'manual.step')",
    "write(result, 'manual.step')",
    "export(result, 'manual.step')",
    "result.write('manual.step')",
    "result.export('manual.step')",
    "result.write_step('manual.step')",
    "result.write_to_step('manual.step')",
    "result.export_step('manual.step')",
    "export_step(result, path='manual.step')",
    "export_step(result, filename='manual.step')",
    "export_step(parts=result, file_path='manual.step')",
    "export_step(result)",
    "export_step(result, 'missing-directory/manual.step')",
    "from OCP.STEPControl import STEPControl_Writer\nSTEPControl_Writer().Write()",
    "import build123d.io",
    "from build123d.io import export_step",
    "from build123d.export import export_step",
    "from build123d import io",
])
def test_wrong_writer_returns_repair_and_no_step(runner, b3d_project, writer):
    script = b3d_project / "model.py"
    script.write_text("result = Box(2, 3, 4)\nshow_object(result)\n" + writer + "\n")
    result = runner.invoke(cli, [
        "run", str(script), "--label", "writer", "--no-daemon",
        "--no-preview", "--no-diff", "--no-view",
    ])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] in {"failed", "validation_error"}
    assert payload["artifact_created"] is False
    assert payload["outputs"]["step"] is None
    diagnostic = payload["checks"][0] if "checks" in payload else payload
    assert "show_object(result)" in diagnostic["suggestion"]
    assert "outputs.step" in diagnostic["suggestion"]
    assert diagnostic["more_at"] == "agentcad docs preamble"
    assert not list(b3d_project.rglob("*.step"))


def test_manual_writer_without_capture_gets_output_contract(runner, b3d_project):
    script = b3d_project / "model.py"
    script.write_text("result = Box(2, 3, 4)\nexport_step(result, 'manual.step')\n")
    result = runner.invoke(cli, ["run", str(script), "--label", "manual", "--no-daemon"])
    assert result.exit_code == 1, result.output
    payload = json.loads(result.stdout)
    check, = payload["checks"]
    assert check["check"] == "show_object_missing"
    assert check["repair_snippets"] == ["show_object(result)"]
    assert "should not export STEP themselves" in check["message"]
    assert "outputs.step" in check["message"]
    assert not list(b3d_project.rglob("*.step"))


@pytest.mark.parametrize("writer", [
    "export_step(result, 'manual.step')",
    "import build123d as b3d\nb3d.export_step(result, 'manual.step')",
    "from build123d import export_step as save\nsave(result, 'manual.step')",
    "result.exportStep('manual.step')",
    "from OCP.STEPControl import STEPControl_Writer as Writer\nwriter = Writer()",
])
@pytest.mark.parametrize("no_daemon", [False, True])
def test_manual_step_rejected_before_execution(runner, b3d_project, monkeypatch, writer, no_daemon):
    from agentcad.commands import run as run_mod
    from agentcad.runners import build123d

    script = b3d_project / "model.py"
    script.write_text("result = Box(2, 3, 4)\n" + writer + "\nshow_object(result)\n")

    def forbidden(*args, **kwargs):
        pytest.fail("Manual STEP validation must precede execution, imports, and daemon routing")

    monkeypatch.setattr(run_mod, "maybe_route_through_daemon", forbidden)
    monkeypatch.setattr(build123d, "execute", forbidden)
    monkeypatch.setattr("agentcad.validate._can_import", forbidden)
    response = runner.invoke(cli, ["run", str(script), "--label", "manual"] + (
        ["--no-daemon"] if no_daemon else []
    ))
    assert response.exit_code == 1, response.output
    payload = json.loads(response.stdout)
    assert payload["status"] == "validation_error"
    assert payload["artifact_created"] is False
    assert payload["outputs"]["step"] is None
    check, = payload["checks"]
    assert check["check"] == "manual_step_export"
    assert "outputs.step" in check["suggestion"]
    assert not list(b3d_project.rglob("*.step"))
    assert not list(b3d_project.glob("v*"))


@pytest.mark.parametrize("call", [
    "write(report)",
    "export(report, 'report.txt')",
    "report.write('report.txt')",
    "exporters.export(result, 'mesh.stl')",
    "export_stl(result, 'mesh.stl')",
])
def test_non_step_writers_pass_validation(call):
    assert validate_script(call + "\nshow_object(result)", check_imports=False) == []


@pytest.mark.parametrize("check_imports", [False, True])
def test_cadquery_manual_step_rejected_without_imports(monkeypatch, check_imports):
    def forbidden(*args, **kwargs):
        pytest.fail("Manual STEP validation must precede import resolution")

    monkeypatch.setattr("agentcad.validate._can_import", forbidden)
    checks = validate_script(
        "from cadquery import exporters\n"
        "result = cq.Workplane('XY').box(2, 3, 4)\n"
        "exporters.export(result, 'manual.step')\nshow_object(result)",
        check_imports=check_imports,
    )
    check, = checks
    assert check["check"] == "manual_step_export"


def test_guides_and_preamble_explain_step_ownership(runner):
    for runtime in ("build123d", "cadquery"):
        guide = guide_body(runtime)
        assert "Scripts should not export STEP themselves" in guide
        assert "outputs.step" in guide
        assert "dry run" in guide
        assert "Validation rejects recognizable manual STEP" in guide
    result = runner.invoke(cli, ["docs", "preamble"])
    assert result.exit_code == 0, result.output
    assert "Scripts should not export STEP themselves" in result.output
    assert "outputs.step" in result.output
