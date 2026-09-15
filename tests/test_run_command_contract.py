"""M69 Slice 2 contracts for literal labels, artifacts, and usage errors."""

import json
import shlex
from pathlib import Path

import pytest

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


# Public issue #193: tool bridges forward argv verbatim, so every `run`
# usage error must hand the agent a copyable corrected command built from the
# pieces that survived parsing, plus the canonical form.
_CANONICAL = "agentcad run SCRIPT --label LABEL"


def _assert_run_recovery(payload, corrected):
    assert payload["canonical_command"] == _CANONICAL
    assert payload["next_actions"][0] == corrected
    assert "agentcad run --help" in payload["next_actions"]
    assert payload["message"].endswith(f"Canonical form: {_CANONICAL}.")


@pytest.mark.parametrize(
    "argv, corrected",
    [
        (["run"], "agentcad run SCRIPT --label LABEL"),
        (["run", "--label", "test"], "agentcad run SCRIPT --label test"),
        (["run", "build.py"], "agentcad run build.py --label LABEL"),
        (
            ["run", "build.py", "--label", "test", "./build.sh"],
            "agentcad run build.py --label test",
        ),
        (
            ["run", "--script", "build.py", "--label", "test"],
            "agentcad run build.py --label test",
        ),
        (
            ["run", "--script=build.py", "--label=test"],
            "agentcad run build.py --label test",
        ),
        (
            ["run", "--render", "iso", "build.py", "--label", "test", "--wat"],
            "agentcad run build.py --label test --render iso",
        ),
        (
            ["run", "build.py", "--label=test", "--runtime", "python",
             "--export", "stl", "--no-preview"],
            "agentcad run build.py --label test --export stl --no-preview",
        ),
        (
            ["run", "build.py", "--dry-run", "--wat"],
            "agentcad run build.py --dry-run",
        ),
        (
            ["run", "build.py", "--label", "new", "--output", "old"],
            "agentcad run build.py --label new",
        ),
        (
            ["run", "my model.py", "--label", "v 1", "--wat"],
            "agentcad run 'my model.py' --label 'v 1'",
        ),
        # Attached values may start with a dash (custom camera angle) and must
        # survive recovery in attached form.
        (
            ["run", "build.py", "--label", "v1", "--render=-45:30", "--wat"],
            "agentcad run build.py --label v1 --render=-45:30",
        ),
        (
            ["run", "build.py", "--label=-v1", "--render=front,-45:30", "--wat"],
            "agentcad run build.py --label=-v1 --render front,-45:30",
        ),
        (
            ["run", "--label", "v1", "--render=-45:30", "build.py", "--wat"],
            "agentcad run build.py --label v1 --render=-45:30",
        ),
        # A value incorrectly attached to a flag is rejected as a unit; its
        # value must not be reinterpreted as the script positional.
        (
            ["run", "--dry-run=true", "build.py"],
            "agentcad run build.py --label LABEL",
        ),
    ],
)
def test_run_usage_errors_lead_with_corrected_command(
    runner, isolated_dir, argv, corrected
):
    result = runner.invoke(cli, argv)

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["command"] == "run"
    assert payload["status"] == "error"
    _assert_run_recovery(payload, corrected)


def test_corrected_command_with_attached_dash_value_reparses(runner, isolated_dir):
    _init(runner)
    (isolated_dir / "model.py").write_text(SIMPLE_SCRIPT)
    first = runner.invoke(
        cli, ["run", "model.py", "--label", "v1", "--render=-45:30", "--wat", "--no-daemon"]
    )
    suggested = json.loads(first.stdout)["next_actions"][0]

    rerun = runner.invoke(
        cli, shlex.split(suggested)[1:] + ["--no-view", "--no-preview", "--no-diff"]
    )

    assert rerun.exit_code == 0, rerun.output
    outcome = json.loads(rerun.stdout)
    assert outcome["status"] == "success"
    assert outcome["renders"]


def test_run_invalid_runtime_explains_library_not_language(runner, isolated_dir):
    result = runner.invoke(
        cli, ["run", "build.py", "--label", "test", "--runtime", "python"]
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="invalid_value", command="run")
    assert "names the CAD library, not the language" in payload["message"]
    assert "'cadquery', 'build123d'" in payload["message"]
    _assert_run_recovery(payload, "agentcad run build.py --label test")


def test_run_script_as_option_is_explained(runner, isolated_dir):
    result = runner.invoke(cli, ["run", "--script", "build.py", "--label", "t"])

    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="unknown_option", command="run")
    assert payload["invalid_option"] == "--script"
    assert "positional argument after `run`, not an option" in payload["message"]


