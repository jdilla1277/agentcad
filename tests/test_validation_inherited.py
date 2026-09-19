"""Inherited versus introduced invalidity.

CADGenBench fixture 202's input STEP is kernel-invalid as shipped, so any
edit of it fails the deliverable gate and an agent would try to repair
damage it did not cause. run now validates loaded inputs on failure and says
which case applies.
"""

import json
import shutil
from pathlib import Path

from agentcad.cli import cli
from agentcad.core_build import annotate_input_provenance
from agentcad.runners import build123d as runner

FIXTURES = Path(__file__).parent / "fixtures" / "validation"


def test_runner_records_loaded_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shutil.copyfile(FIXTURES / "closed_box.step", tmp_path / "box.step")
    result = runner.execute(
        'a = load_step("box.step")\nb = load_step_shape("box.step")\nshow_object(a)\n', None
    )
    assert result.success, result.exception
    assert result.loaded_files == ["box.step", "box.step"]


def test_runner_without_loads_has_no_loaded_files():
    result = runner.execute("show_object(Box(1, 1, 1))\n", None)
    assert result.success and result.loaded_files == []


def _run(runner_, isolated_dir, source, label):
    assert runner_.invoke(cli, ["init", "--runtime", "build123d"]).exit_code == 0
    (isolated_dir / "part.py").write_text(source)
    return runner_.invoke(cli, ["run", "part.py", "--label", label,
                                "--no-preview", "--no-view", "--no-diff", "--no-daemon"])


def test_pass_through_of_an_invalid_input_is_reported_as_inherited(runner, isolated_dir):
    shutil.copyfile(FIXTURES / "open_shell.step", isolated_dir / "open_shell.step")
    result = _run(runner, isolated_dir,
                  'show_object(load_step_shape("open_shell.step"))\n', "inherit")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["status"] == "invalid_geometry"
    v = data["validation"]
    assert v["first_failure"] == "shell_closure"
    assert v["inherited_from_input"] == {
        "path": "open_shell.step", "is_valid": False, "first_failure": "shell_closure"}
    assert "did not introduce" in v["message"] and "open_shell.step" in data["message"]
    assert "inspect open_shell.step" in data["suggestion"]
    # The generic tail and recovery must not send the agent back to the script.
    assert "Fix part.py" not in data["message"]
    assert "repair or replace it" in data["message"]
    assert data["next_actions"][0] == "agentcad inspect open_shell.step --ids"
    assert data["next_actions"][1].startswith("agentcad run part.py")
    assert [r["kind"] for r in v["repairs"]] == ["repair_or_replace_input"]
    assert v["repairs"][0]["applicability"] == "diagnosed"


def test_nested_script_checks_input_from_invocation_directory(runner, isolated_dir):
    shutil.copyfile(FIXTURES / "open_shell.step", isolated_dir / "vendor input.step")
    scripts = isolated_dir / "scripts"
    scripts.mkdir()
    assert runner.invoke(cli, ["init", "--runtime", "build123d"]).exit_code == 0
    (scripts / "part.py").write_text(
        'show_object(load_step_shape("vendor input.step"))\n'
    )

    result = runner.invoke(cli, [
        "run", "scripts/part.py", "--label", "nested", "--no-preview",
        "--no-view", "--no-diff", "--no-daemon",
    ])

    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    validation = data["validation"]
    assert validation["inherited_from_input"]["path"] == "vendor input.step"
    inspect_command = "agentcad inspect 'vendor input.step' --ids"
    assert data["next_actions"][0] == inspect_command
    assert f"`{inspect_command}`" in validation["suggestion"]
    assert f"`{inspect_command}`" in validation["repairs"][0]["how"]


def test_breaking_a_valid_input_is_reported_as_introduced(runner, isolated_dir):
    shutil.copyfile(FIXTURES / "closed_box.step", isolated_dir / "box.step")
    result = _run(runner, isolated_dir,
                  'base = load_step("box.step")\n'
                  'show_object(base.faces().sort_by(Axis.Z)[-1])\n', "introduced")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    v = data["validation"]
    assert v["inherited_from_input"] is False
    assert v["inputs_checked"] == [{"path": "box.step", "is_valid": True, "first_failure": None}]
    assert "this run introduced the failure" in data["message"]
    assert "Fix part.py" in data["message"]
    assert data["next_actions"] == ["agentcad run part.py --label introduced"]
    assert v["repairs"] and v["repairs"][0]["kind"] != "repair_or_replace_input"


def test_valid_result_does_not_validate_inputs(runner, isolated_dir):
    shutil.copyfile(FIXTURES / "closed_box.step", isolated_dir / "box.step")
    result = _run(runner, isolated_dir, 'show_object(load_step("box.step"))\n', "fine")
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "inherited_from_input" not in data["validation"]
    assert "input_validation_ms" not in data["timings"]


def test_annotate_ignores_missing_inputs_and_null_verdicts(tmp_path):
    validation = {"is_valid": None, "first_failure": None, "message": "m"}
    assert annotate_input_provenance(validation, ["nope.step"], cwd=tmp_path) == validation
    validation = {"is_valid": False, "first_failure": "brep_check", "message": "m"}
    out = annotate_input_provenance(dict(validation), ["missing.step"], cwd=tmp_path)
    assert "inherited_from_input" not in out and "inputs_checked" not in out
