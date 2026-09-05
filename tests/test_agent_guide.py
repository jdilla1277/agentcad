"""The canonical agent guide and its automatic installation.

Three properties matter:

1. Every surface (skill, instructions block) renders the same canonical body,
   so they cannot drift from each other.
2. `agentcad init` installs the guide by default — a user who runs only
   `pip install agentcad` + `agentcad init` gets an AGENTS.md block that
   harnesses load unconditionally, plus the Claude Code skill.
3. `agentcad context` detects missing/stale installs and suggests the repair
   without mutating anything.
"""

import json
from pathlib import Path

from agentcad.cli import cli
from agentcad.commands.init import _bootstrap_manifest
from agentcad.commands.instructions import END_MARKER, START_MARKER
from agentcad.guide import guide_body, guide_fingerprint


def _invoke(runner, args):
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


# --- one canonical body across surfaces ---


def test_skill_and_instructions_render_the_same_canonical_body(runner):
    for runtime in ("build123d", "cadquery"):
        body = guide_body(runtime)
        skill = _invoke(runner, ["skill", "show", "--runtime", runtime])
        instructions = _invoke(
            runner, ["instructions", "show", "--runtime", runtime]
        )
        assert skill["content"].endswith(body)
        assert body.rstrip() in instructions["content"]
        assert skill["guide_fingerprint"] == guide_fingerprint(runtime)
        assert instructions["guide_fingerprint"] == guide_fingerprint(runtime)


def test_runtime_guides_differ_and_have_distinct_fingerprints():
    assert guide_body("build123d") != guide_body("cadquery")
    assert guide_fingerprint("build123d") != guide_fingerprint("cadquery")


def test_guide_distinguishes_json_commands_from_human_readable_help():
    for runtime in ("build123d", "cadquery"):
        body = guide_body(runtime)
        assert "Operational commands return structured JSON" in body
        assert "`--help` and `agentcad docs` return readable text" in body


# --- init installs the guide by default ---


def test_init_installs_instructions_and_skill(runner, isolated_dir):
    payload = _invoke(runner, ["init", "--name", "widget"])

    setup = payload["agent_setup"]
    assert setup["status"] == "ready"
    assert setup["guide_fingerprint"] == guide_fingerprint("build123d")
    target_names = {t["name"] for t in setup["targets"]}
    assert target_names == {"claude-skill", "project-instructions"}
    # uniform schema: every target lists its files under "paths"
    assert all(isinstance(t["paths"], list) for t in setup["targets"])

    agents = (isolated_dir / "AGENTS.md").read_text()
    assert START_MARKER in agents
    assert guide_fingerprint("build123d") in agents
    assert guide_body("build123d").rstrip() in agents

    skill_file = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    assert skill_file.read_text().endswith(guide_body("build123d"))


def test_init_no_agent_setup_installs_nothing(runner, isolated_dir):
    payload = _invoke(runner, ["init", "--no-agent-setup"])

    assert payload["agent_setup"] == {"status": "skipped"}
    assert not (isolated_dir / "AGENTS.md").exists()
    assert not (isolated_dir / ".claude").exists()


def test_init_preserves_user_authored_instructions(runner, isolated_dir):
    claude = isolated_dir / "CLAUDE.md"
    claude.write_text("# Project\n\nUse tabs, not spaces.\n")

    _invoke(runner, ["init"])

    content = claude.read_text()
    assert "Use tabs, not spaces." in content
    assert content.count(START_MARKER) == 1
    assert content.count(END_MARKER) == 1
    # auto target updates the existing file rather than creating a second one
    assert not (isolated_dir / "AGENTS.md").exists()


def test_init_cadquery_project_installs_cadquery_guide(runner, isolated_dir):
    _invoke(runner, ["init", "--runtime", "cadquery"])

    agents = (isolated_dir / "AGENTS.md").read_text()
    assert "cq.Workplane" in agents
    assert "build123d primitives" not in agents

    skill_file = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    assert "cq.Workplane" in skill_file.read_text()


def test_import_bootstrap_installs_guide(runner, isolated_dir, monkeypatch):
    monkeypatch.chdir(isolated_dir)
    _bootstrap_manifest(runtime="build123d")

    assert (isolated_dir / "AGENTS.md").exists()
    assert (
        isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    ).exists()


# --- context detects current / stale / missing ---


def test_context_reports_current_after_init(runner, isolated_dir):
    _invoke(runner, ["init"])
    payload = _invoke(runner, ["context"])

    setup = payload["agent_setup"]
    assert setup["instructions"]["state"] == "current"
    assert setup["skill"]["state"] == "current"
    assert "next_actions" not in payload


def test_context_reports_missing_setup_with_repair_actions(
    runner, isolated_dir
):
    _invoke(runner, ["init", "--no-agent-setup"])
    payload = _invoke(runner, ["context"])

    setup = payload["agent_setup"]
    assert setup["instructions"]["state"] == "missing"
    assert setup["skill"]["state"] == "missing"
    actions = payload["next_actions"]
    assert any("instructions install" in action for action in actions)
    assert any("skill install" in action for action in actions)


def test_context_reports_stale_setup_after_content_change(
    runner, isolated_dir
):
    _invoke(runner, ["init"])

    # Simulate a pre-upgrade install: old fingerprint, old skill content.
    agents = isolated_dir / "AGENTS.md"
    agents.write_text(
        agents.read_text().replace(guide_fingerprint("build123d"), "0" * 12)
    )
    skill_file = isolated_dir / ".claude" / "skills" / "agentcad" / "SKILL.md"
    skill_file.write_text(skill_file.read_text() + "\nlocal edit\n")

    payload = _invoke(runner, ["context"])
    setup = payload["agent_setup"]
    assert setup["instructions"]["state"] == "stale"
    assert setup["skill"]["state"] == "stale"
    assert any("refresh" in action for action in payload["next_actions"])


def test_guide_warns_against_rerunning_init():
    for runtime in ("build123d", "cadquery"):
        assert "already initialized" in guide_body(runtime)


def test_second_init_suggests_guide_refresh_not_force(runner, isolated_dir):
    _invoke(runner, ["init"])
    result = runner.invoke(cli, ["init"])
    assert result.exit_code == 1
    parsed = json.loads(result.stdout)
    assert "already initialized" in parsed["suggestion"]
    assert "instructions install" in parsed["suggestion"]


def test_repeated_install_keeps_block_at_top_without_blank_lines(
    runner, isolated_dir
):
    _invoke(runner, ["init"])
    _invoke(runner, ["instructions", "install"])
    _invoke(runner, ["instructions", "install"])

    content = (isolated_dir / "AGENTS.md").read_text()
    assert content.startswith(START_MARKER)
    assert content.count(START_MARKER) == 1


def test_instructions_install_replaces_stale_block_only(runner, isolated_dir):
    agents = isolated_dir / "AGENTS.md"
    agents.write_text(
        "# Project\n\n"
        f"{START_MARKER}\nold pointer block\n{END_MARKER}\n\n"
        "Keep this.\n"
    )

    _invoke(runner, ["instructions", "install"])

    content = agents.read_text()
    assert "old pointer block" not in content
    assert "Keep this." in content
    assert guide_body("build123d").rstrip() in content
    assert content.count(START_MARKER) == 1