def test_run_extra_argument_message_is_punctuated(runner, isolated_dir):
    result = runner.invoke(
        cli, ["run", "build.py", "--label", "t", "./build.sh"]
    )

    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="usage_error", command="run")
    assert "(./build.sh). `run` takes exactly one script path." in payload["message"]


@pytest.mark.parametrize(
    "argv, corrected",
    [
        # An unknown option may or may not have consumed the next token, so a
        # later unambiguous positional wins over its possible value.
        (
            ["run", "--wat", "garbage", "build.py", "--label", "v1"],
            "agentcad run build.py --label v1",
        ),
        (
            ["run", "--lable", "v1", "build.py"],
            "agentcad run build.py --label LABEL",
        ),
        # Only an ambiguous candidate: fall back to the placeholder.
        (
            ["run", "--wat", "garbage", "--label", "v1"],
            "agentcad run SCRIPT --label v1",
        ),
    ],
)
def test_unknown_option_value_is_not_mistaken_for_the_script(
    runner, isolated_dir, argv, corrected
):
    payload = json.loads(runner.invoke(cli, argv).stdout)

    _assert_usage_error(payload, kind="unknown_option", command="run")
    assert payload["next_actions"][0] == corrected


def test_click_suggestion_is_not_double_punctuated(runner, isolated_dir):
    payload = json.loads(runner.invoke(cli, ["run", "--lable", "v1", "build.py"]).stdout)

    assert "Did you mean '--label'? --label names" in payload["message"]
    assert "?." not in payload["message"]


@pytest.mark.parametrize(
    "argv, corrected",
    [
        # Ambiguous token that exists on disk counts when nothing better follows.
        (["run", "--wat", "model.py", "--label", "v1"], "agentcad run model.py --label v1"),
        # ...but a later unambiguous positional replaces it.
        (
            ["run", "--wat", "config.py", "model.py", "--label", "v1"],
            "agentcad run model.py --label v1",
        ),
        (
            ["run", "--wat", "config.py", "--script", "model.py", "--label", "v1"],
            "agentcad run model.py --label v1",
        ),
    ],
)
def test_positional_after_unknown_option_yields_to_unambiguous_script(
    runner, isolated_dir, argv, corrected
):
    for name in ("model.py", "config.py"):
        (isolated_dir / name).write_text(SIMPLE_SCRIPT)

    payload = json.loads(runner.invoke(cli, argv).stdout)

    assert payload["next_actions"][0] == corrected


def test_missing_script_is_filled_from_the_only_script_in_cwd(runner, isolated_dir):
    (isolated_dir / "model.py").write_text(SIMPLE_SCRIPT)

    payload = json.loads(runner.invoke(cli, ["run", "--label", "v1"]).stdout)

    assert payload["next_actions"][0] == "agentcad run model.py --label v1"
    assert "Using the only Python script here: model.py." in payload["message"]


def test_missing_script_lists_candidates_when_ambiguous(runner, isolated_dir):
    for name in ("a.py", "b.py"):
        (isolated_dir / name).write_text(SIMPLE_SCRIPT)

    payload = json.loads(runner.invoke(cli, ["run", "--label", "v1"]).stdout)

    assert payload["next_actions"][0] == "agentcad run SCRIPT --label v1"
    assert "Replace SCRIPT with the path to your Python CAD script." in payload["message"]
    assert "Python scripts here: a.py, b.py." in payload["message"]


def test_missing_label_explains_what_a_label_is(runner, isolated_dir):
    payload = json.loads(runner.invoke(cli, ["run", "build.py"]).stdout)

    assert (
        "--label names this version; replace LABEL with any short name, for "
        "example v1." in payload["message"]
    )


