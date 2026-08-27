"""M69 Slice 3 recovery contracts for failed edits and malformed CAD."""

import json

import pytest

from agentcad import file_detect
from agentcad.cli import cli


def _init(runner):
    result = runner.invoke(cli, ["init", "--name", "recovery"])
    assert result.exit_code == 0, result.output


def _assert_no_artifact_recovery(payload, *, script, label):
    assert payload["label"] == label
    assert payload["artifact_created"] is False
    assert payload["outputs"]["step"] is None
    assert "No STEP was created" in payload["message"]
    assert payload["next_actions"] == [
        f"agentcad run {script} --label {label}"
    ]


def test_static_validation_points_to_fix_and_rerun(
    runner, isolated_dir
):
    _init(runner)
    (isolated_dir / "edit.py").write_text("this is not valid python(")

    result = runner.invoke(
        cli,
        ["run", "edit.py", "--label", "first-edit", "--no-daemon"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_error"
    _assert_no_artifact_recovery(
        payload, script="edit.py", label="first-edit"
    )


def test_script_failure_points_to_fix_and_rerun(runner, isolated_dir):
    _init(runner)
    (isolated_dir / "edit.py").write_text(
        'raise RuntimeError("boom")\nshow_object(Box(1, 1, 1))\n'
    )

    result = runner.invoke(
        cli,
        ["run", "edit.py", "--label", "first-edit", "--no-daemon"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert "boom" in payload["error"]
    _assert_no_artifact_recovery(
        payload, script="edit.py", label="first-edit"
    )


@pytest.mark.parametrize("guess", ["thicken", "modify_step"])
def test_guessed_edit_command_routes_to_script_workflow(
    runner, isolated_dir, guess
):
    result = runner.invoke(cli, [guess])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "unknown_command"
    assert "not a top-level AgentCAD command" in payload["message"]
    assert "Python script" in payload["message"]
    assert payload["next_actions"] == ["agentcad docs editing"]


def test_guessed_shipped_helper_says_it_belongs_inside_edit_py(
    runner, isolated_dir
):
    result = runner.invoke(cli, ["fillet_edges"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "unknown_command"
    assert "script helper" in payload["message"]
    assert "inside edit.py" in payload["message"]
    assert payload["next_actions"] == ["agentcad docs editing"]


def test_unknown_command_uses_existing_edit_script_as_recovery(
    runner, isolated_dir
):
    (isolated_dir / "edit.py").write_text("show_object(Box(1, 1, 1))\n")

    result = runner.invoke(cli, ["fillet_edges"])

    payload = json.loads(result.stdout)
    assert payload["next_actions"] == [
        "agentcad init --name recovered && "
        "agentcad run edit.py --label recovered-edit"
    ]


def test_mislabeled_step_rejects_text_repair_and_has_one_recovery(
    runner, isolated_dir
):
    path = isolated_dir / "broken.step"
    path.write_text("<html><body>not CAD</body></html>")

    result = runner.invoke(cli, ["inspect", str(path), "--no-daemon"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "malformed"
    assert payload["error_kind"] == "malformed_cad"
    assert "Do not" in payload["message"]
    assert "STEP" in payload["message"]
    assert "text" in payload["message"]
    assert "suggestion" not in payload
    assert payload["next_actions"] == ["agentcad docs quickstart"]


def test_parser_failure_removes_native_ansi_and_duplicate_prose(
    runner, isolated_dir, monkeypatch
):
    path = isolated_dir / "truncated.step"
    path.write_text("ISO-10303-21;\nENDSEC;\n")
    monkeypatch.setattr(
        file_detect,
        "detect_file_type",
        lambda _path: {
            "category": file_detect.TIER0_BREP,
            "format": "step",
            "extension": "step",
            "size_bytes": path.stat().st_size,
        },
    )

    def _fail(*_args, **_kwargs):
        raise ValueError("\x1b[31mSTEP parse failed\x1b[0m: incomplete")

    monkeypatch.setattr(
        "agentcad.commands.inspect_cmd._topology_report", _fail
    )

    result = runner.invoke(cli, ["inspect", str(path), "--no-daemon"])

    assert result.exit_code == 1
    assert "\x1b" not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "malformed_cad"
    assert "suggestion" not in payload
    assert payload["message"].count("incomplete") <= 1
    assert len(payload["next_actions"]) == 1


def test_malformed_step_can_initialize_and_run_existing_edit_script(
    runner, isolated_dir
):
    (isolated_dir / "edit.py").write_text("show_object(Box(1, 1, 1))\n")
    path = isolated_dir / "broken.step"
    path.write_text("<html>not CAD</html>")

    result = runner.invoke(cli, ["inspect", str(path), "--no-daemon"])

    payload = json.loads(result.stdout)
    assert payload["next_actions"] == [
        "agentcad init --name recovered && "
        "agentcad run edit.py --label recovered"
    ]


def test_agent_docs_explain_closed_file_recovery_path(runner):
    result = runner.invoke(cli, ["docs", "schema"])

    assert result.exit_code == 0
    content = json.loads(result.stdout)["content"]
    assert "error_kind=malformed_cad" in content
    assert "Never repair STEP by writing" in content
    assert "fix-and-rerun command" in content
