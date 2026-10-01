import json
import shlex
from datetime import datetime

import cadquery as cq
from cadquery import exporters

from agentcad import __version__
from agentcad.cli import cli
from agentcad.commands.init import MANIFEST_FILE


def test_init_creates_manifest_file(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    assert (isolated_dir / MANIFEST_FILE).exists()


def test_init_manifest_has_correct_schema(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert "name" in manifest
    assert "created" in manifest
    assert "version" in manifest
    assert manifest["version"] == __version__


def test_init_default_project_name_is_directory_name(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert manifest["name"] == isolated_dir.name


def test_init_name_flag_overrides_project_name(runner, isolated_dir):
    runner.invoke(cli, ["init", "--name", "enclosure"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert manifest["name"] == "enclosure"


def test_init_created_date_is_iso_format(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    # Should not raise
    datetime.fromisoformat(manifest["created"])


def test_init_stdout_is_valid_json(runner, isolated_dir):
    result = runner.invoke(cli, ["init"])
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)


def test_init_success_json_schema(runner, isolated_dir):
    result = runner.invoke(cli, ["init"])
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "init"
    assert parsed["status"] == "success"
    assert "project" in parsed


def test_init_without_cad_returns_concise_quickstart_actions(runner, isolated_dir):
    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["next_actions"] == [
        "agentcad docs quickstart — follow the first-script workflow for this project",
        "agentcad docs preamble — see the names available in build123d scripts",
    ]
    assert parsed["more_at"] == "agentcad docs quickstart"


def test_init_quickstart_actions_follow_compatibility_runtime(
    runner, isolated_dir
):
    result = runner.invoke(cli, ["init", "--runtime", "cadquery"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["next_actions"][1] == (
        "agentcad docs preamble — see the names available in cadquery scripts"
    )


def test_init_with_cad_routes_to_import_scaffold(runner, isolated_dir):
    (isolated_dir / "input.step").write_text("ISO-10303-21;\n")

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["next_actions"] == [
        "agentcad import input.step — adopt the existing CAD as a versioned "
        "baseline and create edit.py"
    ]
    assert parsed["more_at"] == "agentcad docs editing"


def test_init_cad_choice_prefers_step_then_sorts_by_filename(
    runner, isolated_dir
):
    (isolated_dir / "first.brep").write_text("DBRep_DrawableShape\n")
    (isolated_dir / "z-last.step").write_text("ISO-10303-21;\n")
    (isolated_dir / "a first.stp").write_text("ISO-10303-21;\n")
    version_dir = isolated_dir / "v1_old"
    version_dir.mkdir()
    (version_dir / "output.step").write_text("ISO-10303-21;\n")

    result = runner.invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    parsed = json.loads(result.stdout)
    assert parsed["next_actions"][0].startswith(
        "agentcad import 'a first.stp' —"
    )


def test_init_cad_action_reaches_runnable_import_scaffold(runner, isolated_dir):
    source = isolated_dir / "input.step"
    exporters.export(cq.Workplane("XY").box(10, 10, 10), str(source))

    init_result = runner.invoke(cli, ["init"])
    init_payload = json.loads(init_result.stdout)
    import_command = init_payload["next_actions"][0].split(" — ", 1)[0]

    import_result = runner.invoke(cli, shlex.split(import_command)[1:])
    assert import_result.exit_code == 0, import_result.output
    import_payload = json.loads(import_result.stdout)
    assert import_payload["scaffold"] == "edit.py"
    assert (isolated_dir / "edit.py").exists()

    run_result = runner.invoke(
        cli,
        [
            "run", "edit.py", "--output", "unchanged", "--no-preview",
            "--no-diff", "--no-view", "--no-daemon",
        ],
    )
    assert run_result.exit_code == 0, run_result.output
    run_payload = json.loads(run_result.stdout)
    assert run_payload["status"] == "success"
    assert (isolated_dir / run_payload["outputs"]["step"]).is_file()


def test_init_already_initialized_returns_error(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "error"


def test_init_manifest_has_empty_versions_array(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert "versions" in manifest
    assert manifest["versions"] == []
