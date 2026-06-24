"""`python -m agentcad` / `python -m agentcad.cli` must run the CLI.

Both are common ways a fresh agent runs the tool from a checkout without
installing the console script (see CLAUDE.md's validation guidance). They
used to be footguns: `python -m agentcad` failed with "No module named
agentcad.__main__", and `python -m agentcad.cli` imported the module and
exited silently (exit 0, no output) because there was no
``if __name__ == "__main__"`` guard. A fresh agent reading that silent
success could mistake it for a broken or empty result.

These run the real interpreter via subprocess — CliRunner can't catch a
missing module entry point or a missing __main__ guard.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SRC = str(Path(__file__).resolve().parents[1] / "src")


def _run_module(module: str, *args):
    """Invoke `python -m <module> <args>` against the current checkout."""
    env = dict(os.environ)
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize("module", ["agentcad", "agentcad.cli"])
def test_module_execution_runs_cli(module):
    # `docs` needs no manifest, no file, and no OCCT import — a clean probe
    # that the Click group actually ran and emitted its JSON contract.
    result = _run_module(module, "docs", "commands")
    assert result.returncode == 0, (
        f"`python -m {module} docs commands` exited {result.returncode}\n"
        f"stdout={result.stdout[:300]!r} stderr={result.stderr[:300]!r}"
    )
    assert result.stdout.strip(), (
        f"`python -m {module}` produced no output — the entry point did not "
        f"invoke the CLI (silent-success footgun)."
    )
    parsed = json.loads(result.stdout)
    assert parsed["command"] == "docs"
    assert parsed["status"] == "success"


@pytest.mark.parametrize("module", ["agentcad", "agentcad.cli"])
def test_module_execution_help_exits_zero(module):
    result = _run_module(module, "--help")
    assert result.returncode == 0
    assert "Usage" in result.stdout
