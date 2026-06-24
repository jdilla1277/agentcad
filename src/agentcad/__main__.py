"""Enable ``python -m agentcad`` to run the CLI.

Without this, ``python -m agentcad`` fails with "No module named
agentcad.__main__" and ``python -m agentcad.cli`` exits silently (the
module imports but never invokes the Click group). Both are common ways
a fresh agent runs the tool from a checkout — per CLAUDE.md's validation
guidance — without installing the console script, so route them to the
same entry point.
"""
from agentcad.cli import cli

if __name__ == "__main__":
    cli()
