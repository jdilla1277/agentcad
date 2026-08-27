"""M69 Slice 2 contracts for literal labels, artifacts, and usage errors."""

import json

from agentcad.cli import cli


SIMPLE_SCRIPT = """\
import cadquery as cq
show_object(cq.Workplane("XY").box(10, 10, 10))
"""


def _init(runner):
    result = runner.invoke(
        cli, ["init", "--name", "contracts", "--runtime", "cadquery"]
    )
    assert result.exit_code == 0, result.output


def _write_script(directory, content=SIMPLE_SCRIPT):
    path = directory / "script.py"
    path.write_text(content)
    return path


def _assert_no_step(payload, label):
    assert payload["label"] == label
    assert payload["artifact_created"] is False
    assert payload["outputs"]["step"] is None


def test_run_label_returns_one_literal_step_path(runner, isolated_dir):
    _init(runner)
    _write_script(isolated_dir)

    result = runner.invoke(cli, [
        "run", "script.py", "--label", "first-edit", "--no-preview",
        "--no-diff", "--no-view", "--no-daemon",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["label"] == "first-edit"
    assert payload["artifact_created"] is True
    assert payload["outputs"]["step"] == "v1_first-edit/output.step"
    assert (isolated_dir / payload["outputs"]["step"]).is_file()
    assert "deprecation" not in payload


def test_run_output_alias_preserves_behavior_with_deprecation(
    runner, isolated_dir
):
    _init(runner)
    _write_script(isolated_dir)

    result = runner.invoke(cli, [
        "run", "script.py", "--output", "compat", "--no-preview",
        "--no-diff", "--no-view", "--no-daemon",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["label"] == "compat"
    assert payload["artifact_created"] is True
    assert payload["outputs"]["step"] == "v1_compat/output.step"
    assert "--label" in payload["deprecation"]
    assert "outputs.step" in payload["deprecation"]


def test_run_static_validation_has_explicit_null_step(runner, isolated_dir):
    _init(runner)
    _write_script(isolated_dir, "this is not valid python(")

    result = runner.invoke(
        cli, ["run", "script.py", "--label", "broken", "--no-daemon"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_error"
    _assert_no_step(payload, "broken")


def test_run_execution_failure_has_explicit_null_step(runner, isolated_dir):
    _init(runner)
    _write_script(isolated_dir, """\
import cadquery as cq
raise RuntimeError("boom")
show_object(cq.Workplane("XY").box(1, 1, 1))
""")

    result = runner.invoke(
        cli, ["run", "script.py", "--label", "failed", "--no-daemon"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    _assert_no_step(payload, "failed")


def test_run_dry_run_has_explicit_null_step(runner, isolated_dir):
    _init(runner)
    _write_script(isolated_dir)

    result = runner.invoke(cli, [
        "run", "script.py", "--label", "probe", "--dry-run", "--no-daemon",
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    _assert_no_step(payload, "probe")


def test_run_input_error_has_explicit_null_step(runner, isolated_dir):
    _init(runner)

    result = runner.invoke(
        cli, ["run", "missing.py", "--label", "missing", "--no-daemon"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    _assert_no_step(payload, "missing")


def _assert_usage_error(payload, *, kind, command):
    assert payload["status"] == "error"
    assert payload["error_kind"] == kind
    assert payload["command"] == command
    assert isinstance(payload["message"], str) and payload["message"]
    assert payload["usage"].startswith("Usage:")
    assert "invalid_option" in payload
    assert payload["next_actions"]
    assert all(" — " not in action for action in payload["next_actions"])


def test_unknown_option_is_json_on_stdout(runner, isolated_dir):
    result = runner.invoke(
        cli, ["run", "script.py", "--label", "v1", "--wat"]
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="unknown_option", command="run")
    assert payload["invalid_option"] == "--wat"
    assert payload["label"] == "v1"
    assert payload["artifact_created"] is False
    assert payload["outputs"]["step"] is None


def test_missing_option_value_is_json_on_stdout(runner, isolated_dir):
    result = runner.invoke(cli, ["run", "script.py", "--label"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="missing_parameter", command="run")
    assert payload["invalid_option"] == "--label"
    assert payload["usage"] == "Usage: agentcad run [OPTIONS] SCRIPT"


def test_missing_label_is_json_on_stdout(runner, isolated_dir):
    result = runner.invoke(cli, ["run", "script.py"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="missing_parameter", command="run")
    _assert_no_step(payload, None)


def test_missing_script_argument_is_json_on_stdout(runner, isolated_dir):
    result = runner.invoke(cli, ["run", "--label", "v1"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="missing_parameter", command="run")
    assert payload["label"] == "v1"
    assert payload["invalid_option"] is None


def test_invalid_option_value_is_json_on_stdout(runner, isolated_dir):
    result = runner.invoke(
        cli, ["run", "script.py", "--label", "v1", "--runtime", "nope"]
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="invalid_value", command="run")
    assert payload["invalid_option"] == "--runtime"
    assert payload["label"] == "v1"


def test_label_and_output_together_return_usage_json(runner, isolated_dir):
    result = runner.invoke(
        cli,
        ["run", "script.py", "--label", "new", "--output", "old"],
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="usage_error", command="run")
    assert "not both" in payload["message"]
    assert payload["label"] == "new"
    assert "--label" in payload["deprecation"]


def test_unknown_top_level_command_is_json_on_stdout(runner, isolated_dir):
    result = runner.invoke(cli, ["fillet_edges"])

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="unknown_command", command="fillet_edges")
    assert payload["invalid_option"] is None
    assert payload["next_actions"] == ["agentcad docs editing"]


def test_run_help_prefers_label_and_marks_output_compatibility_alias(runner):
    result = runner.invoke(cli, ["run", "--help"])

    assert result.exit_code == 0
    assert "--label" in result.output
    assert "--output" in result.output
    assert "compatibility alias" in result.output.lower()


def test_top_level_help_uses_label_and_explains_literal_step_path(runner):
    result = runner.invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "agentcad run SCRIPT --label LABEL" in result.output
    assert "--output LABEL" in result.output
    assert "outputs.step" in result.output


def test_commands_docs_explain_output_alias(runner):
    result = runner.invoke(cli, ["docs", "commands"])

    assert result.exit_code == 0
    content = json.loads(result.stdout)["content"]
    assert "Use --label to name the version" in content
    assert "--output is a deprecated alias" in content
    assert "outputs.step" in content
