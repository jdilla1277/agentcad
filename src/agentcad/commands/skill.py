"""agentcad skill — install or show the agent skill file.

The skill body is the canonical guide from `agentcad.guide`; this module only
adds the Agent Skills frontmatter and the `.claude/skills` install location.
`agentcad init` installs the skill automatically, so this command is mainly a
refresh/repair path.
"""

import json
from pathlib import Path

import click

from agentcad.guide import effective_runtime, guide_body, guide_fingerprint


SKILL_RELATIVE_PATH = Path(".claude") / "skills" / "agentcad" / "SKILL.md"

_BUILD123D_FRONTMATTER = """\
---
name: agentcad
description: >
  CAD tool for AI agents. Use when the user asks you to design, model, or build
  a 3D object. agentcad executes build123d Python scripts and produces STEP
  files, PNG renders, mesh exports (STL/GLB/OBJ), and geometric metrics.
compatibility: Requires Python 3.10-3.12 and agentcad installed (pip install agentcad).
allowed-tools: Bash(agentcad:*)
---

"""

_CADQUERY_FRONTMATTER = """\
---
name: agentcad
description: >
  CAD tool for AI agents. Use when the user asks you to design, model, or build
  a 3D object in an existing CadQuery compatibility project. agentcad
  produces STEP files, PNG renders, mesh exports (STL/GLB/OBJ), and metrics.
compatibility: Requires Python 3.10-3.12 and agentcad installed (pip install agentcad).
allowed-tools: Bash(agentcad:*)
---

"""


def _skill_content(runtime: str) -> str:
    frontmatter = (
        _CADQUERY_FRONTMATTER if runtime == "cadquery" else _BUILD123D_FRONTMATTER
    )
    return frontmatter + guide_body(runtime)


def install_skill(cwd: Path, runtime: str) -> dict:
    """Write the skill file for ``runtime`` and return install evidence."""
    skill_path = cwd / SKILL_RELATIVE_PATH
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(_skill_content(runtime))
    return {
        "runtime": runtime,
        "path": str(skill_path),
        "guide_fingerprint": guide_fingerprint(runtime),
    }


def skill_status(cwd: Path, runtime: str) -> dict:
    """Report whether the installed skill matches this package's guide."""
    skill_path = cwd / SKILL_RELATIVE_PATH
    if not skill_path.exists():
        return {"state": "missing", "path": str(skill_path)}
    try:
        installed = skill_path.read_text()
    except OSError:
        return {"state": "missing", "path": str(skill_path)}
    state = "current" if installed == _skill_content(runtime) else "stale"
    return {"state": state, "path": str(skill_path)}


@click.group()
def skill():
    """Manage the agentcad agent skill."""


@skill.command()
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help="Show the skill for an explicit runtime instead of the project mode.",
)
def show(runtime):
    """Print the skill file content as JSON."""
    runtime = effective_runtime(runtime)
    click.echo(json.dumps({
        "command": "skill show",
        "status": "success",
        "runtime": runtime,
        "guide_fingerprint": guide_fingerprint(runtime),
        "content": _skill_content(runtime),
    }))


@skill.command()
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help="Install the skill for an explicit runtime instead of the project mode.",
)
def install(runtime):
    """Install the agent skill to .claude/skills/agentcad/SKILL.md."""
    result = install_skill(Path.cwd(), effective_runtime(runtime))
    click.echo(json.dumps({
        "command": "skill install",
        "status": "success",
        **result,
        "message": "Skill installed. Claude Code will auto-discover it in this project.",
    }))
