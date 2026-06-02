"""Tests for agentcad skill install/show commands."""

import json
from pathlib import Path

from agentcad.cli import cli


def test_skill_show_returns_content(runner):
    result = runner.invoke(cli, ["skill", "show"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert "content" in parsed
    assert "name: agentcad" in parsed["content"]
    assert "description:" in parsed["content"]


def test_skill_show_content_has_frontmatter(runner):
    result = runner.invoke(cli, ["skill", "show"])
    parsed = json.loads(result.stdout)
    content = parsed["content"]
    assert content.startswith("---")
    assert "---" in content[3:]  # closing frontmatter


def test_skill_show_content_mentions_commands(runner):
    result = runner.invoke(cli, ["skill", "show"])
    parsed = json.loads(result.stdout)
    content = parsed["content"]
    assert "agentcad run" in content
    assert "agentcad init" in content
    assert "show_object" in content


def test_skill_install_creates_file(runner, isolated_dir):
    result = runner.invoke(cli, ["skill", "install"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"

    skill_path = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    assert skill_path.exists()
    content = skill_path.read_text()
    assert "name: agentcad" in content


def test_skill_install_path_in_output(runner, isolated_dir):
    result = runner.invoke(cli, ["skill", "install"])
    parsed = json.loads(result.stdout)
    assert "path" in parsed
    assert ".claude/skills/agentcad/SKILL.md" in parsed["path"]


def test_skill_install_overwrites_existing(runner, isolated_dir):
    # Install once
    runner.invoke(cli, ["skill", "install"])
    skill_path = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    assert skill_path.exists()

    # Install again — should succeed without error
    result = runner.invoke(cli, ["skill", "install"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"


def test_skill_install_valid_frontmatter(runner, isolated_dir):
    """Installed skill should have valid Agent Skills spec frontmatter."""
    runner.invoke(cli, ["skill", "install"])
    skill_path = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    content = skill_path.read_text()

    # Check name follows spec: lowercase, hyphens only, no consecutive hyphens
    lines = content.split("\n")
    assert lines[0] == "---"
    name_line = [l for l in lines if l.startswith("name:")][0]
    name = name_line.split(":", 1)[1].strip()
    assert name == "agentcad"
    assert name == name.lower()
    assert "--" not in name
