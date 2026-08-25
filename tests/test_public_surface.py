"""Pin the CLI's public surface so it cannot change without someone noticing.

The website mirrors this surface by hand at https://agentcad.dev/docs, in a
different repo. Between 0.2 and 0.4.1 six commands shipped (`measure`,
`parts`, `import`, `check-spec`, `instructions`, `recover`) and nine docs
sections were added, and the site said none of it — the page still advertised
twelve commands and "17 sections" while the CLI had eighteen and twenty-six.
Nothing failed, because nothing was watching.

This snapshot makes a surface change a visible diff in the release PR, and
the failure message says what else has to move. Regenerate deliberately:

    AGENTCAD_UPDATE_SURFACE=1 pytest tests/test_public_surface.py

then update agentcad.dev/docs in the internal repo before the release ships.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from click.testing import CliRunner

from agentcad.cli import cli

SURFACE_PATH = Path(__file__).parent / "fixtures" / "public_surface.json"

SITE_REMINDER = (
    "The CLI's public surface changed. That surface is mirrored by hand on "
    "https://agentcad.dev/docs (app/docs/page.tsx in jdilla1277/agentcad-internal), "
    "so this is not just a fixture bump:\n"
    "  1. AGENTCAD_UPDATE_SURFACE=1 pytest tests/test_public_surface.py\n"
    "  2. Update the site's command reference and section list to match.\n"
    "  3. Tick the docs-parity box in prd/release_runbook.md Step 5."
)


def _commands() -> list[str]:
    """Top-level command names, read from the `Commands:` block of --help."""
    output = CliRunner().invoke(cli, ["--help"]).output
    block = re.search(r"Commands:\n(.*?)(?:\n\n|\Z)", output, re.S)
    assert block, "--help no longer has a Commands: block"
    return sorted(re.findall(r"^\s{2}(\S+)", block.group(1), re.M))


def _docs_sections() -> list[str]:
    result = CliRunner().invoke(cli, ["docs"])
    assert result.exit_code == 0
    return sorted(json.loads(result.stdout)["sections"])


def _current() -> dict[str, list[str]]:
    return {"commands": _commands(), "docs_sections": _docs_sections()}


def test_public_surface_matches_snapshot():
    current = _current()

    if os.environ.get("AGENTCAD_UPDATE_SURFACE"):
        SURFACE_PATH.write_text(json.dumps(current, indent=2) + "\n")
        return

    expected = json.loads(SURFACE_PATH.read_text())

    for key in ("commands", "docs_sections"):
        added = sorted(set(current[key]) - set(expected[key]))
        removed = sorted(set(expected[key]) - set(current[key]))
        if added or removed:
            raise AssertionError(
                f"{key}: added={added} removed={removed}\n\n{SITE_REMINDER}"
            )
