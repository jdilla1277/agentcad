"""Tests for agentcad instructions install/show commands."""

import json

from agentcad.cli import cli
from agentcad.commands.instructions import END_MARKER, START_MARKER


def test_instructions_show_returns_managed_block(runner):
    result = runner.invoke(cli, ["instructions", "show"])
    assert result.exit_code == 0

    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert START_MARKER in parsed["content"]
    assert END_MARKER in parsed["content"]
    assert "agentcad --help" in parsed["content"]


def test_instructions_install_creates_agents_when_no_instruction_file_exists(runner, isolated_dir):
    result = runner.invoke(cli, ["instructions", "install"])
    assert result.exit_code == 0

    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["paths"] == [str(isolated_dir / "AGENTS.md")]

    content = (isolated_dir / "AGENTS.md").read_text()
    assert "agentcad --help" in content
    assert content.count(START_MARKER) == 1


def test_instructions_install_auto_updates_existing_claude_file(runner, isolated_dir):
    claude = isolated_dir / "CLAUDE.md"
    claude.write_text("# Project\n\nExisting guidance.\n")

    result = runner.invoke(cli, ["instructions", "install"])
    assert result.exit_code == 0

    parsed = json.loads(result.stdout)
    assert parsed["paths"] == [str(claude)]

    content = claude.read_text()
    assert "Existing guidance." in content
    assert "agentcad --help" in content


def test_instructions_install_all_updates_agents_and_claude(runner, isolated_dir):
    result = runner.invoke(cli, ["instructions", "install", "--target", "all"])
    assert result.exit_code == 0

    parsed = json.loads(result.stdout)
    assert parsed["paths"] == [
        str(isolated_dir / "AGENTS.md"),
        str(isolated_dir / "CLAUDE.md"),
    ]
    assert (isolated_dir / "AGENTS.md").exists()
    assert (isolated_dir / "CLAUDE.md").exists()


def test_instructions_install_replaces_existing_block(runner, isolated_dir):
    agents = isolated_dir / "AGENTS.md"
    agents.write_text(
        "# Project\n\n"
        f"{START_MARKER}\nold instructions\n{END_MARKER}\n\n"
        "Keep this.\n"
    )

    result = runner.invoke(cli, ["instructions", "install"])
    assert result.exit_code == 0

    content = agents.read_text()
    assert "old instructions" not in content
    assert "Keep this." in content
    assert content.count(START_MARKER) == 1
    assert content.count(END_MARKER) == 1
