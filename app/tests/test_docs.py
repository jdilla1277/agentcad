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
    assert "install" in sections
    assert "commands" in sections
    assert "render" in sections
    assert "export" in sections
    assert "schema" in sections
    assert "helpers" in sections
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


def test_docs_export_section(runner):
    result = runner.invoke(cli, ["docs", "export"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "stl" in content
    assert "glb" in content
    assert "export_glb" in content


def test_docs_helpers_section(runner):
    result = runner.invoke(cli, ["docs", "helpers"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "loft_sections" in content
    assert "tapered_sweep" in content
    assert "naca_wire" in content
    assert "mirror_fuse" in content


def test_docs_install_section(runner):
    result = runner.invoke(cli, ["docs", "install"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    content = data["content"]
    assert "Python 3.10-3.12" in content
    assert "pip install" in content


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