def test_script_not_found_points_at_existing_scripts(runner, isolated_dir):
    _init(runner)
    (isolated_dir / "model.py").write_text(SIMPLE_SCRIPT)
    result = runner.invoke(
        cli, ["run", "SCRIPT", "--label", "v1", "--no-daemon", "--no-view"]
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "error"
    assert payload["error_kind"] == "script_not_found"
    assert "Script file 'SCRIPT' not found. Python scripts here: model.py." in payload["message"]
    assert payload["next_actions"] == [
        "agentcad run model.py --label v1 --no-view --no-daemon"
    ]
    _assert_no_step(payload, "v1")


def test_script_not_found_keeps_flags(runner, isolated_dir):
    _init(runner)
    for name in ("a.py", "b.py"):
        (isolated_dir / name).write_text(SIMPLE_SCRIPT)
    result = runner.invoke(
        cli, ["run", "nope.py", "--label", "t", "--render", "iso", "--no-daemon", "--no-view"]
    )

    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "script_not_found"
    assert payload["next_actions"] == [
        "agentcad run a.py --label t --render iso --no-view --no-daemon",
        "agentcad run b.py --label t --render iso --no-view --no-daemon",
    ]


def test_script_not_found_hint_keeps_build_dir_and_runs(runner, isolated_dir):
    runner.invoke(
        cli,
        ["init", "--name", "contracts", "--runtime", "cadquery",
         "--build-dir", "artifacts", "--no-agent-setup"],
    )
    (isolated_dir / "model.py").write_text(SIMPLE_SCRIPT)
    result = runner.invoke(
        cli,
        ["run", "missing.py", "--label", "v1", "--build-dir", "artifacts", "--no-daemon", "--no-view"],
    )

    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "script_not_found"
    suggested = payload["next_actions"][0]
    assert "--build-dir" in suggested
    assert not (isolated_dir / "agentcad.json").exists()

    rerun = runner.invoke(cli, shlex.split(suggested)[1:] + ["--no-preview", "--no-diff"])

    assert rerun.exit_code == 0, rerun.output
    outcome = json.loads(rerun.stdout)
    assert outcome["status"] == "success"
    assert Path(outcome["outputs"]["step"]).is_relative_to(isolated_dir / "artifacts")


def test_script_not_found_dry_run_needs_no_label(runner, isolated_dir):
    _init(runner)
    (isolated_dir / "model.py").write_text(SIMPLE_SCRIPT)
    result = runner.invoke(cli, ["run", "nope.py", "--dry-run", "--no-daemon"])

    payload = json.loads(result.stdout)
    assert payload["next_actions"] == ["agentcad run model.py --dry-run --no-daemon"]


def test_script_not_found_without_scripts_sends_to_quickstart(runner, isolated_dir):
    _init(runner)
    result = runner.invoke(
        cli, ["run", "missing.py", "--label", "v1", "--no-daemon", "--no-view"]
    )

    payload = json.loads(result.stdout)
    assert payload["error_kind"] == "script_not_found"
    assert "No Python scripts in this directory" in payload["message"]
    assert payload["next_actions"] == ["agentcad docs quickstart"]


def test_non_run_usage_errors_have_no_run_recovery(runner, isolated_dir):
    result = runner.invoke(cli, ["render", "--wat"])

    payload = json.loads(result.stdout)
    assert payload["command"] == "render"
    assert "canonical_command" not in payload
    assert payload["next_actions"] == ["agentcad render --help"]


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


# --- --label is optional for --dry-run -----------------------------------------
# A dry run never allocates a version, so there is nothing for a label to name.
# Requiring one only cost agents a retry (surfaced by the #207 friction check).


def test_run_dry_run_without_label_succeeds(runner, isolated_dir):
    _init(runner)
    _write_script(isolated_dir)

    result = runner.invoke(cli, ["run", "script.py", "--dry-run", "--no-daemon"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["status"] == "success"
    assert payload["metrics"]["volume"] > 0
    _assert_no_step(payload, None)
    assert not [p for p in isolated_dir.iterdir() if p.name.startswith("v1")]


def test_run_without_label_is_still_required_for_real_runs(runner, isolated_dir):
    _init(runner)
    _write_script(isolated_dir)

    result = runner.invoke(cli, ["run", "script.py", "--no-daemon"])

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    _assert_usage_error(payload, kind="missing_parameter", command="run")
    assert "--label" in payload["message"]
    _assert_no_step(payload, None)
    assert not [p for p in isolated_dir.iterdir() if p.name.startswith("v1")]


def test_run_dry_run_failure_without_label_uses_placeholder_hint(runner, isolated_dir):
    _init(runner)
    _write_script(isolated_dir, "this is not valid python(")

    result = runner.invoke(cli, ["run", "script.py", "--dry-run", "--no-daemon"])

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "validation_error"
    _assert_no_step(payload, None)
    assert "agentcad run script.py --label LABEL" in payload["next_actions"]
    assert "None" not in " ".join(payload["next_actions"])


def test_run_dry_run_without_label_omits_label_from_daemon_argv(
    runner, isolated_dir, monkeypatch
):
    _init(runner)
    _write_script(isolated_dir)
    routed = []

    def fake_route(argv, no_daemon=False):
        routed.append(list(argv))
        raise SystemExit(0)

    monkeypatch.setattr("agentcad.commands.run.maybe_route_through_daemon", fake_route)

    result = runner.invoke(cli, ["run", "script.py", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert routed, "expected the run to be offered to the daemon"
    assert "--label" not in routed[0]
    assert "--output" not in routed[0]
    assert "--dry-run" in routed[0]
