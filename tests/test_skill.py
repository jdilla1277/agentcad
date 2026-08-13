"""Tests for agentcad skill install/show commands."""

import json
from pathlib import Path

from agentcad.cli import cli


def test_skill_show_returns_content(runner):
    result = runner.invoke(cli, ["skill", "show"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["runtime"] == "build123d"
    assert "content" in parsed
    assert "name: agentcad" in parsed["content"]
    assert "description:" in parsed["content"]


def test_default_skill_is_build123d_only(runner, isolated_dir):
    result = runner.invoke(cli, ["skill", "show"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    content = parsed["content"]

    assert parsed["runtime"] == "build123d"
    assert "build123d primitives" in content
    assert "CadQuery compatibility" in content
    assert "cq.Workplane" not in content
    assert "build123d or CadQuery" not in content


def test_cadquery_project_skill_is_compatibility_only(runner, isolated_dir):
    init_result = runner.invoke(
        cli,
        ["init", "--name", "legacy", "--runtime", "cadquery"],
    )
    assert init_result.exit_code == 0

    result = runner.invoke(cli, ["skill", "show"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    content = parsed["content"]

    assert parsed["runtime"] == "cadquery"
    assert "CadQuery compatibility project" in content
    assert "agentcad init --name <project_name> --runtime cadquery" in content
    assert "cq.Workplane" in content
    assert "build123d" not in content.lower()
    assert "Box(10, 20, 5)" not in content


def test_skill_runtime_flag_selects_cadquery_compatibility(runner, isolated_dir):
    result = runner.invoke(
        cli,
        ["skill", "show", "--runtime", "cadquery"],
    )
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["runtime"] == "cadquery"
    assert "cq.Workplane" in parsed["content"]


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
    assert "agentcad check-spec" in content
    assert "agentcad recover VERSION_DIR" in content
    assert "without deleting files" in content
    assert "`passed` is false" in content
    assert "cylindrical_features[].axis" in content
    assert "show_object" in content
    assert "raise_annulus" in content
    assert "Compound(result)" in content
    assert "`load_step(path)` returns a build123d `Part`" in content
    assert "base.solids()" in content
    assert "base.faces()" in content
    assert "base.edges()" in content
    assert "base.bounding_box()" in content
    assert "agentcad docs editing" in content
    assert "--no-preview --no-diff --no-view" in content
    assert "agentcad diff" in content
    assert "explicit" in content
    assert "comparison_phases" in content
    assert "exact_3d_comparison" in content
    assert "duration_ms" in content
    assert "AGENTCAD_DIFF_TIMEOUT_S" in content
    assert "30-second default worker budget" in content
    assert "comparison_3d.status/reason/timeout_s" in content
    assert "create a duplicate version" in content


def test_skill_install_creates_file(runner, isolated_dir):
    result = runner.invoke(cli, ["skill", "install"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["status"] == "success"
    assert parsed["runtime"] == "build123d"

    skill_path = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    assert skill_path.exists()
    content = skill_path.read_text()
    assert "name: agentcad" in content


def test_skill_install_follows_cadquery_project(runner, isolated_dir):
    init_result = runner.invoke(
        cli,
        ["init", "--name", "legacy", "--runtime", "cadquery"],
    )
    assert init_result.exit_code == 0

    result = runner.invoke(cli, ["skill", "install"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["runtime"] == "cadquery"

    skill_path = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    content = skill_path.read_text()
    assert "cq.Workplane" in content
    assert "build123d" not in content.lower()


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
