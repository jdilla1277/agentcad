import json
from datetime import datetime

from cadtool.cli import cli
from cadtool.commands.init import MANIFEST_FILE


def test_init_creates_manifest_file(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    assert (isolated_dir / MANIFEST_FILE).exists()


def test_init_manifest_has_correct_schema(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert "name" in manifest
    assert "created" in manifest
    assert "version" in manifest
    assert manifest["version"] == "0.1.0"


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
    parsed = json.loads(result.output)
    assert isinstance(parsed, dict)


def test_init_success_json_schema(runner, isolated_dir):
    result = runner.invoke(cli, ["init"])
    parsed = json.loads(result.output)
    assert parsed["command"] == "init"
    assert parsed["status"] == "success"
    assert "project" in parsed


def test_init_already_initialized_returns_error(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 1
    parsed = json.loads(result.output)
    assert parsed["status"] == "error"


def test_init_manifest_has_empty_versions_array(runner, isolated_dir):
    runner.invoke(cli, ["init"])
    manifest = json.loads((isolated_dir / MANIFEST_FILE).read_text())
    assert "versions" in manifest
    assert manifest["versions"] == []
