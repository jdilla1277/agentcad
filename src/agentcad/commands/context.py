import json
import shlex
from pathlib import Path

import click
from agentcad.project import get_project, project_options

from agentcad import __version__
from agentcad.commands.instructions import instructions_status
from agentcad.commands.skill import skill_status
from agentcad.guide import guide_fingerprint
from agentcad.manifest import load_manifest
from agentcad.recovery import recovery_summary
from agentcad.runners import dispatch


def _agent_setup_report(cwd: Path, runtime: str) -> tuple[dict, list[str]]:
    """Summarize installed agent-guide surfaces and suggest repairs.

    Only advisory: context never mutates instruction files. A stale state is
    normal right after upgrading agentcad and is fixed by re-running the
    install commands, which replace only the marked block.
    """
    instructions = instructions_status(cwd, runtime)
    skill = skill_status(cwd, runtime)
    report = {
        "guide_fingerprint": guide_fingerprint(runtime),
        "instructions": instructions,
        "skill": skill,
    }
    actions = []
    if instructions["state"] != "current":
        detail = (
            "install the agent guide into AGENTS.md/CLAUDE.md"
            if instructions["state"] == "missing"
            else "refresh the outdated agent guide in AGENTS.md/CLAUDE.md"
        )
        actions.append(f"agentcad instructions install — {detail}")
    if skill["state"] != "current":
        detail = (
            "install the Claude Code skill"
            if skill["state"] == "missing"
            else "refresh the outdated Claude Code skill"
        )
        actions.append(f"agentcad skill install — {detail}")
    return report, actions


@click.command()
@project_options
def context():
    """Show the current project context."""
    manifest = load_manifest(command="context")

    versions = manifest.get("versions", [])
    current = manifest.get("current", None)
    recovery = recovery_summary(get_project().build_root, manifest)
    runtime = manifest.get("runtime") or dispatch.DEFAULT_RUNTIME
    agent_setup, setup_actions = _agent_setup_report(get_project().project_root, runtime)
    if get_project().configured:
        root_arg = " --build-dir " + shlex.quote(str(get_project().build_root))
        setup_actions = [
            action.replace("agentcad instructions install", f"agentcad instructions{root_arg} install")
                  .replace("agentcad skill install", f"agentcad skill{root_arg} install")
            for action in setup_actions
        ]

    versions_summary = [
        {
            "version": v["version"],
            "label": v["label"],
            "status": v["status"],
            "path": v["path"],
            # Pre-1b entries don't have `source` — default to "script" since
            # before `agentcad import` shipped, every version was scripted.
            "source": v.get("source", "script"),
        }
        for v in versions
    ]

    response = {
        "command": "context",
        "status": "success",
        "project": manifest["name"],
        "tool_version": __version__,
        "current": current,
        "version_count": len(versions),
        "versions": versions_summary,
        "recovery": recovery,
        "agent_setup": agent_setup,
    }
    if setup_actions:
        response["next_actions"] = setup_actions
    click.echo(json.dumps(response))
