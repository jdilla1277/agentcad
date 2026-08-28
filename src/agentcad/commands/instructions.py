"""agentcad instructions — install durable project guidance for future agents.

The installed block is the complete canonical guide from `agentcad.guide`,
not a pointer to it. AGENTS.md/CLAUDE.md are loaded unconditionally by agent
harnesses, so this is the one surface that guarantees the guide is in context
before CAD work starts — skill files and `--help` only work when the agent
chooses to read them. `agentcad init` installs this block automatically.
"""

import json
import re
from pathlib import Path

import click

from agentcad.guide import effective_runtime, guide_body, guide_fingerprint


START_MARKER = "<!-- agentcad:start -->"
END_MARKER = "<!-- agentcad:end -->"
_FINGERPRINT_RE = re.compile(r"<!-- agentcad-guide: ([0-9a-f]+) -->")


def instructions_block(runtime: str) -> str:
    """The AGENTS.md/CLAUDE.md managed block: full guide plus fingerprint."""
    return (
        f"{START_MARKER}\n"
        f"<!-- agentcad-guide: {guide_fingerprint(runtime)} -->\n"
        f"{guide_body(runtime).rstrip()}\n"
        f"{END_MARKER}\n"
    )


def _replace_or_append(content: str, block: str) -> str:
    start = content.find(START_MARKER)
    end = content.find(END_MARKER)

    if start != -1 and end != -1 and start < end:
        end += len(END_MARKER)
        prefix = content[:start].rstrip()
        if prefix:
            prefix += "\n\n"
        updated = prefix + block.rstrip() + content[end:]
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


def install_instructions(cwd: Path, target: str, runtime: str) -> dict:
    """Install or refresh the managed block and return install evidence.

    User-authored content outside the markers is always preserved; only the
    marked block is replaced.
    """
    block = instructions_block(runtime)
    written = []
    for path in _target_paths(target, cwd):
        original = path.read_text() if path.exists() else ""
        path.write_text(_replace_or_append(original, block))
        written.append(str(path))
    return {
        "runtime": runtime,
        "target": target,
        "paths": written,
        "guide_fingerprint": guide_fingerprint(runtime),
    }


def instructions_status(cwd: Path, runtime: str) -> dict:
    """Report whether any instruction file carries the current guide."""
    expected = guide_fingerprint(runtime)
    files = {}
    for path in (cwd / "AGENTS.md", cwd / "CLAUDE.md"):
        if not path.exists():
            continue
        try:
            content = path.read_text()
        except OSError:
            continue
        if START_MARKER not in content:
            continue
        match = _FINGERPRINT_RE.search(content)
        installed = match.group(1) if match else None
        files[path.name] = "current" if installed == expected else "stale"

    if not files:
        state = "missing"
    elif "current" in files.values():
        state = "current"
    else:
        state = "stale"
    return {"state": state, "files": files}


@click.group()
def instructions():
    """Manage project instruction snippets for future agents."""


@instructions.command()
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help="Show the block for an explicit runtime instead of the project mode.",
)
def show(runtime):
    """Print the project instruction block as JSON."""
    runtime = effective_runtime(runtime)
    click.echo(json.dumps({
        "command": "instructions show",
        "status": "success",
        "runtime": runtime,
        "guide_fingerprint": guide_fingerprint(runtime),
        "content": instructions_block(runtime),
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
@click.option(
    "--runtime",
    default=None,
    type=click.Choice(["cadquery", "build123d"]),
    help="Install the block for an explicit runtime instead of the project mode.",
)
def install(target, runtime):
    """Install the agentcad guide into AGENTS.md and/or CLAUDE.md."""
    result = install_instructions(Path.cwd(), target, effective_runtime(runtime))
    click.echo(json.dumps({
        "command": "instructions install",
        "status": "success",
        **result,
        "message": (
            "agentcad guide installed. Future agents in this project receive it "
            "automatically from their instruction file."
        ),
    }))
