import json

from click.testing import CliRunner
from cadtool.cli import cli


def test_docs_returns_full_documentation(runner):
    result = runner.invoke(cli, ["docs"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "success"
    assert data["command"] == "docs"
    assert len(data["content"]) > 100


def test_docs_lists_sections(runner):
    result = runner.invoke(cli, ["docs"])
    data = json.loads(result.output)
    sections = data["sections"]
    assert "commands" in sections
    assert "render" in sections
    assert "schema" in sections
    assert "workflow" in sections


def test_docs_commands_section(runner):
    result = runner.invoke(cli, ["docs", "commands"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    for cmd in ["init", "run", "render", "context", "docs", "diff"]:
        assert cmd in content


def test_docs_render_section(runner):
    result = runner.invoke(cli, ["docs", "render"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "view" in content
    assert "zoom" in content
    assert "focus" in content


def test_docs_schema_section(runner):
    result = runner.invoke(cli, ["docs", "schema"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "success" in content
    assert "failed" in content
    assert "error" in content


def test_docs_workflow_section(runner):
    result = runner.invoke(cli, ["docs", "workflow"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "init" in content
    assert "run" in content


def test_docs_unknown_section_error(runner):
    result = runner.invoke(cli, ["docs", "nonexistent"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert data["status"] == "error"
    assert "nonexistent" in data["message"]


def test_docs_works_without_project(runner, isolated_dir):
    # No cadtool.json in isolated_dir
    result = runner.invoke(cli, ["docs"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["status"] == "success"
