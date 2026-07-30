"""agentcad instructions — install durable project guidance for future agents."""

import json
from pathlib import Path

import click


START_MARKER = "<!-- agentcad:start -->"
END_MARKER = "<!-- agentcad:end -->"

INSTRUCTIONS_BLOCK = f"""\
{START_MARKER}
## agentcad

For CAD/modeling tasks, use `agentcad`. Start by running `agentcad --help`;
it is the detailed operational briefing and command reference. Use
`agentcad docs [section]` for deeper examples, write scripts that call
`show_object(...)`, run with `agentcad run SCRIPT --output LABEL`, inspect the
generated `preview.png`, and open the result with `agentcad view ...` after
successful builds.
{END_MARKER}
"""


def _replace_or_append(content: str, block: str) -> str:
    start = content.find(START_MARKER)
    end = content.find(END_MARKER)

    if start != -1 and end != -1 and start < end:
        end += len(END_MARKER)
        updated = content[:start].rstrip() + "\n\n" + block.rstrip() + content[end:]
        return updated.rstrip() + "\n"

    if not content.strip():
        return block.rstrip() + "\n"

    return content.rstrip() + "\n\n" + block.rstrip() + "\n"


def _target_paths(target: str, cwd: Path) -> list[Path]:
    agents = cwd / "AGENTS.md"
    claude = cwd / "CLAUDE.md"

    if target == "agents":
        return [agents]
    if target == "claude":
        return [claude]
    if target == "all":
        return [agents, claude]

    existing = [path for path in (agents, claude) if path.exists()]
    return existing or [agents]


@click.group()
def instructions():
    """Manage project instruction snippets for future agents."""


@instructions.command()
def show():
    """Print the project instruction snippet as JSON."""
    click.echo(json.dumps({
        "command": "instructions show",
        "status": "success",
        "content": INSTRUCTIONS_BLOCK,
    }))


@instructions.command()
@click.option(
    "--target",
    type=click.Choice(["auto", "agents", "claude", "all"]),
    default="auto",
    show_default=True,
    help=(
        "Instruction file to update. auto updates existing AGENTS.md/CLAUDE.md, "
        "or creates AGENTS.md when neither exists."
    ),
)
def install(target):
    """Install the agentcad snippet into AGENTS.md and/or CLAUDE.md."""
    cwd = Path.cwd()
    paths = _target_paths(target, cwd)
    written = []

    for path in paths:
        original = path.read_text() if path.exists() else ""
        updated = _replace_or_append(original, INSTRUCTIONS_BLOCK)
        path.write_text(updated)
        written.append(str(path))

    click.echo(json.dumps({
        "command": "instructions install",
        "status": "success",
        "target": target,
        "paths": written,
        "message": "agentcad instructions installed. Future agents should read agentcad --help for the detailed briefing.",
    }))
